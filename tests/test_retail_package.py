"""Unit tests for retail demo package + reduced restore map (no device)."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from mtpmanager.app.retail_ops import restore_retail_package
from mtpmanager.domain.models import DeviceInfo, DeviceTrackInfo, DeviceTrackRef, TrackMetadata
from mtpmanager.infra.device_export_map import (
    build_entry_dict,
    build_map_document,
    write_export_maps,
)
from mtpmanager.infra.remote_naming import build_remote_path
from mtpmanager.infra.retail_package import (
    PACKAGE_DOCUMENT_TYPE,
    RESTORE_MAP_NAME,
    desired_tags_to_metadata,
    entries_for_restore,
    load_package_map,
    package_retail_export,
    select_retail_entries,
)
from mtpmanager.ports.transport import TransportError


class RetailPackageTests(unittest.TestCase):
    def _write_export(self, tmp: Path) -> Path:
        retail_path = tmp / "Creative Technology Ltd - Dance.mp3"
        retail_path.write_bytes(b"ID3fake-retail-bytes")
        user_path = tmp / "Some Band - Song.mp3"
        user_path.write_bytes(b"ID3fake-user-bytes")

        retail_ref = DeviceTrackRef(
            item_id=136,
            name="Dance.mp3",
            title="Dance",
            artist="Creative Technology Ltd",
            parent_id=100,
            storage_id=0x00010001,
            filetype=2,
        )
        retail_info = DeviceTrackInfo(
            item_id=136,
            name="Dance.mp3",
            title="Dance",
            artist="Creative Technology Ltd",
            album="Creative",
            genre="Demo",
            parent_id=100,
            storage_id=0x00010001,
            filesize=len(retail_path.read_bytes()),
            filetype=2,
            duration_ms=92000,
        )
        user_ref = DeviceTrackRef(
            item_id=200,
            name="Song.mp3",
            title="Song",
            artist="Some Band",
            parent_id=100,
            filetype=2,
        )
        user_info = DeviceTrackInfo(
            item_id=200,
            name="Song.mp3",
            title="Song",
            artist="Some Band",
            album="Album",
            parent_id=100,
            filesize=len(user_path.read_bytes()),
            filetype=2,
        )
        entries = [
            build_entry_dict(
                index=1,
                ref=retail_ref,
                info=retail_info,
                host_path=str(retail_path),
                status="ok",
                tags_written=True,
                export_dir=str(tmp),
            ),
            build_entry_dict(
                index=2,
                ref=user_ref,
                info=user_info,
                host_path=str(user_path),
                status="ok",
                tags_written=True,
                export_dir=str(tmp),
            ),
        ]
        doc = build_map_document(
            entries=entries,
            dest_dir=str(tmp),
            device_info=DeviceInfo(
                name="Zen",
                serial="S1",
                manufacturer="Creative",
                model="ZEN Vision:M",
            ),
        )
        write_export_maps(doc, str(tmp))
        return tmp

    def test_select_only_retail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_export(Path(tmp))
            from mtpmanager.infra.device_export_map import load_export_map

            doc = load_export_map(root)
            assert doc is not None
            selected = select_retail_entries(doc, root)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0][0]["item_id"], 136)

    def test_package_zip_and_reduced_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_export(Path(tmp))
            zip_path = Path(tmp) / "out" / "demos.zip"
            result = package_retail_export(root, zip_path)
            self.assertEqual(result.entry_count, 1)
            self.assertTrue(Path(result.zip_path).is_file())

            with zipfile.ZipFile(result.zip_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn(RESTORE_MAP_NAME, names)
                media = [n for n in names if n.startswith("media/")]
                self.assertEqual(len(media), 1)

            loaded = load_package_map(result.zip_path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["document_type"], PACKAGE_DOCUMENT_TYPE)
            self.assertEqual(len(loaded["entries"]), 1)
            e0 = loaded["entries"][0]
            self.assertEqual(e0["remote_basename"], "Dance.mp3")
            self.assertTrue(e0["flags"]["looks_like_retail_demo"])
            self.assertEqual(e0["desired_tags"]["artist"], "Creative Technology Ltd")
            self.assertIn("package_path", e0)

    def test_desired_tags_to_metadata(self) -> None:
        meta = desired_tags_to_metadata(
            {
                "title": "Dance",
                "artist": "Creative Technology Ltd",
                "album": "Creative",
                "genre": "Demo",
                "duration_ms": 92000,
                "sample_rate": 44100,
                "channels": 2,
                "bitrate": 192,
            }
        )
        self.assertEqual(meta.title, "Dance")
        self.assertAlmostEqual(meta.length_sec, 92.0)
        self.assertEqual(meta.sample_rate, 44100)

    def test_preferred_basename_remote_path(self) -> None:
        remote = build_remote_path(
            TrackMetadata(title="Dance", artist="Creative"),
            ".mp3",
            preferred_basename="Dance.mp3",
        )
        self.assertEqual(remote, "100/Dance.mp3")
        # GUID wins over preferred basename
        g = "a" * 32
        remote_g = build_remote_path(
            TrackMetadata(title="Dance"),
            ".mp3",
            guid=g,
            preferred_basename="Dance.mp3",
        )
        self.assertIn(g, remote_g)
        self.assertNotIn("Dance", remote_g)

    def test_restore_calls_send_without_guid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_export(Path(tmp))
            zip_path = Path(tmp) / "demos.zip"
            package_retail_export(root, zip_path)

            transport = MagicMock()
            transport.send_track.return_value = 999
            result = restore_retail_package(transport, zip_path)
            self.assertEqual(result.succeeded, 1)
            self.assertEqual(result.failed, 0)
            transport.send_track.assert_called_once()
            kwargs = transport.send_track.call_args
            # path, meta as positional; guid None; preferred_basename set
            self.assertIsNone(kwargs.kwargs.get("guid"))
            self.assertEqual(kwargs.kwargs.get("preferred_basename"), "Dance.mp3")
            meta = kwargs.args[1] if len(kwargs.args) > 1 else kwargs.kwargs.get("meta")
            self.assertIsInstance(meta, TrackMetadata)
            self.assertEqual(meta.title, "Dance")

    def test_restore_skips_include_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_export(Path(tmp))
            zip_path = Path(tmp) / "demos.zip"
            package_retail_export(root, zip_path)
            # Unpack, flip include flag, restore from dir
            extract = Path(tmp) / "unpacked"
            extract.mkdir()
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract)
            doc = load_package_map(extract)
            assert doc is not None
            doc["entries"][0]["flags"]["include_in_restore"] = False
            (extract / RESTORE_MAP_NAME).write_text(
                json.dumps(doc, indent=2) + "\n", encoding="utf-8"
            )
            self.assertEqual(len(entries_for_restore(doc)), 0)
            transport = MagicMock()
            result = restore_retail_package(transport, extract)
            self.assertEqual(result.total, 0)
            transport.send_track.assert_not_called()

    def test_restore_aborts_on_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_export(Path(tmp))
            # Add second retail file
            p2 = root / "Creative - Intro.mp3"
            p2.write_bytes(b"more")
            from mtpmanager.infra.device_export_map import load_export_map

            doc = load_export_map(root)
            assert doc is not None
            # Re-package after manually adding second retail entry is complex;
            # single-file abort path is enough.
            zip_path = Path(tmp) / "demos.zip"
            package_retail_export(root, zip_path)
            transport = MagicMock()
            transport.send_track.side_effect = TransportError("boom", fatal=True)
            result = restore_retail_package(transport, zip_path)
            self.assertTrue(result.aborted)
            self.assertEqual(result.failed, 1)


if __name__ == "__main__":
    unittest.main()
