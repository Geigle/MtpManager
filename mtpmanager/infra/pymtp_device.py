"""pymtp/libmtp device adapter — no Tk, no messageboxes."""

from __future__ import annotations

import ctypes
import logging
import os
import time
from collections.abc import Callable

import mtpmanager.infra.pymtp_wrapper as pymtp
from mtpmanager.domain.device_media import apply_track_info, track_refs_from_files
from mtpmanager.domain.models import (
    DeviceInfo,
    DevicePlaylist,
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    FolderEntry,
    TrackMetadata,
)
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_PLAYLIST_FOLDER_ID,
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
        """True if the open session still answers a lightweight USB query.

        After unplug, libmtp may leave a non-NULL device pointer so
        :meth:`is_connected` stays True. Call this to detect a dead session
        and force reconnect logic.

        **Must not** use ``get_modelname`` / ``get_manufacturer`` /
        ``get_serialnumber``: libmtp returns those from cached deviceinfo with
        **no USB traffic**, so they stay "alive" after physical unplug and
        block auto-reconnect forever.

        Uses ``LIBMTP_Get_Storage`` (real PTP GetStorageIDs). Battery is
        avoided — historically flaky on recovering ZENs after long jobs.
        """
        if not self.is_connected():
            return False
        try:
            from mtpmanager.infra.pymtp_wrapper import _device_ptr

            mtp = self._mtp
            lib = getattr(mtp, "mtp", None)
            addr = _device_ptr(getattr(mtp, "device", None))
            get_storage = getattr(lib, "LIBMTP_Get_Storage", None) if lib else None
            if addr and get_storage is not None:
                # 0 = success; -1 (or other non-zero) = device gone / bus error.
                ret = int(get_storage(addr, 0))
                if ret != 0:
                    logger.debug(
                        "MTP session probe Get_Storage failed (ret=%s)",
                        ret,
                    )
                    return False
                return True

            # Fallback when bindings lack Get_Storage: friendly name is a live
            # PTP device property (unlike model/manufacturer strings).
            _ = mtp.get_devicename()
            return True
        except Exception:
            logger.debug(
                "MTP session probe failed (device likely removed)",
                exc_info=True,
            )
            return False

    def connect(self) -> str:
        """Open MTP session only — no battery/storage probes."""
        try:
            self._mtp.connect()
            name = self._safe_device_str("get_devicename", "")
            logger.info("Connected to %s", name or "(unnamed)")
            return name
        except pymtp.AlreadyConnected:
            name = self._safe_device_str("get_devicename", "(unknown)")
            logger.info("%s already connected.", name)
            return name

    def disconnect(self) -> None:
        """Close the session and clear the device pointer.

        After a physical unplug, ``LIBMTP_Release_Device`` may error; still
        clear ``device`` so :meth:`is_connected` is False and auto-connect can
        open a fresh session on replug.
        """
        try:
            self._mtp.disconnect()
            logger.info("Disconnected MTP device.")
        except pymtp.NotConnected:
            logger.info("No MTP device present.")
        except Exception:
            logger.exception(
                "Disconnect raised; clearing session pointer for reconnect"
            )
            try:
                self._mtp.device = None
            except Exception:
                pass
        # Ensure reconnect path is not blocked by a stale non-NULL pointer.
        if getattr(self._mtp, "device", None) is not None:
            try:
                self._mtp.device = None
            except Exception:
                pass

    def _safe_device_str(self, method: str, default: str = "") -> str:
        """Call a pymtp string getter; return *default* on any failure."""
        try:
            fn = getattr(self._mtp, method)
            return _decode(fn())
        except Exception:
            logger.debug("Device %s failed (using default)", method, exc_info=True)
            return default

    def _safe_device_call(self, method: str, default):
        """Call a pymtp method; return *default* on any failure."""
        try:
            fn = getattr(self._mtp, method)
            return fn()
        except Exception:
            logger.debug("Device %s failed (using default)", method, exc_info=True)
            return default

    def get_identity(self) -> DeviceInfo:
        """Minimal identity for connect / auto-connect / profile matching.

        Friendly name, manufacturer, model, and serial number — no battery or
        storage walks (those freeze recovering ZENs after long transfer
        sessions). Serial is required so multi-device inventories do not
        collapse into one ``default`` key.
        """
        return DeviceInfo(
            name=self._safe_device_str("get_devicename"),
            serial=self._safe_device_str("get_serialnumber"),
            manufacturer=self._safe_device_str("get_manufacturer"),
            model=self._safe_device_str("get_modelname"),
        )

    def get_info(self) -> DeviceInfo:
        """Full device diagnostics (Device → Device Info).

        Each optional field is fetched independently and soft-fails to a
        default so a single bad API (e.g. ``get_batterylevel``) never aborts
        the whole dialog or poison-connect recovery.
        """
        identity = self.get_identity()
        battery = self._safe_device_call("get_batterylevel", None)
        free = self._safe_device_call("get_freespace", 0) or 0
        total = self._safe_device_call("get_totalspace", 0) or 0
        used = self._safe_device_call("get_usedspace", 0) or 0
        used_pct = self._safe_device_call("get_usedspace_percent", 0.0)
        try:
            used_pct = float(used_pct or 0.0)
        except (TypeError, ValueError):
            used_pct = 0.0
        return DeviceInfo(
            name=identity.name,
            serial=self._safe_device_str("get_serialnumber"),
            manufacturer=identity.manufacturer,
            battery=battery,
            model=identity.model,
            version=self._safe_device_str("get_deviceversion"),
            free=int(free) if free is not None else 0,
            total=int(total) if total is not None else 0,
            used=int(used) if used is not None else 0,
            used_percent=used_pct,
        )

    def set_device_name(self, name: str) -> None:
        # Wrapper encodes UTF-8 + correct c_char_p argtypes.
        self._mtp.set_devicename(name)

    def create_folder(
        self, name: str, parent: int = DEFAULT_MUSIC_FOLDER_ID
    ) -> int:
        # Parent defaults to Music (100). Storage must match the ZEN media id
        # (same contract as track send); 0 often works for create but is wrong
        # for this device class. Returns the new folder object id.
        new_id = self._mtp.create_folder(
            name, parent=int(parent), storage=int(self.storage_id)
        )
        return int(new_id)

    def list_folders(self) -> list[FolderEntry]:
        folders = self._mtp.get_folder_list()
        result: list[FolderEntry] = []
        if not folders:
            return result
        for folder_id, folder in folders.items():
            name = _decode(folder.name)
            parent_id = int(getattr(folder, "parent_id", 0) or 0)
            result.append(
                FolderEntry(
                    folder_id=int(folder_id),
                    name=name,
                    parent_id=parent_id,
                )
            )
        return result

    def list_files(self) -> list[FileEntry]:
        """Experimental: full MTP file listing via patched get_filelisting."""
        logger.info("list_files (get_filelisting)")
        t0 = time.monotonic()
        try:
            raw = self._mtp.get_filelisting()
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP file listing failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_filelisting failed detail=%s\n%s",
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP file listing failed ({detail}).",
                fatal=True,
            ) from exc

        result: list[FileEntry] = []
        if not raw:
            elapsed = time.monotonic() - t0
            logger.info("list_files ok count=0 elapsed=%.1fs", elapsed)
            return result
        for node in raw:
            name = _decode(getattr(node, "filename", None))
            result.append(
                FileEntry(
                    item_id=int(getattr(node, "item_id", 0) or 0),
                    name=name,
                    parent_id=int(getattr(node, "parent_id", 0) or 0),
                    storage_id=int(getattr(node, "storage_id", 0) or 0),
                    filesize=int(getattr(node, "filesize", 0) or 0),
                    filetype=int(getattr(node, "filetype", 0) or 0),
                    modificationdate=int(
                        getattr(node, "modificationdate", 0) or 0
                    ),
                )
            )
        # Stable order for UI / logs
        result.sort(key=lambda e: (e.parent_id, e.name.casefold(), e.item_id))
        elapsed = time.monotonic() - t0
        logger.info("list_files ok count=%s elapsed=%.1fs", len(result), elapsed)
        if elapsed >= 15.0:
            logger.warning(
                "list_files was slow (%.1fs). libmtp may print "
                "'LIBMTP panic: unable to read in zero packet' to stderr "
                "during long USB walks; that noise can be non-fatal.",
                elapsed,
            )
        return result

    def delete_object(self, object_id: int) -> None:
        """Experimental: delete one object via patched delete_object."""
        oid = int(object_id)
        if oid <= 0:
            raise ValueError(f"Invalid object id: {object_id}")
        logger.info("delete_object id=%s", oid)
        try:
            self._mtp.delete_object(oid)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP delete failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP delete_object failed id=%s detail=%s\n%s",
                oid,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP delete failed ({detail}) for object id={oid}. "
                "Session may be poisoned — disconnect/replug, or use "
                "Config → Stable Mode for transfers.",
                fatal=True,
            ) from exc
        logger.info("delete_object ok id=%s", oid)

    def _playlist_from_snapshot(self, node) -> DevicePlaylist | None:
        """Map a wrapper playlist snapshot to :class:`DevicePlaylist`."""
        try:
            pid = int(getattr(node, "playlist_id", 0) or 0)
            if pid <= 0:
                return None
            tracks = tuple(
                int(x)
                for x in (getattr(node, "tracks", ()) or ())
                if int(x) > 0
            )
            return DevicePlaylist(
                playlist_id=pid,
                name=_decode(getattr(node, "name", "")),
                parent_id=int(getattr(node, "parent_id", 0) or 0),
                storage_id=int(getattr(node, "storage_id", 0) or 0),
                track_ids=tracks,
            )
        except Exception:
            logger.debug("skip bad playlist node", exc_info=True)
            return None

    def list_playlists(self) -> list[DevicePlaylist]:
        """Experimental: list on-device playlists (patched get_playlists).

        Note: on Creative ZEN this is often incomplete — prefer
        :meth:`list_playlists_complete` with device_index candidates.
        """
        logger.info("list_playlists")
        try:
            raw = self._mtp.get_playlists()
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP list playlists failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except Exception as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                pass
            raise TransportError(
                f"PyMTP list playlists failed: {exc}",
                fatal=True,
            ) from exc
        out: list[DevicePlaylist] = []
        for node in raw or []:
            pl = self._playlist_from_snapshot(node)
            if pl is not None:
                out.append(pl)
        logger.info("list_playlists count=%d", len(out))
        return out

    def get_playlist(self, playlist_id: int) -> DevicePlaylist:
        """Experimental: one playlist by object id (patched get_playlist)."""
        oid = int(playlist_id)
        if oid <= 0:
            raise ValueError(f"Invalid playlist id: {playlist_id}")
        logger.info("get_playlist id=%s", oid)
        try:
            raw = self._mtp.get_playlist(oid)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP get playlist failed: device not connected.",
                fatal=True,
            ) from exc
        except pymtp.ObjectNotFound as exc:
            raise TransportError(
                f"PyMTP get playlist: object {oid} not found.",
                fatal=False,
            ) from exc
        except Exception as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                pass
            raise TransportError(
                f"PyMTP get playlist failed id={oid}: {exc}",
                fatal=True,
            ) from exc
        pl = self._playlist_from_snapshot(raw)
        if pl is None:
            raise TransportError(
                f"PyMTP get playlist returned empty snapshot for id={oid}",
                fatal=False,
            )
        return pl

    def list_playlists_complete(
        self,
        *,
        candidate_ids: list[int] | tuple[int, ...] | None = None,
        candidate_names: dict[int, str] | None = None,
    ) -> list[DevicePlaylist]:
        """List playlists, hydrating *candidate_ids* via Get_Playlist.

        Use this for ZEN: pass object ids discovered from the device file
        index (``*.zpl`` under My Playlists). ``Get_Playlist_List`` alone
        frequently returns only a subset.
        """
        by_id: dict[int, DevicePlaylist] = {}
        try:
            for pl in self.list_playlists():
                by_id[int(pl.playlist_id)] = pl
        except TransportError:
            raise
        except Exception as exc:
            logger.warning("list_playlists base failed: %s", exc, exc_info=True)

        names = candidate_names or {}
        for raw_id in candidate_ids or ():
            oid = int(raw_id or 0)
            if oid <= 0 or oid in by_id:
                continue
            try:
                pl = self.get_playlist(oid)
                by_id[oid] = pl
            except TransportError as exc:
                # Still surface the playlist shell so the UI lists it.
                logger.info(
                    "get_playlist id=%s failed (%s); using filename shell",
                    oid,
                    exc,
                )
                shell_name = (names.get(oid) or "").strip() or f"Playlist {oid}"
                by_id[oid] = DevicePlaylist(
                    playlist_id=oid,
                    name=shell_name,
                    track_ids=(),
                )
            except Exception:
                logger.warning(
                    "get_playlist id=%s unexpected failure", oid, exc_info=True
                )
                shell_name = (names.get(oid) or "").strip() or f"Playlist {oid}"
                by_id[oid] = DevicePlaylist(
                    playlist_id=oid,
                    name=shell_name,
                    track_ids=(),
                )

        out = sorted(
            by_id.values(),
            key=lambda p: ((p.name or "").casefold(), int(p.playlist_id)),
        )
        logger.info(
            "list_playlists_complete count=%d (candidates=%d)",
            len(out),
            len(list(candidate_ids or ())),
        )
        return out

    def create_playlist(
        self,
        name: str,
        track_ids: list[int] | tuple[int, ...],
        *,
        parent_id: int = DEFAULT_PLAYLIST_FOLDER_ID,
        storage_id: int | None = None,
    ) -> int:
        """Experimental: create MTP playlist; returns new playlist object id."""
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Playlist name must be non-empty")
        ids = [int(x) for x in track_ids if int(x) > 0]
        storage = int(self.storage_id if storage_id is None else storage_id)
        parent = int(parent_id or DEFAULT_PLAYLIST_FOLDER_ID)
        logger.info(
            "create_playlist name=%r tracks=%d parent=%s storage=%s",
            clean,
            len(ids),
            parent,
            storage,
        )
        try:
            new_id = self._mtp.create_new_playlist(
                clean,
                track_ids=ids,
                parent_id=parent,
                storage_id=storage,
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP create playlist failed: device not connected.",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                pass
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP create_playlist failed name=%r detail=%s\n%s",
                clean,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP create playlist failed ({detail}) for {clean!r}.",
                fatal=True,
            ) from exc
        except Exception as exc:
            raise TransportError(
                f"PyMTP create playlist failed: {exc}",
                fatal=True,
            ) from exc
        return int(new_id)

    def update_playlist(
        self,
        playlist_id: int,
        name: str,
        track_ids: list[int] | tuple[int, ...],
        *,
        parent_id: int = DEFAULT_PLAYLIST_FOLDER_ID,
        storage_id: int | None = None,
    ) -> int:
        """Experimental: replace track list on an existing MTP playlist."""
        oid = int(playlist_id)
        if oid <= 0:
            raise ValueError(f"Invalid playlist id: {playlist_id}")
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Playlist name must be non-empty")
        ids = [int(x) for x in track_ids if int(x) > 0]
        storage = int(self.storage_id if storage_id is None else storage_id)
        parent = int(parent_id or DEFAULT_PLAYLIST_FOLDER_ID)
        logger.info(
            "update_playlist id=%s name=%r tracks=%d",
            oid,
            clean,
            len(ids),
        )
        try:
            self._mtp.update_playlist(
                oid,
                clean,
                track_ids=ids,
                parent_id=parent,
                storage_id=storage,
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP update playlist failed: device not connected.",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                pass
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP update_playlist failed id=%s detail=%s\n%s",
                oid,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP update playlist failed ({detail}) for id={oid}.",
                fatal=True,
            ) from exc
        except Exception as exc:
            raise TransportError(
                f"PyMTP update playlist failed: {exc}",
                fatal=True,
            ) from exc
        return oid

    def get_file_metadata(self, object_id: int) -> FileEntry:
        """Experimental: one-object metadata via patched get_file_metadata."""
        oid = int(object_id)
        if oid <= 0:
            raise ValueError(f"Invalid object id: {object_id}")
        logger.info("get_file_metadata id=%s", oid)
        try:
            node = self._mtp.get_file_metadata(oid)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP get file info failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except pymtp.ObjectNotFound as exc:
            # Wrapper already dumped errorstack; collect text for logs.
            stack_text = _collect_errorstack(self._mtp)
            logger.warning(
                "get_file_metadata ObjectNotFound id=%s "
                "(often ZEN proplist/Get_Filemetadata fragility, not a missing "
                "handle — prefer listing snapshot for File Info)\n%s",
                oid,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"Live Get_Filemetadata failed for object id={oid} "
                "(device returned no metadata; object may still exist — "
                "use listing fields).",
                fatal=False,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_file_metadata failed id=%s detail=%s\n%s",
                oid,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP get file info failed ({detail}) for object id={oid}.",
                fatal=True,
            ) from exc

        entry = FileEntry(
            item_id=int(getattr(node, "item_id", 0) or 0),
            name=_decode(getattr(node, "filename", None)),
            parent_id=int(getattr(node, "parent_id", 0) or 0),
            storage_id=int(getattr(node, "storage_id", 0) or 0),
            filesize=int(getattr(node, "filesize", 0) or 0),
            filetype=int(getattr(node, "filetype", 0) or 0),
            modificationdate=int(getattr(node, "modificationdate", 0) or 0),
        )
        logger.debug(
            "get_file_metadata ok id=%s name=%r parent=%s type=%s size=%s",
            entry.item_id,
            entry.name,
            entry.parent_id,
            entry.filetype,
            entry.filesize,
        )
        return entry

    # This call is risky! Rapidly calling this on many objects can trigger a
    # LIBMTP Panic and poison the session, which will require a 
    # disconnect/replug to recover. Use with care.
    # This function may need work to avoid LIBMTP panics.
    def get_track_metadata(self, object_id: int) -> DeviceTrackInfo:
        """Experimental: on-device track tags via patched get_track_metadata."""
        oid = int(object_id)
        if oid <= 0:
            raise ValueError(f"Invalid object id: {object_id}")
        logger.info("get_track_metadata id=%s", oid)
        try:
            node = self._mtp.get_track_metadata(oid)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP get track info failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except pymtp.ObjectNotFound as exc:
            stack_text = _collect_errorstack(self._mtp)
            logger.warning(
                "get_track_metadata ObjectNotFound id=%s "
                "(not a track, missing handle, or property path failed)\n%s",
                oid,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"No track metadata for object id={oid}. "
                "Object may not be a music/video track, or the device "
                "returned no track properties.",
                fatal=False,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_track_metadata failed id=%s detail=%s\n%s",
                oid,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP get track info failed ({detail}) for object id={oid}.",
                fatal=True,
            ) from exc

        info = DeviceTrackInfo(
            item_id=int(getattr(node, "item_id", 0) or 0),
            name=_decode(getattr(node, "filename", None)),
            parent_id=int(getattr(node, "parent_id", 0) or 0),
            storage_id=int(getattr(node, "storage_id", 0) or 0),
            filesize=int(getattr(node, "filesize", 0) or 0),
            filetype=int(getattr(node, "filetype", 0) or 0),
            modificationdate=int(getattr(node, "modificationdate", 0) or 0),
            title=_decode(getattr(node, "title", None)),
            artist=_decode(getattr(node, "artist", None)),
            album=_decode(getattr(node, "album", None)),
            genre=_decode(getattr(node, "genre", None)),
            composer=_decode(getattr(node, "composer", None)),
            date=_decode(getattr(node, "date", None)),
            tracknumber=int(getattr(node, "tracknumber", 0) or 0),
            duration_ms=int(getattr(node, "duration", 0) or 0),
            sample_rate=int(getattr(node, "samplerate", 0) or 0),
            channels=int(getattr(node, "nochannels", 0) or 0),
            bitrate=int(getattr(node, "bitrate", 0) or 0),
            bitrate_type=int(getattr(node, "bitratetype", 0) or 0),
            rating=int(getattr(node, "rating", 0) or 0),
            usecount=int(getattr(node, "usecount", 0) or 0),
        )
        logger.debug(
            "get_track_metadata ok id=%s name=%r title=%r artist=%r album=%r",
            info.item_id,
            info.name,
            info.title,
            info.artist,
            info.album,
        )
        return info

    def get_file_to_file(
        self,
        object_id: int,
        dest_path: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Download any object by id to *dest_path* (experimental)."""
        oid = int(object_id)
        if oid <= 0:
            raise ValueError(f"Invalid object id: {object_id}")
        dest = str(dest_path or "").strip()
        if not dest:
            raise ValueError("Destination path required")
        logger.info("get_file_to_file id=%s dest=%s", oid, dest)

        def _cb(sent: int, total: int) -> None:
            if on_progress is None:
                return
            try:
                on_progress(int(sent), int(total))
            except Exception:
                logger.debug("get_file_to_file progress failed", exc_info=True)

        try:
            self._mtp.get_file_to_file(
                oid, dest, callback=_cb if on_progress else None
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP download failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
                path=dest,
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"PyMTP download failed: {exc}. Dest: {dest}",
                fatal=True,
                path=dest,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_file_to_file failed id=%s dest=%s detail=%s\n%s",
                oid,
                dest,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            msg = (
                f"PyMTP download failed ({detail}) for object id={oid}. "
                f"Dest: {dest}"
            )
            if stack_text:
                msg = f"{msg}\n{stack_text}"
            raise TransportError(msg, fatal=True, path=dest) from exc
        logger.info("get_file_to_file ok id=%s dest=%s", oid, dest)

    def get_track_to_file(
        self,
        object_id: int,
        dest_path: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Download a track-typed object by id (experimental). Prefer file path for mixed media."""
        oid = int(object_id)
        if oid <= 0:
            raise ValueError(f"Invalid object id: {object_id}")
        dest = str(dest_path or "").strip()
        if not dest:
            raise ValueError("Destination path required")
        logger.info("get_track_to_file id=%s dest=%s", oid, dest)

        def _cb(sent: int, total: int) -> None:
            if on_progress is None:
                return
            try:
                on_progress(int(sent), int(total))
            except Exception:
                logger.debug("get_track_to_file progress failed", exc_info=True)

        try:
            self._mtp.get_track_to_file(
                oid, dest, callback=_cb if on_progress else None
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP track download failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
                path=dest,
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"PyMTP track download failed: {exc}. Dest: {dest}",
                fatal=True,
                path=dest,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_track_to_file failed id=%s dest=%s detail=%s\n%s",
                oid,
                dest,
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            # Fall back to generic file download (works for video/non-track too).
            logger.info(
                "get_track_to_file failed (%s); retrying get_file_to_file id=%s",
                detail,
                oid,
            )
            self.get_file_to_file(oid, dest, on_progress=on_progress)
            return
        logger.info("get_track_to_file ok id=%s dest=%s", oid, dest)

    def send_file(self, path: str, remote_name: str | None = None) -> None:
        keep: list[bytes] = []
        fname = remote_name or "000_TEST_FILE.mp3"
        buf = _keep_bytes(keep, fname) or b"000_TEST_FILE.mp3"
        keep.append(buf)
        logger.debug("send_file path=%s remote=%s", path, fname)
        oid = self._mtp.send_file_from_file(path, buf)
        logger.debug("send_file object_id=%s", oid)

    def list_tracks(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
        *,
        load_tags: bool = True,
        stop_on_fatal: bool = True,
    ) -> list[DeviceTrackRef]:
        """Experimental track list — **mtp-tracks algorithm**.

        1. ``get_filelisting`` (complete, fast on ZEN)
        2. Media filter (audio/video-ish)
        3. Per-id ``get_track_metadata`` for tags (same as CLI ``mtp-tracks``
           which uses Get_Files_And_Folders + Get_Trackmetadata — **not**
           bulk Get_Tracklisting, which is incomplete on this device)

        *load_tags*: when False, return id/filename rows only (fast filter).
        *stop_on_fatal*: abort remaining tag fetches on fatal TransportError.
        Never touch Tk here.
        """
        if on_progress is not None:
            try:
                on_progress(0, 0, "listing device files…")
            except Exception:
                logger.debug("list_tracks progress callback failed", exc_info=True)

        logger.info(
            "list_tracks (mtp-tracks style: filelisting + Get_Trackmetadata)"
        )
        t0 = time.monotonic()
        files = self.list_files()
        refs = track_refs_from_files(files)
        total = len(refs)
        logger.info(
            "list_tracks candidates=%s of %s files (tags=%s)",
            total,
            len(files),
            load_tags,
        )

        if not load_tags or total == 0:
            if on_progress is not None:
                try:
                    on_progress(
                        total,
                        total or 1,
                        f"found {total} track(s)",
                    )
                except Exception:
                    logger.debug(
                        "list_tracks progress callback failed", exc_info=True
                    )
            return refs

        out: list[DeviceTrackRef] = []
        updated = 0
        failed = 0
        for i, ref in enumerate(refs):
            oid = int(ref.item_id or 0)
            label = (ref.name or "").strip() or f"id={oid}"
            if on_progress is not None:
                try:
                    on_progress(
                        i,
                        total,
                        f"track tags {i + 1}/{total}  {label}",
                    )
                except Exception:
                    logger.debug(
                        "list_tracks progress callback failed", exc_info=True
                    )
            if oid <= 0:
                out.append(ref)
                failed += 1
                continue
            try:
                info = self.get_track_metadata(oid)
            except TransportError as exc:
                logger.warning(
                    "list_tracks tag miss id=%s fatal=%s: %s",
                    oid,
                    exc.fatal,
                    exc,
                )
                out.append(ref)
                failed += 1
                if stop_on_fatal and exc.fatal:
                    # Keep remaining as filename-only.
                    out.extend(refs[i + 1 :])
                    logger.error(
                        "list_tracks aborted after fatal tag error id=%s "
                        "got=%s remaining=%s",
                        oid,
                        len(out),
                        total - len(out),
                    )
                    break
                continue
            except Exception:
                logger.exception("list_tracks unexpected tag error id=%s", oid)
                out.append(ref)
                failed += 1
                continue
            out.append(apply_track_info(ref, info))
            updated += 1

        out.sort(
            key=lambda e: (
                (e.artist or "").casefold(),
                (e.title or "").casefold(),
                (e.name or "").casefold(),
                e.item_id,
            )
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "list_tracks ok count=%s tagged=%s failed=%s elapsed=%.1fs",
            len(out),
            updated,
            failed,
            elapsed,
        )
        if elapsed >= 15.0:
            logger.warning(
                "list_tracks was slow (%.1fs). Auto-connect probes are paused "
                "briefly afterward so a recovering session is not torn down.",
                elapsed,
            )
        if on_progress is not None:
            try:
                on_progress(
                    len(out),
                    len(out) or 1,
                    f"found {len(out)} track(s) ({updated} tagged)",
                )
            except Exception:
                logger.debug("list_tracks progress callback failed", exc_info=True)
        return out

    def list_tracks_from_files(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> list[DeviceTrackRef]:
        """Fast track-ish listing: filelisting + media filter (ids/filenames only)."""
        return self.list_tracks(on_progress=on_progress, load_tags=False)

    def list_tracks_via_tracklisting(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> list[DeviceTrackRef]:
        """Diagnostic: bulk ``LIBMTP_Get_Tracklisting*`` (often incomplete on ZEN)."""
        if on_progress is not None:
            try:
                on_progress(0, 0, "listing tracks (Get_Tracklisting)…")
            except Exception:
                logger.debug(
                    "list_tracks_via_tracklisting progress failed",
                    exc_info=True,
                )

        def _cb(sent: int, total: int) -> None:
            if on_progress is None:
                return
            try:
                on_progress(
                    int(sent),
                    max(int(total), int(sent), 1),
                    f"track listing {int(sent)}/{int(total) or '?'}…",
                )
            except Exception:
                logger.debug(
                    "list_tracks_via_tracklisting progress failed",
                    exc_info=True,
                )

        logger.info(
            "list_tracks_via_tracklisting (Get_Tracklisting* — diagnostic)"
        )
        t0 = time.monotonic()
        try:
            raw = self._mtp.get_tracklisting(callback=_cb)
        except pymtp.NotConnected as exc:
            raise TransportError(
                "PyMTP track listing failed: device not connected. "
                "Use Device → Connect first.",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            try:
                self._mtp.debug_stack()
            except Exception:
                logger.debug("Could not dump libmtp error stack", exc_info=True)
            stack_text = _collect_errorstack(self._mtp)
            detail = str(exc).strip() or "CommandFailed"
            logger.error(
                "PyMTP get_tracklisting failed detail=%s\n%s",
                detail,
                stack_text or "(no libmtp errorstack text)",
            )
            raise TransportError(
                f"PyMTP track listing failed ({detail}).",
                fatal=True,
            ) from exc

        result: list[DeviceTrackRef] = []
        for snap in raw or []:
            oid = int(getattr(snap, "item_id", 0) or 0)
            if oid <= 0:
                continue
            result.append(
                DeviceTrackRef(
                    item_id=oid,
                    name=str(getattr(snap, "filename", "") or ""),
                    title=str(getattr(snap, "title", "") or ""),
                    artist=str(getattr(snap, "artist", "") or ""),
                    album=str(getattr(snap, "album", "") or ""),
                    date=str(getattr(snap, "date", "") or ""),
                    tracknumber=str(getattr(snap, "tracknumber", "") or ""),
                    parent_id=int(getattr(snap, "parent_id", 0) or 0),
                    storage_id=int(getattr(snap, "storage_id", 0) or 0),
                    filetype=int(getattr(snap, "filetype", 0) or 0),
                )
            )
        result.sort(
            key=lambda e: (
                (e.artist or "").casefold(),
                (e.title or "").casefold(),
                (e.name or "").casefold(),
                e.item_id,
            )
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "list_tracks_via_tracklisting ok count=%s elapsed=%.1fs",
            len(result),
            elapsed,
        )
        if on_progress is not None:
            try:
                on_progress(
                    len(result),
                    len(result) or 1,
                    f"found {len(result)} track(s)",
                )
            except Exception:
                logger.debug(
                    "list_tracks_via_tracklisting progress failed",
                    exc_info=True,
                )
        return result

    def send_track(
        self,
        path: str,
        meta: TrackMetadata,
        *,
        parent_id: int | None = None,
        guid: str | None = None,
        preferred_basename: str | None = None,
    ) -> int | None:
        """Transport.send_track — push audio with metadata via libmtp.

        Uses the same ZEN remote contract as CmdTransport: numeric folder parent
        (Music by default; explicit *parent_id* for ZENcast/podcasts/video),
        explicit storage id, and a short object basename (GUID when provided;
        else preferred_basename for retail restore). Tags keep full
        title/artist/album.

        On failure raises TransportError (fatal). Does not fall back to CMD.
        Returns the new object id when libmtp reports one.
        """
        _, ext = os.path.splitext(path)
        ext = ext or ".mp3"
        # Explicit parent wins (podcast ZENcast, video, etc.). GUID only
        # controls ObjectFileName, not the parent folder — music stays flat
        # under Music when parent_id is omitted.
        if parent_id is not None and int(parent_id) > 0:
            folder_id = int(parent_id)
        else:
            folder_id = int(self.music_folder_id)
        remote = build_remote_path(
            meta,
            ext,
            music_folder_id=folder_id,
            guid=guid,
            preferred_basename=preferred_basename,
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
        mt.tracknumber = int(meta.tracknumber_for_mtp())
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
                "Use Device → Connect first, or enable Config → Stable Mode "
                "for mtp-sendtr transfers.",
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
        try:
            return int(trid) if trid is not None else None
        except (TypeError, ValueError):
            return None

    def get_representative_sample_format(self, filetype: int) -> dict | None:
        """Probe sample (album art) support for a libmtp filetype enum value."""
        try:
            return self._mtp.get_representative_sample_format(int(filetype))
        except pymtp.NotConnected as exc:
            raise TransportError(
                "Not connected (sample format probe).",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            stack = _collect_errorstack(self._mtp)
            raise TransportError(
                f"Sample format probe failed for filetype={filetype}. {stack}".strip(),
                fatal=False,
                stderr=stack,
            ) from exc

    def send_representative_sample(
        self,
        object_id: int,
        data: bytes,
        *,
        width: int,
        height: int,
        filetype: int | None = None,
    ) -> None:
        """Attach JPEG (etc.) representative sample to a track or album object."""
        try:
            self._mtp.send_representative_sample(
                int(object_id),
                data,
                width=int(width),
                height=int(height),
                filetype=filetype,
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "Not connected (send sample).",
                fatal=True,
            ) from exc
        except ValueError as exc:
            raise TransportError(str(exc), fatal=False) from exc
        except pymtp.CommandFailed as exc:
            stack = _collect_errorstack(self._mtp)
            msg = (
                f"Send representative sample failed object_id={object_id} "
                f"bytes={len(data or b'')}. {stack}"
            ).strip()
            raise TransportError(msg, fatal=False, stderr=stack) from exc

    def get_representative_sample(self, object_id: int) -> dict | None:
        """Read back sample info for *object_id* (may be None if unsupported)."""
        try:
            return self._mtp.get_representative_sample(int(object_id))
        except pymtp.NotConnected as exc:
            raise TransportError(
                "Not connected (get sample).",
                fatal=True,
            ) from exc
        except ValueError as exc:
            raise TransportError(str(exc), fatal=False) from exc

    def create_album(
        self,
        name: str,
        track_ids: list[int] | None = None,
        *,
        artist: str = "",
        genre: str = "",
    ) -> int:
        """Create a device album abstract object; return album object id."""
        try:
            return int(
                self._mtp.create_new_album(
                    name,
                    track_ids=track_ids or [],
                    artist=artist,
                    genre=genre,
                    parent_id=0,
                    storage_id=int(self.storage_id),
                )
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "Not connected (create album).",
                fatal=True,
            ) from exc
        except pymtp.CommandFailed as exc:
            stack = _collect_errorstack(self._mtp)
            raise TransportError(
                f"Create album failed name={name!r}. {stack}".strip(),
                fatal=False,
                stderr=stack,
            ) from exc

    def update_album(
        self,
        album_id: int,
        name: str,
        track_ids: list[int] | None = None,
        *,
        artist: str = "",
        genre: str = "",
    ) -> int:
        """Update an existing device album's metadata and track list."""
        try:
            return int(
                self._mtp.update_album(
                    int(album_id),
                    name,
                    track_ids=track_ids or [],
                    artist=artist,
                    genre=genre,
                    parent_id=0,
                    storage_id=int(self.storage_id),
                )
            )
        except pymtp.NotConnected as exc:
            raise TransportError(
                "Not connected (update album).",
                fatal=True,
            ) from exc
        except ValueError as exc:
            raise TransportError(str(exc), fatal=False) from exc
        except pymtp.CommandFailed as exc:
            stack = _collect_errorstack(self._mtp)
            raise TransportError(
                f"Update album failed id={album_id} name={name!r}. {stack}".strip(),
                fatal=False,
                stderr=stack,
            ) from exc
