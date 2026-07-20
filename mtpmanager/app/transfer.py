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
from mtpmanager.domain.device_profile import needs_transcode
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
# source_path, status: "transcoding" | "transferring" | "done" | "failed"
TrackStatusCallback = Callable[[str, str], None]
# Optional: resolve MTP parent folder id for a track (artist folders, etc.).
ParentFolderResolver = Callable[[TrackMetadata], int | None]


@dataclass(frozen=True)
class PreparedTrack:
    """Local path + metadata ready for transport.send_track."""

    send_path: str
    meta: TrackMetadata
    cleanup_path: str | None
    source_path: str


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
) -> PreparedTrack:
    """Transcode into *slot* if needed; return path/meta for send (no send yet).

    When *device_formats* is set, sources already in a native device format are
    sent as-is (no re-encode), even if they differ from *target_format*.
    """
    raise_if_cancelled(should_cancel)
    target_format = target_format.lower().lstrip(".")
    src = track.path
    meta = track.meta
    cleanup_path: str | None = None

    if needs_transcode(
        src, target_format=target_format, device_formats=device_formats
    ):
        _notify_status(on_track_status, track.path, "transcoding")
        src = transcoder.convert(src, target_format, slot=slot)
        cleanup_path = src
        if reread_tags_after_convert:
            converted = read_metadata(src)
            meta = _merge_meta_after_convert(meta, converted)
    else:
        logger.info(
            "Passthrough (no transcode): %s (target=%s device_formats=%s)",
            src,
            target_format,
            sorted(device_formats) if device_formats else None,
        )

    return PreparedTrack(
        send_path=src,
        meta=meta,
        cleanup_path=cleanup_path,
        source_path=track.path,
    )


def _resolve_parent(
    resolver: ParentFolderResolver | None,
    meta: TrackMetadata,
) -> int | None:
    if resolver is None:
        return None
    return resolver(meta)


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
) -> None:
    """
    Ensure track is device-ready (transcode if needed), then send via transport.
    Temp files from the transcoder are always cleaned up.

    *should_cancel* is checked before prepare and before send (cannot abort an
    in-flight MTP/ffmpeg call).
    """
    raise_if_cancelled(should_cancel, total=1)
    prepared = prepare_track(
        track,
        target_format=target_format,
        transcoder=transcoder,
        slot=slot,
        reread_tags_after_convert=reread_tags_after_convert,
        on_track_status=on_track_status,
        device_formats=device_formats,
        should_cancel=should_cancel,
    )
    try:
        raise_if_cancelled(should_cancel, total=1)
        _notify_status(on_track_status, track.path, "transferring")
        parent_id = _resolve_parent(resolve_parent_folder, prepared.meta)
        transport.send_track(
            prepared.send_path, prepared.meta, parent_id=parent_id
        )
        _notify_status(on_track_status, track.path, "done")
    except Exception:
        _notify_status(on_track_status, track.path, "failed")
        raise
    finally:
        if prepared.cleanup_path is not None:
            transcoder.cleanup(prepared.cleanup_path)


def transfer_tracks(
    tracks: Sequence[Track],
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
) -> int:
    """Transfer many tracks with dual-slot convert/send pipeline.

    While track *i* is sent (blocking transport), track *i+1* is prepared on a
    helper thread into the alternate temp slot (``i % 2`` vs ``(i+1) % 2``).
    Returns number of successful sends.

    *on_track_status* receives ``(source_path, status)`` where status is one of
    ``transcoding``, ``transferring``, ``done``, or ``failed``.

    *device_formats* lists extensions the player plays natively; those sources
    skip ffmpeg even when they differ from *target_format*.

    *should_cancel*: when true between tracks, remaining items are skipped and
    :class:`~mtpmanager.app.cancellation.JobCancelled` is raised (the track
    already in flight still finishes).
    """
    total = len(tracks)
    succeeded = 0
    session_handler = None
    if session_log:
        try:
            session_handler = start_transfer_log()
        except OSError as exc:
            logger.warning("Could not open transfer session log: %s", exc)

    logger.info(
        "Batch transfer start: %d track(s) target_format=%s "
        "device_formats=%s (dual-slot pipeline)",
        total,
        target_format,
        sorted(device_formats) if device_formats else None,
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
                _notify_status(on_track_status, nxt.source_path, "failed")
        except Exception:
            pass
        next_future = None

    def _prepare(track: Track, slot: int) -> PreparedTrack:
        return prepare_track(
            track,
            target_format=target_format,
            transcoder=transcoder,
            slot=slot,
            on_track_status=on_track_status,
            device_formats=device_formats,
            should_cancel=should_cancel,
        )

    def _user_cancel(*, at_index: int) -> None:
        nonlocal prepared
        remaining = max(0, total - at_index)
        logger.info(
            "Batch cancelled by user: succeeded=%d/%d remaining_not_started=%d",
            succeeded,
            total,
            remaining,
        )
        _cancel_next()
        _cleanup(prepared)
        prepared = None
        if on_progress and total:
            on_progress(succeeded, total, "")
        raise JobCancelled(
            f"Cancelled after {succeeded} of {total} track(s)",
            completed=succeeded,
            total=total,
        )

    try:
        if total == 0:
            return 0

        raise_if_cancelled(should_cancel, total=total)

        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mtpmanager-prep"
        ) as pool:
            prepared = _prepare(tracks[0], 0)

            for i, track in enumerate(tracks):
                # Between items: honor cancel before starting the next send.
                if should_cancel is not None and should_cancel():
                    _user_cancel(at_index=i)
                    return succeeded  # unreachable; _user_cancel raises

                if on_progress:
                    on_progress(i, total, track.path)
                logger.info("%d/%d - %s", i + 1, total, track.path)

                if i + 1 < total:
                    next_slot = (i + 1) % 2
                    next_track = tracks[i + 1]
                    next_future = pool.submit(_prepare, next_track, next_slot)

                assert prepared is not None
                try:
                    _notify_status(on_track_status, track.path, "transferring")
                    parent_id = _resolve_parent(
                        resolve_parent_folder, prepared.meta
                    )
                    transport.send_track(
                        prepared.send_path,
                        prepared.meta,
                        parent_id=parent_id,
                    )
                    succeeded += 1
                    _notify_status(on_track_status, track.path, "done")
                except TransportError as exc:
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

                if next_future is not None:
                    try:
                        prepared = next_future.result()
                    except JobCancelled:
                        next_future = None
                        # Prep thread saw cancel; report progress so far.
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
                    prepared = None

        if on_progress and total:
            on_progress(total, total, "")
        logger.info(
            "Batch transfer finished: succeeded=%d/%d",
            succeeded,
            total,
        )
        return succeeded
    finally:
        _cleanup(prepared)
        _cancel_next()
        stop_transfer_log(session_handler)
