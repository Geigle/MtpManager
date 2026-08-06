"""Fuzzy library search (stdlib only; no third-party deps)."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import Track

# Collapse runs of whitespace after casefold.
_WS_RE = re.compile(r"\s+")


def normalize_search_text(value: str) -> str:
    """Casefold and collapse whitespace for matching."""
    return _WS_RE.sub(" ", (value or "").casefold()).strip()


def track_search_blob(track: Track) -> str:
    """Searchable string for one library track."""
    meta = track.meta
    parts = [
        meta.title if meta else "",
        primary_artist(track) if track else "",
        meta.artist if meta else "",
        meta.albumartist if meta else "",
        meta.album if meta else "",
        meta.genre if meta else "",
        meta.composer if meta else "",
        os.path.basename(track.path or ""),
        track.path or "",
    ]
    return normalize_search_text(" ".join(p for p in parts if p))


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
            # Slight boost for matching more of the haystack length.
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
    # Full phrase substring — best.
    idx = blob.find(q)
    if idx >= 0:
        # Prefer earlier matches slightly.
        return 1.0 + 0.05 * (1.0 - idx / max(len(blob), 1))

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
            # All tokens must match (AND).
            return 0.0
        token_scores.append(0.55 + 0.35 * sub)
    return sum(token_scores) / len(token_scores)


def score_track(query: str, track: Track) -> float:
    """Fuzzy relevance of *track* for *query* (0 = no match)."""
    return score_query_against_text(query, track_search_blob(track))


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
    q = normalize_search_text(query)
    if not q:
        return list(tracks), {}

    scored: list[tuple[float, str, Track]] = []
    for t in tracks:
        s = score_track(q, t)
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
