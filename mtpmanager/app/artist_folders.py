"""Ensure a device folder for an artist under Music (experimental send layout).

Creates ``Music/<Artist>`` (numeric parent 100 + folder object id) when missing.
Does **not** invent string paths like ``Music/Artist`` for the remote object name —
send still uses ``{folder_id}/{basename}``.
"""

from __future__ import annotations

import logging

from mtpmanager.domain.library import primary_artist_meta
from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    sanitize_component,
)
from mtpmanager.ports.device import DevicePort

logger = logging.getLogger(__name__)

# Device folder names: keep short; strip the same unsafe MTP characters as basenames.
_MAX_ARTIST_FOLDER_NAME = 48


def artist_folder_name(meta: TrackMetadata) -> str:
    """Sanitized folder name for the track's library artist (albumartist preferred)."""
    raw = primary_artist_meta(meta)
    return sanitize_component(raw, _MAX_ARTIST_FOLDER_NAME)


def find_child_folder(
    device: DevicePort,
    *,
    name: str,
    parent_id: int,
) -> int | None:
    """Return folder id of *name* under *parent_id*, if present (casefold match)."""
    want = (name or "").casefold().strip()
    if not want:
        return None
    for entry in device.list_folders():
        if int(entry.parent_id) != int(parent_id):
            continue
        if (entry.name or "").casefold().strip() == want:
            return int(entry.folder_id)
    return None


def ensure_artist_folder(
    device: DevicePort,
    meta: TrackMetadata,
    *,
    music_parent_id: int = DEFAULT_MUSIC_FOLDER_ID,
    cache: dict[str, int] | None = None,
) -> int:
    """Return device folder id for the artist; create under Music if needed.

    *cache* maps casefold folder name → id for batch transfers (one create per artist).
    """
    name = artist_folder_name(meta)
    key = name.casefold()
    if cache is not None and key in cache:
        return cache[key]

    existing = find_child_folder(device, name=name, parent_id=music_parent_id)
    if existing is not None:
        logger.info(
            "Artist folder exists: %r id=%s parent=%s",
            name,
            existing,
            music_parent_id,
        )
        if cache is not None:
            cache[key] = existing
        return existing

    new_id = int(device.create_folder(name, parent=music_parent_id))
    logger.info(
        "Created artist folder: %r id=%s parent=%s",
        name,
        new_id,
        music_parent_id,
    )
    if cache is not None:
        cache[key] = new_id
    return new_id
