"""Format quality hierarchy and higher-fidelity track preference."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.app.scan_library import scan_library_roots
from mtpmanager.domain.library import (
    format_quality_rank,
    prefer_higher_fidelity_tracks,
    track_content_identity,
)
from mtpmanager.domain.models import Track, TrackMetadata


def _t(
    path: str,
    *,
    artist: str = "Artist",
    album: str = "Album",
    title: str = "Song",
    tracknumber: str = "01",
    bitrate: int = 0,
    sample_rate: int = 0,
    length_sec: float = 0.0,
) -> Track:
    return Track(
        path=path,
        meta=TrackMetadata(
            artist=artist,
            albumartist=artist,
            album=album,
            title=title,
            tracknumber=tracknumber,
            bitrate=bitrate,
            sample_rate=sample_rate,
            length_sec=length_sec,
        ),
    )


class FormatQualityRankTests(unittest.TestCase):
    def test_hierarchy_prefers_lossless_over_lossy(self) -> None:
        self.assertLess(format_quality_rank("song.wav"), format_quality_rank("song.flac"))
        self.assertLess(format_quality_rank("song.flac"), format_quality_rank("song.aac"))
        self.assertLess(format_quality_rank("song.aac"), format_quality_rank("song.mp3"))
        self.assertLess(format_quality_rank("song.mp3"), format_quality_rank("song.ogg"))
        self.assertLess(format_quality_rank("song.ogg"), format_quality_rank("song.wma"))

    def test_lossless_family_ties(self) -> None:
        self.assertEqual(format_quality_rank("flac"), format_quality_rank("alac"))
        self.assertEqual(format_quality_rank(".wav"), format_quality_rank("pcm"))

    def test_unknown_ranks_last(self) -> None:
        self.assertGreater(format_quality_rank("song.xyz"), format_quality_rank("song.wma"))


class PreferHigherFidelityTests(unittest.TestCase):
    def test_prefers_flac_over_mp3_by_tags(self) -> None:
        flac = _t("/lib/FLAC/Artist/Album/01 Song.flac")
        mp3 = _t("/lib/MP3/Artist/Album/01 Song.mp3")
        kept = prefer_higher_fidelity_tracks([mp3, flac])
        self.assertEqual([t.path for t in kept], [flac.path])

    def test_prefers_wav_over_flac(self) -> None:
        wav = _t("/a/01.wav", title="T")
        flac = _t("/b/01.flac", title="T")
        kept = prefer_higher_fidelity_tracks([flac, wav])
        self.assertEqual(kept[0].path, wav.path)

    def test_prefers_mp3_over_ogg_and_wma(self) -> None:
        mp3 = _t("/a/x.mp3")
        ogg = _t("/b/x.ogg")
        wma = _t("/c/x.wma")
        kept = prefer_higher_fidelity_tracks([wma, ogg, mp3])
        self.assertEqual([t.path for t in kept], [mp3.path])

    def test_same_folder_basename_without_tags(self) -> None:
        flac = Track(
            path="/album/track.flac",
            meta=TrackMetadata(title="Unknown Title", artist="Unknown Artist"),
        )
        mp3 = Track(
            path="/album/track.mp3",
            meta=TrackMetadata(title="Unknown Title", artist="Unknown Artist"),
        )
        kept = prefer_higher_fidelity_tracks([mp3, flac])
        self.assertEqual([t.path for t in kept], [flac.path])

    def test_format_folder_path_identity_without_tags(self) -> None:
        flac = Track(
            path="/Music/FLAC/Artist/Album/01 Song.flac",
            meta=TrackMetadata(),
        )
        mp3 = Track(
            path="/Music/MP3/Artist/Album/01 Song.mp3",
            meta=TrackMetadata(),
        )
        self.assertEqual(track_content_identity(flac), track_content_identity(mp3))
        kept = prefer_higher_fidelity_tracks([mp3, flac])
        self.assertEqual([t.path for t in kept], [flac.path])

    def test_different_songs_kept(self) -> None:
        a = _t("/a.flac", title="One")
        b = _t("/b.mp3", title="Two")
        kept = prefer_higher_fidelity_tracks([a, b])
        self.assertEqual({t.path for t in kept}, {a.path, b.path})

    def test_videos_never_collapsed(self) -> None:
        v1 = Track(path="/v/show.avi", meta=TrackMetadata(title="Show"))
        v2 = Track(path="/v/show.mp4", meta=TrackMetadata(title="Show"))
        kept = prefer_higher_fidelity_tracks([v1, v2])
        self.assertEqual(len(kept), 2)

    def test_tie_break_higher_bitrate(self) -> None:
        low = _t("/a/low.flac", bitrate=500, sample_rate=44100)
        high = _t("/b/high.flac", bitrate=1000, sample_rate=44100)
        kept = prefer_higher_fidelity_tracks([low, high])
        self.assertEqual(kept[0].path, high.path)

    def test_scan_library_roots_drops_lossy_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flac_dir = root / "FLAC" / "Artist" / "Album"
            mp3_dir = root / "MP3" / "Artist" / "Album"
            flac_dir.mkdir(parents=True)
            mp3_dir.mkdir(parents=True)
            (flac_dir / "01 Song.flac").write_bytes(b"f")
            (mp3_dir / "01 Song.mp3").write_bytes(b"m")
            other = root / "Other"
            other.mkdir()
            (other / "unique.mp3").write_bytes(b"u")

            meta_by_name = {
                "01 Song.flac": TrackMetadata(
                    artist="Artist", album="Album", title="Song", tracknumber="1"
                ),
                "01 Song.mp3": TrackMetadata(
                    artist="Artist", album="Album", title="Song", tracknumber="1"
                ),
                "unique.mp3": TrackMetadata(
                    artist="Other", album="Solo", title="Unique", tracknumber="1"
                ),
            }

            def _meta(path: str) -> TrackMetadata:
                return meta_by_name[os.path.basename(path)]

            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                side_effect=_meta,
            ):
                lib = scan_library_roots([str(root)])

            paths = [t.path for t in lib.tracks]
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(p.endswith("01 Song.flac") for p in paths))
            self.assertTrue(any(p.endswith("unique.mp3") for p in paths))
            self.assertFalse(any(p.endswith("01 Song.mp3") for p in paths))


if __name__ == "__main__":
    unittest.main()
