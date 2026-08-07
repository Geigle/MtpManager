"""Cross-process exclusive ownership of the MTP device session.

In-process USB serialization lives in :class:`~mtpmanager.app.device_io_gate.DeviceIoGate`.
This lock coordinates **separate processes** (Tk GUI vs headless CLI/MCP) so they
do not open libmtp against the same ZEN at once.

Lock file: ``device_session.lock`` under the app data dir (JSON: pid, holder, started_at).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

LOCK_FILENAME = "device_session.lock"


class DeviceSessionBusy(RuntimeError):
    """Another live process holds the device session lock."""

    def __init__(
        self,
        message: str = "Device session busy",
        *,
        holder: str | None = None,
        pid: int | None = None,
    ) -> None:
        super().__init__(message)
        self.holder = holder
        self.pid = pid


@dataclass(frozen=True)
class LockInfo:
    """Snapshot of who holds (or held) the session lock."""

    held: bool
    holder: str | None = None
    pid: int | None = None
    started_at: float | None = None
    path: str = ""
    stale: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "held": self.held,
            "holder": self.holder,
            "pid": self.pid,
            "started_at": self.started_at,
            "path": self.path,
            "stale": self.stale,
        }


def lock_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / LOCK_FILENAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False
    return True


def _read_raw(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def inspect_lock(*, data_dir: Path | None = None) -> LockInfo:
    """Return lock status without acquiring (stale locks reported as held=False)."""
    path = lock_path(data_dir=data_dir)
    if not path.is_file():
        return LockInfo(held=False, path=str(path))
    raw = _read_raw(path)
    if raw is None:
        return LockInfo(held=False, path=str(path), stale=True)
    try:
        pid = int(raw.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    holder = str(raw.get("holder") or "") or None
    try:
        started = float(raw["started_at"]) if raw.get("started_at") is not None else None
    except (TypeError, ValueError):
        started = None
    if pid and _pid_alive(pid):
        return LockInfo(
            held=True,
            holder=holder,
            pid=pid,
            started_at=started,
            path=str(path),
            stale=False,
        )
    return LockInfo(
        held=False,
        holder=holder,
        pid=pid or None,
        started_at=started,
        path=str(path),
        stale=True,
    )


class DeviceSessionLock:
    """Non-reentrant cross-process lock for device USB ownership."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir
        self._path = lock_path(data_dir=data_dir)
        self._holder: str | None = None
        self._owned = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def owned(self) -> bool:
        return self._owned

    @property
    def holder(self) -> str | None:
        return self._holder if self._owned else None

    def status(self) -> LockInfo:
        if self._owned:
            return LockInfo(
                held=True,
                holder=self._holder,
                pid=os.getpid(),
                started_at=None,
                path=str(self._path),
                stale=False,
            )
        return inspect_lock(data_dir=self._data_dir)

    def try_acquire(self, holder: str) -> bool:
        """Acquire lock for *holder*. Returns False if another owner holds it.

        Same-process: only the :class:`DeviceSessionLock` instance that acquired
        the lock may re-enter; a second instance sees busy (even though the PID
        matches). Cross-process: a live foreign PID is always busy.
        """
        if self._owned:
            return True

        clean = (holder or "unknown").strip() or "unknown"
        self._path.parent.mkdir(parents=True, exist_ok=True)

        info = inspect_lock(data_dir=self._data_dir)
        if info.held:
            # Live holder in another process, or another lock object in this one.
            if info.pid is not None and info.pid != os.getpid():
                logger.info(
                    "Device session lock busy: holder=%r pid=%s path=%s",
                    info.holder,
                    info.pid,
                    info.path,
                )
                return False
            if info.pid == os.getpid():
                # File says we (this PID) hold it, but this instance does not.
                logger.info(
                    "Device session lock busy in-process: holder=%r path=%s",
                    info.holder,
                    info.path,
                )
                return False

        # Break stale lock file if present.
        if self._path.is_file() and (info.stale or not info.held):
            try:
                self._path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Cannot remove stale device lock %s: %s", self._path, e)
                return False

        payload = {
            "pid": os.getpid(),
            "holder": clean,
            "started_at": time.time(),
        }
        tmp = self._path.with_suffix(".lock.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=0) + "\n", encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:
            logger.warning("Cannot write device session lock %s: %s", self._path, e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

        # Re-read to detect lost race (best-effort; not fcntl exclusive).
        raw = _read_raw(self._path)
        if raw is None:
            self._owned = False
            self._holder = None
            return False
        try:
            pid = int(raw.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid != os.getpid():
            return False

        self._owned = True
        self._holder = clean
        logger.debug("Acquired device session lock holder=%r path=%s", clean, self._path)
        return True

    def acquire(self, holder: str) -> None:
        """Acquire or raise :class:`DeviceSessionBusy`."""
        if self.try_acquire(holder):
            return
        info = inspect_lock(data_dir=self._data_dir)
        raise DeviceSessionBusy(
            f"Device session busy (held by {info.holder!r} pid={info.pid})",
            holder=info.holder,
            pid=info.pid,
        )

    def release(self, holder: str | None = None) -> bool:
        """Release if we own the lock. Returns True when released."""
        if not self._owned:
            return False
        if holder is not None and self._holder != holder:
            return False
        try:
            raw = _read_raw(self._path)
            if raw is not None:
                try:
                    pid = int(raw.get("pid") or 0)
                except (TypeError, ValueError):
                    pid = 0
                if pid == os.getpid():
                    self._path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Cannot release device session lock %s: %s", self._path, e)
            return False
        finally:
            self._owned = False
            self._holder = None
        logger.debug("Released device session lock path=%s", self._path)
        return True

    def __enter__(self) -> DeviceSessionLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
