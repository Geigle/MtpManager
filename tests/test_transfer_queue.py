"""Unit tests for BatchTransferQueue."""

from __future__ import annotations

import unittest

from mtpmanager.app.transfer_queue import BatchTransferQueue
from mtpmanager.domain.models import Track, TrackMetadata


def _t(path: str, title: str = "T") -> Track:
    return Track(path=path, meta=TrackMetadata(title=title))


class BatchTransferQueueTests(unittest.TestCase):
    def test_extend_dedupes_by_path(self) -> None:
        q = BatchTransferQueue([_t("/a"), _t("/b")])
        added = q.extend([_t("/b"), _t("/c"), _t("/a"), _t("/d")])
        self.assertEqual([t.path for t in added], ["/c", "/d"])
        self.assertEqual(q.total(), 4)
        self.assertEqual(q.paths(), ["/a", "/b", "/c", "/d"])

    def test_track_at(self) -> None:
        q = BatchTransferQueue([_t("/a"), _t("/b")])
        self.assertEqual(q.track_at(0).path, "/a")  # type: ignore[union-attr]
        self.assertEqual(q.track_at(1).path, "/b")  # type: ignore[union-attr]
        self.assertIsNone(q.track_at(2))
        self.assertIsNone(q.track_at(-1))


if __name__ == "__main__":
    unittest.main()
