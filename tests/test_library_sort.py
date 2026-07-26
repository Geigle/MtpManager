"""Unit tests for library sort / grouping (no Tk)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.library_sort import (
    ARTIST_COLUMN_CYCLE,
    SortPrimary,
    directory_label,
    group_by_album,
    group_by_artist_album,
    group_by_artist_dash_album,
    group_by_directory,
    group_by_year,
    is_various_artists_name,
    next_artist_column_sort,
    sort_tracks_flat,
    strip_collaboration_credits,
    track_core_artist_key,
    tracks_should_group_as_various_artists,
)
from mtpmanager.domain.models import Track, TrackMetadata


def _t(
    path: str,
    *,
    title: str = "T",
    artist: str = "A",
    albumartist: str | None = None,
    album: str = "Al",
    tracknumber: str = "01",
    date: str = "",
    genre: str = "",
) -> Track:
    # albumartist=None → leave empty (no auto-copy from artist).
    if albumartist is None:
        aa = artist
    else:
        aa = albumartist
    return Track(
        path=path,
        meta=TrackMetadata(
            title=title,
            artist=artist,
            albumartist=aa,
            album=album,
            tracknumber=tracknumber,
            date=date,
            genre=genre,
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

    def test_group_album_label_is_album_dash_artist(self) -> None:
        tracks = [
            _t("/1", album="Greatest Hits", artist="A", albumartist="A"),
            _t("/2", album="Greatest Hits", artist="B", albumartist="B"),
        ]
        groups = group_by_album(tracks)
        self.assertEqual(len(groups), 2)
        labels = sorted(g.label for g in groups)
        self.assertEqual(
            labels,
            ["Greatest Hits - A", "Greatest Hits - B"],
        )

    def test_group_by_directory_keeps_folder_mates_together(self) -> None:
        tracks = [
            _t(
                "/Music/VA Comp/01.mp3",
                artist="One",
                albumartist="Various Artists",
                album="VA Comp",
                tracknumber="01",
            ),
            _t(
                "/Music/VA Comp/02.mp3",
                artist="Two",
                albumartist="Various Artists",
                album="VA Comp",
                tracknumber="02",
            ),
            _t(
                "/Music/Solo Band/Album/01.mp3",
                artist="Solo Band",
                album="Album",
                tracknumber="01",
            ),
        ]
        groups = group_by_directory(tracks)
        labels = [g.label for g in groups]
        self.assertEqual(labels, ["Album", "VA Comp"])
        va = next(g for g in groups if g.label == "VA Comp")
        self.assertEqual(len(va.tracks), 2)
        self.assertEqual(
            [t.meta.tracknumber for t in va.tracks],
            ["01", "02"],
        )

    def test_directory_label_basename(self) -> None:
        self.assertEqual(directory_label("/Music/VA Comp"), "VA Comp")

    def test_group_by_artist_dash_album_label_and_order(self) -> None:
        tracks = [
            _t("/a/1", artist="B", album="Z", tracknumber="01"),
            _t("/b/1", artist="A", album="Y", tracknumber="01"),
        ]
        groups = group_by_artist_dash_album(tracks)
        self.assertEqual(
            [g.label for g in groups],
            ["A - Y", "B - Z"],
        )

    def test_group_by_artist_dash_album_multi_artist_dir_is_various(self) -> None:
        tracks = [
            _t(
                "/Music/Comp/01.mp3",
                artist="One",
                albumartist="One",
                album="The Comp",
                tracknumber="01",
            ),
            _t(
                "/Music/Comp/02.mp3",
                artist="Two",
                albumartist="Two",
                album="The Comp",
                tracknumber="02",
            ),
            _t(
                "/Music/Solo/Album/01.mp3",
                artist="Solo",
                album="Solo Album",
                tracknumber="01",
            ),
        ]
        groups = group_by_artist_dash_album(tracks)
        labels = [g.label for g in groups]
        self.assertIn("Various Artists - The Comp", labels)
        self.assertIn("Solo - Solo Album", labels)
        va = next(g for g in groups if g.label.startswith("Various Artists"))
        self.assertEqual(len(va.tracks), 2)

    def test_feat_guests_do_not_force_various_artists(self) -> None:
        tracks = [
            _t(
                "/Music/Main Band/Album/01.mp3",
                title="Opener (feat. Guest Star)",
                artist="Main Band feat. Guest Star",
                albumartist="",
                album="The Album",
                tracknumber="01",
            ),
            _t(
                "/Music/Main Band/Album/02.mp3",
                title="Closer",
                artist="Main Band",
                albumartist="",
                album="The Album",
                tracknumber="02",
            ),
            _t(
                "/Music/Main Band/Album/03.mp3",
                title="Bonus",
                artist="Main Band ft. Other",
                albumartist="",
                album="The Album",
                tracknumber="03",
            ),
        ]
        self.assertFalse(tracks_should_group_as_various_artists(tracks))
        groups = group_by_artist_dash_album(tracks)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].label, "Main Band - The Album")
        self.assertEqual(len(groups[0].tracks), 3)

    def test_shared_albumartist_keeps_features_together(self) -> None:
        tracks = [
            _t(
                "/x/01.mp3",
                artist="Main Band feat. A",
                albumartist="Main Band",
                album="LP",
            ),
            _t(
                "/x/02.mp3",
                artist="Main Band feat. B",
                albumartist="Main Band",
                album="LP",
            ),
        ]
        self.assertFalse(tracks_should_group_as_various_artists(tracks))
        groups = group_by_artist_dash_album(tracks)
        self.assertEqual(groups[0].label, "Main Band - LP")

    def test_explicit_various_artists_albumartist(self) -> None:
        tracks = [
            _t(
                "/c/01.mp3",
                artist="One",
                albumartist="Various Artists",
                album="Now 40",
            ),
            _t(
                "/c/02.mp3",
                artist="Two",
                albumartist="Various Artists",
                album="Now 40",
            ),
        ]
        self.assertTrue(tracks_should_group_as_various_artists(tracks))

    def test_single_artist_soundtrack_genre_stays_with_artist(self) -> None:
        """Genre Soundtrack/OST alone must not force VA when one core artist."""
        same_artist_ost = [
            _t(
                "/Music/Film Score/01.mp3",
                artist="Hans Zimmer",
                albumartist="Hans Zimmer",
                album="Inception",
                genre="Soundtrack",
            ),
            _t(
                "/Music/Film Score/02.mp3",
                artist="Hans Zimmer",
                albumartist="Hans Zimmer",
                album="Inception",
                genre="OST",
            ),
        ]
        self.assertFalse(tracks_should_group_as_various_artists(same_artist_ost))
        groups = group_by_artist_dash_album(same_artist_ost)
        self.assertEqual(groups[0].label, "Hans Zimmer - Inception")

    def test_multi_artist_soundtrack_genre_is_various(self) -> None:
        multi_ost = [
            _t(
                "/Music/OST/01.mp3",
                artist="One",
                albumartist="One",
                album="Movie Songs",
                genre="Soundtrack",
            ),
            _t(
                "/Music/OST/02.mp3",
                artist="Two",
                albumartist="Two",
                album="Movie Songs",
                genre="Soundtrack",
            ),
        ]
        self.assertTrue(tracks_should_group_as_various_artists(multi_ost))
        groups = group_by_artist_dash_album(multi_ost)
        self.assertEqual(groups[0].label, "Various Artists - Movie Songs")

    def test_compilation_path_signal(self) -> None:
        by_path = [
            _t(
                "/Music/Compilations/Hits/01.mp3",
                artist="A",
                albumartist="A",
            ),
            _t(
                "/Music/Compilations/Hits/02.mp3",
                artist="A",
                albumartist="A",
            ),
        ]
        self.assertTrue(tracks_should_group_as_various_artists(by_path))

    def test_strip_collaboration_credits(self) -> None:
        self.assertEqual(
            strip_collaboration_credits("Main Band feat. Guest"),
            "Main Band",
        )
        self.assertEqual(
            strip_collaboration_credits("Main (feat. Guest)"),
            "Main",
        )
        self.assertEqual(
            strip_collaboration_credits("Artist x Other"),
            "Artist",
        )
        self.assertEqual(
            strip_collaboration_credits("Foo vs. Bar"),
            "Foo",
        )
        self.assertTrue(is_various_artists_name("Various Artists"))
        self.assertTrue(is_various_artists_name("V.A."))
        self.assertFalse(is_various_artists_name("Main Band"))

    def test_track_core_artist_key_strips_feat(self) -> None:
        t1 = _t("/1", artist="Main feat. G", albumartist="")
        t2 = _t("/2", artist="Main", albumartist="")
        self.assertEqual(track_core_artist_key(t1), track_core_artist_key(t2))

    def test_artist_column_cycle_order(self) -> None:
        self.assertEqual(len(ARTIST_COLUMN_CYCLE), 4)
        p, r = SortPrimary.DIRECTORY, False
        steps = []
        for _ in range(5):
            p, r = next_artist_column_sort(p, r)
            steps.append((p, r))
        self.assertEqual(
            steps,
            [
                (SortPrimary.ARTIST, False),
                (SortPrimary.ARTIST, True),
                (SortPrimary.ARTIST_ALBUM_COMBO, False),
                (SortPrimary.ARTIST_ALBUM_COMBO, True),
                (SortPrimary.ARTIST, False),
            ],
        )


if __name__ == "__main__":
    unittest.main()