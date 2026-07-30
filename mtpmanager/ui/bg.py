"""Run blocking work off the Tk main thread; deliver results via root.after."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Payload: (generation, "done"|"error"|"progress", result_or_exc_or_args)
_QueueItem = tuple[int, str, Any]


@dataclass
class _JobHandlers:
    on_done: Callable[[Any], None]
    on_error: Callable[[BaseException], None]
    on_progress: Callable[..., None] | None
    name: str


class TkBackgroundRunner:
    """Background jobs for a Tk root — concurrent-safe.

    Workers must not touch Tk widgets. Results are applied on the main thread
    via a short ``after`` poll loop.

    Multiple jobs may run at once (e.g. library index restore + device index
    seed). Each job keeps its own callbacks; completing job A never discards
    job B. Older single-flight “latest gen wins” behavior was unsafe: a device
    seed would orphan a library restore and leave ``_library_busy`` stuck.

    Optional *on_progress* receives args from :meth:`progress_callback` for that
    job’s generation (also main-thread only).

    Inside a worker thread, :attr:`generation` returns **that job’s** gen (via
    thread-local), so existing ``gen = self._bg.generation`` call sites stay
    correct under concurrency.
    """

    def __init__(self, root, *, poll_ms: int = 50) -> None:
        self._root = root
        self._poll_ms = poll_ms
        self._q: queue.Queue[_QueueItem] = queue.Queue()
        self._generation = 0
        self._jobs: dict[int, _JobHandlers] = {}
        self._jobs_lock = threading.Lock()
        self._inflight = 0
        self._poll_scheduled = False
        self._tls = threading.local()

    @property
    def generation(self) -> int:
        """Active job gen on a worker thread; last submitted gen on main."""
        job_gen = getattr(self._tls, "generation", None)
        if job_gen is not None:
            return int(job_gen)
        return self._generation

    @property
    def busy(self) -> bool:
        return self._inflight > 0

    def progress_callback(self, gen: int) -> Callable[..., None]:
        """Return a thread-safe progress reporter for job *gen*.

        Only enqueues; the main-thread poll (kept alive while jobs are inflight)
        drains the queue. Avoids ``root.after`` from worker threads.
        """

        def report(*args: Any) -> None:
            self._q.put((gen, "progress", args))

        return report

    def submit(
        self,
        fn: Callable[[], T],
        *,
        on_done: Callable[[T], None],
        on_error: Callable[[BaseException], None],
        on_progress: Callable[..., None] | None = None,
        name: str = "mtpmanager-bg",
    ) -> int:
        """Start *fn* on a daemon thread. Returns the job generation id.

        Use :meth:`progress_callback` with the returned generation from inside
        *fn* (via :attr:`generation` on the worker, or the return value from the
        main thread after submit returns).
        """
        self._generation += 1
        gen = self._generation
        with self._jobs_lock:
            self._jobs[gen] = _JobHandlers(
                on_done=on_done,  # type: ignore[arg-type]
                on_error=on_error,
                on_progress=on_progress,
                name=name,
            )
        self._inflight += 1

        def worker() -> None:
            self._tls.generation = gen
            try:
                result = fn()
                self._q.put((gen, "done", result))
            except BaseException as exc:
                # JobCancelled is expected UX, not a failure — log quietly.
                from mtpmanager.app.cancellation import JobCancelled

                if isinstance(exc, JobCancelled):
                    logger.info(
                        "Background job cancelled (gen=%s name=%s): %s",
                        gen,
                        name,
                        exc,
                    )
                else:
                    logger.exception(
                        "Background job failed (gen=%s name=%s)", gen, name
                    )
                self._q.put((gen, "error", exc))
            finally:
                self._tls.generation = None
            # Do not call root.after from the worker — Tk is not thread-safe.
            # The main-thread poll reschedules itself while _inflight > 0.

        threading.Thread(target=worker, name=f"{name}-{gen}", daemon=True).start()
        self._ensure_poll()
        return gen

    def _ensure_poll(self) -> None:
        if self._poll_scheduled:
            return
        self._poll_scheduled = True
        try:
            self._root.after(self._poll_ms, self._poll)
        except Exception:
            self._poll_scheduled = False

    def _pop_job(self, gen: int) -> _JobHandlers | None:
        with self._jobs_lock:
            return self._jobs.pop(gen, None)

    def _get_job(self, gen: int) -> _JobHandlers | None:
        with self._jobs_lock:
            return self._jobs.get(gen)

    def _poll(self) -> None:
        self._poll_scheduled = False
        while True:
            try:
                gen, kind, payload = self._q.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                job = self._get_job(gen)
                if job is not None and job.on_progress is not None:
                    try:
                        job.on_progress(*payload)
                    except Exception:
                        logger.exception(
                            "Progress callback failed (gen=%s name=%s)",
                            gen,
                            job.name,
                        )
                continue

            self._inflight = max(0, self._inflight - 1)
            job = self._pop_job(gen)
            if job is None:
                logger.debug(
                    "Discarding orphan background result gen=%s (no handlers)",
                    gen,
                )
                continue

            if kind == "done":
                try:
                    job.on_done(payload)
                except Exception:
                    logger.exception(
                        "Done callback failed (gen=%s name=%s)", gen, job.name
                    )
            else:
                try:
                    job.on_error(payload)
                except Exception:
                    logger.exception(
                        "Error callback failed (gen=%s name=%s)", gen, job.name
                    )

        if self._inflight > 0:
            self._ensure_poll()
