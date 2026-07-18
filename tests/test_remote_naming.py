"""Unit tests for shared MTP remote naming (no device required)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    MAX_REMOTE_BASENAME,
    ZEN_VISION_M_FOLDER_IDS,
    ZEN_VISION_M_FOLDER_NAMES,
    build_remote_path,
    sanitize_component,
    split_remote_path,
    year_arg,
)


def _meta(**kwargs) -> TrackMetadata:
    defaults = dict(
        artist="Mick Gordon",
        album="Doom (Original Game Soundtrack)",
        title="Flesh & Metal",
        tracknumber="08",
        date="2016-09-28",
    )
    defaults.update(kwargs)
    return TrackMetadata(**defaults)


class RemoteNamingTests(unittest.TestCase):
    def test_build_remote_path_uses_music_folder_and_short_name(self) -> None:
        remote = build_remote_path(_meta(), ".mp3")
        parent, basename = split_remote_path(remote)
        self.assertEqual(parent, DEFAULT_MUSIC_FOLDER_ID)
        self.assertTrue(basename.endswith(".mp3"))
        self.assertLessEqual(len(basename), MAX_REMOTE_BASENAME)
        self.assertIn("08", basename)
        self.assertIn("Flesh", basename)
        self.assertNotIn("&", basename)
        self.assertNotIn("Mick Gordon", basename)  # long artist-album form gone

    def test_sanitize_strips_unsafe_chars(self) -> None:
        cleaned = sanitize_component('a/b\\c:d*e?f"g<h>i|j&k', 40)
        for ch in r'/\:*?"<>|&':
            self.assertNotIn(ch, cleaned)

    def test_basename_truncated_under_limit(self) -> None:
        meta = _meta(title="X" * 200, tracknumber="01")
        remote = build_remote_path(meta, ".mp3", max_basename=56)
        _, basename = split_remote_path(remote)
        self.assertLessEqual(len(basename), 56)

    def test_year_arg_extracts_four_digit_year(self) -> None:
        self.assertEqual(year_arg("2016-09-28"), "2016")
        self.assertEqual(year_arg("2016"), "2016")
        self.assertEqual(year_arg(""), "")

    def test_split_remote_path(self) -> None:
        self.assertEqual(split_remote_path("100/08 Title.mp3"), (100, "08 Title.mp3"))
        self.assertEqual(
            split_remote_path("bare.mp3"),
            (DEFAULT_MUSIC_FOLDER_ID, "bare.mp3"),
        )

    def test_default_storage_id_is_zen_media(self) -> None:
        self.assertEqual(DEFAULT_STORAGE_ID, 0x00010001)

    def test_zen_vision_m_folder_map(self) -> None:
        """Device → List Folders layout on Creative ZEN Vision:M (reference)."""
        expected = {
            100: "Music",
            104: "My Playlists",
            108: "My Recordings",
            112: "My Organizer",
            116: "Pictures",
            120: "Video",
            124: "TV",
            128: "ZENcast",
            132: "My Slideshows",
        }
        self.assertEqual(dict(ZEN_VISION_M_FOLDER_IDS), expected)
        self.assertEqual(DEFAULT_MUSIC_FOLDER_ID, 100)
        self.assertEqual(ZEN_VISION_M_FOLDER_IDS[DEFAULT_MUSIC_FOLDER_ID], "Music")
        self.assertEqual(ZEN_VISION_M_FOLDER_NAMES["music"], 100)
        self.assertEqual(ZEN_VISION_M_FOLDER_NAMES["zencast"], 128)
        # Immutable reference map — do not invent nested remote paths.
        with self.assertRaises(TypeError):
            ZEN_VISION_M_FOLDER_IDS[100] = "Nope"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
