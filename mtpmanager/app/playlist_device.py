"""Push a host playlist onto the device as an MTP playlist object.

Track files must already be on the device. Membership is resolved via host
track GUIDs → real MTP object ids in ``device_files`` (item_id > 0).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mtpmanager.domain.device_folders import FolderRole
from mtpmanager.domain.models import DevicePlaylist, FileEntry, Track
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.device_index import item_ids_for_guids
from mtpmanager.infra.remote_naming import (
    DEFAULT_PLAYLIST_FOLDER_ID,
    DEFAULT_STORAGE_ID,
)

# libmtp 1.1.23 / pymtp_wrapper FILETYPE map (not stock pymtp's older numbers).
_LIBMTP_FILETYPE_PLAYLIST = 43
_PLAYLIST_NAME_SUFFIXES = (".zpl", ".pla", ".m3u", ".m3u8")

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


def move_ids_by_indices(
    track_ids: Sequence[int],
    indices: Sequence[int],
    *,
    delta: int,
) -> list[int]:
    """Reorder *track_ids* by moving positions in *indices* up/down by one step.

    *indices* are 0-based positions in the current list (duplicates ignored).
    *delta* < 0 moves earlier; *delta* > 0 moves later. Boundary moves are no-ops
    for the whole selection (same as host M3U reorder).
    """
    items = [int(x) for x in track_ids]
    if not items or not indices or not delta:
        return items
    selected = sorted({int(i) for i in indices if 0 <= int(i) < len(items)})
    if not selected:
        return items
    step = -1 if delta < 0 else 1
    if step < 0:
        if min(selected) == 0:
            return items
        for i in selected:
            j = i - 1
            items[i], items[j] = items[j], items[i]
    else:
        if max(selected) >= len(items) - 1:
            return items
        for i in sorted(selected, reverse=True):
            j = i + 1
            items[i], items[j] = items[j], items[i]
    return items


def remove_ids_at_indices(
    track_ids: Sequence[int],
    indices: Sequence[int],
) -> list[int]:
    """Drop *track_ids* entries at the given 0-based positions."""
    drop = {int(i) for i in indices if int(i) >= 0}
    if not drop:
        return [int(x) for x in track_ids]
    return [int(x) for i, x in enumerate(track_ids) if i not in drop]


def playlist_display_name(name: str, playlist_id: int = 0) -> str:
    """Human label for a device playlist (strip Creative ``.zpl`` suffix)."""
    raw = (name or "").strip()
    if not raw:
        return f"Playlist {playlist_id}" if playlist_id else "Playlist"
    lower = raw.casefold()
    for suf in _PLAYLIST_NAME_SUFFIXES:
        if lower.endswith(suf):
            raw = raw[: -len(suf)].strip()
            break
    return raw or (f"Playlist {playlist_id}" if playlist_id else "Playlist")


def playlist_candidates_from_files(
    files: Sequence[FileEntry],
    *,
    playlist_parent_ids: set[int] | frozenset[int] | None = None,
    playlist_filetype: int = _LIBMTP_FILETYPE_PLAYLIST,
) -> list[FileEntry]:
    """Find on-device playlist objects from a file listing / device_index cache.

    Creative ZEN Vision:M stores playlists as ``*.zpl`` under My Playlists
    (parent 104). ``LIBMTP_Get_Playlist_List`` only returns objects whose PTP
    ObjectFormat is AbstractAudioVideoPlaylist *and* that still live in the
    session object cache — on this device that often yields a single hit even
    when many ``.zpl`` files are present. File listing + per-id
    ``Get_Playlist`` recovers the rest.
    """
    parents = {
        int(x)
        for x in (playlist_parent_ids or {DEFAULT_PLAYLIST_FOLDER_ID})
        if int(x) > 0
    }
    out: list[FileEntry] = []
    seen: set[int] = set()
    for entry in files or ():
        oid = int(getattr(entry, "item_id", 0) or 0)
        if oid <= 0 or oid in seen:
            continue
        name = str(getattr(entry, "name", "") or "").strip()
        ft = int(getattr(entry, "filetype", 0) or 0)
        parent = int(getattr(entry, "parent_id", 0) or 0)
        lower = name.casefold()
        is_playlist = False
        if ft == int(playlist_filetype):
            is_playlist = True
        elif any(lower.endswith(suf) for suf in _PLAYLIST_NAME_SUFFIXES):
            is_playlist = True
        elif parents and parent in parents and name:
            # Non-folder objects living in My Playlists.
            is_playlist = True
        if not is_playlist:
            continue
        seen.add(oid)
        out.append(entry)
    out.sort(
        key=lambda e: (
            playlist_display_name(str(e.name or ""), int(e.item_id or 0)).casefold(),
            int(e.item_id or 0),
        )
    )
    return out


def merge_device_playlists(
    primary: Sequence[DevicePlaylist],
    extras: Sequence[DevicePlaylist],
) -> list[DevicePlaylist]:
    """Union by playlist_id; *primary* wins on conflict."""
    by_id: dict[int, DevicePlaylist] = {}
    for pl in list(primary or ()) + list(extras or ()):
        pid = int(getattr(pl, "playlist_id", 0) or 0)
        if pid <= 0:
            continue
        if pid not in by_id:
            by_id[pid] = pl
    return sorted(
        by_id.values(),
        key=lambda p: (
            playlist_display_name(p.name or "", p.playlist_id).casefold(),
            int(p.playlist_id),
        ),
    )
