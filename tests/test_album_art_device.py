"""Unit tests for device album art grouping and push helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mtpmanager.app.album_art_device import (
    album_grouping_key,
    group_tracks_by_album,
    push_album_art_for_tracks,
)
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import new_track_guid
from mtpmanager.infra.device_index import (
    get_device_album,
    record_device_album,
    record_send,
)


class AlbumGroupingTests(unittest.TestCase):
    def test_grouping_key_prefers_albumartist(self) -> None:
        t = Track(
            path="/a.mp3",
            meta=TrackMetadata(
                artist="Feat Guest",
                albumartist="Main Band",
                album="Debut",
            ),
            guid=new_track_guid(),
        )
        key = album_grouping_key(t)
        self.assertIsNotNone(key)
        assert key is not None
        self.assertIn("main band", key)
        self.assertIn("debut", key)

    def test_empty_album_skipped(self) -> None:
        t = Track(
            path="/a.mp3",
            meta=TrackMetadata(artist="A", album=""),
            guid=new_track_guid(),
        )
        self.assertIsNone(album_grouping_key(t))

    def test_group_tracks_by_album(self) -> None:
        g1, g2 = new_track_guid(), new_track_guid()
        tracks = [
            Track(
                path="/1.mp3",
                meta=TrackMetadata(albumartist="A", album="One"),
                guid=g1,
            ),
            Track(
                path="/2.mp3",
                meta=TrackMetadata(albumartist="A", album="One"),
                guid=g2,
            ),
            Track(
                path="/3.mp3",
                meta=TrackMetadata(albumartist="B", album="Two"),
                guid=new_track_guid(),
            ),
        ]
        groups = group_tracks_by_album(tracks)
        self.assertEqual(len(groups), 2)
        keys = list(groups.keys())
        self.assertEqual(len(groups[keys[0]]), 2)


class PodcastCoverResolutionTests(unittest.TestCase):
    def test_prepare_jpeg_uses_podcast_artwork_file(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        from mtpmanager.app.album_art_device import _prepare_jpeg_for_group
        from mtpmanager.infra.album_art import prepare_device_cover_jpeg_from_image_file

        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp) / "artwork.png"
            Image.new("RGB", (120, 120), color=(200, 40, 40)).save(
                art, format="PNG"
            )
            out = prepare_device_cover_jpeg_from_image_file(
                str(art), max_edge=80, max_bytes=24 * 1024
            )
            self.assertIsNotNone(out)
            assert out is not None
            self.assertTrue(out[0][:2] == b"\xff\xd8")
            self.assertLessEqual(max(out[1], out[2]), 80)

            # Group with no embedded art still finds show art via mock path.
            track = Track(
                path=str(Path(tmp) / "missing.mp3"),
                meta=TrackMetadata(
                    artist="Host",
                    albumartist="Show Name",
                    album="Show Name",
                    title="Ep 1",
                    genre="Podcast",
                ),
                guid=new_track_guid(),
            )
            # No file → embedded fails; without DB show lookup returns None.
            self.assertIsNone(
                _prepare_jpeg_for_group([track], max_edge=80, max_bytes=20480)
            )


class AlbumArtPushTests(unittest.TestCase):
    def test_push_creates_album_and_sends_sample(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            music = Path(tmp) / "music"
            music.mkdir()
            track_path = music / "song.mp3"
            track_path.write_bytes(b"x")
            cover = music / "cover.jpg"
            Image.new("RGB", (200, 200), color=(40, 80, 160)).save(
                cover, format="JPEG"
            )

            guid = new_track_guid()
            track = Track(
                path=str(track_path),
                meta=TrackMetadata(
                    artist="Artist",
                    albumartist="Artist",
                    album="Album Title",
                    title="Song",
                ),
                guid=guid,
            )
            db = Path(tmp) / "device.db"
            serial = "test-serial"
            record_send(
                serial,
                remote_name=f"{guid}.mp3",
                guid=guid,
                item_id=1001,
                parent_id=100,
                storage_id=0x10001,
                path=db,
            )

            device = MagicMock()
            device.get_representative_sample_format.return_value = {
                "width": 80,
                "height": 80,
                "size": 24576,
                "filetype": 14,
                "filetype_name": "JPEG",
            }
            device.create_album.return_value = 5555
            device.send_representative_sample.return_value = None

            batch = push_album_art_for_tracks(
                device=device,
                serial=serial,
                tracks=[track],
                index_path=db,
            )
            self.assertEqual(batch.art_sent_count, 1)
            self.assertEqual(batch.ok_count, 1)
            device.create_album.assert_called_once()
            device.send_representative_sample.assert_called_once()
            args, kwargs = device.send_representative_sample.call_args
            self.assertEqual(args[0], 5555)
            self.assertTrue(args[1][:2] == b"\xff\xd8")
            cached = get_device_album(serial, album_grouping_key(track), path=db)
            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual(cached["album_id"], 5555)
            self.assertTrue(cached["art_sha256"])

    def test_push_skips_when_art_unchanged(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            music = Path(tmp) / "music"
            music.mkdir()
            track_path = music / "song.mp3"
            track_path.write_bytes(b"x")
            Image.new("RGB", (100, 100), color=(1, 2, 3)).save(
                music / "cover.png", format="PNG"
            )
            guid = new_track_guid()
            track = Track(
                path=str(track_path),
                meta=TrackMetadata(
                    artist="A", albumartist="A", album="Alb", title="T"
                ),
                guid=guid,
            )
            db = Path(tmp) / "device.db"
            serial = "s2"
            record_send(
                serial,
                remote_name=f"{guid}.mp3",
                guid=guid,
                item_id=42,
                path=db,
            )
            device = MagicMock()
            device.get_representative_sample_format.return_value = {
                "width": 80,
                "height": 80,
                "size": 24576,
                "filetype": 14,
            }
            device.create_album.return_value = 9
            # First push
            push_album_art_for_tracks(
                device=device, serial=serial, tracks=[track], index_path=db
            )
            self.assertEqual(device.send_representative_sample.call_count, 1)
            # Second push should skip art
            device.reset_mock()
            device.get_representative_sample_format.return_value = {
                "width": 80,
                "height": 80,
                "size": 24576,
                "filetype": 14,
            }
            batch = push_album_art_for_tracks(
                device=device, serial=serial, tracks=[track], index_path=db
            )
            self.assertEqual(batch.art_sent_count, 0)
            self.assertTrue(batch.albums[0].art_skipped)
            device.create_album.assert_not_called()
            device.send_representative_sample.assert_not_called()


if __name__ == "__main__":
    unittest.main()
