"""Unit tests for exclusive MTP/USB I/O gate."""

from __future__ import annotations

import time
import unittest

from mtpmanager.app.device_io_gate import DeviceIoBusy, DeviceIoGate


class DeviceIoGateTests(unittest.TestCase):
    def test_try_acquire_and_release(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        self.assertTrue(g.try_acquire("transfer"))
        self.assertTrue(g.is_held())
        self.assertEqual(g.holder, "transfer")
        self.assertFalse(g.try_acquire("auto-connect"))
        self.assertTrue(g.release(reason="transfer"))
        self.assertFalse(g.is_held())
        self.assertTrue(g.try_acquire("auto-connect"))
        g.release(reason="auto-connect")

    def test_stale_release_does_not_clear_new_holder(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        self.assertTrue(g.try_acquire("seed"))
        g.steal("manual-disconnect")
        self.assertEqual(g.holder, "manual-disconnect")
        # Finished seed must not clear the steal.
        self.assertFalse(g.release(reason="seed"))
        self.assertEqual(g.holder, "manual-disconnect")
        self.assertTrue(g.release(reason="manual-disconnect"))
        self.assertFalse(g.is_held())

    def test_steal_returns_previous_holder(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        g.try_acquire("transfer")
        prev = g.steal("manual-disconnect")
        self.assertEqual(prev, "transfer")
        g.release(reason="manual-disconnect")

    def test_can_auto_probe_false_when_held(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        g.try_acquire("transfer")
        self.assertFalse(g.can_auto_probe())
        g.release(reason="transfer")
        self.assertTrue(g.can_auto_probe())

    def test_quiet_blocks_probe_not_job_acquire(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        g.mark_quiet(0.5)
        self.assertTrue(g.is_quiet())
        self.assertFalse(g.can_auto_probe())
        # Jobs may still acquire during quiet (user-initiated work).
        self.assertTrue(g.try_acquire("transfer"))
        g.release(reason="transfer", quiet_s=0.3)
        self.assertTrue(g.is_quiet())
        self.assertFalse(g.can_auto_probe())

    def test_release_with_quiet_extends_window(self) -> None:
        g = DeviceIoGate(quiet_after_s=12.0)
        g.try_acquire("transfer")
        g.release(reason="transfer", quiet_s=0.2)
        self.assertGreater(g.quiet_remaining_s(), 0.0)
        self.assertFalse(g.can_auto_probe())

    def test_quiet_expires(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        g.mark_quiet(0.05)
        self.assertFalse(g.can_auto_probe())
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and not g.can_auto_probe():
            time.sleep(0.01)
        self.assertTrue(g.can_auto_probe())

    def test_hold_context_manager(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        with g.hold("listing", quiet_after_s=0.1):
            self.assertEqual(g.holder, "listing")
            with self.assertRaises(DeviceIoBusy):
                with g.hold("other"):
                    pass
        self.assertFalse(g.is_held())
        self.assertTrue(g.is_quiet())

    def test_hold_raises_when_busy(self) -> None:
        g = DeviceIoGate(quiet_after_s=0)
        g.try_acquire("a")
        with self.assertRaises(DeviceIoBusy) as ctx:
            with g.hold("b"):
                pass
        self.assertEqual(ctx.exception.holder, "a")
        g.release(reason="a")


if __name__ == "__main__":
    unittest.main()
