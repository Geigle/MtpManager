from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.models import TrackMetadata


class Transport(Protocol):
    """Sends a local audio file to the device with metadata."""

    def send_track(self, path: str, meta: TrackMetadata) -> None: ...
