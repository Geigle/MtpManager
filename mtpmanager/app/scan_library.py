"""Scan a directory tree into a Library."""

from __future__ import annotations

import logging
import os
from typing import Iterable

from mtpmanager.domain.library import Library, is_music_file, normalize_library_roots
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
    """Recursively scan *root_path* for music; return a single-root Library."""
    roots = normalize_library_roots([root_path] if root_path else [])
    if not roots or not os.path.isdir(roots[0]):
        return Library(tracks=[], root_paths=roots)
    found = _scan_dir(roots[0])
    found.sort(key=lambda t: t.path)
    return Library(tracks=found, root_paths=roots)


def scan_library_roots(root_paths: Iterable[str]) -> Library:
    """Scan every library root and merge tracks (dedupe by absolute path).

    Unreachable roots are kept in ``Library.root_paths`` but contribute no
    tracks (logged). Nested roots that share files only appear once.
    """
    roots = normalize_library_roots(root_paths)
    if not roots:
        return Library(tracks=[], root_paths=[])

    found: list[Track] = []
    seen_paths: set[str] = set()
    for root in roots:
        if not os.path.isdir(root):
            logger.warning("Library root not reachable during scan: %r", root)
            continue
        for track in _scan_dir(root):
            if track.path in seen_paths:
                continue
            seen_paths.add(track.path)
            found.append(track)

    found.sort(key=lambda t: t.path)
    logger.info(
        "Scanned %d library root(s) → %d track(s)",
        len(roots),
        len(found),
    )
    return Library(tracks=found, root_paths=roots)
