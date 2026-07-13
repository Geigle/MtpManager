"""Library index and music file helpers (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from mtpmanager.domain.models import Track

MUSIC_EXTENSIONS = frozenset(
    {"aac", "alac", "flac", "mp3", "ogg", "vorbis", "wav", "wma"}
)


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


@dataclass
class Library:
    """Ordered collection of tracks for UI indexing (0-based)."""

    tracks: list[Track] = field(default_factory=list)
    root_path: str = ""

    def __len__(self) -> int:
        return len(self.tracks)

    def get(self, index: int) -> Track:
        return self.tracks[index]

    def filter_by_artist(self, artist: str) -> list[Track]:
        return [t for t in self.tracks if t.meta.artist == artist]

    def filter_by_album(self, artist: str, album: str) -> list[Track]:
        return [
            t
            for t in self.tracks
            if t.meta.artist == artist and t.meta.album == album
        ]

    def sorted_by_path(self) -> Library:
        return Library(
            tracks=sorted(self.tracks, key=lambda t: t.path),
            root_path=self.root_path,
        )
