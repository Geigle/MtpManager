"""
Load pymtp with platform-specific libmtp discovery fixes.

On Linux, ctypes.util.find_library("mtp") resolves via ldconfig as usual.
On macOS, find_library often returns None; patch it before pymtp loads libmtp.

Also patches known pymtp/libmtp binding bugs that break track send on modern
libmtp (1.1.x) and Apple Silicon / Python 3:
  * LIBMTP_Filetype enum off-by-one (missing FOLDER=0)
  * Missing argtypes for LIBMTP_Send_Track_From_File (bad calls on arm64)
  * Dump_Errorstack called without a device pointer (NULL-device PANIC)
  * get_folder_list / get_parent_folders use dict.has_key (Python 2 only)
  * create_folder / set_devicename pass Python str without c_char_p argtypes
    (arm64/Py3: often only the first character is stored on the device)
  * get_filelisting linked-list walk (NULL-safe) + filelisting callback argtypes
  * get_tracklisting linked-list walk + snapshot + destroy_track_t per node
    + optional LIBMTP_progressfunc_t (object-handle scan progress)
  * delete_object argtypes + device-pointer path (LIBMTP_Delete_Object)
  * get_file_metadata argtypes + device-pointer path (LIBMTP_Get_Filemetadata)
  * get_track_metadata argtypes + snapshot + destroy_track_t (LIBMTP_Get_Trackmetadata)

Living catalog of failure classes and *predicted* next breaks:
  docs/pymtp-binding-hazards.md
What libmtp/pymtp/MtpManager implement vs leave unbound:
  docs/libmtp-api-coverage.md
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import types

if sys.platform == "darwin" and ctypes.util.find_library("mtp") is None:
    _orig_find_library = ctypes.util.find_library

    def _find_library(name):
        if name == "mtp":
            for path in (
                "/opt/homebrew/lib/libmtp.dylib",
                "/usr/local/lib/libmtp.dylib",
            ):
                if os.path.exists(path):
                    return path
        return _orig_find_library(name)

    ctypes.util.find_library = _find_library

from pymtp import *  # noqa: E402, F403
from pymtp import LIBMTP_Filetype  # noqa: E402
from pymtp import MTP as _MTP  # noqa: E402
from pymtp import CommandFailed, NotConnected, ObjectNotFound  # noqa: E402
import pymtp as _pymtp  # noqa: E402

# ---------------------------------------------------------------------------
# Fix LIBMTP_Filetype enum (critical for send_track / send_file)
# ---------------------------------------------------------------------------
# Stock pymtp omitted LIBMTP_FILETYPE_FOLDER = 0, so every subsequent value was
# off-by-one vs modern libmtp (1.1.23). find_filetype("x.mp3") returned 1 (WAV)
# instead of 2 (MP3). Mutate in place so MTP.find_filetype sees the fix.
# ---------------------------------------------------------------------------
_LIBMTP_FILETYPE_1_1_23: dict[str, int] = {
    "FOLDER": 0,
    "WAV": 1,
    "MP3": 2,
    "WMA": 3,
    "OGG": 4,
    "AUDIBLE": 5,
    "MP4": 6,
    "UNDEF_AUDIO": 7,
    "WMV": 8,
    "AVI": 9,
    "MPEG": 10,
    "ASF": 11,
    "QT": 12,
    "UNDEF_VIDEO": 13,
    "JPEG": 14,
    "JFIF": 15,
    "TIFF": 16,
    "BMP": 17,
    "GIF": 18,
    "PICT": 19,
    "PNG": 20,
    "VCALENDAR1": 21,
    "VCALENDAR2": 22,
    "VCARD2": 23,
    "VCARD3": 24,
    "WINDOWSIMAGEFORMAT": 25,
    "WINEXEC": 26,
    "TEXT": 27,
    "HTML": 28,
    "FIRMWARE": 29,
    "AAC": 30,
    "MEDIACARD": 31,
    "FLAC": 32,
    "MP2": 33,
    "M4A": 34,
    "DOC": 35,
    "XML": 36,
    "XLS": 37,
    "PPT": 38,
    "MHT": 39,
    "JP2": 40,
    "JPX": 41,
    "ALBUM": 42,
    "PLAYLIST": 43,
    "UNKNOWN": 44,
}

LIBMTP_Filetype.clear()
LIBMTP_Filetype.update(_LIBMTP_FILETYPE_1_1_23)


def _configure_libmtp_ctypes() -> None:
    """Set argtypes/restype so multi-arg libmtp calls are correct on arm64."""
    lib = _pymtp._libmtp

    # Device pointers: accept any pointer-sized value (stock pymtp device
    # struct layout is also slightly stale vs libmtp 1.1.23).
    dev_p = ctypes.c_void_p
    track_p = ctypes.POINTER(_pymtp.LIBMTP_Track)
    err_p = ctypes.POINTER(_pymtp.LIBMTP_Error)

    lib.LIBMTP_Send_Track_From_File.argtypes = [
        dev_p,
        ctypes.c_char_p,
        track_p,
        dev_p,  # progress callback or NULL
        dev_p,  # user data or NULL
    ]
    lib.LIBMTP_Send_Track_From_File.restype = ctypes.c_int

    lib.LIBMTP_Send_File_From_File.argtypes = [
        dev_p,
        ctypes.c_char_p,
        ctypes.POINTER(_pymtp.LIBMTP_File),
        dev_p,
        dev_p,
    ]
    lib.LIBMTP_Send_File_From_File.restype = ctypes.c_int

    lib.LIBMTP_Dump_Errorstack.argtypes = [dev_p]
    lib.LIBMTP_Dump_Errorstack.restype = None

    lib.LIBMTP_Get_Errorstack.argtypes = [dev_p]
    lib.LIBMTP_Get_Errorstack.restype = err_p

    if hasattr(lib, "LIBMTP_Clear_Errorstack"):
        lib.LIBMTP_Clear_Errorstack.argtypes = [dev_p]
        lib.LIBMTP_Clear_Errorstack.restype = None

    if hasattr(lib, "LIBMTP_Get_Storage"):
        lib.LIBMTP_Get_Storage.argtypes = [dev_p, ctypes.c_int]
        lib.LIBMTP_Get_Storage.restype = ctypes.c_int

    folder_p = ctypes.POINTER(_pymtp.LIBMTP_Folder)
    if hasattr(lib, "LIBMTP_Get_Folder_List"):
        lib.LIBMTP_Get_Folder_List.argtypes = [dev_p]
        lib.LIBMTP_Get_Folder_List.restype = folder_p
    if hasattr(lib, "LIBMTP_Find_Folder"):
        lib.LIBMTP_Find_Folder.argtypes = [folder_p, ctypes.c_uint32]
        lib.LIBMTP_Find_Folder.restype = folder_p

    # uint32_t LIBMTP_Create_Folder(dev, char *name, uint32_t parent, uint32_t storage)
    if hasattr(lib, "LIBMTP_Create_Folder"):
        lib.LIBMTP_Create_Folder.argtypes = [
            dev_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.LIBMTP_Create_Folder.restype = ctypes.c_uint32

    if hasattr(lib, "LIBMTP_Set_Friendlyname"):
        lib.LIBMTP_Set_Friendlyname.argtypes = [dev_p, ctypes.c_char_p]
        lib.LIBMTP_Set_Friendlyname.restype = ctypes.c_int

    file_p = ctypes.POINTER(_pymtp.LIBMTP_File)
    # LIBMTP_file_t *Get_Filelisting_With_Callback(dev, progress_cb, user_data)
    if hasattr(lib, "LIBMTP_Get_Filelisting_With_Callback"):
        lib.LIBMTP_Get_Filelisting_With_Callback.argtypes = [
            dev_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LIBMTP_Get_Filelisting_With_Callback.restype = file_p

    # LIBMTP_track_t *Get_Tracklisting_With_Callback(dev, progress_cb, user_data)
    if hasattr(lib, "LIBMTP_Get_Tracklisting_With_Callback"):
        lib.LIBMTP_Get_Tracklisting_With_Callback.argtypes = [
            dev_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LIBMTP_Get_Tracklisting_With_Callback.restype = track_p

    # int LIBMTP_Delete_Object(LIBMTP_mtpdevice_t *, uint32_t object_id)
    if hasattr(lib, "LIBMTP_Delete_Object"):
        lib.LIBMTP_Delete_Object.argtypes = [dev_p, ctypes.c_uint32]
        lib.LIBMTP_Delete_Object.restype = ctypes.c_int

    # LIBMTP_file_t *LIBMTP_Get_Filemetadata(dev, uint32_t fileid)
    if hasattr(lib, "LIBMTP_Get_Filemetadata"):
        lib.LIBMTP_Get_Filemetadata.argtypes = [dev_p, ctypes.c_uint32]
        lib.LIBMTP_Get_Filemetadata.restype = file_p

    # LIBMTP_track_t *LIBMTP_Get_Trackmetadata(dev, uint32_t trackid)
    if hasattr(lib, "LIBMTP_Get_Trackmetadata"):
        lib.LIBMTP_Get_Trackmetadata.argtypes = [dev_p, ctypes.c_uint32]
        lib.LIBMTP_Get_Trackmetadata.restype = track_p

    if hasattr(lib, "LIBMTP_destroy_track_t"):
        lib.LIBMTP_destroy_track_t.argtypes = [track_p]
        lib.LIBMTP_destroy_track_t.restype = None


_configure_libmtp_ctypes()


def _as_c_char_p(value) -> bytes:
    """Encode a name for libmtp char* APIs (must stay bytes for the call)."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, ctypes.c_char_p):
        raw = value.value
        return raw if raw is not None else b""
    return str(value).encode("utf-8")


def _ptr_truthy(ptr) -> bool:
    """True if a ctypes POINTER is non-NULL."""
    if not ptr:
        return False
    try:
        return ctypes.cast(ptr, ctypes.c_void_p).value not in (None, 0)
    except (TypeError, ValueError, ctypes.ArgumentError):
        return bool(ptr)


def _get_folder_list(self):
    """Return ``{folder_id: LIBMTP_Folder}`` for the device (Python 3 safe).

    Stock pymtp uses ``dict.has_key``, which was removed in Python 3, so
    Device → List Folders crashes with AttributeError. Walk logic matches
    stock; only membership checks and NULL handling are modernized.
    """
    if self.device is None:
        raise NotConnected

    root = self.mtp.LIBMTP_Get_Folder_List(self.device)
    if not _ptr_truthy(root):
        return {}

    ret: dict = {}
    cur = root
    while True:
        if not _ptr_truthy(cur):
            break
        try:
            node = cur.contents
        except (ValueError, TypeError):
            break

        if node.folder_id not in ret:
            ret[node.folder_id] = node
            scanned = False
        else:
            scanned = True

        if (not scanned) and _ptr_truthy(node.child):
            cur = node.child
        elif _ptr_truthy(node.sibling):
            cur = node.sibling
        elif int(node.parent_id) != 0:
            found = self.mtp.LIBMTP_Find_Folder(root, int(node.parent_id))
            if not _ptr_truthy(found):
                break
            cur = found
        else:
            break

    return ret


def _get_parent_folders(self):
    """Return top-level folder structs (Python 3 safe; stock used has_key)."""
    if self.device is None:
        raise NotConnected

    root = self.mtp.LIBMTP_Get_Folder_List(self.device)
    if not _ptr_truthy(root):
        return []

    tmp: dict = {}
    cur = root
    while True:
        if not _ptr_truthy(cur):
            break
        try:
            node = cur.contents
        except (ValueError, TypeError):
            break

        if node.folder_id not in tmp:
            tmp[node.folder_id] = node

        if _ptr_truthy(node.sibling):
            cur = node.sibling
        else:
            break

    return list(tmp.values())


def _device_ptr(device) -> int | None:
    """Return the raw address of a pymtp device pointer, or None."""
    if device is None:
        return None
    try:
        return ctypes.cast(device, ctypes.c_void_p).value
    except (TypeError, ValueError, ctypes.ArgumentError):
        return None


def _debug_stack(self) -> None:
    """Dump error stack with a valid device pointer (stock pymtp omits it)."""
    addr = _device_ptr(self.device)
    if not addr:
        return
    try:
        self.mtp.LIBMTP_Dump_Errorstack(addr)
    except Exception:
        pass


def _send_track_from_file(self, source, target, metadata, callback=None):
    """Send a track with correct ctypes argtypes and path encoding.

    Replaces stock pymtp.MTP.send_track_from_file, which:
      * did not set argtypes (fragile on arm64)
      * called Dump_Errorstack() with no device argument
      * used a broken exists-check (``os.path.exists(source) == None``)
    """
    if self.device is None:
        raise NotConnected

    if not os.path.isfile(source):
        raise OSError(f"Track source not found: {source}")

    # filename: accept str/bytes/c_char_p
    if isinstance(target, ctypes.c_char_p):
        metadata.filename = target
    elif isinstance(target, bytes):
        metadata.filename = target
    else:
        metadata.filename = str(target).encode("utf-8")

    metadata.filetype = int(self.find_filetype(source))
    metadata.filesize = os.stat(source).st_size

    source_b = source.encode("utf-8") if isinstance(source, str) else source
    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    # Optional progress callback — stock Progressfunc signature is also wrong
    # (missing user-data arg); only pass NULL for reliability.
    _ = callback
    ret = self.mtp.LIBMTP_Send_Track_From_File(
        dev,
        source_b,
        ctypes.byref(metadata),
        None,
        None,
    )
    if ret != 0:
        _debug_stack(self)
        raise CommandFailed
    return metadata.item_id


def _get_filelisting(self, callback=None):
    """Return a list of ``LIBMTP_File`` structs (Python 3 / NULL-safe walk).

    Stock walks ``while next:`` without treating a NULL head as empty and uses
    an untyped progress callback. We always pass NULL for progress (experimental
    List Files does not need a callback) and stop cleanly on NULL links.
    """
    if self.device is None:
        raise NotConnected

    _ = callback  # progress callbacks remain unpatched / unused
    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    head = self.mtp.LIBMTP_Get_Filelisting_With_Callback(dev, None, None)
    if not _ptr_truthy(head):
        return []

    ret: list = []
    cur = head
    while _ptr_truthy(cur):
        try:
            node = cur.contents
        except (ValueError, TypeError):
            break
        ret.append(node)
        nxt = getattr(node, "next", None)
        if not _ptr_truthy(nxt):
            break
        cur = nxt

    return ret


# LIBMTP_progressfunc_t: int (*)(uint64_t sent, uint64_t total, void const *data)
# Return non-zero to cancel. Keep a module-level type so callbacks stay typed.
_ProgressFunc = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p
)


def _get_tracklisting(self, callback=None):
    """Return track snapshots (Python 3 / NULL-safe); free each C track node.

    Stock walks the linked list without a NULL-safe head check, leaves C
    strings allocated, and uses an untyped progress callback. We snapshot
    fields into Python and ``LIBMTP_destroy_track_t`` each node before
    following ``next`` (libmtp docs: destroy one node at a time — does not
    free the rest of the chain).

    *callback*, if given, is ``callback(sent, total)`` where *sent*/*total*
    are object-handle indices from libmtp (not yet the final track count).
    The expensive work is inside this C call (per-track metadata GETs).
    """
    if self.device is None:
        raise NotConnected

    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    # Keep the ctypes callback alive for the duration of the C call.
    c_progress = None
    if callback is not None:

        def _c_progress(sent, total, _data):
            try:
                callback(int(sent), int(total))
            except Exception:
                pass
            return 0  # continue

        c_progress = _ProgressFunc(_c_progress)

    head = self.mtp.LIBMTP_Get_Tracklisting_With_Callback(dev, c_progress, None)
    # Reference c_progress so it cannot be collected mid-call on some Pythons.
    _ = c_progress
    if not _ptr_truthy(head):
        return []

    destroy = getattr(self.mtp, "LIBMTP_destroy_track_t", None)
    ret: list = []
    cur = head
    while _ptr_truthy(cur):
        try:
            node = cur.contents
        except (ValueError, TypeError):
            break
        snap = _snapshot_track(node)
        ret.append(snap)
        nxt = getattr(node, "next", None)
        if destroy is not None:
            try:
                destroy(cur)
            except Exception:
                pass
        if not _ptr_truthy(nxt):
            break
        cur = nxt

    return ret


def _create_folder(self, name, parent=0, storage=0):
    """Create a folder with a proper UTF-8 C string (Python 3 / arm64 safe).

    Stock pymtp passes a Python ``str`` into ``LIBMTP_Create_Folder`` with no
    argtypes. On modern ctypes that mis-marshals the pointer so libmtp often
    only sees the first byte (folder named ``"B"`` for ``"Blargh"``).
    """
    if self.device is None:
        raise NotConnected

    name_b = _as_c_char_p(name)
    if not name_b:
        raise ValueError("Folder name must be non-empty")

    # create_string_buffer keeps a stable NUL-terminated buffer for the call.
    name_buf = ctypes.create_string_buffer(name_b)
    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    ret = self.mtp.LIBMTP_Create_Folder(
        dev,
        name_buf,
        ctypes.c_uint32(int(parent)),
        ctypes.c_uint32(int(storage)),
    )
    if ret == 0:
        _debug_stack(self)
        raise CommandFailed
    return int(ret)


def _set_devicename(self, name):
    """Set friendly name with UTF-8 c_char_p (same first-byte class of bug)."""
    if self.device is None:
        raise NotConnected

    name_b = _as_c_char_p(name)
    name_buf = ctypes.create_string_buffer(name_b)
    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    ret = self.mtp.LIBMTP_Set_Friendlyname(dev, name_buf)
    if ret != 0:
        _debug_stack(self)
        raise CommandFailed
    return ret


def _delete_object(self, object_id):
    """Delete one object by id with typed device/object args (arm64-safe).

    Stock passes the raw device struct and an untyped int into
    ``LIBMTP_Delete_Object``. No ``char*`` risk, but missing argtypes still
    matter on arm64; use the same device-pointer path as other patched calls.
    """
    if self.device is None:
        raise NotConnected

    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    ret = self.mtp.LIBMTP_Delete_Object(dev, ctypes.c_uint32(int(object_id)))
    if ret != 0:
        _debug_stack(self)
        raise CommandFailed
    return ret


def _get_file_metadata(self, file_id):
    """Return one ``LIBMTP_File`` by id with typed device/object args.

    Stock passes the raw device struct and untyped int into
    ``LIBMTP_Get_Filemetadata``. Same arm64 argtypes class as delete_object.

    libmtp's Get_Filemetadata asks ``ptp_object_want`` for ObjectInfo **and**
    MTP property list. On ZEN Vision:M (``BROKEN_MTPGETOBJPROPLIST_ALL``),
    single-object proplist can fail for some handles that still appear in
    ``Get_Filelisting`` — NULL here is often "proplist incomplete", not a
    missing object. Callers should fall back to listing snapshot fields.
    """
    if self.device is None:
        raise NotConnected

    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    ret = self.mtp.LIBMTP_Get_Filemetadata(dev, ctypes.c_uint32(int(file_id)))
    if not _ptr_truthy(ret):
        # Surface libmtp/USB noise (e.g. zero-packet panic) before ObjectNotFound.
        _debug_stack(self)
        raise ObjectNotFound
    try:
        return ret.contents
    except (ValueError, TypeError) as exc:
        _debug_stack(self)
        raise ObjectNotFound from exc


def _c_str_field(value) -> str:
    """Decode a libmtp ``char *`` field into a Python str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _snapshot_track(node) -> types.SimpleNamespace:
    """Copy LIBMTP_Track fields into a plain Python object (safe after destroy)."""
    return types.SimpleNamespace(
        item_id=int(getattr(node, "item_id", 0) or 0),
        parent_id=int(getattr(node, "parent_id", 0) or 0),
        storage_id=int(getattr(node, "storage_id", 0) or 0),
        title=_c_str_field(getattr(node, "title", None)),
        artist=_c_str_field(getattr(node, "artist", None)),
        composer=_c_str_field(getattr(node, "composer", None)),
        genre=_c_str_field(getattr(node, "genre", None)),
        album=_c_str_field(getattr(node, "album", None)),
        date=_c_str_field(getattr(node, "date", None)),
        filename=_c_str_field(getattr(node, "filename", None)),
        tracknumber=int(getattr(node, "tracknumber", 0) or 0),
        duration=int(getattr(node, "duration", 0) or 0),
        samplerate=int(getattr(node, "samplerate", 0) or 0),
        nochannels=int(getattr(node, "nochannels", 0) or 0),
        wavecodec=int(getattr(node, "wavecodec", 0) or 0),
        bitrate=int(getattr(node, "bitrate", 0) or 0),
        bitratetype=int(getattr(node, "bitratetype", 0) or 0),
        rating=int(getattr(node, "rating", 0) or 0),
        usecount=int(getattr(node, "usecount", 0) or 0),
        filesize=int(getattr(node, "filesize", 0) or 0),
        modificationdate=int(getattr(node, "modificationdate", 0) or 0),
        filetype=int(getattr(node, "filetype", 0) or 0),
    )


def _get_track_metadata(self, track_id):
    """Return one track metadata snapshot with typed args; free the C track.

    Stock passes untyped device/id into ``LIBMTP_Get_Trackmetadata`` and keeps
    the C-allocated ``LIBMTP_track_t`` (string leaks). We snapshot fields into
    Python and call ``LIBMTP_destroy_track_t``.

    Unlike Get_Filemetadata, libmtp only *requires* ObjectInfo for the handle;
    tags are filled via proplist cache or per-property GETs. Still USB-heavy —
    do not call in a tight loop. Non-track objects return NULL → ObjectNotFound.
    """
    if self.device is None:
        raise NotConnected

    dev = _device_ptr(self.device)
    if not dev:
        raise NotConnected

    ret = self.mtp.LIBMTP_Get_Trackmetadata(dev, ctypes.c_uint32(int(track_id)))
    if not _ptr_truthy(ret):
        _debug_stack(self)
        raise ObjectNotFound
    try:
        try:
            node = ret.contents
        except (ValueError, TypeError) as exc:
            _debug_stack(self)
            raise ObjectNotFound from exc
        return _snapshot_track(node)
    finally:
        destroy = getattr(self.mtp, "LIBMTP_destroy_track_t", None)
        if destroy is not None:
            try:
                destroy(ret)
            except Exception:
                pass


# Monkey-patch stock methods so all callers get the fixed behavior.
_MTP.debug_stack = _debug_stack
_MTP.send_track_from_file = _send_track_from_file
_MTP.get_folder_list = _get_folder_list
_MTP.get_parent_folders = _get_parent_folders
_MTP.get_filelisting = _get_filelisting
_MTP.create_folder = _create_folder
_MTP.get_tracklisting = _get_tracklisting
_MTP.set_devicename = _set_devicename
_MTP.delete_object = _delete_object
_MTP.get_file_metadata = _get_file_metadata
_MTP.get_track_metadata = _get_track_metadata
