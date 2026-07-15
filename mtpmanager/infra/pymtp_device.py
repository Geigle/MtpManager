"""pymtp/libmtp device adapter — no Tk, no messageboxes."""

from __future__ import annotations

import ctypes
import logging
import os

import mtpmanager.infra.pymtp_wrapper as pymtp
from mtpmanager.domain.models import DeviceInfo, FolderEntry, TrackMetadata
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    build_remote_path,
    split_remote_path,
    year_arg,
)
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)

# After a dead PyMTP session (PTP 02ff), fall back to mtp-sendtr which opens a
# fresh session — same path that already works in stable mode.
_FALLBACK_MARKERS = (
    "02ff",
    "PTP I/O Error",
    "Could not send object",
    "Could not close session",
)


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


def _should_fallback_to_cmd(stderr: str, message: str) -> bool:
    blob = f"{stderr}\n{message}"
    return any(m in blob for m in _FALLBACK_MARKERS)


class PymtpDevice:
    """DevicePort + Transport implementation backed by pymtp.MTP.

    Track send uses libmtp via pymtp first. On fatal PTP I/O (common when the
    long-lived experimental session is poisoned), releases the session and
    retries once via CmdTransport (mtp-sendtr), then reconnects.
    """

    def __init__(
        self,
        mtp: pymtp.MTP | None = None,
        *,
        storage_id: int = DEFAULT_STORAGE_ID,
        music_folder_id: int = DEFAULT_MUSIC_FOLDER_ID,
        cmd_fallback: bool = True,
    ):
        self._mtp = mtp if mtp is not None else pymtp.MTP()
        self.storage_id = storage_id
        self.music_folder_id = music_folder_id
        self.cmd_fallback = cmd_fallback

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
        """
        try:
            self._send_track_pymtp(path, meta)
            return
        except TransportError as exc:
            if not self.cmd_fallback or not _should_fallback_to_cmd(
                exc.stderr or "", str(exc)
            ):
                raise
            logger.warning(
                "PyMTP send hit fatal PTP/session error; "
                "releasing session and retrying via mtp-sendtr. err=%s",
                exc,
            )

        self._send_track_cmd_fallback(path, meta)

    def _send_track_pymtp(self, path: str, meta: TrackMetadata) -> None:
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

    def _send_track_cmd_fallback(self, path: str, meta: TrackMetadata) -> None:
        """Release exclusive PyMTP session and send with proven mtp-sendtr."""
        was_connected = getattr(self._mtp, "device", None) is not None
        if was_connected:
            try:
                self.disconnect()
            except Exception:
                logger.debug("Disconnect before CMD fallback failed", exc_info=True)
                # Force-clear so reconnect can open a new session.
                try:
                    self._mtp.device = None
                except Exception:
                    pass

        try:
            CmdTransport(
                storage_id=self.storage_id,
                music_folder_id=self.music_folder_id,
            ).send_track(path, meta)
            logger.info("CMD fallback send succeeded path=%s", path)
        finally:
            if was_connected:
                try:
                    self.connect()
                except Exception:
                    logger.exception(
                        "Could not re-open PyMTP session after CMD fallback; "
                        "use Connect again before device tools."
                    )
