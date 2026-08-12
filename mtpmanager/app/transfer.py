"""Single transfer pipeline: optional transcode → transport.send.

Batch transfers pipeline convert of track N+1 into the alternate temp slot
while track N is being sent, so ffmpeg cannot clobber a file in flight.
Works for any Transport (CMD mtp-sendtr or Experimental PyMTP).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from mtpmanager.app.cancellation import (
    CancelCheck,
    JobCancelled,
    raise_if_cancelled,
)
from mtpmanager.app.transfer_queue import BatchTransferQueue
from mtpmanager.domain.audio_encode import AudioEncodeSettings
from mtpmanager.domain.audio_quality import (
    DeviceCapabilities,
    decide_action,
)
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.special_sync import (
    SpecialSyncOptions,
    apply_meta_patch,
    basename_for_special_sync,
)
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
# source_path, status: "transcoding" | "transferring" | "done" | "failed" | "skipped"
TrackStatusCallback = Callable[[str, str], None]
# Optional: resolve MTP parent folder id for a track (artist folders, etc.).
# Ignored when GUID remote naming is active (flat under Music 100).
ParentFolderResolver = Callable[[TrackMetadata], int | None]
# After a successful send: (guid, remote send path, optional object id)
AfterSendCallback = Callable[[str, str, int | None], None]
# Per-track encode recipe (e.g. audiobook override vs global Config).
EncodeSettingsResolver = Callable[[Track], AudioEncodeSettings | None]


@dataclass(frozen=True)
class PreparedTrack:
    """Local path + metadata ready for transport.send_track.

    When *already_on_device* is True, prepare/transcode was skipped and
    *send_path* is empty — the batch pipeline only records a skip.

    *preferred_basename* is set when Special Sync disables GUID naming.
    *fixed_parent_id* overrides normal parent resolution when set.
    """

    send_path: str
    meta: TrackMetadata
    cleanup_path: str | None
    source_path: str
    guid: str = ""
    already_on_device: bool = False
    preferred_basename: str | None = None
    fixed_parent_id: int | None = None
    use_guid: bool = True


def _merge_meta_after_convert(
    original: TrackMetadata, converted: TrackMetadata
) -> TrackMetadata:
    """Prefer original tags; take stream length/bitrate from converted when useful."""
    return TrackMetadata(
        artist=original.artist or converted.artist,
        albumartist=original.albumartist or converted.albumartist,
        composer=original.composer or converted.composer,
        album=original.album or converted.album,
        title=original.title or converted.title,
        genre=original.genre or converted.genre,
        tracknumber=original.tracknumber or converted.tracknumber,
        date=original.date or converted.date,
        length_sec=converted.length_sec or original.length_sec,
        sample_rate=converted.sample_rate or original.sample_rate,
        channels=converted.channels or original.channels,
        bitrate=converted.bitrate or original.bitrate,
        bitrate_mode=converted.bitrate_mode or original.bitrate_mode,
    )


def _notify_status(
    on_track_status: TrackStatusCallback | None,
    source_path: str,
    status: str,
) -> None:
    if on_track_status is None:
        return
    try:
        on_track_status(source_path, status)
    except Exception:
        logger.debug("on_track_status failed", exc_info=True)


def prepare_track(
    track: Track,
    *,
    target_format: str,
    transcoder: Transcoder,
    slot: int = 0,
    reread_tags_after_convert: bool = True,
    on_track_status: TrackStatusCallback | None = None,
    device_formats: Collection[str] | None = None,
    should_cancel: CancelCheck | None = None,
    encode_settings: AudioEncodeSettings | None = None,
    force_transcode: bool = False,
    special: SpecialSyncOptions | None = None,
) -> PreparedTrack:
    """Transcode into *slot* if needed; return path/meta for send (no send yet).

    Quality policy (:func:`~mtpmanager.domain.audio_quality.decide_action`):

    - Prefer bit-perfect COPY when the device supports the source codec.
    - TRANSCODE only for device incompatibility, explicit lower-quality
      settings (podcast/speech / Shrink), or tempo.
    - Target settings are clamped so they never claim higher fidelity than
      the source (no lossy→higher-lossy or lossy→lossless "upgrades").

    *force_transcode*: request a re-encode (Shrink). Still skips when the
    recipe would not lower quality on a device-native file.

    *special*: optional Special Sync overrides (meta patch, encode force,
    GUID/basename/parent). Host library tags are not mutated.
    """
    raise_if_cancelled(should_cancel)
    track = _apply_special_to_track(track, special)
    if special is not None:
        if special.encode is not None:
            encode_settings = special.encode
        if special.force_transcode:
            force_transcode = True
    if encode_settings is not None:
        target_format = encode_settings.file_extension()
    else:
        target_format = target_format.lower().lstrip(".")
    src = track.path
    meta = track.meta
    cleanup_path: str | None = None

    force_tempo = bool(
        encode_settings is not None and encode_settings.needs_tempo_filter()
    )
    caps = DeviceCapabilities.from_formats(device_formats)
    decision = decide_action(
        src,
        caps,
        meta=meta,
        preferred_settings=encode_settings,
        force_transcode=force_transcode,
        force_tempo=force_tempo,
        target_format=target_format,
    )
    logger.info("%s", decision.log_line(src))

    if decision.action == "TRANSCODE":
        out_fmt = decision.target_format or target_format
        use_settings = decision.settings
        _notify_status(on_track_status, track.path, "transcoding")
        # force=True: same-container downsizes (speech preset / shrink) must
        # not be short-circuited by FFmpegTranscoder passthrough.
        src = transcoder.convert(
            src,
            out_fmt,
            slot=slot,
            settings=use_settings,
            force=True,
        )
        cleanup_path = src
        if reread_tags_after_convert:
            converted = read_metadata(src)
            meta = _merge_meta_after_convert(meta, converted)
            # Re-apply patch so convert tag re-read cannot clobber overrides.
            if special is not None and special.meta_patch:
                meta = apply_meta_patch(
                    meta,
                    special.meta_patch,
                    apply_all=special.apply_meta_to_all,
                )
    else:
        logger.info(
            "Passthrough (no transcode): %s (%s)",
            src,
            decision.reason,
        )

    use_guid = True if special is None else bool(special.use_guid)
    if use_guid:
        guid = track.guid if is_track_guid(track.guid) else new_track_guid()
        preferred_basename = None
    else:
        guid = ""
        out_ext = (
            encode_settings.file_extension()
            if encode_settings is not None
            else target_format
        )
        if special is not None:
            preferred_basename = basename_for_special_sync(
                track, meta, out_ext, options=special
            )
        else:
            preferred_basename = None

    # Fixed parent only for modes that share one parent for the whole batch.
    # artist / artist_album use resolve_parent_folder per track instead.
    fixed_parent: int | None = None
    if special is not None and special.parent_id is not None:
        mode = special.folder_mode or "none"
        if mode in ("none", "custom"):
            fixed_parent = int(special.parent_id)

    return PreparedTrack(
        send_path=src,
        meta=meta,
        cleanup_path=cleanup_path,
        source_path=track.path,
        guid=guid,
        preferred_basename=preferred_basename,
        fixed_parent_id=fixed_parent,
        use_guid=use_guid,
    )


def _resolve_parent(
    resolver: ParentFolderResolver | None,
    meta: TrackMetadata,
    *,
    guid: str | None = None,
    fixed_parent_id: int | None = None,
    honor_resolver: bool = False,
) -> int | None:
    """Resolve MTP parent folder id for a send.

    *fixed_parent_id* (Special Sync) always wins when set — including when
    GUID ObjectFileName mode would otherwise force flat Music.

    *honor_resolver*: Special Sync artist/album folder modes — use the
    resolver even when a host GUID is present.

    Music with a host GUID stays flat under Music (resolver artist/album
    nesting is ignored). Podcasts always consult the resolver so they land
    under ZENcast even though ObjectFileName is still the episode GUID.
    """
    if fixed_parent_id is not None:
        return int(fixed_parent_id)
    if resolver is None:
        return None
    parent = resolver(meta)
    if parent is None:
        return None
    genre = (getattr(meta, "genre", None) or "").strip().casefold()
    if genre == "podcast":
        return parent
    if honor_resolver:
        return parent
    # Music GUID ObjectFileName mode: flat under Music (ignore artist folders).
    if guid and is_track_guid(guid):
        return None
    return parent


def _apply_special_to_track(
    track: Track,
    special: SpecialSyncOptions | None,
) -> Track:
    """Return a Track with Special Sync metadata patch applied (host path same)."""
    if special is None:
        return track
    meta = apply_meta_patch(
        track.meta,
        special.meta_patch,
        apply_all=special.apply_meta_to_all,
    )
    if meta is track.meta:
        return track
    return Track(path=track.path, meta=meta, guid=track.guid)


def _guid_already_on_device(
    guid: str,
    device_guid_stems: Collection[str] | None,
) -> bool:
    if not device_guid_stems or not is_track_guid(guid):
        return False
    return guid in device_guid_stems


def _resolve_encode_settings(
    track: Track,
    *,
    encode_settings: AudioEncodeSettings | None,
    resolve_encode_settings: EncodeSettingsResolver | None,
) -> AudioEncodeSettings | None:
    if resolve_encode_settings is not None:
        try:
            return resolve_encode_settings(track)
        except Exception:
            logger.debug(
                "resolve_encode_settings failed for %s", track.path, exc_info=True
            )
    return encode_settings


def transfer_track(
    track: Track,
    *,
    target_format: str,
    transport: Transport,
    transcoder: Transcoder,
    reread_tags_after_convert: bool = True,
    slot: int = 0,
    on_track_status: TrackStatusCallback | None = None,
    resolve_parent_folder: ParentFolderResolver | None = None,
    device_formats: Collection[str] | None = None,
    should_cancel: CancelCheck | None = None,
    device_guid_stems: Collection[str] | None = None,
    on_after_send: AfterSendCallback | None = None,
    encode_settings: AudioEncodeSettings | None = None,
    resolve_encode_settings: EncodeSettingsResolver | None = None,
    force_transcode: bool = False,
    special: SpecialSyncOptions | None = None,
) -> None:
    """
    Ensure track is device-ready (transcode if needed), then send via transport.
    Temp files from the transcoder are always cleaned up.

    *should_cancel* is checked before prepare and before send (cannot abort an
    in-flight MTP/ffmpeg call).

    *device_guid_stems*: set of 32-hex GUIDs already present on the device
    (durable device index). Matching tracks skip transcode and send.

    *special*: optional Special Sync overrides (see :class:`SpecialSyncOptions`).
    """
    raise_if_cancelled(should_cancel, total=1)
    if special is not None and not special.effective_skip_if_present():
        device_guid_stems = None
    guid_hint = track.guid if is_track_guid(track.guid) else ""
    if guid_hint and _guid_already_on_device(guid_hint, device_guid_stems):
        logger.info(
            "Skip (already on device, no transcode): guid=%s path=%s",
            guid_hint,
            track.path,
        )
        _notify_status(on_track_status, track.path, "skipped")
        return

    track_encode = _resolve_encode_settings(
        track,
        encode_settings=encode_settings,
        resolve_encode_settings=resolve_encode_settings,
    )
    if special is not None and special.encode is not None:
        track_encode = special.encode
    if special is not None and special.force_transcode:
        force_transcode = True
    track_fmt = (
        track_encode.file_extension()
        if track_encode is not None
        else target_format
    )
    prepared = prepare_track(
        track,
        target_format=track_fmt,
        transcoder=transcoder,
        slot=slot,
        reread_tags_after_convert=reread_tags_after_convert,
        on_track_status=on_track_status,
        device_formats=device_formats,
        should_cancel=should_cancel,
        encode_settings=track_encode,
        force_transcode=force_transcode,
        special=special,
    )
    try:
        raise_if_cancelled(should_cancel, total=1)
        _notify_status(on_track_status, track.path, "transferring")
        send_guid = prepared.guid if prepared.use_guid else None
        honor = bool(
            special is not None
            and special.folder_mode in ("artist", "artist_album")
        )
        parent_id = _resolve_parent(
            resolve_parent_folder,
            prepared.meta,
            guid=send_guid if prepared.use_guid else None,
            fixed_parent_id=prepared.fixed_parent_id,
            honor_resolver=honor,
        )
        object_id = transport.send_track(
            prepared.send_path,
            prepared.meta,
            parent_id=parent_id,
            guid=send_guid,
            preferred_basename=prepared.preferred_basename,
        )
        if on_after_send is not None:
            try:
                on_after_send(prepared.guid, prepared.send_path, object_id)
            except Exception:
                logger.debug("on_after_send failed", exc_info=True)
        _notify_status(on_track_status, track.path, "done")
    except Exception:
        _notify_status(on_track_status, track.path, "failed")
        raise
    finally:
        if prepared.cleanup_path is not None:
            transcoder.cleanup(prepared.cleanup_path)


def transfer_tracks(
    tracks: Sequence[Track] | BatchTransferQueue,
    *,
    target_format: str,
    transport: Transport,
    transcoder: Transcoder,
    on_progress: ProgressCallback | None = None,
    on_track_status: TrackStatusCallback | None = None,
    stop_on_fatal: bool = True,
    session_log: bool = True,
    resolve_parent_folder: ParentFolderResolver | None = None,
    device_formats: Collection[str] | None = None,
    should_cancel: CancelCheck | None = None,
    device_guid_stems: Collection[str] | None = None,
    on_after_send: AfterSendCallback | None = None,
    encode_settings: AudioEncodeSettings | None = None,
    resolve_encode_settings: EncodeSettingsResolver | None = None,
    special: SpecialSyncOptions | None = None,
) -> int:
    """Transfer many tracks with dual-slot convert/send pipeline.

    *tracks* may be a fixed sequence or a live :class:`BatchTransferQueue`
    that the UI can extend while this function runs (append artist/album/
    selection). The worker re-reads ``queue.total()`` each iteration.

    While track *i* is sent (blocking transport), track *i+1* is prepared on a
    helper thread into the alternate temp slot (``i % 2`` vs ``(i+1) % 2``).
    Returns number of successful sends (including skips counted as success).

    *on_track_status* receives ``(source_path, status)`` where status is one of
    ``transcoding``, ``transferring``, ``done``, ``skipped``, or ``failed``.

    *device_formats* lists extensions the player plays natively; those sources
    skip ffmpeg even when they differ from *target_format*.

    *device_guid_stems*: GUIDs already on the device (durable index); matching
    tracks skip both transcode and send.

    *resolve_encode_settings*: optional per-track encode recipe (e.g. audiobook
    override). When set, wins over the batch-level *encode_settings*.

    *special*: optional Special Sync overrides applied to every track in the
    batch (encode, meta patch, parent, GUID/basename). When Special Sync
    disables skip-if-present, *device_guid_stems* is ignored.

    *should_cancel*: when true between tracks, remaining items are skipped and
    :class:`~mtpmanager.app.cancellation.JobCancelled` is raised (the track
    already in flight still finishes).
    """
    if isinstance(tracks, BatchTransferQueue):
        track_queue = tracks
    else:
        track_queue = BatchTransferQueue(tracks)

    if special is not None and not special.effective_skip_if_present():
        device_guid_stems = None
    if special is not None and special.encode is not None:
        encode_settings = special.encode

    succeeded = 0
    skipped = 0
    session_handler = None
    if session_log:
        try:
            session_handler = start_transfer_log()
        except OSError as exc:
            logger.warning("Could not open transfer session log: %s", exc)

    logger.info(
        "Batch transfer start: %d track(s) target_format=%s "
        "device_formats=%s device_guid_stems=%s special=%s "
        "(queue dual-slot pipeline)",
        track_queue.total(),
        target_format,
        sorted(device_formats) if device_formats else None,
        len(device_guid_stems) if device_guid_stems is not None else None,
        special is not None,
    )

    prepared: PreparedTrack | None = None
    next_future: Future[PreparedTrack] | None = None

    def _cleanup(prep: PreparedTrack | None) -> None:
        if prep is not None and prep.cleanup_path is not None:
            transcoder.cleanup(prep.cleanup_path)

    def _cancel_next() -> None:
        nonlocal next_future
        if next_future is None:
            return
        next_future.cancel()
        try:
            if next_future.done() and not next_future.cancelled():
                nxt = next_future.result()
                _cleanup(nxt)
                if not nxt.already_on_device:
                    _notify_status(on_track_status, nxt.source_path, "failed")
        except Exception:
            pass
        next_future = None

    def _prepare(track: Track, slot: int) -> PreparedTrack:
        # Skip ffmpeg entirely when the host GUID is already on the device.
        guid_hint = track.guid if is_track_guid(track.guid) else ""
        if guid_hint and _guid_already_on_device(guid_hint, device_guid_stems):
            logger.info(
                "Skip prepare (already on device, no transcode): guid=%s path=%s",
                guid_hint,
                track.path,
            )
            return PreparedTrack(
                send_path="",
                meta=track.meta,
                cleanup_path=None,
                source_path=track.path,
                guid=guid_hint,
                already_on_device=True,
            )
        track_encode = _resolve_encode_settings(
            track,
            encode_settings=encode_settings,
            resolve_encode_settings=resolve_encode_settings,
        )
        if special is not None and special.encode is not None:
            track_encode = special.encode
        force_tc = bool(special is not None and special.force_transcode)
        track_fmt = (
            track_encode.file_extension()
            if track_encode is not None
            else target_format
        )
        return prepare_track(
            track,
            target_format=track_fmt,
            transcoder=transcoder,
            slot=slot,
            on_track_status=on_track_status,
            device_formats=device_formats,
            should_cancel=should_cancel,
            encode_settings=track_encode,
            force_transcode=force_tc,
            special=special,
        )

    def _live_total() -> int:
        return max(1, track_queue.total())

    def _user_cancel(*, at_index: int) -> None:
        nonlocal prepared
        total_now = track_queue.total()
        remaining = max(0, total_now - at_index)
        logger.info(
            "Batch cancelled by user: succeeded=%d/%d remaining_not_started=%d",
            succeeded,
            total_now,
            remaining,
        )
        _cancel_next()
        _cleanup(prepared)
        prepared = None
        if on_progress and total_now:
            on_progress(succeeded, total_now, "")
        raise JobCancelled(
            f"Cancelled after {succeeded} of {total_now} track(s)",
            completed=succeeded,
            total=total_now,
        )

    try:
        first = track_queue.track_at(0)
        if first is None:
            return 0

        raise_if_cancelled(should_cancel, total=_live_total())

        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mtpmanager-prep"
        ) as pool:
            prepared = _prepare(first, 0)
            i = 0

            while True:
                track = track_queue.track_at(i)
                if track is None:
                    break

                total = track_queue.total()

                # Between items: honor cancel before starting the next send.
                if should_cancel is not None and should_cancel():
                    _user_cancel(at_index=i)
                    return succeeded  # unreachable; _user_cancel raises

                if on_progress:
                    on_progress(i, total, track.path)
                logger.info("%d/%d - %s", i + 1, total, track.path)

                # Dual-slot: start preparing the next known track if any.
                if next_future is None:
                    next_track = track_queue.track_at(i + 1)
                    if next_track is not None:
                        next_future = pool.submit(
                            _prepare, next_track, (i + 1) % 2
                        )

                assert prepared is not None
                try:
                    if prepared.already_on_device or _guid_already_on_device(
                        prepared.guid, device_guid_stems
                    ):
                        logger.info(
                            "Skip (already on device, no transcode): "
                            "guid=%s path=%s",
                            prepared.guid,
                            track.path,
                        )
                        succeeded += 1
                        skipped += 1
                        _notify_status(on_track_status, track.path, "skipped")
                    else:
                        _notify_status(
                            on_track_status, track.path, "transferring"
                        )
                        send_guid = (
                            prepared.guid if prepared.use_guid else None
                        )
                        honor = bool(
                            special is not None
                            and special.folder_mode
                            in ("artist", "artist_album")
                        )
                        parent_id = _resolve_parent(
                            resolve_parent_folder,
                            prepared.meta,
                            guid=send_guid if prepared.use_guid else None,
                            fixed_parent_id=prepared.fixed_parent_id,
                            honor_resolver=honor,
                        )
                        object_id = transport.send_track(
                            prepared.send_path,
                            prepared.meta,
                            parent_id=parent_id,
                            guid=send_guid,
                            preferred_basename=prepared.preferred_basename,
                        )
                        if on_after_send is not None:
                            try:
                                on_after_send(
                                    prepared.guid,
                                    prepared.send_path,
                                    object_id,
                                )
                            except Exception:
                                logger.debug(
                                    "on_after_send failed", exc_info=True
                                )
                        succeeded += 1
                        _notify_status(on_track_status, track.path, "done")
                except TransportError as exc:
                    total = track_queue.total()
                    remaining = total - i - 1
                    logger.error(
                        "FAILED (%d/%d): %s fatal=%s path=%s rc=%s",
                        i + 1,
                        total,
                        exc,
                        exc.fatal,
                        exc.path or track.path,
                        exc.returncode,
                    )
                    if exc.stderr:
                        logger.error("Transport stderr:\n%s", exc.stderr)
                    _notify_status(on_track_status, track.path, "failed")
                    _cleanup(prepared)
                    prepared = None
                    if exc.fatal and stop_on_fatal:
                        _cancel_next()
                        logger.error(
                            "Aborting batch: device/session looks unusable. "
                            "%d track(s) not attempted. Succeeded: %d/%d.",
                            remaining,
                            succeeded,
                            total,
                        )
                        if on_progress and total:
                            on_progress(i + 1, total, track.path)
                        raise
                    logger.warning(
                        "Continuing after non-fatal failure (%d left).",
                        remaining,
                    )
                else:
                    _cleanup(prepared)
                    prepared = None

                i += 1

                if next_future is not None:
                    try:
                        prepared = next_future.result()
                    except JobCancelled:
                        next_future = None
                        total = track_queue.total()
                        raise JobCancelled(
                            f"Cancelled after {succeeded} of {total} track(s)",
                            completed=succeeded,
                            total=total,
                        )
                    except Exception:
                        next_future = None
                        raise
                    next_future = None
                else:
                    # Maybe the UI enqueued more tracks during the last send.
                    nxt = track_queue.track_at(i)
                    if nxt is not None:
                        prepared = _prepare(nxt, i % 2)
                    else:
                        prepared = None
                        break

        total = track_queue.total()
        if on_progress and total:
            on_progress(total, total, "")
        logger.info(
            "Batch transfer finished: succeeded=%d/%d skipped=%d",
            succeeded,
            total,
            skipped,
        )
        return succeeded
    finally:
        _cleanup(prepared)
        _cancel_next()
        stop_transfer_log(session_handler)
