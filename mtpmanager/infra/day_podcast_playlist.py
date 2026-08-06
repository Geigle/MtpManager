"""Durable on-device day playlist plan for scheduled podcast capture.

Membership is GUID-only (episodes already sent to the player). The host does
not need local media files. State survives app restarts within the same local
calendar day so playlist publish can still run after reconnect.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mtpmanager.app.podcast_schedule import podcast_day_playlist_name
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

_FILENAME = "day_podcast_playlist.json"


def _state_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    return default_data_dir() / _FILENAME


def _today_local() -> str:
    return datetime.now().astimezone().date().isoformat()


def load_day_playlist_plan(*, path: Path | None = None) -> dict[str, Any] | None:
    """Return today's plan ``{name, guids, local_date}`` or None if stale/missing."""
    dest = _state_path(path)
    if not dest.is_file():
        return None
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("day podcast playlist plan read failed: %s", e)
        return None
    if not isinstance(raw, dict):
        return None
    day = str(raw.get("local_date") or "").strip()[:10]
    if day != _today_local():
        return None
    name = str(raw.get("name") or "").strip()
    guids = [
        g.strip().lower()
        for g in (raw.get("guids") or [])
        if is_track_guid(str(g))
    ]
    if not name:
        name = podcast_day_playlist_name()
    return {"name": name, "guids": guids, "local_date": day}


def ensure_day_playlist_plan(
    *,
    when: datetime | date | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return today's plan, creating an empty one if needed."""
    existing = load_day_playlist_plan(path=path)
    if existing is not None:
        return existing
    if when is None:
        when = datetime.now().astimezone()
    if isinstance(when, datetime):
        local_day = when.date().isoformat()
        name = podcast_day_playlist_name(when)
    else:
        local_day = when.isoformat()
        name = podcast_day_playlist_name(when)
    plan = {"name": name, "guids": [], "local_date": local_day}
    save_day_playlist_plan(plan, path=path)
    return plan


def save_day_playlist_plan(
    plan: dict[str, Any],
    *,
    path: Path | None = None,
) -> None:
    dest = _state_path(path)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "local_date": str(plan.get("local_date") or _today_local())[:10],
            "name": str(plan.get("name") or podcast_day_playlist_name()).strip(),
            "guids": [
                g.strip().lower()
                for g in (plan.get("guids") or [])
                if is_track_guid(str(g))
            ],
        }
        dest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("day podcast playlist plan write failed: %s", e)


def append_day_playlist_guid(
    guid: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Append a successfully sent episode GUID; returns updated plan."""
    g = (guid or "").strip().lower()
    if not is_track_guid(g):
        return None
    plan = ensure_day_playlist_plan(path=path)
    guids = list(plan.get("guids") or [])
    if g not in guids:
        guids.append(g)
        plan["guids"] = guids
        save_day_playlist_plan(plan, path=path)
    return plan


def clear_day_playlist_plan(*, path: Path | None = None) -> None:
    dest = _state_path(path)
    try:
        if dest.is_file():
            dest.unlink()
    except OSError as e:
        logger.debug("day podcast playlist plan clear failed: %s", e)
