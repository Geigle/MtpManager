"""Unit tests for extended M3U helpers."""

from __future__ import annotations

import os
import unittest

from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_m3u import (
    PlaylistEntry,
    append_entries,
    empty_m3u,
    entry_from_track,
    move_paths,
    parse_m3u,
    remove_paths,
    reorder_by_paths,
    serialize_m3u,
)


class PlaylistM3uTests(unittest.TestCase):
    def test_empty_round_trip(self) -> None:
        self.assertEqual(parse_m3u(empty_m3u()), [])
        self.assertTrue(serialize_m3u([]).startswith("#EXTM3U"))

    def test_parse_extended(self) -> None:
        text = (
            "#EXTM3U\n"
            "#EXTINF:215,Artist - Title\n"
            "/Music/a.flac\n"
            "#EXTINF:100,Only Title\n"
            "/Music/b.mp3\n"
        )
        entries = parse_m3u(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].path, os.path.normpath("/Music/a.flac"))
        self.assertEqual(entries[0].artist, "Artist")
        self.assertEqual(entries[0].title, "Title")
        self.assertEqual(entries[0].duration_sec, 215)
        self.assertEqual(entries[1].title, "Only Title")

    def test_serialize_from_tracks(self) -> None:
        t = Track(
            path="/lib/song.flac",
            meta=TrackMetadata(
                artist="A",
                albumartist="A",
                title="T",
                length_sec=90.7,
            ),
        )
        text = serialize_m3u([t])
        entries = parse_m3u(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "T")
        self.assertEqual(entries[0].duration_sec, 90)

    def test_append_skip_existing(self) -> None:
        base = serialize_m3u(
            [PlaylistEntry(path="/a.mp3", title="A", duration_sec=1)]
        )
        more = [
            PlaylistEntry(path="/a.mp3", title="A2", duration_sec=1),
            PlaylistEntry(path="/b.mp3", title="B", duration_sec=2),
        ]
        out = append_entries(base, more, skip_existing=True)
        paths = [e.path for e in parse_m3u(out)]
        self.assertEqual(
            paths,
            [os.path.normpath("/a.mp3"), os.path.normpath("/b.mp3")],
        )

    def test_remove_paths(self) -> None:
        text = serialize_m3u(
            [
                PlaylistEntry(path="/a.mp3", title="A"),
                PlaylistEntry(path="/b.mp3", title="B"),
            ]
        )
        out = remove_paths(text, ["/a.mp3"])
        paths = [e.path for e in parse_m3u(out)]
        self.assertEqual(paths, [os.path.normpath("/b.mp3")])

    def test_entry_from_track(self) -> None:
        t = Track(
            path="/x/y.flac",
            meta=TrackMetadata(artist="Art", title="Song", length_sec=10),
        )
        e = entry_from_track(t)
        self.assertEqual(e.path, os.path.normpath("/x/y.flac"))
        self.assertEqual(e.title, "Song")

    def test_move_paths_single_and_block(self) -> None:
        text = serialize_m3u(
            [
                PlaylistEntry(path="/a.mp3", title="A"),
                PlaylistEntry(path="/b.mp3", title="B"),
                PlaylistEntry(path="/c.mp3", title="C"),
                PlaylistEntry(path="/d.mp3", title="D"),
            ]
        )
        up = move_paths(text, ["/b.mp3"], delta=-1)
        self.assertEqual(
            [e.path for e in parse_m3u(up)],
            [
                os.path.normpath("/b.mp3"),
                os.path.normpath("/a.mp3"),
                os.path.normpath("/c.mp3"),
                os.path.normpath("/d.mp3"),
            ],
        )
        # Multi-select block at top cannot move up.
        top = move_paths(text, ["/a.mp3", "/b.mp3"], delta=-1)
        self.assertEqual(
            [e.path for e in parse_m3u(top)],
            [e.path for e in parse_m3u(text)],
        )
        # Contiguous block down one step.
        down = move_paths(text, ["/a.mp3", "/b.mp3"], delta=1)
        self.assertEqual(
            [e.path for e in parse_m3u(down)],
            [
                os.path.normpath("/c.mp3"),
                os.path.normpath("/a.mp3"),
                os.path.normpath("/b.mp3"),
                os.path.normpath("/d.mp3"),
            ],
        )

    def test_reorder_by_paths(self) -> None:
        text = serialize_m3u(
            [
                PlaylistEntry(path="/a.mp3", title="A"),
                PlaylistEntry(path="/b.mp3", title="B"),
                PlaylistEntry(path="/c.mp3", title="C"),
            ]
        )
        out = reorder_by_paths(text, ["/c.mp3", "/a.mp3"])
        paths = [e.path for e in parse_m3u(out)]
        self.assertEqual(
            paths,
            [
                os.path.normpath("/c.mp3"),
                os.path.normpath("/a.mp3"),
                os.path.normpath("/b.mp3"),  # leftover
            ],
        )


if __name__ == "__main__":
    unittest.main()
