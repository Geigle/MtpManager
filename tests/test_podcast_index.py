"""Podcast SQLite CRUD tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.podcast_index import (
    create_or_update_podcast,
    delete_podcast,
    get_podcast,
    known_feed_guids,
    list_episodes,
    list_podcasts,
    normalize_feed_url,
    upsert_episodes,
)


class PodcastIndexTests(unittest.TestCase):
    def test_normalize_feed_url(self) -> None:
        self.assertEqual(
            normalize_feed_url("HTTPS://Example.COM/feed/"),
            "https://example.com/feed",
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


if __name__ == "__main__":
    unittest.main()
