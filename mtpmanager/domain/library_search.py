"""Fuzzy library search (stdlib only; no third-party deps).

## Query syntax

Free text is matched across title, artist, album, and related fields with
**equal** base weights. Strongest matches sort first.

Optional **field keywords** boost a metadata field’s weight and require that
field to match the given term:

| Keyword | Field |
|---------|--------|
| ``artist:`` | track artist + primary artist |
| ``albumartist:`` | album artist |
| ``album:`` | album title |
| ``title:`` | track title |
| ``genre:`` | genre |
| ``composer:`` | composer |
| ``path:`` / ``file:`` / ``filename:`` | path / basename |

Examples::

    nightwish
    artist:nightwish
    artist:iron maiden album:powerslave
    title:countdown europe

Quoted terms: ``artist:"blind guardian"``.

Unknown ``field:`` prefixes are treated as ordinary free text.

When a search filter is active in the UI, results are a **flat** list (no
artist/album headers), ordered by score. Clear the search to restore the
normal grouped library view.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import Track

# Collapse runs of whitespace after casefold.
_WS_RE = re.compile(r"\s+")

# artist:term or artist:"quoted term" (term runs until whitespace unless quoted).
_FIELD_TOKEN_RE = re.compile(
    r"""
    (?P<field>[A-Za-z_]+)
    :
    (?P<term>
        "(?P<quoted>[^"]*)"
        |
        (?P<bare>\S+)
    )
    """,
    re.VERBOSE,
)

# Canonical field keys used in scoring / keywords.
FIELD_ARTIST = "artist"
FIELD_ALBUMARTIST = "albumartist"
FIELD_ALBUM = "album"
FIELD_TITLE = "title"
FIELD_GENRE = "genre"
FIELD_COMPOSER = "composer"
FIELD_PATH = "path"

_FIELD_ALIASES: dict[str, str] = {
    "artist": FIELD_ARTIST,
    "albumartist": FIELD_ALBUMARTIST,
    "album": FIELD_ALBUM,
    "title": FIELD_TITLE,
    "genre": FIELD_GENRE,
    "composer": FIELD_COMPOSER,
    "path": FIELD_PATH,
    "file": FIELD_PATH,
    "filename": FIELD_PATH,
}

# Base weight for every field (no default artist preference).
_BASE_WEIGHT = 1.0
# Applied to a field when the query uses field:term for that field.
_FIELD_KEYWORD_BOOST = 3.0

_ALL_FIELDS: tuple[str, ...] = (
    FIELD_ARTIST,
    FIELD_ALBUMARTIST,
    FIELD_ALBUM,
    FIELD_TITLE,
    FIELD_GENRE,
    FIELD_COMPOSER,
    FIELD_PATH,
)


@dataclass(frozen=True)
class ParsedSearchQuery:
    """Parsed toolbar search string."""

    # Free-text remainder (no field: tokens), already normalized.
    free_text: str = ""
    # Canonical field → terms to require/match on that field.
    field_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.free_text and not self.field_terms


def normalize_search_text(value: str) -> str:
    """Casefold and collapse whitespace for matching."""
    return _WS_RE.sub(" ", (value or "").casefold()).strip()


def parse_search_query(raw: str) -> ParsedSearchQuery:
    """Split *raw* into free text and ``field:term`` clauses."""
    text = raw or ""
    free_chunks: list[str] = []
    field_terms: dict[str, list[str]] = defaultdict(list)
    pos = 0
    for m in _FIELD_TOKEN_RE.finditer(text):
        free_chunks.append(text[pos : m.start()])
        key = (m.group("field") or "").casefold()
        term_raw = m.group("quoted")
        if term_raw is None:
            term_raw = m.group("bare") or ""
        term = (term_raw or "").strip()
        canon = _FIELD_ALIASES.get(key)
        if canon and term:
            field_terms[canon].append(term)
        else:
            # Unknown keyword → keep literal in free text.
            free_chunks.append(m.group(0))
        pos = m.end()
    free_chunks.append(text[pos:])
    free = normalize_search_text(" ".join(free_chunks))
    frozen = {k: tuple(v) for k, v in field_terms.items()}
    return ParsedSearchQuery(free_text=free, field_terms=frozen)


def field_weights_for_query(parsed: ParsedSearchQuery) -> dict[str, float]:
    """Base weight 1.0; keyword fields get :data:`_FIELD_KEYWORD_BOOST`."""
    weights = {f: _BASE_WEIGHT for f in _ALL_FIELDS}
    for f in parsed.field_terms:
        if f in weights:
            weights[f] = _BASE_WEIGHT * _FIELD_KEYWORD_BOOST
    return weights


def track_field_texts(track: Track) -> dict[str, str]:
    """Map canonical field → raw searchable text for *track*."""
    meta = track.meta
    artist_parts = [
        primary_artist(track) if track else "",
        meta.artist if meta else "",
    ]
    seen: set[str] = set()
    artist_bits: list[str] = []
    for p in artist_parts:
        n = normalize_search_text(p)
        if n and n not in seen:
            seen.add(n)
            artist_bits.append(p)
    return {
        FIELD_ARTIST: " ".join(artist_bits),
        FIELD_ALBUMARTIST: (meta.albumartist if meta else "") or "",
        FIELD_ALBUM: (meta.album if meta else "") or "",
        FIELD_TITLE: (meta.title if meta else "") or "",
        FIELD_GENRE: (meta.genre if meta else "") or "",
        FIELD_COMPOSER: (meta.composer if meta else "") or "",
        FIELD_PATH: " ".join(
            p
            for p in (
                os.path.basename(track.path or ""),
                track.path or "",
            )
            if p
        ),
    }


def track_search_fields(track: Track) -> list[tuple[float, str]]:
    """Weighted fields for *track* (equal base weights; no query boosts)."""
    texts = track_field_texts(track)
    return [(_BASE_WEIGHT, texts[f]) for f in _ALL_FIELDS]


def track_search_blob(track: Track) -> str:
    """Combined searchable string (tests / diagnostics)."""
    return normalize_search_text(
        " ".join(text for text in track_field_texts(track).values() if text)
    )


def _subsequence_score(query: str, text: str) -> float:
    """Score 0..1 if all *query* chars appear in order in *text* (dense better)."""
    if not query:
        return 1.0
    if not text:
        return 0.0
    qi = 0
    first = -1
    last = -1
    qlen = len(query)
    for ti, ch in enumerate(text):
        if ch != query[qi]:
            continue
        if first < 0:
            first = ti
        last = ti
        qi += 1
        if qi >= qlen:
            span = max(1, last - first + 1)
            density = qlen / span
            coverage = min(1.0, qlen / max(len(text), 1))
            return max(0.0, min(1.0, 0.75 * density + 0.25 * coverage))
    return 0.0


def score_query_against_text(query: str, text: str) -> float:
    """Fuzzy score of *query* against *text* (both already normalized or not)."""
    q = normalize_search_text(query)
    blob = normalize_search_text(text)
    if not q:
        return 1.0
    if not blob:
        return 0.0
    idx = blob.find(q)
    if idx >= 0:
        pos = 1.0 - idx / max(len(blob), 1)
        coverage = min(1.0, len(q) / max(len(blob), 1))
        return 1.0 + 0.05 * pos + 0.08 * coverage

    tokens = q.split()
    if not tokens:
        return 0.0

    token_scores: list[float] = []
    for tok in tokens:
        if tok in blob:
            token_scores.append(0.92)
            continue
        sub = _subsequence_score(tok, blob)
        if sub <= 0.0:
            return 0.0
        token_scores.append(0.55 + 0.35 * sub)
    return sum(token_scores) / len(token_scores)


def score_track(
    query: str | ParsedSearchQuery,
    track: Track,
) -> float:
    """Fuzzy relevance of *track* for *query* (0 = no match).

    Equal field weights by default. ``field:term`` clauses boost that field’s
    weight and require a match on that field for *term*.
    """
    parsed = (
        query
        if isinstance(query, ParsedSearchQuery)
        else parse_search_query(query)
    )
    if parsed.is_empty():
        return 1.0

    texts = track_field_texts(track)
    weights = field_weights_for_query(parsed)

    # Require every field:term to match its field.
    for field_key, terms in parsed.field_terms.items():
        text = texts.get(field_key, "")
        for term in terms:
            if score_query_against_text(term, text) <= 0.0:
                return 0.0

    best = 0.0

    # Score required field terms (with boosted weights).
    for field_key, terms in parsed.field_terms.items():
        text = texts.get(field_key, "")
        w = weights.get(field_key, _BASE_WEIGHT)
        for term in terms:
            s = score_query_against_text(term, text)
            if s > 0.0:
                best = max(best, w * s)

    free = parsed.free_text
    if not free:
        return best

    # Free text: best weighted field for the full phrase.
    for field_key, text in texts.items():
        s = score_query_against_text(free, text)
        if s > 0.0:
            best = max(best, weights.get(field_key, _BASE_WEIGHT) * s)

    tokens = free.split()
    if len(tokens) <= 1:
        return best

    # Multi-token free text: each token must match somewhere.
    token_scores: list[float] = []
    for tok in tokens:
        tok_best = 0.0
        for field_key, text in texts.items():
            s = score_query_against_text(tok, text)
            if s > 0.0:
                tok_best = max(
                    tok_best, weights.get(field_key, _BASE_WEIGHT) * s
                )
        if tok_best <= 0.0:
            return 0.0
        token_scores.append(tok_best)
    blended = sum(token_scores) / len(token_scores)
    return max(best, blended)


def filter_library_tracks_scored(
    tracks: Sequence[Track],
    query: str,
    *,
    min_score: float = 0.12,
) -> tuple[list[Track], dict[str, float]]:
    """Return (tracks best-first, path→score) for *query*.

    Empty / whitespace *query* returns all *tracks* in original order and an
    empty score map.
    """
    parsed = parse_search_query(query)
    if parsed.is_empty():
        return list(tracks), {}

    scored: list[tuple[float, str, Track]] = []
    for t in tracks:
        s = score_track(parsed, t)
        if s >= min_score:
            scored.append((s, t.path or "", t))
    scored.sort(key=lambda row: (-row[0], row[1].casefold()))
    out = [t for _s, _p, t in scored]
    scores = {p: s for s, p, _t in scored if p}
    return out, scores


def filter_library_tracks(
    tracks: Sequence[Track],
    query: str,
    *,
    min_score: float = 0.12,
) -> list[Track]:
    """Return tracks matching *query*, best matches first.

    Empty / whitespace *query* returns a shallow copy of *tracks* in the
    original order (no scoring).
    """
    out, _scores = filter_library_tracks_scored(
        tracks, query, min_score=min_score
    )
    return out


def sort_tracks_by_score(
    tracks: Sequence[Track],
    scores: dict[str, float],
) -> list[Track]:
    """Stable order: highest score first, then path."""
    return sorted(
        tracks,
        key=lambda t: (
            -float(scores.get(t.path or "", 0.0)),
            (t.path or "").casefold(),
        ),
    )


def _group_best_score(node, scores: dict[str, float]) -> float:
    """Max track score under a :class:`~mtpmanager.domain.library_sort.GroupNode`."""
    best = 0.0
    for t in getattr(node, "tracks", ()) or ():
        best = max(best, float(scores.get(t.path or "", 0.0)))
    for child in getattr(node, "children", ()) or ():
        best = max(best, _group_best_score(child, scores))
    return best


def reorder_groups_by_score(groups: Sequence, scores: dict[str, float]) -> list:
    """Reorder group tree so strongest matches surface first.

    *groups* are :class:`~mtpmanager.domain.library_sort.GroupNode` instances.
    Within each group, tracks are sorted by score; sibling groups by best score.
    (UI search mode uses a flat list; this remains for optional callers.)
    """
    from mtpmanager.domain.library_sort import GroupNode

    if not scores:
        return list(groups)

    def reorder_node(node: GroupNode) -> GroupNode:
        if node.children:
            kids = [reorder_node(c) for c in node.children]
            kids.sort(
                key=lambda n: (
                    -_group_best_score(n, scores),
                    (n.label or "").casefold(),
                    n.key,
                )
            )
            return GroupNode(
                key=node.key,
                label=node.label,
                tracks=(),
                children=tuple(kids),
            )
        ordered = sort_tracks_by_score(node.tracks, scores)
        return GroupNode(
            key=node.key,
            label=node.label,
            tracks=tuple(ordered),
            children=(),
        )

    out = [reorder_node(g) for g in groups]
    out.sort(
        key=lambda n: (
            -_group_best_score(n, scores),
            (n.label or "").casefold(),
            n.key,
        )
    )
    return out
