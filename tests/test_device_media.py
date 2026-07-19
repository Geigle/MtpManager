"""Unit tests for device media heuristics (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.device_media import (
    apply_track_info,
    looks_like_track,
    merge_track_refs,
    track_refs_from_files,
)
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef, FileEntry


def _file(
    oid: int,
    name: str,
    *,
    filetype: int = 0,
    parent_id: int = 100,
    storage_id: int = 0x00010001,
) -> FileEntry:
    return FileEntry(
        item_id=oid,
        name=name,
        parent_id=parent_id,
        storage_id=storage_id,
        filetype=filetype,
        filesize=1000,
    )


class LooksLikeTrackTests(unittest.TestCase):
    def test_mp3_filetype(self) -> None:
        self.assertTrue(looks_like_track(_file(1, "x.bin", filetype=2)))

    def test_extension_fallback(self) -> None:
        self.assertTrue(looks_like_track(_file(1, "Song.MP3", filetype=0)))
        self.assertTrue(looks_like_track(_file(1, "clip.wma", filetype=99)))

    def test_rejects_non_media(self) -> None:
        self.assertFalse(looks_like_track(_file(1, "cover.jpg", filetype=14)))
        self.assertFalse(looks_like_track(_file(1, "readme.txt", filetype=0)))
        self.assertFalse(looks_like_track(_file(1, "Music", filetype=0)))


class TrackRefsFromFilesTests(unittest.TestCase):
    def test_filters_and_maps(self) -> None:
        files = [
            _file(10, "a.mp3", filetype=2),
            _file(11, "cover.jpg", filetype=14),
            _file(12, "b.wma", filetype=3),
            _file(13, "notes.txt", filetype=0),
            _file(14, "c.FLAC", filetype=0),  # ext fallback
        ]
        refs = track_refs_from_files(files)
        self.assertEqual([r.item_id for r in refs], [10, 12, 14])
        self.assertEqual(refs[0].name, "a.mp3")
        self.assertEqual(refs[0].title, "")
        self.assertEqual(refs[0].artist, "")
        self.assertEqual(refs[0].parent_id, 100)
        self.assertEqual(refs[0].storage_id, 0x00010001)
        self.assertEqual(refs[0].filetype, 2)
        self.assertEqual(refs[2].name, "c.FLAC")
        self.assertEqual(refs[2].filetype, 0)

    def test_sort_by_name_when_tags_empty(self) -> None:
        files = [
            _file(3, "zeta.mp3", filetype=2),
            _file(1, "alpha.mp3", filetype=2),
            _file(2, "beta.mp3", filetype=2),
        ]
        refs = track_refs_from_files(files)
        self.assertEqual([r.name for r in refs], ["alpha.mp3", "beta.mp3", "zeta.mp3"])

    def test_empty(self) -> None:
        self.assertEqual(track_refs_from_files([]), [])


class MergeTrackRefsTests(unittest.TestCase):
    def test_prefers_tagged_and_adds_missing(self) -> None:
        tagged = [
            DeviceTrackRef(
                item_id=10,
                name="a.mp3",
                title="Alpha",
                artist="Artist",
                filetype=2,
            )
        ]
        from_files = track_refs_from_files(
            [
                _file(10, "a.mp3", filetype=2),
                _file(20, "b.mp3", filetype=2),
            ]
        )
        merged = merge_track_refs(tagged, from_files)
        by_id = {r.item_id: r for r in merged}
        self.assertEqual(set(by_id), {10, 20})
        # Sort is artist/title/name - empty artist (file-only) sorts first.
        self.assertEqual([r.item_id for r in merged], [20, 10])
        self.assertEqual(by_id[10].title, "Alpha")
        self.assertEqual(by_id[10].artist, "Artist")
        self.assertEqual(by_id[20].title, "")
        self.assertEqual(by_id[20].name, "b.mp3")


class ApplyTrackInfoTests(unittest.TestCase):
    def test_overlays_tags_keeps_id(self) -> None:
        ref = DeviceTrackRef(
            item_id=42,
            name="short.mp3",
            title="",
            artist="",
            parent_id=100,
            storage_id=0x00010001,
            filetype=2,
        )
        info = DeviceTrackInfo(
            item_id=42,
            name="short.mp3",
            title="Full Title",
            artist="The Artist",
            parent_id=100,
            storage_id=0x00010001,
            filetype=2,
        )
        out = apply_track_info(ref, info)
        self.assertEqual(out.item_id, 42)
        self.assertEqual(out.title, "Full Title")
        self.assertEqual(out.artist, "The Artist")
        self.assertEqual(out.name, "short.mp3")
        self.assertEqual(out.parent_id, 100)


class TrackLineFallbackTests(unittest.TestCase):
    def test_title_falls_back_to_name(self) -> None:
        from mtpmanager.ui.formatting import track_line

        line = track_line(
            DeviceTrackRef(item_id=1, name="song.mp3", title="", artist="", filetype=2)
        )
        # Empty title -> filename appears as the title column (not a bare em dash).
        self.assertIn("song.mp3", line)
        with_title = track_line(
            DeviceTrackRef(
                item_id=2,
                name="file.mp3",
                title="Real Title",
                artist="Band",
                filetype=2,
            )
        )
        self.assertIn("Real Title", with_title)
        self.assertIn("Band", with_title)


if __name__ == "__main__":
    unittest.main()
