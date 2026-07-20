"""Ensure device folders for artist (and optional album) under Music.

Creates ``Music/<Artist>`` and optionally ``Music/<Artist>/<Album>`` as real
folder objects (numeric parent ids). Does **not** invent string paths like
``Music/Artist/Album`` for the remote object name — send still uses
``{folder_id}/{basename}``.
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
_MAX_ALBUM_FOLDER_NAME = 48


def artist_folder_name(meta: TrackMetadata) -> str:
    """Sanitized folder name for the track's library artist (albumartist preferred)."""
    raw = primary_artist_meta(meta)
    return sanitize_component(raw, _MAX_ARTIST_FOLDER_NAME)


def album_folder_name(meta: TrackMetadata) -> str:
    """Sanitized folder name for the track's album tag."""
    raw = (meta.album or "").strip() or "Unknown Album"
    return sanitize_component(raw, _MAX_ALBUM_FOLDER_NAME)


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


def _cache_get(cache: dict[str, int] | None, key: str) -> int | None:
    if cache is None:
        return None
    return cache.get(key)


def _cache_put(cache: dict[str, int] | None, key: str, folder_id: int) -> None:
    if cache is not None:
        cache[key] = folder_id


def ensure_artist_folder(
    device: DevicePort,
    meta: TrackMetadata,
    *,
    music_parent_id: int = DEFAULT_MUSIC_FOLDER_ID,
    cache: dict[str, int] | None = None,
) -> int:
    """Return device folder id for the artist; create under Music if needed.

    *cache* maps keys → folder id for batch transfers (one create per artist).
    Artist keys are ``a:<casefold name>``.
    """
    name = artist_folder_name(meta)
    key = f"a:{name.casefold()}"
    hit = _cache_get(cache, key)
    if hit is not None:
        return hit

    existing = find_child_folder(device, name=name, parent_id=music_parent_id)
    if existing is not None:
        logger.info(
            "Artist folder exists: %r id=%s parent=%s",
            name,
            existing,
            music_parent_id,
        )
        _cache_put(cache, key, existing)
        return existing

    new_id = int(device.create_folder(name, parent=music_parent_id))
    logger.info(
        "Created artist folder: %r id=%s parent=%s",
        name,
        new_id,
        music_parent_id,
    )
    _cache_put(cache, key, new_id)
    return new_id


def ensure_album_folder(
    device: DevicePort,
    meta: TrackMetadata,
    *,
    music_parent_id: int = DEFAULT_MUSIC_FOLDER_ID,
    cache: dict[str, int] | None = None,
) -> int:
    """Return device folder id for ``Music/<Artist>/<Album>``; create as needed.

    Ensures the artist folder first, then the album folder under it.
    *cache* also holds album keys ``alb:<artist_id>:<casefold album name>``.
    """
    artist_id = ensure_artist_folder(
        device,
        meta,
        music_parent_id=music_parent_id,
        cache=cache,
    )
    name = album_folder_name(meta)
    key = f"alb:{artist_id}:{name.casefold()}"
    hit = _cache_get(cache, key)
    if hit is not None:
        return hit

    existing = find_child_folder(device, name=name, parent_id=artist_id)
    if existing is not None:
        logger.info(
            "Album folder exists: %r id=%s parent=%s",
            name,
            existing,
            artist_id,
        )
        _cache_put(cache, key, existing)
        return existing

    new_id = int(device.create_folder(name, parent=artist_id))
    logger.info(
        "Created album folder: %r id=%s parent=%s",
        name,
        new_id,
        artist_id,
    )
    _cache_put(cache, key, new_id)
    return new_id
