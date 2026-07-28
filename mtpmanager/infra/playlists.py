"""Host playlists stored as M3U text in the library index SQLite DB."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_m3u import (
    PlaylistEntry,
    append_entries,
    empty_m3u,
    entry_from_track,
    parse_m3u,
    remove_paths,
    serialize_m3u,
)
from mtpmanager.infra.library_index import (
    _connect,
    _init_schema,
    _track_from_row,
    index_path,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaylistInfo:
    """Playlist header (no full M3U body)."""

    id: int
    name: str
    updated_at: str = ""
    track_count: int = 0


@dataclass(frozen=True)
class Playlist:
    """Full playlist row including M3U body."""

    id: int
    name: str
    m3u_text: str
    created_at: str = ""
    updated_at: str = ""

    def entries(self) -> list[PlaylistEntry]:
        return parse_m3u(self.m3u_text)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_playlists_schema(conn: sqlite3.Connection) -> None:
    """Create playlists table if missing (safe to call repeatedly)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS playlists (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL COLLATE NOCASE,
          m3u_text TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_playlists_name_unique
          ON playlists(name COLLATE NOCASE);
        """
    )


def _open(path: Path | None = None) -> tuple[sqlite3.Connection, Path]:
    dest = path if path is not None else index_path()
    conn = _connect(dest)
    _init_schema(conn)
    ensure_playlists_schema(conn)
    return conn, dest


def _count_entries(m3u_text: str) -> int:
    return len(parse_m3u(m3u_text or ""))


def list_playlists(*, path: Path | None = None) -> list[PlaylistInfo]:
    """Return playlists ordered by name (case-insensitive)."""
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        rows = conn.execute(
            "SELECT id, name, m3u_text, updated_at FROM playlists "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()
        out: list[PlaylistInfo] = []
        for r in rows:
            text = str(r["m3u_text"] or "")
            out.append(
                PlaylistInfo(
                    id=int(r["id"]),
                    name=str(r["name"] or ""),
                    updated_at=str(r["updated_at"] or ""),
                    track_count=_count_entries(text),
                )
            )
        return out
    except sqlite3.Error as e:
        logger.warning("list_playlists failed: %s", e)
        return []
    finally:
        if conn is not None:
            conn.close()


def get_playlist(playlist_id: int, *, path: Path | None = None) -> Playlist | None:
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        row = conn.execute(
            "SELECT id, name, m3u_text, created_at, updated_at "
            "FROM playlists WHERE id = ?",
            (int(playlist_id),),
        ).fetchone()
        if row is None:
            return None
        return Playlist(
            id=int(row["id"]),
            name=str(row["name"] or ""),
            m3u_text=str(row["m3u_text"] or empty_m3u()),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )
    except sqlite3.Error as e:
        logger.warning("get_playlist failed: %s", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def create_playlist(name: str, *, path: Path | None = None) -> Playlist:
    """Create an empty playlist. Raises ValueError on bad/duplicate name."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Playlist name is required")
    now = _utc_now()
    body = empty_m3u()
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        with conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO playlists (name, m3u_text, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (clean, body, now, now),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"A playlist named {clean!r} already exists") from e
            pid = int(cur.lastrowid)
        return Playlist(
            id=pid, name=clean, m3u_text=body, created_at=now, updated_at=now
        )
    finally:
        if conn is not None:
            conn.close()


def rename_playlist(
    playlist_id: int, new_name: str, *, path: Path | None = None
) -> Playlist:
    clean = (new_name or "").strip()
    if not clean:
        raise ValueError("Playlist name is required")
    now = _utc_now()
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        with conn:
            try:
                cur = conn.execute(
                    "UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
                    (clean, now, int(playlist_id)),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"A playlist named {clean!r} already exists") from e
            if cur.rowcount == 0:
                raise ValueError(f"Playlist id {playlist_id} not found")
        pl = get_playlist(playlist_id, path=path)
        if pl is None:
            raise ValueError(f"Playlist id {playlist_id} not found")
        return pl
    finally:
        if conn is not None:
            conn.close()


def delete_playlist(playlist_id: int, *, path: Path | None = None) -> bool:
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        with conn:
            cur = conn.execute(
                "DELETE FROM playlists WHERE id = ?", (int(playlist_id),)
            )
            return cur.rowcount > 0
    except sqlite3.Error as e:
        logger.warning("delete_playlist failed: %s", e)
        return False
    finally:
        if conn is not None:
            conn.close()


def set_playlist_m3u(
    playlist_id: int, m3u_text: str, *, path: Path | None = None
) -> Playlist:
    now = _utc_now()
    text = m3u_text if m3u_text is not None else empty_m3u()
    if not text.strip():
        text = empty_m3u()
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        with conn:
            cur = conn.execute(
                "UPDATE playlists SET m3u_text = ?, updated_at = ? WHERE id = ?",
                (text, now, int(playlist_id)),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Playlist id {playlist_id} not found")
        pl = get_playlist(playlist_id, path=path)
        if pl is None:
            raise ValueError(f"Playlist id {playlist_id} not found")
        return pl
    finally:
        if conn is not None:
            conn.close()


def append_tracks_to_playlist(
    playlist_id: int,
    tracks: Iterable[Track],
    *,
    skip_existing: bool = True,
    path: Path | None = None,
) -> Playlist:
    """Append library tracks to a playlist; returns updated playlist."""
    pl = get_playlist(playlist_id, path=path)
    if pl is None:
        raise ValueError(f"Playlist id {playlist_id} not found")
    entries = [entry_from_track(t) for t in tracks if t and t.path]
    new_text = append_entries(
        pl.m3u_text, entries, skip_existing=skip_existing
    )
    return set_playlist_m3u(playlist_id, new_text, path=path)


def remove_paths_from_playlist(
    playlist_id: int,
    paths: Iterable[str],
    *,
    path: Path | None = None,
) -> Playlist:
    pl = get_playlist(playlist_id, path=path)
    if pl is None:
        raise ValueError(f"Playlist id {playlist_id} not found")
    new_text = remove_paths(pl.m3u_text, list(paths))
    return set_playlist_m3u(playlist_id, new_text, path=path)


def resolve_playlist_tracks(
    playlist: Playlist,
    *,
    path: Path | None = None,
) -> list[Track]:
    """Resolve M3U paths to Track objects using the library index when possible.

    Missing index rows become placeholder tracks (path + EXTINF tags).
    """
    entries = playlist.entries()
    if not entries:
        return []

    by_path: dict[str, Track] = {}
    conn: sqlite3.Connection | None = None
    try:
        conn, _dest = _open(path)
        for e in entries:
            p = os.path.normpath(e.path)
            if p in by_path:
                continue
            row = conn.execute(
                "SELECT * FROM tracks WHERE path = ?", (p,)
            ).fetchone()
            if row is not None:
                by_path[p] = _track_from_row(row)
    except sqlite3.Error as e:
        logger.warning("resolve_playlist_tracks index lookup failed: %s", e)
    finally:
        if conn is not None:
            conn.close()

    out: list[Track] = []
    for e in entries:
        p = os.path.normpath(e.path)
        hit = by_path.get(p)
        if hit is not None:
            out.append(hit)
            continue
        meta = TrackMetadata(
            artist=e.artist or "Unknown Artist",
            albumartist=e.artist or "Unknown Artist",
            title=e.title or (os.path.basename(p) or "Unknown Title"),
            length_sec=float(e.duration_sec) if e.duration_sec > 0 else 0.0,
        )
        out.append(Track(path=p, meta=meta))
    return out
