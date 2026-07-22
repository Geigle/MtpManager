"""Durable on-device file inventory in the library SQLite DB.

Full ``list_files`` / get_filelisting is expensive and can destabilize ZEN
sessions. Seed once on connect (or explicit Refresh), then maintain rows on
successful send / delete. Skip-if-present and List Files/Tracks read this
cache — not a live USB walk every sync.

Multiple devices: inventory is keyed by MTP serial when available, else a
stable fingerprint of manufacturer/model/friendly name. Never share one
``default`` bucket across two plugged players.
"""

from __future__ import annotations

import hashlib
import logging
import re
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
_PLACEHOLDER_SERIALS = frozenset(
    {
        "",
        "default",
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "0",
    }
)

# device_files PK is (serial, item_id). item_id > 0 is real MTP object id;
# item_id < 0 is synthetic (send without object id / nameless collision).
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
  item_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  parent_id INTEGER NOT NULL DEFAULT 100,
  storage_id INTEGER NOT NULL DEFAULT 65537,
  filesize INTEGER NOT NULL DEFAULT 0,
  filetype INTEGER NOT NULL DEFAULT 0,
  guid TEXT,
  last_seen_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'list',
  PRIMARY KEY (serial, item_id),
  FOREIGN KEY (serial) REFERENCES devices(serial) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_files_guid
  ON device_files(serial, guid);
CREATE INDEX IF NOT EXISTS idx_device_files_name
  ON device_files(serial, name);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_placeholder_serial(value: str | None) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return text.casefold() in _PLACEHOLDER_SERIALS


def _fingerprint_key(manufacturer: str, model: str, name: str) -> str:
    """Stable key when MTP serial is missing (common on some Creative firmware)."""
    parts = [
        (manufacturer or "").strip().casefold(),
        (model or "").strip().casefold(),
        (name or "").strip().casefold(),
    ]
    raw = "|".join(parts)
    if not any(parts):
        return DEFAULT_SERIAL
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    # Human-readable prefix for logs (sanitized model).
    label = re.sub(r"[^a-zA-Z0-9._-]+", "_", (model or name or "device").strip())[:24]
    return f"fp:{label}:{digest}"


def device_serial_key(
    info: DeviceInfo | None = None,
    *,
    serial: str | None = None,
) -> str:
    """Stable key for a physical device.

    Preference order:
    1. Explicit *serial* argument (when not a placeholder)
    2. ``info.serial`` from MTP
    3. Fingerprint of manufacturer + model + friendly name
    4. ``default`` only when nothing else is known
    """
    if serial is not None and not _is_placeholder_serial(serial):
        # Already a fingerprint or real serial — keep as-is.
        return str(serial).strip()
    if info is not None:
        if not _is_placeholder_serial(info.serial):
            return str(info.serial).strip()
        return _fingerprint_key(info.manufacturer, info.model, info.name)
    return DEFAULT_SERIAL


def synthetic_item_id(name: str, parent_id: int = DEFAULT_MUSIC_FOLDER_ID) -> int:
    """Negative id for rows without a known MTP object id (unique per name/parent)."""
    raw = f"{int(parent_id)}\0{(name or '').strip()}".encode("utf-8")
    h = int(hashlib.md5(raw).hexdigest()[:8], 16)
    val = -abs(h)
    return val if val != 0 else -1


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
    _migrate_device_files_pk(conn)
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if ver < 3:
        conn.execute("PRAGMA user_version = 3")


def _table_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # pk ordinal > 0 means part of primary key
    keyed = [(int(r["pk"]), str(r["name"])) for r in rows if int(r["pk"] or 0) > 0]
    keyed.sort()
    return [name for _, name in keyed]


def _migrate_device_files_pk(conn: sqlite3.Connection) -> None:
    """Recreate device_files if still on (serial, name) PK from schema v2."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='device_files'"
    ).fetchone()
    if row is None:
        return
    pk = _table_pk_columns(conn, "device_files")
    if pk == ["serial", "item_id"]:
        return
    logger.info(
        "Migrating device_files PK %s → (serial, item_id)",
        pk or "(none)",
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS device_files_v3 (
          serial TEXT NOT NULL,
          item_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          parent_id INTEGER NOT NULL DEFAULT 100,
          storage_id INTEGER NOT NULL DEFAULT 65537,
          filesize INTEGER NOT NULL DEFAULT 0,
          filetype INTEGER NOT NULL DEFAULT 0,
          guid TEXT,
          last_seen_at TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'list',
          PRIMARY KEY (serial, item_id),
          FOREIGN KEY (serial) REFERENCES devices(serial) ON DELETE CASCADE
        );
        """
    )
    old = conn.execute("SELECT * FROM device_files").fetchall()
    seen: set[tuple[str, int]] = set()
    for r in old:
        serial = str(r["serial"] or DEFAULT_SERIAL)
        name = str(r["name"] or "").strip()
        if not name:
            continue
        oid = int(r["item_id"] or 0)
        if oid <= 0:
            oid = synthetic_item_id(name, int(r["parent_id"] or DEFAULT_MUSIC_FOLDER_ID))
        key = (serial, oid)
        if key in seen:
            # Collision: force unique synthetic
            oid = synthetic_item_id(f"{name}#{r['item_id']}", int(r["parent_id"] or 0))
            key = (serial, oid)
        seen.add(key)
        conn.execute(
            """
            INSERT OR REPLACE INTO device_files_v3 (
              serial, item_id, name, parent_id, storage_id, filesize,
              filetype, guid, last_seen_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                serial,
                oid,
                name,
                int(r["parent_id"] or 0),
                int(r["storage_id"] or 0),
                int(r["filesize"] or 0),
                int(r["filetype"] or 0),
                r["guid"],
                r["last_seen_at"] or _utc_now(),
                r["source"] or "list",
            ),
        )
    conn.execute("DROP TABLE device_files")
    conn.execute("ALTER TABLE device_files_v3 RENAME TO device_files")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_files_guid "
        "ON device_files(serial, guid)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_files_name "
        "ON device_files(serial, name)"
    )


def _migrate_legacy_device_objects(conn: sqlite3.Connection) -> None:
    """Copy schema-v1 device_objects into device_files under default serial."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='device_objects'"
    ).fetchone()
    if row is None:
        return
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
        oid = int(r["item_id"] or 0)
        if oid <= 0:
            oid = synthetic_item_id(name, int(r["parent_id"] or DEFAULT_MUSIC_FOLDER_ID))
        conn.execute(
            """
            INSERT INTO device_files (
              serial, item_id, name, parent_id, storage_id, filesize, filetype,
              guid, last_seen_at, source
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 'send')
            ON CONFLICT(serial, item_id) DO UPDATE SET
              name = excluded.name,
              parent_id = excluded.parent_id,
              storage_id = excluded.storage_id,
              guid = COALESCE(excluded.guid, device_files.guid),
              last_seen_at = excluded.last_seen_at
            """,
            (
                DEFAULT_SERIAL,
                oid,
                name,
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


def _dedupe_listing(entries: Sequence[FileEntry]) -> list[FileEntry]:
    """One row per (serial-local) item_id; fall back to parent+name.

    MTP listings can repeat basenames under different folders (or rarely the
    same parent). PK is (serial, item_id), so we key on positive item_id.
    """
    by_id: dict[int, FileEntry] = {}
    no_id: dict[tuple[int, str], FileEntry] = {}
    for e in entries:
        name = (e.name or "").strip()
        if not name:
            continue
        oid = int(e.item_id or 0)
        if oid > 0:
            # Prefer larger filesize on collision (more complete metadata).
            prev = by_id.get(oid)
            if prev is None or int(e.filesize or 0) >= int(prev.filesize or 0):
                by_id[oid] = e
        else:
            key = (int(e.parent_id or 0), name.casefold())
            no_id[key] = e
    out = list(by_id.values())
    out.extend(no_id.values())
    return out


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
    raw_list = list(files)
    entries = _dedupe_listing(raw_list)
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
            written = 0
            for e in entries:
                name = (e.name or "").strip()
                if not name:
                    continue
                oid = int(e.item_id or 0)
                if oid <= 0:
                    oid = synthetic_item_id(name, int(e.parent_id or 0))
                guid = guid_from_remote_name(name)
                conn.execute(
                    """
                    INSERT INTO device_files (
                      serial, item_id, name, parent_id, storage_id, filesize,
                      filetype, guid, last_seen_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        oid,
                        name,
                        int(e.parent_id or 0),
                        int(e.storage_id or 0),
                        int(e.filesize or 0),
                        int(e.filetype or 0),
                        guid,
                        now,
                        source,
                    ),
                )
                written += 1
        logger.info(
            "Device index replace serial=%s count=%d (from %d listed) source=%s",
            key,
            written,
            len(raw_list),
            source,
        )
        return written
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
    oid = int(item_id or 0)
    if oid <= 0:
        oid = synthetic_item_id(name, int(parent_id))
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
            # Also drop any prior row for this GUID/name so skip set stays clean.
            if g:
                conn.execute(
                    "DELETE FROM device_files WHERE serial = ? AND guid = ? AND item_id != ?",
                    (key, g, oid),
                )
            conn.execute(
                """
                INSERT INTO device_files (
                  serial, item_id, name, parent_id, storage_id, filesize,
                  filetype, guid, last_seen_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'send')
                ON CONFLICT(serial, item_id) DO UPDATE SET
                  name = excluded.name,
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
                    oid,
                    name,
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
    if oid == 0:
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


def list_known_devices(*, path: Path | None = None) -> list[dict[str, object]]:
    """Return known device rows for diagnostics / future picker UI."""
    conn, _ = _open(path)
    try:
        rows = conn.execute(
            "SELECT serial, name, manufacturer, model, last_listed_at, list_complete "
            "FROM devices ORDER BY (last_listed_at IS NULL), last_listed_at DESC, serial"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
