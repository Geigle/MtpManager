"""Unit tests for library sort / grouping (no Tk)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.library_sort import (
    SortPrimary,
    group_by_album,
    group_by_artist_album,
    group_by_year,
    sort_tracks_flat,
)
from mtpmanager.domain.models import Track, TrackMetadata


def _t(
    path: str,
    *,
    title: str = "T",
    artist: str = "A",
    albumartist: str = "",
    album: str = "Al",
    tracknumber: str = "01",
    date: str = "",
) -> Track:
    return Track(
        path=path,
        meta=TrackMetadata(
            title=title,
            artist=artist,
            albumartist=albumartist or artist,
            album=album,
            tracknumber=tracknumber,
            date=date,
        ),
    )


class PrimaryArtistTests(unittest.TestCase):
    def test_prefers_albumartist(self) -> None:
        t = _t("/x", artist="Guest", albumartist="Main Band")
        self.assertEqual(primary_artist(t), "Main Band")

    def test_falls_back_to_artist(self) -> None:
        t = _t("/x", artist="Solo", albumartist="Unknown Artist")
        self.assertEqual(primary_artist(t), "Solo")


class LibrarySortTests(unittest.TestCase):
    def test_sort_title(self) -> None:
        tracks = [
            _t("/b", title="Zebra"),
            _t("/a", title="Apple"),
            _t("/c", title="apple"),  # casefold
        ]
        out = sort_tracks_flat(tracks, SortPrimary.TITLE)
        self.assertEqual([t.meta.title for t in out], ["Apple", "apple", "Zebra"])

    def test_group_artist_album_hierarchy(self) -> None:
        tracks = [
            _t("/1", artist="B", album="Z", title="t2", tracknumber="02"),
            _t("/2", artist="A", album="X", title="t1", tracknumber="01"),
            _t("/3", artist="A", album="Y", title="t1", tracknumber="01"),
            _t("/4", artist="A", album="X", title="t2", tracknumber="02"),
        ]
        groups = group_by_artist_album(tracks)
        self.assertEqual([g.label for g in groups], ["A", "B"])
        a = groups[0]
        self.assertEqual([c.label for c in a.children], ["X", "Y"])
        self.assertEqual(
            [t.meta.tracknumber for t in a.children[0].tracks],
            ["01", "02"],
        )

    def test_group_by_albumartist_keeps_cd_together(self) -> None:
        """Track ARTIST can differ (features); albumartist groups the CD."""
        tracks = [
            _t(
                "/1",
                artist="Main Band feat. Guest",
                albumartist="Main Band",
                album="The Album",
                tracknumber="01",
                title="Opener",
            ),
            _t(
                "/2",
                artist="Main Band",
                albumartist="Main Band",
                album="The Album",
                tracknumber="02",
                title="Closer",
            ),
            _t(
                "/3",
                artist="Other",
                albumartist="Other",
                album="Elsewhere",
                tracknumber="01",
                title="Solo",
            ),
        ]
        groups = group_by_artist_album(tracks)
        self.assertEqual([g.label for g in groups], ["Main Band", "Other"])
        main = groups[0]
        self.assertEqual([c.label for c in main.children], ["The Album"])
        self.assertEqual(
            [t.meta.title for t in main.children[0].tracks],
            ["Opener", "Closer"],
        )

    def test_group_year_newest_first(self) -> None:
        tracks = [
            _t("/1", date="2010", artist="A"),
            _t("/2", date="2020-01-01", artist="B"),
            _t("/3", date="", artist="C"),
        ]
        groups = group_by_year(tracks)
        labels = [g.label for g in groups]
        self.assertEqual(labels[0], "2020")
        self.assertEqual(labels[1], "2010")
        self.assertEqual(labels[-1], "Unknown year")

    def test_group_album(self) -> None:
        tracks = [
            _t("/1", album="B", artist="X"),
            _t("/2", album="A", artist="Y"),
        ]
        groups = group_by_album(tracks)
        self.assertTrue(groups[0].label.startswith("A"))
        self.assertEqual(len(groups[0].tracks), 1)

    def test_group_album_separates_same_title_different_albumartist(self) -> None:
        tracks = [
            _t("/1", album="Greatest Hits", artist="A", albumartist="A"),
            _t("/2", album="Greatest Hits", artist="B", albumartist="B"),
        ]
        groups = group_by_album(tracks)
        self.assertEqual(len(groups), 2)
        labels = sorted(g.label for g in groups)
        self.assertEqual(
            labels,
            ["Greatest Hits — A", "Greatest Hits — B"],
        )


if __name__ == "__main__":
    unittest.main()