"""Library index and music file helpers (stdlib only)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable

from mtpmanager.domain.models import Track

MUSIC_EXTENSIONS = frozenset(
    {"aac", "alac", "flac", "mp3", "ogg", "vorbis", "wav", "wma"}
)

_UNKNOWN_ARTIST = "Unknown Artist"
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def extension_of(path: str) -> str:
    lower = path.lower()
    for ext in MUSIC_EXTENSIONS:
        if lower.endswith("." + ext):
            return ext
    if "." in path:
        return path.rsplit(".", 1)[-1].lower()
    return ""


def is_format(path: str, fmt: str) -> bool:
    """True if path is already the given audio format (by extension)."""
    return extension_of(path) == fmt.lower().lstrip(".")


def is_music_file(path: str, exclude_formats: Iterable[str] | None = None) -> bool:
    """True if path looks like a known music file, optionally excluding formats."""
    exclude = {e.lower().lstrip(".") for e in (exclude_formats or ())}
    ext = extension_of(path)
    if ext in MUSIC_EXTENSIONS and ext not in exclude:
        return True
    return False


def _year_from_date(date: str) -> str | None:
    """Extract a 19xx/20xx year from a date tag, if present."""
    if not date:
        return None
    m = _YEAR_RE.search(str(date).strip())
    return m.group(1) if m else None


def _albumartist_meaningful(albumartist: str) -> bool:
    return bool(albumartist) and albumartist != _UNKNOWN_ARTIST


def _artist_meaningful(artist: str) -> bool:
    return bool(artist) and artist != _UNKNOWN_ARTIST


def _path_has_component(path: str, name: str) -> bool:
    """True if any path component casefold-equals *name*."""
    if not _artist_meaningful(name):
        return False
    key = name.casefold().strip()
    return any(
        part.casefold().strip() == key
        for part in path.replace("\\", "/").split("/")
    )


def _album_path_hint(candidate: Track, seed: Track, album: str) -> bool:
    """True when path layout suggests candidate belongs with seed's album.

    Fires when both tracks share the same grandparent directory (e.g.
    Artist/Album vs Various/Album under one collection folder), or when both
    parent folders are named after the album and share a multi-level path
    prefix (avoids matching every 'Greatest Hits' folder on the disk).
    """
    cand_dir = os.path.dirname(candidate.path)
    seed_dir = os.path.dirname(seed.path)

    cand_grand = os.path.dirname(cand_dir)
    seed_grand = os.path.dirname(seed_dir)
    if cand_grand and seed_grand and cand_grand == seed_grand:
        return True

    album_key = album.casefold().strip()
    if not album_key:
        return False
    if (
        os.path.basename(cand_dir).casefold().strip() != album_key
        or os.path.basename(seed_dir).casefold().strip() != album_key
    ):
        return False
    try:
        common = os.path.commonpath([cand_dir, seed_dir])
    except ValueError:
        return False
    parts = [p for p in common.replace("\\", "/").split("/") if p]
    # Require depth beyond a single top-level segment (e.g. not just "/Music").
    return len(parts) >= 2


@dataclass
class Library:
    """Ordered collection of tracks for UI indexing (0-based)."""

    tracks: list[Track] = field(default_factory=list)
    root_path: str = ""

    def __len__(self) -> int:
        return len(self.tracks)

    def get(self, index: int) -> Track:
        return self.tracks[index]

    def filter_by_artist(self, seed: Track) -> list[Track]:
        """Tracks by the same artist as *seed*.

        Primary identity is seed.meta.artist. Includes tracks with a matching
        artist tag, albums credited to that artist via albumartist, or paths
        that contain the artist name as a folder component. Logs when a track
        is included despite a different artist tag (questionable membership).
        """
        artist = seed.meta.artist
        artist_ok = _artist_meaningful(artist)

        matches: list[Track] = []
        for t in self.tracks:
            reasons: list[str] = []
            if t.meta.artist == artist:
                reasons.append("same_artist")
            if artist_ok and t.meta.albumartist == artist:
                reasons.append("same_albumartist")
            if artist_ok and _path_has_component(t.path, artist):
                reasons.append("path_artist")

            if not reasons:
                continue

            if "same_artist" not in reasons:
                print(
                    f"Artist match (questionable): {t.meta.title!r} by {t.meta.artist!r} "
                    f"— reasons: {', '.join(reasons)}; artist={artist!r}"
                )
            matches.append(t)
        return matches

    def filter_by_album(self, seed: Track) -> list[Track]:
        """Tracks belonging to the same album as *seed*.

        Requires matching album title plus at least one corroborating signal:
        same artist, meaningful same albumartist, same parent directory, or
        same year with a path layout hint. Logs when a track is included
        despite a different artist (questionable membership).
        """
        album = seed.meta.album
        seed_dir = os.path.dirname(seed.path)
        seed_year = _year_from_date(seed.meta.date)
        seed_aa = seed.meta.albumartist
        aa_ok = _albumartist_meaningful(seed_aa)

        matches: list[Track] = []
        for t in self.tracks:
            if t.meta.album != album:
                continue

            reasons: list[str] = []
            if t.meta.artist == seed.meta.artist:
                reasons.append("same_artist")
            if aa_ok and t.meta.albumartist == seed_aa:
                reasons.append("same_albumartist")
            if os.path.dirname(t.path) == seed_dir:
                reasons.append("same_dir")
            t_year = _year_from_date(t.meta.date)
            if seed_year and t_year == seed_year:
                reasons.append("same_year")

            strong = any(
                r in reasons for r in ("same_artist", "same_albumartist", "same_dir")
            )
            path_hint = (
                "same_year" in reasons
                and not strong
                and _album_path_hint(t, seed, album)
            )
            if path_hint:
                reasons.append("year+path_hint")

            if not strong and not path_hint:
                continue

            if "same_artist" not in reasons:
                print(
                    f"Album match (questionable): {t.meta.title!r} by {t.meta.artist!r} "
                    f"— reasons: {', '.join(reasons)}; album={album!r}"
                )
            matches.append(t)
        return matches

    def sorted_by_path(self) -> Library:
        return Library(
            tracks=sorted(self.tracks, key=lambda t: t.path),
            root_path=self.root_path,
        )
