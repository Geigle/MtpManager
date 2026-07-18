"""Pure string formatting for UI display."""

from __future__ import annotations

from mtpmanager.domain.models import (
    DeviceInfo,
    DeviceTrackInfo,
    FileEntry,
    FolderEntry,
    Track,
)


def track_summary(track: Track) -> str:
    m = track.meta
    return f"{m.title[:30]}, {m.artist[:30]}, {m.album[:30]}, ({m.tracknumber})"


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
