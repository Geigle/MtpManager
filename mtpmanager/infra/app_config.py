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
VALID_SEND_FORMATS = frozenset({"mp3", "wma", "wav"})
DEFAULT_SEND_FORMAT = "mp3"

# Audio→still-video (ZVM ZENcast). Proven on device: 2 fps · 128×96 (~4:3).
# 1 fps failed; 15 fps · 640×480 worked but was large (+110% vs audio).
DEFAULT_AUDIO_PODCAST_STILL_FPS = 2.0
DEFAULT_AUDIO_PODCAST_STILL_WIDTH = 128
DEFAULT_AUDIO_PODCAST_STILL_HEIGHT = 96

# Automatic podcast full-sync schedule (Library → Podcast Settings…).
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri")
ALL_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_PODCAST_SCHEDULE_TIME = "06:30"
DEFAULT_PODCAST_MAX_NEW_PER_SHOW = 1
MAX_PODCAST_NEW_PER_SHOW = 20


def normalize_schedule_time(value: object) -> str:
    """Return HH:MM (24h) or the default when invalid."""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_PODCAST_SCHEDULE_TIME
    parts = raw.split(":")
    if len(parts) != 2:
        return DEFAULT_PODCAST_SCHEDULE_TIME
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return DEFAULT_PODCAST_SCHEDULE_TIME
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEFAULT_PODCAST_SCHEDULE_TIME
    return f"{hour:02d}:{minute:02d}"


def normalize_schedule_days(value: object) -> list[str]:
    """Normalize day keys; empty/invalid → weekdays."""
    allowed = set(ALL_DAY_KEYS)
    if isinstance(value, str):
        key = value.strip().lower()
        if key in ("weekdays", "weekday"):
            return list(WEEKDAY_KEYS)
        if key in ("daily", "all", "every"):
            return list(ALL_DAY_KEYS)
        value = [p.strip() for p in key.split(",") if p.strip()]
    if not isinstance(value, (list, tuple)):
        return list(WEEKDAY_KEYS)
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        d = str(item or "").strip().lower()[:3]
        if d in allowed and d not in seen:
            seen.add(d)
            out.append(d)
    return out if out else list(WEEKDAY_KEYS)


def normalize_max_new_per_show(value: object) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PODCAST_MAX_NEW_PER_SHOW
    return max(1, min(MAX_PODCAST_NEW_PER_SHOW, n))


def normalize_still_frame_size(width: int, height: int) -> tuple[int, int]:
    """Positive even dimensions (MPEG-4-friendly); minimum 16×16."""
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = DEFAULT_AUDIO_PODCAST_STILL_WIDTH
    try:
        h = int(height)
    except (TypeError, ValueError):
        h = DEFAULT_AUDIO_PODCAST_STILL_HEIGHT
    w = max(16, w)
    h = max(16, h)
    # Even (yuv420); prefer multiples of 16 when already close.
    if w % 2:
        w += 1
    if h % 2:
        h += 1
    return w, h


def normalize_still_fps(value: object) -> float:
    """Positive still frame rate; default when missing/invalid."""
    try:
        fps = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_AUDIO_PODCAST_STILL_FPS
    if fps <= 0:
        return DEFAULT_AUDIO_PODCAST_STILL_FPS
    # Cap absurd values; ZEN video max is ~30.
    return min(fps, 60.0)


@dataclass
class AppConfig:
    """User preferences loaded from disk."""

    send_format: str = DEFAULT_SEND_FORMAT
    # When True, transfers use mtp-sendtr (Stable). Default is PyMTP (Experimental).
    stable_mode: bool = False
    # After Experimental (PyMTP) music sync: create/update MTP album objects and
    # attach JPEG representative samples (ZEN: album only, not tracks).
    sync_album_art: bool = True
    # When True, show Device/Transfer menu items marked experimental (list/delete
    # diagnostics, retail package tools, etc.). Default off to keep the UI simple.
    enable_experimental_tools: bool = False
    # When True, create Music/<artist> on the device and send tracks there (PyMTP).
    store_tracks_in_artist_folder: bool = False
    # When True (requires artist folders), create Music/<artist>/<album> and send there.
    store_tracks_in_album_folder: bool = False
    # When True, Send Video shows broken device presets (e.g. ZEN WMV·WMA).
    show_broken_video_presets: bool = False
    # When True, the bottom playback bar stays visible even when idle.
    always_show_playback_controls: bool = False
    # When True, create ZENcast/<show>/ folders for podcast sends (PyMTP; experimental).
    store_podcasts_in_show_folders: bool = False
    # When True (experimental tools), video podcast episodes sync as video
    # (XviD on ZEN) under ZENcast. Default off: video-only items extract audio;
    # dual feeds prefer audio enclosure. Hidden unless Enable Experimental Tools.
    allow_video_podcasts_to_sync: bool = False
    # When True (experimental tools), audio podcasts are muxed as still-image
    # XviD video under ZENcast (ZVM experiment: only video objects appear in
    # ZENcast; audio lands in Music). Hidden unless Enable Experimental Tools.
    sync_audio_podcasts_as_video: bool = False
    # Still-video ladder (edit config.json to binary-search the ZVM floor).
    audio_podcast_still_fps: float = DEFAULT_AUDIO_PODCAST_STILL_FPS
    audio_podcast_still_width: int = DEFAULT_AUDIO_PODCAST_STILL_WIDTH
    audio_podcast_still_height: int = DEFAULT_AUDIO_PODCAST_STILL_HEIGHT
    # When True (default), keep enclosure/encode files under data/podcasts/.
    # When False, delete local copies after a successful sync of that episode.
    keep_downloaded_podcasts: bool = True
    # Scheduled full podcast sync (app must stay open; catch-up after wake).
    podcast_auto_enabled: bool = False
    podcast_schedule_days: tuple[str, ...] = WEEKDAY_KEYS
    podcast_schedule_time: str = DEFAULT_PODCAST_SCHEDULE_TIME
    # 1–N most recent new episodes per show per full sync.
    podcast_max_new_per_show: int = DEFAULT_PODCAST_MAX_NEW_PER_SHOW
    podcast_auto_sync_to_device: bool = True
    # Last completed full sync (UTC ISO + local calendar date for catch-up).
    podcast_last_full_sync_at: str = ""
    podcast_last_full_sync_local_date: str = ""
    version: int = CONFIG_VERSION

    def normalized_send_format(self) -> str:
        fmt = (self.send_format or DEFAULT_SEND_FORMAT).lower().lstrip(".")
        if fmt not in VALID_SEND_FORMATS:
            return DEFAULT_SEND_FORMAT
        return fmt

    def active_mode(self) -> str:
        """Return ``\"stable\"`` or ``\"experimental\"``."""
        return "stable" if self.stable_mode else "experimental"


def config_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / CONFIG_FILENAME


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


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
    artist = _as_bool(raw.get("store_tracks_in_artist_folder"), False)
    album = _as_bool(raw.get("store_tracks_in_album_folder"), False)
    # Album folders only make sense under artist folders.
    if not artist:
        album = False
    cfg = AppConfig(
        send_format=fmt,
        stable_mode=_as_bool(raw.get("stable_mode"), False),
        sync_album_art=_as_bool(raw.get("sync_album_art"), True),
        enable_experimental_tools=_as_bool(
            raw.get("enable_experimental_tools"), False
        ),
        store_tracks_in_artist_folder=artist,
        store_tracks_in_album_folder=album,
        show_broken_video_presets=_as_bool(
            raw.get("show_broken_video_presets"), False
        ),
        always_show_playback_controls=_as_bool(
            raw.get("always_show_playback_controls"), False
        ),
        store_podcasts_in_show_folders=_as_bool(
            raw.get("store_podcasts_in_show_folders"), False
        ),
        allow_video_podcasts_to_sync=_as_bool(
            raw.get("allow_video_podcasts_to_sync"), False
        ),
        sync_audio_podcasts_as_video=_as_bool(
            raw.get("sync_audio_podcasts_as_video"), False
        ),
        audio_podcast_still_fps=normalize_still_fps(
            raw.get("audio_podcast_still_fps", DEFAULT_AUDIO_PODCAST_STILL_FPS)
        ),
        audio_podcast_still_width=DEFAULT_AUDIO_PODCAST_STILL_WIDTH,
        audio_podcast_still_height=DEFAULT_AUDIO_PODCAST_STILL_HEIGHT,
        keep_downloaded_podcasts=_as_bool(
            raw.get("keep_downloaded_podcasts"), True
        ),
        podcast_auto_enabled=_as_bool(raw.get("podcast_auto_enabled"), False),
        podcast_schedule_days=tuple(
            normalize_schedule_days(raw.get("podcast_schedule_days"))
        ),
        podcast_schedule_time=normalize_schedule_time(
            raw.get("podcast_schedule_time", DEFAULT_PODCAST_SCHEDULE_TIME)
        ),
        podcast_max_new_per_show=normalize_max_new_per_show(
            raw.get("podcast_max_new_per_show", DEFAULT_PODCAST_MAX_NEW_PER_SHOW)
        ),
        podcast_auto_sync_to_device=_as_bool(
            raw.get("podcast_auto_sync_to_device"), True
        ),
        podcast_last_full_sync_at=str(
            raw.get("podcast_last_full_sync_at") or ""
        ).strip(),
        podcast_last_full_sync_local_date=str(
            raw.get("podcast_last_full_sync_local_date") or ""
        ).strip()[:10],
        version=int(raw.get("version", CONFIG_VERSION) or CONFIG_VERSION),
    )
    sw, sh = normalize_still_frame_size(
        raw.get("audio_podcast_still_width", DEFAULT_AUDIO_PODCAST_STILL_WIDTH),
        raw.get("audio_podcast_still_height", DEFAULT_AUDIO_PODCAST_STILL_HEIGHT),
    )
    cfg.audio_podcast_still_width = sw
    cfg.audio_podcast_still_height = sh
    cfg.send_format = cfg.normalized_send_format()
    return cfg


def save_app_config(config: AppConfig, *, path: Path | None = None) -> Path:
    """Write config atomically. Returns the path written."""
    dest = path if path is not None else config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    artist = bool(config.store_tracks_in_artist_folder)
    album = bool(config.store_tracks_in_album_folder) and artist
    payload = {
        "version": CONFIG_VERSION,
        "send_format": config.normalized_send_format(),
        "stable_mode": bool(config.stable_mode),
        "sync_album_art": bool(config.sync_album_art),
        "enable_experimental_tools": bool(config.enable_experimental_tools),
        "store_tracks_in_artist_folder": artist,
        "store_tracks_in_album_folder": album,
        "show_broken_video_presets": bool(config.show_broken_video_presets),
        "always_show_playback_controls": bool(
            config.always_show_playback_controls
        ),
        "store_podcasts_in_show_folders": bool(
            config.store_podcasts_in_show_folders
        ),
        "allow_video_podcasts_to_sync": bool(
            config.allow_video_podcasts_to_sync
        ),
        "sync_audio_podcasts_as_video": bool(
            config.sync_audio_podcasts_as_video
        ),
        "audio_podcast_still_fps": normalize_still_fps(
            config.audio_podcast_still_fps
        ),
        "audio_podcast_still_width": normalize_still_frame_size(
            config.audio_podcast_still_width, config.audio_podcast_still_height
        )[0],
        "audio_podcast_still_height": normalize_still_frame_size(
            config.audio_podcast_still_width, config.audio_podcast_still_height
        )[1],
        "keep_downloaded_podcasts": bool(config.keep_downloaded_podcasts),
        "podcast_auto_enabled": bool(config.podcast_auto_enabled),
        "podcast_schedule_days": list(
            normalize_schedule_days(config.podcast_schedule_days)
        ),
        "podcast_schedule_time": normalize_schedule_time(
            config.podcast_schedule_time
        ),
        "podcast_max_new_per_show": normalize_max_new_per_show(
            config.podcast_max_new_per_show
        ),
        "podcast_auto_sync_to_device": bool(config.podcast_auto_sync_to_device),
        "podcast_last_full_sync_at": str(
            config.podcast_last_full_sync_at or ""
        ).strip(),
        "podcast_last_full_sync_local_date": str(
            config.podcast_last_full_sync_local_date or ""
        ).strip()[:10],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    logger.info("Saved app config → %s", dest)
    return dest
