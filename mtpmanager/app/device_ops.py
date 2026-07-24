"""Device administration use cases."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from mtpmanager.app.cancellation import CancelCheck
from mtpmanager.domain.device_media import apply_track_info
from mtpmanager.domain.models import (
    DeleteAllResult,
    DeviceInfo,
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    FolderEntry,
    TrackMetadata,
)
from mtpmanager.infra.mutagen_tags import write_metadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    build_remote_path,
    sanitize_component,
    split_remote_path,
)
from mtpmanager.ports.device import DevicePort
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

# done_count, total, current track (or None when finished)
DeleteProgressCallback = Callable[[int, int, DeviceTrackRef | None], None]
# done_count, total, message
EnrichProgressCallback = Callable[[int, int, str], None]
# done_count, total, current ref or None
RetrieveProgressCallback = Callable[[int, int, DeviceTrackRef | None], None]

_UNSAFE_HOST = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


@dataclass(frozen=True)
class EnrichTracksResult:
    """Outcome of on-demand tag fetch for a selection of track refs."""

    refs: list[DeviceTrackRef]
    updated: int
    failed: int
    aborted: bool = False
    failed_id: int | None = None


def connect(device: DevicePort) -> str:
    """Open MTP session only (no battery/storage probes)."""
    return device.connect()


def disconnect(device: DevicePort) -> None:
    device.disconnect()


def get_device_identity(device: DevicePort) -> DeviceInfo:
    """Lightweight identity (name / manufacturer / model) for connect + profile."""
    return device.get_identity()


def get_device_info(device: DevicePort) -> DeviceInfo:
    """Full diagnostics for Device → Device Info (optional fields soft-fail)."""
    return device.get_info()


def set_device_name(device: DevicePort, name: str) -> None:
    device.set_device_name(name)


def create_folder(
    device: DevicePort,
    name: str,
    parent: int = DEFAULT_MUSIC_FOLDER_ID,
) -> int:
    return device.create_folder(name, parent=parent)


def list_folders(device: DevicePort) -> list[FolderEntry]:
    return device.list_folders()


def list_files(device: DevicePort) -> list[FileEntry]:
    """Experimental full file listing (Device -> List Files)."""
    return device.list_files()


def list_tracks(
    device: DevicePort,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[DeviceTrackRef]:
    """Experimental track listing (List Tracks / Delete All Tracks).

    Default (mtp-tracks style): filelisting + per-id Get_Trackmetadata.
    *on_progress(done, total, message)* is optional.
    """
    if on_progress is None:
        return device.list_tracks()
    try:
        return device.list_tracks(on_progress=on_progress)
    except TypeError:
        # Fakes / older adapters without the kwarg.
        return device.list_tracks()


def enrich_track_refs(
    device: DevicePort,
    refs: Sequence[DeviceTrackRef],
    *,
    on_progress: EnrichProgressCallback | None = None,
    stop_on_fatal: bool = True,
) -> EnrichTracksResult:
    """Fetch on-device tags for *refs* via get_track_metadata (selection only).

    Non-fatal misses keep the original ref. Fatal ``TransportError`` aborts
    the rest of the batch (session likely poisoned) and returns partial
    updates already applied.
    """
    batch = list(refs)
    total = len(batch)
    out: list[DeviceTrackRef] = []
    updated = 0
    failed = 0
    logger.info("enrich_track_refs start total=%s", total)

    def _progress(done: int, message: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, message)
        except Exception:
            logger.debug("enrich_track_refs on_progress failed", exc_info=True)

    for i, ref in enumerate(batch):
        oid = int(getattr(ref, "item_id", 0) or 0)
        label = (ref.name or ref.title or "").strip() or f"id={oid}"
        _progress(i, f"loading tags... {i + 1}/{total}  {label}")
        if oid <= 0:
            out.append(ref)
            failed += 1
            continue
        try:
            info = device.get_track_metadata(oid)
        except TransportError as exc:
            logger.warning(
                "enrich_track_refs failed id=%s label=%r fatal=%s: %s",
                oid,
                label,
                exc.fatal,
                exc,
            )
            out.append(ref)
            failed += 1
            if stop_on_fatal and exc.fatal:
                # Keep remaining refs unchanged.
                out.extend(batch[i + 1 :])
                _progress(i, f"aborted at id={oid}")
                return EnrichTracksResult(
                    refs=out,
                    updated=updated,
                    failed=failed,
                    aborted=True,
                    failed_id=oid,
                )
            continue
        except Exception:
            logger.exception("enrich_track_refs unexpected id=%s", oid)
            out.append(ref)
            failed += 1
            continue
        out.append(apply_track_info(ref, info))
        updated += 1

    _progress(total, f"loaded tags for {updated}/{total}")
    logger.info(
        "enrich_track_refs done updated=%s failed=%s total=%s",
        updated,
        failed,
        total,
    )
    return EnrichTracksResult(refs=out, updated=updated, failed=failed)


def delete_object(device: DevicePort, object_id: int) -> None:
    """Experimental single-object delete (Device -> Delete Track)."""
    device.delete_object(int(object_id))


def get_file_metadata(device: DevicePort, object_id: int) -> FileEntry:
    """Experimental single-object metadata (Device -> Get File Info)."""
    return device.get_file_metadata(int(object_id))


def get_track_metadata(device: DevicePort, object_id: int) -> DeviceTrackInfo:
    """Experimental on-device track tags (Device -> Get Track Info)."""
    return device.get_track_metadata(int(object_id))


@dataclass(frozen=True)
class RetrievedItem:
    """One retrieve attempt with full detail for the export map."""

    ref: DeviceTrackRef
    path: str | None = None
    info: DeviceTrackInfo | None = None
    status: str = "ok"  # ok | failed
    error: str = ""
    tags_written: bool = False


@dataclass(frozen=True)
class RetrieveTracksResult:
    """Outcome of experimental Get Tracks from Device."""

    total: int
    succeeded: int
    failed: int
    paths: list[str] = field(default_factory=list)
    items: list[RetrievedItem] = field(default_factory=list)
    aborted: bool = False
    cancelled: bool = False
    failed_id: int | None = None
    map_json_path: str = ""
    map_md_path: str = ""


def track_info_to_metadata(info: DeviceTrackInfo) -> TrackMetadata:
    """Map on-device track tags to host TrackMetadata for mutagen write."""
    tn = str(info.tracknumber or "").strip() or "01"
    if info.tracknumber and int(info.tracknumber) > 0:
        tn = f"{int(info.tracknumber):02d}"
    length = 0.0
    if info.duration_ms and info.duration_ms > 0:
        length = float(info.duration_ms) / 1000.0
    return TrackMetadata(
        artist=(info.artist or "").strip() or "Unknown Artist",
        albumartist=(info.artist or "").strip() or "Unknown Artist",
        composer=(info.composer or "").strip() or "Unknown Composer",
        album=(info.album or "").strip() or "Unknown Album",
        title=(info.title or "").strip() or "Unknown Title",
        genre=(info.genre or "").strip() or "Unknown Genre",
        tracknumber=tn,
        date=(info.date or "").strip(),
        length_sec=length,
        sample_rate=int(info.sample_rate or 0),
        channels=int(info.channels or 0),
        bitrate=int(info.bitrate or 0),
        bitrate_mode=int(info.bitrate_type or 0),
    )


def suggested_retrieve_basename(
    ref: DeviceTrackRef,
    *,
    info: DeviceTrackInfo | None = None,
) -> str:
    """Build a host-safe basename with extension from device name/tags."""
    raw_name = (ref.name or "").strip() or (info.name if info else "") or "track"
    _, ext = os.path.splitext(raw_name)
    if not ext:
        ext = ".mp3"
    ext = ext if ext.startswith(".") else f".{ext}"

    title = ""
    artist = ""
    if info is not None:
        title = (info.title or "").strip()
        artist = (info.artist or "").strip()
    if not title:
        title = (ref.title or "").strip()
    if not artist:
        artist = (ref.artist or "").strip()

    if title and artist and artist not in ("—", "Unknown Artist"):
        body = f"{artist} - {title}"
    elif title:
        body = title
    else:
        body = os.path.splitext(raw_name)[0] or f"track_{ref.item_id}"

    body = _UNSAFE_HOST.sub(" ", body)
    body = sanitize_component(body, 80)
    return f"{body}{ext.lower() if len(ext) <= 5 else ext}"


def unique_dest_path(dest_dir: str, basename: str) -> str:
    """Return dest_dir/basename, adding (n) before ext if the path exists."""
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    candidate = os.path.join(dest_dir, basename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(basename)
    n = 2
    while True:
        alt = os.path.join(dest_dir, f"{stem} ({n}){ext}")
        if not os.path.exists(alt):
            return alt
        n += 1


def retrieve_track(
    device: DevicePort,
    ref: DeviceTrackRef,
    dest_dir: str,
    *,
    info: DeviceTrackInfo | None = None,
    write_tags: bool = True,
) -> RetrievedItem:
    """Download one track/media object to *dest_dir*; optionally write tags.

    Uses ``get_file_to_file`` (works for audio and video). Tries track
    metadata when *info* is not provided. Returns a :class:`RetrievedItem`.
    """
    oid = int(ref.item_id or 0)
    if oid <= 0:
        raise ValueError(f"Invalid object id on ref: {ref!r}")

    meta_info = info
    if meta_info is None:
        try:
            meta_info = device.get_track_metadata(oid)
        except TransportError as exc:
            if exc.fatal:
                raise
            logger.debug(
                "retrieve_track: no track metadata id=%s (%s)", oid, exc
            )
            meta_info = None
        except Exception:
            logger.debug(
                "retrieve_track: track metadata failed id=%s", oid, exc_info=True
            )
            meta_info = None

    basename = suggested_retrieve_basename(ref, info=meta_info)
    dest = unique_dest_path(dest_dir, basename)
    logger.info(
        "retrieve_track id=%s name=%r → %s", oid, ref.name, dest
    )

    # Prefer generic file download (audio + video). Fall back if needed.
    getter = getattr(device, "get_file_to_file", None)
    if getter is None:
        raise TransportError(
            "Device adapter does not support get_file_to_file",
            fatal=True,
        )
    getter(oid, dest)

    tags_written = False
    if write_tags and meta_info is not None:
        host_meta = track_info_to_metadata(meta_info)
        # Skip placeholder-only writes
        if (host_meta.title and host_meta.title != "Unknown Title") or (
            host_meta.artist and host_meta.artist not in ("Unknown Artist",)
        ):
            if write_metadata(dest, host_meta):
                tags_written = True
                logger.info("retrieve_track wrote tags path=%s", dest)

    return RetrievedItem(
        ref=ref,
        path=dest,
        info=meta_info,
        status="ok",
        tags_written=tags_written,
    )


def retrieve_tracks(
    device: DevicePort,
    refs: Sequence[DeviceTrackRef],
    dest_dir: str,
    *,
    on_progress: RetrieveProgressCallback | None = None,
    stop_on_fatal: bool = True,
    should_cancel: CancelCheck | None = None,
    write_tags: bool = True,
    device_info: DeviceInfo | None = None,
    write_map: bool = True,
) -> RetrieveTracksResult:
    """Download many tracks to *dest_dir* with best-effort metadata.

    When *write_map* is True (default), writes verbose editable
    ``device_media_map.json`` + ``device_media_map.md`` into *dest_dir*.
    """
    from mtpmanager.infra.device_export_map import (
        build_entry_dict,
        build_map_document,
        filetype_label,
        write_export_maps,
    )
    from mtpmanager.infra.mutagen_tags import read_metadata

    batch = list(refs)
    total = len(batch)
    paths: list[str] = []
    items: list[RetrievedItem] = []
    succeeded = 0
    failed = 0
    logger.info("retrieve_tracks start total=%s dest=%s", total, dest_dir)

    def _progress(done: int, current: DeviceTrackRef | None) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, current)
        except Exception:
            logger.debug("retrieve_tracks on_progress failed", exc_info=True)

    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    def _finish(
        *,
        aborted: bool = False,
        cancelled: bool = False,
        failed_id: int | None = None,
    ) -> RetrieveTracksResult:
        map_json = ""
        map_md = ""
        if write_map:
            try:
                info = device_info
                if info is None:
                    try:
                        info = device.get_identity()
                    except Exception:
                        info = DeviceInfo()
                entry_dicts = []
                for idx, item in enumerate(items, start=1):
                    host_tags: dict = {}
                    if item.path and os.path.isfile(item.path):
                        try:
                            hm = read_metadata(item.path)
                            host_tags = {
                                "title": hm.title,
                                "artist": hm.artist,
                                "album": hm.album,
                                "genre": hm.genre,
                                "tracknumber": hm.tracknumber,
                                "date": hm.date,
                                "length_sec": hm.length_sec,
                                "sample_rate": hm.sample_rate,
                                "channels": hm.channels,
                                "bitrate": hm.bitrate,
                            }
                        except Exception:
                            pass
                    ft = int(
                        (item.info.filetype if item.info else 0)
                        or (item.ref.filetype or 0)
                    )
                    entry_dicts.append(
                        build_entry_dict(
                            index=idx,
                            ref=item.ref,
                            info=item.info,
                            host_path=item.path,
                            status=item.status,
                            error=item.error,
                            tags_written=item.tags_written,
                            host_tags=host_tags,
                            filetype_desc=filetype_label(ft, device=device),
                            export_dir=dest_dir,
                        )
                    )
                doc = build_map_document(
                    entries=entry_dicts,
                    dest_dir=dest_dir,
                    device_info=info,
                )
                jpath, mpath = write_export_maps(doc, dest_dir)
                map_json = str(jpath)
                map_md = str(mpath)
            except Exception:
                logger.exception("Failed to write device export map")
        return RetrieveTracksResult(
            total=total,
            succeeded=succeeded,
            failed=failed,
            paths=paths,
            items=items,
            aborted=aborted,
            cancelled=cancelled,
            failed_id=failed_id,
            map_json_path=map_json,
            map_md_path=map_md,
        )

    for i, ref in enumerate(batch):
        if _cancelled():
            logger.info(
                "retrieve_tracks cancelled succeeded=%s/%s",
                succeeded,
                total,
            )
            _progress(succeeded, None)
            return _finish(cancelled=True)
        _progress(i, ref)
        try:
            item = retrieve_track(
                device, ref, dest_dir, write_tags=write_tags
            )
            items.append(item)
            if item.path:
                paths.append(item.path)
            succeeded += 1
        except TransportError as exc:
            logger.error(
                "retrieve_tracks failed id=%s fatal=%s: %s",
                ref.item_id,
                exc.fatal,
                exc,
            )
            items.append(
                RetrievedItem(
                    ref=ref,
                    status="failed",
                    error=str(exc),
                )
            )
            failed += 1
            if stop_on_fatal and exc.fatal:
                _progress(i, ref)
                return _finish(
                    aborted=True, failed_id=int(ref.item_id or 0)
                )
        except Exception as exc:
            logger.exception("retrieve_tracks unexpected id=%s", ref.item_id)
            items.append(
                RetrievedItem(
                    ref=ref,
                    status="failed",
                    error=str(exc),
                )
            )
            failed += 1

    _progress(total, None)
    logger.info(
        "retrieve_tracks done succeeded=%s failed=%s total=%s",
        succeeded,
        failed,
        total,
    )
    return _finish()


def delete_all_tracks(
    device: DevicePort,
    tracks: Sequence[DeviceTrackRef] | None = None,
    *,
    on_progress: DeleteProgressCallback | None = None,
    stop_on_fatal: bool = True,
    should_cancel: CancelCheck | None = None,
) -> DeleteAllResult:
    """Delete every track on the device (or the provided snapshot).

    Uses the track listing (music/video) so folders/photos are left alone.
    On fatal ``TransportError``, aborts remaining deletes - the MTP session
    is likely poisoned (same policy as transfer batches).

    *should_cancel*: when true between deletes, remaining items are skipped
    (the delete already in flight still finishes).
    """
    batch = list(tracks) if tracks is not None else list_tracks(device)
    # Stable unique positive ids (listing can theoretically repeat).
    seen: set[int] = set()
    ordered: list[DeviceTrackRef] = []
    for t in batch:
        oid = int(getattr(t, "item_id", 0) or 0)
        if oid <= 0 or oid in seen:
            continue
        seen.add(oid)
        ordered.append(t)

    total = len(ordered)
    deleted = 0
    deleted_ids: list[int] = []
    logger.info("delete_all_tracks start total=%s", total)

    def _progress(done: int, current: DeviceTrackRef | None) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, current)
        except Exception:
            logger.debug("delete_all on_progress failed", exc_info=True)

    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    for t in ordered:
        if _cancelled():
            logger.info(
                "delete_all_tracks cancelled by user deleted=%s/%s",
                deleted,
                total,
            )
            _progress(deleted, None)
            return DeleteAllResult(
                total=total,
                deleted=deleted,
                cancelled=True,
                deleted_ids=tuple(deleted_ids),
            )
        _progress(deleted, t)
        oid = int(t.item_id)
        label = (t.name or t.title or "").strip() or f"id={oid}"
        try:
            device.delete_object(oid)
        except TransportError as exc:
            logger.error(
                "delete_all_tracks failed at id=%s label=%r deleted=%s/%s fatal=%s: %s",
                oid,
                label,
                deleted,
                total,
                exc.fatal,
                exc,
            )
            if stop_on_fatal and exc.fatal:
                _progress(deleted, t)
                return DeleteAllResult(
                    total=total,
                    deleted=deleted,
                    failed_id=oid,
                    aborted=True,
                    deleted_ids=tuple(deleted_ids),
                )
            raise
        deleted += 1
        deleted_ids.append(oid)
        logger.info(
            "delete_all_tracks deleted id=%s label=%r (%s/%s)",
            oid,
            label,
            deleted,
            total,
        )

    _progress(deleted, None)
    logger.info("delete_all_tracks done deleted=%s/%s", deleted, total)
    return DeleteAllResult(
        total=total,
        deleted=deleted,
        deleted_ids=tuple(deleted_ids),
    )


def send_test_file(device: DevicePort, path: str) -> None:
    device.send_file(path)


# Ad-hoc video send destinations (Device → Send Video…).
VIDEO_PARENT_CHOICES: frozenset[int] = frozenset(
    {DEFAULT_VIDEO_FOLDER_ID, DEFAULT_TV_FOLDER_ID}
)

# on_progress(kind, *args) kinds used by Send Video worker:
#   "phase", "transcode"|"send"     — status line phase labels
#   "progress", done, total, label  — determinate bar (0–100 domain)
#   "status", message               — status line only
SendVideoProgress = Callable[..., None]


@dataclass(frozen=True)
class SendVideoResult:
    """Outcome of an ad-hoc video send."""

    object_id: int | None
    parent_id: int
    remote_basename: str
    path: str  # path actually sent (may be temp encode)
    source_path: str = ""
    encoded: bool = False
    encode_skipped_compatible: bool = False


def send_video(
    transport: Transport,
    path: str,
    *,
    parent_id: int,
    title: str | None = None,
    preferred_basename: str | None = None,
) -> SendVideoResult:
    """Send a local video file under Video (120) or TV (124).

    Uses the normal track-send path (``LIBMTP_Send_Track_From_File``) so
    storage_id, filetype, and parent match the ZEN contract — same approach
    as retail video restore. ObjectFileName is the sanitized host basename
    (no library GUID).
    """
    parent = int(parent_id)
    if parent not in VIDEO_PARENT_CHOICES:
        raise ValueError(
            f"parent_id must be Video ({DEFAULT_VIDEO_FOLDER_ID}) or "
            f"TV ({DEFAULT_TV_FOLDER_ID}), got {parent}"
        )
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Video file not found: {path!r}")

    base = (preferred_basename or os.path.basename(path)).strip()
    if not base:
        base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if not ext:
        _, path_ext = os.path.splitext(path)
        ext = path_ext or ".avi"
        base = f"{stem or 'video'}{ext}"
    display_title = (title or stem or "Video").strip() or "Video"
    meta = TrackMetadata(
        title=display_title,
        artist="Unknown Artist",
        album="Unknown Album",
        tracknumber="01",
    )
    remote = build_remote_path(
        meta,
        ext,
        music_folder_id=parent,
        preferred_basename=base,
    )
    _, remote_base = split_remote_path(remote)
    logger.info(
        "send_video path=%s parent=%s remote=%s",
        path,
        parent,
        remote_base,
    )
    object_id = transport.send_track(
        path,
        meta,
        parent_id=parent,
        guid=None,
        preferred_basename=base,
    )
    return SendVideoResult(
        object_id=object_id,
        parent_id=parent,
        remote_basename=remote_base,
        path=path,
        source_path=path,
        encoded=False,
    )


def prepare_and_send_video(
    transport: Transport,
    source_path: str,
    *,
    parent_id: int,
    encode_profile=None,
    encode_for_device: bool = False,
    ignore_max_fps: bool = False,
    on_progress: SendVideoProgress | None = None,
    title: str | None = None,
) -> SendVideoResult:
    """Optional device-profile encode, then :func:`send_video`.

    When *encode_for_device* and *encode_profile* are set, re-encodes to the
    retail-demo fingerprint unless the source already matches. Temp encodes
    are cleaned up after send (success or failure).

    *ignore_max_fps*: when encoding, skip the profile's max_fps cap (keep
    source rate above the device limit — experimental).
    """
    from mtpmanager.domain.device_profile import VideoEncodeProfile
    from mtpmanager.infra.ffmpeg_video import (
        cleanup_video_temp,
        convert_video_for_profile,
        default_temp_video_path,
        video_matches_encode_profile,
    )

    def _emit(kind: str, *args) -> None:
        if on_progress is None:
            return
        try:
            on_progress(kind, *args)
        except Exception:
            logger.debug("send_video on_progress failed", exc_info=True)

    src = source_path
    if not src or not os.path.isfile(src):
        raise FileNotFoundError(f"Video file not found: {src!r}")

    profile: VideoEncodeProfile | None = encode_profile
    send_path = src
    temp_path: str | None = None
    encoded = False
    skipped_ok = False
    source_stem = os.path.splitext(os.path.basename(src))[0] or "video"
    skip_cap = bool(ignore_max_fps) and encode_for_device

    try:
        if encode_for_device and profile is not None:
            if video_matches_encode_profile(src, profile):
                skipped_ok = True
                logger.info(
                    "Send Video: source already matches profile %s — skip encode",
                    profile.id,
                )
                _emit("status", "already device-compatible — skipping encode")
            else:
                _emit("phase", "transcode")
                _emit("progress", 0, 100, "encoding for device…")
                temp_path = default_temp_video_path(profile)

                def _enc_progress(done: float, total: float, msg: str) -> None:
                    if total and total > 0:
                        # Reserve 0–85% of the bar for encode.
                        pct = int(min(85, max(0, (done / total) * 85)))
                        _emit("progress", pct, 100, msg)
                    else:
                        _emit("status", msg)

                if skip_cap:
                    logger.info(
                        "Send Video: ignore_max_fps — not applying profile "
                        "max_fps=%s (experimental)",
                        profile.max_fps,
                    )
                send_path = convert_video_for_profile(
                    src,
                    profile,
                    dest_path=temp_path,
                    on_progress=_enc_progress,
                    ignore_max_fps=skip_cap,
                )
                encoded = True
                _emit("progress", 85, 100, "encode complete — sending…")

        # ObjectFileName: keep host stem; use encoded extension when converted.
        if encoded and profile is not None:
            pref = f"{source_stem}.{profile.container.lstrip('.')}"
        else:
            pref = os.path.basename(src)

        _emit("phase", "send")
        _emit("status", "sending to device…")
        _emit("progress", 90 if encoded or skipped_ok else 0, 100, "sending…")

        result = send_video(
            transport,
            send_path,
            parent_id=parent_id,
            title=title or source_stem,
            preferred_basename=pref,
        )
        _emit("progress", 100, 100, "done")
        return SendVideoResult(
            object_id=result.object_id,
            parent_id=result.parent_id,
            remote_basename=result.remote_basename,
            path=result.path,
            source_path=src,
            encoded=encoded,
            encode_skipped_compatible=skipped_ok,
        )
    finally:
        if temp_path:
            cleanup_video_temp(temp_path)
