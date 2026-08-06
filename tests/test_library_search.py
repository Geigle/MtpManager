"""Unit tests for fuzzy library search."""

from __future__ import annotations

import unittest

from mtpmanager.domain.library_search import (
    filter_library_tracks,
    normalize_search_text,
    score_query_against_text,
    score_track,
)
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
        # Missing vowels still match as subsequence of characters in order.
        s = score_query_against_text("helloween", "helloween keeper of the seven keys")
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
        # Exact-ish title/artist hit ranks above unrelated.
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


if __name__ == "__main__":
    unittest.main()
