"""Unit tests for automatic podcast schedule helpers."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from mtpmanager.app.podcast_schedule import (
    components_to_hhmm,
    format_schedule_summary,
    hhmm_to_12h,
    is_due,
    next_run_after,
    podcast_day_playlist_name,
)
from mtpmanager.infra.app_config import WEEKDAY_KEYS


class PodcastScheduleTests(unittest.TestCase):
    def test_is_due_weekday_morning_catchup(self) -> None:
        # Wednesday 2026-08-05 07:00 local, schedule weekdays 06:30, never run.
        now = datetime(2026, 8, 5, 7, 0, 0)  # Wednesday
        self.assertTrue(
            is_due(
                now_local=now,
                days=WEEKDAY_KEYS,
                time_hhmm="06:30",
                last_run_local_date="",
            )
        )
        self.assertFalse(
            is_due(
                now_local=now,
                days=WEEKDAY_KEYS,
                time_hhmm="06:30",
                last_run_local_date="2026-08-05",
            )
        )
        early = datetime(2026, 8, 5, 6, 0, 0)
        self.assertFalse(
            is_due(
                now_local=early,
                days=WEEKDAY_KEYS,
                time_hhmm="06:30",
                last_run_local_date="",
            )
        )
        sat = datetime(2026, 8, 8, 8, 0, 0)  # Saturday
        self.assertFalse(
            is_due(
                now_local=sat,
                days=WEEKDAY_KEYS,
                time_hhmm="06:30",
                last_run_local_date="",
            )
        )

    def test_12h_round_trip(self) -> None:
        self.assertEqual(hhmm_to_12h("06:30"), (6, 30, "AM"))
        self.assertEqual(hhmm_to_12h("00:05"), (12, 5, "AM"))
        self.assertEqual(hhmm_to_12h("12:00"), (12, 0, "PM"))
        self.assertEqual(hhmm_to_12h("18:45"), (6, 45, "PM"))
        self.assertEqual(components_to_hhmm(6, 30, "AM"), "06:30")
        self.assertEqual(components_to_hhmm(12, 0, "AM"), "00:00")
        self.assertEqual(components_to_hhmm(12, 15, "PM"), "12:15")
        self.assertEqual(components_to_hhmm(6, 45, "PM"), "18:45")

    def test_next_run_and_summary(self) -> None:
        now = datetime(2026, 8, 5, 5, 0, 0)  # before 06:30
        nxt = next_run_after(
            now_local=now,
            days=WEEKDAY_KEYS,
            time_hhmm="06:30",
            last_run_local_date="",
        )
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertEqual(nxt.hour, 6)
        self.assertEqual(nxt.minute, 30)
        summary = format_schedule_summary(days=WEEKDAY_KEYS, time_hhmm="06:30")
        self.assertIn("weekdays", summary)
        self.assertIn("6:30 AM", summary)

    def test_day_playlist_name(self) -> None:
        self.assertEqual(
            podcast_day_playlist_name(datetime(2026, 8, 5, 7, 30)),
            "Podcasts Aug 5, 2026",
        )
        self.assertEqual(
            podcast_day_playlist_name(date(2026, 12, 1)),
            "Podcasts Dec 1, 2026",
        )


if __name__ == "__main__":
    unittest.main()
