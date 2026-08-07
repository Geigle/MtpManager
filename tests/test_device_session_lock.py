"""Cross-process device session lock unit tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.device_session_lock import (
    DeviceSessionBusy,
    DeviceSessionLock,
    inspect_lock,
)


class DeviceSessionLockTests(unittest.TestCase):
    def test_acquire_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            lock = DeviceSessionLock(data_dir=data)
            self.assertTrue(lock.try_acquire("cli"))
            self.assertTrue(lock.owned)
            info = inspect_lock(data_dir=data)
            self.assertTrue(info.held)
            self.assertEqual(info.holder, "cli")
            self.assertEqual(info.pid, os.getpid())
            self.assertTrue(lock.release("cli"))
            self.assertFalse(lock.owned)
            info2 = inspect_lock(data_dir=data)
            self.assertFalse(info2.held)

    def test_second_instance_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            a = DeviceSessionLock(data_dir=data)
            b = DeviceSessionLock(data_dir=data)
            self.assertTrue(a.try_acquire("gui"))
            self.assertFalse(b.try_acquire("cli"))
            with self.assertRaises(DeviceSessionBusy):
                b.acquire("cli")
            a.release("gui")
            self.assertTrue(b.try_acquire("cli"))
            b.release("cli")

    def test_stale_pid_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            path = data / "device_session.lock"
            path.write_text(
                json.dumps({"pid": 999_999_999, "holder": "dead", "started_at": 0}),
                encoding="utf-8",
            )
            info = inspect_lock(data_dir=data)
            self.assertFalse(info.held)
            self.assertTrue(info.stale)
            lock = DeviceSessionLock(data_dir=data)
            self.assertTrue(lock.try_acquire("cli"))
            lock.release("cli")


if __name__ == "__main__":
    unittest.main()
