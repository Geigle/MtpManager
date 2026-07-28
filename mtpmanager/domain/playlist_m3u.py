"""Extended M3U parse/serialize for host playlists (stdlib only)."""

from __future__ import annotations

import os
import re
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
