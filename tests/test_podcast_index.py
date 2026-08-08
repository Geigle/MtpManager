"""Podcast SQLite CRUD tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datetime import datetime

from mtpmanager.app.podcast_ops import (
    pick_new_not_on_device,
    podcast_episode_title,
    podcast_episode_tracknumber,
    pub_date_to_yyyymmdd,
    upsert_scheduled_day_playlist,
)
from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.playlists import get_playlist_by_name
from mtpmanager.domain.audio_encode import get_preset
from mtpmanager.infra.podcast_index import (
    create_or_update_podcast,
    delete_podcast,
    get_episode,
    get_podcast,
    get_tracks_by_podcast_guids,
    known_feed_guids,
    known_podcast_guids,
    list_episodes,
    list_podcasts,
    normalize_feed_url,
    set_episode_local_path,
    set_podcast_audio_encode,
    set_podcast_auto_last_run,
    set_podcast_auto_settings,
    set_podcast_playback_speed,
    upsert_episodes,
)


class PodcastIndexTests(unittest.TestCase):
    def test_normalize_feed_url(self) -> None:
        self.assertEqual(
            normalize_feed_url("HTTPS://Example.COM/feed/"),
            "https://example.com/feed",
        )

    def test_pub_date_tracknumber_yyyymmdd(self) -> None:
        self.assertEqual(pub_date_to_yyyymmdd("2026-08-08"), "20260808")
        self.assertEqual(
            pub_date_to_yyyymmdd("2026-08-08T12:00:00Z"), "20260808"
        )
        self.assertEqual(pub_date_to_yyyymmdd(""), "")
        from mtpmanager.infra.podcast_index import PodcastEpisode

        ep = PodcastEpisode(
            id=1,
            podcast_id=1,
            guid="a" * 32,
            feed_guid="x",
            pub_date="2026-08-08",
            episode_index=3,
        )
        self.assertEqual(
            podcast_episode_tracknumber(ep, use_date=True), "20260808"
        )
        self.assertEqual(
            podcast_episode_tracknumber(ep, use_date=False), "3"
        )
        meta = TrackMetadata(tracknumber="20260808")
        self.assertEqual(meta.tracknumber_int(), 20260808)
        # MTP c_ushort: pack date ordinal, invert so newer → smaller track #.
        ordinal = (2026 - 2000) * 512 + 8 * 32 + 8
        packed = meta.tracknumber_for_mtp()
        self.assertLessEqual(packed, 0xFFFF)
        self.assertEqual(packed, 0xFFFF - ordinal)
        older = TrackMetadata(tracknumber="20250101").tracknumber_for_mtp()
        newer = TrackMetadata(tracknumber="20260808").tracknumber_for_mtp()
        self.assertLess(newer, older)

    def test_pub_date_title_prefix(self) -> None:
        from mtpmanager.infra.podcast_index import PodcastEpisode

        ep = PodcastEpisode(
            id=1,
            podcast_id=1,
            guid="a" * 32,
            feed_guid="x",
            title="Morning Brief",
            pub_date="2026-08-08",
            episode_index=3,
        )
        self.assertEqual(
            podcast_episode_title(ep, use_date=True),
            "20260808 Morning Brief",
        )
        self.assertEqual(
            podcast_episode_title(ep, use_date=False),
            "Morning Brief",
        )
        # Already prefixed: do not double-prefix.
        ep2 = PodcastEpisode(
            id=2,
            podcast_id=1,
            guid="b" * 32,
            feed_guid="y",
            title="20260808 Morning Brief",
            pub_date="2026-08-08",
        )
        self.assertEqual(
            podcast_episode_title(ep2, use_date=True),
            "20260808 Morning Brief",
        )
        # No date → bare title.
        ep3 = PodcastEpisode(
            id=3,
            podcast_id=1,
            guid="c" * 32,
            feed_guid="z",
            title="No Date Ep",
            pub_date="",
        )
        self.assertEqual(
            podcast_episode_title(ep3, use_date=True),
            "No Date Ep",
        )

    def test_crud_and_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            p = create_or_update_podcast(
                feed_url="https://example.com/rss",
                title="Show",
                author="Host",
                path=db,
            )
            self.assertEqual(p.title, "Show")
            n = upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "a",
                        "title": "Old",
                        "pub_date": "2024-01-01T00:00:00Z",
                        "enclosure_url": "https://x/a.mp3",
                    },
                    {
                        "feed_guid": "b",
                        "title": "New",
                        "pub_date": "2025-01-01T00:00:00Z",
                        "enclosure_url": "https://x/b.mp3",
                    },
                    {
                        "feed_guid": "v",
                        "title": "Video Ep",
                        "pub_date": "2025-02-01T00:00:00Z",
                        "enclosure_url": "https://x/v.mp4",
                        "enclosure_type": "video/mp4",
                        "is_video": True,
                        "video_enclosure_url": "https://x/v.mp4",
                        "video_enclosure_type": "video/mp4",
                    },
                ],
                path=db,
            )
            self.assertEqual(n, 3)
            eps = list_episodes(p.id, path=db)
            self.assertEqual(len(eps), 3)
            self.assertEqual(eps[0].title, "Video Ep")
            self.assertTrue(eps[0].is_video)
            self.assertEqual(eps[0].video_enclosure_url, "https://x/v.mp4")
            self.assertEqual(eps[1].title, "New")
            self.assertFalse(eps[1].is_video)
            self.assertTrue(eps[1].guid)
            # Upsert same feed_guid → no new row; metadata + is_video refresh
            n2 = upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "b",
                        "title": "New Renamed",
                        "pub_date": "2025-01-01T00:00:00Z",
                        "is_video": True,
                        "video_enclosure_url": "https://x/b.mp4",
                        "video_enclosure_type": "video/mp4",
                    }
                ],
                path=db,
            )
            self.assertEqual(n2, 0)
            eps2 = list_episodes(p.id, path=db)
            renamed = next(e for e in eps2 if e.feed_guid == "b")
            self.assertEqual(renamed.title, "New Renamed")
            self.assertTrue(renamed.is_video)
            self.assertEqual(renamed.video_enclosure_url, "https://x/b.mp4")
            self.assertEqual(known_feed_guids(p.id, path=db), {"a", "b", "v"})
            self.assertEqual(len(list_podcasts(path=db)), 1)
            self.assertTrue(delete_podcast(p.id, path=db))
            self.assertIsNone(get_podcast(p.id, path=db))
            self.assertEqual(list_episodes(p.id, path=db), [])

    def test_per_show_audio_encode(self) -> None:
        preset = get_preset("mp3_cbr_64")
        assert preset is not None
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            p = create_or_update_podcast(
                feed_url="https://example.com/rpg",
                title="RPG Show",
                path=db,
            )
            self.assertIsNone(p.audio_encode)
            self.assertEqual(p.playback_speed, 1.0)
            updated = set_podcast_audio_encode(p.id, preset.settings, path=db)
            assert updated is not None
            self.assertIsNotNone(updated.audio_encode)
            self.assertEqual(updated.audio_encode.preset_id, "mp3_cbr_64")
            again = get_podcast(p.id, path=db)
            assert again is not None and again.audio_encode is not None
            self.assertEqual(again.audio_encode.bitrate_kbps, 64)
            cleared = set_podcast_audio_encode(p.id, None, path=db)
            assert cleared is not None
            self.assertIsNone(cleared.audio_encode)
            sped = set_podcast_playback_speed(p.id, 1.5, path=db)
            assert sped is not None
            self.assertEqual(sped.playback_speed, 1.5)
            reloaded = get_podcast(p.id, path=db)
            assert reloaded is not None
            self.assertEqual(reloaded.playback_speed, 1.5)

    def test_auto_settings_and_retrieved_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            p = create_or_update_podcast(
                feed_url="https://example.com/auto",
                title="Auto Show",
                path=db,
            )
            self.assertTrue(p.auto_update)
            updated = set_podcast_auto_settings(
                p.id,
                auto_update=False,
                schedule_time="21:30",
                path=db,
            )
            assert updated is not None
            self.assertFalse(updated.auto_update)
            self.assertEqual(updated.schedule_time, "21:30")
            set_podcast_auto_last_run(p.id, "2026-08-05", path=db)
            again = get_podcast(p.id, path=db)
            assert again is not None
            self.assertEqual(again.auto_last_run_local_date, "2026-08-05")

            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "e1",
                        "title": "Ep1",
                        "pub_date": "2026-08-01T00:00:00Z",
                        "enclosure_url": "https://x/e1.mp3",
                    },
                    {
                        "feed_guid": "e2",
                        "title": "Ep2",
                        "pub_date": "2026-08-04T00:00:00Z",
                        "enclosure_url": "https://x/e2.mp3",
                    },
                ],
                path=db,
            )
            eps = list_episodes(p.id, path=db)
            self.assertEqual(len(eps), 2)
            newest = eps[0]
            set_episode_local_path(
                newest.id,
                "/tmp/fake.mp3",
                path=db,
                stamp_retrieved=True,
                mark_pending_device_sync=True,
            )
            got = get_episode(newest.id, path=db)
            assert got is not None
            self.assertTrue(got.retrieved_at)
            self.assertTrue(got.pending_device_sync)
            first_stamp = got.retrieved_at
            set_episode_local_path(
                newest.id, "/tmp/fake.mp3", path=db, stamp_retrieved=True
            )
            got2 = get_episode(newest.id, path=db)
            assert got2 is not None
            self.assertEqual(got2.retrieved_at, first_stamp)

            picks = pick_new_not_on_device(p.id, set(), limit=2, path=db)
            self.assertEqual(len(picks), 2)
            picks2 = pick_new_not_on_device(
                p.id, {newest.guid}, limit=2, path=db
            )
            self.assertEqual(len(picks2), 1)
            self.assertEqual(picks2[0].feed_guid, "e1")
            # Since window excludes older episode — limit does not backfill.
            picks3 = pick_new_not_on_device(
                p.id, set(), limit=2, path=db, since_iso="2026-08-03"
            )
            self.assertEqual(len(picks3), 1)
            self.assertEqual(picks3[0].feed_guid, "e2")
            # Only one episode in window even when limit=2 (no older fillers).
            picks4 = pick_new_not_on_device(
                p.id, set(), limit=2, path=db, since_iso="2026-08-04"
            )
            self.assertEqual(len(picks4), 1)
            self.assertEqual(picks4[0].feed_guid, "e2")
            # Undated items must not pad the since-window quota.
            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "e-undated",
                        "title": "No Date",
                        "pub_date": "",
                        "enclosure_url": "https://x/e0.mp3",
                    },
                ],
                path=db,
            )
            picks5 = pick_new_not_on_device(
                p.id, set(), limit=2, path=db, since_iso="2026-08-03"
            )
            self.assertEqual(len(picks5), 1)
            self.assertEqual(picks5[0].feed_guid, "e2")

    def test_get_tracks_by_podcast_guids(self) -> None:
        """Device ObjectFileName GUID → podcast_episodes metadata (not tracks)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            p = create_or_update_podcast(
                feed_url="https://example.com/show",
                title="Cool Show",
                author="Host Name",
                path=db,
            )
            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "ep-a",
                        "title": "Episode Alpha",
                        "pub_date": "2026-08-01T12:00:00Z",
                        "enclosure_url": "https://x/a.mp3",
                        "duration_sec": 120,
                    }
                ],
                path=db,
            )
            eps = list_episodes(p.id, path=db)
            self.assertEqual(len(eps), 1)
            guid = eps[0].guid
            self.assertTrue(guid)

            by = get_tracks_by_podcast_guids([guid, "deadbeef" * 4], path=db)
            self.assertIn(guid, by)
            self.assertEqual(len(by), 1)
            t = by[guid]
            self.assertEqual(t.guid, guid)
            self.assertEqual(t.meta.title, "Episode Alpha")
            self.assertEqual(t.meta.album, "Cool Show")
            self.assertEqual(t.meta.artist, "Host Name")
            self.assertEqual(t.meta.genre, "Podcast")
            self.assertTrue(t.path.startswith("podcast:"))

            known = known_podcast_guids([guid, "0" * 32], path=db)
            self.assertEqual(known, {guid})

    def test_upsert_scheduled_day_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            p = create_or_update_podcast(
                feed_url="https://example.com/rss2",
                title="Daily",
                author="Net",
                path=db,
            )
            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "d1",
                        "title": "One",
                        "pub_date": "2026-08-05T08:00:00Z",
                        "enclosure_url": "https://x/1.mp3",
                    },
                    {
                        "feed_guid": "d2",
                        "title": "Two",
                        "pub_date": "2026-08-05T09:00:00Z",
                        "enclosure_url": "https://x/2.mp3",
                    },
                ],
                path=db,
            )
            eps = list_episodes(p.id, path=db)
            when = datetime(2026, 8, 5, 10, 0, 0)
            day = upsert_scheduled_day_playlist(eps[:1], when=when, path=db)
            self.assertIsNotNone(day)
            assert day is not None
            self.assertEqual(day.name, "Podcasts Aug 5, 2026")
            self.assertEqual(len(day.guids), 1)
            self.assertEqual(day.added, 1)
            hit = get_playlist_by_name(day.name, path=db)
            self.assertIsNotNone(hit)

            day2 = upsert_scheduled_day_playlist(eps, when=when, path=db)
            self.assertIsNotNone(day2)
            assert day2 is not None
            self.assertEqual(day2.name, day.name)
            self.assertEqual(len(day2.guids), 2)
            self.assertEqual(day2.added, 1)


if __name__ == "__main__":
    unittest.main()
