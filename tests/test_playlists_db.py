"""SQLite playlist CRUD tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_m3u import parse_m3u
from mtpmanager.infra.playlists import (
    append_tracks_to_playlist,
    create_playlist,
    delete_playlist,
    get_playlist,
    list_playlists,
    remove_paths_from_playlist,
    rename_playlist,
    resolve_playlist_tracks,
)
from mtpmanager.infra.library_index import save_library_index
from mtpmanager.domain.library import Library


class PlaylistsDbTests(unittest.TestCase):
    def test_crud_and_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            pl = create_playlist("Favorites", path=db)
            self.assertEqual(pl.name, "Favorites")
            self.assertTrue(pl.m3u_text.startswith("#EXTM3U"))

            names = [p.name for p in list_playlists(path=db)]
            self.assertEqual(names, ["Favorites"])

            t1 = Track(
                path=os.path.join(tmp, "a.flac"),
                meta=TrackMetadata(artist="A", title="One", length_sec=10),
                guid="a" * 32,
            )
            t2 = Track(
                path=os.path.join(tmp, "b.mp3"),
                meta=TrackMetadata(artist="B", title="Two", length_sec=20),
                guid="b" * 32,
            )
            Path(t1.path).write_bytes(b"x")
            Path(t2.path).write_bytes(b"y")

            # Seed tracks table for resolve
            save_library_index(
                Library(tracks=[t1, t2], root_paths=[tmp]),
                path=db,
            )

            pl = append_tracks_to_playlist(pl.id, [t1, t2], path=db)
            entries = parse_m3u(pl.m3u_text)
            self.assertEqual(len(entries), 2)

            # skip_existing
            pl = append_tracks_to_playlist(pl.id, [t1], skip_existing=True, path=db)
            self.assertEqual(len(parse_m3u(pl.m3u_text)), 2)

            tracks = resolve_playlist_tracks(pl, path=db)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0].guid, t1.guid)

            pl = remove_paths_from_playlist(pl.id, [t1.path], path=db)
            self.assertEqual(len(parse_m3u(pl.m3u_text)), 1)

            pl = rename_playlist(pl.id, "Road Trip", path=db)
            self.assertEqual(pl.name, "Road Trip")

            self.assertTrue(delete_playlist(pl.id, path=db))
            self.assertIsNone(get_playlist(pl.id, path=db))
            self.assertEqual(list_playlists(path=db), [])

    def test_duplicate_name_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            create_playlist("Same", path=db)
            with self.assertRaises(ValueError):
                create_playlist("same", path=db)


if __name__ == "__main__":
    unittest.main()
