"""Parent folder resolution for music GUID vs podcast ZENcast."""

from __future__ import annotations

import unittest

from mtpmanager.app.transfer import _resolve_parent
from mtpmanager.domain.models import TrackMetadata


class ResolveParentTests(unittest.TestCase):
    def test_podcast_guid_uses_resolver(self) -> None:
        meta = TrackMetadata(
            artist="Network",
            album="Show",
            title="Ep",
            genre="Podcast",
        )
        parent = _resolve_parent(
            lambda m: 128,
            meta,
            guid="a" * 32,
        )
        self.assertEqual(parent, 128)

    def test_music_guid_ignores_artist_folder(self) -> None:
        meta = TrackMetadata(
            artist="Band",
            album="Album",
            title="Song",
            genre="Rock",
        )
        parent = _resolve_parent(
            lambda m: 999,
            meta,
            guid="b" * 32,
        )
        self.assertIsNone(parent)

    def test_music_without_guid_keeps_resolver(self) -> None:
        meta = TrackMetadata(
            artist="Band",
            album="Album",
            title="Song",
            genre="Rock",
        )
        parent = _resolve_parent(lambda m: 999, meta, guid="")
        self.assertEqual(parent, 999)

    def test_no_resolver(self) -> None:
        meta = TrackMetadata(genre="Podcast")
        self.assertIsNone(_resolve_parent(None, meta, guid="c" * 32))


if __name__ == "__main__":
    unittest.main()
