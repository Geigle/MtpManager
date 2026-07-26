"""Pure sort and grouping helpers for the library tree view."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from mtpmanager.domain.library import primary_artist, year_from_date
from mtpmanager.domain.models import Track

UNKNOWN_YEAR = "Unknown year"
VARIOUS_ARTISTS = "Various Artists"

# Explicit albumartist / artist names that mean a compilation (case-insensitive).
_VA_NAME_EXACT = frozenset(
    {
        "various artists",
        "various artist",
        "various",
        "va",
        "v.a.",
        "v.a",
        "v/a",
        "ost",
        "soundtrack",
        "original soundtrack",
        "original motion picture soundtrack",
        "original television soundtrack",
        "music from the motion picture",
    }
)

# Path folder names that strongly suggest a compilation shelf.
_VA_PATH_TOKENS = frozenset(
    {
        "various artists",
        "various artist",
        "compilations",
        "compilation",
        "soundtracks",
        "soundtrack",
        "ost",
    }
)

# Genre tokens that *hint* at a multi-artist release. Alone they never force
# VA when every track shares one core artist (e.g. a single-composer OST).
_VA_GENRE_HINT_TOKENS = frozenset(
    {
        "compilation",
        "compilations",
        "soundtrack",
        "soundtracks",
        "ost",
        "various artists",
    }
)

# Collaboration / guest credit markers. These must *not* force a release onto
# Various Artists when they are the only reason artist strings differ.
# Covers feat/ft/featuring, with/w/, vs/versus, and common “x” collab form.
_COLLAB_MARKER = (
    r"(?:feat\.?|ft\.?|featuring|with|w\/|vs\.?|versus|"
    r"prod\.?|produced\s+by|duet(?:\s+with)?|presents)"
)

# "Main feat. Guest" / "Main - feat. Guest" / "Main (feat. Guest)"
_COLLAB_INLINE_RE = re.compile(
    rf"""
    \s*
    (?:
        [\(\[]\s*{_COLLAB_MARKER}\b.*?[\)\]]   # parenthetical / bracket guest
      | (?:[\-–—/|,]\s*)?{_COLLAB_MARKER}\b.*$  # trailing feat. / with / vs.
      | \s+x\s+.+$                              # "Artist x Artist" collab form
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# Title-only guest credits: do not use titles for multi-artist *identity*, but
# presence of feat. in titles is a negative signal against "true multi-artist".
_TITLE_COLLAB_RE = re.compile(
    rf"\b{_COLLAB_MARKER}\b|\bx\b",
    re.IGNORECASE,
)


class SortPrimary(str, Enum):
    DIRECTORY = "directory"  # hierarchy: parent folder → tracks
    TITLE = "title"
    ARTIST = "artist"  # hierarchy: albumartist → album → tracks
    # One-level "{artist} - {album}" → tracks (default library view; VA algorithm)
    ARTIST_ALBUM_COMBO = "artist_album_combo"
    ALBUM = "album"  # hierarchy: "{album} - {artist}" → tracks
    YEAR = "year"  # hierarchy: year → tracks (by albumartist, album, #)
    ARTIST_ALBUM = "artist_album"  # flat sort by albumartist, album, #


# Artist-column click cycle: (primary, reverse).
ARTIST_COLUMN_CYCLE: tuple[tuple[SortPrimary, bool], ...] = (
    (SortPrimary.ARTIST, False),
    (SortPrimary.ARTIST, True),
    (SortPrimary.ARTIST_ALBUM_COMBO, False),
    (SortPrimary.ARTIST_ALBUM_COMBO, True),
)


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


def _artist_key(track: Track) -> str:
    """Casefold library artist key (albumartist preferred)."""
    return _casefold(primary_artist(track)) or "unknown artist"


def is_various_artists_name(name: str) -> bool:
    """True when *name* is an explicit Various Artists / soundtrack label."""
    key = _casefold(name).strip()
    if not key:
        return False
    if key in _VA_NAME_EXACT:
        return True
    # "Various Artists - Something", "VA - 2020 Hits"
    if key.startswith("various artists") or key.startswith("various artist"):
        return True
    if re.match(r"^v\.?a\.?\b", key):
        return True
    return False


def strip_collaboration_credits(name: str) -> str:
    """Remove feat./ft./with/vs./x guest tails so core artist identity remains.

    Examples::

        "Main Band feat. Guest" → "Main Band"
        "Main (feat. Guest)" → "Main"
        "Artist x Other" → "Artist"
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    cleaned = _COLLAB_INLINE_RE.sub("", raw).strip(" \t-–—/,|")
    # Collapse leftover empty parens from odd tags.
    cleaned = re.sub(r"\s*[\(\[]\s*[\)\]]\s*", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t-–—/,|")
    return cleaned or raw


def _path_suggests_compilation(path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").casefold().split("/") if p]
    for part in parts:
        if part in _VA_PATH_TOKENS:
            return True
        if part.startswith("various artist"):
            return True
        # "VA - Now That's What I Call"
        if re.match(r"^va\b", part) or re.match(r"^v\.a\.?\b", part):
            return True
    return False


def _genre_hints_compilation(genre: str) -> bool:
    """True when genre text includes soundtrack/compilation/ost-style tokens."""
    if not genre:
        return False
    tokens = re.split(r"[\s/;,|]+", genre.casefold())
    return any(t in _VA_GENRE_HINT_TOKENS for t in tokens if t)


def _title_has_collaboration_credit(title: str) -> bool:
    return bool(title and _TITLE_COLLAB_RE.search(title))


def _known_core_artist_keys(tracks: Sequence[Track]) -> set[str]:
    """Distinct core artist keys, excluding unknown / explicit VA placeholders."""
    keys = {track_core_artist_key(t) for t in tracks}
    keys.discard("")
    return {
        k
        for k in keys
        if k not in {"unknown artist", _casefold(VARIOUS_ARTISTS)}
    }


def track_core_artist_key(track: Track) -> str:
    """Casefold artist identity for multi-artist detection (features stripped).

    Prefers albumartist when set (and not a placeholder). Falls back to the
    track artist with collaboration credits removed so ``Main feat. X`` and
    ``Main`` count as the same core artist.
    """
    aa = (track.meta.albumartist or "").strip()
    if aa and _casefold(aa) not in {"", "unknown artist"}:
        if is_various_artists_name(aa):
            return _casefold(VARIOUS_ARTISTS)
        # Albumartist is authoritative for the release; still strip a rare
        # "Main feat. Guest" albumartist so it matches plain "Main".
        core = strip_collaboration_credits(aa)
        return _casefold(core) or "unknown artist"

    ar = (track.meta.artist or "").strip()
    if is_various_artists_name(ar):
        return _casefold(VARIOUS_ARTISTS)
    core = strip_collaboration_credits(ar) if ar else ""
    if not core:
        core = strip_collaboration_credits(primary_artist(track))
    return _casefold(core) or "unknown artist"


def tracks_should_group_as_various_artists(tracks: Sequence[Track]) -> bool:
    """Decide whether a co-located track set is a compilation (→ Various Artists).

    Positive signals:
      * Explicit VA / OST *name* on albumartist or primary artist (majority)
      * Path components like ``Various Artists`` / ``Compilations`` / ``OST``
      * Genre tokens (``soundtrack``, ``ost``, ``compilation``, …) **only if**
        the set has 2+ distinct core artists — a single-artist soundtrack
        stays under that artist
      * 2+ distinct core artists after feat./guest stripping (no single shared
        non-VA albumartist)

    Negative / non-triggers:
      * feat./ft./featuring/with/vs./x guest credits alone
      * One distinct core artist (even when genre is Soundtrack/OST)
    """
    items = [t for t in tracks if t is not None]
    if len(items) < 2:
        # Single-track folders: only explicit VA naming counts.
        if not items:
            return False
        t = items[0]
        return is_various_artists_name(t.meta.albumartist) or is_various_artists_name(
            primary_artist(t)
        ) or _path_suggests_compilation(t.path)

    n = len(items)
    known = _known_core_artist_keys(items)
    multi_core = len(known) > 1

    # --- Strong positive: explicit VA identity in tags ---
    explicit_va = sum(
        1
        for t in items
        if is_various_artists_name(t.meta.albumartist)
        or is_various_artists_name(primary_artist(t))
    )
    if explicit_va >= max(1, (n + 1) // 2):
        return True

    if any(_path_suggests_compilation(t.path) for t in items):
        return True

    # Genre is a soft hint: Soundtrack/OST/Compilation only reinforce VA when
    # metadata already shows multiple core artists (not a solo film score).
    if multi_core and any(
        _genre_hints_compilation(t.meta.genre or "") for t in items
    ):
        return True

    # --- Core artist diversity (features stripped) ---
    core_keys = {track_core_artist_key(t) for t in items}
    va_keys = {k for k in core_keys if k == _casefold(VARIOUS_ARTISTS)}

    if va_keys and not known:
        return True
    if not multi_core:
        # One core artist (possibly with feat. guests / Soundtrack genre).
        return False

    # Multiple core artists. Extra guard: shared non-VA albumartist wins.
    albumartists = {
        strip_collaboration_credits((t.meta.albumartist or "").strip())
        for t in items
    }
    albumartists.discard("")
    meaningful_aa = {
        a
        for a in albumartists
        if _casefold(a) not in {"unknown artist"} and not is_various_artists_name(a)
    }
    if len(meaningful_aa) == 1:
        return False

    return True


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
            _artist_key(t),
            _casefold(m.album),
            track_number_key(t),
            t.path,
        )

    def key_artist_album(t: Track) -> tuple:
        m = t.meta
        return (
            _artist_key(t),
            _casefold(m.album),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    def key_album(t: Track) -> tuple:
        m = t.meta
        return (
            _casefold(m.album),
            _artist_key(t),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    def key_directory(t: Track) -> tuple:
        parent = os.path.dirname(t.path) or t.path
        m = t.meta
        return (
            _casefold(parent),
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
            _artist_key(t),
            _casefold(m.album),
            track_number_key(t),
            _casefold(m.title),
            t.path,
        )

    key_fn = {
        SortPrimary.TITLE: key_title,
        SortPrimary.ARTIST_ALBUM: key_artist_album,
        SortPrimary.ARTIST_ALBUM_COMBO: key_artist_album,
        SortPrimary.ALBUM: key_album,
        SortPrimary.YEAR: key_year,
        SortPrimary.DIRECTORY: key_directory,
        # Hierarchical modes still need a flat order for grouping
        SortPrimary.ARTIST: key_artist_album,
    }[primary]

    return sorted(tracks, key=key_fn, reverse=reverse)


def next_artist_column_sort(
    primary: SortPrimary,
    reverse: bool,
) -> tuple[SortPrimary, bool]:
    """Advance the Artist-column cycle, or enter it at step 0 if elsewhere."""
    cur = (primary, reverse)
    try:
        idx = ARTIST_COLUMN_CYCLE.index(cur)
    except ValueError:
        return ARTIST_COLUMN_CYCLE[0]
    return ARTIST_COLUMN_CYCLE[(idx + 1) % len(ARTIST_COLUMN_CYCLE)]


def directory_label(dir_path: str) -> str:
    """Human label for a host folder group (last path component, or full path)."""
    if not dir_path:
        return "Unknown folder"
    base = os.path.basename(dir_path.rstrip(os.sep + "/"))
    return base or dir_path


def group_by_directory(tracks: Sequence[Track]) -> list[GroupNode]:
    """Parent-directory groups → tracks (by track #, title).

    Keeps filesystem albums and Various Artists compilations together when
    they share a folder, independent of albumartist/artist tags.
    """
    ordered = sort_tracks_flat(tracks, SortPrimary.DIRECTORY)
    by_dir: dict[str, list[Track]] = defaultdict(list)
    for t in ordered:
        dkey = os.path.dirname(t.path) or t.path
        by_dir[dkey].append(t)

    groups: list[GroupNode] = []
    for dkey in sorted(by_dir.keys(), key=_casefold):
        dir_tracks = sorted(
            by_dir[dkey],
            key=lambda t: (
                track_number_key(t),
                _casefold(t.meta.title),
                t.path,
            ),
        )
        groups.append(
            GroupNode(
                key=f"dir:{dkey}",
                label=directory_label(dkey),
                tracks=tuple(dir_tracks),
            )
        )
    return groups


def group_by_artist_album(tracks: Sequence[Track]) -> list[GroupNode]:
    """Albumartist groups → album subgroups → tracks (by track #).

    Top-level identity is :func:`~mtpmanager.domain.library.primary_artist`
    so a CD stays together even when individual track ARTIST tags differ.
    """
    ordered = sort_tracks_flat(tracks, SortPrimary.ARTIST_ALBUM)
    by_artist: dict[str, list[Track]] = defaultdict(list)
    artist_labels: dict[str, str] = {}
    for t in ordered:
        key = _artist_key(t)
        by_artist[key].append(t)
        artist_labels.setdefault(key, primary_artist(t))

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


def group_by_artist_dash_album(tracks: Sequence[Track]) -> list[GroupNode]:
    """``{artist} - {album}`` groups → tracks (by track #).

    Per host directory, :func:`tracks_should_group_as_various_artists` decides
    whether the folder is a compilation (label under Various Artists) or a
    single-artist release (feat./guest credits do not force VA).
    """
    by_dir: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        dkey = os.path.dirname(t.path) or t.path
        by_dir[dkey].append(t)

    va_dirs = {
        d for d, ts in by_dir.items() if tracks_should_group_as_various_artists(ts)
    }

    by_group: dict[str, list[Track]] = defaultdict(list)
    labels: dict[str, str] = {}
    for t in tracks:
        dkey = os.path.dirname(t.path) or t.path
        alkey = _casefold(t.meta.album) or "unknown album"
        album_label = t.meta.album or "Unknown Album"
        if dkey in va_dirs:
            akey = _casefold(VARIOUS_ARTISTS)
            artist_label = VARIOUS_ARTISTS
        else:
            # Prefer stable core identity for the header when not VA.
            core = strip_collaboration_credits(primary_artist(t)) or primary_artist(t)
            akey = _casefold(core) or "unknown artist"
            artist_label = core or "Unknown Artist"
        composite = f"{akey}\0{alkey}"
        by_group[composite].append(t)
        if composite not in labels:
            labels[composite] = f"{artist_label} - {album_label}"

    def group_sort_key(composite: str) -> tuple[str, str]:
        akey, alkey = composite.split("\0", 1)
        return (akey, alkey)

    groups: list[GroupNode] = []
    for composite in sorted(by_group.keys(), key=group_sort_key):
        album_tracks = sorted(
            by_group[composite],
            key=lambda t: (
                track_number_key(t),
                _casefold(t.meta.title),
                t.path,
            ),
        )
        groups.append(
            GroupNode(
                key=f"artist_album:{composite}",
                label=labels[composite],
                tracks=tuple(album_tracks),
            )
        )
    return groups


def group_by_album(tracks: Sequence[Track]) -> list[GroupNode]:
    """Album groups (scoped by albumartist) → tracks (by track #).

    Same album title under different albumartists stays separate so two
    different CDs named "Greatest Hits" do not merge.
    """
    ordered = sort_tracks_flat(tracks, SortPrimary.ALBUM)
    by_album: dict[str, list[Track]] = defaultdict(list)
    labels: dict[str, str] = {}
    for t in ordered:
        akey = _artist_key(t)
        alkey = _casefold(t.meta.album) or "unknown album"
        composite = f"{akey}\0{alkey}"
        by_album[composite].append(t)
        if composite not in labels:
            artist = primary_artist(t)
            album = t.meta.album or "Unknown Album"
            labels[composite] = f"{album} - {artist}"

    def album_group_sort_key(composite: str) -> tuple[str, str]:
        # Prefer album title order, then albumartist (matches key_album).
        akey, alkey = composite.split("\0", 1)
        return (alkey, akey)

    groups: list[GroupNode] = []
    for composite in sorted(by_album.keys(), key=album_group_sort_key):
        album_tracks = sorted(
            by_album[composite],
            key=lambda t: (
                track_number_key(t),
                _casefold(t.meta.title),
                t.path,
            ),
        )
        groups.append(
            GroupNode(
                key=f"album:{composite}",
                label=labels[composite],
                tracks=tuple(album_tracks),
            )
        )
    return groups


def group_by_year(tracks: Sequence[Track]) -> list[GroupNode]:
    """Year groups (newest first) → tracks by albumartist, album, #."""
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
    """Values for tree columns: #0 text, title, artist, album, year.

    The Artist column still shows the track-level ARTIST tag (features, guests);
    hierarchy grouping uses albumartist via :func:`primary_artist`.
    """
    m = track.meta
    num = str(m.tracknumber or "")
    year = year_from_date(m.date) or ""
    return (num, m.title or "", m.artist or "", m.album or "", year)
