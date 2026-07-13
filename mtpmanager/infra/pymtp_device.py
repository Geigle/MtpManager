"""pymtp/libmtp device adapter — no Tk, no messageboxes."""

from __future__ import annotations

import ctypes
import os

import mtpmanager.infra.pymtp_wrapper as pymtp
from mtpmanager.domain.models import DeviceInfo, FolderEntry, TrackMetadata


def _c_str(value: str) -> ctypes.c_char_p:
    return ctypes.c_char_p(value.encode("utf-8"))


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class PymtpDevice:
    """DevicePort + Transport implementation backed by pymtp.MTP."""

    def __init__(self, mtp: pymtp.MTP | None = None):
        self._mtp = mtp if mtp is not None else pymtp.MTP()

    @property
    def raw(self) -> pymtp.MTP:
        return self._mtp

    def connect(self) -> str:
        try:
            self._mtp.connect()
            name = _decode(self._mtp.get_devicename())
            print(f"Connected to {name}")
            return name
        except pymtp.AlreadyConnected:
            try:
                name = _decode(self._mtp.get_devicename())
            except Exception:
                name = "(unknown)"
            print(f"{name} already connected.")
            return name

    def disconnect(self) -> None:
        try:
            self._mtp.disconnect()
        except pymtp.NotConnected:
            print("No MTP device present.")

    def get_info(self) -> DeviceInfo:
        return DeviceInfo(
            name=_decode(self._mtp.get_devicename()),
            serial=_decode(self._mtp.get_serialnumber()),
            manufacturer=_decode(self._mtp.get_manufacturer()),
            battery=self._mtp.get_batterylevel(),
            model=_decode(self._mtp.get_modelname()),
            version=_decode(self._mtp.get_deviceversion()),
            free=self._mtp.get_freespace(),
            total=self._mtp.get_totalspace(),
            used=self._mtp.get_usedspace(),
            used_percent=self._mtp.get_usedspace_percent(),
        )

    def set_device_name(self, name: str) -> None:
        self._mtp.set_devicename(name.encode("utf-8"))

    def create_folder(self, name: str, parent: int = 100) -> None:
        self._mtp.create_folder(name, parent=parent)

    def list_folders(self) -> list[FolderEntry]:
        folders = self._mtp.get_folder_list()
        result: list[FolderEntry] = []
        if not folders:
            return result
        for folder_id, folder in folders.items():
            name = _decode(folder.name)
            result.append(FolderEntry(folder_id=int(folder_id), name=name))
        return result

    def send_file(self, path: str, remote_name: str | None = None) -> None:
        fname = remote_name or "000_TEST_FILE.mp3"
        print(f"=====\n{path}\n=====")
        oid = self._mtp.send_file_from_file(path, _c_str(fname))
        print(oid)

    def get_tracklisting(self):
        return self._mtp.get_tracklisting()

    def get_file_metadata(self, object_id: int):
        return self._mtp.get_file_metadata(object_id)

    def send_track(self, path: str, meta: TrackMetadata) -> None:
        """Transport.send_track — push audio with metadata via libmtp."""
        mt = pymtp.LIBMTP_Track()
        mt.title = _c_str(meta.title)
        mt.artist = _c_str(meta.artist)
        mt.composer = _c_str(meta.composer)
        mt.genre = _c_str(meta.genre)
        mt.album = _c_str(meta.album)
        mt.date = _c_str(meta.date)
        mt.tracknumber = ctypes.c_ushort(meta.tracknumber_int())
        mt.duration = ctypes.c_uint32(round(meta.length_sec * 1000))
        if meta.sample_rate:
            mt.samplerate = ctypes.c_uint32(meta.sample_rate)
        if meta.channels:
            mt.nochannels = ctypes.c_ushort(meta.channels)
        if meta.bitrate:
            mt.bitrate = ctypes.c_uint32(meta.bitrate)
        mt.bitratetype = meta.bitrate_mode

        _, ext = os.path.splitext(path)
        ext = ext or ".mp3"
        fname = f"{meta.artist} - {meta.album} - {meta.tracknumber} - {meta.title}{ext}"
        trid = self._mtp.send_track_from_file(path, _c_str(fname), mt)
        print(trid)
