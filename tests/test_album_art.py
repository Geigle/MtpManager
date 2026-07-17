"""Unit tests for album art extraction / disk cache."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.album_art import (
    DEFAULT_THUMB_SIZE,
    cached_thumb_exists,
    ensure_cached_thumb,
    load_cover_bytes,
    photoimage_from_cache_file,
    warm_album_thumbs,
)


class AlbumArtTests(unittest.TestCase):
    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_cover_bytes("/no/such/file.flac"))

    def test_sidecar_and_disk_cache(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            music = Path(tmp) / "music"
            music.mkdir()
            track = music / "song.flac"
            track.write_bytes(b"not a real flac")
            cover = music / "cover.png"
            Image.new("RGB", (64, 64), color=(20, 40, 200)).save(cover, format="PNG")

            self.assertIsNone(
                cached_thumb_exists(str(track), size=DEFAULT_THUMB_SIZE, data_dir=data_dir)
            )
            path = ensure_cached_thumb(
                str(track), size=DEFAULT_THUMB_SIZE, data_dir=data_dir
            )
            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.is_file())
            # Second call is cache hit
            path2 = ensure_cached_thumb(
                str(track), size=DEFAULT_THUMB_SIZE, data_dir=data_dir
            )
            self.assertEqual(path, path2)

            n = warm_album_thumbs(
                [str(track)], size=DEFAULT_THUMB_SIZE, data_dir=data_dir
            )
            self.assertEqual(n, 1)

            from tkinter import Tk

            root = Tk()
            root.withdraw()
            try:
                photo = photoimage_from_cache_file(path, master=root)
                self.assertIsNotNone(photo)
                assert photo is not None
                self.assertEqual(photo.width(), DEFAULT_THUMB_SIZE)
                self.assertEqual(photo.height(), DEFAULT_THUMB_SIZE)
            finally:
                root.destroy()

    def test_jpeg_sidecar_cached_as_png(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            music = Path(tmp) / "music"
            music.mkdir()
            track = music / "song.mp3"
            track.write_bytes(b"x")
            cover = music / "folder.jpg"
            buf = io.BytesIO()
            Image.new("RGB", (120, 80), color=(200, 20, 20)).save(buf, format="JPEG")
            cover.write_bytes(buf.getvalue())

            path = ensure_cached_thumb(
                str(track), size=DEFAULT_THUMB_SIZE, data_dir=data_dir
            )
            self.assertIsNotNone(path)
            assert path is not None
            self.assertEqual(path.suffix, ".png")


if __name__ == "__main__":
    unittest.main()
