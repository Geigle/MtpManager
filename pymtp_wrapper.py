"""
Load pymtp with platform-specific libmtp discovery fixes.

On Linux, ctypes.util.find_library("mtp") resolves via ldconfig as usual.
On macOS, find_library often returns None; patch it before pymtp loads libmtp.
"""

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

from pymtp import *  # noqa: F403