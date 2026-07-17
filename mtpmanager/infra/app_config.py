"""Durable app preferences (JSON under the app data dir)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"
CONFIG_VERSION = 1
VALID_SEND_FORMATS = frozenset({"mp3", "wma"})
DEFAULT_SEND_FORMAT = "mp3"


@dataclass
class AppConfig:
    """User preferences loaded from disk."""

    send_format: str = DEFAULT_SEND_FORMAT
    version: int = CONFIG_VERSION

    def normalized_send_format(self) -> str:
        fmt = (self.send_format or DEFAULT_SEND_FORMAT).lower().lstrip(".")
        if fmt not in VALID_SEND_FORMATS:
            return DEFAULT_SEND_FORMAT
        return fmt


def config_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / CONFIG_FILENAME


def load_app_config(*, path: Path | None = None) -> AppConfig:
    """Load config from disk; return defaults if missing or invalid."""
    src = path if path is not None else config_path()
    if not src.is_file():
        return AppConfig()
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        logger.warning("Cannot read app config %s: %s", src, e)
        return AppConfig()
    if not isinstance(raw, dict):
        return AppConfig()
    fmt = raw.get("send_format", DEFAULT_SEND_FORMAT)
    if not isinstance(fmt, str):
        fmt = DEFAULT_SEND_FORMAT
    cfg = AppConfig(
        send_format=fmt,
        version=int(raw.get("version", CONFIG_VERSION) or CONFIG_VERSION),
    )
    cfg.send_format = cfg.normalized_send_format()
    return cfg


def save_app_config(config: AppConfig, *, path: Path | None = None) -> Path:
    """Write config atomically. Returns the path written."""
    dest = path if path is not None else config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONFIG_VERSION,
        "send_format": config.normalized_send_format(),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    logger.info("Saved app config → %s", dest)
    return dest
