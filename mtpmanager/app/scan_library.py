"""Scan a directory tree into a Library."""

from __future__ import annotations

import logging
import os

from mtpmanager.domain.library import Library, is_music_file
from mtpmanager.domain.models import Track
from mtpmanager.infra.mutagen_tags import read_metadata

logger = logging.getLogger(__name__)


def _scan_dir(dir_path: str) -> list[Track]:
    tracks: list[Track] = []
    try:
        entries = os.listdir(dir_path)
    except OSError as e:
        logger.warning("Cannot list %s: %s", dir_path, e)
        return tracks

    for name in entries:
        full = os.path.join(dir_path, name)
        if os.path.isdir(full):
            tracks.extend(_scan_dir(full))

    for name in entries:
        full = os.path.join(dir_path, name)
        if not os.path.isfile(full):
            continue
        filename = os.fsdecode(name) if isinstance(name, bytes) else name
        if not is_music_file(filename):
            continue
        meta = read_metadata(full)
        tracks.append(Track(path=full, meta=meta))
    return tracks


def scan_library(root_path: str) -> Library:
    """Recursively scan root_path for music; return sorted Library."""
    if not root_path or not os.path.isdir(root_path):
        return Library(tracks=[], root_path=root_path or "")
    found = _scan_dir(root_path)
    found.sort(key=lambda t: t.path)
    return Library(tracks=found, root_path=root_path)
