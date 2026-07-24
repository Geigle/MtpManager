"""Pure string formatting for UI display."""

from __future__ import annotations

from mtpmanager.domain.library import year_from_date
from mtpmanager.domain.models import (
    DeviceInfo,
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    FolderEntry,
    Track,
    TrackMetadata,
)


def track_summary(track: Track) -> str:
    m = track.meta
    return f"{m.title[:30]}, {m.artist[:30]}, {m.album[:30]}, ({m.tracknumber})"


def format_duration(seconds: float | int | None) -> str:
    """Human duration for selection detail (m:ss or h:mm:ss)."""
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _meta_line_bits(meta: TrackMetadata) -> list[str]:
    bits: list[str] = []
    year = year_from_date(meta.date or "")
    if year:
        bits.append(year)
    tn = str(meta.tracknumber or "").strip()
    if tn and tn not in ("0",):
        bits.append(f"#{tn}")
    dur = format_duration(meta.length_sec)
    if dur:
        bits.append(dur)
    return bits


def track_selection_detail(track: Track) -> str:
    """Multi-line left-panel detail for a single selected library track."""
    m = track.meta
    lines: list[str] = [
        (m.title or "").strip() or "Unknown Title",
        (m.artist or "").strip() or "Unknown Artist",
        (m.album or "").strip() or "Unknown Album",
    ]
    bits = _meta_line_bits(m)
    if bits:
        lines.append(" · ".join(bits))
    genre = (m.genre or "").strip()
    if genre and genre.casefold() not in ("unknown genre", "unknown"):
        lines.append(genre)
    return "\n".join(lines)


def artist_selection_detail(artist: str, track_count: int) -> str:
    """Left-panel detail for an artist group header selection."""
    name = (artist or "").strip() or "Unknown Artist"
    n = max(0, int(track_count))
    noun = "track" if n == 1 else "tracks"
    return f"{name}\n{n} {noun}"


def album_selection_detail(
    album: str,
    *,
    artist: str = "",
    track_count: int = 0,
    year: str = "",
) -> str:
    """Left-panel detail for an album group header selection."""
    lines = [(album or "").strip() or "Unknown Album"]
    art = (artist or "").strip()
    if art:
        lines.append(art)
    y = (year or "").strip()
    n = max(0, int(track_count))
    noun = "track" if n == 1 else "tracks"
    tail_bits: list[str] = []
    if y:
        tail_bits.append(y)
    tail_bits.append(f"{n} {noun}")
    lines.append(" · ".join(tail_bits))
    return "\n".join(lines)


def multi_selection_detail(track_count: int) -> str:
    """Left-panel detail when multiple rows/tracks are selected."""
    n = max(0, int(track_count))
    noun = "track" if n == 1 else "tracks"
    return f"{n} {noun} selected"


def device_info_summary(info: DeviceInfo) -> str:
    used_mb = (info.used or 0) / 1_000_000
    total_mb = (info.total or 0) / 1_000_000
    return (
        f"Name:{info.name}\n"
        f"Serial:{info.serial}\n"
        f"Manufacturer:{info.manufacturer}\n"
        f"Battery:{info.battery}\n"
        f"Model:{info.model}\n"
        f"Version:{info.version}\n"
        f"Used:{used_mb:.2f}/{total_mb:.2f}\n"
        f"Used %:{info.used_percent:.2f}\n"
        f"Free:{info.free}"
    )


def folder_line(entry: FolderEntry) -> str:
    parent = getattr(entry, "parent_id", 0) or 0
    if parent:
        return f"{entry.folder_id:8} {entry.name}  (parent {parent})"
    return f"{entry.folder_id:8} {entry.name}"


def file_line(entry: FileEntry) -> str:
    """One line for Device → List Files dialog / logs."""
    size = int(entry.filesize or 0)
    if size >= 1_000_000:
        size_s = f"{size / 1_000_000:.1f}MB"
    elif size >= 1000:
        size_s = f"{size / 1000:.1f}kB"
    else:
        size_s = f"{size}B"
    return (
        f"{entry.item_id:8}  parent={entry.parent_id:<6}  "
        f"type={entry.filetype:<3}  {size_s:>8}  {entry.name}"
    )


def track_line(entry: DeviceTrackRef) -> str:
    """One line for Device → List Tracks dialog / logs.

    Prefer on-device artist/title when present; fall back to filename so
    file-listing-only rows (no tags) are still readable.
    """
    name = (entry.name or "").strip() or "(unnamed)"
    title = (entry.title or "").strip() or name
    artist = (entry.artist or "").strip() or "—"
    return (
        f"{entry.item_id:8}  parent={entry.parent_id:<6}  "
        f"type={entry.filetype:<3}  {artist[:24]:<24}  "
        f"{title[:28]:<28}  {name}"
    )


def file_metadata_summary(entry: FileEntry) -> str:
    """Multi-line summary for Device → Get File Info."""
    size = int(entry.filesize or 0)
    if size >= 1_000_000:
        size_s = f"{size / 1_000_000:.2f} MB ({size} bytes)"
    elif size >= 1000:
        size_s = f"{size / 1000:.1f} kB ({size} bytes)"
    else:
        size_s = f"{size} bytes"

    mtime = int(entry.modificationdate or 0)
    if mtime > 0:
        try:
            from datetime import datetime, timezone

            mtime_s = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except (OverflowError, OSError, ValueError):
            mtime_s = str(mtime)
    else:
        mtime_s = "(none)"

    name = (entry.name or "").strip() or "(unnamed)"
    return (
        f"Object id: {entry.item_id}\n"
        f"Name: {name}\n"
        f"Parent id: {entry.parent_id}\n"
        f"Storage id: 0x{int(entry.storage_id):08x} ({entry.storage_id})\n"
        f"Filetype: {entry.filetype}\n"
        f"Size: {size_s}\n"
        f"Modified: {mtime_s}"
    )


def track_metadata_summary(info: DeviceTrackInfo) -> str:
    """Multi-line summary for Device → Get Track Info."""
    size = int(info.filesize or 0)
    if size >= 1_000_000:
        size_s = f"{size / 1_000_000:.2f} MB ({size} bytes)"
    elif size >= 1000:
        size_s = f"{size / 1000:.1f} kB ({size} bytes)"
    else:
        size_s = f"{size} bytes"

    mtime = int(info.modificationdate or 0)
    if mtime > 0:
        try:
            from datetime import datetime, timezone

            mtime_s = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except (OverflowError, OSError, ValueError):
            mtime_s = str(mtime)
    else:
        mtime_s = "(none)"

    dur_ms = int(info.duration_ms or 0)
    if dur_ms > 0:
        total_s = dur_ms // 1000
        mm, ss = divmod(total_s, 60)
        duration_s = f"{mm}:{ss:02d} ({dur_ms} ms)"
    else:
        duration_s = "(none)"

    name = (info.name or "").strip() or "(unnamed)"
    title = (info.title or "").strip() or "(none)"
    artist = (info.artist or "").strip() or "(none)"
    album = (info.album or "").strip() or "(none)"
    genre = (info.genre or "").strip() or "(none)"
    composer = (info.composer or "").strip() or "(none)"
    date = (info.date or "").strip() or "(none)"

    br = int(info.bitrate or 0)
    br_s = f"{br} bps" if br else "(none)"
    sr = int(info.sample_rate or 0)
    sr_s = f"{sr} Hz" if sr else "(none)"
    ch = int(info.channels or 0)
    ch_s = str(ch) if ch else "(none)"

    return (
        f"Object id: {info.item_id}\n"
        f"Filename: {name}\n"
        f"Parent id: {info.parent_id}\n"
        f"Storage id: 0x{int(info.storage_id):08x} ({info.storage_id})\n"
        f"Filetype: {info.filetype}\n"
        f"Size: {size_s}\n"
        f"Modified: {mtime_s}\n"
        f"---\n"
        f"Title: {title}\n"
        f"Artist: {artist}\n"
        f"Album: {album}\n"
        f"Track #: {info.tracknumber}\n"
        f"Genre: {genre}\n"
        f"Composer: {composer}\n"
        f"Date: {date}\n"
        f"Duration: {duration_s}\n"
        f"Sample rate: {sr_s}\n"
        f"Channels: {ch_s}\n"
        f"Bitrate: {br_s}\n"
        f"Bitrate type: {info.bitrate_type}\n"
        f"Rating: {info.rating}\n"
        f"Use count: {info.usecount}"
    )
