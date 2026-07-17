"""Pure string formatting for UI display."""

from __future__ import annotations

from mtpmanager.domain.models import DeviceInfo, FolderEntry, Track


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
    return f"{entry.folder_id:8} {entry.name}"
