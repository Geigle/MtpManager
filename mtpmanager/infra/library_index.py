"""Persist and restore a Library as a versioned JSON index under the app data dir."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

INDEX_VERSION = 1
INDEX_FILENAME = "library_index.json"

_META_FIELD_NAMES = frozenset(f.name for f in fields(TrackMetadata))


def index_path(*, data_dir: Path | None = None) -> Path:
    """Return the path to the library index JSON file."""
    base = data_dir if data_dir is not None else default_data_dir()
    return base / INDEX_FILENAME


def _meta_to_dict(meta: TrackMetadata) -> dict[str, Any]:
    return asdict(meta)


def _meta_from_dict(raw: dict[str, Any] | None) -> TrackMetadata:
    if not raw or not isinstance(raw, dict):
        return TrackMetadata()
    kwargs = {k: raw[k] for k in _META_FIELD_NAMES if k in raw}
    try:
        return TrackMetadata(**kwargs)
    except TypeError:
        # Unexpected value types — fall back field-by-field with defaults.
        defaults = TrackMetadata()
        safe: dict[str, Any] = {}
        for name in _META_FIELD_NAMES:
            if name not in kwargs:
                continue
            expected = type(getattr(defaults, name))
            try:
                safe[name] = expected(kwargs[name])  # type: ignore[call-arg]
            except (TypeError, ValueError):
                continue
        return TrackMetadata(**safe)


def _track_to_dict(track: Track) -> dict[str, Any]:
    return {"path": track.path, "meta": _meta_to_dict(track.meta)}


def _track_from_dict(raw: dict[str, Any]) -> Track | None:
    path = raw.get("path")
    if not path or not isinstance(path, str):
        return None
    meta = _meta_from_dict(raw.get("meta") if isinstance(raw.get("meta"), dict) else None)
    return Track(path=path, meta=meta)


def save_library_index(
    library: Library,
    *,
    path: Path | None = None,
) -> Path:
    """Write *library* to the index file. Returns the path written."""
    dest = path if path is not None else index_path()
    payload = {
        "version": INDEX_VERSION,
        "root_path": library.root_path or "",
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tracks": [_track_to_dict(t) for t in library.tracks],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    logger.info(
        "Saved library index: %d tracks under %s → %s",
        len(library.tracks),
        library.root_path,
        dest,
    )
    return dest


def load_library_index(
    *,
    path: Path | None = None,
    drop_missing_files: bool = True,
) -> Library | None:
    """Load a Library from the index file.

    Returns None if the file is missing, unreadable, or invalid.
    When *drop_missing_files* is True, tracks whose paths no longer exist
    on disk are omitted (count logged).
    """
    src = path if path is not None else index_path()
    if not src.is_file():
        return None
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        logger.warning("Cannot read library index %s: %s", src, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("Library index %s: root is not an object", src)
        return None

    root_path = raw.get("root_path")
    if not isinstance(root_path, str):
        logger.warning("Library index %s: missing or invalid root_path", src)
        return None

    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, list):
        logger.warning("Library index %s: missing or invalid tracks list", src)
        return None

    tracks: list[Track] = []
    dropped = 0
    for item in tracks_raw:
        if not isinstance(item, dict):
            continue
        track = _track_from_dict(item)
        if track is None:
            continue
        if drop_missing_files and not os.path.isfile(track.path):
            dropped += 1
            continue
        tracks.append(track)

    if dropped:
        logger.info(
            "Library index: dropped %d missing file(s); kept %d",
            dropped,
            len(tracks),
        )

    logger.info(
        "Loaded library index: %d tracks under %s from %s",
        len(tracks),
        root_path,
        src,
    )
    return Library(tracks=tracks, root_path=root_path)


def index_exists(*, path: Path | None = None) -> bool:
    """True if the library index file exists on disk."""
    src = path if path is not None else index_path()
    return src.is_file()
