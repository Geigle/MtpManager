"""Unit tests for library sort / grouping (no Tk)."""

from __future__ import annotations

import os
import unittest

from mtpmanager.domain.library import (
    Library,
    is_audiobook_genre,
    is_audiobook_track,
    is_video_file,
    is_video_track,
    merge_scanned_roots,
    partition_library_media,
    partition_music_and_audiobooks,
    path_is_excluded,
    path_looks_like_tv_series,
    path_under_root,
    primary_artist,
    tv_series_title_for_path,
    video_display_title,
)
from mtpmanager.domain.library_sort import (
    ARTIST_COLUMN_CYCLE,
    SortPrimary,
    directory_label,
    group_by_album,
    group_by_artist_album,
    group_by_artist_dash_album,
    group_by_artist_album_year,
    group_by_directory,
    group_by_year,
    group_videos_for_library,
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


class AudiobookGenreTests(unittest.TestCase):
    def test_exact_and_plural(self) -> None:
        self.assertTrue(is_audiobook_genre("Audiobook"))
        self.assertTrue(is_audiobook_genre("audiobooks"))
        self.assertFalse(is_audiobook_genre("Rock"))
        self.assertFalse(is_audiobook_genre("Unknown Genre"))
        self.assertFalse(is_audiobook_genre(""))

    def test_multi_value_token(self) -> None:
        self.assertTrue(is_audiobook_genre("Spoken Word / Audiobook"))
        self.assertTrue(is_audiobook_genre("Fiction; Audiobook"))

    def test_partition_music_and_audiobooks(self) -> None:
        music = _t("/m.mp3", genre="Rock")
        book = _t("/b.mp3", genre="Audiobook", artist="Author")
        m, a = partition_music_and_audiobooks([music, book])
        self.assertEqual(m, [music])
        self.assertEqual(a, [book])
        self.assertTrue(is_audiobook_track(book))
        self.assertFalse(is_audiobook_track(music))

    def test_partition_library_media_splits_video(self) -> None:
        music = _t("/m.mp3", genre="Rock")
        book = _t("/b.mp3", genre="Audiobook", artist="Author")
        video = _t("/Movies/clip.avi", title="Clip")
        m, v, a = partition_library_media([music, book, video])
        self.assertEqual(m, [music])
        self.assertEqual(v, [video])
        self.assertEqual(a, [book])
        self.assertTrue(is_video_file("/x.mp4"))
        self.assertTrue(is_video_track(video))
        self.assertFalse(is_video_track(music))
        # Videos are not misclassified as music even with audiobook genre tags.
        tagged_vid = _t("/show.mkv", genre="Audiobook")
        m2, v2, a2 = partition_library_media([tagged_vid])
        self.assertEqual(m2, [])
        self.assertEqual(v2, [tagged_vid])
        self.assertEqual(a2, [])

    def test_video_display_title_is_filename(self) -> None:
        t = _t("/Media/Shows/S01E01.avi", title="Ignored Tag Title")
        self.assertEqual(video_display_title(t), "S01E01.avi")

    def test_tv_series_title_from_season_folder(self) -> None:
        path = "/Media/TV/Babylon 5/Season 1/S01E01.avi"
        self.assertTrue(path_looks_like_tv_series(path))
        self.assertEqual(tv_series_title_for_path(path), "Babylon 5")

    def test_tv_series_title_from_parent_when_sxxexx_in_file(self) -> None:
        path = "/Media/TV/Firefly/S01E03.mkv"
        self.assertEqual(tv_series_title_for_path(path), "Firefly")

    def test_tv_series_title_from_filename_when_no_show_folder(self) -> None:
        path = "/Downloads/Firefly.S01E01.avi"
        self.assertEqual(tv_series_title_for_path(path), "Firefly")

    def test_movie_not_treated_as_tv(self) -> None:
        path = "/Media/Movies/Inception (2010).mp4"
        self.assertFalse(path_looks_like_tv_series(path))
        self.assertIsNone(tv_series_title_for_path(path))

    def test_group_videos_for_library_series_and_movies(self) -> None:
        tracks = [
            _t("/Media/TV/Show A/Season 1/S01E02.avi"),
            _t("/Media/TV/Show A/Season 1/S01E01.avi"),
            _t("/Media/TV/Show A/Season 2/S02E01.avi"),
            _t("/Media/Movies/Cool Film/Cool Film.mp4"),
        ]
        groups = group_videos_for_library(tracks)
        labels = [g.label for g in groups]
        self.assertIn("Show A", labels)
        show = next(g for g in groups if g.label == "Show A")
        self.assertEqual(len(show.tracks), 3)
        # Episodes ordered by season/episode, not path alone.
        self.assertEqual(
            [os.path.basename(t.path) for t in show.tracks],
            ["S01E01.avi", "S01E02.avi", "S02E01.avi"],
        )
        # Movie stays under its folder name.
        self.assertTrue(any(g.label == "Cool Film" for g in groups))

    def test_merge_scanned_roots_keeps_other_roots(self) -> None:
        existing = Library(
            tracks=[
                _t("/libA/a.mp3", title="A"),
                _t("/libB/old.mp3", title="Old"),
            ],
            root_paths=["/libA", "/libB"],
        )
        scanned = Library(
            tracks=[_t("/libB/new.mp3", title="New")],
            root_paths=["/libB"],
        )
        merged = merge_scanned_roots(
            existing,
            scanned,
            scanned_roots=["/libB"],
            final_roots=["/libA", "/libB", "/libC"],
        )
        paths = [t.path for t in merged.tracks]
        self.assertIn("/libA/a.mp3", paths)
        self.assertIn("/libB/new.mp3", paths)
        self.assertNotIn("/libB/old.mp3", paths)
        self.assertEqual(merged.root_paths, ["/libA", "/libB", "/libC"])
        self.assertTrue(path_under_root("/libB/x.mp3", "/libB"))
        self.assertFalse(path_under_root("/libA/x.mp3", "/libB"))
        self.assertTrue(
            path_is_excluded("/Movie/Extras/t.mp4", ["/Movie/Extras"])
        )
        self.assertFalse(
            path_is_excluded("/Movie/feature.mp4", ["/Movie/Extras"])
        )


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

    def test_group_by_artist_album_year_author_then_release(self) -> None:
        tracks = [
            _t("/1", artist="Zed", date="2015", album="B", title="t1"),
            _t("/2", artist="Ann", date="2020", album="Late", title="t2"),
            _t("/3", artist="Ann", date="2010", album="Early", title="t3"),
            _t("/4", artist="Ann", date="", album="NoYear", title="t4"),
            _t("/5", artist="Zed", date="2001", album="A", title="t5"),
            _t(
                "/6",
                artist="Ann",
                date="2010",
                album="Early",
                title="t3b",
                tracknumber="02",
            ),
        ]
        groups = group_by_artist_album_year(tracks)
        self.assertEqual([g.label for g in groups], ["Ann", "Zed"])
        ann = groups[0]
        self.assertEqual(
            [c.label for c in ann.children],
            ["Early - 2010", "Late - 2020", "NoYear - Unknown year"],
        )
        self.assertEqual(
            [t.meta.title for t in ann.children[0].tracks],
            ["t3", "t3b"],
        )
        zed = groups[1]
        self.assertEqual(
            [c.label for c in zed.children],
            ["A - 2001", "B - 2015"],
        )

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