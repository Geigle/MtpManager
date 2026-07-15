"""Central logging setup: multi-file handlers, retention, transfer session logs."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Module-level log dir set by configure_logging so app code need not hardcode paths.
_log_dir: Path | None = None
_configured = False

# Defaults (overridable via env where noted in the plan)
_MAIN_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MAIN_BACKUP_COUNT = 7
_ERROR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_ERROR_BACKUP_COUNT = 5
_DEFAULT_MAX_AGE_DAYS = 14

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def default_log_dir() -> Path:
    """Platform path for log files (or MTP_MANAGER_LOG_DIR override)."""
    override = os.environ.get("MTP_MANAGER_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "MtpManager"

    # Linux / other Unix: XDG_STATE_HOME or ~/.local/share
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg_state:
        return Path(xdg_state).expanduser() / "mtpmanager" / "logs"
    return Path.home() / ".local" / "share" / "mtpmanager" / "logs"


def get_log_dir() -> Path:
    """Return the active log directory (after configure_logging) or the default."""
    if _log_dir is not None:
        return _log_dir
    return default_log_dir()


def _console_level() -> int:
    debug = os.environ.get("MTP_MANAGER_DEBUG", "").strip().lower()
    if debug in ("1", "true", "yes", "on"):
        return logging.DEBUG
    if "--verbose" in sys.argv or "-v" in sys.argv:
        return logging.DEBUG
    return logging.INFO


def _max_age_days() -> int:
    raw = os.environ.get("MTP_MANAGER_LOG_MAX_AGE_DAYS", "").strip()
    if not raw:
        return _DEFAULT_MAX_AGE_DAYS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_AGE_DAYS


def prune_old_logs(log_dir: Path, *, max_age_days: int | None = None) -> int:
    """Delete log files older than max_age_days. Returns count removed."""
    if max_age_days is None:
        max_age_days = _max_age_days()
    if not log_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in log_dir.glob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def configure_logging(
    *,
    level: int = logging.DEBUG,
    log_dir: Path | str | None = None,
    console: bool = True,
) -> Path:
    """Install file + optional console handlers once. Returns the log directory."""
    global _log_dir, _configured

    resolved = Path(log_dir) if log_dir is not None else default_log_dir()
    resolved = resolved.expanduser()
    resolved.mkdir(parents=True, exist_ok=True)
    _log_dir = resolved

    if _configured:
        return resolved

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers if something else already configured root.
    # We still install our own set once per process via _configured.
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    main_path = resolved / "mtpmanager.log"
    main_handler = RotatingFileHandler(
        main_path,
        maxBytes=_MAIN_MAX_BYTES,
        backupCount=_MAIN_BACKUP_COUNT,
        encoding="utf-8",
    )
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(formatter)
    root.addHandler(main_handler)

    error_path = resolved / "errors.log"
    error_handler = RotatingFileHandler(
        error_path,
        maxBytes=_ERROR_MAX_BYTES,
        backupCount=_ERROR_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(_console_level())
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    _configured = True
    return resolved


def start_transfer_log(log_dir: Path | None = None) -> logging.Handler:
    """Attach a per-batch FileHandler under the mtpmanager logger tree."""
    directory = Path(log_dir) if log_dir is not None else get_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"transfer-{stamp}.log"

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.name = f"transfer-session:{path.name}"

    # Capture mtpmanager.* (app, infra, domain) for this batch.
    logging.getLogger("mtpmanager").addHandler(handler)
    return handler


def stop_transfer_log(handler: logging.Handler | None) -> None:
    """Detach and close a transfer session handler."""
    if handler is None:
        return
    root_pkg = logging.getLogger("mtpmanager")
    root_pkg.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass
