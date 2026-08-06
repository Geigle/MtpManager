"""Pure schedule helpers for automatic podcast full-sync.

No I/O — unit-testable. Controllers call this on a timer and catch-up
after launch/wake. Last full-sync stamps live in app config (global) and
optionally per-show in the podcast index.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from mtpmanager.infra.app_config import (
    ALL_DAY_KEYS,
    DEFAULT_PODCAST_SCHEDULE_TIME,
    WEEKDAY_KEYS,
    normalize_max_new_per_show,
    normalize_schedule_days,
    normalize_schedule_time,
)

# Python weekday(): Mon=0 … Sun=6
_WEEKDAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_INDEX_TO_KEY = {v: k for k, v in _WEEKDAY_INDEX.items()}


def day_key_for_date(d: date) -> str:
    return _INDEX_TO_KEY[d.weekday()]


def parse_hhmm(value: str) -> tuple[int, int]:
    """Return (hour, minute); invalid → default 06:30."""
    norm = normalize_schedule_time(value)
    h, m = norm.split(":")
    return int(h), int(m)


def hhmm_to_12h(time_hhmm: str) -> tuple[int, int, str]:
    """Return (hour_1_12, minute, 'AM'|'PM') from 24h HH:MM."""
    hour24, minute = parse_hhmm(time_hhmm)
    ampm = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12
    if hour12 == 0:
        hour12 = 12
    return hour12, minute, ampm


def components_to_hhmm(hour12: int, minute: int, ampm: str) -> str:
    """Build normalized 24h HH:MM from 12h components."""
    try:
        h = int(hour12)
        m = int(minute)
    except (TypeError, ValueError):
        return DEFAULT_PODCAST_SCHEDULE_TIME
    m = max(0, min(59, m))
    h = max(1, min(12, h))
    is_pm = str(ampm or "").strip().upper().startswith("P")
    if h == 12:
        hour24 = 12 if is_pm else 0
    else:
        hour24 = h + 12 if is_pm else h
    return normalize_schedule_time(f"{hour24:02d}:{m:02d}")


@dataclass(frozen=True)
class EffectiveSchedule:
    days: tuple[str, ...]
    time_hhmm: str


def effective_schedule(
    *,
    global_days: Sequence[str],
    global_time: str,
    show_auto_update: bool = True,
    show_schedule_time: str = "",
    show_schedule_days: str = "",
) -> EffectiveSchedule | None:
    """Return effective days/time for a show, or None if auto-update is off."""
    if not show_auto_update:
        return None
    days = normalize_schedule_days(global_days)
    if (show_schedule_days or "").strip():
        days = normalize_schedule_days(show_schedule_days)
    time_hhmm = normalize_schedule_time(global_time)
    override = (show_schedule_time or "").strip()
    if override:
        time_hhmm = normalize_schedule_time(override)
    return EffectiveSchedule(days=tuple(days), time_hhmm=time_hhmm)


def is_due(
    *,
    now_local: datetime,
    days: Sequence[str],
    time_hhmm: str,
    last_run_local_date: str,
) -> bool:
    """True when *now_local* is on a scheduled day, at/after the time, and
    a full sync has not already completed for today's local date.
    """
    day_keys = set(normalize_schedule_days(days))
    today = now_local.date()
    if day_key_for_date(today) not in day_keys:
        return False
    last = (last_run_local_date or "").strip()[:10]
    today_s = today.isoformat()
    if last == today_s:
        return False
    hour, minute = parse_hhmm(time_hhmm)
    if (now_local.hour, now_local.minute) < (hour, minute):
        return False
    return True


def next_run_after(
    *,
    now_local: datetime,
    days: Sequence[str],
    time_hhmm: str,
    last_run_local_date: str = "",
) -> datetime | None:
    """Next local fire time at or after *now_local* (or catch-up if due now)."""
    day_keys = set(normalize_schedule_days(days))
    if not day_keys:
        return None
    hour, minute = parse_hhmm(time_hhmm)
    if is_due(
        now_local=now_local,
        days=list(day_keys),
        time_hhmm=time_hhmm,
        last_run_local_date=last_run_local_date,
    ):
        return now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
    start = now_local.date()
    today_key = day_key_for_date(start)
    if today_key in day_keys:
        candidate = datetime(start.year, start.month, start.day, hour, minute)
        last = (last_run_local_date or "").strip()[:10]
        if candidate > now_local and last != start.isoformat():
            return candidate
    for offset in range(1, 15):
        d = start + timedelta(days=offset)
        if day_key_for_date(d) in day_keys:
            return datetime(d.year, d.month, d.day, hour, minute)
    return None


def format_schedule_summary(
    *,
    days: Sequence[str],
    time_hhmm: str,
) -> str:
    """Human-readable short summary for dialogs."""
    dlist = normalize_schedule_days(days)
    t = normalize_schedule_time(time_hhmm)
    hour12, minute, ampm = hhmm_to_12h(t)
    time_part = f"{hour12}:{minute:02d} {ampm}"
    if dlist == list(WEEKDAY_KEYS):
        day_part = "weekdays"
    elif dlist == list(ALL_DAY_KEYS):
        day_part = "daily"
    else:
        day_part = ", ".join(dlist)
    return f"{day_part} at {time_part}"


def clamp_max_new(n: object) -> int:
    return normalize_max_new_per_show(n)
