"""Unit tests for durable SQLite library index (no device / mutagen required)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.app.scan_library import scan_library_roots
from mtpmanager.domain.library import Library, normalize_library_roots
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra.library_index import (
    exclude_library_paths,
    get_tracks_by_guids,
    list_library_exclusions,
    load_exclusion_paths,
    load_legacy_json_library,
    load_library_index,
    migrate_json_if_needed,
    remove_library_exclusions,
    save_library_index,
    untrack_library_roots,
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
                root_paths=[str(root)],
            )
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
            self.assertTrue(is_track_guid(lib.tracks[0].guid))
            self.assertEqual(lib.tracks[0].guid, g1)
            self.assertTrue(is_track_guid(lib.tracks[1].guid))

            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_paths, [str(root)])
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(len(loaded.tracks), 2)
            self.assertEqual(loaded.tracks[0].path, str(f1))
            self.assertEqual(loaded.tracks[0].guid, g1)
            self.assertEqual(loaded.tracks[0].meta.title, "One")
            self.assertEqual(loaded.tracks[0].meta.artist, "A")
            self.assertEqual(loaded.tracks[1].meta.title, "Two")
            self.assertEqual(loaded.tracks[1].meta.bitrate, 320000)
            self.assertEqual(loaded.tracks[1].meta.length_sec, 120.5)

    def test_multi_root_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = Path(tmp) / "Flac"
            r2 = Path(tmp) / "Mp3"
            r1.mkdir()
            r2.mkdir()
            f1 = r1 / "a.flac"
            f2 = r2 / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            lib = Library(
                tracks=[
                    _track(str(f1), title="One"),
                    _track(str(f2), title="Two"),
                ],
                root_paths=[str(r1), str(r2)],
            )
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_paths, [str(r1), str(r2)])
            self.assertEqual(loaded.root_path, str(r1))
            self.assertEqual(len(loaded.tracks), 2)
            titles = {t.meta.title for t in loaded.tracks}
            self.assertEqual(titles, {"One", "Two"})

    def test_legacy_single_root_db_migrates_to_root_paths(self) -> None:
        """DBs written before schema v3 still load with a one-element root list."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f1.write_bytes(b"x")
            dest = Path(tmp) / "library_index.db"
            conn = sqlite3.connect(str(dest))
            try:
                conn.executescript(
                    """
                    CREATE TABLE library_meta (
                      id INTEGER PRIMARY KEY CHECK (id = 1),
                      root_path TEXT NOT NULL,
                      scanned_at TEXT NOT NULL
                    );
                    CREATE TABLE tracks (
                      guid TEXT PRIMARY KEY,
                      path TEXT NOT NULL UNIQUE,
                      artist TEXT NOT NULL DEFAULT '',
                      albumartist TEXT NOT NULL DEFAULT '',
                      composer TEXT NOT NULL DEFAULT '',
                      album TEXT NOT NULL DEFAULT '',
                      title TEXT NOT NULL DEFAULT '',
                      genre TEXT NOT NULL DEFAULT '',
                      tracknumber TEXT NOT NULL DEFAULT '01',
                      date TEXT NOT NULL DEFAULT '',
                      length_sec REAL NOT NULL DEFAULT 0,
                      sample_rate INTEGER NOT NULL DEFAULT 0,
                      channels INTEGER NOT NULL DEFAULT 0,
                      bitrate INTEGER NOT NULL DEFAULT 0,
                      bitrate_mode INTEGER NOT NULL DEFAULT 0,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    """
                )
                guid = new_track_guid()
                conn.execute(
                    "INSERT INTO library_meta (id, root_path, scanned_at) "
                    "VALUES (1, ?, '2026-01-01T00:00:00Z')",
                    (str(root),),
                )
                conn.execute(
                    """
                    INSERT INTO tracks (
                      guid, path, artist, album, title, tracknumber,
                      length_sec, sample_rate, channels, bitrate, bitrate_mode,
                      created_at, updated_at
                    ) VALUES (?, ?, 'A', 'B', 'Legacy', '01', 1, 0, 0, 0, 0, 't', 't')
                    """,
                    (guid, str(f1)),
                )
                conn.commit()
            finally:
                conn.close()

            loaded = load_library_index(path=dest, migrate_json=False)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_paths, [str(root)])
            self.assertEqual(loaded.tracks[0].meta.title, "Legacy")

    def test_resave_preserves_guid_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.flac"
            f1.write_bytes(b"x")
            dest = Path(tmp) / "library_index.db"
            lib1 = Library(
                tracks=[_track(str(f1), title="One")],
                root_paths=[str(root)],
            )
            save_library_index(lib1, path=dest)
            guid = lib1.tracks[0].guid
            # Rescan-like save with empty guid still reuses path mapping.
            lib2 = Library(
                tracks=[_track(str(f1), title="One Updated")],
                root_paths=[str(root)],
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
                root_paths=[str(root)],
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
            lib = Library(tracks=[], root_paths=[str(root)])
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)
            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_path, str(root))
            self.assertEqual(loaded.root_paths, [str(root)])
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
                root_paths=[str(root)],
            )
            save_library_index(lib, path=dest)
            g = lib.tracks[0].guid
            found = get_tracks_by_guids([g, "0" * 32], path=dest)
            self.assertIn(g, found)
            self.assertEqual(found[g].meta.title, "Hit")
            self.assertNotIn("0" * 32, found)

    def test_save_untracks_instead_of_deleting(self) -> None:
        """Tracks removed from the library stay in SQLite as untracked GUIDs."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f2 = root / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            dest = Path(tmp) / "library_index.db"
            lib = Library(
                tracks=[
                    _track(str(f1), title="Keep"),
                    _track(str(f2), title="Drop"),
                ],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=dest)
            g_drop = next(t.guid for t in lib.tracks if t.path == str(f2))
            g_keep = next(t.guid for t in lib.tracks if t.path == str(f1))

            lib2 = Library(
                tracks=[_track(str(f1), title="Keep", guid=g_keep)],
                root_paths=[str(root)],
            )
            save_library_index(lib2, path=dest)

            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(len(loaded.tracks), 1)
            self.assertEqual(loaded.tracks[0].guid, g_keep)

            # Untracked GUID still resolvable for device inventory joins.
            found = get_tracks_by_guids([g_drop], path=dest)
            self.assertIn(g_drop, found)
            self.assertEqual(found[g_drop].meta.title, "Drop")

            # Re-scan same path reuses the untracked GUID.
            lib3 = Library(
                tracks=[
                    _track(str(f1), title="Keep"),
                    _track(str(f2), title="Back"),
                ],
                root_paths=[str(root)],
            )
            save_library_index(lib3, path=dest)
            by_path = {t.path: t for t in lib3.tracks}
            self.assertEqual(by_path[str(f2)].guid, g_drop)

    def test_exclude_library_paths_and_scan_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Media"
            movie = root / "Movie"
            extras = movie / "Extras"
            movie.mkdir(parents=True)
            extras.mkdir()
            main = movie / "feature.mp4"
            bonus = extras / "trailer.mp4"
            song = root / "song.mp3"
            main.write_bytes(b"a")
            bonus.write_bytes(b"b")
            song.write_bytes(b"c")
            dest = Path(tmp) / "library_index.db"
            lib = Library(
                tracks=[
                    _track(str(main), title="Feature"),
                    _track(str(bonus), title="Trailer"),
                    _track(str(song), title="Song"),
                ],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=dest)
            g_bonus = next(t.guid for t in lib.tracks if t.path == str(bonus))

            remaining = exclude_library_paths(
                [(str(extras), "folder")], path=dest
            )
            paths = {t.path for t in remaining.tracks}
            self.assertIn(str(main), paths)
            self.assertIn(str(song), paths)
            self.assertNotIn(str(bonus), paths)
            self.assertEqual(
                list_library_exclusions(path=dest),
                [(str(extras), "folder")],
            )
            # GUID retained for device join.
            self.assertIn(g_bonus, get_tracks_by_guids([g_bonus], path=dest))

            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                return_value=TrackMetadata(title="t"),
            ):
                scanned = scan_library_roots(
                    [str(root)],
                    exclusions=load_exclusion_paths(path=dest),
                )
            scanned_paths = {t.path for t in scanned.tracks}
            self.assertIn(str(main), scanned_paths)
            self.assertIn(str(song), scanned_paths)
            self.assertNotIn(str(bonus), scanned_paths)

            remove_library_exclusions([str(extras)], path=dest)
            self.assertEqual(list_library_exclusions(path=dest), [])

    def test_untrack_library_roots_no_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = Path(tmp) / "A"
            r2 = Path(tmp) / "B"
            r1.mkdir()
            r2.mkdir()
            f1 = r1 / "a.mp3"
            f2 = r2 / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            dest = Path(tmp) / "library_index.db"
            lib = Library(
                tracks=[
                    _track(str(f1), title="A-track"),
                    _track(str(f2), title="B-track"),
                ],
                root_paths=[str(r1), str(r2)],
            )
            save_library_index(lib, path=dest)
            g_b = next(t.guid for t in lib.tracks if t.path == str(f2))

            remaining = untrack_library_roots(
                [str(r2)], final_roots=[str(r1)], path=dest
            )
            self.assertEqual(remaining.root_paths, [str(r1)])
            self.assertEqual(len(remaining.tracks), 1)
            self.assertEqual(remaining.tracks[0].path, str(f1))

            loaded = load_library_index(path=dest)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.root_paths, [str(r1)])
            self.assertEqual(len(loaded.tracks), 1)
            self.assertEqual(get_tracks_by_guids([g_b], path=dest)[g_b].meta.title, "B-track")

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
            self.assertEqual(loaded.root_paths, [str(root)])

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
            self.assertEqual(lib.root_paths, ["/music"])
            self.assertEqual(lib.tracks[0].meta.title, "T")


class LibraryIndexStreamTests(unittest.TestCase):
    def test_on_progress_streams_meta_and_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            files = []
            for i in range(10):
                p = root / f"t{i:02d}.mp3"
                p.write_bytes(b"x")
                files.append(p)
            lib = Library(
                tracks=[_track(str(p), title=f"T{i}") for i, p in enumerate(files)],
                root_paths=[str(root)],
            )
            dest = Path(tmp) / "library_index.db"
            save_library_index(lib, path=dest)

            events: list[tuple] = []

            def on_progress(kind, *args) -> None:
                events.append((kind, args))

            loaded = load_library_index(
                path=dest,
                migrate_json=False,
                on_progress=on_progress,
                progress_batch_first=1,
                progress_batch_second=1,
                progress_batch_cap=4,
                progress_yield_s=0.0,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(len(loaded.tracks), 10)
            kinds = [e[0] for e in events]
            self.assertEqual(kinds[0], "meta")
            self.assertEqual(events[0][1][0], [str(root)])
            self.assertEqual(events[0][1][1], 10)
            batch_events = [e for e in events if e[0] == "batch"]
            self.assertGreaterEqual(len(batch_events), 2)
            kept_sizes = [len(e[1][0]) for e in batch_events]
            # Fibonacci 1,1,2,3,4(cap remainder path) — first sizes climb.
            self.assertEqual(kept_sizes[0], 1)
            self.assertEqual(sum(kept_sizes), 10)


class NormalizeRootsAndScanTests(unittest.TestCase):
    def test_normalize_library_roots_dedupes_and_drops_empty(self) -> None:
        roots = normalize_library_roots(["", "/a/b", "/a/b/", "/a/c"])
        self.assertEqual(roots, [os.path.normpath("/a/b"), os.path.normpath("/a/c")])

    def test_scan_library_roots_merges_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = Path(tmp) / "one"
            r2 = Path(tmp) / "two"
            r1.mkdir()
            r2.mkdir()
            (r1 / "a.mp3").write_bytes(b"x")
            (r2 / "b.flac").write_bytes(b"y")
            # Nested under r1 also present as its own root would double-count
            # without path dedupe — nested file should appear once.
            nested = r1 / "sub"
            nested.mkdir()
            (nested / "c.mp3").write_bytes(b"z")

            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                return_value=TrackMetadata(title="t"),
            ):
                lib = scan_library_roots([str(r1), str(r2), str(nested)])

            self.assertEqual(
                lib.root_paths,
                normalize_library_roots([str(r1), str(r2), str(nested)]),
            )
            paths = {t.path for t in lib.tracks}
            self.assertEqual(len(paths), 3)
            self.assertTrue(any(p.endswith("a.mp3") for p in paths))
            self.assertTrue(any(p.endswith("b.flac") for p in paths))
            self.assertTrue(any(p.endswith("c.mp3") for p in paths))

    def test_scan_library_roots_keeps_unreachable_in_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live"
            live.mkdir()
            (live / "a.mp3").write_bytes(b"x")
            missing = str(Path(tmp) / "gone")
            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                return_value=TrackMetadata(title="t"),
            ):
                lib = scan_library_roots([str(live), missing])
            self.assertEqual(len(lib.tracks), 1)
            self.assertEqual(
                lib.root_paths,
                normalize_library_roots([str(live), missing]),
            )

    def test_scan_library_roots_reports_bottom_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            album_a = root / "Artist" / "AlbumA"
            album_b = root / "Artist" / "AlbumB"
            album_a.mkdir(parents=True)
            album_b.mkdir(parents=True)
            (album_a / "1.mp3").write_bytes(b"a")
            (album_b / "2.mp3").write_bytes(b"b")
            seen: list[str] = []

            def on_dir(path: str) -> None:
                seen.append(os.path.basename(path.rstrip(os.sep)))

            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                return_value=TrackMetadata(title="t"),
            ):
                lib = scan_library_roots([str(root)], on_dir_progress=on_dir)

            self.assertEqual(len(lib.tracks), 2)
            self.assertEqual(sorted(seen), ["AlbumA", "AlbumB"])

    def test_scan_library_roots_includes_video_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Media"
            music = root / "Music"
            videos = root / "Videos" / "Shows"
            music.mkdir(parents=True)
            videos.mkdir(parents=True)
            (music / "song.mp3").write_bytes(b"a")
            (videos / "ep1.avi").write_bytes(b"b")
            (videos / "notes.txt").write_bytes(b"c")
            with mock.patch(
                "mtpmanager.app.scan_library.read_metadata",
                return_value=TrackMetadata(title="t"),
            ):
                lib = scan_library_roots([str(root)])
            paths = {t.path for t in lib.tracks}
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(p.endswith("song.mp3") for p in paths))
            self.assertTrue(any(p.endswith("ep1.avi") for p in paths))
            self.assertFalse(any(p.endswith("notes.txt") for p in paths))


if __name__ == "__main__":
    unittest.main()
