"""Unit tests for durable SQLite library index (no device / mutagen required)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.library_index import (
    get_tracks_by_guids,
    load_legacy_json_library,
    load_library_index,
    migrate_json_if_needed,
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
    guid = defaults.pop("guid", "")
    return Track(path=path, meta=TrackMetadata(**defaults), guid=guid)


class LibraryIndexTests(unittest.TestCase):
    def test_round_trip_preserves_paths_metadata_and_guids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.flac"
            f2 = root / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            g1 = new_track_guid()
            lib = Library(
                tracks=[
                    _track(str(f1), title="One", artist="A", guid=g1),
                    _track(str(f2), title="Two", artist="B", bitrate=320000),
                ],
                root_path=str(root),
            )
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
            self.assertTrue(is_track_guid(lib.tracks[0].guid))
            self.assertEqual(lib.tracks[0].guid, g1)
            self.assertTrue(is_track_guid(lib.tracks[1].guid))

            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(len(loaded.tracks), 2)
            self.assertEqual(loaded.tracks[0].path, str(f1))
            self.assertEqual(loaded.tracks[0].guid, g1)
            self.assertEqual(loaded.tracks[0].meta.title, "One")
            self.assertEqual(loaded.tracks[0].meta.artist, "A")
            self.assertEqual(loaded.tracks[1].meta.title, "Two")
            self.assertEqual(loaded.tracks[1].meta.bitrate, 320000)
            self.assertEqual(loaded.tracks[1].meta.length_sec, 120.5)

    def test_resave_preserves_guid_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.flac"
            f1.write_bytes(b"x")
            dest = Path(tmp) / "library_index.db"
            lib1 = Library(
                tracks=[_track(str(f1), title="One")],
                root_path=str(root),
            )
            save_library_index(lib1, path=dest)
            guid = lib1.tracks[0].guid
            # Rescan-like save with empty guid still reuses path mapping.
            lib2 = Library(
                tracks=[_track(str(f1), title="One Updated")],
                root_path=str(root),
            )
            save_library_index(lib2, path=dest)
            self.assertEqual(lib2.tracks[0].guid, guid)
            loaded = load_library_index(path=dest)
            assert loaded is not None
            self.assertEqual(loaded.tracks[0].guid, guid)
            self.assertEqual(loaded.tracks[0].meta.title, "One Updated")

    def test_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.db"
            self.assertIsNone(load_library_index(path=missing, migrate_json=False))

    def test_corrupt_db_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.db"
            bad.write_text("not a sqlite database", encoding="utf-8")
            self.assertIsNone(load_library_index(path=bad, migrate_json=False))

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
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
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
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(loaded.tracks, [])

    def test_get_tracks_by_guids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f1.write_bytes(b"x")
            dest = Path(tmp) / "library_index.db"
            lib = Library(
                tracks=[_track(str(f1), title="Hit")],
                root_path=str(root),
            )
            save_library_index(lib, path=dest)
            g = lib.tracks[0].guid
            found = get_tracks_by_guids([g, "0" * 32], path=dest)
            self.assertIn(g, found)
            self.assertEqual(found[g].meta.title, "Hit")
            self.assertNotIn("0" * 32, found)

    def test_json_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f1.write_bytes(b"x")
            json_path = base / "library_index.json"
            payload = {
                "version": 1,
                "root_path": str(root),
                "scanned_at": "2026-01-01T00:00:00Z",
                "tracks": [
                    {
                        "path": str(f1),
                        "meta": {
                            "title": "Legacy",
                            "artist": "Old",
                            "album": "A",
                            "tracknumber": "01",
                            "length_sec": 10.0,
                            "sample_rate": 0,
                            "channels": 0,
                            "bitrate": 0,
                            "bitrate_mode": 0,
                            "albumartist": "",
                            "composer": "",
                            "genre": "",
                            "date": "",
                        },
                    }
                ],
            }
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            db = base / "library_index.db"
            self.assertTrue(migrate_json_if_needed(data_dir=base, db_path=db))
            loaded = load_library_index(path=db, migrate_json=False)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(len(loaded.tracks), 1)
            self.assertEqual(loaded.tracks[0].meta.title, "Legacy")
            self.assertTrue(is_track_guid(loaded.tracks[0].guid))

            # Second migrate is a no-op.
            self.assertFalse(migrate_json_if_needed(data_dir=base, db_path=db))

    def test_load_auto_migrates_json_when_db_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f1.write_bytes(b"x")
            json_path = base / "library_index.json"
            json_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root_path": str(root),
                        "scanned_at": "x",
                        "tracks": [
                            {
                                "path": str(f1),
                                "meta": {
                                    "title": "Auto",
                                    "artist": "A",
                                    "album": "B",
                                    "tracknumber": "01",
                                    "length_sec": 1.0,
                                    "sample_rate": 0,
                                    "channels": 0,
                                    "bitrate": 0,
                                    "bitrate_mode": 0,
                                    "albumartist": "",
                                    "composer": "",
                                    "genre": "",
                                    "date": "",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            db = base / "library_index.db"
            loaded = load_library_index(path=db, migrate_json=True)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.tracks[0].meta.title, "Auto")

    def test_load_legacy_json_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "library_index.json"
            p.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root_path": "/music",
                        "tracks": [{"path": "/music/a.mp3", "meta": {"title": "T"}}],
                    }
                ),
                encoding="utf-8",
            )
            lib = load_legacy_json_library(p)
            self.assertIsNotNone(lib)
            assert lib is not None
            self.assertEqual(lib.root_path, "/music")
            self.assertEqual(lib.tracks[0].meta.title, "T")


if __name__ == "__main__":
    unittest.main()
