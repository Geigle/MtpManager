from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.models import TrackMetadata


class TransportError(Exception):
    """Raised when a track send fails.

    *fatal* means the MTP/USB session or device is likely unusable for further
    transfers in this batch (I/O dead, storage unusable, hang/timeout, etc.).
    Callers should abort remaining tracks when fatal is True.
    """

    def __init__(
        self,
        message: str,
        *,
        fatal: bool = True,
        path: str = "",
        stderr: str = "",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.fatal = fatal
        self.path = path
        self.stderr = stderr
        self.returncode = returncode


class Transport(Protocol):
    """Sends a local audio file to the device with metadata."""

    def send_track(
        self,
        path: str,
        meta: TrackMetadata,
        *,
        parent_id: int | None = None,
        guid: str | None = None,
        preferred_basename: str | None = None,
    ) -> int | None:
        """Send *path* with *meta*.

        *parent_id*, when set, is the MTP folder object id for the parent
        (e.g. Music=100). GUID mode forces Music and ignores nested parents.
        *guid*, when set, is the 32-char host track id used as ObjectFileName.
        *preferred_basename*, when set (and no GUID), is the ObjectFileName
        (retail restore of original demo names).

        Returns the new MTP object id when the transport knows it (PyMTP),
        else ``None`` (e.g. mtp-sendtr).
        """
        ...
