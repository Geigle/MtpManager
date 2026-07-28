"""Library index and music file helpers (stdlib only)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterable

from mtpmanager.domain.models import Track, TrackMetadata

logger = logging.getLogger(__name__)

MUSIC_EXTENSIONS = frozenset(
    {"aac", "alac", "flac", "mp3", "ogg", "vorbis", "wav", "wma"}
)

# Host Video tab / scan (aligned with Device → Send Video pickers + ZEN video).
# Intentionally excludes pure-audio containers (m4a/m4b stay music).
VIDEO_EXTENSIONS = frozenset(
    {"wmv", "avi", "mpg", "mpeg", "mov", "asf", "mp4", "m4v", "qt", "mkv"}
)

# Audio format preference for duplicate tracks (lower rank = preferred).
# Uncompressed PCM first, then lossless compressed, then lossy by typical
# transparency at common bitrates (AAC > MP3 > Vorbis > WMA). Opus ranks
# above AAC when present; m4a is treated as AAC-tier (container, usually lossy).
FORMAT_QUALITY_RANK: dict[str, int] = {
    # Uncompressed PCM
    "wav": 0,
    "pcm": 0,
    "aiff": 0,
    "aif": 0,
    # Lossless compressed (bit-identical to source PCM)
    "flac": 10,
    "alac": 10,
    "ape": 10,
    "wv": 10,
    "wavpack": 10,
    # Lossy — higher fidelity first
    "opus": 20,
    "aac": 30,
    "m4a": 30,
    "mp3": 40,
    "ogg": 50,
    "vorbis": 50,
    "wma": 60,
}
_UNKNOWN_FORMAT_RANK = 100

# Path components treated as format/quality folders when building a path-based
# identity (e.g. Music/FLAC/Artist/Album vs Music/MP3/Artist/Album).
_FORMAT_PATH_COMPONENTS = frozenset(
    {
        "wav",
        "pcm",
        "aiff",
        "aif",
        "flac",
        "alac",
        "ape",
        "wv",
        "wavpack",
        "opus",
        "aac",
        "m4a",
        "mp3",
        "ogg",
        "vorbis",
        "wma",
        "lossy",
        "lossless",
        "uncompressed",
    }
)

_UNKNOWN_ARTIST = "Unknown Artist"
_UNKNOWN_ALBUM = "Unknown Album"
_UNKNOWN_TITLE = "Unknown Title"
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def extension_of(path: str) -> str:
    lower = path.lower()
    for ext in MUSIC_EXTENSIONS | VIDEO_EXTENSIONS:
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


def is_video_file(path: str) -> bool:
    """True if path looks like a known video file (by extension)."""
    return extension_of(path) in VIDEO_EXTENSIONS


def is_library_media_file(
    path: str, exclude_formats: Iterable[str] | None = None
) -> bool:
    """True if path is music or video for library scan."""
    if is_video_file(path):
        return True
    return is_music_file(path, exclude_formats=exclude_formats)


def format_quality_rank(path_or_ext: str) -> int:
    """Return preference rank for an audio format (lower = higher fidelity).

    Accepts a file path or bare extension (with or without a leading dot).
    Unknown formats rank last so they lose to known higher-quality encodings.
    """
    raw = (path_or_ext or "").strip().lower()
    if not raw:
        return _UNKNOWN_FORMAT_RANK
    if "/" in raw or "\\" in raw or "." in raw[1:]:
        ext = extension_of(raw)
    else:
        ext = raw.lstrip(".")
    return FORMAT_QUALITY_RANK.get(ext, _UNKNOWN_FORMAT_RANK)


def _meaningful_tag(value: str, *unknowns: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    key = text.casefold()
    for u in unknowns:
        if key == u.casefold():
            return False
    return True


def _path_identity_key(path: str) -> tuple[str, ...]:
    """Stable path-based identity with format-folder components stripped.

    ``Music/FLAC/Artist/Album/01 Song.flac`` and
    ``Music/MP3/Artist/Album/01 Song.mp3`` share the same key.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return ()
    stem = parts[-1].rsplit(".", 1)[0] if "." in parts[-1] else parts[-1]
    dirs = parts[:-1]
    filtered = [p for p in dirs if p.casefold() not in _FORMAT_PATH_COMPONENTS]
    return tuple(p.casefold() for p in filtered + [stem])


def track_content_identity(track: Track) -> tuple:
    """Identity key for “same song, different encoding” collapse.

    Prefer tag identity when artist + title are meaningful; otherwise fall
    back to a path key that ignores common format folder names.
    """
    if not track:
        return ("empty",)
    meta = track.meta or TrackMetadata()
    artist = primary_artist(track).strip()
    album = (meta.album or "").strip()
    title = (meta.title or "").strip()
    if _meaningful_tag(artist, _UNKNOWN_ARTIST) and _meaningful_tag(
        title, _UNKNOWN_TITLE
    ):
        album_key = (
            album.casefold()
            if _meaningful_tag(album, _UNKNOWN_ALBUM)
            else ""
        )
        return (
            "meta",
            artist.casefold(),
            album_key,
            meta.tracknumber_int(),
            title.casefold(),
        )
    return ("path",) + _path_identity_key(track.path or "")


def _format_preference_sort_key(track: Track) -> tuple:
    """Sort key: best quality first, then technical stream info, then path."""
    meta = track.meta or TrackMetadata()
    return (
        format_quality_rank(track.path or ""),
        # Prefer richer stream info when ranks tie (negated → higher first).
        -int(meta.sample_rate or 0),
        -int(meta.bitrate or 0),
        -float(meta.length_sec or 0.0),
        (track.path or "").casefold(),
    )


def prefer_higher_fidelity_tracks(tracks: Iterable[Track]) -> list[Track]:
    """Keep one track per content identity, preferring higher-fidelity encodings.

    When the library contains the same song as both FLAC and MP3 (parallel
    rip folders, or same basename with different extensions), only the better
    source is listed and used for sync/transcode. Videos are never collapsed.
    Order of the result is stable by path.
    """
    audio: list[Track] = []
    videos: list[Track] = []
    for t in tracks:
        if is_video_track(t):
            videos.append(t)
        else:
            audio.append(t)

    winners: dict[tuple, Track] = {}
    for t in audio:
        key = track_content_identity(t)
        prev = winners.get(key)
        if prev is None or _format_preference_sort_key(t) < _format_preference_sort_key(
            prev
        ):
            winners[key] = t

    kept = list(winners.values()) + videos
    kept.sort(key=lambda t: (t.path or "").casefold())
    dropped = len(audio) - len(winners)
    if dropped > 0:
        logger.info(
            "Format preference: kept %d audio track(s), dropped %d lower-fidelity duplicate(s)",
            len(winners),
            dropped,
        )
    return kept


def year_from_date(date: str) -> str | None:
    """Extract a 19xx/20xx year from a date tag, if present."""
    if not date:
        return None
    m = _YEAR_RE.search(str(date).strip())
    return m.group(1) if m else None


# Back-compat alias for internal call sites.
_year_from_date = year_from_date

# Genre tokens that mean spoken-word audiobook (case-insensitive).
_AUDIOBOOK_GENRE_TOKENS = frozenset({"audiobook", "audiobooks"})


def is_audiobook_genre(genre: str) -> bool:
    """True when *genre* is (or includes) the Audiobook genre token.

    Matches exact ``Audiobook`` / ``Audiobooks`` (any case) and multi-value
    tags split on spaces, slashes, semicolons, commas, or pipes
    (e.g. ``Spoken Word / Audiobook``).
    """
    raw = (genre or "").strip()
    if not raw:
        return False
    key = raw.casefold()
    if key in ("unknown genre", "unknown"):
        return False
    if key in _AUDIOBOOK_GENRE_TOKENS:
        return True
    tokens = re.split(r"[\s/;,|]+", key)
    return any(t in _AUDIOBOOK_GENRE_TOKENS for t in tokens if t)


def is_audiobook_track(track: Track) -> bool:
    """True when the track's genre tags it as an audiobook."""
    return is_audiobook_genre(track.meta.genre if track and track.meta else "")


def is_video_track(track: Track) -> bool:
    """True when the track path is a video container (by extension)."""
    return bool(track and is_video_file(track.path))


def partition_library_media(
    tracks: Iterable[Track],
) -> tuple[list[Track], list[Track], list[Track]]:
    """Split tracks into (music, videos, audiobooks).

    Video is decided by file extension first. Remaining audio with genre
    Audiobook goes to audiobooks; everything else is music.
    """
    music: list[Track] = []
    videos: list[Track] = []
    audiobooks: list[Track] = []
    for t in tracks:
        if is_video_track(t):
            videos.append(t)
        elif is_audiobook_track(t):
            audiobooks.append(t)
        else:
            music.append(t)
    return music, videos, audiobooks


def partition_music_and_audiobooks(
    tracks: Iterable[Track],
) -> tuple[list[Track], list[Track]]:
    """Split tracks into (music, audiobooks); videos are dropped from both.

    Prefer :func:`partition_library_media` when the Video tab is involved.
    """
    music, _videos, audiobooks = partition_library_media(tracks)
    return music, audiobooks


def path_under_root(path: str, root: str) -> bool:
    """True when *path* is *root* or a file/dir under *root* (normpath)."""
    if not path or not root:
        return False
    p = os.path.normpath(path)
    r = os.path.normpath(root)
    if p == r:
        return True
    prefix = r + os.sep
    return p.startswith(prefix)


def path_is_excluded(path: str, exclusions: Iterable[str]) -> bool:
    """True when *path* matches any exclusion rule (exact path or under a folder).

    Exclusion entries are absolute paths to files or directories. A directory
    rule excludes the directory itself and every descendant path.
    """
    if not path:
        return False
    p = os.path.normpath(path)
    for raw in exclusions:
        if not raw:
            continue
        ex = os.path.normpath(raw)
        if p == ex or path_under_root(p, ex):
            return True
    return False


def merge_scanned_roots(
    existing: Library,
    scanned: Library,
    *,
    scanned_roots: Iterable[str],
    final_roots: Iterable[str],
) -> Library:
    """Merge a partial scan into *existing* without dropping other roots.

    Tracks whose paths fall under any of *scanned_roots* are replaced by
    *scanned* (so deleted files under the new/updated root disappear). Tracks
    outside those roots are kept unchanged.
    """
    roots_scanned = normalize_library_roots(scanned_roots)
    keep = [
        t
        for t in existing.tracks
        if not any(path_under_root(t.path, r) for r in roots_scanned)
    ]
    merged = prefer_higher_fidelity_tracks(list(keep) + list(scanned.tracks))
    return Library(
        tracks=merged,
        root_paths=normalize_library_roots(final_roots),
    )


def video_display_title(track: Track) -> str:
    """Filename (basename) for Video-tab rows — tags are often empty/unreliable."""
    if not track or not track.path:
        return "Unknown Title"
    base = os.path.basename(track.path)
    return base or "Unknown Title"


# --- TV-series path heuristics (Library → Video grouping) ---

# S01E02, s1e2, S01.E02, S01_E02
_TV_EPISODE_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])s(\d{1,2})\s*[._\-\s]*e(\d{1,3})(?:[^a-z0-9]|$)"
)
# 1x02, 01x02
_TV_X_EPISODE_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(\d{1,2})\s*x\s*(\d{1,3})(?:[^a-z0-9]|$)"
)
# Folder names like "Season 1", "Season 01", "S01", "Series 2"
_TV_SEASON_FOLDER_RE = re.compile(
    r"(?i)^(?:season|series)\s*0*(\d{1,2})$|^s0*(\d{1,2})$"
)
# Parent contains "Season 1" as a token (not "Season of the Witch")
_TV_SEASON_IN_NAME_RE = re.compile(r"(?i)\bseason\s*0*\d{1,2}\b")

# Shelf / dump folders — prefer series title from filename when parent is one of these.
_TV_GENERIC_PARENTS = frozenset(
    {
        "downloads",
        "download",
        "tv",
        "television",
        "series",
        "shows",
        "show",
        "video",
        "videos",
        "movies",
        "movie",
        "media",
        "tmp",
        "temp",
        "incoming",
        "complete",
        "completed",
    }
)


def _path_parts(path: str) -> tuple[str, str, str, str]:
    """Return (file_stem, file_base, parent_name, grandparent_name)."""
    base = os.path.basename(path or "")
    stem = os.path.splitext(base)[0] if base else ""
    parent_path = os.path.dirname(path or "")
    parent = os.path.basename(parent_path.rstrip(os.sep + "/")) if parent_path else ""
    grand_path = os.path.dirname(parent_path) if parent_path else ""
    grand = os.path.basename(grand_path.rstrip(os.sep + "/")) if grand_path else ""
    return stem, base, parent, grand


def is_season_folder_name(name: str) -> bool:
    """True when *name* is a pure season folder (e.g. ``Season 1``, ``S02``)."""
    n = (name or "").strip()
    if not n:
        return False
    return bool(_TV_SEASON_FOLDER_RE.fullmatch(n))


def parse_tv_episode_codes(text: str) -> tuple[int, int] | None:
    """Return ``(season, episode)`` if *text* looks like S01E02 / 1x02."""
    if not text:
        return None
    m = _TV_EPISODE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _TV_X_EPISODE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def path_looks_like_tv_series(path: str) -> bool:
    """True when a video path looks like a TV episode / season layout."""
    stem, base, parent, _grand = _path_parts(path)
    if parse_tv_episode_codes(stem) or parse_tv_episode_codes(base):
        return True
    if parse_tv_episode_codes(parent):
        return True
    if is_season_folder_name(parent):
        return True
    if _TV_SEASON_IN_NAME_RE.search(parent):
        return True
    # Bare word "season" only when paired with episode-like siblings is
    # handled by folder structure; avoid movie titles like "Season of X".
    return False


def _series_title_from_filename_stem(stem: str) -> str | None:
    """Strip S01E02 / 1x02 tails from a filename stem → show title."""
    raw = (stem or "").strip()
    if not raw:
        return None
    m = _TV_EPISODE_RE.search(raw)
    if not m:
        m = _TV_X_EPISODE_RE.search(raw)
    if not m:
        return None
    head = raw[: m.start()].strip(" ._-")
    # Drop trailing junk like " - " left after the episode token.
    head = re.sub(r"[\s._\-]+$", "", head)
    # Common "Show.Name.S01E01" → "Show Name"
    if head and "." in head and " " not in head:
        head = head.replace(".", " ")
    head = re.sub(r"\s{2,}", " ", head).strip(" ._-")
    return head or None


def tv_series_title_for_path(path: str) -> str | None:
    """Series display title when *path* looks like TV content, else None.

    Preference order:
      1. Grandparent when parent is a season folder (``Show/Season 1/ep.avi``)
      2. Parent folder when the file (or parent name) has episode codes
      3. Filename stem with SxxEyy stripped
    """
    if not path_looks_like_tv_series(path):
        return None
    stem, base, parent, grand = _path_parts(path)

    if is_season_folder_name(parent) and grand:
        return grand
    if _TV_SEASON_IN_NAME_RE.search(parent) and grand and not is_season_folder_name(parent):
        # e.g. parent "Show Name Season 1" — prefer grandparent when present
        # and parent is not already the show; if grand looks like a shelf
        # ("TV", "Series") use the parent cleaned of "Season N".
        if grand.casefold() not in {"tv", "television", "series", "shows", "video", "videos"}:
            return grand
        cleaned = _TV_SEASON_IN_NAME_RE.sub("", parent).strip(" ._-")
        return cleaned or parent

    ep_in_file = parse_tv_episode_codes(stem) or parse_tv_episode_codes(base)
    if ep_in_file:
        from_name = _series_title_from_filename_stem(stem)
        parent_key = parent.casefold() if parent else ""
        if (
            parent
            and not is_season_folder_name(parent)
            and parent_key not in _TV_GENERIC_PARENTS
        ):
            # Parent is the show folder (not a download/TV shelf).
            if not _TV_SEASON_IN_NAME_RE.search(parent):
                return parent
            cleaned = _TV_SEASON_IN_NAME_RE.sub("", parent).strip(" ._-")
            return cleaned or parent
        if from_name:
            return from_name
        if grand and grand.casefold() not in _TV_GENERIC_PARENTS:
            return grand
        return parent or from_name

    if parent:
        return parent
    return _series_title_from_filename_stem(stem)


def tv_episode_sort_key(path: str) -> tuple:
    """Sort key for episode files: season, episode, then path."""
    stem, base, parent, _g = _path_parts(path)
    codes = (
        parse_tv_episode_codes(stem)
        or parse_tv_episode_codes(base)
        or parse_tv_episode_codes(parent)
    )
    if codes:
        return (0, codes[0], codes[1], (path or "").casefold())
    # Season folder without episode codes → sort by season folder number.
    if is_season_folder_name(parent):
        m = _TV_SEASON_FOLDER_RE.fullmatch(parent.strip())
        if m:
            s = int(m.group(1) or m.group(2) or 0)
            return (0, s, 0, (path or "").casefold())
    return (1, 0, 0, (path or "").casefold())


def _albumartist_meaningful(albumartist: str) -> bool:
    return bool(albumartist) and albumartist != _UNKNOWN_ARTIST


def _artist_meaningful(artist: str) -> bool:
    return bool(artist) and artist != _UNKNOWN_ARTIST


def primary_artist_meta(meta: TrackMetadata) -> str:
    """Artist key for grouping / device folders from track metadata."""
    aa = (meta.albumartist or "").strip()
    if _albumartist_meaningful(aa):
        return aa
    ar = (meta.artist or "").strip()
    return ar if ar else _UNKNOWN_ARTIST


def primary_artist(track: Track) -> str:
    """Artist key for library grouping: prefer albumartist, else track artist.

    Keeps a whole CD under one heading when track-level ARTIST tags differ
    (features, classical performers, “Various Artists” compilations with a
    real albumartist, etc.). Falls back to the track artist when albumartist
    is missing or the unknown placeholder.
    """
    return primary_artist_meta(track.meta)


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


def normalize_library_roots(paths: Iterable[str]) -> list[str]:
    """Deduplicate library roots preserving order; drop empties.

    Paths are normalized with :func:`os.path.normpath` (not realpath) so
    distinct mount points and intentional symlink layouts stay distinct.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw or not isinstance(raw, str):
            continue
        key = os.path.normpath(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


@dataclass
class Library:
    """Ordered collection of tracks for UI indexing (0-based).

    *root_paths* is the durable list of host folders that compose this
    library (one mixed tree, or several media locations). ``root_path`` is
    the first entry when present — convenient for single-root callers and
    file-dialog defaults.
    """

    tracks: list[Track] = field(default_factory=list)
    root_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root_paths = normalize_library_roots(self.root_paths)

    @property
    def root_path(self) -> str:
        """First library root, or empty string when none are set."""
        return self.root_paths[0] if self.root_paths else ""

    def __len__(self) -> int:
        return len(self.tracks)

    def get(self, index: int) -> Track:
        return self.tracks[index]

    def filter_by_directory(self, seed: Track) -> list[Track]:
        """Tracks that share the same parent directory as *seed*."""
        seed_dir = os.path.dirname(seed.path) or seed.path
        return [t for t in self.tracks if (os.path.dirname(t.path) or t.path) == seed_dir]

    def filter_by_artist(self, seed: Track) -> list[Track]:
        """Tracks by the same library artist as *seed*.

        Identity is :func:`primary_artist` (albumartist when set, else track
        artist). Also includes tracks whose track artist or albumartist tag
        equals that key, or whose path has that name as a folder component.
        Logs when a track is included despite a different track-artist tag.
        """
        artist = primary_artist(seed)
        artist_ok = _artist_meaningful(artist)

        matches: list[Track] = []
        for t in self.tracks:
            reasons: list[str] = []
            if primary_artist(t) == artist:
                reasons.append("same_primary_artist")
            if t.meta.artist == artist:
                reasons.append("same_artist")
            if artist_ok and t.meta.albumartist == artist:
                reasons.append("same_albumartist")
            if artist_ok and _path_has_component(t.path, artist):
                reasons.append("path_artist")

            if not reasons:
                continue

            if "same_artist" not in reasons and "same_primary_artist" not in reasons:
                logger.debug(
                    "Artist match (questionable): %r by %r — reasons: %s; artist=%r",
                    t.meta.title,
                    t.meta.artist,
                    ", ".join(reasons),
                    artist,
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
                logger.debug(
                    "Album match (questionable): %r by %r — reasons: %s; album=%r",
                    t.meta.title,
                    t.meta.artist,
                    ", ".join(reasons),
                    album,
                )
            matches.append(t)
        return matches

    def sorted_by_path(self) -> Library:
        return Library(
            tracks=sorted(self.tracks, key=lambda t: t.path),
            root_paths=list(self.root_paths),
        )
