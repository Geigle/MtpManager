"""Unit tests: device identity vs full info soft-fail (no hardware)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from mtpmanager.infra.pymtp_device import PymtpDevice


class _FakeMtp:
    """Minimal pymtp.MTP stand-in with controllable failures."""

    def __init__(self) -> None:
        self.device = object()  # "connected"
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def _record(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} failed")

    def get_devicename(self):
        self._record("get_devicename")
        return b"Gage's Zen"

    def get_serialnumber(self):
        self._record("get_serialnumber")
        return b"SN123"

    def get_manufacturer(self):
        self._record("get_manufacturer")
        return b"Creative Technology Ltd"

    def get_modelname(self):
        self._record("get_modelname")
        return b"ZEN Vision:M"

    def get_deviceversion(self):
        self._record("get_deviceversion")
        return b"1.61"

    def get_batterylevel(self):
        self._record("get_batterylevel")
        return (100, 80)

    def get_freespace(self):
        self._record("get_freespace")
        return 1_000_000

    def get_totalspace(self):
        self._record("get_totalspace")
        return 2_000_000

    def get_usedspace(self):
        self._record("get_usedspace")
        return 1_000_000

    def get_usedspace_percent(self):
        self._record("get_usedspace_percent")
        return 50.0


class DeviceInfoSoftFailTests(unittest.TestCase):
    def _device(self) -> tuple[PymtpDevice, _FakeMtp]:
        fake = _FakeMtp()
        dev = PymtpDevice.__new__(PymtpDevice)
        dev._mtp = fake  # type: ignore[attr-defined]
        dev.storage_id = 0x00010001
        return dev, fake

    def test_get_identity_skips_battery_and_storage(self) -> None:
        dev, fake = self._device()
        info = dev.get_identity()
        self.assertEqual(info.name, "Gage's Zen")
        self.assertIn("Creative", info.manufacturer)
        self.assertIn("Vision", info.model)
        self.assertIsNone(info.battery)
        self.assertEqual(info.free, 0)
        self.assertNotIn("get_batterylevel", fake.calls)
        self.assertNotIn("get_freespace", fake.calls)
        self.assertIn("get_modelname", fake.calls)

    def test_get_info_tolerates_battery_failure(self) -> None:
        dev, fake = self._device()
        fake.fail.add("get_batterylevel")
        info = dev.get_info()
        self.assertEqual(info.name, "Gage's Zen")
        self.assertEqual(info.model, "ZEN Vision:M")
        self.assertIsNone(info.battery)
        # Storage still attempted and filled when healthy.
        self.assertEqual(info.free, 1_000_000)
        self.assertEqual(info.serial, "SN123")

    def test_get_info_tolerates_storage_failure(self) -> None:
        dev, fake = self._device()
        fake.fail.update(
            {"get_freespace", "get_totalspace", "get_usedspace", "get_usedspace_percent"}
        )
        info = dev.get_info()
        self.assertEqual(info.battery, (100, 80))
        self.assertEqual(info.free, 0)
        self.assertEqual(info.total, 0)
        self.assertEqual(info.used, 0)

    def test_session_alive_uses_modelname_only(self) -> None:
        dev, fake = self._device()
        self.assertTrue(dev.session_alive())
        self.assertEqual(fake.calls, ["get_modelname"])
        fake.fail.add("get_modelname")
        self.assertFalse(dev.session_alive())


if __name__ == "__main__":
    unittest.main()
