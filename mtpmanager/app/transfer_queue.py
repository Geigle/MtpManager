"""Thread-safe track queue for an active batch transfer.

The worker drains by index while the UI may append more tracks (artist/album/
selection) without restarting the job.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from mtpmanager.domain.models import Track


class BatchTransferQueue:
    """Ordered transfer plan that can grow while a worker is draining it.

    Identity for dedupe is absolute source *path*. Already-queued or completed
    paths are not added again.
    """

    def __init__(self, tracks: Sequence[Track] | None = None) -> None:
        self._lock = threading.RLock()
        self._tracks: list[Track] = []
        self._paths: set[str] = set()
        if tracks:
            self.extend(tracks)

    def extend(self, tracks: Sequence[Track]) -> list[Track]:
        """Append unique-by-path tracks. Returns the tracks actually added."""
        added: list[Track] = []
        with self._lock:
            for t in tracks:
                path = (t.path or "").strip()
                if not path or path in self._paths:
                    continue
                self._tracks.append(t)
                self._paths.add(path)
                added.append(t)
        return added

    def track_at(self, index: int) -> Track | None:
        with self._lock:
            if 0 <= index < len(self._tracks):
                return self._tracks[index]
            return None

    def total(self) -> int:
        with self._lock:
            return len(self._tracks)

    def paths(self) -> list[str]:
        with self._lock:
            return [t.path for t in self._tracks]

    def snapshot(self) -> list[Track]:
        with self._lock:
            return list(self._tracks)

    def __len__(self) -> int:
        return self.total()
