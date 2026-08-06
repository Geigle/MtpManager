"""Host podcast subscriptions + episodes in the library index SQLite DB.

# TODO(follow-up): OPML import/export
# TODO(follow-up): Device → Podcasts inventory browser
# TODO(follow-up): per-show schedule days override UI (column already exists)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, urlunparse

from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.library_index import _connect, _init_schema, index_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Podcast:
    id: int
    feed_url: str
    title: str = ""
    author: str = ""
    description: str = ""
    image_url: str = ""
    site_url: str = ""
    last_fetched_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    episode_count: int = 0
    # Automatic full-sync (Library → Podcast Settings + per-show override).
    auto_update: bool = True
    schedule_time: str = ""  # HH:MM override; empty = global
    schedule_days: str = ""  # reserved; empty = global
    auto_last_run_local_date: str = ""  # YYYY-MM-DD


@dataclass(frozen=True)
class PodcastEpisode:
    id: int
    podcast_id: int
    guid: str  # host 32-hex identity for device ObjectFileName
    feed_guid: str
    title: str = ""
    description: str = ""
    pub_date: str = ""  # release date (ISO-ish) from feed
    duration_sec: float = 0.0
    enclosure_url: str = ""
    enclosure_type: str = ""
    enclosure_bytes: int = 0
    local_path: str = ""
    episode_index: int = 0
    season: int = 0
    # True when the feed item has a video enclosure (may still prefer audio).
    is_video: bool = False
    video_enclosure_url: str = ""
    video_enclosure_type: str = ""
    created_at: str = ""
    updated_at: str = ""
    # When the enclosure was first fully downloaded (ISO UTC); empty if never.
    retrieved_at: str = ""
    # Set by full-sync host pass; cleared after successful device send / skip.
    pending_device_sync: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_feed_url(url: str) -> str:
    """Normalize a feed URL for uniqueness (scheme/host lower, strip junk)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme:
        parsed = urlparse("https://" + raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # Drop fragments; keep query (some hosts need it).
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def ensure_podcasts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS podcasts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          feed_url TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          author TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          image_url TEXT NOT NULL DEFAULT '',
          site_url TEXT NOT NULL DEFAULT '',
          last_fetched_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_feed_url
          ON podcasts(feed_url);

        CREATE TABLE IF NOT EXISTS podcast_episodes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          podcast_id INTEGER NOT NULL,
          guid TEXT NOT NULL,
          feed_guid TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          pub_date TEXT NOT NULL DEFAULT '',
          duration_sec REAL NOT NULL DEFAULT 0,
          enclosure_url TEXT NOT NULL DEFAULT '',
          enclosure_type TEXT NOT NULL DEFAULT '',
          enclosure_bytes INTEGER NOT NULL DEFAULT 0,
          local_path TEXT NOT NULL DEFAULT '',
          episode_index INTEGER NOT NULL DEFAULT 0,
          season INTEGER NOT NULL DEFAULT 0,
          is_video INTEGER NOT NULL DEFAULT 0,
          video_enclosure_url TEXT NOT NULL DEFAULT '',
          video_enclosure_type TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
          UNIQUE (podcast_id, feed_guid)
        );
        CREATE INDEX IF NOT EXISTS idx_podcast_episodes_podcast_pub
          ON podcast_episodes(podcast_id, pub_date DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_podcast_episodes_guid
          ON podcast_episodes(guid);
        """
    )
    _migrate_episode_video_columns(conn)
    _migrate_podcast_auto_columns(conn)
    _migrate_episode_retrieval_columns(conn)


def _migrate_episode_video_columns(conn: sqlite3.Connection) -> None:
    """Add video columns on DBs created before video-podcast support."""
    try:
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(podcast_episodes)").fetchall()
        }
    except sqlite3.Error:
        return
    alters: list[str] = []
    if "is_video" not in cols:
        alters.append(
            "ALTER TABLE podcast_episodes ADD COLUMN is_video INTEGER NOT NULL DEFAULT 0"
        )
    if "video_enclosure_url" not in cols:
        alters.append(
            "ALTER TABLE podcast_episodes ADD COLUMN "
            "video_enclosure_url TEXT NOT NULL DEFAULT ''"
        )
    if "video_enclosure_type" not in cols:
        alters.append(
            "ALTER TABLE podcast_episodes ADD COLUMN "
            "video_enclosure_type TEXT NOT NULL DEFAULT ''"
        )
    for sql in alters:
        try:
            conn.execute(sql)
        except sqlite3.Error as e:
            logger.debug("podcast episode column migrate skipped: %s", e)


def _migrate_podcast_auto_columns(conn: sqlite3.Connection) -> None:
    """Per-show auto-update schedule columns."""
    try:
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(podcasts)").fetchall()
        }
    except sqlite3.Error:
        return
    alters: list[str] = []
    if "auto_update" not in cols:
        alters.append(
            "ALTER TABLE podcasts ADD COLUMN auto_update INTEGER NOT NULL DEFAULT 1"
        )
    if "schedule_time" not in cols:
        alters.append(
            "ALTER TABLE podcasts ADD COLUMN schedule_time TEXT NOT NULL DEFAULT ''"
        )
    if "schedule_days" not in cols:
        alters.append(
            "ALTER TABLE podcasts ADD COLUMN schedule_days TEXT NOT NULL DEFAULT ''"
        )
    if "auto_last_run_local_date" not in cols:
        alters.append(
            "ALTER TABLE podcasts ADD COLUMN "
            "auto_last_run_local_date TEXT NOT NULL DEFAULT ''"
        )
    for sql in alters:
        try:
            conn.execute(sql)
        except sqlite3.Error as e:
            logger.debug("podcast auto column migrate skipped: %s", e)


def _migrate_episode_retrieval_columns(conn: sqlite3.Connection) -> None:
    """Retrieval stamp + pending device-sync flag."""
    try:
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(podcast_episodes)").fetchall()
        }
    except sqlite3.Error:
        return
    alters: list[str] = []
    if "retrieved_at" not in cols:
        alters.append(
            "ALTER TABLE podcast_episodes ADD COLUMN "
            "retrieved_at TEXT NOT NULL DEFAULT ''"
        )
    if "pending_device_sync" not in cols:
        alters.append(
            "ALTER TABLE podcast_episodes ADD COLUMN "
            "pending_device_sync INTEGER NOT NULL DEFAULT 0"
        )
    for sql in alters:
        try:
            conn.execute(sql)
        except sqlite3.Error as e:
            logger.debug("podcast retrieval column migrate skipped: %s", e)


def _open(path: Path | None = None) -> tuple[sqlite3.Connection, Path]:
    dest = path if path is not None else index_path()
    conn = _connect(dest)
    _init_schema(conn)
    ensure_podcasts_schema(conn)
    return conn, dest


def _podcast_from_row(row: sqlite3.Row, *, episode_count: int = 0) -> Podcast:
    keys = set(row.keys())
    auto_update = True
    if "auto_update" in keys:
        auto_update = bool(
            int(row["auto_update"] if row["auto_update"] is not None else 1)
        )
    schedule_time = (
        str(row["schedule_time"] or "") if "schedule_time" in keys else ""
    )
    schedule_days = (
        str(row["schedule_days"] or "") if "schedule_days" in keys else ""
    )
    auto_last = (
        str(row["auto_last_run_local_date"] or "")
        if "auto_last_run_local_date" in keys
        else ""
    )
    return Podcast(
        id=int(row["id"]),
        feed_url=str(row["feed_url"] or ""),
        title=str(row["title"] or ""),
        author=str(row["author"] or ""),
        description=str(row["description"] or ""),
        image_url=str(row["image_url"] or ""),
        site_url=str(row["site_url"] or ""),
        last_fetched_at=str(row["last_fetched_at"] or ""),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        episode_count=int(episode_count),
        auto_update=auto_update,
        schedule_time=schedule_time,
        schedule_days=schedule_days,
        auto_last_run_local_date=auto_last,
    )


def _episode_from_row(row: sqlite3.Row) -> PodcastEpisode:
    keys = set(row.keys())
    is_video = False
    if "is_video" in keys:
        is_video = bool(int(row["is_video"] or 0))
    video_url = str(row["video_enclosure_url"] or "") if "video_enclosure_url" in keys else ""
    video_type = (
        str(row["video_enclosure_type"] or "") if "video_enclosure_type" in keys else ""
    )
    retrieved_at = str(row["retrieved_at"] or "") if "retrieved_at" in keys else ""
    pending = False
    if "pending_device_sync" in keys:
        pending = bool(int(row["pending_device_sync"] or 0))
    return PodcastEpisode(
        id=int(row["id"]),
        podcast_id=int(row["podcast_id"]),
        guid=str(row["guid"] or ""),
        feed_guid=str(row["feed_guid"] or ""),
        title=str(row["title"] or ""),
        description=str(row["description"] or ""),
        pub_date=str(row["pub_date"] or ""),
        duration_sec=float(row["duration_sec"] or 0),
        enclosure_url=str(row["enclosure_url"] or ""),
        enclosure_type=str(row["enclosure_type"] or ""),
        enclosure_bytes=int(row["enclosure_bytes"] or 0),
        local_path=str(row["local_path"] or ""),
        episode_index=int(row["episode_index"] or 0),
        season=int(row["season"] or 0),
        is_video=is_video,
        video_enclosure_url=video_url,
        video_enclosure_type=video_type,
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        retrieved_at=retrieved_at,
        pending_device_sync=pending,
    )


def list_podcasts(*, path: Path | None = None) -> list[Podcast]:
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        rows = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM podcast_episodes e WHERE e.podcast_id = p.id)
                AS episode_count
            FROM podcasts p
            ORDER BY p.title COLLATE NOCASE, p.id
            """
        ).fetchall()
        return [
            _podcast_from_row(r, episode_count=int(r["episode_count"] or 0))
            for r in rows
        ]
    except sqlite3.Error as e:
        logger.warning("list_podcasts failed: %s", e)
        return []
    finally:
        if conn is not None:
            conn.close()


def get_podcast(podcast_id: int, *, path: Path | None = None) -> Podcast | None:
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        row = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM podcast_episodes e WHERE e.podcast_id = p.id)
                AS episode_count
            FROM podcasts p WHERE p.id = ?
            """,
            (int(podcast_id),),
        ).fetchone()
        if row is None:
            return None
        return _podcast_from_row(row, episode_count=int(row["episode_count"] or 0))
    except sqlite3.Error as e:
        logger.warning("get_podcast failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def get_podcast_by_feed_url(
    feed_url: str, *, path: Path | None = None
) -> Podcast | None:
    key = normalize_feed_url(feed_url)
    if not key:
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        row = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM podcast_episodes e WHERE e.podcast_id = p.id)
                AS episode_count
            FROM podcasts p WHERE p.feed_url = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return _podcast_from_row(row, episode_count=int(row["episode_count"] or 0))
    except sqlite3.Error as e:
        logger.warning("get_podcast_by_feed_url failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def create_or_update_podcast(
    *,
    feed_url: str,
    title: str = "",
    author: str = "",
    description: str = "",
    image_url: str = "",
    site_url: str = "",
    path: Path | None = None,
) -> Podcast:
    key = normalize_feed_url(feed_url)
    if not key:
        raise ValueError("Feed URL is required")
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            existing = conn.execute(
                "SELECT id FROM podcasts WHERE feed_url = ?", (key,)
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    """
                    INSERT INTO podcasts (
                      feed_url, title, author, description, image_url, site_url,
                      last_fetched_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        title or "",
                        author or "",
                        description or "",
                        image_url or "",
                        site_url or "",
                        now,
                        now,
                        now,
                    ),
                )
                pid = int(cur.lastrowid)
            else:
                pid = int(existing["id"])
                conn.execute(
                    """
                    UPDATE podcasts SET
                      title = CASE WHEN ? != '' THEN ? ELSE title END,
                      author = CASE WHEN ? != '' THEN ? ELSE author END,
                      description = CASE WHEN ? != '' THEN ? ELSE description END,
                      image_url = CASE WHEN ? != '' THEN ? ELSE image_url END,
                      site_url = CASE WHEN ? != '' THEN ? ELSE site_url END,
                      last_fetched_at = ?,
                      updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        title,
                        author,
                        author,
                        description,
                        description,
                        image_url,
                        image_url,
                        site_url,
                        site_url,
                        now,
                        now,
                        pid,
                    ),
                )
        pl = get_podcast(pid, path=path)
        if pl is None:
            raise RuntimeError("podcast row missing after upsert")
        return pl
    finally:
        if conn is not None:
            conn.close()


def delete_podcast(podcast_id: int, *, path: Path | None = None) -> bool:
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            cur = conn.execute(
                "DELETE FROM podcasts WHERE id = ?", (int(podcast_id),)
            )
            return int(cur.rowcount or 0) > 0
    except sqlite3.Error as e:
        logger.warning("delete_podcast failed: %s", e)
        return False
    finally:
        if conn is not None:
            conn.close()


def list_episodes(
    podcast_id: int,
    *,
    limit: int | None = None,
    path: Path | None = None,
) -> list[PodcastEpisode]:
    """Episodes newest-first (pub_date DESC, then id DESC)."""
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        sql = (
            "SELECT * FROM podcast_episodes WHERE podcast_id = ? "
            "ORDER BY pub_date DESC, id DESC"
        )
        params: list = [int(podcast_id)]
        if limit is not None and int(limit) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [_episode_from_row(r) for r in rows]
    except sqlite3.Error as e:
        logger.warning("list_episodes failed: %s", e)
        return []
    finally:
        if conn is not None:
            conn.close()


def get_episode(episode_id: int, *, path: Path | None = None) -> PodcastEpisode | None:
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        row = conn.execute(
            "SELECT * FROM podcast_episodes WHERE id = ?", (int(episode_id),)
        ).fetchone()
        return _episode_from_row(row) if row is not None else None
    except sqlite3.Error as e:
        logger.warning("get_episode failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def get_episode_by_guid(
    guid: str, *, path: Path | None = None
) -> PodcastEpisode | None:
    """Lookup episode by host 32-hex GUID (ObjectFileName stem for audio)."""
    g = (guid or "").strip().lower()
    if not g or not is_track_guid(g):
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        row = conn.execute(
            "SELECT * FROM podcast_episodes WHERE guid = ?", (g,)
        ).fetchone()
        return _episode_from_row(row) if row is not None else None
    except sqlite3.Error as e:
        logger.warning("get_episode_by_guid failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def known_feed_guids(
    podcast_id: int, *, path: Path | None = None
) -> set[str]:
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        rows = conn.execute(
            "SELECT feed_guid FROM podcast_episodes WHERE podcast_id = ?",
            (int(podcast_id),),
        ).fetchall()
        return {str(r["feed_guid"] or "") for r in rows if r["feed_guid"]}
    except sqlite3.Error as e:
        logger.warning("known_feed_guids failed: %s", e)
        return set()
    finally:
        if conn is not None:
            conn.close()


def upsert_episodes(
    podcast_id: int,
    episodes: Iterable[dict],
    *,
    path: Path | None = None,
) -> int:
    """Insert new episodes (by feed_guid); do not overwrite existing local_path.

    Each dict may include: feed_guid, title, description, pub_date, duration_sec,
    enclosure_url, enclosure_type, enclosure_bytes, episode_index, season,
    is_video, video_enclosure_url, video_enclosure_type.
    Returns number of newly inserted rows.
    """
    pid = int(podcast_id)
    now = _utc_now()
    inserted = 0
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            for raw in episodes:
                feed_guid = str(raw.get("feed_guid") or "").strip()
                if not feed_guid:
                    continue
                is_video = 1 if raw.get("is_video") else 0
                video_url = str(raw.get("video_enclosure_url") or "")
                video_type = str(raw.get("video_enclosure_type") or "")
                existing = conn.execute(
                    "SELECT id, guid FROM podcast_episodes "
                    "WHERE podcast_id = ? AND feed_guid = ?",
                    (pid, feed_guid),
                ).fetchone()
                if existing is not None:
                    # Refresh metadata only; keep guid + local_path.
                    conn.execute(
                        """
                        UPDATE podcast_episodes SET
                          title = CASE WHEN ? != '' THEN ? ELSE title END,
                          description = CASE WHEN ? != '' THEN ? ELSE description END,
                          pub_date = CASE WHEN ? != '' THEN ? ELSE pub_date END,
                          duration_sec = CASE WHEN ? > 0 THEN ? ELSE duration_sec END,
                          enclosure_url = CASE WHEN ? != '' THEN ? ELSE enclosure_url END,
                          enclosure_type = CASE WHEN ? != '' THEN ? ELSE enclosure_type END,
                          enclosure_bytes = CASE WHEN ? > 0 THEN ? ELSE enclosure_bytes END,
                          episode_index = CASE WHEN ? > 0 THEN ? ELSE episode_index END,
                          season = CASE WHEN ? > 0 THEN ? ELSE season END,
                          is_video = ?,
                          video_enclosure_url = CASE WHEN ? != '' THEN ? ELSE video_enclosure_url END,
                          video_enclosure_type = CASE WHEN ? != '' THEN ? ELSE video_enclosure_type END,
                          updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            raw.get("title") or "",
                            raw.get("title") or "",
                            raw.get("description") or "",
                            raw.get("description") or "",
                            raw.get("pub_date") or "",
                            raw.get("pub_date") or "",
                            float(raw.get("duration_sec") or 0),
                            float(raw.get("duration_sec") or 0),
                            raw.get("enclosure_url") or "",
                            raw.get("enclosure_url") or "",
                            raw.get("enclosure_type") or "",
                            raw.get("enclosure_type") or "",
                            int(raw.get("enclosure_bytes") or 0),
                            int(raw.get("enclosure_bytes") or 0),
                            int(raw.get("episode_index") or 0),
                            int(raw.get("episode_index") or 0),
                            int(raw.get("season") or 0),
                            int(raw.get("season") or 0),
                            is_video,
                            video_url,
                            video_url,
                            video_type,
                            video_type,
                            now,
                            int(existing["id"]),
                        ),
                    )
                    continue
                host_guid = new_track_guid()
                conn.execute(
                    """
                    INSERT INTO podcast_episodes (
                      podcast_id, guid, feed_guid, title, description, pub_date,
                      duration_sec, enclosure_url, enclosure_type, enclosure_bytes,
                      local_path, episode_index, season,
                      is_video, video_enclosure_url, video_enclosure_type,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        host_guid,
                        feed_guid,
                        str(raw.get("title") or ""),
                        str(raw.get("description") or ""),
                        str(raw.get("pub_date") or ""),
                        float(raw.get("duration_sec") or 0),
                        str(raw.get("enclosure_url") or ""),
                        str(raw.get("enclosure_type") or ""),
                        int(raw.get("enclosure_bytes") or 0),
                        int(raw.get("episode_index") or 0),
                        int(raw.get("season") or 0),
                        is_video,
                        video_url,
                        video_type,
                        now,
                        now,
                    ),
                )
                inserted += 1
            conn.execute(
                "UPDATE podcasts SET last_fetched_at = ?, updated_at = ? WHERE id = ?",
                (now, now, pid),
            )
        return inserted
    finally:
        if conn is not None:
            conn.close()


def set_episode_local_path(
    episode_id: int,
    local_path: str,
    *,
    path: Path | None = None,
    stamp_retrieved: bool = True,
    mark_pending_device_sync: bool | None = None,
) -> None:
    """Update local_path; optionally stamp first retrieval and pending sync.

    *stamp_retrieved*: when True and path is non-empty, set ``retrieved_at``
    only if it is currently empty (idempotent).
    *mark_pending_device_sync*: True/False set the flag; None leaves it alone.
    """
    now = _utc_now()
    dest = (local_path or "").strip()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            if dest and stamp_retrieved:
                conn.execute(
                    """
                    UPDATE podcast_episodes SET
                      local_path = ?,
                      updated_at = ?,
                      retrieved_at = CASE
                        WHEN retrieved_at = '' OR retrieved_at IS NULL
                        THEN ? ELSE retrieved_at END
                    WHERE id = ?
                    """,
                    (dest, now, now, int(episode_id)),
                )
            else:
                conn.execute(
                    "UPDATE podcast_episodes SET local_path = ?, updated_at = ? "
                    "WHERE id = ?",
                    (dest, now, int(episode_id)),
                )
            if mark_pending_device_sync is not None:
                conn.execute(
                    "UPDATE podcast_episodes SET pending_device_sync = ?, "
                    "updated_at = ? WHERE id = ?",
                    (1 if mark_pending_device_sync else 0, now, int(episode_id)),
                )
    finally:
        if conn is not None:
            conn.close()


def set_episode_pending_device_sync(
    episode_id: int,
    pending: bool,
    *,
    path: Path | None = None,
) -> None:
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            conn.execute(
                "UPDATE podcast_episodes SET pending_device_sync = ?, updated_at = ? "
                "WHERE id = ?",
                (1 if pending else 0, now, int(episode_id)),
            )
    finally:
        if conn is not None:
            conn.close()


def clear_pending_device_sync_for_ids(
    episode_ids: Iterable[int],
    *,
    path: Path | None = None,
) -> int:
    ids = [int(i) for i in episode_ids if int(i) > 0]
    if not ids:
        return 0
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE podcast_episodes SET pending_device_sync = 0, "
                f"updated_at = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )
            return int(cur.rowcount or 0)
    except sqlite3.Error as e:
        logger.warning("clear_pending_device_sync_for_ids failed: %s", e)
        return 0
    finally:
        if conn is not None:
            conn.close()


def list_pending_device_sync_episodes(
    *,
    path: Path | None = None,
) -> list[PodcastEpisode]:
    """Episodes marked pending with a non-empty local_path."""
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        rows = conn.execute(
            """
            SELECT * FROM podcast_episodes
            WHERE pending_device_sync = 1 AND local_path != ''
            ORDER BY retrieved_at ASC, pub_date DESC, id ASC
            """
        ).fetchall()
        return [_episode_from_row(r) for r in rows]
    except sqlite3.Error as e:
        logger.warning("list_pending_device_sync_episodes failed: %s", e)
        return []
    finally:
        if conn is not None:
            conn.close()


def set_podcast_auto_settings(
    podcast_id: int,
    *,
    auto_update: bool | None = None,
    schedule_time: str | None = None,
    schedule_days: str | None = None,
    path: Path | None = None,
) -> Podcast | None:
    """Update per-show auto-update settings. None fields are left unchanged."""
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            row = conn.execute(
                "SELECT * FROM podcasts WHERE id = ?", (int(podcast_id),)
            ).fetchone()
            if row is None:
                return None
            keys = set(row.keys())
            au = (
                (1 if auto_update else 0)
                if auto_update is not None
                else int(row["auto_update"] if "auto_update" in keys else 1)
            )
            st = (
                str(schedule_time or "").strip()
                if schedule_time is not None
                else str(row["schedule_time"] if "schedule_time" in keys else "")
            )
            sd = (
                str(schedule_days or "").strip()
                if schedule_days is not None
                else str(row["schedule_days"] if "schedule_days" in keys else "")
            )
            conn.execute(
                """
                UPDATE podcasts SET
                  auto_update = ?, schedule_time = ?, schedule_days = ?,
                  updated_at = ?
                WHERE id = ?
                """,
                (au, st, sd, now, int(podcast_id)),
            )
        return get_podcast(podcast_id, path=path)
    except sqlite3.Error as e:
        logger.warning("set_podcast_auto_settings failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def set_podcast_auto_last_run(
    podcast_id: int,
    local_date: str,
    *,
    path: Path | None = None,
) -> None:
    """Record last successful schedule pass local date (YYYY-MM-DD)."""
    now = _utc_now()
    day = str(local_date or "").strip()[:10]
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            conn.execute(
                "UPDATE podcasts SET auto_last_run_local_date = ?, updated_at = ? "
                "WHERE id = ?",
                (day, now, int(podcast_id)),
            )
    except sqlite3.Error as e:
        logger.warning("set_podcast_auto_last_run failed: %s", e)
    finally:
        if conn is not None:
            conn.close()


def latest_episode(
    podcast_id: int, *, path: Path | None = None
) -> PodcastEpisode | None:
    eps = list_episodes(podcast_id, limit=1, path=path)
    return eps[0] if eps else None


def episode_cache_dir(
    podcast_id: int, *, data_dir: Path | None = None
) -> Path:
    """Directory for downloaded enclosures for one show."""
    from mtpmanager.infra.app_paths import default_data_dir

    base = data_dir if data_dir is not None else default_data_dir()
    dest = base / "podcasts" / str(int(podcast_id))
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def podcasts_cache_root(*, data_dir: Path | None = None) -> Path:
    """Root directory for all podcast downloads (``…/podcasts/``)."""
    from mtpmanager.infra.app_paths import default_data_dir

    base = data_dir if data_dir is not None else default_data_dir()
    return base / "podcasts"


def clear_all_episode_local_paths(*, path: Path | None = None) -> int:
    """Set every episode ``local_path`` to empty. Returns rows updated."""
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _ = _open(path)
        with conn:
            cur = conn.execute(
                "UPDATE podcast_episodes SET local_path = '', updated_at = ? "
                "WHERE local_path != ''",
                (now,),
            )
            return int(cur.rowcount or 0)
    except sqlite3.Error as e:
        logger.warning("clear_all_episode_local_paths failed: %s", e)
        return 0
    finally:
        if conn is not None:
            conn.close()
