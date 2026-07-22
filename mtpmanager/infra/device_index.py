"""Durable on-device file inventory in the library SQLite DB.

Full ``list_files`` / get_filelisting is expensive and can destabilize ZEN
sessions. Seed once on connect (or explicit Refresh), then maintain rows on
successful send / delete. Skip-if-present and List Files/Tracks read this
cache — not a live USB walk every sync.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from mtpmanager.domain.device_media import track_refs_from_files
from mtpmanager.domain.models import DeviceInfo, DeviceTrackRef, FileEntry
from mtpmanager.domain.track_id import guid_from_remote_name, is_track_guid
from mtpmanager.infra.library_index import index_path
from mtpmanager.infra.remote_naming import DEFAULT_MUSIC_FOLDER_ID, DEFAULT_STORAGE_ID

logger = logging.getLogger(__name__)

DEFAULT_SERIAL = "default"

_DEVICES_SQL = """
CREATE TABLE IF NOT EXISTS devices (
  serial TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  manufacturer TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  last_listed_at TEXT,
  list_complete INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_files (
  serial TEXT NOT NULL,
  name TEXT NOT NULL,
  item_id INTEGER NOT NULL DEFAULT 0,
  parent_id INTEGER NOT NULL DEFAULT 100,
  storage_id INTEGER NOT NULL DEFAULT 65537,
  filesize INTEGER NOT NULL DEFAULT 0,
  filetype INTEGER NOT NULL DEFAULT 0,
  guid TEXT,
  last_seen_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'list',
  PRIMARY KEY (serial, name),
  FOREIGN KEY (serial) REFERENCES devices(serial) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_files_guid
  ON device_files(serial, guid);
CREATE INDEX IF NOT EXISTS idx_device_files_item
  ON device_files(serial, item_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def device_serial_key(info: DeviceInfo | None = None, *, serial: str | None = None) -> str:
    """Stable key for a physical device (serial preferred)."""
    if serial and str(serial).strip():
        return str(serial).strip()
    if info is not None and (info.serial or "").strip():
        return str(info.serial).strip()
    return DEFAULT_SERIAL


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


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DEVICES_SQL)
    _migrate_legacy_device_objects(conn)
    # Keep user_version at least 2 when device tables exist.
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if ver < 2:
        conn.execute("PRAGMA user_version = 2")


def _migrate_legacy_device_objects(conn: sqlite3.Connection) -> None:
    """Copy schema-v1 device_objects into device_files under default serial."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='device_objects'"
    ).fetchone()
    if row is None:
        return
    # Ensure default device row
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO devices (serial, name, manufacturer, model, last_listed_at, list_complete)
        VALUES (?, '', '', '', ?, 0)
        ON CONFLICT(serial) DO NOTHING
        """,
        (DEFAULT_SERIAL, now),
    )
    try:
        legacy = conn.execute(
            "SELECT guid, item_id, parent_id, storage_id, remote_name, "
            "last_seen_at, last_sent_at FROM device_objects"
        ).fetchall()
    except sqlite3.Error:
        return
    for r in legacy:
        name = str(r["remote_name"] or "").strip()
        if not name:
            continue
        guid = str(r["guid"] or "") if is_track_guid(str(r["guid"] or "")) else None
        if guid is None:
            guid = guid_from_remote_name(name)
        seen = r["last_seen_at"] or r["last_sent_at"] or now
        conn.execute(
            """
            INSERT INTO device_files (
              serial, name, item_id, parent_id, storage_id, filesize, filetype,
              guid, last_seen_at, source
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 'send')
            ON CONFLICT(serial, name) DO UPDATE SET
              item_id = excluded.item_id,
              parent_id = excluded.parent_id,
              storage_id = excluded.storage_id,
              guid = COALESCE(excluded.guid, device_files.guid),
              last_seen_at = excluded.last_seen_at
            """,
            (
                DEFAULT_SERIAL,
                name,
                int(r["item_id"] or 0),
                int(r["parent_id"] or DEFAULT_MUSIC_FOLDER_ID),
                int(r["storage_id"] or DEFAULT_STORAGE_ID),
                guid,
                seen,
            ),
        )
    conn.execute("DROP TABLE device_objects")
    logger.info(
        "Migrated device_objects → device_files (%d row(s))",
        len(legacy),
    )


def _open(path: Path | None = None) -> tuple[sqlite3.Connection, Path]:
    dest = path if path is not None else index_path()
    conn = _connect(dest)
    _ensure_schema(conn)
    return conn, dest


def upsert_device(
    serial: str,
    *,
    name: str = "",
    manufacturer: str = "",
    model: str = "",
    path: Path | None = None,
) -> None:
    """Ensure a devices row exists for *serial*."""
    key = device_serial_key(serial=serial)
    conn, _ = _open(path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO devices (serial, name, manufacturer, model, last_listed_at, list_complete)
                VALUES (?, ?, ?, ?, NULL, 0)
                ON CONFLICT(serial) DO UPDATE SET
                  name = CASE WHEN excluded.name != '' THEN excluded.name ELSE devices.name END,
                  manufacturer = CASE
                    WHEN excluded.manufacturer != '' THEN excluded.manufacturer
                    ELSE devices.manufacturer END,
                  model = CASE WHEN excluded.model != '' THEN excluded.model ELSE devices.model END
                """,
                (key, name or "", manufacturer or "", model or ""),
            )
    finally:
        conn.close()


def replace_device_listing(
    serial: str,
    files: Sequence[FileEntry] | Iterable[FileEntry],
    *,
    path: Path | None = None,
    source: str = "list",
) -> int:
    """Replace all device_files for *serial* from a full list_files snapshot.

    Marks ``list_complete=1``. Returns number of rows written.
    """
    key = device_serial_key(serial=serial)
    now = _utc_now()
    entries = list(files)
    conn, _ = _open(path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO devices (serial, name, manufacturer, model, last_listed_at, list_complete)
                VALUES (?, '', '', '', ?, 1)
                ON CONFLICT(serial) DO UPDATE SET
                  last_listed_at = excluded.last_listed_at,
                  list_complete = 1
                """,
                (key, now),
            )
            conn.execute("DELETE FROM device_files WHERE serial = ?", (key,))
            for e in entries:
                name = (e.name or "").strip()
                if not name:
                    continue
                guid = guid_from_remote_name(name)
                conn.execute(
                    """
                    INSERT INTO device_files (
                      serial, name, item_id, parent_id, storage_id, filesize,
                      filetype, guid, last_seen_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        name,
                        int(e.item_id or 0),
                        int(e.parent_id or 0),
                        int(e.storage_id or 0),
                        int(e.filesize or 0),
                        int(e.filetype or 0),
                        guid,
                        now,
                        source,
                    ),
                )
        logger.info(
            "Device index replace serial=%s count=%d source=%s",
            key,
            len(entries),
            source,
        )
        return len(entries)
    finally:
        conn.close()


def record_send(
    serial: str,
    *,
    remote_name: str,
    guid: str | None = None,
    item_id: int | None = None,
    parent_id: int = DEFAULT_MUSIC_FOLDER_ID,
    storage_id: int = DEFAULT_STORAGE_ID,
    filesize: int = 0,
    filetype: int = 0,
    path: Path | None = None,
) -> None:
    """Upsert one file after a successful transfer (no USB list)."""
    key = device_serial_key(serial=serial)
    name = (remote_name or "").strip()
    if not name:
        return
    g = normalize_guid_or_parse(guid, name)
    now = _utc_now()
    conn, _ = _open(path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO devices (serial, name, manufacturer, model, last_listed_at, list_complete)
                VALUES (?, '', '', '', NULL, 0)
                ON CONFLICT(serial) DO NOTHING
                """,
                (key,),
            )
            conn.execute(
                """
                INSERT INTO device_files (
                  serial, name, item_id, parent_id, storage_id, filesize,
                  filetype, guid, last_seen_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'send')
                ON CONFLICT(serial, name) DO UPDATE SET
                  item_id = CASE
                    WHEN excluded.item_id > 0 THEN excluded.item_id
                    ELSE device_files.item_id END,
                  parent_id = excluded.parent_id,
                  storage_id = excluded.storage_id,
                  filesize = CASE
                    WHEN excluded.filesize > 0 THEN excluded.filesize
                    ELSE device_files.filesize END,
                  filetype = CASE
                    WHEN excluded.filetype > 0 THEN excluded.filetype
                    ELSE device_files.filetype END,
                  guid = COALESCE(excluded.guid, device_files.guid),
                  last_seen_at = excluded.last_seen_at,
                  source = 'send'
                """,
                (
                    key,
                    name,
                    int(item_id or 0),
                    int(parent_id),
                    int(storage_id),
                    int(filesize or 0),
                    int(filetype or 0),
                    g,
                    now,
                ),
            )
    finally:
        conn.close()


def normalize_guid_or_parse(guid: str | None, name: str) -> str | None:
    if guid and is_track_guid(guid):
        return guid
    return guid_from_remote_name(name)


def remove_by_item_id(
    serial: str,
    item_id: int,
    *,
    path: Path | None = None,
) -> int:
    """Remove cache rows for MTP object id. Returns rows deleted."""
    key = device_serial_key(serial=serial)
    oid = int(item_id)
    if oid <= 0:
        return 0
    conn, _ = _open(path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM device_files WHERE serial = ? AND item_id = ?",
                (key, oid),
            )
            n = cur.rowcount if cur.rowcount is not None else 0
        if n:
            logger.debug("Device index remove item_id=%s serial=%s n=%s", oid, key, n)
        return int(n)
    finally:
        conn.close()


def remove_by_name(
    serial: str,
    name: str,
    *,
    path: Path | None = None,
) -> int:
    key = device_serial_key(serial=serial)
    n_name = (name or "").strip()
    if not n_name:
        return 0
    conn, _ = _open(path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM device_files WHERE serial = ? AND name = ?",
                (key, n_name),
            )
            return int(cur.rowcount or 0)
    finally:
        conn.close()


def remove_by_guid(
    serial: str,
    guid: str,
    *,
    path: Path | None = None,
) -> int:
    key = device_serial_key(serial=serial)
    if not is_track_guid(guid):
        return 0
    conn, _ = _open(path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM device_files WHERE serial = ? AND guid = ?",
                (key, guid),
            )
            return int(cur.rowcount or 0)
    finally:
        conn.close()


def guid_stems_on_device(
    serial: str,
    *,
    path: Path | None = None,
) -> set[str]:
    """GUID stems known to be on *serial* (for skip-if-present)."""
    key = device_serial_key(serial=serial)
    conn, _ = _open(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT guid FROM device_files "
            "WHERE serial = ? AND guid IS NOT NULL AND guid != ''",
            (key,),
        ).fetchall()
        return {str(r["guid"]) for r in rows if is_track_guid(str(r["guid"]))}
    finally:
        conn.close()


def list_cached_files(
    serial: str,
    *,
    path: Path | None = None,
) -> list[FileEntry]:
    """Return FileEntry list from cache (may be empty if never seeded)."""
    key = device_serial_key(serial=serial)
    conn, _ = _open(path)
    try:
        rows = conn.execute(
            "SELECT item_id, name, parent_id, storage_id, filesize, filetype "
            "FROM device_files WHERE serial = ? "
            "ORDER BY parent_id, name COLLATE NOCASE, item_id",
            (key,),
        ).fetchall()
        return [
            FileEntry(
                item_id=int(r["item_id"] or 0),
                name=str(r["name"] or ""),
                parent_id=int(r["parent_id"] or 0),
                storage_id=int(r["storage_id"] or 0),
                filesize=int(r["filesize"] or 0),
                filetype=int(r["filetype"] or 0),
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_cached_track_refs(
    serial: str,
    *,
    path: Path | None = None,
) -> list[DeviceTrackRef]:
    """Media-filtered track refs from cached files."""
    return track_refs_from_files(list_cached_files(serial, path=path))


def device_list_is_complete(
    serial: str,
    *,
    path: Path | None = None,
) -> bool:
    key = device_serial_key(serial=serial)
    conn, _ = _open(path)
    try:
        row = conn.execute(
            "SELECT list_complete FROM devices WHERE serial = ?",
            (key,),
        ).fetchone()
        return bool(row and int(row["list_complete"] or 0))
    finally:
        conn.close()


def file_count(
    serial: str,
    *,
    path: Path | None = None,
) -> int:
    key = device_serial_key(serial=serial)
    conn, _ = _open(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM device_files WHERE serial = ?",
            (key,),
        ).fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()
