"""Shrink: re-encode on-device tracks with more aggressive compression.

Identify host library/podcast tracks already on the device (by GUID), delete
the MTP objects, re-send with a forced convert, and rewrite any device
playlists that referenced the old object ids.

Playlist membership is snapshotted *before* deletes: after a track is removed,
many players drop the dead id from playlist associations, so a re-list would
never see the old object id and the rewrite would no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from mtpmanager.app.device_ops import delete_object
from mtpmanager.app.playlist_device import load_device_playlists_for_lookup
from mtpmanager.app.transfer import transfer_track
from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    closest_preset_for_bitrate,
    estimate_settings_bitrate_kbps,
    presets_for_format,
    resolve_settings,
)
from mtpmanager.domain.device_profile import needs_transcode
from mtpmanager.domain.models import Track
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.device_index import (
    item_id_for_guid,
    item_ids_for_guids,
    remove_by_item_id,
)
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShrinkItem:
    """One host track that has a real object id on the device."""

    track: Track
    guid: str
    item_id: int


@dataclass
class ShrinkResult:
    attempted: int = 0
    deleted: int = 0
    resent: int = 0
    playlists_updated: int = 0
    skipped: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def would_passthrough(
    path: str,
    settings: AudioEncodeSettings,
) -> bool:
    """True when prepare would not re-encode (already target container)."""
    if settings.needs_tempo_filter():
        return False
    return not needs_transcode(
        path,
        target_format=settings.file_extension(),
        device_formats=None,
    )


def resolve_shrink_encode_settings(
    track: Track,
    *,
    configured: AudioEncodeSettings,
    user_override: AudioEncodeSettings | None = None,
    allowed_formats: frozenset[str] | None = None,
) -> AudioEncodeSettings:
    """Pick encode recipe for a shrink send.

    *user_override* wins when provided (dialog). Otherwise use *configured*
    (global / audiobook / podcast override already resolved for the track).
    """
    base = user_override if user_override is not None else configured
    return resolve_settings(settings=base, allowed_formats=allowed_formats)


def suggest_shrink_preset(
    track: Track,
    *,
    configured: AudioEncodeSettings,
    allowed_formats: frozenset[str] | None = None,
):
    """Closest ladder step to the on-disk bitrate for the shrink dialog."""
    fmt = configured.normalized_format()
    br: int | None = None
    try:
        meta = read_metadata(track.path)
        if meta.bitrate and meta.bitrate > 0:
            # mutagen often reports bits/s
            b = int(meta.bitrate)
            br = b // 1000 if b > 1000 else b
    except Exception:
        logger.debug("suggest_shrink_preset tag read failed", exc_info=True)
    if br is None:
        br = estimate_settings_bitrate_kbps(configured)
    return closest_preset_for_bitrate(
        fmt, br, allowed_formats=allowed_formats
    )


def collect_shrink_items(
    tracks: Sequence[Track],
    *,
    serial: str,
) -> tuple[list[ShrinkItem], list[Track]]:
    """Partition tracks into on-device (shrinkable) vs missing from index."""
    guids = [t.guid for t in tracks if t and is_track_guid(t.guid)]
    mapping = item_ids_for_guids(serial, guids) if guids else {}
    items: list[ShrinkItem] = []
    missing: list[Track] = []
    for t in tracks:
        if not t or not is_track_guid(t.guid):
            missing.append(t)
            continue
        oid = mapping.get(t.guid) or item_id_for_guid(serial, t.guid)
        if oid is None or int(oid) <= 0:
            missing.append(t)
            continue
        items.append(ShrinkItem(track=t, guid=t.guid, item_id=int(oid)))
    return items, missing


@dataclass
class PlaylistRewriteWorkspace:
    """Mutable playlist memberships snapshotted *before* shrink deletes.

    After a track is deleted, many devices (incl. Creative ZEN) drop the dead
    object id from playlist associations. Re-listing then never sees *old_id*,
    so a naive rewrite no-ops. Keep pre-delete memberships and remap in place.
    """

    playlist_id: int
    name: str
    parent_id: int
    track_ids: list[int]


def snapshot_playlists_for_rewrite(
    device,
    *,
    serial: str,
    parent_id: int | None = None,
) -> list[PlaylistRewriteWorkspace]:
    """Load device playlists once (full track lists) for id remapping."""
    try:
        listed = load_device_playlists_for_lookup(
            device, serial=serial, parent_id=parent_id
        )
    except Exception:
        logger.warning("snapshot playlists for rewrite failed", exc_info=True)
        return []
    out: list[PlaylistRewriteWorkspace] = []
    for pl in listed:
        ids = [int(x) for x in (pl.track_ids or ()) if int(x) > 0]
        out.append(
            PlaylistRewriteWorkspace(
                playlist_id=int(pl.playlist_id),
                name=(pl.name or "").strip()
                or f"Playlist {pl.playlist_id}",
                parent_id=int(getattr(pl, "parent_id", 0) or 0),
                track_ids=ids,
            )
        )
    logger.info(
        "Shrink: snapshotted %d playlist(s) for item-id rewrite",
        len(out),
    )
    return out


def rewrite_playlists_item_id(
    device,
    *,
    old_id: int,
    new_id: int,
    workspace: Sequence[PlaylistRewriteWorkspace],
) -> int:
    """Replace *old_id* with *new_id* in *workspace* playlists and push updates.

    Mutates each matching workspace entry's ``track_ids``. Returns the number
    of playlists successfully written to the device.
    """
    old_id = int(old_id)
    new_id = int(new_id)
    if old_id <= 0 or new_id <= 0 or old_id == new_id:
        return 0
    if not hasattr(device, "update_playlist"):
        return 0
    updated = 0
    for pl in workspace:
        ids = list(pl.track_ids)
        if old_id not in ids:
            continue
        remapped = [new_id if x == old_id else x for x in ids]
        # Dedupe while preserving order (same track twice after remap).
        seen: set[int] = set()
        ordered: list[int] = []
        for x in remapped:
            if x in seen:
                continue
            seen.add(x)
            ordered.append(x)
        name = (pl.name or "").strip() or f"Playlist {pl.playlist_id}"
        parent = int(pl.parent_id or 0) or None
        try:
            device.update_playlist(
                int(pl.playlist_id),
                name,
                ordered,
                parent_id=parent,
            )
            pl.track_ids = ordered
            pl.name = name
            updated += 1
            logger.info(
                "Shrink: rewrote playlist id=%s name=%r %s→%s",
                pl.playlist_id,
                name,
                old_id,
                new_id,
            )
        except Exception:
            logger.exception(
                "Shrink: playlist update failed id=%s", pl.playlist_id
            )
    return updated


def shrink_items(
    items: Sequence[ShrinkItem],
    *,
    serial: str,
    device,
    transport: Transport,
    transcoder: Transcoder,
    encode_for_item: Callable[[ShrinkItem], AudioEncodeSettings],
    resolve_parent_folder=None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_after_send=None,
    playlist_parent_id: int | None = None,
) -> ShrinkResult:
    """Delete each item on-device, re-send with forced encode, fix playlists."""
    result = ShrinkResult()
    total = len(items)
    # Snapshot memberships BEFORE deletes — device may prune dead track ids.
    workspace = snapshot_playlists_for_rewrite(
        device, serial=serial, parent_id=playlist_parent_id
    )
    for i, item in enumerate(items):
        if should_cancel is not None and should_cancel():
            break
        result.attempted += 1
        label = (
            (item.track.meta.title if item.track.meta else "")
            or item.guid[:8]
        )
        if on_progress:
            on_progress(i, total, label)
        old_id = int(item.item_id)
        try:
            delete_object(device, old_id)
            result.deleted += 1
        except Exception as e:
            msg = f"delete failed {label!r} id={old_id}: {e}"
            logger.exception("%s", msg)
            result.errors.append(msg)
            continue
        try:
            remove_by_item_id(serial, old_id)
        except Exception:
            logger.debug("index remove after shrink delete failed", exc_info=True)

        settings = encode_for_item(item)
        fmt = settings.normalized_format()
        new_id_box: list[int | None] = [None]

        def _after(guid: str, path: str, oid: int | None, _box=new_id_box) -> None:
            if oid is not None and int(oid) > 0:
                _box[0] = int(oid)
            if on_after_send is not None:
                try:
                    on_after_send(guid, path, oid)
                except Exception:
                    logger.debug("shrink on_after_send failed", exc_info=True)

        try:
            transfer_track(
                item.track,
                target_format=fmt,
                transport=transport,
                transcoder=transcoder,
                resolve_parent_folder=resolve_parent_folder,
                device_formats=None,  # don't passthrough native formats
                should_cancel=should_cancel,
                device_guid_stems=None,  # we just deleted; don't skip
                on_after_send=_after,
                encode_settings=settings,
                force_transcode=True,
            )
            result.resent += 1
        except Exception as e:
            msg = f"re-send failed {label!r}: {e}"
            logger.exception("%s", msg)
            result.errors.append(msg)
            continue

        new_id = new_id_box[0]
        if new_id is not None and new_id > 0:
            try:
                n = rewrite_playlists_item_id(
                    device,
                    old_id=old_id,
                    new_id=new_id,
                    workspace=workspace,
                )
                result.playlists_updated += n
            except Exception as e:
                logger.warning(
                    "playlist rewrite failed old=%s new=%s: %s",
                    old_id,
                    new_id,
                    e,
                )
        else:
            logger.warning(
                "Shrink re-send produced no object id for guid=%s…",
                item.guid[:8],
            )

    if on_progress and total:
        on_progress(total, total, "")
    return result
