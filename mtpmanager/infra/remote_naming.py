"""Shared remote object naming for MTP transports (CMD + PyMTP).

Creative ZEN Vision:M (and similar players) need:
- Music folder parent id (100 on this device)
- Explicit storage id (0x00010001), not 0
- Short, sanitized object basenames (well under 64 chars; no & \\ / : * ? " < > |)

Experiment (GUID mode): ObjectFileName is ``{32hex-guid}{ext}`` under folder 100.
Full title/artist/album still go in MTP tag fields; only the wire name is a GUID.
"""

from __future__ import annotations

import os
import re
from types import MappingProxyType

from mtpmanager.domain.models import TrackMetadata
from mtpmanager.domain.track_id import (
    is_track_guid,
    normalize_guid,
    remote_basename as guid_remote_basename,
)

# Empirical send hygiene: keep basenames short. Track 8 of Doom hit exactly 64
# chars (no ext, contained &) with the old long remote basename and failed at
# finalize when stacked with bad parent/storage. Not a proven hard device max —
# longer ObjectFileName values can already exist on-device from other tools.
# See docs/basename-limit-evidence.md.
MAX_REMOTE_BASENAME = 56

# ---------------------------------------------------------------------------
# Creative ZEN Vision:M top-level folder IDs
# ---------------------------------------------------------------------------
# Captured via Device → List Folders (PyMTP / LIBMTP_Get_Folder_List) on a
# real Vision:M. Same layout as historical mtp-folders output. These are
# *object IDs*, not path strings — never invent "Music/Artist/Album".
#
# Track send always targets MUSIC (100). Other IDs are reference only until
# playlist/photo/video send is implemented.
# ---------------------------------------------------------------------------
ZEN_VISION_M_FOLDER_IDS: MappingProxyType[int, str] = MappingProxyType(
    {
        100: "Music",
        104: "My Playlists",
        108: "My Recordings",
        112: "My Organizer",
        116: "Pictures",
        120: "Video",
        124: "TV",
        128: "ZENcast",  # Podcasts
        132: "My Slideshows",
    }
)

# Reverse lookup by casefold name → id (for reference / future discovery).
ZEN_VISION_M_FOLDER_NAMES: MappingProxyType[str, int] = MappingProxyType(
    {name.casefold(): folder_id for folder_id, name in ZEN_VISION_M_FOLDER_IDS.items()}
)

# Device layout: folder 100 == "Music".
# mtp-sendtr accepts a numeric parent as the dirname of the remote path.
DEFAULT_MUSIC_FOLDER_ID = 100
assert DEFAULT_MUSIC_FOLDER_ID in ZEN_VISION_M_FOLDER_IDS
assert ZEN_VISION_M_FOLDER_IDS[DEFAULT_MUSIC_FOLDER_ID] == "Music"

# Storage Media on the ZEN Vision:M (mtp-detect: StorageID 0x00010001).
# storage_id 0 makes get_suggested_storage_id fail after the bulk transfer.
DEFAULT_STORAGE_ID = 0x00010001

# Unsafe in MTP object names on older Creative firmware.
_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|&\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_component(value: str, max_len: int) -> str:
    text = _UNSAFE_CHARS.sub(" ", str(value or ""))
    text = _WHITESPACE.sub(" ", text).strip(" .")
    if not text:
        text = "unknown"
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    return text or "unknown"


def build_remote_path(
    meta: TrackMetadata,
    file_extension: str,
    *,
    music_folder_id: int = DEFAULT_MUSIC_FOLDER_ID,
    max_basename: int = MAX_REMOTE_BASENAME,
    guid: str | None = None,
    preferred_basename: str | None = None,
) -> str:
    """Build a short remote path under the device Music folder.

    When *guid* is a valid 32-char hex track id, the object name is
    ``{guid}{ext}`` (flat inventory key for list_files + host DB join).
    When *preferred_basename* is set (and no GUID), use that sanitized name
    (retail restore of original ObjectFileNames). Otherwise falls back to the
    legacy short title form.

    mtp-sendtr uses dirname(remote) as parent id and basename as object name.
    Nested Artist/Album paths are *not* created (parse_path only looks up
    existing folders). A numeric parent (e.g. 100) is the reliable form.

    PyMTP uses the same shape: parent_id field + basename-only filename.
    """
    ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
    if ext == ".":
        ext = ".mp3"

    g = normalize_guid(guid) if guid else None
    if g is not None and is_track_guid(g):
        basename = guid_remote_basename(g, ext)
        if len(basename) > max_basename:
            # Should never happen for 32hex + short ext; keep contract hard.
            basename = basename[:max_basename]
        return f"{int(music_folder_id)}/{basename}"

    # Leave room for extension inside the device name limit.
    body_max = max(8, max_basename - len(ext))

    pref = (preferred_basename or "").strip()
    if pref:
        stem, pref_ext = os.path.splitext(pref)
        use_ext = pref_ext if pref_ext else ext
        stem = sanitize_component(stem or "track", body_max)
        basename = f"{stem}{use_ext if use_ext.startswith('.') else f'.{use_ext}'}"
        if len(basename) > max_basename:
            # Keep extension; trim stem.
            stem_max = max(1, max_basename - len(use_ext if use_ext.startswith('.') else f'.{use_ext}'))
            stem = sanitize_component(stem, stem_max)
            use_ext = use_ext if use_ext.startswith(".") else f".{use_ext}"
            basename = f"{stem}{use_ext}"
        return f"{int(music_folder_id)}/{basename}"

    track_no = str(meta.tracknumber).split("/")[0].strip() or "00"
    # Prefer compact "08 Title.mp3"; fall back to title-only if still long.
    title = sanitize_component(meta.title, body_max)
    candidate = sanitize_component(f"{track_no} {title}", body_max)
    if len(candidate) < 4:
        artist = sanitize_component(meta.artist, 20)
        candidate = sanitize_component(f"{track_no} {artist} {title}", body_max)

    return f"{int(music_folder_id)}/{candidate}{ext}"


def split_remote_path(remote: str) -> tuple[int, str]:
    """Split ``100/08 Title.mp3`` into (parent_id, basename)."""
    remote = str(remote or "").strip().replace("\\", "/")
    if "/" in remote:
        parent_s, basename = remote.rsplit("/", 1)
        try:
            parent_id = int(parent_s)
        except ValueError:
            parent_id = DEFAULT_MUSIC_FOLDER_ID
        basename = basename or "unknown.mp3"
        return parent_id, basename
    return DEFAULT_MUSIC_FOLDER_ID, remote or "unknown.mp3"


def year_arg(date: str) -> str:
    """Extract a 4-digit year from a date tag when present."""
    raw = str(date or "").strip()
    m = re.search(r"\b((?:19|20)\d{2})\b", raw)
    return m.group(1) if m else raw
