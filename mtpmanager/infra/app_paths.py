"""Platform paths for durable app data (library index, etc.)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """Platform path for app data files (or MTP_MANAGER_DATA_DIR override)."""
    override = os.environ.get("MTP_MANAGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MtpManager"

    xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data:
        return Path(xdg_data).expanduser() / "mtpmanager"
    return Path.home() / ".local" / "share" / "mtpmanager"
