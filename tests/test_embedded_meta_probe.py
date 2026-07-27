"""Unit tests for embedded file-tag recovery from device objects."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtpmanager.app.device_ops import (
    probe_embedded_metadata,
    resolve_tags_with_embedded_fallback,
)
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef, TrackMetadata
from mtpmanager.ports.transport import TransportError


class _FakeDevice:
    def __init__(self, payload: bytes = b"x", *, info=None, fail_download=False):
        self.payload = payload
        self.info = info
        self.fail_download = fail_download
        self.downloads: list[tuple[int, str]] = []

    def get_file_to_file(self, oid, dest, on_progress=None):
        self.downloads.append((int(oid), dest))
        if self.fail_download:
            raise TransportError("download failed", fatal=False)
        Path(dest).write_bytes(self.payload)

    def get_track_metadata(self, oid):
        if self.info is not None:
            return self.info
        raise TransportError("no meta", fatal=False)


class EmbeddedMetaProbeTests(unittest.TestCase):
    def test_probe_returns_usable_meta(self) -> None:
        device = _FakeDevice()
        ref = DeviceTrackRef(
            item_id=7,
            name="track.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
        )
        good = TrackMetadata(title="Real Title", artist="Real Artist", album="LP")
        with patch(
            "mtpmanager.app.device_ops.read_metadata", return_value=good
        ):
            result = probe_embedded_metadata(device, ref)
        self.assertTrue(result.usable)
        self.assertEqual(result.meta.title, "Real Title")
        self.assertEqual(result.meta.artist, "Real Artist")
        self.assertIsNone(result.path)  # cleaned up
        self.assertEqual(len(device.downloads), 1)
        # Temp file removed.
        self.assertFalse(os.path.isfile(device.downloads[0][1]))

    def test_probe_keep_file(self) -> None:
        device = _FakeDevice()
        ref = DeviceTrackRef(item_id=8, name="a.mp3")
        good = TrackMetadata(title="T", artist="A")
        with patch(
            "mtpmanager.app.device_ops.read_metadata", return_value=good
        ):
            result = probe_embedded_metadata(device, ref, keep_file=True)
        self.assertTrue(result.usable)
        self.assertIsNotNone(result.path)
        assert result.path is not None
        self.assertTrue(os.path.isfile(result.path))
        os.remove(result.path)

    def test_probe_rejects_placeholder_file_tags(self) -> None:
        device = _FakeDevice()
        ref = DeviceTrackRef(item_id=9, name="b.mp3")
        bad = TrackMetadata(
            title="Unknown Title", artist="Unknown Artist", album="Unknown Album"
        )
        with patch(
            "mtpmanager.app.device_ops.read_metadata", return_value=bad
        ):
            result = probe_embedded_metadata(device, ref)
        self.assertFalse(result.usable)
        self.assertIsNone(result.meta)

    def test_resolve_skips_probe_when_device_tags_ok(self) -> None:
        info = DeviceTrackInfo(
            item_id=1,
            name="ok.mp3",
            title="Song",
            artist="Band",
            album="Album",
        )
        device = _FakeDevice(info=info)
        ref = DeviceTrackRef(item_id=1, name="ok.mp3")
        with patch(
            "mtpmanager.app.device_ops.probe_embedded_metadata"
        ) as probe:
            out_info, file_meta, path = resolve_tags_with_embedded_fallback(
                device, ref
            )
        probe.assert_not_called()
        self.assertEqual(out_info.title, "Song")
        self.assertIsNone(file_meta)
        self.assertIsNone(path)
        self.assertEqual(device.downloads, [])

    def test_resolve_probes_when_placeholder(self) -> None:
        info = DeviceTrackInfo(
            item_id=2,
            name="bad.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
            album="Unknown Album",
        )
        device = _FakeDevice(info=info)
        ref = DeviceTrackRef(
            item_id=2,
            name="bad.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
        )
        good = TrackMetadata(title="From File", artist="From File Artist")
        with patch(
            "mtpmanager.app.device_ops.read_metadata", return_value=good
        ):
            out_info, file_meta, path = resolve_tags_with_embedded_fallback(
                device, ref, keep_download=True
            )
        self.assertIsNotNone(file_meta)
        assert file_meta is not None
        self.assertEqual(file_meta.title, "From File")
        self.assertEqual(out_info.title, "From File")
        self.assertIsNotNone(path)
        if path and os.path.isfile(path):
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
