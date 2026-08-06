"""Unit tests for artist-aware playlist shuffles."""

from __future__ import annotations

import random
import unittest
from collections import Counter

from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_shuffle import (
    artist_key,
    merge_shuffle,
    seed_from_track,
    spotify_shuffle,
)


def _t(
    path: str,
    artist: str,
    *,
    album: str = "Alb",
    title: str | None = None,
    guid: str = "",
) -> Track:
    return Track(
        path=path,
        guid=guid,
        meta=TrackMetadata(
            artist=artist,
            albumartist=artist,
            album=album,
            title=title or path,
        ),
    )


class PlaylistShuffleTests(unittest.TestCase):
    def test_artist_key_unknown(self) -> None:
        t = _t("/a.mp3", "Unknown Artist")
        self.assertEqual(artist_key(t), "Unknown")

    def test_seed_stable(self) -> None:
        t = _t("/a.mp3", "A", guid="ab" * 16)
        self.assertEqual(seed_from_track(t), seed_from_track(t))

    def test_merge_preserves_membership(self) -> None:
        tracks = [
            _t(f"/{a}{i}.mp3", a, album=f"{a}-alb")
            for a, n in (("A", 5), ("B", 3), ("C", 2))
            for i in range(n)
        ]
        rng = random.Random(42)
        out = merge_shuffle(tracks, rng=rng)
        self.assertEqual(len(out), len(tracks))
        self.assertEqual(
            Counter(t.path for t in out),
            Counter(t.path for t in tracks),
        )

    def test_merge_reduces_same_artist_runs_vs_clumped(self) -> None:
        # Clumped input: AAAA BBB CC — merge should spread.
        tracks = (
            [_t(f"/a{i}.mp3", "A") for i in range(4)]
            + [_t(f"/b{i}.mp3", "B") for i in range(3)]
            + [_t(f"/c{i}.mp3", "C") for i in range(2)]
        )
        out = merge_shuffle(tracks, rng=random.Random(7))

        def max_run(seq: list[Track]) -> int:
            best = cur = 1
            for i in range(1, len(seq)):
                if artist_key(seq[i]) == artist_key(seq[i - 1]):
                    cur += 1
                    best = max(best, cur)
                else:
                    cur = 1
            return best

        self.assertLessEqual(max_run(out), max_run(tracks))
        # With 4+3+2, max same-artist run can be >1 but should not stay 4.
        self.assertLess(max_run(out), 4)

    def test_merge_single_artist_permutation(self) -> None:
        tracks = [_t(f"/a{i}.mp3", "Only") for i in range(5)]
        out = merge_shuffle(tracks, rng=random.Random(1))
        self.assertEqual(sorted(t.path for t in out), sorted(t.path for t in tracks))

    def test_spotify_preserves_membership(self) -> None:
        tracks = [
            _t(f"/{a}{i}.mp3", a)
            for a, n in (("X", 4), ("Y", 4), ("Z", 2))
            for i in range(n)
        ]
        out = spotify_shuffle(tracks, rng=random.Random(99))
        self.assertEqual(len(out), len(tracks))
        self.assertEqual(
            {t.path for t in out},
            {t.path for t in tracks},
        )

    def test_spotify_deterministic_with_seed(self) -> None:
        tracks = [
            _t(f"/{a}{i}.mp3", a, guid=f"{a}{i}" + "0" * 30)
            for a, n in (("P", 3), ("Q", 3))
            for i in range(n)
        ]
        seed_track = tracks[2]
        from mtpmanager.domain.playlist_shuffle import rng_from_seed_track

        a = spotify_shuffle(tracks, rng=rng_from_seed_track(seed_track, extra="spotify"))
        b = spotify_shuffle(tracks, rng=rng_from_seed_track(seed_track, extra="spotify"))
        self.assertEqual([t.path for t in a], [t.path for t in b])

    def test_empty_and_singleton(self) -> None:
        self.assertEqual(merge_shuffle([]), [])
        self.assertEqual(spotify_shuffle([]), [])
        one = [_t("/one.mp3", "Solo")]
        self.assertEqual(merge_shuffle(one)[0].path, "/one.mp3")
        self.assertEqual(spotify_shuffle(one)[0].path, "/one.mp3")


if __name__ == "__main__":
    unittest.main()
