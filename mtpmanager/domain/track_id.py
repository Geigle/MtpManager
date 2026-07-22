"""Stable track GUIDs for host index and flat MTP object names.

ObjectFileName form: ``{guid}{ext}`` under Music folder 100
(e.g. ``100/a1b2c3d4….mp3``). Host SQLite is source of truth for tags.
"""

from __future__ import annotations

import re
import uuid

# UUID4 hex without hyphens (32 lowercase hex chars).
GUID_HEX_LEN = 32
_GUID_RE = re.compile(rf"^[0-9a-f]{{{GUID_HEX_LEN}}}$")


def new_track_guid() -> str:
    """Return a new lowercase 32-char hex GUID (UUID4 without hyphens)."""
    return uuid.uuid4().hex


def is_track_guid(value: str | None) -> bool:
    """True if *value* is a 32-char lowercase hex track GUID."""
    if not value or not isinstance(value, str):
        return False
    return _GUID_RE.fullmatch(value) is not None


def normalize_guid(value: str | None) -> str | None:
    """Return lowercase GUID if *value* is valid hex (any case), else None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "")
    if is_track_guid(text):
        return text
    return None


def guid_from_remote_name(name: str | None) -> str | None:
    """Parse ObjectFileName / basename into a track GUID, or None.

    Accepts ``a1b2….mp3``, ``A1B2….MP3``, or bare stem without extension.
    """
    if not name or not isinstance(name, str):
        return None
    base = name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not base:
        return None
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return normalize_guid(stem)


def remote_basename(guid: str, file_extension: str) -> str:
    """Build ObjectFileName ``{guid}{ext}`` (extension required)."""
    g = normalize_guid(guid)
    if g is None:
        raise ValueError(f"invalid track guid: {guid!r}")
    ext = file_extension if str(file_extension).startswith(".") else f".{file_extension}"
    if ext == ".":
        ext = ".mp3"
    return f"{g}{ext.lower() if ext.isupper() else ext}"
