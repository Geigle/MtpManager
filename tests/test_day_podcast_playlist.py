"""Durable day podcast playlist plan (no device)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from mtpmanager.infra.day_podcast_playlist import (
    append_day_playlist_guid,
    clear_day_playlist_plan,
    ensure_day_playlist_plan,
    load_day_playlist_plan,
)


class DayPodcastPlaylistPlanTests(unittest.TestCase):
    def test_ensure_append_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "day_podcast_playlist.json"
            when = datetime(2026, 8, 6, 10, 0, 0)
            plan = ensure_day_playlist_plan(when=when, path=path)
            self.assertEqual(plan["name"], "Podcasts Aug 6, 2026")
            self.assertEqual(plan["guids"], [])

            g1 = "a" * 32
            g2 = "b" * 32
            # append uses today from wall clock — force via ensure + save path
            # by writing guids only when local_date matches today.
            # For unit test, patch by saving plan with today's date then append.
            from mtpmanager.infra import day_podcast_playlist as mod

            today = mod._today_local()
            plan["local_date"] = today
            plan["name"] = "Podcasts Test"
            mod.save_day_playlist_plan(plan, path=path)

            p2 = append_day_playlist_guid(g1, path=path)
            self.assertIsNotNone(p2)
            assert p2 is not None
            self.assertIn(g1, p2["guids"])
            p3 = append_day_playlist_guid(g1, path=path)
            assert p3 is not None
            self.assertEqual(p3["guids"].count(g1), 1)
            append_day_playlist_guid(g2, path=path)
            loaded = load_day_playlist_plan(path=path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["guids"], [g1, g2])
            clear_day_playlist_plan(path=path)
            self.assertIsNone(load_day_playlist_plan(path=path))


if __name__ == "__main__":
    unittest.main()
