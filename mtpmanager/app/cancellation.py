"""Cooperative cancel for long-running transfer / device batch jobs."""

from __future__ import annotations

from collections.abc import Callable

# True when the user has requested cancel (checked between units of work).
CancelCheck = Callable[[], bool]


class JobCancelled(Exception):
    """Raised when a batch job stops because the user cancelled.

    The current unit of work (one send / one delete) is allowed to finish;
    remaining items are not started.
    """

    def __init__(
        self,
        message: str = "Cancelled",
        *,
        completed: int = 0,
        total: int = 0,
    ) -> None:
        super().__init__(message)
        self.completed = int(completed)
        self.total = int(total)


def raise_if_cancelled(
    should_cancel: CancelCheck | None,
    *,
    completed: int = 0,
    total: int = 0,
    message: str = "Cancelled by user",
) -> None:
    """Raise :class:`JobCancelled` when *should_cancel* reports True."""
    if should_cancel is None:
        return
    try:
        cancelled = bool(should_cancel())
    except Exception:
        return
    if cancelled:
        raise JobCancelled(message, completed=completed, total=total)
