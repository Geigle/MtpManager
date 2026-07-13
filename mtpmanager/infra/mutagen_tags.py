"""Unified tag reading via mutagen."""

from __future__ import annotations

import os

from mutagen.asf import ASF
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

from mtpmanager.domain.library import is_format
from mtpmanager.domain.models import TrackMetadata


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


def _vorbis_get(tag_dict, tag_id: str) -> str:
    if tag_dict is None:
        tag_dict = {}
    if tag_id in tag_dict:
        raw = tag_dict[tag_id][0]
        if tag_id == "TRACKNUMBER" and "/" in raw:
            raw = raw.split("/")[0]
        if tag_id == "TRACKNUMBER":
            return _pad_tracknum(str(raw))
        return str(raw)

    if tag_id in ("TRACKNUMBER", "DISCNUMBER"):
        return "01"
    if tag_id in ("ALBUMARTIST", "COMPOSER"):
        return _vorbis_get(tag_dict, "ARTIST")
    if tag_id == "ARTIST":
        return "Unknown Artist"
    if tag_id == "DATE":
        return _vorbis_get(tag_dict, "YEAR")
    if tag_id == "ALBUM":
        return "Unknown Album"
    if tag_id == "TITLE":
        return "Unknown Title"
    return ""


def _from_flac(path: str) -> TrackMetadata:
    trk = FLAC(path)
    tags = trk.tags
    info = trk.info
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


def _asf_get(tag_dict: dict, tag_id: str) -> str:
    """Read ASF tags; keys may be mixed-case depending on mutagen version."""
    # Try common key variants
    candidates = [tag_id, tag_id.upper(), tag_id.lower(), tag_id.capitalize()]
    for key in candidates:
        if key in tag_dict:
            val = tag_dict[key]
            raw = val[0] if isinstance(val, (list, tuple)) else val
            raw = str(raw)
            if tag_id.lower() in ("tracknumber", "track") and "/" in raw:
                raw = raw.split("/")[0]
            if tag_id.lower() in ("tracknumber", "track"):
                return _pad_tracknum(raw)
            return raw

    lower = tag_id.lower()
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
        if is_format(path, "wma"):
            return _from_asf(path)
        # Try ID3 for aac/etc., then FLAC, then defaults
        try:
            return _from_id3(path)
        except Exception:
            pass
        try:
            return _from_flac(path)
        except Exception:
            pass
        try:
            return _from_asf(path)
        except Exception:
            pass
    except Exception as exc:
        print(f"Tag read failed for {path}: {exc}")

    base = os.path.splitext(os.path.basename(path))[0]
    return TrackMetadata(title=base or "Unknown Title")


class MutagenTagReader:
    def read_metadata(self, path: str) -> TrackMetadata:
        return read_metadata(path)
