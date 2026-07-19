from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.models import (
    DeviceInfo,
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    FolderEntry,
)


class DevicePort(Protocol):
    """MTP device session and admin operations."""

    def connect(self) -> str:
        """Connect; return device name."""
        ...

    def disconnect(self) -> None: ...

    def get_info(self) -> DeviceInfo: ...

    def set_device_name(self, name: str) -> None: ...

    def create_folder(self, name: str, parent: int = 100) -> int:
        """Create a folder under *parent* (default Music id 100 on ZEN Vision:M).

        Returns the new folder object id.
        """
        ...

    def list_folders(self) -> list[FolderEntry]: ...

    def list_files(self) -> list[FileEntry]:
        """Full device file listing (experimental; may be large/slow)."""
        ...

    def list_tracks(
        self,
        on_progress=None,
    ) -> list[DeviceTrackRef]:
        """Device track listing (music/video; experimental).

        Fast file-listing + media filter (ids/filenames). Optional
        *on_progress(done, total, message)*. Tags via get_track_metadata.
        """
        ...

    def delete_object(self, object_id: int) -> None:
        """Delete one object by MTP item id (experimental)."""
        ...

    def get_file_metadata(self, object_id: int) -> FileEntry:
        """Fetch one object's metadata by id (experimental Get File Info)."""
        ...

    def get_track_metadata(self, object_id: int) -> DeviceTrackInfo:
        """Fetch one track's on-device tags by id (experimental Get Track Info)."""
        ...

    def send_file(self, path: str, remote_name: str | None = None) -> None: ...
