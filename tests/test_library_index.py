"""Unit tests for durable library index (no device / mutagen required)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.library_index import (
    load_library_index,
    save_library_index,
)


def _track(path: str, **meta_kw) -> Track:
    defaults = dict(
        artist="Artist",
        album="Album",
        title="Title",
        tracknumber="01",
        length_sec=120.5,
        sample_rate=44100,
        channels=2,
    )
    defaults.update(meta_kw)
    return Track(path=path, meta=TrackMetadata(**defaults))


class LibraryIndexTests(unittest.TestCase):
    def test_round_trip_preserves_paths_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.flac"
            f2 = root / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            lib = Library(
                tracks=[
                    _track(str(f1), title="One", artist="A"),
                    _track(str(f2), title="Two", artist="B", bitrate=320000),
                ],
                root_path=str(root),
            )
            dest = Path(tmp) / "library_index.json"
            save_library_index(lib, path=dest)
            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(len(loaded.tracks), 2)
            self.assertEqual(loaded.tracks[0].path, str(f1))
            self.assertEqual(loaded.tracks[0].meta.title, "One")
            self.assertEqual(loaded.tracks[0].meta.artist, "A")
            self.assertEqual(loaded.tracks[1].meta.title, "Two")
            self.assertEqual(loaded.tracks[1].meta.bitrate, 320000)
            self.assertEqual(loaded.tracks[1].meta.length_sec, 120.5)

    def test_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertIsNone(load_library_index(path=missing))

    def test_corrupt_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_library_index(path=bad))

    def test_load_drops_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            exists = root / "keep.mp3"
            exists.write_bytes(b"x")
            gone = root / "gone.mp3"
            lib = Library(
                tracks=[_track(str(exists)), _track(str(gone))],
                root_path=str(root),
            )
            dest = Path(tmp) / "library_index.json"
            save_library_index(lib, path=dest)
            # Ensure gone never existed on disk after save
            self.assertFalse(os.path.isfile(str(gone)))
            loaded = load_library_index(path=dest, drop_missing_files=True)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(len(loaded.tracks), 1)
            self.assertEqual(loaded.tracks[0].path, str(exists))

    def test_empty_tracks_round_trips_with_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "EmptyMusic"
            root.mkdir()
            lib = Library(tracks=[], root_path=str(root))
            dest = Path(tmp) / "library_index.json"
            save_library_index(lib, path=dest)
            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(loaded.tracks, [])
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["tracks"], [])

    def test_invalid_root_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "library_index.json"
            dest.write_text(
                json.dumps({"version": 1, "tracks": []}),
                encoding="utf-8",
            )
            self.assertIsNone(load_library_index(path=dest))


if __name__ == "__main__":
    unittest.main()
