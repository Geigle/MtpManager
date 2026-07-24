"""Unit tests for device export map document (no device)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.models import DeviceInfo, DeviceTrackInfo, DeviceTrackRef
from mtpmanager.infra.device_export_map import (
    MAP_JSON_NAME,
    MAP_MD_NAME,
    build_entry_dict,
    build_map_document,
    load_export_map,
    write_export_maps,
)


class DeviceExportMapTests(unittest.TestCase):
    def test_round_trip_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ref = DeviceTrackRef(
                item_id=136,
                name="Dance.mp3",
                title="Dance",
                artist="Creative Technology Ltd",
                parent_id=100,
                storage_id=0x00010001,
                filetype=2,
            )
            info = DeviceTrackInfo(
                item_id=136,
                name="Dance.mp3",
                title="Dance",
                artist="Creative Technology Ltd",
                album="Creative",
                genre="Demo",
                parent_id=100,
                storage_id=0x00010001,
                filesize=2235499,
                filetype=2,
                duration_ms=92000,
                usecount=15,
            )
            entry = build_entry_dict(
                index=1,
                ref=ref,
                info=info,
                host_path=str(Path(tmp) / "Creative Technology Ltd - Dance.mp3"),
                status="ok",
                tags_written=True,
                filetype_desc="ISO MPEG-1 Audio Layer 3",
                export_dir=tmp,
            )
            self.assertTrue(entry["flags"]["looks_like_retail_demo"])
            self.assertFalse(entry["flags"]["tags_missing"])
            self.assertIn("desired_tags", entry)
            self.assertEqual(entry["desired_tags"]["title"], "Dance")

            doc = build_map_document(
                entries=[entry],
                dest_dir=tmp,
                device_info=DeviceInfo(
                    name="My Zen",
                    serial="ABC",
                    manufacturer="Creative",
                    model="ZEN Vision:M",
                ),
            )
            j, m = write_export_maps(doc, tmp)
            self.assertEqual(j.name, MAP_JSON_NAME)
            self.assertEqual(m.name, MAP_MD_NAME)
            loaded = load_export_map(tmp)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["schema_version"], 1)
            self.assertEqual(len(loaded["entries"]), 1)
            self.assertEqual(loaded["device"]["serial"], "ABC")
            # Ensure valid JSON and human-editable fields present
            raw = json.loads(j.read_text(encoding="utf-8"))
            self.assertIn("how_to_edit", raw)
            self.assertIn("editor_notes", raw["entries"][0])
            md = m.read_text(encoding="utf-8")
            self.assertIn("Dance", md)
            self.assertIn("item_id=136", md)

    def test_missing_tags_flag(self) -> None:
        ref = DeviceTrackRef(item_id=1, name="orphan.mp3")
        entry = build_entry_dict(
            index=1,
            ref=ref,
            info=None,
            host_path=None,
            status="failed",
            error="boom",
            export_dir="/tmp",
        )
        self.assertTrue(entry["flags"]["tags_missing"])
        self.assertTrue(entry["flags"]["needs_manual_tag_edit"])


if __name__ == "__main__":
    unittest.main()
