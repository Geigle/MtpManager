"""pymtp/libmtp device adapter — no Tk, no messageboxes."""

from __future__ import annotations

import ctypes
import logging
import os

import mtpmanager.infra.pymtp_wrapper as pymtp
from mtpmanager.domain.models import DeviceInfo, FolderEntry, TrackMetadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    build_remote_path,
    split_remote_path,
    year_arg,
)
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        mtp: pymtp.MTP | None = None,
        *,
        storage_id: int = DEFAULT_STORAGE_ID,
        music_folder_id: int = DEFAULT_MUSIC_FOLDER_ID,
    ):
        self._mtp = mtp if mtp is not None else pymtp.MTP()
        self.storage_id = storage_id
        self.music_folder_id = music_folder_id

    @property
    def raw(self) -> pymtp.MTP:
        return self._mtp

    def connect(self) -> str:
        try:
            self._mtp.connect()
            name = _decode(self._mtp.get_devicename())
            logger.info("Connected to %s", name)
            return name
        except pymtp.AlreadyConnected:
            try:
                name = _decode(self._mtp.get_devicename())
            except Exception:
                name = "(unknown)"
            logger.info("%s already connected.", name)
            return name

    def disconnect(self) -> None:
        try:
            self._mtp.disconnect()
            logger.info("Disconnected MTP device.")
        except pymtp.NotConnected:
            logger.info("No MTP device present.")

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
        logger.debug("send_file path=%s remote=%s", path, fname)
        oid = self._mtp.send_file_from_file(path, _c_str(fname))
        logger.debug("send_file object_id=%s", oid)

    def get_tracklisting(self):
        return self._mtp.get_tracklisting()

    def get_file_metadata(self, object_id: int):
        return self._mtp.get_file_metadata(object_id)

    def send_track(self, path: str, meta: TrackMetadata) -> None:
        """Transport.send_track — push audio with metadata via libmtp.

        Uses the same ZEN remote contract as CmdTransport: Music folder parent,
        explicit storage id, and a short sanitized object basename. Tags keep
        full title/artist/album (including characters unsafe in filenames).
        """
        _, ext = os.path.splitext(path)
        ext = ext or ".mp3"
        remote = build_remote_path(
            meta,
            ext,
            music_folder_id=self.music_folder_id,
        )
        parent_id, basename = split_remote_path(remote)

        mt = pymtp.LIBMTP_Track()
        mt.parent_id = int(parent_id)
        mt.storage_id = int(self.storage_id)
        mt.title = _c_str(meta.title)
        mt.artist = _c_str(meta.artist)
        mt.composer = _c_str(meta.composer)
        mt.genre = _c_str(meta.genre)
        mt.album = _c_str(meta.album)
        mt.date = _c_str(year_arg(meta.date))
        mt.tracknumber = ctypes.c_ushort(meta.tracknumber_int())
        mt.duration = ctypes.c_uint32(round(meta.length_sec * 1000))
        if meta.sample_rate:
            mt.samplerate = ctypes.c_uint32(meta.sample_rate)
        if meta.channels:
            mt.nochannels = ctypes.c_ushort(meta.channels)
        if meta.bitrate:
            mt.bitrate = ctypes.c_uint32(meta.bitrate)
        mt.bitratetype = meta.bitrate_mode

        logger.debug(
            "send_track path=%s remote=%s parent=%s storage=0x%08x",
            path,
            basename,
            parent_id,
            self.storage_id,
        )
        try:
            trid = self._mtp.send_track_from_file(path, _c_str(basename), mt)
        except pymtp.NotConnected as exc:
            raise TransportError(
                f"PyMTP send failed: device not connected. Path: {path}",
                fatal=True,
                path=path,
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"PyMTP send failed: {exc}. Path: {path}",
                fatal=True,
                path=path,
            ) from exc
        except pymtp.CommandFailed as exc:
            # pymtp dumps the libmtp error stack to stderr only when its
            # internal __DEBUG__ is set; always attempt a dump and wrap with
            # a useful message for transfer.py / UI handling.
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP send_track failed path=%s remote=%s parent=%s "
                "storage=0x%08x detail=%s (libmtp stack may be on stderr)",
                path,
                basename,
                parent_id,
                self.storage_id,
                detail,
            )
            raise TransportError(
                f"PyMTP send failed ({detail}). "
                f"Remote={basename} parent={parent_id} "
                f"storage=0x{self.storage_id:08x}. Path: {path}",
                fatal=True,
                path=path,
            ) from exc

        logger.debug("send_track object_id=%s path=%s", trid, path)
