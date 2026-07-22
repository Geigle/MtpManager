"""Unified tag reading via mutagen."""

from __future__ import annotations

import logging
import os
from typing import Any

from mutagen.asf import ASF
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis

from mtpmanager.domain.library import is_format
from mtpmanager.domain.models import TrackMetadata

logger = logging.getLogger(__name__)


def _pad_tracknum(value: str) -> str:
    if len(value) < 2:
        return "0" + value
    return value


def _from_id3(path: str) -> TrackMetadata:
    trk = MP3(path)
    info = trk.info
    try:
        tag = EasyID3(path)
    except Exception:
        tag = {}

    def get(key: str) -> str:
        if key in tag:
            raw = tag[key][0]
            if key == "tracknumber" and "/" in raw:
                raw = raw.split("/")[0]
            return _pad_tracknum(raw) if key == "tracknumber" else raw
        if key in ("tracknumber", "discnumber"):
            return "01"
        if key in ("albumartist", "composer"):
            return get("artist") or "Unknown Artist"
        if key == "date":
            return get("year")
        if key == "artist":
            return "Unknown Artist"
        if key == "album":
            return "Unknown Album"
        if key == "title":
            return "Unknown Title"
        return ""

    return TrackMetadata(
        artist=get("artist") or "Unknown Artist",
        albumartist=get("albumartist") or "Unknown Artist",
        composer=get("composer") or "Unknown Composer",
        album=get("album") or "Unknown Album",
        title=get("title") or "Unknown Title",
        genre=get("genre") or "Unknown Genre",
        tracknumber=get("tracknumber") or "01",
        date=get("date") or "",
        length_sec=float(getattr(info, "length", 0) or 0),
        sample_rate=int(getattr(info, "sample_rate", 0) or 0),
        channels=int(getattr(info, "channels", 0) or 0),
        bitrate=int(getattr(info, "bitrate", 0) or 0),
        bitrate_mode=int(getattr(info, "bitrate_mode", 0) or 0),
    )


def _vorbis_lookup(tag_dict: Any, *keys: str) -> str | None:
    """Return first non-empty string for any of the keys (case-insensitive)."""
    if tag_dict is None:
        return None
    # Prefer direct membership (mutagen VCommentDict is case-insensitive).
    for key in keys:
        try:
            if key in tag_dict:
                val = tag_dict[key]
                raw = val[0] if isinstance(val, (list, tuple)) else val
                text = str(raw).strip()
                if text:
                    return text
        except (TypeError, KeyError, IndexError):
            continue
    # Fallback: normalize keys to lowercase for plain dicts / odd containers.
    try:
        items = dict(tag_dict)
    except Exception:
        return None
    lower_map = {str(k).lower(): v for k, v in items.items()}
    for key in keys:
        val = lower_map.get(key.lower())
        if val is None:
            continue
        raw = val[0] if isinstance(val, (list, tuple)) else val
        text = str(raw).strip()
        if text:
            return text
    return None


def _vorbis_get(tag_dict: Any, tag_id: str) -> str:
    """Read a Vorbis comment field (FLAC / Ogg Vorbis). Keys are case-insensitive."""
    wanted = tag_id.upper()
    raw = _vorbis_lookup(tag_dict, wanted)
    if raw is not None:
        if wanted == "TRACKNUMBER" and "/" in raw:
            raw = raw.split("/")[0]
        if wanted == "TRACKNUMBER":
            return _pad_tracknum(str(raw))
        return str(raw)

    if wanted in ("TRACKNUMBER", "DISCNUMBER"):
        return "01"
    if wanted in ("ALBUMARTIST", "COMPOSER"):
        return _vorbis_get(tag_dict, "ARTIST")
    if wanted == "ARTIST":
        return "Unknown Artist"
    if wanted == "DATE":
        return _vorbis_get(tag_dict, "YEAR")
    if wanted == "ALBUM":
        return "Unknown Album"
    if wanted == "TITLE":
        return "Unknown Title"
    return ""


def _from_vorbis_audio(trk: Any) -> TrackMetadata:
    """Build TrackMetadata from a mutagen audio object with Vorbis comments."""
    tags = getattr(trk, "tags", None)
    info = getattr(trk, "info", None)
    return TrackMetadata(
        artist=_vorbis_get(tags, "ARTIST"),
        albumartist=_vorbis_get(tags, "ALBUMARTIST"),
        composer=_vorbis_get(tags, "COMPOSER"),
        album=_vorbis_get(tags, "ALBUM"),
        title=_vorbis_get(tags, "TITLE"),
        genre=_vorbis_get(tags, "GENRE") or "Unknown Genre",
        tracknumber=_vorbis_get(tags, "TRACKNUMBER"),
        date=_vorbis_get(tags, "DATE"),
        length_sec=float(getattr(info, "length", 0) or 0),
        sample_rate=int(getattr(info, "sample_rate", 0) or 0),
        channels=int(getattr(info, "channels", 0) or 0),
        bitrate=int(getattr(info, "bitrate", 0) or 0),
        bitrate_mode=0,
    )


def _from_flac(path: str) -> TrackMetadata:
    return _from_vorbis_audio(FLAC(path))


def _from_ogg(path: str) -> TrackMetadata:
    """Ogg Vorbis (.ogg / .vorbis) — same comment schema as FLAC, different container."""
    return _from_vorbis_audio(OggVorbis(path))


# Windows Media / ASF content descriptors (WMA). Mutagen exposes the standard
# names (Title, Author, WM/…) — not the generic EasyID3-style keys.
_ASF_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("Title", "WM/Title", "title", "TITLE"),
    # Performer is almost always "Author" on WMA, not "Artist".
    "artist": ("Author", "WM/AlbumArtist", "Artist", "artist", "ARTIST"),
    "albumartist": ("WM/AlbumArtist", "Author", "AlbumArtist", "albumartist"),
    "album": ("WM/AlbumTitle", "Album", "album", "ALBUM"),
    "genre": ("WM/Genre", "Genre", "genre", "GENRE"),
    "composer": ("WM/Composer", "Composer", "composer"),
    "tracknumber": (
        "WM/TrackNumber",
        "WM/Track",
        "TrackNumber",
        "Track",
        "tracknumber",
    ),
    "date": ("WM/Year", "Year", "date", "DATE", "WM/OriginalReleaseTime"),
    "year": ("WM/Year", "Year", "year"),
    "discnumber": ("WM/PartOfSet", "WM/DiscNumber", "discnumber"),
}


def _asf_value_text(val: Any) -> str:
    """Normalize mutagen ASF attribute / plain value to a stripped string."""
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        if not val:
            return ""
        val = val[0]
    if hasattr(val, "value"):
        val = val.value
    return str(val).strip()


def _asf_lookup(tag_dict: dict | None, *keys: str) -> str | None:
    if not tag_dict:
        return None
    # Exact keys first (as_dict uses canonical WM names).
    for key in keys:
        if key in tag_dict:
            text = _asf_value_text(tag_dict[key])
            if text:
                return text
    # Case-insensitive fallback for odd taggers / plain mocks.
    lower_map = {str(k).lower(): v for k, v in tag_dict.items()}
    for key in keys:
        if key.lower() in lower_map:
            text = _asf_value_text(lower_map[key.lower()])
            if text:
                return text
    return None


def _asf_get(tag_dict: dict | None, tag_id: str) -> str:
    """Read ASF/WMA tags using Windows Media key names and common aliases."""
    lower = tag_id.lower()
    aliases = _ASF_KEY_ALIASES.get(lower, (tag_id, tag_id.capitalize(), tag_id.upper()))
    raw = _asf_lookup(tag_dict, *aliases)

    if raw is not None:
        if lower in ("tracknumber", "track", "discnumber"):
            if "/" in raw:
                raw = raw.split("/")[0]
            # Some writers store zero-based WM/Track; TrackNumber is preferred
            # via alias order. Pad digits only when purely numeric.
            digits = raw.strip()
            if digits.isdigit():
                return _pad_tracknum(digits)
            return raw
        return raw

    if lower in ("tracknumber", "discnumber", "track"):
        return "01"
    if lower in ("albumartist", "composer"):
        return _asf_get(tag_dict, "artist")
    if lower == "artist":
        return "Unknown Artist"
    if lower == "date":
        return _asf_get(tag_dict, "year")
    if lower == "album":
        return "Unknown Album"
    if lower == "title":
        return "Unknown Title"
    return ""


def _from_asf(path: str) -> TrackMetadata:
    """WMA / ASF — tags use Title/Author/WM/* content descriptors."""
    trk = ASF(path)
    tags = trk.tags.as_dict() if trk.tags is not None else {}
    info = trk.info
    return TrackMetadata(
        artist=_asf_get(tags, "artist"),
        albumartist=_asf_get(tags, "albumartist"),
        composer=_asf_get(tags, "composer"),
        album=_asf_get(tags, "album"),
        title=_asf_get(tags, "title"),
        genre=_asf_get(tags, "genre") or "Unknown Genre",
        tracknumber=_asf_get(tags, "tracknumber"),
        date=_asf_get(tags, "date"),
        length_sec=float(getattr(info, "length", 0) or 0),
        sample_rate=int(getattr(info, "sample_rate", 0) or 0),
        channels=int(getattr(info, "channels", 0) or 0),
        bitrate=int(getattr(info, "bitrate", 0) or 0),
        bitrate_mode=0,
    )


def read_metadata(path: str) -> TrackMetadata:
    """Read tags for a local audio file. Falls back to filename-based defaults."""
    if not os.path.isfile(path):
        return TrackMetadata(title=os.path.basename(path))

    try:
        if is_format(path, "mp3"):
            return _from_id3(path)
        if is_format(path, "flac"):
            return _from_flac(path)
        if is_format(path, "ogg") or is_format(path, "vorbis"):
            return _from_ogg(path)
        if is_format(path, "wma"):
            return _from_asf(path)
        # Try known containers for aac/wav/etc., then basename defaults
        try:
            return _from_id3(path)
        except Exception:
            pass
        try:
            return _from_flac(path)
        except Exception:
            pass
        try:
            return _from_ogg(path)
        except Exception:
            pass
        try:
            return _from_asf(path)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Tag read failed for %s: %s", path, exc)

    base = os.path.splitext(os.path.basename(path))[0]
    return TrackMetadata(title=base or "Unknown Title")


class MutagenTagReader:
    def read_metadata(self, path: str) -> TrackMetadata:
        return read_metadata(path)
