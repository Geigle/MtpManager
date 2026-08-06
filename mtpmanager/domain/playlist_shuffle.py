"""Playlist reorder shuffles (artist-aware; pure, no I/O).

Two algorithms:

* **merge_shuffle** — merge partitions by artist (tracks free to leave album
  order); optional hierarchical mode keeps albums as blocks.
* **merge_shuffle_by_album** — hierarchical merge: shuffle within each album,
  keep album blocks together, merge-shuffle those blocks by artist.
* **spotify_shuffle** — 2014-style dithered / balanced positions per artist.

Both take a sequence of :class:`~mtpmanager.domain.models.Track` (or any object
with artist metadata) and return a **new** list. Pass a seeded
:class:`random.Random` for reproducibility; the UI seeds from the context-menu
track.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import Track, TrackMetadata

T = TypeVar("T")

# Spec sentinel for empty / missing artist.
_UNKNOWN = "Unknown"


def artist_key(track: object) -> str:
    """Stable artist string for shuffle grouping."""
    if isinstance(track, Track):
        raw = primary_artist(track)
    else:
        meta = getattr(track, "meta", None)
        if isinstance(meta, TrackMetadata):
            raw = primary_artist(Track(path="", meta=meta))
        else:
            raw = getattr(track, "artist", None) or ""
    s = (raw or "").strip()
    if not s or s.casefold() in {"unknown", "unknown artist"}:
        return _UNKNOWN
    return s


def album_key(track: object) -> str:
    """Album label for hierarchical merge-shuffle."""
    meta = getattr(track, "meta", None)
    if isinstance(meta, TrackMetadata):
        s = (meta.album or "").strip()
    else:
        s = (getattr(track, "album", None) or "").strip()
    if not s or s.casefold() in {"unknown", "unknown album"}:
        return "Unknown Album"
    return s


def seed_from_track(track: object) -> int:
    """Deterministic RNG seed from a seed track (guid preferred, else path)."""
    guid = (getattr(track, "guid", None) or "").strip().lower()
    path = (getattr(track, "path", None) or "").strip()
    raw = f"{guid}|{path}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:16], 16)


def rng_from_seed_track(
    track: object | None,
    *,
    extra: str = "",
) -> random.Random:
    """Build a Random for shuffle; *extra* distinguishes algorithms if needed."""
    base = seed_from_track(track) if track is not None else 0
    if extra:
        mix = hashlib.sha256(f"{base}:{extra}".encode()).hexdigest()
        base = int(mix[:16], 16)
    return random.Random(base)


def fisher_yates(items: Sequence[T], rng: random.Random) -> list[T]:
    """Return a new list shuffled with Fisher–Yates."""
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = rng.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def _split_evenly(
    x: Sequence[T],
    n_parts: int,
    rng: random.Random,
) -> list[list[T]]:
    """Partition *x* into *n_parts* contiguous parts; lengths differ by ≤1."""
    items = list(x)
    if n_parts <= 0:
        return [items] if items else []
    if n_parts == 1:
        return [items]
    n = len(items)
    base, extra = divmod(n, n_parts)
    # Distribute remainders randomly among parts.
    extras = [0] * n_parts
    if extra > 0:
        idxs = list(range(n_parts))
        rng.shuffle(idxs)
        for i in idxs[:extra]:
            extras[i] = 1
    parts: list[list[T]] = []
    pos = 0
    for i in range(n_parts):
        ln = base + extras[i]
        parts.append(items[pos : pos + ln])
        pos += ln
    return parts


def interleave(
    x: Sequence[T],
    y: Sequence[T],
    rng: random.Random,
) -> list[T]:
    """Interleave larger partition *x* around elements of *y* (``len(x) >= len(y)``)."""
    xs = fisher_yates(x, rng)
    ys = list(y)
    parts = _split_evenly(xs, len(ys) + 1, rng)
    out: list[T] = []
    for i, part in enumerate(parts):
        out.extend(part)
        if i < len(ys):
            out.append(ys[i])
    return out


def _runs_by_key(
    items: Sequence[T],
    key_fn: Callable[[T], str],
) -> list[list[T]]:
    if not items:
        return []
    runs: list[list[T]] = []
    cur: list[T] = [items[0]]
    cur_key = key_fn(items[0])
    for item in items[1:]:
        k = key_fn(item)
        if k == cur_key:
            cur.append(item)
        else:
            runs.append(cur)
            cur = [item]
            cur_key = k
    runs.append(cur)
    return runs


def _adjust_span_count(
    spans: list[list[T]],
    need: int,
    rng: random.Random,
) -> list[list[T]]:
    """Grow/shrink span list to exactly *need* by splitting or merging."""
    spans = [list(s) for s in spans if s]
    if need <= 0:
        return spans
    if not spans:
        return [[] for _ in range(need)]

    while len(spans) < need:
        candidates = [i for i, s in enumerate(spans) if len(s) > 1]
        if not candidates:
            # Cannot split further; pad empty spans at random edges.
            spans.insert(rng.randrange(len(spans) + 1), [])
            continue
        i = candidates[rng.randrange(len(candidates))]
        s = spans[i]
        cut = rng.randrange(1, len(s))
        spans = spans[:i] + [s[:cut], s[cut:]] + spans[i + 1 :]

    while len(spans) > need:
        i = rng.randrange(len(spans) - 1)
        spans[i] = spans[i] + spans[i + 1]
        del spans[i + 1]

    return spans


def intersperse(
    x: Sequence[T],
    y: Sequence[T],
    rng: random.Random,
    key_fn: Callable[[T], str],
) -> list[T]:
    """Insert smaller partition *x* into *y*, targeting same-artist runs."""
    xs = list(x)
    ys = list(y)
    if not xs:
        return ys
    if not ys:
        return fisher_yates(xs, rng)

    spans = _runs_by_key(ys, key_fn)
    spans = _adjust_span_count(spans, len(xs) + 1, rng)
    # Shuffle insert order of x for variety within the structure.
    xs = fisher_yates(xs, rng)
    out: list[T] = []
    for i, span in enumerate(spans):
        out.extend(span)
        if i < len(xs):
            out.append(xs[i])
    return out


def merge_shuffle_by_key(
    items: Sequence[T],
    key_fn: Callable[[T], str],
    rng: random.Random,
) -> list[T]:
    """Core merge-shuffle: partitions by *key_fn*, smallest first."""
    items = list(items)
    if len(items) <= 1:
        return items

    groups: dict[str, list[T]] = defaultdict(list)
    for item in items:
        groups[key_fn(item)].append(item)

    if len(groups) == 1:
        return fisher_yates(items, rng)

    by_len: dict[int, list[list[T]]] = defaultdict(list)
    for part in groups.values():
        by_len[len(part)].append(list(part))

    partitions: list[list[T]] = []
    for length in sorted(by_len.keys()):
        bucket = by_len[length]
        rng.shuffle(bucket)
        partitions.extend(bucket)

    result: list[T] = []
    for part in partitions:
        n = len(part)
        m = len(result)
        if n >= m:
            result = interleave(part, result, rng)
        else:
            result = intersperse(part, result, rng, key_fn)
    return result


@dataclass
class _AlbumBundle:
    """Super-track for hierarchical merge-shuffle (one album under one artist)."""

    artist: str
    tracks: list[Track]


def merge_shuffle(
    tracks: Sequence[Track],
    *,
    rng: random.Random | None = None,
    hierarchical: bool = False,
) -> list[Track]:
    """Merge-shuffle by artist (default: individual tracks, not album blocks).

    Spreads artists to reduce consecutive same-artist plays. Tracks from the
    same album may separate. Pass *hierarchical=True* (or use
    :func:`merge_shuffle_by_album`) to keep each album as a contiguous block.
    """
    rng = rng if rng is not None else random.Random()
    items = list(tracks)
    if len(items) <= 1:
        return items

    if not hierarchical:
        return merge_shuffle_by_key(items, artist_key, rng)

    # Group by (artist, album); preserve first-seen order for stability.
    album_groups: dict[tuple[str, str], list[Track]] = {}
    album_order: list[tuple[str, str]] = []
    for t in items:
        key = (artist_key(t), album_key(t))
        if key not in album_groups:
            album_groups[key] = []
            album_order.append(key)
        album_groups[key].append(t)

    bundles: list[_AlbumBundle] = []
    for key in album_order:
        art, _alb = key
        shuffled = fisher_yates(album_groups[key], rng)
        bundles.append(_AlbumBundle(artist=art, tracks=shuffled))

    def bundle_artist(b: _AlbumBundle) -> str:
        return b.artist

    ordered = merge_shuffle_by_key(bundles, bundle_artist, rng)
    out: list[Track] = []
    for b in ordered:
        out.extend(b.tracks)
    return out


def merge_shuffle_by_album(
    tracks: Sequence[Track],
    *,
    rng: random.Random | None = None,
) -> list[Track]:
    """Merge-shuffle that keeps each album as a contiguous block.

    Within each album tracks are Fisher–Yates shuffled; album blocks are then
    merge-shuffled by artist (same-artist albums may still interleave with
    other artists' blocks).
    """
    return merge_shuffle(tracks, rng=rng, hierarchical=True)


def spotify_shuffle(
    tracks: Sequence[Track],
    *,
    rng: random.Random | None = None,
    jitter_frac: float = 0.1,
) -> list[Track]:
    """Spotify 2014-style dithered / balanced shuffle by artist.

    *jitter_frac* is the ± fraction of ideal spacing (Spotify used 0.1).
    """
    rng = rng if rng is not None else random.Random()
    items = list(tracks)
    if len(items) <= 1:
        return items

    groups: dict[str, list[Track]] = defaultdict(list)
    for t in items:
        groups[artist_key(t)].append(t)

    positioned: list[tuple[float, int, Track]] = []
    # Secondary index keeps sort stable for identical floats.
    seq = 0
    for _artist, group in groups.items():
        g = fisher_yates(group, rng)
        n = len(g)
        if n == 0:
            continue
        spacing = 1.0 / n
        initial = rng.uniform(0.0, spacing)
        amp = jitter_frac * spacing
        for k, track in enumerate(g):
            jitter = rng.uniform(-amp, amp) if amp > 0 else 0.0
            pos = (k / n) + initial + jitter
            positioned.append((pos, seq, track))
            seq += 1

    positioned.sort(key=lambda row: (row[0], row[1]))
    return [t for _p, _i, t in positioned]
