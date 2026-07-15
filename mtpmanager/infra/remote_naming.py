"""Shared remote object naming for MTP transports (CMD + PyMTP).

Creative ZEN Vision:M (and similar players) need:
- Music folder parent id (100 on this device)
- Explicit storage id (0x00010001), not 0
- Short, sanitized object basenames (well under 64 chars; no & \\ / : * ? " < > |)
"""

from __future__ import annotations

import re

from mtpmanager.domain.models import TrackMetadata

# Creative ZEN Vision:M (and many MTP players) use a short object-name limit.
# Track 8 of Doom hit exactly 64 chars with the old long remote basename.
MAX_REMOTE_BASENAME = 56

# Device layout from mtp-folders on ZEN Vision:M: folder 100 == "Music".
# mtp-sendtr accepts a numeric parent as the dirname of the remote path.
DEFAULT_MUSIC_FOLDER_ID = 100

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
) -> str:
    """Build a short remote path under the device Music folder.

    mtp-sendtr uses dirname(remote) as parent id and basename as object name.
    Nested Artist/Album paths are *not* created (parse_path only looks up
    existing folders). A numeric parent (e.g. 100) is the reliable form.

    PyMTP uses the same shape: parent_id field + basename-only filename.
    """
    ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
    if ext == ".":
        ext = ".mp3"
    # Leave room for extension inside the device name limit.
    body_max = max(8, max_basename - len(ext))

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
