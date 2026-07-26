"""Persist and restore a Library as a SQLite index under the app data dir.

Schema version 3: one or more library roots + flat track rows (path, guid, tags).
``library_meta.root_path`` remains the first root (back-compat); ``root_paths``
is a JSON array of all roots. Optional device_objects table records last-known
on-device basename / item id.

Legacy ``library_index.json`` is imported once when the DB is missing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Iterable

from mtpmanager.domain.library import Library, normalize_library_roots
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3
INDEX_FILENAME = "library_index.db"
LEGACY_JSON_FILENAME = "library_index.json"

_META_FIELD_NAMES = tuple(f.name for f in fields(TrackMetadata))

# Host library tables only. Device inventory lives in device_index.py
# (devices / device_files) on the same DB file.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS library_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  root_path TEXT NOT NULL,
  root_paths TEXT NOT NULL DEFAULT '[]',
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


def _library_meta_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(library_meta)").fetchall()
    return {str(r[1]) for r in rows}


def _migrate_library_meta(conn: sqlite3.Connection) -> None:
    """Add multi-root column on existing DBs created before schema v3."""
    cols = _library_meta_columns(conn)
    if not cols:
        return
    if "root_paths" not in cols:
        conn.execute(
            "ALTER TABLE library_meta ADD COLUMN root_paths TEXT NOT NULL DEFAULT '[]'"
        )
        # Seed root_paths from legacy single root_path where still empty.
        row = conn.execute(
            "SELECT root_path, root_paths FROM library_meta WHERE id = 1"
        ).fetchone()
        if row is not None:
            primary = row["root_path"] if "root_path" in row.keys() else ""
            existing = row["root_paths"] if "root_paths" in row.keys() else "[]"
            roots = _roots_from_meta(
                str(primary or ""),
                existing if existing not in ("", "[]", None) else None,
            )
            if roots:
                conn.execute(
                    "UPDATE library_meta SET root_paths = ? WHERE id = 1",
                    (_roots_to_json(roots),),
                )


def _roots_to_json(root_paths: list[str]) -> str:
    return json.dumps(list(root_paths), ensure_ascii=False)


def _roots_from_meta(root_path: str, root_paths_raw: Any) -> list[str]:
    """Parse durable roots from meta row (JSON list or legacy single path)."""
    roots: list[str] = []
    if isinstance(root_paths_raw, str) and root_paths_raw.strip():
        try:
            parsed = json.loads(root_paths_raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            roots = [p for p in parsed if isinstance(p, str)]
    if not roots and isinstance(root_path, str) and root_path:
        roots = [root_path]
    return normalize_library_roots(roots)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    _migrate_library_meta(conn)
    # Device inventory tables (same DB); safe no-op if already present.
    try:
        from mtpmanager.infra.device_index import _ensure_schema as _device_schema

        _device_schema(conn)
    except Exception:
        logger.debug("device_index schema ensure skipped", exc_info=True)
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

        roots = normalize_library_roots(library.root_paths)
        if not roots and library.root_path:
            roots = normalize_library_roots([library.root_path])
        library.root_paths[:] = roots
        primary = roots[0] if roots else ""
        roots_json = _roots_to_json(roots)

        with conn:
            conn.execute(
                """
                INSERT INTO library_meta (id, root_path, root_paths, scanned_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  root_path = excluded.root_path,
                  root_paths = excluded.root_paths,
                  scanned_at = excluded.scanned_at
                """,
                (primary, roots_json, now),
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
        "Saved library index: %d tracks under %d root(s) %s → %s",
        len(library.tracks),
        len(library.root_paths),
        library.root_paths,
        dest,
    )
    return dest


def load_library_index(
    *,
    path: Path | None = None,
    drop_missing_files: bool = True,
    migrate_json: bool = True,
    keep_missing_if_roots_unreachable: bool = False,
    on_progress: Callable[..., None] | None = None,
    progress_batch_first: int = 1,
    progress_batch_second: int = 1,
    progress_batch_cap: int = 512,
    progress_yield_s: float = 0.015,
) -> Library | None:
    """Load a Library from the SQLite index.

    Returns None if the DB is missing/unreadable and no JSON migration applies.
    When *drop_missing_files* is True, tracks whose paths no longer exist
    on disk are omitted (count logged). When *keep_missing_if_roots_unreachable*
    is also True, missing-file drops are skipped if **no** root directory is
    present (stale offline index still displays).

    *on_progress* (optional, may run on a worker thread) receives:

    - ``("meta", root_paths, row_count)`` once roots are known
    - ``("batch", tracks, kept_count, row_count)`` for each Fibonacci-sized
      batch of kept tracks (so a UI can paint while load continues)

    A short *progress_yield_s* sleep runs after each progress batch so a Tk
    poll loop can paint between batches (otherwise a fast SSD load queues
    every batch before the first UI refresh).
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
            "SELECT root_path, root_paths, scanned_at FROM library_meta WHERE id = 1"
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
                        keep_missing_if_roots_unreachable=keep_missing_if_roots_unreachable,
                        on_progress=on_progress,
                        progress_batch_first=progress_batch_first,
                        progress_batch_second=progress_batch_second,
                        progress_batch_cap=progress_batch_cap,
                        progress_yield_s=progress_yield_s,
                    )
            logger.warning("Library index %s: no library_meta row", dest)
            return None

        root_path = meta_row["root_path"]
        if not isinstance(root_path, str):
            logger.warning("Library index %s: invalid root_path", dest)
            return None
        roots = _roots_from_meta(root_path, meta_row["root_paths"])
        if not roots and root_path:
            roots = normalize_library_roots([root_path])
        if not roots:
            logger.warning("Library index %s: no library roots", dest)
            return None

        any_root_live = any(os.path.isdir(r) for r in roots)
        should_drop = bool(drop_missing_files)
        if should_drop and keep_missing_if_roots_unreachable and not any_root_live:
            should_drop = False
            logger.warning(
                "Library index root(s) not reachable: %r — keeping stale rows",
                roots,
            )

        row_count_raw = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
        row_count = int(row_count_raw[0] or 0) if row_count_raw is not None else 0
        if on_progress is not None:
            try:
                on_progress("meta", list(roots), row_count)
            except Exception:
                logger.debug("library index on_progress(meta) failed", exc_info=True)

        cursor = conn.execute(
            "SELECT * FROM tracks ORDER BY path COLLATE NOCASE"
        )
        tracks: list[Track] = []
        dropped = 0
        batch: list[Track] = []
        fib_a = max(1, int(progress_batch_first))
        fib_b = max(1, int(progress_batch_second))
        cap = max(1, int(progress_batch_cap))
        next_batch = min(fib_a, cap)

        def flush_batch() -> None:
            nonlocal batch, fib_a, fib_b, next_batch
            if not batch or on_progress is None:
                batch = []
                return
            payload = list(batch)
            batch = []
            try:
                on_progress("batch", payload, len(tracks), row_count)
            except Exception:
                logger.debug(
                    "library index on_progress(batch) failed", exc_info=True
                )
            # Let the UI thread drain progress and paint rows.
            if progress_yield_s > 0:
                time.sleep(progress_yield_s)
            fib_a, fib_b = fib_b, fib_a + fib_b
            next_batch = min(fib_a, cap)

        for row in cursor:
            track = _track_from_row(row)
            if not track.path:
                continue
            if should_drop and not os.path.isfile(track.path):
                dropped += 1
                continue
            tracks.append(track)
            if on_progress is not None:
                batch.append(track)
                if len(batch) >= next_batch:
                    flush_batch()

        if on_progress is not None and batch:
            flush_batch()

        if dropped:
            logger.info(
                "Library index: dropped %d missing file(s); kept %d",
                dropped,
                len(tracks),
            )

        logger.info(
            "Loaded library index: %d tracks under %d root(s) %s from %s",
            len(tracks),
            len(roots),
            roots,
            dest,
        )
        return Library(tracks=tracks, root_paths=roots)
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
    roots_raw = raw.get("root_paths")
    roots: list[str] = []
    if isinstance(roots_raw, list):
        roots = [p for p in roots_raw if isinstance(p, str)]
    if not roots and isinstance(root_path, str) and root_path:
        roots = [root_path]
    if not roots:
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
    return Library(tracks=tracks, root_paths=roots)


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
