"""Durable record of a multi-track sync job for resume-after-failure."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

SYNC_JOB_FILENAME = "sync_job.json"
SYNC_JOB_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SyncJobState:
    """Scope + progress for one multi-track sync.

    *paths* is the ordered plan for the whole job. *next_index* is the first
    path not yet successfully sent (so resume retries a failure and skips
    completed tracks).
    """

    version: int = SYNC_JOB_VERSION
    kind: str = "batch"
    label: str = ""
    target_format: str = "mp3"
    mode: str = "experimental"
    paths: list[str] = field(default_factory=list)
    next_index: int = 0
    status: str = "running"
    last_error: str = ""
    last_failed_path: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def total(self) -> int:
        return len(self.paths)

    @property
    def succeeded(self) -> int:
        return max(0, min(self.next_index, self.total))

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.succeeded)

    def is_resumable(self) -> bool:
        if self.status not in ("failed", "cancelled", "running"):
            return False
        return 0 <= self.next_index < self.total

    def remaining_paths(self) -> list[str]:
        if self.next_index <= 0:
            return list(self.paths)
        if self.next_index >= self.total:
            return []
        return list(self.paths[self.next_index :])

    def append_paths(self, paths: list[str]) -> list[str]:
        """Append unique paths to the plan (for mid-job queue growth).

        Returns the paths that were actually added.
        """
        known = set(self.paths)
        added: list[str] = []
        for p in paths:
            path = (p or "").strip()
            if not path or path in known:
                continue
            self.paths.append(path)
            known.add(path)
            added.append(path)
        if added:
            self.updated_at = _utc_now()
            if self.status == "completed":
                # Queue grew after drain thought it was done — keep running.
                self.status = "running"
        return added

    def mark_path_done(self, path: str) -> bool:
        """Advance *next_index* past *path* if it is the current head (or earlier).

        Returns True if state changed.
        """
        if not path or not self.paths:
            return False
        try:
            idx = self.paths.index(path)
        except ValueError:
            return False
        # Only move forward; ignore out-of-order / duplicate notifications.
        if idx < self.next_index:
            return False
        if idx > self.next_index:
            # Track completed out of order — still advance through it only if
            # everything before is already done (idx == next_index is normal).
            # If we only hear about later tracks, still set next to idx+1 only
            # when idx == next_index. Sequential pipeline always matches.
            if idx != self.next_index:
                # Allow advancing when this is the expected next item only.
                return False
        self.next_index = idx + 1
        self.updated_at = _utc_now()
        self.last_failed_path = ""
        self.last_error = ""
        return True

    def mark_path_failed(self, path: str, error: str = "") -> None:
        """Record failure at *path*; resume will retry this path.

        Never advances *next_index* past unsent work (e.g. dual-slot prep of
        a later track reporting failed on cancel must not skip the current head).
        """
        if path:
            try:
                idx = self.paths.index(path)
            except ValueError:
                idx = self.next_index
            if idx <= self.next_index:
                self.next_index = max(0, min(idx, self.total))
                self.last_failed_path = path
            else:
                # Failure notification for a not-yet-reached path — keep head.
                if not self.last_failed_path:
                    self.last_failed_path = self.paths[self.next_index] if self.paths else path
        self.last_error = (error or "")[:2000]
        self.status = "failed"
        self.updated_at = _utc_now()

    def mark_cancelled(self) -> None:
        self.status = "cancelled"
        self.updated_at = _utc_now()

    def mark_completed(self) -> None:
        self.next_index = self.total
        self.status = "completed"
        self.last_failed_path = ""
        self.last_error = ""
        self.updated_at = _utc_now()

    def mark_running(self) -> None:
        self.status = "running"
        self.updated_at = _utc_now()

    def summary_line(self) -> str:
        label = self.label or self.kind or "sync"
        return (
            f"{label}: {self.succeeded}/{self.total} sent "
            f"({self.remaining} left), status={self.status}"
        )


def sync_job_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / SYNC_JOB_FILENAME


def new_sync_job(
    *,
    paths: list[str],
    kind: str = "batch",
    label: str = "",
    target_format: str = "mp3",
    mode: str = "experimental",
    next_index: int = 0,
) -> SyncJobState:
    now = _utc_now()
    return SyncJobState(
        version=SYNC_JOB_VERSION,
        kind=kind or "batch",
        label=label or "",
        target_format=(target_format or "mp3").lower().lstrip("."),
        mode=mode or "experimental",
        paths=list(paths),
        next_index=max(0, int(next_index)),
        status="running",
        created_at=now,
        updated_at=now,
    )


def load_sync_job(*, path: Path | None = None) -> SyncJobState | None:
    """Load job from disk; None if missing or invalid."""
    src = path if path is not None else sync_job_path()
    if not src.is_file():
        return None
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        logger.warning("Cannot read sync job %s: %s", src, e)
        return None
    if not isinstance(raw, dict):
        return None
    return _from_dict(raw)


def save_sync_job(job: SyncJobState, *, path: Path | None = None) -> Path:
    """Write job atomically. Returns path written."""
    dest = path if path is not None else sync_job_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    job.updated_at = _utc_now()
    payload = _to_dict(job)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    logger.debug("Saved sync job → %s (%s)", dest, job.summary_line())
    return dest


def clear_sync_job(*, path: Path | None = None) -> None:
    """Remove the durable job file if present."""
    dest = path if path is not None else sync_job_path()
    try:
        if dest.is_file():
            dest.unlink()
            logger.info("Cleared sync job %s", dest)
    except OSError as e:
        logger.warning("Could not clear sync job %s: %s", dest, e)


def _to_dict(job: SyncJobState) -> dict[str, Any]:
    return {
        "version": int(job.version),
        "kind": job.kind,
        "label": job.label,
        "target_format": job.target_format,
        "mode": job.mode,
        "paths": list(job.paths),
        "next_index": int(job.next_index),
        "status": job.status,
        "last_error": job.last_error,
        "last_failed_path": job.last_failed_path,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _from_dict(raw: dict[str, Any]) -> SyncJobState | None:
    paths_raw = raw.get("paths")
    if not isinstance(paths_raw, list):
        return None
    paths: list[str] = []
    for p in paths_raw:
        if isinstance(p, str) and p:
            paths.append(p)
    if not paths:
        return None
    try:
        next_index = int(raw.get("next_index", 0) or 0)
    except (TypeError, ValueError):
        next_index = 0
    next_index = max(0, min(next_index, len(paths)))
    status = str(raw.get("status") or "failed")
    if status not in ("running", "completed", "failed", "cancelled"):
        status = "failed"
    return SyncJobState(
        version=int(raw.get("version", SYNC_JOB_VERSION) or SYNC_JOB_VERSION),
        kind=str(raw.get("kind") or "batch"),
        label=str(raw.get("label") or ""),
        target_format=str(raw.get("target_format") or "mp3").lower().lstrip("."),
        mode=str(raw.get("mode") or "experimental"),
        paths=paths,
        next_index=next_index,
        status=status,
        last_error=str(raw.get("last_error") or ""),
        last_failed_path=str(raw.get("last_failed_path") or ""),
        created_at=str(raw.get("created_at") or ""),
        updated_at=str(raw.get("updated_at") or ""),
    )
