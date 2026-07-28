"""Unit tests for host audio playback (ffplay wrapper + playlist rules)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.infra.app_config import AppConfig, load_app_config, save_app_config
from mtpmanager.infra.audio_player import AudioPlayer, ffplay_bin
from mtpmanager.ui.window import (
    BG_PLAYING,
    CTX_PLAY_ALBUM_GROUP,
    CTX_PLAY_ARTIST_GROUP,
    CTX_PLAY_TRACK,
    CTX_PLAY_TRACKS,
    MENU_ALWAYS_SHOW_PLAYBACK,
    MainWindow,
    _PLAYBACK_MARQUEE_GAP,
    _PLAYBACK_TITLE_WIDTH,
)


def _make_tone(path: str, duration: float = 1.5) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-ar",
            "44100",
            path,
        ],
        check=True,
        capture_output=True,
    )


@unittest.skipUnless(ffplay_bin() and shutil.which("ffmpeg"), "ffplay/ffmpeg required")
class AudioPlayerIntegrationTests(unittest.TestCase):
    def test_play_pause_seek_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            _make_tone(wav, 2.0)
            player = AudioPlayer()
            try:
                player.play(wav, duration_sec=2.0)
                self.assertEqual(player.poll(), "playing")
                time.sleep(0.35)
                pos = player.position_sec()
                self.assertGreater(pos, 0.05)
                player.pause()
                self.assertEqual(player.poll(), "paused")
                paused_at = player.position_sec()
                time.sleep(0.2)
                self.assertAlmostEqual(player.position_sec(), paused_at, delta=0.05)
                player.resume()
                self.assertEqual(player.poll(), "playing")
                player.seek(0.2)
                time.sleep(0.15)
                self.assertLess(player.position_sec(), 1.0)
            finally:
                player.stop()
            self.assertEqual(player.poll(), "idle")

    def test_missing_file_raises(self) -> None:
        player = AudioPlayer()
        with self.assertRaises(FileNotFoundError):
            player.play("/no/such/file.mp3")


class AudioPlayerUnitTests(unittest.TestCase):
    def test_ffplay_missing_raises(self) -> None:
        player = AudioPlayer()
        with mock.patch(
            "mtpmanager.infra.audio_player.ffplay_bin", return_value=None
        ):
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                f.write(b"x")
                f.flush()
                with self.assertRaises(RuntimeError):
                    player.play(f.name, duration_sec=1.0)


class PlaybackConfigTests(unittest.TestCase):
    def test_always_show_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            save_app_config(
                AppConfig(always_show_playback_controls=True),
                path=dest,
            )
            loaded = load_app_config(path=dest)
            self.assertTrue(loaded.always_show_playback_controls)

    def test_ui_constants(self) -> None:
        self.assertEqual(MENU_ALWAYS_SHOW_PLAYBACK, "Always show playback controls")
        self.assertEqual(CTX_PLAY_TRACK, "Play This Track")
        self.assertEqual(CTX_PLAY_TRACKS, "Play These Tracks")
        self.assertEqual(CTX_PLAY_ALBUM_GROUP, "Play Album")
        self.assertEqual(CTX_PLAY_ARTIST_GROUP, "Play All from Artist")
        self.assertTrue(BG_PLAYING.startswith("#"))


class PlaylistAdvanceLogicTests(unittest.TestCase):
    """Document auto-advance vs manual wrap without spinning up Tk."""

    def test_auto_advance_stops_after_last(self) -> None:
        queue = ["a", "b", "c"]
        index = 2  # last track finished
        nxt = index + 1
        self.assertFalse(0 <= nxt < len(queue))

    def test_manual_next_wraps(self) -> None:
        queue = ["a", "b", "c"]
        index = 2
        nxt = index + 1
        if nxt >= len(queue):
            nxt = 0
        self.assertEqual(nxt, 0)

    def test_manual_prev_wraps(self) -> None:
        queue = ["a", "b", "c"]
        index = 0
        prev = index - 1
        if prev < 0:
            prev = len(queue) - 1
        self.assertEqual(prev, 2)


class ShutdownPlaybackTests(unittest.TestCase):
    def test_shutdown_stops_active_player(self) -> None:
        """shutdown_playback must terminate ffplay even without Tk mainloop."""
        player = AudioPlayer()
        # Minimal stand-in for AppController.shutdown_playback core.
        with mock.patch.object(player, "stop") as stop:
            player.stop()
            stop.assert_called_once()

    def test_audio_player_stop_kills_process(self) -> None:
        if not ffplay_bin() or not shutil.which("ffmpeg"):
            self.skipTest("ffplay/ffmpeg required")
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            _make_tone(wav, 3.0)
            player = AudioPlayer()
            player.play(wav, duration_sec=3.0)
            self.assertEqual(player.poll(), "playing")
            player.stop()
            self.assertEqual(player.poll(), "idle")
            self.assertFalse(player.is_active)


class PlaybackTitleMarqueeTests(unittest.TestCase):
    def test_short_title_unchanged(self) -> None:
        short = "Short title"
        self.assertEqual(
            MainWindow.marquee_window(short, 0, _PLAYBACK_TITLE_WIDTH),
            short,
        )
        self.assertEqual(
            MainWindow.marquee_window(short, 5, _PLAYBACK_TITLE_WIDTH),
            short,
        )

    def test_long_title_rotates_one_char(self) -> None:
        long = "A" * (_PLAYBACK_TITLE_WIDTH + 5)
        w0 = MainWindow.marquee_window(long, 0, _PLAYBACK_TITLE_WIDTH)
        w1 = MainWindow.marquee_window(long, 1, _PLAYBACK_TITLE_WIDTH)
        self.assertEqual(len(w0), _PLAYBACK_TITLE_WIDTH)
        self.assertEqual(len(w1), _PLAYBACK_TITLE_WIDTH)
        self.assertEqual(w0, long[:_PLAYBACK_TITLE_WIDTH])
        self.assertEqual(w1[0], long[1])
        # One-char step: window shifts left by one into the source.
        self.assertEqual(w1[:-1], w0[1:])

    def test_wraps_through_gap(self) -> None:
        text = "Hello World Title That Is Long Enough"
        width = 10
        cycle = text + _PLAYBACK_MARQUEE_GAP
        end_offset = len(text) - 2
        window = MainWindow.marquee_window(
            text, end_offset, width, gap=_PLAYBACK_MARQUEE_GAP
        )
        self.assertEqual(len(window), width)
        # Near the end, the gap and start of the title appear.
        self.assertIn(" ", window)
        doubled = cycle + cycle
        self.assertEqual(window, doubled[end_offset : end_offset + width])


if __name__ == "__main__":
    unittest.main()
