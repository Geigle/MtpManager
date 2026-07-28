"""Push a host playlist onto the device as an MTP playlist object.

Track files must already be on the device. Membership is resolved via host
track GUIDs → real MTP object ids in ``device_files`` (item_id > 0).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mtpmanager.domain.device_folders import FolderRole
from mtpmanager.domain.models import DevicePlaylist, Track
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.device_index import item_ids_for_guids
from mtpmanager.infra.remote_naming import (
    DEFAULT_PLAYLIST_FOLDER_ID,
    DEFAULT_STORAGE_ID,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevicePlaylistPushResult:
    """Outcome of creating/updating an on-device playlist."""

    playlist_id: int
    name: str
    created: bool
    track_ids: tuple[int, ...]
    resolved: int
    missing_guid: int
    # GUIDs that could not be mapped to a real object id.
    unresolved_guids: tuple[str, ...] = ()


def ordered_guids_from_tracks(tracks: Sequence[Track]) -> list[str]:
    """Preserve playlist order; drop tracks without a valid host GUID."""
    out: list[str] = []
    for t in tracks:
        g = (t.guid or "").strip().lower() if t else ""
        if is_track_guid(g):
            out.append(g)
    return out


def resolve_track_object_ids(
    serial: str,
    guids: Sequence[str],
) -> tuple[list[int], list[str]]:
    """Map ordered GUIDs to object ids; return (ids_in_order, unresolved)."""
    mapping = item_ids_for_guids(serial, list(guids))
    ids: list[int] = []
    missing: list[str] = []
    for g in guids:
        oid = mapping.get(g)
        if oid is not None and int(oid) > 0:
            ids.append(int(oid))
        else:
            missing.append(g)
    return ids, missing


def find_device_playlist_by_name(
    playlists: Sequence[DevicePlaylist],
    name: str,
) -> DevicePlaylist | None:
    """Case-insensitive name match; first hit wins."""
    key = (name or "").strip().casefold()
    if not key:
        return None
    for pl in playlists:
        if (pl.name or "").strip().casefold() == key:
            return pl
    return None


def push_playlist_to_device(
    *,
    device,
    serial: str,
    name: str,
    guids_in_order: Sequence[str],
    parent_id: int | None = None,
    storage_id: int = DEFAULT_STORAGE_ID,
    list_playlists: Callable[[], list[DevicePlaylist]] | None = None,
) -> DevicePlaylistPushResult:
    """Create or update an on-device playlist for *name* with *guids_in_order*.

    *device* must expose ``create_playlist`` / ``update_playlist`` and
    optionally ``list_playlists`` (used when *list_playlists* is None).

    Raises ``ValueError`` when no real object ids can be resolved (empty
    membership). Transport failures propagate from the device adapter.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Playlist name is required")

    guids = [g for g in guids_in_order if is_track_guid(g)]
    track_ids, unresolved = resolve_track_object_ids(serial, guids)
    if not track_ids:
        raise ValueError(
            "No on-device object ids for playlist tracks "
            "(refresh Device Index after transfer, or re-sync tracks)."
        )

    parent = int(parent_id) if parent_id and int(parent_id) > 0 else (
        DEFAULT_PLAYLIST_FOLDER_ID
    )
    storage = int(storage_id or DEFAULT_STORAGE_ID)

    lister = list_playlists
    if lister is None:
        lister = getattr(device, "list_playlists", None)
    existing: DevicePlaylist | None = None
    if callable(lister):
        try:
            existing = find_device_playlist_by_name(lister() or [], clean_name)
        except Exception:
            logger.warning(
                "list_playlists failed; will try create for %r",
                clean_name,
                exc_info=True,
            )
            existing = None

    if existing is not None and existing.playlist_id > 0:
        new_id = device.update_playlist(
            existing.playlist_id,
            clean_name,
            track_ids,
            parent_id=parent,
            storage_id=storage,
        )
        created = False
        playlist_id = int(new_id or existing.playlist_id)
        logger.info(
            "Updated device playlist id=%s name=%r tracks=%d (unresolved=%d)",
            playlist_id,
            clean_name,
            len(track_ids),
            len(unresolved),
        )
    else:
        playlist_id = int(
            device.create_playlist(
                clean_name,
                track_ids,
                parent_id=parent,
                storage_id=storage,
            )
        )
        created = True
        logger.info(
            "Created device playlist id=%s name=%r tracks=%d (unresolved=%d)",
            playlist_id,
            clean_name,
            len(track_ids),
            len(unresolved),
        )

    return DevicePlaylistPushResult(
        playlist_id=playlist_id,
        name=clean_name,
        created=created,
        track_ids=tuple(track_ids),
        resolved=len(track_ids),
        missing_guid=len(unresolved),
        unresolved_guids=tuple(unresolved),
    )


def playlists_parent_id(folder_layout) -> int:
    """Resolve My Playlists folder id from live layout or legacy default."""
    if folder_layout is not None:
        try:
            rid = folder_layout.id_for(FolderRole.PLAYLISTS)
            if rid is not None and int(rid) > 0:
                return int(rid)
        except Exception:
            pass
    return DEFAULT_PLAYLIST_FOLDER_ID
