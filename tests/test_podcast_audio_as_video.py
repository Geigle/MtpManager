"""Prepare audio podcasts as still-image video jobs (experimental)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtpmanager.app.podcast_ops import prepare_episodes_for_sync
from mtpmanager.infra.podcast_index import (
    create_or_update_podcast,
    upsert_episodes,
    list_episodes,
    set_episode_local_path,
)


class PrepareAudioAsVideoTests(unittest.TestCase):
    def test_audio_as_video_builds_still_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "library_index.db"
            data = root / "data"
            data.mkdir()
            p = create_or_update_podcast(
                feed_url="https://example.com/rss",
                title="Show",
                author="Host",
                image_url="https://example.com/art.jpg",
                path=db,
            )
            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "a1",
                        "title": "Ep One",
                        "enclosure_url": "https://x/a.mp3",
                        "enclosure_type": "audio/mpeg",
                    }
                ],
                path=db,
            )
            ep = list_episodes(p.id, path=db)[0]
            # Plant a fake downloaded audio file under the episode cache.
            from mtpmanager.infra.podcast_index import episode_cache_dir

            cache = episode_cache_dir(p.id, data_dir=data)
            audio = cache / f"{ep.guid}.mp3"
            audio.write_bytes(b"ID3fake")
            set_episode_local_path(ep.id, str(audio), path=db)
            ep = list_episodes(p.id, path=db)[0]

            art_path = cache / "artwork.jpg"
            art_path.write_bytes(b"\xff\xd8fakejpg")

            with patch(
                "mtpmanager.app.podcast_ops.ensure_podcast_artwork",
                return_value=str(art_path),
            ):
                prep = prepare_episodes_for_sync(
                    [ep],
                    path=db,
                    data_dir=data,
                    allow_video=False,
                    audio_as_video=True,
                    target_audio_format="mp3",
                )
            self.assertEqual(len(prep.audio_tracks), 0)
            self.assertEqual(len(prep.video_jobs), 1)
            job = prep.video_jobs[0]
            self.assertTrue(job.from_audio_still)
            self.assertEqual(job.local_path, str(audio))
            self.assertEqual(job.image_path, str(art_path))
            self.assertEqual(job.episode.title, "Ep One")

    def test_default_audio_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "library_index.db"
            data = root / "data"
            data.mkdir()
            p = create_or_update_podcast(
                feed_url="https://example.com/rss2",
                title="Show2",
                path=db,
            )
            upsert_episodes(
                p.id,
                [
                    {
                        "feed_guid": "b1",
                        "title": "Audio Only",
                        "enclosure_url": "https://x/b.mp3",
                        "enclosure_type": "audio/mpeg",
                    }
                ],
                path=db,
            )
            ep = list_episodes(p.id, path=db)[0]
            from mtpmanager.infra.podcast_index import episode_cache_dir

            cache = episode_cache_dir(p.id, data_dir=data)
            audio = cache / f"{ep.guid}.mp3"
            audio.write_bytes(b"ID3fake")
            set_episode_local_path(ep.id, str(audio), path=db)
            ep = list_episodes(p.id, path=db)[0]

            prep = prepare_episodes_for_sync(
                [ep],
                path=db,
                data_dir=data,
                allow_video=False,
                audio_as_video=False,
                target_audio_format="mp3",
            )
            self.assertEqual(len(prep.video_jobs), 0)
            self.assertEqual(len(prep.audio_tracks), 1)
            self.assertEqual(prep.audio_tracks[0].meta.genre, "Podcast")


if __name__ == "__main__":
    unittest.main()
