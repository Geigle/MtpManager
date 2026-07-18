"""Device administration use cases."""

from __future__ import annotations

from mtpmanager.domain.models import DeviceInfo, FileEntry, FolderEntry
from mtpmanager.infra.remote_naming import DEFAULT_MUSIC_FOLDER_ID
from mtpmanager.ports.device import DevicePort


def connect(device: DevicePort) -> str:
    return device.connect()


def disconnect(device: DevicePort) -> None:
    device.disconnect()


def get_device_info(device: DevicePort) -> DeviceInfo:
    return device.get_info()


def set_device_name(device: DevicePort, name: str) -> None:
    device.set_device_name(name)


def create_folder(
    device: DevicePort,
    name: str,
    parent: int = DEFAULT_MUSIC_FOLDER_ID,
) -> int:
    return device.create_folder(name, parent=parent)


def list_folders(device: DevicePort) -> list[FolderEntry]:
    return device.list_folders()


def list_files(device: DevicePort) -> list[FileEntry]:
    """Experimental full file listing (Device → List Files)."""
    return device.list_files()


def delete_object(device: DevicePort, object_id: int) -> None:
    """Experimental single-object delete (Device → Delete Track)."""
    device.delete_object(int(object_id))


def send_test_file(device: DevicePort, path: str) -> None:
    device.send_file(path)
