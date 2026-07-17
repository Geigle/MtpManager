"""Unit tests for tag reading (mutagen adapters)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from mtpmanager.infra.mutagen_tags import (
    _vorbis_get,
    read_metadata,
)


class VorbisGetTests(unittest.TestCase):
    def test_case_insensitive_plain_dict(self) -> None:
        tags = {"artist": ["Blind Guardian"], "TITLE": ["The Ninth Wave"]}
        self.assertEqual(_vorbis_get(tags, "ARTIST"), "Blind Guardian")
        self.assertEqual(_vorbis_get(tags, "TITLE"), "The Ninth Wave")

    def test_tracknumber_padding_and_total(self) -> None:
        tags = {"TRACKNUMBER": ["3/11"]}
        self.assertEqual(_vorbis_get(tags, "TRACKNUMBER"), "03")

    def test_albumartist_falls_back_to_artist(self) -> None:
        tags = {"ARTIST": ["Solo"]}
        self.assertEqual(_vorbis_get(tags, "ALBUMARTIST"), "Solo")

    def test_missing_defaults(self) -> None:
        self.assertEqual(_vorbis_get({}, "ARTIST"), "Unknown Artist")
        self.assertEqual(_vorbis_get(None, "TITLE"), "Unknown Title")
        self.assertEqual(_vorbis_get({}, "TRACKNUMBER"), "01")


class ReadMetadataRoutingTests(unittest.TestCase):
    def test_ogg_extension_uses_ogg_reader(self) -> None:
        expected = MagicMock()
        with (
            patch("mtpmanager.infra.mutagen_tags.os.path.isfile", return_value=True),
            patch(
                "mtpmanager.infra.mutagen_tags._from_ogg",
                return_value=expected,
            ) as from_ogg,
            patch("mtpmanager.infra.mutagen_tags._from_id3") as from_id3,
            patch("mtpmanager.infra.mutagen_tags._from_flac") as from_flac,
        ):
            result = read_metadata("/music/album/track.ogg")
        self.assertIs(result, expected)
        from_ogg.assert_called_once_with("/music/album/track.ogg")
        from_id3.assert_not_called()
        from_flac.assert_not_called()

    def test_vorbis_extension_uses_ogg_reader(self) -> None:
        expected = MagicMock()
        with (
            patch("mtpmanager.infra.mutagen_tags.os.path.isfile", return_value=True),
            patch(
                "mtpmanager.infra.mutagen_tags._from_ogg",
                return_value=expected,
            ) as from_ogg,
        ):
            result = read_metadata("/music/track.vorbis")
        self.assertIs(result, expected)
        from_ogg.assert_called_once()


@unittest.skipUnless(
    os.path.isfile(
        "/Volumes/music/0 Music/Blind Guardian/Beyond The Red Mirror/"
        "01 - The Ninth Wave - Beyond The Red Mirror - Blind Guardian.ogg"
    ),
    "sample Ogg Vorbis library path not mounted",
)
class RealOggVorbisIntegrationTests(unittest.TestCase):
    PATH = (
        "/Volumes/music/0 Music/Blind Guardian/Beyond The Red Mirror/"
        "01 - The Ninth Wave - Beyond The Red Mirror - Blind Guardian.ogg"
    )

    def test_reads_blind_guardian_tags(self) -> None:
        md = read_metadata(self.PATH)
        self.assertEqual(md.artist, "Blind Guardian")
        self.assertEqual(md.album, "Beyond The Red Mirror")
        self.assertEqual(md.title, "The Ninth Wave")
        self.assertEqual(md.genre, "Power Metal")
        self.assertEqual(md.tracknumber, "01")
        self.assertEqual(md.date, "2015")
        self.assertGreater(md.length_sec, 500.0)
        self.assertGreater(md.bitrate, 0)
        self.assertEqual(md.sample_rate, 44100)
        self.assertEqual(md.channels, 2)


if __name__ == "__main__":
    unittest.main()
