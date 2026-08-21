"""Persist and restore a Library as a SQLite index under the app data dir.

Schema version 7: multi-root library + flat track rows (path, guid, tags) with
``tracked`` flag, plus ``library_exclusions`` (file/folder paths skipped on
scan and untracked from the UI). **Tracked** rows appear in the library UI;
**untracked** rows keep path/tags/GUID forever so device joins and future
rescans can reuse the same identity (principle: once identified by GUID, keep
it). Schema v6 adds ``playlists`` (named M3U bodies). Schema v7 adds
``podcasts`` / ``podcast_episodes`` for RSS subscriptions.

``library_meta.root_path`` remains the first root (back-compat); ``root_paths``
is a JSON array of active roots. Device inventory lives alongside in the same
DB file via ``device_index``.

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

from mtpmanager.domain.library import (
    Library,
    normalize_library_roots,
    path_is_excluded,
    path_under_root,
    prefer_higher_fidelity_tracks,
)
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.app_paths import default_data_dir
from mtpmanager.infra import private_hooks

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7
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
  tracked INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_exclusions (
  path TEXT PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'folder',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_path ON tracks(path);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_album ON tracks(artist, album);
CREATE INDEX IF NOT EXISTS idx_library_exclusions_kind ON library_exclusions(kind);
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


def _tracks_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(tracks)").fetchall()
    return {str(r[1]) for r in rows}


def _migrate_tracks_tracked(conn: sqlite3.Connection) -> None:
    """Add *tracked* column (schema v4). Existing rows default to tracked."""
    cols = _tracks_columns(conn)
    if not cols:
        return
    if "tracked" not in cols:
        conn.execute(
            "ALTER TABLE tracks ADD COLUMN tracked INTEGER NOT NULL DEFAULT 1"
        )
    # Index may be missing on brand-new DBs created before this helper ran, or
    # after ALTER; always ensure it (CREATE INDEX does not need the column to
    # exist at executescript time of the base schema).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tracks_tracked ON tracks(tracked)"
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
    _migrate_tracks_tracked(conn)
    # Host playlists (M3U text bodies).
    try:
        from mtpmanager.infra.playlists import ensure_playlists_schema

        ensure_playlists_schema(conn)
    except Exception:
        logger.debug("playlists schema ensure skipped", exc_info=True)
    # Podcast subscriptions + episodes.
    try:
        from mtpmanager.infra.podcast_index import ensure_podcasts_schema

        ensure_podcasts_schema(conn)
    except Exception:
        logger.debug("podcasts schema ensure skipped", exc_info=True)
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
    """path → guid for *all* rows (tracked and untracked) for GUID reuse."""
    rows = conn.execute("SELECT path, guid FROM tracks").fetchall()
    return {str(r["path"]): str(r["guid"]) for r in rows}


def _upsert_tracked_track(
    conn: sqlite3.Connection,
    track: Track,
    *,
    now: str,
) -> None:
    params = _meta_to_params(track.meta)
    conn.execute(
        """
        INSERT INTO tracks (
          guid, path,
          artist, albumartist, composer, album, title, genre,
          tracknumber, date, length_sec,
          sample_rate, channels, bitrate, bitrate_mode,
          tracked,
          created_at, updated_at
        ) VALUES (
          :guid, :path,
          :artist, :albumartist, :composer, :album, :title, :genre,
          :tracknumber, :date, :length_sec,
          :sample_rate, :channels, :bitrate, :bitrate_mode,
          1,
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
          tracked = 1,
          updated_at = excluded.updated_at
        """,
        {
            "guid": track.guid,
            "path": track.path,
            "created_at": now,
            "updated_at": now,
            **params,
        },
    )


def _set_library_meta_roots(
    conn: sqlite3.Connection,
    roots: list[str],
    *,
    now: str,
) -> None:
    primary = roots[0] if roots else ""
    conn.execute(
        """
        INSERT INTO library_meta (id, root_path, root_paths, scanned_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          root_path = excluded.root_path,
          root_paths = excluded.root_paths,
          scanned_at = excluded.scanned_at
        """,
        (primary, _roots_to_json(roots), now),
    )


def upsert_library_tracks(
    tracks: Iterable[Track],
    *,
    path: Path | None = None,
) -> int:
    """Upsert specific tracks without rewriting the full library index.

    Use this when only a few rows changed (e.g. a newly assigned video GUID).
    Does **not** soft-untrack other rows. Calls
    :func:`private_hooks.after_library_saved` with only these tracks so optional
    ``.tuneout`` rewrites stay scoped to touched directories.

    Tracks without a valid GUID or path are skipped. Returns the number written.
    """
    dest = path if path is not None else index_path()
    items = [
        t
        for t in tracks
        if t is not None
        and isinstance(t.path, str)
        and t.path
        and is_track_guid(getattr(t, "guid", "") or "")
    ]
    if not items:
        return 0

    now = _utc_now()
    t0 = time.perf_counter()
    conn = _connect(dest)
    try:
        _init_schema(conn)
        with conn:
            for t in items:
                # Path is UNIQUE: drop any other GUID row for this path first.
                conn.execute(
                    "DELETE FROM tracks WHERE path = ? AND guid != ?",
                    (t.path, t.guid),
                )
                _upsert_tracked_track(conn, t, now=now)
    finally:
        conn.close()

    elapsed = time.perf_counter() - t0
    logger.info(
        "Upserted %d library track(s) in %.2fs → %s",
        len(items),
        elapsed,
        dest,
    )
    private_hooks.after_library_saved(items)
    return len(items)


def save_library_index(
    library: Library,
    *,
    path: Path | None = None,
) -> Path:
    """Write *library* tracked set to the SQLite index; preserve untracked GUIDs.

    Assigns/preserves GUIDs using path maps that include untracked rows so a
    file reappearing at the same path reclaims its identity. Rows present in
    the DB but absent from *library* are **untracked** (not deleted).

    Mutates ``library.tracks`` so callers keep the assigned GUIDs.
    Returns the database path written.

    Prefer :func:`upsert_library_tracks` for small incremental GUID fixes —
    a full save rewrites every tracked row and may rewrite every ``.tuneout``
    sidecar via private hooks (multi-minute beach ball on large libraries).
    """
    dest = path if path is not None else index_path()
    now = _utc_now()
    t0 = time.perf_counter()
    conn = _connect(dest)
    try:
        _init_schema(conn)
        path_map = _load_path_guid_map(conn)
        # Optional private adapter may override path→guid (e.g. portable sidecars).
        path_map = private_hooks.enrich_path_guid_map(library.tracks, path_map)
        assigned = ensure_track_guids(library.tracks, path_to_guid=path_map)
        library.tracks[:] = assigned

        roots = normalize_library_roots(library.root_paths)
        if not roots and library.root_path:
            roots = normalize_library_roots([library.root_path])
        library.root_paths[:] = roots
        keep_guids = [t.guid for t in assigned if is_track_guid(t.guid)]

        with conn:
            _set_library_meta_roots(conn, roots, now=now)

            # Path is UNIQUE: if a path's GUID changed, drop the old row first
            # so the upsert can insert the new primary key (obsolete ids may
            # be retained by a private adapter outside SQLite).
            for t in assigned:
                if is_track_guid(t.guid):
                    conn.execute(
                        "DELETE FROM tracks WHERE path = ? AND guid != ?",
                        (t.path, t.guid),
                    )

            for t in assigned:
                _upsert_tracked_track(conn, t, now=now)

            # Soft-drop: keep GUID/path/tags for device join + future reuse.
            if keep_guids:
                placeholders = ",".join("?" * len(keep_guids))
                conn.execute(
                    f"UPDATE tracks SET tracked = 0, updated_at = ? "
                    f"WHERE tracked != 0 AND guid NOT IN ({placeholders})",
                    [now, *keep_guids],
                )
            else:
                conn.execute(
                    "UPDATE tracks SET tracked = 0, updated_at = ? WHERE tracked != 0",
                    (now,),
                )
    finally:
        conn.close()

    untracked = 0
    try:
        conn = _connect(dest)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tracks WHERE tracked = 0"
        ).fetchone()
        untracked = int(row["n"] if row else 0)
        conn.close()
    except sqlite3.Error:
        pass

    sql_elapsed = time.perf_counter() - t0
    logger.info(
        "Saved library index: %d tracked, %d untracked under %d root(s) %s → %s "
        "(sql %.1fs)",
        len(library.tracks),
        untracked,
        len(library.root_paths),
        library.root_paths,
        dest,
        sql_elapsed,
    )
    hook_t0 = time.perf_counter()
    private_hooks.after_library_saved(library.tracks)
    hook_elapsed = time.perf_counter() - hook_t0
    if hook_elapsed >= 0.5:
        logger.info(
            "after_library_saved hooks finished in %.1fs (%d track(s))",
            hook_elapsed,
            len(library.tracks),
        )
    return dest


def list_library_exclusions(
    *,
    path: Path | None = None,
) -> list[tuple[str, str]]:
    """Return durable exclusion rules as ``(path, kind)`` sorted by path.

    *kind* is ``\"file\"`` or ``\"folder\"``.
    """
    dest = path if path is not None else index_path()
    if not dest.is_file():
        return []
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(dest)
        _init_schema(conn)
        rows = conn.execute(
            "SELECT path, kind FROM library_exclusions "
            "ORDER BY path COLLATE NOCASE"
        ).fetchall()
        out: list[tuple[str, str]] = []
        for r in rows:
            p = str(r["path"] or "").strip()
            if not p:
                continue
            kind = str(r["kind"] or "folder").strip().lower()
            if kind not in ("file", "folder"):
                kind = "folder"
            out.append((os.path.normpath(p), kind))
        return out
    except sqlite3.Error as e:
        logger.warning("list_library_exclusions failed: %s", e)
        return []
    finally:
        if conn is not None:
            conn.close()


def load_exclusion_paths(*, path: Path | None = None) -> list[str]:
    """Return just the exclusion paths (for scan filters)."""
    return [p for p, _k in list_library_exclusions(path=path)]


def exclude_library_paths(
    entries: Iterable[tuple[str, str]],
    *,
    path: Path | None = None,
) -> Library:
    """Add exclusion rules and untrack matching media; return tracked Library.

    *entries* are ``(path, kind)`` with kind ``file`` or ``folder``. GUIDs for
    untracked rows are kept. Active library roots are unchanged.
    """
    dest = path if path is not None else index_path()
    now = _utc_now()
    cleaned: list[tuple[str, str]] = []
    for raw_path, raw_kind in entries:
        p = os.path.normpath((raw_path or "").strip())
        if not p:
            continue
        kind = (raw_kind or "folder").strip().lower()
        if kind not in ("file", "folder"):
            kind = "folder" if os.path.isdir(p) else "file"
        cleaned.append((p, kind))
    if not cleaned:
        # Still return current tracked set.
        lib = load_library_index(
            path=dest,
            drop_missing_files=False,
            migrate_json=False,
            keep_missing_if_roots_unreachable=True,
        )
        return lib or Library()

    conn = _connect(dest)
    try:
        _init_schema(conn)
        meta_row = conn.execute(
            "SELECT root_path, root_paths FROM library_meta WHERE id = 1"
        ).fetchone()
        roots: list[str] = []
        if meta_row is not None:
            roots = _roots_from_meta(
                str(meta_row["root_path"] or ""),
                meta_row["root_paths"],
            )

        exclusion_paths = [p for p, _k in cleaned]
        untracked_n = 0
        with conn:
            for p, kind in cleaned:
                conn.execute(
                    """
                    INSERT INTO library_exclusions (path, kind, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      kind = excluded.kind,
                      created_at = library_exclusions.created_at
                    """,
                    (p, kind, now),
                )

            rows = conn.execute(
                "SELECT guid, path FROM tracks WHERE tracked != 0"
            ).fetchall()
            to_untrack: list[str] = []
            for row in rows:
                tp = str(row["path"] or "")
                if path_is_excluded(tp, exclusion_paths):
                    to_untrack.append(str(row["guid"]))
            if to_untrack:
                placeholders = ",".join("?" * len(to_untrack))
                conn.execute(
                    f"UPDATE tracks SET tracked = 0, updated_at = ? "
                    f"WHERE guid IN ({placeholders})",
                    [now, *to_untrack],
                )
                untracked_n = len(to_untrack)

            tracked_rows = conn.execute(
                "SELECT * FROM tracks WHERE tracked != 0 "
                "ORDER BY path COLLATE NOCASE"
            ).fetchall()
            tracks = [_track_from_row(r) for r in tracked_rows if r["path"]]

        logger.info(
            "Excluded %d path(s); untracked %d track(s); %d tracked remain",
            len(cleaned),
            untracked_n,
            len(tracks),
        )
        return Library(tracks=tracks, root_paths=roots)
    finally:
        conn.close()


def remove_library_exclusions(
    paths: Iterable[str],
    *,
    path: Path | None = None,
) -> int:
    """Delete exclusion rules for *paths*. Returns number of rows removed.

    Does not rescan; caller should re-scan affected folders so media reappears.
    """
    dest = path if path is not None else index_path()
    cleaned = [os.path.normpath(p) for p in paths if (p or "").strip()]
    if not cleaned or not dest.is_file():
        return 0
    conn = _connect(dest)
    try:
        _init_schema(conn)
        removed = 0
        with conn:
            for p in cleaned:
                cur = conn.execute(
                    "DELETE FROM library_exclusions WHERE path = ?",
                    (p,),
                )
                removed += int(cur.rowcount or 0)
        logger.info("Removed %d library exclusion(s)", removed)
        return removed
    finally:
        conn.close()


def untrack_library_roots(
    removed_roots: Iterable[str],
    *,
    final_roots: Iterable[str] | None = None,
    path: Path | None = None,
) -> Library:
    """Mark tracks under *removed_roots* as untracked without rescanning.

    Updates active root list to *final_roots* (or current roots minus removed).
    Returns the remaining **tracked** library for the UI. GUID rows stay in the
    DB so device inventory can still resolve tags, and a future rescan of the
    same path reuses the GUID.
    """
    dest = path if path is not None else index_path()
    now = _utc_now()
    drop = normalize_library_roots(removed_roots)
    conn = _connect(dest)
    try:
        _init_schema(conn)
        meta_row = conn.execute(
            "SELECT root_path, root_paths FROM library_meta WHERE id = 1"
        ).fetchone()
        if meta_row is None:
            roots: list[str] = []
        else:
            roots = _roots_from_meta(
                str(meta_row["root_path"] or ""),
                meta_row["root_paths"],
            )
        if final_roots is not None:
            remaining = normalize_library_roots(final_roots)
        else:
            drop_set = set(drop)
            remaining = [r for r in roots if r not in drop_set]

        untracked_n = 0
        with conn:
            _set_library_meta_roots(conn, remaining, now=now)
            if drop:
                # Load paths for rows we might untrack (tracked only).
                rows = conn.execute(
                    "SELECT guid, path FROM tracks WHERE tracked != 0"
                ).fetchall()
                to_untrack: list[str] = []
                for row in rows:
                    p = str(row["path"] or "")
                    if any(path_under_root(p, r) for r in drop):
                        to_untrack.append(str(row["guid"]))
                if to_untrack:
                    placeholders = ",".join("?" * len(to_untrack))
                    conn.execute(
                        f"UPDATE tracks SET tracked = 0, updated_at = ? "
                        f"WHERE guid IN ({placeholders})",
                        [now, *to_untrack],
                    )
                    untracked_n = len(to_untrack)

            tracked_rows = conn.execute(
                "SELECT * FROM tracks WHERE tracked != 0 "
                "ORDER BY path COLLATE NOCASE"
            ).fetchall()
            tracks = [_track_from_row(r) for r in tracked_rows if r["path"]]

        logger.info(
            "Untracked %d track(s) under removed root(s) %s; "
            "%d tracked remain; roots=%s",
            untracked_n,
            drop,
            len(tracks),
            remaining,
        )
        return Library(tracks=tracks, root_paths=remaining)
    finally:
        conn.close()


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
            root_path = ""
        roots = _roots_from_meta(root_path, meta_row["root_paths"])
        if not roots and root_path:
            roots = normalize_library_roots([root_path])
        # Empty roots are valid (all roots removed; untracked GUIDs may remain).

        any_root_live = any(os.path.isdir(r) for r in roots) if roots else False
        should_drop = bool(drop_missing_files)
        if should_drop and keep_missing_if_roots_unreachable and not any_root_live:
            should_drop = False
            if roots:
                logger.warning(
                    "Library index root(s) not reachable: %r — keeping stale rows",
                    roots,
                )

        # UI only sees tracked rows; untracked GUIDs remain for device join.
        row_count_raw = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE tracked != 0"
        ).fetchone()
        row_count = int(row_count_raw[0] or 0) if row_count_raw is not None else 0
        if on_progress is not None:
            try:
                on_progress("meta", list(roots), row_count)
            except Exception:
                logger.debug("library index on_progress(meta) failed", exc_info=True)

        cursor = conn.execute(
            "SELECT * FROM tracks WHERE tracked != 0 ORDER BY path COLLATE NOCASE"
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

        tracks = prefer_higher_fidelity_tracks(tracks)

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


def get_tracks_by_paths(
    paths: Collection[str],
    *,
    path: Path | None = None,
) -> dict[str, Track]:
    """Return ``{path: Track}`` for known host paths (missing omitted)."""
    if not paths:
        return {}
    dest = path if path is not None else index_path()
    if not dest.is_file():
        return {}
    clean = [p for p in paths if isinstance(p, str) and p]
    if not clean:
        return {}
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(dest)
        _init_schema(conn)
        placeholders = ",".join("?" * len(clean))
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE path IN ({placeholders})",
            clean,
        ).fetchall()
        return {str(r["path"]): _track_from_row(r) for r in rows}
    except sqlite3.Error as e:
        logger.warning("get_tracks_by_paths failed: %s", e)
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
