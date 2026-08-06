"""Unit tests for fuzzy library search."""

from __future__ import annotations

import unittest

from mtpmanager.domain.library_search import (
    filter_library_tracks,
    filter_library_tracks_scored,
    normalize_search_text,
    parse_search_query,
    reorder_groups_by_score,
    score_query_against_text,
    score_track,
)
from mtpmanager.domain.library_sort import GroupNode
from mtpmanager.domain.models import Track, TrackMetadata


def _t(path: str, *, title: str, artist: str, album: str = "") -> Track:
    return Track(
        path=path,
        meta=TrackMetadata(
            title=title,
            artist=artist,
            albumartist=artist,
            album=album,
        ),
    )


class LibrarySearchTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize_search_text("  Foo   BAR "), "foo bar")

    def test_substring_beats_partial(self) -> None:
        blob = "blind guardian nightfall in middle earth"
        self.assertGreater(
            score_query_against_text("nightfall", blob),
            score_query_against_text("ngtfll", blob),
        )

    def test_subsequence_typo_tolerant(self) -> None:
        s = score_query_against_text(
            "helloween", "helloween keeper of the seven keys"
        )
        self.assertGreater(s, 0.5)

    def test_filter_membership_and_order(self) -> None:
        tracks = [
            _t("/a/hammerfall.mp3", title="Hammerfall", artist="HammerFall"),
            _t("/b/nightwish.mp3", title="Ghost Love Score", artist="Nightwish"),
            _t("/c/other.mp3", title="Something Else", artist="Nobody"),
        ]
        out = filter_library_tracks(tracks, "night")
        paths = [t.path for t in out]
        self.assertIn("/b/nightwish.mp3", paths)
        self.assertNotIn("/c/other.mp3", paths)
        self.assertEqual(paths[0], "/b/nightwish.mp3")

    def test_multi_token_and(self) -> None:
        tracks = [
            _t("/a.mp3", title="The Final Countdown", artist="Europe"),
            _t("/b.mp3", title="Countdown", artist="Other"),
        ]
        out = filter_library_tracks(tracks, "final europe")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].path, "/a.mp3")

    def test_empty_query_preserves_all(self) -> None:
        tracks = [
            _t("/a.mp3", title="A", artist="A"),
            _t("/b.mp3", title="B", artist="B"),
        ]
        out = filter_library_tracks(tracks, "   ")
        self.assertEqual([t.path for t in out], ["/a.mp3", "/b.mp3"])

    def test_score_track_uses_album(self) -> None:
        t = _t(
            "/x.mp3",
            title="Intro",
            artist="Band",
            album="Powerslave",
        )
        self.assertGreater(score_track("powerslave", t), 0.5)

    def test_no_default_artist_boost(self) -> None:
        """Equal base weights: same string on artist vs title scores similarly."""
        by_artist = _t(
            "/artist.mp3", title="Some Song", artist="Nightfall", album="Debut"
        )
        by_title = _t(
            "/title.mp3", title="Nightfall", artist="Other Band", album="Hits"
        )
        sa = score_track("nightfall", by_artist)
        st = score_track("nightfall", by_title)
        # Both strong substring hits; within a small band (no 3x artist boost).
        self.assertAlmostEqual(sa, st, delta=0.25)

    def test_parse_field_keywords(self) -> None:
        p = parse_search_query('artist:nightwish album:"once" free')
        self.assertEqual(p.free_text, "free")
        self.assertEqual(p.field_terms["artist"], ("nightwish",))
        self.assertEqual(p.field_terms["album"], ("once",))

    def test_artist_keyword_boosts_and_requires_field(self) -> None:
        by_artist = _t(
            "/a.mp3", title="Ghost Love Score", artist="Nightwish", album="Once"
        )
        by_title_only = _t(
            "/b.mp3", title="Nightwish Tribute", artist="Cover Band", album="X"
        )
        # Free text: both match.
        free = filter_library_tracks([by_artist, by_title_only], "nightwish")
        self.assertEqual(len(free), 2)
        # artist: requires artist field — title-only drop out.
        out = filter_library_tracks(
            [by_artist, by_title_only], "artist:nightwish"
        )
        self.assertEqual([t.path for t in out], ["/a.mp3"])
        # Keyword boost ranks pure artist match above a weaker free match.
        sa = score_track("artist:nightwish", by_artist)
        st = score_track("nightwish", by_title_only)
        self.assertGreater(sa, st)

    def test_reorder_groups_by_score(self) -> None:
        weak = _t("/w.mp3", title="Weak", artist="A", album="One")
        strong = _t("/s.mp3", title="Nightfall", artist="B", album="Two")
        mid = _t("/m.mp3", title="Night something", artist="A", album="One")
        scores = {
            weak.path: 0.2,
            mid.path: 0.5,
            strong.path: 1.0,
        }
        groups = [
            GroupNode(
                key="a",
                label="A",
                children=(
                    GroupNode(key="a1", label="One", tracks=(weak, mid)),
                ),
            ),
            GroupNode(
                key="b",
                label="B",
                children=(
                    GroupNode(key="b1", label="Two", tracks=(strong,)),
                ),
            ),
        ]
        ranked = reorder_groups_by_score(groups, scores)
        self.assertEqual(ranked[0].key, "b")
        one = ranked[1].children[0]
        self.assertEqual([t.path for t in one.tracks], [mid.path, weak.path])


if __name__ == "__main__":
    unittest.main()
