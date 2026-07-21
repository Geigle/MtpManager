"""Persist and restore a Library as a SQLite index under the app data dir.

Schema version 1: library root + flat track rows (path, guid, tags).
Optional device_objects table records last-known on-device basename / item id.

Legacy ``library_index.json`` is imported once when the DB is missing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Iterable

from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
INDEX_FILENAME = "library_index.db"
LEGACY_JSON_FILENAME = "library_index.json"

_META_FIELD_NAMES = tuple(f.name for f in fields(TrackMetadata))

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS library_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  root_path TEXT NOT NULL,
  scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
  guid TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  artist TEXT NOT NULL DEFAULT '',
  albumartist TEXT NOT NULL DEFAULT '',
  composer TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  tracknumber TEXT NOT NULL DEFAULT '01',
  date TEXT NOT NULL DEFAULT '',
  length_sec REAL NOT NULL DEFAULT 0,
  sample_rate INTEGER NOT NULL DEFAULT 0,
  channels INTEGER NOT NULL DEFAULT 0,
  bitrate INTEGER NOT NULL DEFAULT 0,
  bitrate_mode INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_path ON tracks(path);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_album ON tracks(artist, album);

CREATE TABLE IF NOT EXISTS device_objects (
  guid TEXT PRIMARY KEY REFERENCES tracks(guid) ON DELETE CASCADE,
  item_id INTEGER,
  parent_id INTEGER NOT NULL DEFAULT 100,
  storage_id INTEGER NOT NULL DEFAULT 65537,
  remote_name TEXT NOT NULL,
  last_seen_at TEXT,
  last_sent_at TEXT
);
"""


def index_path(*, data_dir: Path | None = None) -> Path:
    """Return the path to the library index SQLite database."""
    base = data_dir if data_dir is not None else default_data_dir()
    return base / INDEX_FILENAME


def legacy_json_path(*, data_dir: Path | None = None) -> Path:
    """Return the path to the pre-SQLite JSON index (migration source)."""
    base = data_dir if data_dir is not None else default_data_dir()
    return base / LEGACY_JSON_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        pass
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    # user_version for future migrations
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if int(ver or 0) < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _meta_from_row(row: sqlite3.Row | dict[str, Any]) -> TrackMetadata:
    kwargs: dict[str, Any] = {}
    defaults = TrackMetadata()
    for name in _META_FIELD_NAMES:
        if name not in row.keys() if hasattr(row, "keys") else name not in row:
            continue
        raw = row[name]
        expected = type(getattr(defaults, name))
        try:
            kwargs[name] = expected(raw)  # type: ignore[call-arg]
        except (TypeError, ValueError):
            continue
    return TrackMetadata(**kwargs)


def _track_from_row(row: sqlite3.Row) -> Track:
    return Track(
        path=str(row["path"]),
        meta=_meta_from_row(row),
        guid=str(row["guid"] or ""),
    )


def _meta_to_params(meta: TrackMetadata) -> dict[str, Any]:
    return {name: getattr(meta, name) for name in _META_FIELD_NAMES}


def ensure_track_guids(
    tracks: Iterable[Track],
    *,
    path_to_guid: dict[str, str] | None = None,
) -> list[Track]:
    """Return tracks with stable GUIDs (reuse path map, then existing guid, else new)."""
    known = path_to_guid or {}
    out: list[Track] = []
    used: set[str] = set()
    for t in tracks:
        guid = ""
        if t.path in known and is_track_guid(known[t.path]):
            guid = known[t.path]
        elif is_track_guid(t.guid) and t.guid not in used:
            guid = t.guid
        else:
            guid = new_track_guid()
        # Avoid primary-key collisions if two tracks somehow share a guid.
        while guid in used:
            guid = new_track_guid()
        used.add(guid)
        if t.guid == guid:
            out.append(t)
        else:
            out.append(Track(path=t.path, meta=t.meta, guid=guid))
    return out


def _load_path_guid_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT path, guid FROM tracks").fetchall()
    return {str(r["path"]): str(r["guid"]) for r in rows}


def save_library_index(
    library: Library,
    *,
    path: Path | None = None,
) -> Path:
    """Write *library* to the SQLite index. Assigns/preserves GUIDs on tracks.

    Mutates ``library.tracks`` so callers keep the assigned GUIDs.
    Returns the database path written.
    """
    dest = path if path is not None else index_path()
    now = _utc_now()
    conn = _connect(dest)
    try:
        _init_schema(conn)
        path_map = _load_path_guid_map(conn)
        assigned = ensure_track_guids(library.tracks, path_to_guid=path_map)
        library.tracks[:] = assigned

        with conn:
            conn.execute(
                """
                INSERT INTO library_meta (id, root_path, scanned_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  root_path = excluded.root_path,
                  scanned_at = excluded.scanned_at
                """,
                (library.root_path or "", now),
            )
            # Full replace of track set for this library (matches old JSON rewrite).
            keep_guids = [t.guid for t in assigned]
            if keep_guids:
                placeholders = ",".join("?" * len(keep_guids))
                conn.execute(
                    f"DELETE FROM tracks WHERE guid NOT IN ({placeholders})",
                    keep_guids,
                )
            else:
                conn.execute("DELETE FROM tracks")

            for t in assigned:
                params = _meta_to_params(t.meta)
                conn.execute(
                    """
                    INSERT INTO tracks (
                      guid, path,
                      artist, albumartist, composer, album, title, genre,
                      tracknumber, date, length_sec,
                      sample_rate, channels, bitrate, bitrate_mode,
                      created_at, updated_at
                    ) VALUES (
                      :guid, :path,
                      :artist, :albumartist, :composer, :album, :title, :genre,
                      :tracknumber, :date, :length_sec,
                      :sample_rate, :channels, :bitrate, :bitrate_mode,
                      :created_at, :updated_at
                    )
                    ON CONFLICT(guid) DO UPDATE SET
                      path = excluded.path,
                      artist = excluded.artist,
                      albumartist = excluded.albumartist,
                      composer = excluded.composer,
                      album = excluded.album,
                      title = excluded.title,
                      genre = excluded.genre,
                      tracknumber = excluded.tracknumber,
                      date = excluded.date,
                      length_sec = excluded.length_sec,
                      sample_rate = excluded.sample_rate,
                      channels = excluded.channels,
                      bitrate = excluded.bitrate,
                      bitrate_mode = excluded.bitrate_mode,
                      updated_at = excluded.updated_at
                    """,
                    {
                        "guid": t.guid,
                        "path": t.path,
                        "created_at": now,
                        "updated_at": now,
                        **params,
                    },
                )
                # If path changed ownership of a guid collision was handled above;
                # also clear orphan device_objects for deleted guids via FK cascade
                # only when row deleted — ON DELETE CASCADE handles that.
    finally:
        conn.close()

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
    migrate_json: bool = True,
) -> Library | None:
    """Load a Library from the SQLite index.

    Returns None if the DB is missing/unreadable and no JSON migration applies.
    When *drop_missing_files* is True, tracks whose paths no longer exist
    on disk are omitted (count logged).
    """
    dest = path if path is not None else index_path()

    if migrate_json and not dest.is_file():
        data_dir = dest.parent
        migrated = migrate_json_if_needed(data_dir=data_dir, db_path=dest)
        if not migrated and not dest.is_file():
            return None

    if not dest.is_file():
        return None

    try:
        conn = _connect(dest)
    except sqlite3.Error as e:
        logger.warning("Cannot open library index %s: %s", dest, e)
        return None

    try:
        _init_schema(conn)
        meta_row = conn.execute(
            "SELECT root_path, scanned_at FROM library_meta WHERE id = 1"
        ).fetchone()
        if meta_row is None:
            # Empty DB — try JSON migration into this path.
            if migrate_json:
                conn.close()
                conn = None  # type: ignore[assignment]
                if migrate_json_if_needed(data_dir=dest.parent, db_path=dest):
                    return load_library_index(
                        path=dest,
                        drop_missing_files=drop_missing_files,
                        migrate_json=False,
                    )
            logger.warning("Library index %s: no library_meta row", dest)
            return None

        root_path = meta_row["root_path"]
        if not isinstance(root_path, str):
            logger.warning("Library index %s: invalid root_path", dest)
            return None

        rows = conn.execute(
            "SELECT * FROM tracks ORDER BY path COLLATE NOCASE"
        ).fetchall()
        tracks: list[Track] = []
        dropped = 0
        for row in rows:
            track = _track_from_row(row)
            if not track.path:
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
            dest,
        )
        return Library(tracks=tracks, root_path=root_path)
    except sqlite3.Error as e:
        logger.warning("Cannot read library index %s: %s", dest, e)
        return None
    finally:
        if conn is not None:
            conn.close()


def get_tracks_by_guids(
    guids: Collection[str],
    *,
    path: Path | None = None,
) -> dict[str, Track]:
    """Return ``{guid: Track}`` for known GUIDs in the index (missing omitted)."""
    if not guids:
        return {}
    dest = path if path is not None else index_path()
    if not dest.is_file():
        return {}
    clean = [g for g in guids if is_track_guid(g)]
    if not clean:
        return {}
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(dest)
        _init_schema(conn)
        placeholders = ",".join("?" * len(clean))
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE guid IN ({placeholders})",
            clean,
        ).fetchall()
        return {str(r["guid"]): _track_from_row(r) for r in rows}
    except sqlite3.Error as e:
        logger.warning("get_tracks_by_guids failed: %s", e)
        return {}
    finally:
        if conn is not None:
            conn.close()


def upsert_device_object(
    guid: str,
    *,
    remote_name: str,
    item_id: int | None = None,
    parent_id: int = 100,
    storage_id: int = 0x00010001,
    sent: bool = False,
    path: Path | None = None,
) -> None:
    """Record last-known device object for a library GUID (best-effort)."""
    if not is_track_guid(guid):
        return
    dest = path if path is not None else index_path()
    if not dest.is_file():
        return
    now = _utc_now()
    try:
        conn = _connect(dest)
        _init_schema(conn)
        # Only if track exists
        exists = conn.execute(
            "SELECT 1 FROM tracks WHERE guid = ?", (guid,)
        ).fetchone()
        if exists is None:
            conn.close()
            return
        with conn:
            conn.execute(
                """
                INSERT INTO device_objects (
                  guid, item_id, parent_id, storage_id, remote_name,
                  last_seen_at, last_sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                  item_id = COALESCE(excluded.item_id, device_objects.item_id),
                  parent_id = excluded.parent_id,
                  storage_id = excluded.storage_id,
                  remote_name = excluded.remote_name,
                  last_seen_at = excluded.last_seen_at,
                  last_sent_at = CASE
                    WHEN excluded.last_sent_at IS NOT NULL
                    THEN excluded.last_sent_at
                    ELSE device_objects.last_sent_at
                  END
                """,
                (
                    guid,
                    item_id,
                    int(parent_id),
                    int(storage_id),
                    remote_name,
                    now,
                    now if sent else None,
                ),
            )
    except sqlite3.Error as e:
        logger.debug("upsert_device_object failed: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def index_exists(*, path: Path | None = None) -> bool:
    """True if the library index database exists on disk."""
    src = path if path is not None else index_path()
    return src.is_file()


# ---------------------------------------------------------------------------
# Legacy JSON migration
# ---------------------------------------------------------------------------


def _meta_from_dict(raw: dict[str, Any] | None) -> TrackMetadata:
    if not raw or not isinstance(raw, dict):
        return TrackMetadata()
    kwargs = {k: raw[k] for k in _META_FIELD_NAMES if k in raw}
    try:
        return TrackMetadata(**kwargs)
    except TypeError:
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


def _track_from_json_dict(raw: dict[str, Any]) -> Track | None:
    path = raw.get("path")
    if not path or not isinstance(path, str):
        return None
    meta = _meta_from_dict(
        raw.get("meta") if isinstance(raw.get("meta"), dict) else None
    )
    guid = raw.get("guid") if isinstance(raw.get("guid"), str) else ""
    return Track(path=path, meta=meta, guid=guid if is_track_guid(guid) else "")


def load_legacy_json_library(json_path: Path) -> Library | None:
    """Load the old JSON index shape (for one-shot migration)."""
    if not json_path.is_file():
        return None
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        logger.warning("Cannot read legacy library index %s: %s", json_path, e)
        return None
    if not isinstance(raw, dict):
        return None
    root_path = raw.get("root_path")
    if not isinstance(root_path, str):
        return None
    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, list):
        return None
    tracks: list[Track] = []
    for item in tracks_raw:
        if not isinstance(item, dict):
            continue
        track = _track_from_json_dict(item)
        if track is not None:
            tracks.append(track)
    return Library(tracks=tracks, root_path=root_path)


def migrate_json_if_needed(
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
) -> bool:
    """If SQLite is missing/empty and JSON exists, import once. Returns True on migrate."""
    base = data_dir if data_dir is not None else default_data_dir()
    dest = db_path if db_path is not None else (base / INDEX_FILENAME)
    json_src = base / LEGACY_JSON_FILENAME

    if dest.is_file():
        # Only migrate into empty DBs (no meta row).
        try:
            conn = _connect(dest)
            _init_schema(conn)
            row = conn.execute(
                "SELECT 1 FROM library_meta WHERE id = 1"
            ).fetchone()
            conn.close()
            if row is not None:
                return False
        except sqlite3.Error:
            return False

    if not json_src.is_file():
        return False

    lib = load_legacy_json_library(json_src)
    if lib is None:
        return False

    save_library_index(lib, path=dest)
    logger.info(
        "Migrated library index JSON → SQLite: %d tracks (%s → %s)",
        len(lib.tracks),
        json_src,
        dest,
    )
    return True
