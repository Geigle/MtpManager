"""Durable staged video encodes awaiting device sync.

Compressed outputs live under the app data dir (``staged_videos/``) with a
JSON manifest. Entries expire after one week; successful sync deletes them.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

STAGED_VIDEOS_DIRNAME = "staged_videos"
MANIFEST_FILENAME = "staged_videos.json"
STAGED_TTL = timedelta(days=7)
MANIFEST_VERSION = 1
STAGED_FILE_PREFIX = "STAGED_VIDEO_"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """UTC timestamp for manifest ``created_at`` / ``updated_at`` fields."""
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_iso() -> str:
    return utc_now_iso()


def _parse_iso(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class StagedVideoEntry:
    """One compressed video waiting to sync to the device."""

    id: str
    source_path: str
    staged_path: str
    parent_id: int
    created_at: str
    title: str = ""
    preferred_basename: str = ""
    guid: str = ""
    encoded: bool = False
    encode_skipped_compatible: bool = False
    preset_id: str | None = None
    resolution_id: str | None = None
    ignore_max_fps: bool = False

    def source_key(self) -> str:
        return os.path.normpath(self.source_path or "")

    def is_expired(self, *, now: datetime | None = None, ttl: timedelta = STAGED_TTL) -> bool:
        created = _parse_iso(self.created_at)
        if created is None:
            return True
        ref = now or _utc_now()
        return created + ttl <= ref

    def staged_exists(self) -> bool:
        return bool(self.staged_path) and os.path.isfile(self.staged_path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object | None) -> StagedVideoEntry | None:
        if not isinstance(raw, dict):
            return None
        known = {f.name for f in fields(cls)}
        data = {k: raw[k] for k in raw if k in known}
        sid = str(data.get("id") or "").strip()
        source = os.path.normpath(str(data.get("source_path") or "").strip())
        staged = str(data.get("staged_path") or "").strip()
        if not sid or not source or not staged:
            return None
        try:
            parent_id = int(data.get("parent_id") or 0)
        except (TypeError, ValueError):
            parent_id = 0
        if parent_id <= 0:
            return None
        created = str(data.get("created_at") or "").strip() or _utc_now_iso()
        return cls(
            id=sid,
            source_path=source,
            staged_path=staged,
            parent_id=parent_id,
            created_at=created,
            title=str(data.get("title") or "").strip(),
            preferred_basename=str(data.get("preferred_basename") or "").strip(),
            guid=str(data.get("guid") or "").strip(),
            encoded=bool(data.get("encoded")),
            encode_skipped_compatible=bool(data.get("encode_skipped_compatible")),
            preset_id=(str(data.get("preset_id") or "").strip() or None),
            resolution_id=(
                str(data.get("resolution_id") or "").strip().casefold() or None
            ),
            ignore_max_fps=bool(data.get("ignore_max_fps")),
        )


def staged_videos_dir(*, data_dir: Path | None = None) -> Path:
    """Directory for staged encode files + manifest."""
    base = data_dir if data_dir is not None else default_data_dir()
    path = base / STAGED_VIDEOS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(*, data_dir: Path | None = None) -> Path:
    return staged_videos_dir(data_dir=data_dir) / MANIFEST_FILENAME


def load_staged_videos(*, data_dir: Path | None = None) -> list[StagedVideoEntry]:
    """Load manifest entries (does not purge). Missing file → empty list."""
    path = manifest_path(data_dir=data_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("staged videos manifest unreadable: %s", exc)
        return []
    items = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: list[StagedVideoEntry] = []
    for row in items:
        entry = StagedVideoEntry.from_dict(row)
        if entry is not None:
            out.append(entry)
    return out


def save_staged_videos(
    entries: list[StagedVideoEntry], *, data_dir: Path | None = None
) -> Path:
    """Write manifest atomically."""
    dest = manifest_path(data_dir=data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": MANIFEST_VERSION,
        "updated_at": _utc_now_iso(),
        "entries": [e.to_dict() for e in entries],
    }
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def delete_staged_file(path: str | None) -> None:
    """Delete a staged encode file (prefix-guarded)."""
    if not path:
        return
    base = os.path.basename(path)
    if not base.startswith(STAGED_FILE_PREFIX):
        logger.debug("refusing to delete non-staged path %s", path)
        return
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as exc:
        logger.warning("could not delete staged video %s: %s", path, exc)


def remove_staged_entry(
    entry_id: str, *, data_dir: Path | None = None, delete_file: bool = True
) -> StagedVideoEntry | None:
    """Remove one entry from the manifest; optionally delete its file."""
    entries = load_staged_videos(data_dir=data_dir)
    kept: list[StagedVideoEntry] = []
    removed: StagedVideoEntry | None = None
    for e in entries:
        if e.id == entry_id:
            removed = e
            continue
        kept.append(e)
    if removed is None:
        return None
    save_staged_videos(kept, data_dir=data_dir)
    if delete_file:
        delete_staged_file(removed.staged_path)
    return removed


def find_staged_by_source(
    source_path: str, *, data_dir: Path | None = None
) -> StagedVideoEntry | None:
    """Return the staged entry for *source_path* if present and file exists."""
    key = os.path.normpath(source_path or "")
    if not key:
        return None
    for entry in load_staged_videos(data_dir=data_dir):
        if entry.source_key() == key and entry.staged_exists():
            return entry
    return None


def list_syncable_staged(*, data_dir: Path | None = None) -> list[StagedVideoEntry]:
    """Entries whose staged files still exist (after optional purge)."""
    purge_expired_staged_videos(data_dir=data_dir)
    return [e for e in load_staged_videos(data_dir=data_dir) if e.staged_exists()]


def new_staged_path(
    *,
    container: str = "avi",
    data_dir: Path | None = None,
    entry_id: str | None = None,
) -> tuple[str, str]:
    """Allocate ``(entry_id, absolute_staged_path)`` under the staged dir."""
    sid = (entry_id or uuid.uuid4().hex[:16]).strip() or uuid.uuid4().hex[:16]
    ext = (container or "avi").lstrip(".") or "avi"
    name = f"{STAGED_FILE_PREFIX}{sid}.{ext}"
    path = staged_videos_dir(data_dir=data_dir) / name
    return sid, str(path)


def upsert_staged_entry(
    entry: StagedVideoEntry, *, data_dir: Path | None = None
) -> StagedVideoEntry:
    """Insert or replace by source_path (one staged output per source)."""
    key = entry.source_key()
    entries = load_staged_videos(data_dir=data_dir)
    kept: list[StagedVideoEntry] = []
    for e in entries:
        if e.source_key() == key or e.id == entry.id:
            if e.staged_path != entry.staged_path:
                delete_staged_file(e.staged_path)
            continue
        kept.append(e)
    kept.append(entry)
    save_staged_videos(kept, data_dir=data_dir)
    return entry


def purge_expired_staged_videos(
    *, data_dir: Path | None = None, ttl: timedelta = STAGED_TTL
) -> list[StagedVideoEntry]:
    """Delete expired (or missing-file) entries. Returns removed entries."""
    now = _utc_now()
    entries = load_staged_videos(data_dir=data_dir)
    kept: list[StagedVideoEntry] = []
    removed: list[StagedVideoEntry] = []
    for e in entries:
        missing = not e.staged_exists()
        if missing or e.is_expired(now=now, ttl=ttl):
            removed.append(e)
            delete_staged_file(e.staged_path)
            continue
        kept.append(e)
    if removed:
        save_staged_videos(kept, data_dir=data_dir)
        logger.info(
            "purged %d staged video(s) (expired or missing); %d remain",
            len(removed),
            len(kept),
        )
    # Also remove orphan STAGED_VIDEO_* files not listed in the manifest.
    try:
        root = staged_videos_dir(data_dir=data_dir)
        known = {os.path.normpath(e.staged_path) for e in kept}
        for child in root.iterdir():
            if not child.is_file():
                continue
            if child.name == MANIFEST_FILENAME:
                continue
            if not child.name.startswith(STAGED_FILE_PREFIX):
                continue
            if os.path.normpath(str(child)) not in known:
                try:
                    child.unlink()
                except OSError:
                    pass
    except OSError:
        logger.debug("orphan staged cleanup failed", exc_info=True)
    return removed


def copy_or_link_to_staged(src: str, dest: str) -> str:
    """Copy *src* to *dest* (replace if present). Returns *dest*."""
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
    shutil.copy2(src, dest)
    return dest
