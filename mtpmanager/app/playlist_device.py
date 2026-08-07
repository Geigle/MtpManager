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
from mtpmanager.infra.device_index import item_ids_for_guids, list_cached_files
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
    """Case-insensitive name match; first hit wins.

    Compares via :func:`playlist_display_name` so Creative ``Name.zpl`` matches
    a host-side name of ``Name`` (day podcast playlists, host sync titles).
    """
    key = playlist_display_name(name or "", 0).casefold()
    if not key or key == "playlist":
        # Empty / placeholder — require an exact non-empty raw match fallback.
        raw = (name or "").strip().casefold()
        if not raw:
            return None
        key = raw
    for pl in playlists:
        pl_key = playlist_display_name(
            pl.name or "", int(pl.playlist_id or 0)
        ).casefold()
        if pl_key == key:
            return pl
        # Also accept raw wire name equality (ids in display name, etc.).
        if (pl.name or "").strip().casefold() == (name or "").strip().casefold():
            return pl
    return None


def load_device_playlists_for_lookup(
    device,
    *,
    serial: str = "",
    parent_id: int | None = None,
) -> list[DevicePlaylist]:
    """Best-effort full playlist list for name lookup (ZEN-safe via index).

    Prefers ``list_playlists_complete`` with ``*.zpl`` candidates from the
    device_index cache so Creative playlists are not missed by the thin
    ``Get_Playlist_List`` result.
    """
    parent = int(parent_id) if parent_id and int(parent_id) > 0 else (
        DEFAULT_PLAYLIST_FOLDER_ID
    )
    candidates: list[int] = []
    names: dict[int, str] = {}
    key = (serial or "").strip()
    if key:
        try:
            files = list_cached_files(key)
            for e in playlist_candidates_from_files(
                files, playlist_parent_ids={parent}
            ):
                oid = int(e.item_id or 0)
                if oid > 0:
                    candidates.append(oid)
                    names[oid] = str(e.name or "")
        except Exception:
            logger.debug(
                "load_device_playlists_for_lookup: index candidates failed",
                exc_info=True,
            )

    complete = getattr(device, "list_playlists_complete", None)
    if callable(complete):
        try:
            return list(
                complete(candidate_ids=candidates, candidate_names=names) or []
            )
        except Exception:
            logger.warning(
                "list_playlists_complete failed; falling back",
                exc_info=True,
            )

    lister = getattr(device, "list_playlists", None)
    if callable(lister):
        try:
            return list(lister() or [])
        except Exception:
            logger.warning("list_playlists failed", exc_info=True)
    return []


def push_playlist_to_device(
    *,
    device,
    serial: str,
    name: str,
    guids_in_order: Sequence[str],
    parent_id: int | None = None,
    storage_id: int = DEFAULT_STORAGE_ID,
    list_playlists: Callable[[], list[DevicePlaylist]] | None = None,
    merge_existing: bool = False,
) -> DevicePlaylistPushResult:
    """Create or update an on-device playlist for *name* with *guids_in_order*.

    *device* must expose ``create_playlist`` / ``update_playlist`` and
    optionally ``list_playlists`` / ``list_playlists_complete``.

    When *list_playlists* is None, playlists are discovered via
    :func:`load_device_playlists_for_lookup` (device_index ``*.zpl`` + complete
    list) so ZEN same-name updates are not missed.

    *merge_existing*: if a playlist with *name* already exists, append newly
    resolved object ids to its current membership (skip duplicates) instead of
    replacing the whole track list. Used for day podcast playlists that
    accumulate episodes across multiple syncs.

    Raises ``ValueError`` when no real object ids can be resolved (empty
    membership, or merge with nothing new and no prior tracks). Transport
    failures propagate from the device adapter.
    """
    clean_name = playlist_display_name((name or "").strip(), 0)
    if not clean_name or clean_name == "Playlist":
        clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Playlist name is required")

    guids = [g for g in guids_in_order if is_track_guid(g)]
    track_ids, unresolved = resolve_track_object_ids(serial, guids)
    if not track_ids and not merge_existing:
        raise ValueError(
            "No on-device object ids for playlist tracks "
            "(refresh Device Index after transfer, or re-sync tracks)."
        )

    parent = int(parent_id) if parent_id and int(parent_id) > 0 else (
        DEFAULT_PLAYLIST_FOLDER_ID
    )
    storage = int(storage_id or DEFAULT_STORAGE_ID)

    existing: DevicePlaylist | None = None
    try:
        if callable(list_playlists):
            listed = list(list_playlists() or [])
        else:
            listed = load_device_playlists_for_lookup(
                device, serial=serial, parent_id=parent
            )
        existing = find_device_playlist_by_name(listed, clean_name)
    except Exception:
        logger.warning(
            "list_playlists for push failed; will try create for %r",
            clean_name,
            exc_info=True,
        )
        existing = None

    final_ids = list(track_ids)
    if existing is not None and existing.playlist_id > 0 and merge_existing:
        prior = [int(x) for x in (existing.track_ids or ()) if int(x) > 0]
        final_ids, added, skipped = append_ids_to_order(
            prior, track_ids, skip_existing=True
        )
        logger.info(
            "Merge into device playlist id=%s name=%r prior=%d new=%d "
            "added=%d skipped=%d → total=%d",
            existing.playlist_id,
            clean_name,
            len(prior),
            len(track_ids),
            added,
            skipped,
            len(final_ids),
        )
        if not final_ids:
            raise ValueError(
                "No on-device object ids for playlist tracks "
                "(existing membership empty and new GUIDs unresolved)."
            )
        if added == 0:
            # Nothing to write — still a successful update outcome.
            return DevicePlaylistPushResult(
                playlist_id=int(existing.playlist_id),
                name=clean_name,
                created=False,
                track_ids=tuple(final_ids),
                resolved=len(track_ids),
                missing_guid=len(unresolved),
                unresolved_guids=tuple(unresolved),
            )

    if not final_ids:
        raise ValueError(
            "No on-device object ids for playlist tracks "
            "(refresh Device Index after transfer, or re-sync tracks)."
        )

    if existing is not None and existing.playlist_id > 0:
        # Keep wire name when present (may still carry .zpl from Creative).
        wire_name = (existing.name or "").strip() or clean_name
        # Prefer clean display name for new membership writes.
        wire_name = playlist_display_name(wire_name, existing.playlist_id)
        if wire_name == f"Playlist {existing.playlist_id}":
            wire_name = clean_name
        new_id = device.update_playlist(
            existing.playlist_id,
            wire_name,
            final_ids,
            parent_id=parent,
            storage_id=storage,
        )
        created = False
        playlist_id = int(new_id or existing.playlist_id)
        logger.info(
            "Updated device playlist id=%s name=%r tracks=%d (unresolved=%d merge=%s)",
            playlist_id,
            wire_name,
            len(final_ids),
            len(unresolved),
            merge_existing,
        )
    else:
        playlist_id = int(
            device.create_playlist(
                clean_name,
                final_ids,
                parent_id=parent,
                storage_id=storage,
            )
        )
        created = True
        logger.info(
            "Created device playlist id=%s name=%r tracks=%d (unresolved=%d)",
            playlist_id,
            clean_name,
            len(final_ids),
            len(unresolved),
        )

    return DevicePlaylistPushResult(
        playlist_id=playlist_id,
        name=clean_name,
        created=created,
        track_ids=tuple(final_ids),
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


def append_ids_to_order(
    track_ids: Sequence[int],
    new_ids: Sequence[int],
    *,
    skip_existing: bool = True,
) -> tuple[list[int], int, int]:
    """Append *new_ids* to *track_ids*.

    Returns ``(merged, added_count, skipped_count)``. When *skip_existing* is
    true, ids already present are not duplicated (first occurrence wins).
    """
    out = [int(x) for x in track_ids if int(x) > 0]
    present = set(out) if skip_existing else set()
    added = 0
    skipped = 0
    for raw in new_ids:
        oid = int(raw or 0)
        if oid <= 0:
            continue
        if skip_existing and oid in present:
            skipped += 1
            continue
        out.append(oid)
        present.add(oid)
        added += 1
    return out, added, skipped


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
