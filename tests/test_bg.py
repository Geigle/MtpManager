"""Unit tests for Tk background job runner."""

from __future__ import annotations

import threading
import time
import unittest
from tkinter import Tk

from mtpmanager.ui.bg import TkBackgroundRunner


class TkBackgroundRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Tk()
        self.root.withdraw()
        self.runner = TkBackgroundRunner(self.root, poll_ms=20)

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

    def _pump_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.02)
        self.fail("timed out waiting for background job")

    def test_done_callback_on_main_thread(self) -> None:
        done: list[int] = []

        def work() -> int:
            return 42

        self.runner.submit(
            work,
            on_done=done.append,
            on_error=lambda e: self.fail(str(e)),
        )
        self._pump_until(lambda: bool(done))
        self.assertEqual(done, [42])
        self.assertFalse(self.runner.busy)

    def test_error_callback(self) -> None:
        errors: list[BaseException] = []

        def work() -> None:
            raise RuntimeError("boom")

        self.runner.submit(
            work,
            on_done=lambda _: self.fail("should not succeed"),
            on_error=errors.append,
        )
        self._pump_until(lambda: bool(errors))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_stale_result_discarded(self) -> None:
        results: list[str] = []
        release_slow = threading.Event()

        def slow() -> str:
            release_slow.wait(timeout=2.0)
            return "slow"

        def fast() -> str:
            return "fast"

        self.runner.submit(
            slow,
            on_done=results.append,
            on_error=lambda e: self.fail(str(e)),
        )
        self.runner.submit(
            fast,
            on_done=results.append,
            on_error=lambda e: self.fail(str(e)),
        )
        release_slow.set()
        self._pump_until(lambda: "fast" in results and not self.runner.busy)
        self.assertEqual(results, ["fast"])


if __name__ == "__main__":
    unittest.main()
