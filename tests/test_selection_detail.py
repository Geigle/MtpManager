"""Unit tests: left-panel selection detail formatting."""

from __future__ import annotations

import unittest

from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.ui.formatting import (
    album_selection_detail,
    artist_selection_detail,
    format_duration,
    multi_selection_detail,
    track_selection_detail,
)


class FormatDurationTests(unittest.TestCase):
    def test_empty_for_zero(self) -> None:
        self.assertEqual(format_duration(0), "")
        self.assertEqual(format_duration(None), "")

    def test_mm_ss(self) -> None:
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(12.4), "0:12")

    def test_h_mm_ss(self) -> None:
        self.assertEqual(format_duration(3661), "1:01:01")


class SelectionDetailTests(unittest.TestCase):
    def test_track_detail_lines(self) -> None:
        track = Track(
            path="/music/a.flac",
            meta=TrackMetadata(
                title="Day Six: Childhood",
                artist="Ayreon",
                album="The Human Equation",
                tracknumber="6",
                date="2004",
                length_sec=312,
                genre="Progressive Metal",
            ),
        )
        text = track_selection_detail(track)
        self.assertIn("Day Six: Childhood", text)
        self.assertIn("Ayreon", text)
        self.assertIn("The Human Equation", text)
        self.assertIn("2004", text)
        self.assertIn("#6", text)
        self.assertIn("5:12", text)
        self.assertIn("Progressive Metal", text)
        # Path is shown separately (italic label), not inside the detail text.
        self.assertNotIn("/music/a.flac", text)

    def test_track_detail_skips_unknown_genre(self) -> None:
        track = Track(
            path="/x.mp3",
            meta=TrackMetadata(title="T", genre="Unknown Genre"),
        )
        text = track_selection_detail(track)
        self.assertNotIn("Unknown Genre", text)

    def test_artist_detail(self) -> None:
        self.assertEqual(
            artist_selection_detail("Ayreon", 42),
            "Ayreon\n42 tracks",
        )
        self.assertEqual(
            artist_selection_detail("Solo", 1),
            "Solo\n1 track",
        )

    def test_album_detail(self) -> None:
        text = album_selection_detail(
            "The Human Equation",
            artist="Ayreon",
            track_count=20,
            year="2004",
        )
        self.assertEqual(
            text,
            "The Human Equation\nAyreon\n2004 · 20 tracks",
        )

    def test_multi_selection(self) -> None:
        self.assertEqual(multi_selection_detail(3), "3 tracks selected")
        self.assertEqual(multi_selection_detail(1), "1 track selected")


if __name__ == "__main__":
    unittest.main()
