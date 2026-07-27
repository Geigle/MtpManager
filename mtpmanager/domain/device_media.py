"""Heuristics for device object types (experimental admin UI / listing)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence

from mtpmanager.domain.models import (
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    Track,
    TrackMetadata,
)
from mtpmanager.domain.track_id import guid_from_remote_name

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

# Audio-only subset (Device tab → Music). Video stays for List Tracks / Get Tracks.
MUSIC_FILETYPES = frozenset(
    {
        1,  # WAV
        2,  # MP3
        3,  # WMA
        4,  # OGG
        5,  # AUDIBLE
        6,  # MP4 (often audio on ZEN)
        7,  # UNDEF_AUDIO
        30,  # AAC
        32,  # FLAC
        33,  # MP2
        34,  # M4A
    }
)

VIDEO_FILETYPES = frozenset(
    {
        8,  # WMV
        9,  # AVI
        10,  # MPEG
        11,  # ASF
        12,  # QT
        13,  # UNDEF_VIDEO
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

MUSIC_EXTS = (
    ".mp3",
    ".wma",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
    ".m4b",
    ".mp2",
)

VIDEO_EXTS = (
    ".wmv",
    ".avi",
    ".mpg",
    ".mpeg",
    ".mov",
    ".asf",
)

# ZEN Vision:M parents that are not the Music category (see remote_naming).
_NON_MUSIC_PARENT_IDS = frozenset(
    {
        120,  # Video
        124,  # TV
        128,  # ZENcast / Podcasts
        116,  # Pictures
    }
)

# Device → Send Video destinations (Video / TV folders).
VIDEO_PARENT_IDS = frozenset(
    {
        120,  # Video
        124,  # TV
    }
)

# Extra container extensions often used for video under Video/TV (not in VIDEO_EXTS).
_VIDEO_FOLDER_EXTS = VIDEO_EXTS + (".mp4", ".m4v")


def looks_like_track(entry: object) -> bool:
    """True when a listed object is likely music/video (not a hard libmtp gate)."""
    ft = int(getattr(entry, "filetype", 0) or 0)
    if ft in TRACK_FILETYPES:
        return True
    name = (getattr(entry, "name", None) or "").strip().lower()
    return any(name.endswith(ext) for ext in TRACK_EXTS)


def looks_like_music(entry: object) -> bool:
    """True when a listed object is likely audio for the Device → Music tab.

    Excludes video filetypes/extensions and known non-music ZEN parents
    (Video / TV / ZENcast / Pictures). App-sent tracks live under Music 100.
    """
    ft = int(getattr(entry, "filetype", 0) or 0)
    if ft in VIDEO_FILETYPES:
        return False
    name = (getattr(entry, "name", None) or "").strip().lower()
    if any(name.endswith(ext) for ext in VIDEO_EXTS):
        return False
    parent = int(getattr(entry, "parent_id", 0) or 0)
    if parent in _NON_MUSIC_PARENT_IDS:
        return False
    if ft in MUSIC_FILETYPES:
        return True
    if any(name.endswith(ext) for ext in MUSIC_EXTS):
        return True
    # MP4 under Music (or unknown parent) can be audio; keep if looks_like_track
    # already would, but not video-parent (handled above).
    if name.endswith(".mp4") and parent not in _NON_MUSIC_PARENT_IDS:
        return True
    return False


def _ref_from_file_entry(entry: FileEntry) -> DeviceTrackRef:
    name = (entry.name or "").strip()
    return DeviceTrackRef(
        item_id=int(entry.item_id or 0),
        name=name,
        title="",
        artist="",
        album="",
        parent_id=int(entry.parent_id or 0),
        storage_id=int(entry.storage_id or 0),
        filetype=int(entry.filetype or 0),
    )


def track_refs_from_files(files: Sequence[FileEntry] | Iterable[FileEntry]) -> list[DeviceTrackRef]:
    """Build track refs from a full file listing (ids/names only; no tags)."""
    result: list[DeviceTrackRef] = []
    for entry in files:
        if not looks_like_track(entry):
            continue
        result.append(_ref_from_file_entry(entry))
    return _sort_track_refs(result)


def music_refs_from_files(
    files: Sequence[FileEntry] | Iterable[FileEntry],
) -> list[DeviceTrackRef]:
    """Audio-only track refs for the Device tab Music tree (ids/names only)."""
    result: list[DeviceTrackRef] = []
    for entry in files:
        if not looks_like_music(entry):
            continue
        result.append(_ref_from_file_entry(entry))
    return _sort_track_refs(result)


def looks_like_video(entry: object) -> bool:
    """True when a listed object is likely video for the Device → Video tab.

    Matches video filetypes/extensions, plus media containers under the ZEN
    Video (120) / TV (124) folders (e.g. ``.mp4`` sent via Send Video).
    """
    ft = int(getattr(entry, "filetype", 0) or 0)
    if ft in VIDEO_FILETYPES:
        return True
    name = (getattr(entry, "name", None) or "").strip().lower()
    if any(name.endswith(ext) for ext in VIDEO_EXTS):
        return True
    parent = int(getattr(entry, "parent_id", 0) or 0)
    if parent in VIDEO_PARENT_IDS and any(
        name.endswith(ext) for ext in _VIDEO_FOLDER_EXTS
    ):
        return True
    return False


def video_refs_from_files(
    files: Sequence[FileEntry] | Iterable[FileEntry],
) -> list[DeviceTrackRef]:
    """Video-only track refs for the Device tab Video tree (ids/names only)."""
    result: list[DeviceTrackRef] = []
    for entry in files:
        if not looks_like_video(entry):
            continue
        result.append(_ref_from_file_entry(entry))
    return _sort_track_refs(result)


def video_folder_label(parent_id: int) -> str:
    """Human label for a video object parent (Video / TV / Other)."""
    pid = int(parent_id or 0)
    if pid == 120:
        return "Video"
    if pid == 124:
        return "TV"
    return "Other"


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
    tn = int(info.tracknumber or 0)
    tracknumber = str(tn) if tn > 0 else (ref.tracknumber or "")
    genre = (info.genre or "").strip() or ref.genre
    return DeviceTrackRef(
        item_id=int(ref.item_id or info.item_id or 0),
        name=name,
        title=(info.title or "").strip(),
        artist=(info.artist or "").strip(),
        album=(info.album or "").strip(),
        date=(info.date or "").strip() or ref.date,
        tracknumber=tracknumber,
        genre=genre,
        parent_id=int(info.parent_id or ref.parent_id or 0),
        storage_id=int(info.storage_id or ref.storage_id or 0),
        filetype=int(info.filetype or ref.filetype or 0),
    )


def apply_host_meta(ref: DeviceTrackRef, meta: TrackMetadata) -> DeviceTrackRef:
    """Overlay host-library tags onto a listing ref (GUID basename join)."""
    return DeviceTrackRef(
        item_id=int(ref.item_id or 0),
        name=ref.name,
        title=(meta.title or "").strip() or ref.title,
        artist=(meta.artist or "").strip() or ref.artist,
        album=(meta.album or "").strip() or ref.album,
        date=(meta.date or "").strip() or ref.date,
        tracknumber=(str(meta.tracknumber or "").strip() or ref.tracknumber),
        genre=(meta.genre or "").strip() or ref.genre,
        parent_id=int(ref.parent_id or 0),
        storage_id=int(ref.storage_id or 0),
        filetype=int(ref.filetype or 0),
    )


# Placeholder strings devices and our own code use when tags are missing.
# Common after someone copies files as if the player were mass storage (MTP
# then surfaces empty track metadata as "Unknown …"). Some firmware/tools
# use angle-bracket forms such as ``<Unknown>`` (not invented by this app).
_PLACEHOLDER_TAG_VALUES = frozenset(
    {
        "",
        "—",
        "-",
        "unknown",
        "unknown artist",
        "unknown album",
        "unknown title",
        "unknown genre",
        "unknown composer",
        "n/a",
        "na",
        "none",
        "null",
        # Device / libmtp / Creative-style literals (with or without <>).
        "<unknown>",
        "<unknown artist>",
        "<unknown album>",
        "<unknown title>",
        "<unknown genre>",
        "<unknown composer>",
    }
)


def is_placeholder_tag(value: str | None) -> bool:
    """True when *value* is empty or a known unknown/placeholder label.

    Recognizes host defaults (``Unknown Artist``), bare ``Unknown``, and
    device-supplied forms like ``<Unknown>``. Angle brackets are stripped
    before matching so ``<Unknown Artist>`` counts as well.
    """
    text = (value or "").strip()
    if not text:
        return True
    key = text.casefold()
    if key in _PLACEHOLDER_TAG_VALUES:
        return True
    # Strip one layer of surrounding <…> / […] / (…) then re-check.
    if len(key) >= 2 and key[0] in "<[(" and key[-1] in ">])":
        inner = key[1:-1].strip()
        if not inner or inner in _PLACEHOLDER_TAG_VALUES:
            return True
        if inner == "unknown" or inner.startswith("unknown "):
            return True
    # Bare "Unknown …" already covered via casefold set; also accept any
    # tag that is only the word unknown (optionally with spaces).
    if key == "unknown" or key.startswith("unknown "):
        return True
    return False


def tags_look_placeholder(
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    object_name: str | None = None,
) -> bool:
    """True when core identity tags look like empty/placeholder device metadata.

    Used to detect MTP inventory that never received proper track tags (e.g.
    files dropped via a mass-storage workflow the device does not support).

    Requires **artist** placeholder and **title** either placeholder or equal
    to the object basename (UI often shows filename when title is empty; some
    devices also copy the filename into the title field).
    """
    if not is_placeholder_tag(artist):
        return False
    if is_placeholder_tag(title):
        return True
    # Filename-as-title: empty device title was replaced for display, or the
    # player stored the ObjectFileName stem/basename as the title tag.
    name = (object_name or "").strip()
    if not name:
        return False
    t = (title or "").strip()
    if not t:
        return True
    if t.casefold() == name.casefold():
        return True
    stem, _ext = os.path.splitext(name)
    if stem and t.casefold() == stem.casefold():
        return True
    return False


def track_meta_looks_placeholder(
    meta: TrackMetadata | None,
    *,
    object_name: str | None = None,
) -> bool:
    """:func:`tags_look_placeholder` for a host :class:`TrackMetadata`."""
    if meta is None:
        return True
    return tags_look_placeholder(
        title=meta.title,
        artist=meta.artist,
        album=meta.album,
        object_name=object_name,
    )


def ref_tags_look_placeholder(ref: DeviceTrackRef | None) -> bool:
    """Placeholder check against listing / Get Track Info fields on *ref*."""
    if ref is None:
        return True
    return tags_look_placeholder(
        title=ref.title,
        artist=ref.artist,
        album=ref.album,
        object_name=ref.name,
    )


def track_info_looks_placeholder(
    info: DeviceTrackInfo | None,
    *,
    object_name: str | None = None,
) -> bool:
    """Placeholder check against LIBMTP_Get_Trackmetadata fields."""
    if info is None:
        return True
    return tags_look_placeholder(
        title=info.title,
        artist=info.artist,
        album=info.album,
        object_name=object_name or info.name,
    )


def track_meta_is_usable(meta: TrackMetadata | None) -> bool:
    """True when embedded file tags are worth preferring over placeholders."""
    if meta is None:
        return False
    # Need at least a real title *or* a real artist (not both empty).
    title_ok = not is_placeholder_tag(meta.title)
    artist_ok = not is_placeholder_tag(meta.artist)
    return title_ok or artist_ok


def guid_stems_from_files(
    files: Sequence[FileEntry] | Iterable[FileEntry],
) -> set[str]:
    """Collect 32-hex GUID stems from device file basenames (any extension)."""
    stems: set[str] = set()
    for entry in files:
        g = guid_from_remote_name(getattr(entry, "name", None))
        if g:
            stems.add(g)
    return stems


def guid_stems_from_track_refs(
    refs: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
) -> set[str]:
    """Collect GUID stems from device track listing names."""
    stems: set[str] = set()
    for ref in refs:
        g = guid_from_remote_name(getattr(ref, "name", None))
        if g:
            stems.add(g)
    return stems


def enrich_refs_from_host(
    refs: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
    by_guid: Mapping[str, Track] | Mapping[str, TrackMetadata],
) -> list[DeviceTrackRef]:
    """Fill artist/title/album from host library for GUID-named device objects.

    *by_guid* maps 32-hex guid → ``Track`` or ``TrackMetadata``.
    Non-GUID or unknown names are left unchanged.
    """
    out: list[DeviceTrackRef] = []
    for ref in refs:
        g = guid_from_remote_name(ref.name)
        if not g or g not in by_guid:
            out.append(ref)
            continue
        hit = by_guid[g]
        meta = hit.meta if isinstance(hit, Track) else hit
        out.append(apply_host_meta(ref, meta))
    return _sort_track_refs(out)


def resolve_device_tracks_for_display(
    refs: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
    by_guid: Mapping[str, Track] | Mapping[str, TrackMetadata],
) -> list[Track]:
    """Build host-shaped ``Track`` rows for the Device music treeview.

    Resolution order per object:
    1. GUID basename → host library tags (full ``Track.meta`` when available)
    2. On-device tags already on the ref (Get Track Info / enrich)
    3. Filename (or ``id=<item_id>``) as the title

    Synthetic paths are ``device:<item_id>:<name>`` so tree iids stay unique
    and do not collide with the host library tree.
    """
    out: list[Track] = []
    for ref in refs:
        oid = int(ref.item_id or 0)
        name = (ref.name or "").strip() or f"id={oid}"
        g = guid_from_remote_name(ref.name)
        path = f"device:{oid}:{name}"

        if g and g in by_guid:
            hit = by_guid[g]
            if isinstance(hit, Track):
                # Prefer host path for album-art cache keys; keep device prefix
                # so library tree iids never collide.
                host_path = hit.path or path
                out.append(
                    Track(
                        path=f"device:{oid}:{host_path}",
                        meta=hit.meta,
                        guid=g,
                    )
                )
            else:
                out.append(Track(path=path, meta=hit, guid=g))
            continue

        title = (ref.title or "").strip()
        artist = (ref.artist or "").strip()
        album = (ref.album or "").strip()
        genre = (ref.genre or "").strip()
        # Empty or placeholder titles (common on device video) → filename.
        if not title or title.casefold() == "unknown title":
            title = name
        if not artist:
            artist = "Unknown Artist"
        if not album:
            album = "Unknown Album"
        tn = (ref.tracknumber or "").strip()
        meta = TrackMetadata(
            title=title,
            artist=artist,
            albumartist=artist,
            album=album,
            genre=genre or "Unknown Genre",
            date=(ref.date or "").strip(),
            tracknumber=tn or "01",
        )
        out.append(Track(path=path, meta=meta, guid=g or ""))
    return out


def refs_needing_device_tags(
    refs: Sequence[DeviceTrackRef] | Iterable[DeviceTrackRef],
    by_guid: Mapping[str, Track] | Mapping[str, TrackMetadata],
) -> list[DeviceTrackRef]:
    """Refs that still need Get_Trackmetadata (no host GUID hit, empty title)."""
    need: list[DeviceTrackRef] = []
    for ref in refs:
        g = guid_from_remote_name(ref.name)
        if g and g in by_guid:
            continue
        if (ref.title or "").strip():
            continue
        if int(ref.item_id or 0) <= 0:
            continue
        need.append(ref)
    return need


def _sort_track_refs(refs: list[DeviceTrackRef]) -> list[DeviceTrackRef]:
    refs.sort(
        key=lambda e: (
            (e.artist or "").casefold(),
            (e.album or "").casefold(),
            (e.title or "").casefold(),
            (e.name or "").casefold(),
            e.item_id,
        )
    )
    return refs
