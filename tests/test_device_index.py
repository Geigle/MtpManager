"""Unit tests for durable device file inventory (no device / USB)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.models import DeviceInfo, FileEntry
from mtpmanager.domain.track_id import new_track_guid
from mtpmanager.infra.device_index import (
    device_list_is_complete,
    device_serial_key,
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
            # Synthetic negative item_id when MTP id unknown
            self.assertLess(files[0].item_id, 0)
            self.assertEqual(files[0].name, f"{g}.mp3")

    def test_duplicate_names_in_listing_ok(self) -> None:
        """Same basename under different parents / duplicate item_ids must not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            g = new_track_guid()
            n = replace_device_listing(
                "SER_DUP",
                [
                    _file(10, f"{g}.mp3", parent=100),
                    _file(11, f"{g}.mp3", parent=200),  # same name, other folder
                    _file(10, f"{g}.mp3", parent=100),  # duplicate item_id
                    _file(12, "readme.txt", parent=100),
                ],
                path=db,
            )
            self.assertGreaterEqual(n, 2)
            files = list_cached_files("SER_DUP", path=db)
            ids = {f.item_id for f in files}
            self.assertIn(10, ids)
            self.assertIn(11, ids)
            self.assertEqual(guid_stems_on_device("SER_DUP", path=db), {g})

    def test_device_serial_key_prefers_mtp_serial(self) -> None:
        info = DeviceInfo(
            name="ZEN A",
            serial="ABC123",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        # Serial alone — not combined with name/model.
        self.assertEqual(device_serial_key(info), "ABC123")

    def test_device_serial_key_fingerprint_ignores_friendly_name(self) -> None:
        a = DeviceInfo(
            name="My ZEN",
            serial="",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        b = DeviceInfo(
            name="Other ZEN",  # rename must not change inventory key
            serial="",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        c = DeviceInfo(
            name="My ZEN",
            serial="",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        other_model = DeviceInfo(
            name="My ZEN",
            serial="",
            manufacturer="Creative",
            model="ZEN Micro",
        )
        ka, kb, kc = device_serial_key(a), device_serial_key(b), device_serial_key(c)
        self.assertTrue(ka.startswith("fp:"))
        self.assertEqual(ka, kb)  # friendly name ignored
        self.assertEqual(ka, kc)
        self.assertNotEqual(ka, device_serial_key(other_model))
        self.assertNotEqual(ka, "default")

    def test_two_models_isolated_by_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            k1 = device_serial_key(
                DeviceInfo(name="Player One", manufacturer="Creative", model="ZEN")
            )
            k2 = device_serial_key(
                DeviceInfo(
                    name="Player One",
                    manufacturer="Creative",
                    model="ZEN Vision:M",
                )
            )
            self.assertNotEqual(k1, k2)
            g1, g2 = new_track_guid(), new_track_guid()
            replace_device_listing(k1, [_file(1, f"{g1}.mp3")], path=db)
            replace_device_listing(k2, [_file(2, f"{g2}.mp3")], path=db)
            self.assertEqual(guid_stems_on_device(k1, path=db), {g1})
            self.assertEqual(guid_stems_on_device(k2, path=db), {g2})
            self.assertEqual(len(list_cached_files(k1, path=db)), 1)
            self.assertEqual(len(list_cached_files(k2, path=db)), 1)

    def test_serial_key_stable_after_rename(self) -> None:
        """Same serial / same mfr+model → same key after friendly-name change."""
        before = DeviceInfo(
            name="Old Name",
            serial="SN99",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        after = DeviceInfo(
            name="New Name",
            serial="SN99",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        self.assertEqual(device_serial_key(before), device_serial_key(after))
        no_serial_before = DeviceInfo(
            name="Old Name",
            serial="",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        no_serial_after = DeviceInfo(
            name="New Name",
            serial="",
            manufacturer="Creative",
            model="ZEN Vision:M",
        )
        self.assertEqual(
            device_serial_key(no_serial_before),
            device_serial_key(no_serial_after),
        )


if __name__ == "__main__":
    unittest.main()
