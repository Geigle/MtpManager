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
                ],
                path=db,
            )
            self.assertEqual(n, 2)
            eps = list_episodes(p.id, path=db)
            self.assertEqual(len(eps), 2)
            self.assertEqual(eps[0].title, "New")
            self.assertTrue(eps[0].guid)
            # Upsert same feed_guid → no new row
            n2 = upsert_episodes(
                p.id,
                [{"feed_guid": "b", "title": "New Renamed", "pub_date": "2025-01-01T00:00:00Z"}],
                path=db,
            )
            self.assertEqual(n2, 0)
            eps2 = list_episodes(p.id, path=db)
            self.assertEqual(eps2[0].title, "New Renamed")
            self.assertEqual(known_feed_guids(p.id, path=db), {"a", "b"})
            self.assertEqual(len(list_podcasts(path=db)), 1)
            self.assertTrue(delete_podcast(p.id, path=db))
            self.assertIsNone(get_podcast(p.id, path=db))
            self.assertEqual(list_episodes(p.id, path=db), [])


if __name__ == "__main__":
    unittest.main()
