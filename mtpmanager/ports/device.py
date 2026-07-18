from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.models import DeviceInfo, FolderEntry


class DevicePort(Protocol):
    """MTP device session and admin operations."""

    def connect(self) -> str:
        """Connect; return device name."""
        ...

    def disconnect(self) -> None: ...

    def get_info(self) -> DeviceInfo: ...

    def set_device_name(self, name: str) -> None: ...

    def create_folder(self, name: str, parent: int = 100) -> None:
        """Create a folder under *parent* (default Music id 100 on ZEN Vision:M)."""
        ...

    def list_folders(self) -> list[FolderEntry]: ...

    def send_file(self, path: str, remote_name: str | None = None) -> None: ...

    def get_tracklisting(self): ...
