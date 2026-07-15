"""
Load pymtp with platform-specific libmtp discovery fixes.

On Linux, ctypes.util.find_library("mtp") resolves via ldconfig as usual.
On macOS, find_library often returns None; patch it before pymtp loads libmtp.

Also patches known pymtp/libmtp binding bugs that break track send on modern
libmtp (1.1.x) and Apple Silicon:
  * LIBMTP_Filetype enum off-by-one (missing FOLDER=0)
  * Missing argtypes for LIBMTP_Send_Track_From_File (bad calls on arm64)
  * Dump_Errorstack called without a device pointer (NULL-device PANIC)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

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
from pymtp import CommandFailed, NotConnected  # noqa: E402
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


_configure_libmtp_ctypes()


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


# Monkey-patch stock methods so all callers get the fixed behavior.
_MTP.debug_stack = _debug_stack
_MTP.send_track_from_file = _send_track_from_file
