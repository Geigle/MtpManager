"""Pure sort and grouping helpers for the library tree view."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from mtpmanager.domain.library import year_from_date
from mtpmanager.domain.models import Track

UNKNOWN_YEAR = "Unknown year"


class SortPrimary(str, Enum):
    TITLE = "title"
    ARTIST = "artist"  # hierarchy: artist → album → tracks
    ALBUM = "album"  # hierarchy: album → tracks
    YEAR = "year"  # hierarchy: year → tracks (by artist, album, #)
    ARTIST_ALBUM = "artist_album"  # flat sort by artist, album, #


@dataclass(frozen=True)
class GroupNode:
    """A display group (artist / album / year) with ordered child tracks or subgroups."""

    key: str
    label: str
    tracks: tuple[Track, ...] = ()
    children: tuple["GroupNode", ...] = ()


def track_number_key(track: Track) -> int:
    return track.meta.tracknumber_int()


def _casefold(s: str) -> str:
    return (s or "").casefold()


def sort_tracks_flat(
    tracks: Sequence[Track],
    primary: SortPrimary,
    *,
    reverse: bool = False,
) -> list[Track]:
    """Return a new list sorted for flat (non-hierarchical) primaries."""

    def key_title(t: Track) -> tuple:
        m = t.meta
        return (
            _casefold(m.title),
            _casefold(m.artist),
            _casefold(m.album),
            track_number_key(t),
            t.path,
        )

    def key_artist_album(t: Track) -> tuple:
        m = t.meta
        return (
            _casefold(m.artist),
            _casefold(m.album),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    def key_album(t: Track) -> tuple:
        m = t.meta
        return (
            _casefold(m.album),
            _casefold(m.artist),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    def key_year(t: Track) -> tuple:
        y = year_from_date(t.meta.date) or ""
        m = t.meta
        # Unknown year last when ascending
        y_key = y if y else "\uffff"
        return (
            y_key,
            _casefold(m.artist),
            _casefold(m.album),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    key_fn = {
        SortPrimary.TITLE: key_title,
        SortPrimary.ARTIST_ALBUM: key_artist_album,
        SortPrimary.ALBUM: key_album,
        SortPrimary.YEAR: key_year,
        # Hierarchical modes still need a flat order for grouping
        SortPrimary.ARTIST: key_artist_album,
    }[primary]

    return sorted(tracks, key=key_fn, reverse=reverse)


def group_by_artist_album(tracks: Sequence[Track]) -> list[GroupNode]:
    """Artist groups → album subgroups → tracks (by track #)."""
    ordered = sort_tracks_flat(tracks, SortPrimary.ARTIST_ALBUM)
    by_artist: dict[str, list[Track]] = defaultdict(list)
    artist_labels: dict[str, str] = {}
    for t in ordered:
        key = _casefold(t.meta.artist) or "unknown artist"
        by_artist[key].append(t)
        artist_labels.setdefault(key, t.meta.artist or "Unknown Artist")

    artists: list[GroupNode] = []
    for akey in sorted(by_artist.keys()):
        atracks = by_artist[akey]
        by_album: dict[str, list[Track]] = defaultdict(list)
        album_labels: dict[str, str] = {}
        for t in atracks:
            alkey = _casefold(t.meta.album) or "unknown album"
            by_album[alkey].append(t)
            album_labels.setdefault(alkey, t.meta.album or "Unknown Album")

        albums: list[GroupNode] = []
        for alkey in sorted(by_album.keys()):
            album_tracks = sorted(
                by_album[alkey],
                key=lambda t: (track_number_key(t), _casefold(t.meta.title), t.path),
            )
            albums.append(
                GroupNode(
                    key=f"album:{akey}:{alkey}",
                    label=album_labels[alkey],
                    tracks=tuple(album_tracks),
                )
            )
        artists.append(
            GroupNode(
                key=f"artist:{akey}",
                label=artist_labels[akey],
                children=tuple(albums),
            )
        )
    return artists


def group_by_album(tracks: Sequence[Track]) -> list[GroupNode]:
    """Album groups → tracks (by track #). Label includes artist when useful."""
    ordered = sort_tracks_flat(tracks, SortPrimary.ALBUM)
    by_album: dict[str, list[Track]] = defaultdict(list)
    labels: dict[str, str] = {}
    for t in ordered:
        alkey = _casefold(t.meta.album) or "unknown album"
        by_album[alkey].append(t)
        if alkey not in labels:
            artist = t.meta.artist or "Unknown Artist"
            album = t.meta.album or "Unknown Album"
            labels[alkey] = f"{album} — {artist}"

    groups: list[GroupNode] = []
    for alkey in sorted(by_album.keys()):
        album_tracks = sorted(
            by_album[alkey],
            key=lambda t: (
                _casefold(t.meta.artist),
                track_number_key(t),
                _casefold(t.meta.title),
                t.path,
            ),
        )
        groups.append(
            GroupNode(
                key=f"album:{alkey}",
                label=labels[alkey],
                tracks=tuple(album_tracks),
            )
        )
    return groups


def group_by_year(tracks: Sequence[Track]) -> list[GroupNode]:
    """Year groups (newest first) → tracks by artist, album, #."""
    by_year: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        y = year_from_date(t.meta.date) or UNKNOWN_YEAR
        by_year[y].append(t)

    def year_sort_key(y: str) -> tuple:
        if y == UNKNOWN_YEAR:
            return (1, "")
        return (0, y)

    # Newest years first
    years_sorted = sorted(by_year.keys(), key=year_sort_key, reverse=True)
    # Put unknown at end
    years_sorted = [y for y in years_sorted if y != UNKNOWN_YEAR] + (
        [UNKNOWN_YEAR] if UNKNOWN_YEAR in by_year else []
    )

    groups: list[GroupNode] = []
    for y in years_sorted:
        ytracks = sort_tracks_flat(by_year[y], SortPrimary.ARTIST_ALBUM)
        groups.append(
            GroupNode(
                key=f"year:{y}",
                label=y,
                tracks=tuple(ytracks),
            )
        )
    return groups


def iter_track_cells(track: Track) -> tuple[str, str, str, str, str]:
    """Values for tree columns: #0 text, title, artist, album, year."""
    m = track.meta
    num = str(m.tracknumber or "")
    year = year_from_date(m.date) or ""
    return (num, m.title or "", m.artist or "", m.album or "", year)
