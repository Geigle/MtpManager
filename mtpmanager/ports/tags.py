from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.models import TrackMetadata


class TagReader(Protocol):
    def read_metadata(self, path: str) -> TrackMetadata: ...
