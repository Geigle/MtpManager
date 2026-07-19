"""Domain models — pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrackMetadata:
    artist: str = "Unknown Artist"
    albumartist: str = "Unknown Artist"
    composer: str = "Unknown Composer"
    album: str = "Unknown Album"
    title: str = "Unknown Title"
    genre: str = "Unknown Genre"
    tracknumber: str = "01"
    date: str = ""
    length_sec: float = 0.0
    # Technical stream info (optional; used by pymtp send)
    sample_rate: int = 0
    channels: int = 0
    bitrate: int = 0
    bitrate_mode: int = 0

    def tracknumber_int(self) -> int:
        raw = str(self.tracknumber).split("/")[0].strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1


@dataclass(frozen=True)
class Track:
    path: str
    meta: TrackMetadata


@dataclass(frozen=True)
class DeviceInfo:
    name: str = ""
    serial: str = ""
    manufacturer: str = ""
    battery: Any = None
    model: str = ""
    version: str = ""
    free: int = 0
    total: int = 0
    used: int = 0
    used_percent: float = 0.0

    def as_legacy_dict(self) -> dict:
        """Shape expected by older UI formatting code."""
        return {
            "Name": self.name,
            "Serial": self.serial,
            "Manufacturer": self.manufacturer,
            "Battery": self.battery,
            "Model": self.model,
            "Version": self.version,
            "Free": self.free,
            "Total": self.total,
            "Used": self.used,
            "UsedPercent": self.used_percent,
        }


@dataclass(frozen=True)
class FolderEntry:
    folder_id: int
    name: str
    parent_id: int = 0


@dataclass(frozen=True)
class FileEntry:
    """One object from device file listing / file metadata (LIBMTP_file_t)."""

    item_id: int
    name: str
    parent_id: int = 0
    storage_id: int = 0
    filesize: int = 0
    filetype: int = 0
    modificationdate: int = 0


@dataclass(frozen=True)
class DeviceTrackInfo:
    """On-device track metadata from LIBMTP_Get_Trackmetadata (experimental)."""

    item_id: int
    name: str = ""
    parent_id: int = 0
    storage_id: int = 0
    filesize: int = 0
    filetype: int = 0
    modificationdate: int = 0
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    composer: str = ""
    date: str = ""
    tracknumber: int = 0
    duration_ms: int = 0
    sample_rate: int = 0
    channels: int = 0
    bitrate: int = 0
    bitrate_type: int = 0
    rating: int = 0
    usecount: int = 0


@dataclass(frozen=True)
class DeviceTrackRef:
    """One track from device track listing (ids + labels for delete/admin)."""

    item_id: int
    name: str = ""
    title: str = ""
    artist: str = ""
    parent_id: int = 0
    storage_id: int = 0
    filetype: int = 0


@dataclass(frozen=True)
class DeleteAllResult:
    """Outcome of Device → Delete All Tracks."""

    total: int
    deleted: int
    failed_id: int | None = None
    aborted: bool = False
