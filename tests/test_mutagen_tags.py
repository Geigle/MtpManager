"""Unit tests for tag reading (mutagen adapters)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from mtpmanager.infra.mutagen_tags import (
    _asf_get,
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


class AsfGetTests(unittest.TestCase):
    """WMA tags use Title/Author/WM/* — not EasyID3-style artist/album keys."""

    def test_windows_media_keys(self) -> None:
        tags = {
            "Title": ["Be My Escape"],
            "Author": ["Relient K"],
            "WM/AlbumTitle": ["Mmhmm"],
            "WM/AlbumArtist": ["Relient K"],
            "WM/Genre": ["Rock"],
            "WM/Composer": ["Matthew Thiessen"],
            "WM/TrackNumber": [2],
            "WM/Year": ["2004"],
        }
        self.assertEqual(_asf_get(tags, "title"), "Be My Escape")
        self.assertEqual(_asf_get(tags, "artist"), "Relient K")
        self.assertEqual(_asf_get(tags, "album"), "Mmhmm")
        self.assertEqual(_asf_get(tags, "albumartist"), "Relient K")
        self.assertEqual(_asf_get(tags, "genre"), "Rock")
        self.assertEqual(_asf_get(tags, "composer"), "Matthew Thiessen")
        self.assertEqual(_asf_get(tags, "tracknumber"), "02")
        self.assertEqual(_asf_get(tags, "date"), "2004")

    def test_empty_composer_falls_back_to_artist(self) -> None:
        tags = {"Author": ["Silvara Shea"], "WM/Composer": [""]}
        self.assertEqual(_asf_get(tags, "composer"), "Silvara Shea")

    def test_track_alias_when_tracknumber_missing(self) -> None:
        tags = {"WM/Track": ["4"]}
        self.assertEqual(_asf_get(tags, "tracknumber"), "04")

    def test_missing_defaults(self) -> None:
        self.assertEqual(_asf_get({}, "artist"), "Unknown Artist")
        self.assertEqual(_asf_get(None, "title"), "Unknown Title")
        self.assertEqual(_asf_get({}, "tracknumber"), "01")


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

    def test_wma_extension_uses_asf_reader(self) -> None:
        expected = MagicMock()
        with (
            patch("mtpmanager.infra.mutagen_tags.os.path.isfile", return_value=True),
            patch(
                "mtpmanager.infra.mutagen_tags._from_asf",
                return_value=expected,
            ) as from_asf,
            patch("mtpmanager.infra.mutagen_tags._from_id3") as from_id3,
        ):
            result = read_metadata("/music/album/track.wma")
        self.assertIs(result, expected)
        from_asf.assert_called_once_with("/music/album/track.wma")
        from_id3.assert_not_called()


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


@unittest.skipUnless(
    os.path.isfile(
        "/Volumes/music/0 Music/Reliant K/Mmhmm/"
        "02 - Be My Escape - Mmhmm - Relient K.wma"
    ),
    "sample WMA library path not mounted",
)
class RealWmaIntegrationTests(unittest.TestCase):
    PATH = (
        "/Volumes/music/0 Music/Reliant K/Mmhmm/"
        "02 - Be My Escape - Mmhmm - Relient K.wma"
    )

    def test_reads_relient_k_tags(self) -> None:
        md = read_metadata(self.PATH)
        self.assertEqual(md.artist, "Relient K")
        self.assertEqual(md.albumartist, "Relient K")
        self.assertEqual(md.album, "Mmhmm")
        self.assertEqual(md.title, "Be My Escape")
        self.assertEqual(md.genre, "Rock")
        self.assertEqual(md.composer, "Matthew Thiessen")
        self.assertEqual(md.tracknumber, "02")
        self.assertEqual(md.date, "2004")
        self.assertGreater(md.length_sec, 200.0)
        self.assertGreater(md.bitrate, 0)
        self.assertEqual(md.sample_rate, 44100)
        self.assertEqual(md.channels, 2)


if __name__ == "__main__":
    unittest.main()
