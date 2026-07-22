"""Resolve packaged device graphic paths."""

from __future__ import annotations

from pathlib import Path

_DEVICES_DIR = Path(__file__).resolve().parent.parent / "assets" / "devices"


def devices_dir() -> Path:
    return _DEVICES_DIR


def device_graphic_path(filename: str) -> Path:
    """Return absolute path to a device graphic basename under assets/devices."""
    name = Path(filename).name  # no directory traversal
    return _DEVICES_DIR / name
