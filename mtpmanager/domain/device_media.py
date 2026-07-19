"""Heuristics for device object types (experimental admin UI / listing)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef, FileEntry

# libmtp audio/video-ish filetypes (wrapper LIBMTP_Filetype / libmtp 1.1.x).
TRACK_FILETYPES = frozenset(
    {
        1,  # WAV
        2,  # MP3
        3,  # WMA
        4,  # OGG
        5,  # AUDIBLE
        6,  # MP4
        7,  # UNDEF_AUDIO
        8,  # WMV
        9,  # AVI
        10,  # MPEG
        11,  # ASF
        12,  # QT
        13,  # UNDEF_VIDEO
        30,  # AAC
        32,  # FLAC
        33,  # MP2
        34,  # M4A
    }
)

TRACK_EXTS = (
    ".mp3",
    ".wma",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
    ".mp4",
    ".m4b",
    ".asf",
    ".wmv",
    ".avi",
    ".mpg",
    ".mpeg",
)


def looks_like_track(entry: object) -> bool:
    """True when a listed object is likely music/video (not a hard libmtp gate)."""
    ft = int(getattr(entry, "filetype", 0) or 0)
    if ft in TRACK_FILETYPES:
        return True
    name = (getattr(entry, "name", None) or "").strip().lower()
    return any(name.endswith(ext) for ext in TRACK_EXTS)


def track_refs_from_files(files: Sequence[FileEntry] | Iterable[FileEntry]) -> list[DeviceTrackRef]:
    """Build track refs from a full file listing (ids/names only; no tags)."""
    result: list[DeviceTrackRef] = []
    for entry in files:
        if not looks_like_track(entry):
            continue
        name = (entry.name or "").strip()
        result.append(
            DeviceTrackRef(
                item_id=int(entry.item_id or 0),
                name=name,
                title="",
                artist="",
                parent_id=int(entry.parent_id or 0),
                storage_id=int(entry.storage_id or 0),
                filetype=int(entry.filetype or 0),
            )
        )
    return _sort_track_refs(result)


def merge_track_refs(
    tagged: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
    from_files: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
) -> list[DeviceTrackRef]:
    """Prefer rows that already have tags; add file-only ids missing there.

    Kept for tests and any future hybrid path. Bulk List Tracks uses file
    listing only; tags come from on-demand ``get_track_metadata``.
    """
    by_id: dict[int, DeviceTrackRef] = {}
    for ref in tagged:
        oid = int(ref.item_id or 0)
        if oid <= 0:
            continue
        by_id[oid] = ref
    for ref in from_files:
        oid = int(ref.item_id or 0)
        if oid <= 0 or oid in by_id:
            continue
        by_id[oid] = ref
    return _sort_track_refs(list(by_id.values()))


def apply_track_info(ref: DeviceTrackRef, info: DeviceTrackInfo) -> DeviceTrackRef:
    """Overlay Get_Trackmetadata fields onto a listing ref (new frozen instance)."""
    name = (info.name or ref.name or "").strip()
    return DeviceTrackRef(
        item_id=int(ref.item_id or info.item_id or 0),
        name=name,
        title=(info.title or "").strip(),
        artist=(info.artist or "").strip(),
        parent_id=int(info.parent_id or ref.parent_id or 0),
        storage_id=int(info.storage_id or ref.storage_id or 0),
        filetype=int(info.filetype or ref.filetype or 0),
    )


def _sort_track_refs(refs: list[DeviceTrackRef]) -> list[DeviceTrackRef]:
    refs.sort(
        key=lambda e: (
            (e.artist or "").casefold(),
            (e.title or "").casefold(),
            (e.name or "").casefold(),
            e.item_id,
        )
    )
    return refs
