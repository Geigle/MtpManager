"""Run blocking work off the Tk main thread; deliver results via root.after."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Payload: (generation, "done"|"error"|"progress", result_or_exc_or_args)
_QueueItem = tuple[int, str, Any]


class TkBackgroundRunner:
    """Single-flight-friendly background jobs for a Tk root.

    Workers must not touch Tk widgets. Results are applied on the main thread
    via a short ``after`` poll loop. When a newer job is submitted, results
    from older generations are discarded.

    Optional *on_progress* receives args from :meth:`progress_callback` for the
    active generation (also main-thread only).
    """

    def __init__(self, root, *, poll_ms: int = 50) -> None:
        self._root = root
        self._poll_ms = poll_ms
        self._q: queue.Queue[_QueueItem] = queue.Queue()
        self._generation = 0
        self._inflight = 0
        self._poll_scheduled = False
        self._on_done: Callable[[Any], None] | None = None
        self._on_error: Callable[[BaseException], None] | None = None
        self._on_progress: Callable[..., None] | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def busy(self) -> bool:
        return self._inflight > 0

    def progress_callback(self, gen: int) -> Callable[..., None]:
        """Return a thread-safe progress reporter for job *gen*."""

        def report(*args: Any) -> None:
            self._q.put((gen, "progress", args))
            self._ensure_poll()

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
        *fn* (via closure) or capture it before starting work.
        """
        self._generation += 1
        gen = self._generation
        self._on_done = on_done  # type: ignore[assignment]
        self._on_error = on_error
        self._on_progress = on_progress
        self._inflight += 1

        def worker() -> None:
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

    def _poll(self) -> None:
        self._poll_scheduled = False
        while True:
            try:
                gen, kind, payload = self._q.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                if gen == self._generation and self._on_progress is not None:
                    try:
                        self._on_progress(*payload)
                    except Exception:
                        logger.exception("Progress callback failed")
                continue

            self._inflight = max(0, self._inflight - 1)

            if gen != self._generation:
                logger.debug("Discarding stale background result gen=%s", gen)
                continue

            if kind == "done":
                if self._on_done is not None:
                    self._on_done(payload)
            else:
                if self._on_error is not None:
                    self._on_error(payload)

        if self._inflight > 0:
            self._ensure_poll()
