"""Unit tests: device identity vs full info soft-fail (no hardware)."""

from __future__ import annotations

import ctypes
import unittest
from unittest.mock import MagicMock, patch

from mtpmanager.infra.pymtp_device import PymtpDevice


class _FakeLib:
    """Stand-in for pymtp.MTP.mtp (ctypes libmtp bindings)."""

    def __init__(self) -> None:
        self.storage_ret = 0
        self.storage_calls = 0

    def LIBMTP_Get_Storage(self, _addr, _sortby):
        self.storage_calls += 1
        return self.storage_ret


class _FakeMtp:
    """Minimal pymtp.MTP stand-in with controllable failures."""

    def __init__(self) -> None:
        # Non-NULL ctypes-like pointer so _device_ptr can resolve an address.
        self.device = ctypes.c_void_p(0x1000)
        self.fail: set[str] = set()
        self.calls: list[str] = []
        self.mtp = _FakeLib()

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

    def disconnect(self):
        self._record("disconnect")
        if "disconnect" in self.fail:
            # Simulate Release_Device error after unplug; leave stale pointer.
            raise RuntimeError("Could not close session")
        self.device = None


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
        self.assertEqual(info.serial, "SN123")  # serial is cheap + multi-device key
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

    def test_session_alive_uses_get_storage_not_modelname(self) -> None:
        """Liveness must hit USB (Get_Storage); modelname is cached-only."""
        dev, fake = self._device()
        with patch(
            "mtpmanager.infra.pymtp_wrapper._device_ptr",
            return_value=0x1000,
        ):
            self.assertTrue(dev.session_alive())
        self.assertEqual(fake.mtp.storage_calls, 1)
        self.assertNotIn("get_modelname", fake.calls)
        self.assertNotIn("get_batterylevel", fake.calls)

    def test_session_alive_false_when_get_storage_fails(self) -> None:
        """Physical unplug: Get_Storage returns -1 → dead session."""
        dev, fake = self._device()
        fake.mtp.storage_ret = -1
        with patch(
            "mtpmanager.infra.pymtp_wrapper._device_ptr",
            return_value=0x1000,
        ):
            self.assertFalse(dev.session_alive())
        self.assertEqual(fake.mtp.storage_calls, 1)
        # Cached modelname must not be consulted as a substitute.
        self.assertNotIn("get_modelname", fake.calls)

    def test_session_alive_false_when_not_connected(self) -> None:
        dev, fake = self._device()
        fake.device = None
        self.assertFalse(dev.session_alive())
        self.assertEqual(fake.mtp.storage_calls, 0)

    def test_session_alive_fallback_without_get_storage(self) -> None:
        dev, fake = self._device()
        fake.mtp = object()  # no LIBMTP_Get_Storage
        with patch(
            "mtpmanager.infra.pymtp_wrapper._device_ptr",
            return_value=0x1000,
        ):
            self.assertTrue(dev.session_alive())
        self.assertIn("get_devicename", fake.calls)
        self.assertNotIn("get_modelname", fake.calls)

    def test_disconnect_clears_pointer_even_on_error(self) -> None:
        """Stale pointer after failed Release would block auto-reconnect."""
        dev, fake = self._device()
        fake.fail.add("disconnect")
        self.assertTrue(dev.is_connected())
        dev.disconnect()
        self.assertFalse(dev.is_connected())
        self.assertIsNone(fake.device)


if __name__ == "__main__":
    unittest.main()
