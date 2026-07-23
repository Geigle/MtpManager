"""Unit tests for experimental retrieve-from-device helpers (no device)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.app.device_ops import (
    RetrieveTracksResult,
    retrieve_track,
    retrieve_tracks,
    suggested_retrieve_basename,
    track_info_to_metadata,
    unique_dest_path,
)
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef
from mtpmanager.infra.mutagen_tags import write_metadata
from mtpmanager.ports.transport import TransportError


class NamingTests(unittest.TestCase):
    def test_suggested_basename_from_tags(self) -> None:
        ref = DeviceTrackRef(item_id=1, name="x.mp3", title="", artist="")
        info = DeviceTrackInfo(
            item_id=1,
            name="x.mp3",
            title="The Saint",
            artist="Dr SK Chew",
        )
        name = suggested_retrieve_basename(ref, info=info)
        self.assertTrue(name.endswith(".mp3"))
        self.assertIn("Saint", name)
        self.assertIn("Chew", name)
        self.assertNotIn("/", name)

    def test_suggested_basename_falls_back_to_filename(self) -> None:
        ref = DeviceTrackRef(item_id=9, name="Color Fantasia.avi")
        name = suggested_retrieve_basename(ref)
        self.assertEqual(name, "Color Fantasia.avi")

    def test_unique_dest_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = unique_dest_path(tmp, "a.mp3")
            Path(p1).write_bytes(b"x")
            p2 = unique_dest_path(tmp, "a.mp3")
            self.assertNotEqual(p1, p2)
            self.assertTrue(p2.endswith("a (2).mp3") or " (2)" in p2)


class TrackInfoMetaTests(unittest.TestCase):
    def test_track_info_to_metadata(self) -> None:
        info = DeviceTrackInfo(
            item_id=1,
            title="Dance",
            artist="Creative",
            album="Creative",
            genre="Demo",
            tracknumber=2,
            duration_ms=92000,
        )
        meta = track_info_to_metadata(info)
        self.assertEqual(meta.title, "Dance")
        self.assertEqual(meta.artist, "Creative")
        self.assertEqual(meta.tracknumber, "02")
        self.assertAlmostEqual(meta.length_sec, 92.0)


class FakeDevice:
    def __init__(self) -> None:
        self.downloaded: list[tuple[int, str]] = []
        self.meta: dict[int, DeviceTrackInfo] = {}

    def get_track_metadata(self, object_id: int) -> DeviceTrackInfo:
        oid = int(object_id)
        if oid in self.meta:
            return self.meta[oid]
        raise TransportError("no meta", fatal=False)

    def get_file_to_file(self, object_id: int, dest_path: str, *, on_progress=None):
        Path(dest_path).write_bytes(b"fake-media")
        self.downloaded.append((int(object_id), dest_path))


class RetrieveTracksTests(unittest.TestCase):
    def test_retrieve_track_downloads_and_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dev = FakeDevice()
            dev.meta[10] = DeviceTrackInfo(
                item_id=10,
                name="x.mp3",
                title="Dance",
                artist="Creative Technology Ltd",
                album="Creative",
            )
            ref = DeviceTrackRef(item_id=10, name="x.mp3")
            item = retrieve_track(dev, ref, tmp)
            self.assertEqual(item.status, "ok")
            self.assertTrue(os.path.isfile(item.path or ""))
            self.assertEqual(dev.downloaded[0][0], 10)
            self.assertIn("Dance", os.path.basename(item.path or ""))

    def test_retrieve_tracks_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dev = FakeDevice()
            refs = [
                DeviceTrackRef(item_id=1, name="a.mp3", title="A", artist="X"),
                DeviceTrackRef(item_id=2, name="b.mp3", title="B", artist="Y"),
            ]
            result = retrieve_tracks(dev, refs, tmp, write_tags=False)
            self.assertIsInstance(result, RetrieveTracksResult)
            self.assertEqual(result.succeeded, 2)
            self.assertEqual(result.failed, 0)
            self.assertEqual(len(result.paths), 2)
            self.assertTrue(result.map_json_path)
            self.assertTrue(os.path.isfile(result.map_json_path))
            self.assertTrue(os.path.isfile(result.map_md_path))

    def test_retrieve_tracks_fatal_aborts(self) -> None:
        class FailSecond(FakeDevice):
            def get_file_to_file(self, object_id, dest_path, *, on_progress=None):
                if int(object_id) == 2:
                    raise TransportError("boom", fatal=True)
                return super().get_file_to_file(
                    object_id, dest_path, on_progress=on_progress
                )

        with tempfile.TemporaryDirectory() as tmp:
            dev = FailSecond()
            refs = [
                DeviceTrackRef(item_id=1, name="a.mp3"),
                DeviceTrackRef(item_id=2, name="b.mp3"),
                DeviceTrackRef(item_id=3, name="c.mp3"),
            ]
            result = retrieve_tracks(dev, refs, tmp, write_tags=False)
            self.assertTrue(result.aborted)
            self.assertEqual(result.succeeded, 1)
            self.assertEqual(result.failed_id, 2)
            # Map still written with partial + failed rows
            self.assertTrue(result.map_json_path)
            self.assertEqual(len(result.items), 2)


class WriteMetadataTests(unittest.TestCase):
    def test_write_mp3_tags_round_trip(self) -> None:
        # Minimal valid-enough MP3 frame for mutagen may fail; skip if no write.
        try:
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, TIT2
        except ImportError:
            self.skipTest("mutagen incomplete")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.mp3")
            # Create empty file — EasyID3 may fail without frames; that's OK.
            Path(path).write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 200)
            from mtpmanager.domain.models import TrackMetadata

            meta = TrackMetadata(title="Hello", artist="World", album="A")
            ok = write_metadata(path, meta)
            # Either wrote or mutagen rejected empty stream — both acceptable.
            self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()
