"""Unit tests for durable app config."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.app_config import (
    AppConfig,
    load_app_config,
    save_app_config,
)


class AppConfigTests(unittest.TestCase):
    def test_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_app_config(path=Path(tmp) / "nope.json")
            self.assertEqual(cfg.normalized_send_format(), "mp3")
            self.assertFalse(cfg.stable_mode)
            self.assertFalse(cfg.enable_experimental_tools)
            self.assertFalse(cfg.store_tracks_in_artist_folder)
            self.assertFalse(cfg.store_tracks_in_album_folder)
            self.assertFalse(cfg.show_broken_video_presets)
            self.assertFalse(cfg.always_show_playback_controls)
            self.assertFalse(cfg.store_podcasts_in_show_folders)
            self.assertFalse(cfg.allow_video_podcasts_to_sync)
            self.assertFalse(cfg.sync_audio_podcasts_as_video)
            self.assertEqual(cfg.audio_podcast_still_fps, 2.0)
            self.assertEqual(cfg.audio_podcast_still_width, 128)
            self.assertEqual(cfg.audio_podcast_still_height, 96)
            self.assertTrue(cfg.keep_downloaded_podcasts)
            self.assertEqual(cfg.active_mode(), "experimental")

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            save_app_config(
                AppConfig(
                    send_format="wma",
                    stable_mode=True,
                    enable_experimental_tools=True,
                    store_tracks_in_artist_folder=True,
                    store_tracks_in_album_folder=True,
                    show_broken_video_presets=True,
                    always_show_playback_controls=True,
                    allow_video_podcasts_to_sync=True,
                    sync_audio_podcasts_as_video=True,
                    audio_podcast_still_fps=2.0,
                    audio_podcast_still_width=128,
                    audio_podcast_still_height=96,
                    keep_downloaded_podcasts=False,
                ),
                path=dest,
            )
            loaded = load_app_config(path=dest)
            self.assertEqual(loaded.normalized_send_format(), "wma")
            self.assertTrue(loaded.stable_mode)
            self.assertTrue(loaded.enable_experimental_tools)
            self.assertTrue(loaded.store_tracks_in_artist_folder)
            self.assertTrue(loaded.store_tracks_in_album_folder)
            self.assertTrue(loaded.show_broken_video_presets)
            self.assertTrue(loaded.always_show_playback_controls)
            self.assertTrue(loaded.allow_video_podcasts_to_sync)
            self.assertTrue(loaded.sync_audio_podcasts_as_video)
            self.assertEqual(loaded.audio_podcast_still_fps, 2.0)
            self.assertEqual(loaded.audio_podcast_still_width, 128)
            self.assertEqual(loaded.audio_podcast_still_height, 96)
            self.assertFalse(loaded.keep_downloaded_podcasts)
            self.assertEqual(loaded.active_mode(), "stable")
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["send_format"], "wma")
            self.assertTrue(data["stable_mode"])
            self.assertTrue(data["enable_experimental_tools"])
            self.assertTrue(data["store_tracks_in_artist_folder"])
            self.assertTrue(data["store_tracks_in_album_folder"])
            self.assertTrue(data["show_broken_video_presets"])
            self.assertTrue(data["always_show_playback_controls"])
            self.assertTrue(data["allow_video_podcasts_to_sync"])
            self.assertTrue(data["sync_audio_podcasts_as_video"])
            self.assertEqual(data["audio_podcast_still_fps"], 2.0)
            self.assertEqual(data["audio_podcast_still_width"], 128)
            self.assertEqual(data["audio_podcast_still_height"], 96)
            self.assertFalse(data["keep_downloaded_podcasts"])

    def test_album_folder_requires_artist_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "store_tracks_in_artist_folder": False,
                        "store_tracks_in_album_folder": True,
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_app_config(path=dest)
            self.assertFalse(cfg.store_tracks_in_artist_folder)
            self.assertFalse(cfg.store_tracks_in_album_folder)

            save_app_config(
                AppConfig(
                    store_tracks_in_artist_folder=False,
                    store_tracks_in_album_folder=True,
                ),
                path=dest,
            )
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertFalse(data["store_tracks_in_album_folder"])

    def test_invalid_format_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text(
                json.dumps({"version": 1, "send_format": "flac"}),
                encoding="utf-8",
            )
            cfg = load_app_config(path=dest)
            self.assertEqual(cfg.normalized_send_format(), "mp3")
            self.assertFalse(cfg.stable_mode)

    def test_wav_is_valid_send_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            save_app_config(AppConfig(send_format="wav"), path=dest)
            loaded = load_app_config(path=dest)
            self.assertEqual(loaded.normalized_send_format(), "wav")


if __name__ == "__main__":
    unittest.main()
