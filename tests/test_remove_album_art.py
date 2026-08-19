"""Tests for removing on-device album art + clearing host cache."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mtpmanager.app.album_art_device import (
    remove_device_album_art,
    remove_device_album_art_many,
)
from mtpmanager.infra.device_index import (
    clear_device_album,
    get_device_album,
    record_device_album,
    upsert_device,
)
from mtpmanager.ports.transport import TransportError


class RemoveAlbumArtTests(unittest.TestCase):
    def test_deletes_object_and_clears_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "test-serial"
            upsert_device(serial, name="ZEN", path=db)
            key = "artist\0album"
            album_id = 4242
            record_device_album(
                serial,
                album_key=key,
                album_id=album_id,
                name="Album",
                artist="Artist",
                art_sha256="abc",
                track_ids=[1, 2],
                path=db,
            )
            device = MagicMock()
            result = remove_device_album_art(
                device=device,
                serial=serial,
                album_key=key,
                name="Album",
                artist="Artist",
                index_path=db,
            )
            device.delete_object.assert_called_once_with(album_id)
            self.assertTrue(result.deleted_object)
            self.assertTrue(result.cleared_cache)
            self.assertFalse(result.error)
            self.assertIsNone(get_device_album(serial, key, path=db))

    def test_clears_cache_when_object_already_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "test-serial"
            upsert_device(serial, name="ZEN", path=db)
            key = "a\0b"
            record_device_album(
                serial,
                album_key=key,
                album_id=99,
                name="B",
                artist="A",
                path=db,
            )
            device = MagicMock()
            device.delete_object.side_effect = TransportError(
                "gone", fatal=False
            )
            result = remove_device_album_art(
                device=device,
                serial=serial,
                album_key=key,
                index_path=db,
            )
            self.assertFalse(result.deleted_object)
            self.assertTrue(result.cleared_cache)
            self.assertEqual(result.error, "")
            self.assertIsNone(get_device_album(serial, key, path=db))

    def test_clear_device_album_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "s"
            upsert_device(serial, path=db)
            key = "x\0y"
            record_device_album(
                serial, album_key=key, album_id=1, path=db
            )
            self.assertTrue(clear_device_album(serial, key, path=db))
            self.assertFalse(clear_device_album(serial, key, path=db))

    def test_remove_many_dedupes_and_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "batch-serial"
            upsert_device(serial, name="ZEN", path=db)
            k1, k2 = "a\0one", "b\0two"
            record_device_album(
                serial, album_key=k1, album_id=10, name="One", path=db
            )
            record_device_album(
                serial, album_key=k2, album_id=20, name="Two", path=db
            )
            device = MagicMock()
            result = remove_device_album_art_many(
                device=device,
                serial=serial,
                albums=[
                    (k1, "One", "A"),
                    (k1, "One", "A"),  # dup
                    (k2, "Two", "B"),
                ],
                index_path=db,
            )
            self.assertEqual(len(result.albums), 2)
            self.assertEqual(result.deleted_count, 2)
            self.assertEqual(device.delete_object.call_count, 2)
            self.assertIsNone(get_device_album(serial, k1, path=db))
            self.assertIsNone(get_device_album(serial, k2, path=db))


if __name__ == "__main__":
    unittest.main()
