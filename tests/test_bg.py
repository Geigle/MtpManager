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

    def test_concurrent_jobs_both_complete(self) -> None:
        """Library restore + device seed must both finish (no stale discard)."""
        results: list[str] = []
        release_slow = threading.Event()
        lock = threading.Lock()

        def slow() -> str:
            release_slow.wait(timeout=2.0)
            return "slow"

        def fast() -> str:
            return "fast"

        def record(value: str) -> None:
            with lock:
                results.append(value)

        self.runner.submit(
            slow,
            on_done=record,
            on_error=lambda e: self.fail(str(e)),
            name="library-restore",
        )
        self.runner.submit(
            fast,
            on_done=record,
            on_error=lambda e: self.fail(str(e)),
            name="device-index-seed",
        )
        # Fast may finish first; release slow so both complete.
        self._pump_until(lambda: "fast" in results, timeout=2.0)
        release_slow.set()
        self._pump_until(
            lambda: set(results) == {"slow", "fast"} and not self.runner.busy,
            timeout=2.0,
        )
        self.assertEqual(set(results), {"slow", "fast"})

    def test_worker_generation_is_job_local(self) -> None:
        """Worker threads see their own gen even when another job is submitted."""
        release_first = threading.Event()
        seen: dict[str, int] = {}
        lock = threading.Lock()

        def first() -> str:
            with lock:
                seen["first"] = self.runner.generation
            release_first.wait(timeout=2.0)
            with lock:
                seen["first_after"] = self.runner.generation
            return "first"

        def second() -> str:
            with lock:
                seen["second"] = self.runner.generation
            return "second"

        gen1 = self.runner.submit(
            first,
            on_done=lambda _: None,
            on_error=lambda e: self.fail(str(e)),
        )
        # Wait until first has recorded its gen, then start second.
        self._pump_until(lambda: "first" in seen, timeout=2.0)
        gen2 = self.runner.submit(
            second,
            on_done=lambda _: None,
            on_error=lambda e: self.fail(str(e)),
        )
        self._pump_until(lambda: "second" in seen, timeout=2.0)
        release_first.set()
        self._pump_until(lambda: not self.runner.busy, timeout=2.0)

        self.assertEqual(seen["first"], gen1)
        self.assertEqual(seen["first_after"], gen1)
        self.assertEqual(seen["second"], gen2)
        self.assertNotEqual(gen1, gen2)

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

    def test_progress_routed_to_owning_job(self) -> None:
        """Progress for job A must not invoke job B's progress handler."""
        a_progress: list[Any] = []
        b_progress: list[Any] = []
        release_a = threading.Event()
        done: list[str] = []

        def work_a() -> str:
            gen = self.runner.generation
            report = self.runner.progress_callback(gen)
            report("from-a")
            release_a.wait(timeout=2.0)
            return "a"

        def work_b() -> str:
            gen = self.runner.generation
            report = self.runner.progress_callback(gen)
            report("from-b")
            return "b"

        self.runner.submit(
            work_a,
            on_done=done.append,
            on_error=lambda e: self.fail(str(e)),
            on_progress=lambda *a: a_progress.append(a),
            name="job-a",
        )
        self.runner.submit(
            work_b,
            on_done=done.append,
            on_error=lambda e: self.fail(str(e)),
            on_progress=lambda *a: b_progress.append(a),
            name="job-b",
        )
        self._pump_until(lambda: "b" in done and bool(b_progress), timeout=2.0)
        release_a.set()
        self._pump_until(lambda: set(done) == {"a", "b"} and not self.runner.busy)

        self.assertEqual(a_progress, [("from-a",)])
        self.assertEqual(b_progress, [("from-b",)])


if __name__ == "__main__":
    unittest.main()
