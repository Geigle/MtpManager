"""Device administration use cases."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mtpmanager.app.cancellation import CancelCheck
from mtpmanager.domain.device_media import apply_track_info
from mtpmanager.domain.models import (
    DeleteAllResult,
    DeviceInfo,
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    FolderEntry,
)
from mtpmanager.infra.remote_naming import DEFAULT_MUSIC_FOLDER_ID
from mtpmanager.ports.device import DevicePort
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)

# done_count, total, current track (or None when finished)
DeleteProgressCallback = Callable[[int, int, DeviceTrackRef | None], None]
# done_count, total, message
EnrichProgressCallback = Callable[[int, int, str], None]


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
