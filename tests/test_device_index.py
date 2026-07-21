"""Unit tests for durable device file inventory (no device / USB)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.models import FileEntry
from mtpmanager.domain.track_id import new_track_guid
from mtpmanager.infra.device_index import (
    device_list_is_complete,
    guid_stems_on_device,
    list_cached_files,
    list_cached_track_refs,
    record_send,
    remove_by_item_id,
    replace_device_listing,
    upsert_device,
)


def _file(oid: int, name: str, *, parent: int = 100) -> FileEntry:
    return FileEntry(
        item_id=oid,
        name=name,
        parent_id=parent,
        storage_id=0x00010001,
        filesize=1000,
        filetype=2,
    )


class DeviceIndexTests(unittest.TestCase):
    def test_replace_and_guid_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            g1 = new_track_guid()
            g2 = new_track_guid()
            replace_device_listing(
                "SER1",
                [
                    _file(1, f"{g1}.mp3"),
                    _file(2, "08 Title.mp3"),
                    _file(3, f"{g2}.wma"),
                ],
                path=db,
            )
            self.assertTrue(device_list_is_complete("SER1", path=db))
            stems = guid_stems_on_device("SER1", path=db)
            self.assertEqual(stems, {g1, g2})
            files = list_cached_files("SER1", path=db)
            self.assertEqual(len(files), 3)
            refs = list_cached_track_refs("SER1", path=db)
            self.assertEqual(len(refs), 3)

    def test_record_send_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            upsert_device("SER1", name="ZEN", path=db)
            g = new_track_guid()
            record_send(
                "SER1",
                remote_name=f"{g}.mp3",
                guid=g,
                item_id=99,
                path=db,
            )
            self.assertEqual(guid_stems_on_device("SER1", path=db), {g})
            n = remove_by_item_id("SER1", 99, path=db)
            self.assertEqual(n, 1)
            self.assertEqual(guid_stems_on_device("SER1", path=db), set())

    def test_replace_clears_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            g_old = new_track_guid()
            g_new = new_track_guid()
            replace_device_listing(
                "S", [_file(1, f"{g_old}.mp3")], path=db
            )
            replace_device_listing(
                "S", [_file(2, f"{g_new}.mp3")], path=db
            )
            self.assertEqual(guid_stems_on_device("S", path=db), {g_new})
            self.assertEqual(len(list_cached_files("S", path=db)), 1)

    def test_serials_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            ga = new_track_guid()
            gb = new_track_guid()
            replace_device_listing("A", [_file(1, f"{ga}.mp3")], path=db)
            replace_device_listing("B", [_file(2, f"{gb}.mp3")], path=db)
            self.assertEqual(guid_stems_on_device("A", path=db), {ga})
            self.assertEqual(guid_stems_on_device("B", path=db), {gb})

    def test_record_send_without_item_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            g = new_track_guid()
            record_send(
                "default",
                remote_name=f"{g}.mp3",
                guid=g,
                item_id=None,
                path=db,
            )
            self.assertEqual(guid_stems_on_device("default", path=db), {g})
            files = list_cached_files("default", path=db)
            self.assertEqual(files[0].item_id, 0)
            self.assertEqual(files[0].name, f"{g}.mp3")


if __name__ == "__main__":
    unittest.main()
