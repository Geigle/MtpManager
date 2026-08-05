"""Exclusive ownership of MTP/USB I/O for one device session.

All paths that touch libmtp (transfers, listings, index seed, tag enrich,
embedded meta probes, auto-connect poll, manual connect/disconnect) must
hold this gate. The auto-connect poll uses :meth:`can_auto_probe` +
:meth:`try_acquire` so it never blocks and never races active work.

Job-level UI state (``_transfer_busy``, Cancel button) stays separate —
this gate is only about USB ownership.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator


# Default post-job bus recovery pause (ZEN often flaky for a few seconds
# after long list/send walks). Auto-connect respects this; real jobs do not.
DEFAULT_USB_QUIET_S = 12.0


class DeviceIoBusy(RuntimeError):
    """Raised when exclusive device I/O cannot be acquired."""

    def __init__(self, holder: str | None = None) -> None:
        self.holder = holder
        if holder:
            super().__init__(f"Device I/O busy (held by {holder!r})")
        else:
            super().__init__("Device I/O busy")


class DeviceIoGate:
    """Non-reentrant exclusive lock + optional quiet window for probes."""

    def __init__(self, *, quiet_after_s: float = DEFAULT_USB_QUIET_S) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None
        self._quiet_until = 0.0
        self._default_quiet_s = max(0.0, float(quiet_after_s))

    @property
    def holder(self) -> str | None:
        with self._lock:
            return self._holder

    def is_held(self) -> bool:
        with self._lock:
            return self._holder is not None

    def is_quiet(self) -> bool:
        with self._lock:
            return time.monotonic() < self._quiet_until

    def quiet_remaining_s(self) -> float:
        with self._lock:
            return max(0.0, self._quiet_until - time.monotonic())

    def mark_quiet(self, seconds: float | None = None) -> None:
        """Extend the quiet window (monotonic). Does not release a hold."""
        sec = self._default_quiet_s if seconds is None else max(0.0, float(seconds))
        if sec <= 0:
            return
        until = time.monotonic() + sec
        with self._lock:
            if until > self._quiet_until:
                self._quiet_until = until

    def can_auto_probe(self) -> bool:
        """True when the auto-connect poll may attempt USB I/O."""
        with self._lock:
            if self._holder is not None:
                return False
            return time.monotonic() >= self._quiet_until

    def try_acquire(self, reason: str) -> bool:
        """Non-blocking acquire. Ignores the quiet window (jobs may run)."""
        label = (reason or "device-io").strip() or "device-io"
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = label
            return True

    def steal(self, reason: str) -> str | None:
        """Take ownership even if held (manual disconnect / mode switch).

        Returns the previous holder name, if any. The previous owner should
        treat a later :meth:`release` as a no-op if it no longer owns the gate
        (see :meth:`release`).
        """
        label = (reason or "device-io").strip() or "device-io"
        with self._lock:
            prev = self._holder
            self._holder = label
            return prev

    def release(
        self,
        *,
        reason: str | None = None,
        quiet_s: float | None = None,
    ) -> bool:
        """Release ownership.

        If *reason* is set, only release when the current holder matches
        (avoids a finished job clearing a newer steal/acquire). Returns True
        when the hold was cleared.

        *quiet_s*: when not None, mark quiet for that many seconds after
        release (use for post-job bus recovery). Pass ``0`` to clear quiet
        without extending; omit to leave the quiet window unchanged.
        """
        with self._lock:
            if reason is not None and self._holder is not None and self._holder != reason:
                # Stale release from a previous owner after steal.
                if quiet_s is not None and quiet_s > 0:
                    until = time.monotonic() + float(quiet_s)
                    if until > self._quiet_until:
                        self._quiet_until = until
                return False
            cleared = self._holder is not None
            self._holder = None
            if quiet_s is not None and quiet_s > 0:
                until = time.monotonic() + float(quiet_s)
                if until > self._quiet_until:
                    self._quiet_until = until
            return cleared

    @contextmanager
    def hold(
        self,
        reason: str,
        *,
        quiet_after_s: float | None = None,
        block: bool = False,
    ) -> Iterator[None]:
        """Context manager for exclusive USB work on the calling thread.

        *block* is unused (reserved); acquisition is always non-blocking and
        raises :class:`DeviceIoBusy` on failure. Prefer :meth:`try_acquire` +
        explicit release for jobs that span a background worker.
        """
        del block  # reserved
        if not self.try_acquire(reason):
            raise DeviceIoBusy(self.holder)
        try:
            yield
        finally:
            self.release(reason=reason, quiet_s=quiet_after_s)
