"""Extended M3U parse/serialize for host playlists (stdlib only)."""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import Track, TrackMetadata

# Leading whitespace stripped; paths keep internal spaces.
_EXTINF_RE = re.compile(
    r"^#EXTINF\s*:\s*(-?\d+)\s*(?:,\s*(.*))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlaylistEntry:
    """One media path in playlist order (optional EXTINF metadata)."""

    path: str
    title: str = ""
    artist: str = ""
    duration_sec: int = -1


def empty_m3u() -> str:
    """Minimal valid extended M3U document."""
    return "#EXTM3U\n"


def parse_m3u(text: str) -> list[PlaylistEntry]:
    """Parse extended or simple M3U into ordered entries.

    Ignores blank lines and unknown ``#`` comments. An ``#EXTINF`` line
    attaches to the next non-comment path line only.
    """
    if not text:
        return []
    entries: list[PlaylistEntry] = []
    pending_dur = -1
    pending_artist = ""
    pending_title = ""
    has_extinf = False

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _EXTINF_RE.match(line)
            if m:
                try:
                    pending_dur = int(m.group(1))
                except (TypeError, ValueError):
                    pending_dur = -1
                display = (m.group(2) or "").strip()
                pending_artist, pending_title = _split_extinf_display(display)
                has_extinf = True
            continue

        path = os.path.normpath(line)
        if has_extinf:
            entries.append(
                PlaylistEntry(
                    path=path,
                    title=pending_title,
                    artist=pending_artist,
                    duration_sec=pending_dur,
                )
            )
        else:
            entries.append(PlaylistEntry(path=path))
        pending_dur = -1
        pending_artist = ""
        pending_title = ""
        has_extinf = False
    return entries


def serialize_m3u(entries: list[PlaylistEntry] | list[Track]) -> str:
    """Build extended M3U text (always starts with ``#EXTM3U``)."""
    lines = ["#EXTM3U"]
    for item in entries:
        if isinstance(item, Track):
            entry = entry_from_track(item)
        else:
            entry = item
        if not entry.path:
            continue
        lines.append(_extinf_line(entry))
        lines.append(entry.path)
    return "\n".join(lines) + "\n"


def entry_from_track(track: Track) -> PlaylistEntry:
    """Build a playlist entry from a library track."""
    meta = track.meta or TrackMetadata()
    artist = primary_artist(track)
    title = (meta.title or "").strip()
    dur = -1
    if meta.length_sec and meta.length_sec > 0:
        dur = int(meta.length_sec)
    return PlaylistEntry(
        path=os.path.normpath(track.path or ""),
        title=title,
        artist=artist if artist != "Unknown Artist" else (meta.artist or ""),
        duration_sec=dur,
    )


def append_entries(
    text: str,
    new_entries: list[PlaylistEntry],
    *,
    skip_existing: bool = True,
) -> str:
    """Append entries to M3U text; optionally skip paths already present."""
    existing = parse_m3u(text)
    seen = {os.path.normpath(e.path) for e in existing}
    out = list(existing)
    for e in new_entries:
        path = os.path.normpath(e.path or "")
        if not path:
            continue
        if skip_existing and path in seen:
            continue
        out.append(
            PlaylistEntry(
                path=path,
                title=e.title,
                artist=e.artist,
                duration_sec=e.duration_sec,
            )
        )
        seen.add(path)
    return serialize_m3u(out)


def remove_paths(text: str, paths: list[str] | set[str]) -> str:
    """Drop entries whose path is in *paths* (normpath match)."""
    drop = {os.path.normpath(p) for p in paths if p}
    if not drop:
        return serialize_m3u(parse_m3u(text)) if text.strip() else empty_m3u()
    kept = [
        e for e in parse_m3u(text) if os.path.normpath(e.path) not in drop
    ]
    return serialize_m3u(kept) if kept else empty_m3u()


def move_paths(
    text: str,
    paths: list[str] | set[str],
    *,
    delta: int,
) -> str:
    """Move selected paths one step up (*delta* < 0) or down (*delta* > 0).

    Multi-select moves each selected index by one step in *delta*'s direction,
    processing from the end that avoids collisions (standard list reorder).
    Paths are matched by normpath; only the first occurrence of each path is
    selected when *paths* is a set of path strings. Duplicate path rows: all
    matching occurrences whose path is in *paths* move together.
    """
    if not delta:
        return serialize_m3u(parse_m3u(text)) if text.strip() else empty_m3u()
    entries = parse_m3u(text)
    if len(entries) < 2:
        return serialize_m3u(entries) if entries else empty_m3u()

    want = {os.path.normpath(p) for p in paths if p}
    if not want:
        return serialize_m3u(entries)

    selected = [
        i
        for i, e in enumerate(entries)
        if os.path.normpath(e.path) in want
    ]
    if not selected:
        return serialize_m3u(entries)

    items = list(entries)
    step = -1 if delta < 0 else 1
    if step < 0:
        # Move up: lowest indices first.
        if min(selected) == 0:
            return serialize_m3u(items)
        for i in sorted(selected):
            j = i - 1
            items[i], items[j] = items[j], items[i]
    else:
        # Move down: highest indices first.
        if max(selected) >= len(items) - 1:
            return serialize_m3u(items)
        for i in sorted(selected, reverse=True):
            j = i + 1
            items[i], items[j] = items[j], items[i]
    return serialize_m3u(items)


def reorder_by_paths(text: str, ordered_paths: list[str]) -> str:
    """Rewrite playlist order from an ordered path list.

    Each path in *ordered_paths* consumes the next unused matching entry from
    *text* (normpath). Entries never listed are appended in prior order.
    """
    entries = parse_m3u(text)
    if not entries:
        return empty_m3u()
    pools: dict[str, deque[PlaylistEntry]] = {}
    for e in entries:
        pools.setdefault(os.path.normpath(e.path), deque()).append(e)

    out: list[PlaylistEntry] = []
    taken: set[int] = set()
    for raw in ordered_paths:
        key = os.path.normpath(raw or "")
        if not key:
            continue
        pool = pools.get(key)
        if not pool:
            continue
        e = pool.popleft()
        out.append(e)
        taken.add(id(e))
    for e in entries:
        if id(e) not in taken:
            out.append(e)
    return serialize_m3u(out) if out else empty_m3u()


def paths_in_m3u(text: str) -> list[str]:
    """Ordered unique-preserving list of paths in *text*."""
    return [e.path for e in parse_m3u(text)]


def _split_extinf_display(display: str) -> tuple[str, str]:
    """Split ``Artist - Title`` display; title-only if no separator."""
    if not display:
        return "", ""
    if " - " in display:
        artist, title = display.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", display.strip()


def _extinf_line(entry: PlaylistEntry) -> str:
    dur = int(entry.duration_sec) if entry.duration_sec is not None else -1
    if dur < 0:
        dur = -1
    artist = (entry.artist or "").strip()
    title = (entry.title or "").strip()
    if artist and title:
        display = f"{artist} - {title}"
    elif title:
        display = title
    elif artist:
        display = artist
    else:
        display = os.path.basename(entry.path) or "Unknown"
    return f"#EXTINF:{dur},{display}"
