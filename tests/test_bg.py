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

    def test_progress_callback_on_main_thread(self) -> None:
        seen: list[tuple] = []
        main = threading.get_ident()
        started = threading.Event()

        def work() -> str:
            started.wait(timeout=2.0)
            gen = self.runner.generation
            report = self.runner.progress_callback(gen)
            report("status", "scanning 10/100")
            report("progress", 10, 100, "scanning 10/100")
            # Give the main-thread poll a chance to drain progress before done.
            time.sleep(0.05)
            return "ok"

        def on_progress(*args) -> None:
            seen.append((threading.get_ident(), args))

        done: list[str] = []
        self.runner.submit(
            work,
            on_done=done.append,
            on_error=lambda e: self.fail(str(e)),
            on_progress=on_progress,
        )
        started.set()
        self._pump_until(lambda: bool(done), timeout=3.0)
        # Progress may arrive in the same poll burst as done; drain once more.
        self.root.update()
        self.assertEqual(done, ["ok"])
        self.assertGreaterEqual(len(seen), 2)
        self.assertTrue(all(tid == main for tid, _ in seen))
        kinds = [args[0] for _, args in seen]
        self.assertIn("status", kinds)
        self.assertIn("progress", kinds)


if __name__ == "__main__":
    unittest.main()
