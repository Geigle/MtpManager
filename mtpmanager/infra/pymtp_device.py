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


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _keep_bytes(keep: list[bytes], value: str | None) -> bytes | None:
    """Encode UTF-8 and retain the buffer for the lifetime of a C call.

    Returns None for empty values so libmtp sees NULL (matches mtp-sendtr).
    """
    text = str(value or "").strip()
    if not text:
        return None
    buf = text.encode("utf-8")
    keep.append(buf)
    return buf


def _year_date_field(date: str) -> str:
    """libmtp/sendtr-style date: ``YYYY0101T0000.0`` when a year is known."""
    year = year_arg(date)
    if year and year.isdigit() and len(year) == 4:
        return f"{year}0101T0000.0"
    return year


def _collect_errorstack(mtp: pymtp.MTP) -> str:
    """Read libmtp error texts into a string for app logs (not only stderr)."""
    addr = None
    try:
        from mtpmanager.infra.pymtp_wrapper import _device_ptr

        addr = _device_ptr(getattr(mtp, "device", None))
    except Exception:
        if getattr(mtp, "device", None) is not None:
            try:
                addr = ctypes.cast(mtp.device, ctypes.c_void_p).value
            except Exception:
                addr = None
    if not addr:
        return ""
    try:
        stack = mtp.mtp.LIBMTP_Get_Errorstack(addr)
    except Exception:
        logger.debug("LIBMTP_Get_Errorstack failed", exc_info=True)
        return ""
    if not stack:
        return ""

    messages: list[str] = []
    try:
        current = stack
        for _ in range(64):
            if not current:
                break
            err = current.contents
            text = _decode(err.error_text).strip()
            if text:
                messages.append(text)
            nxt = err.next
            if not nxt:
                break
            current = nxt
    except Exception:
        logger.debug("Walking libmtp error stack failed", exc_info=True)

    try:
        clear = getattr(mtp.mtp, "LIBMTP_Clear_Errorstack", None)
        if clear is not None:
            clear(addr)
    except Exception:
        logger.debug("LIBMTP_Clear_Errorstack failed", exc_info=True)

    return "\n".join(messages)


class PymtpDevice:
    """DevicePort + Transport implementation backed by pymtp.MTP.

    Experimental send is pure libmtp/PyMTP. Failures raise TransportError and
    are not silently retried via mtp-sendtr — the UI should guide the user to
    Stable Mode when they choose that path.
    """

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

    def is_connected(self) -> bool:
        """True when a PyMTP/libmtp session appears open."""
        try:
            return getattr(self._mtp, "device", None) is not None
        except Exception:
            return False

    def session_alive(self) -> bool:
        """True if the open session still answers a lightweight query.

        After unplug, libmtp may leave a non-NULL device pointer so
        :meth:`is_connected` stays True. Call this to detect a dead session
        and force reconnect logic.
        """
        if not self.is_connected():
            return False
        try:
            # Any simple property read that hits the device; failures mean gone.
            _ = self._mtp.get_modelname()
            return True
        except Exception:
            logger.debug("MTP session probe failed (device likely removed)", exc_info=True)
            return False

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
        keep: list[bytes] = []
        fname = remote_name or "000_TEST_FILE.mp3"
        buf = _keep_bytes(keep, fname) or b"000_TEST_FILE.mp3"
        keep.append(buf)
        logger.debug("send_file path=%s remote=%s", path, fname)
        oid = self._mtp.send_file_from_file(path, buf)
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

        On failure raises TransportError (fatal). Does not fall back to CMD.
        """
        _, ext = os.path.splitext(path)
        ext = ext or ".mp3"
        remote = build_remote_path(
            meta,
            ext,
            music_folder_id=self.music_folder_id,
        )
        parent_id, basename = split_remote_path(remote)

        # Keep Python bytes alive for the full C call (ctypes c_char_p fields).
        keep: list[bytes] = []

        mt = pymtp.LIBMTP_Track()
        mt.parent_id = int(parent_id)
        mt.storage_id = int(self.storage_id)
        mt.title = _keep_bytes(keep, meta.title)
        mt.artist = _keep_bytes(keep, meta.artist)
        mt.composer = _keep_bytes(keep, meta.composer)
        mt.genre = _keep_bytes(keep, meta.genre)
        mt.album = _keep_bytes(keep, meta.album)
        mt.date = _keep_bytes(keep, _year_date_field(meta.date))
        mt.tracknumber = int(meta.tracknumber_int())
        mt.duration = int(round(float(meta.length_sec or 0) * 1000))
        if meta.sample_rate:
            mt.samplerate = int(meta.sample_rate)
        if meta.channels:
            mt.nochannels = int(meta.channels)
        if meta.bitrate:
            mt.bitrate = int(meta.bitrate)
        mt.bitratetype = int(meta.bitrate_mode or 0)

        # Refresh storage list (sendtr does this before applying storage_id).
        try:
            from mtpmanager.infra.pymtp_wrapper import _device_ptr

            addr = _device_ptr(self._mtp.device)
            if addr and hasattr(self._mtp.mtp, "LIBMTP_Get_Storage"):
                self._mtp.mtp.LIBMTP_Get_Storage(addr, 0)
        except Exception:
            logger.debug("LIBMTP_Get_Storage before send failed", exc_info=True)

        filetype = int(self._mtp.find_filetype(path))
        basename_b = _keep_bytes(keep, basename) or b"track.mp3"
        keep.append(basename_b)

        logger.debug(
            "send_track path=%s remote=%s parent=%s storage=0x%08x filetype=%s",
            path,
            basename,
            parent_id,
            self.storage_id,
            filetype,
        )
        try:
            trid = self._mtp.send_track_from_file(path, basename_b, mt)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP send failed: device not connected. "
                "Use Connect on the Experimental tab first, or switch to "
                "Stable Mode for mtp-sendtr transfers.",
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
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP send_track failed path=%s remote=%s parent=%s "
                "storage=0x%08x filetype=%s detail=%s\n%s",
                path,
                basename,
                parent_id,
                self.storage_id,
                filetype,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            msg = (
                f"PyMTP send failed ({detail}). "
                f"Remote={basename} parent={parent_id} "
                f"storage=0x{self.storage_id:08x} filetype={filetype}. "
                f"Path: {path}"
            )
            if stack_text:
                msg = f"{msg}\n{stack_text}"
            raise TransportError(
                msg,
                fatal=True,
                path=path,
                stderr=stack_text,
            ) from exc

        _ = keep  # lifetime through C call
        logger.debug("send_track object_id=%s path=%s", trid, path)
