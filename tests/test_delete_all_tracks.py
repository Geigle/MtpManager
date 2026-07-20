"""Unit tests for Device → Delete All Tracks batch use case (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.app.device_ops import delete_all_tracks
from mtpmanager.domain.models import DeviceTrackRef
from mtpmanager.ports.transport import TransportError


class _FakeDevice:
    def __init__(self, tracks: list[DeviceTrackRef], *, fail_at: int | None = None) -> None:
        self._tracks = list(tracks)
        self.deleted: list[int] = []
        self._fail_at = fail_at

    def list_tracks(self) -> list[DeviceTrackRef]:
        return list(self._tracks)

    def delete_object(self, object_id: int) -> None:
        oid = int(object_id)
        if self._fail_at is not None and len(self.deleted) == self._fail_at:
            raise TransportError(f"boom at {oid}", fatal=True)
        self.deleted.append(oid)


def _ref(oid: int, name: str = "") -> DeviceTrackRef:
    return DeviceTrackRef(item_id=oid, name=name or f"{oid}.mp3", title=f"T{oid}")


class DeleteAllTracksTests(unittest.TestCase):
    def test_deletes_all_in_order(self) -> None:
        tracks = [_ref(10), _ref(20), _ref(30)]
        dev = _FakeDevice(tracks)
        progress: list[tuple[int, int, int | None]] = []

        def on_progress(done: int, total: int, current) -> None:
            cid = current.item_id if current is not None else None
            progress.append((done, total, cid))

        result = delete_all_tracks(dev, tracks, on_progress=on_progress)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.deleted, 3)
        self.assertFalse(result.aborted)
        self.assertIsNone(result.failed_id)
        self.assertEqual(dev.deleted, [10, 20, 30])
        self.assertEqual(progress[-1], (3, 3, None))

    def test_aborts_on_fatal(self) -> None:
        tracks = [_ref(1), _ref(2), _ref(3)]
        dev = _FakeDevice(tracks, fail_at=1)
        result = delete_all_tracks(dev, tracks)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.total, 3)
        self.assertTrue(result.aborted)
        self.assertEqual(result.failed_id, 2)
        self.assertEqual(dev.deleted, [1])

    def test_skips_duplicate_and_invalid_ids(self) -> None:
        tracks = [_ref(5), _ref(0), _ref(5), _ref(6)]
        dev = _FakeDevice(tracks)
        result = delete_all_tracks(dev, tracks)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.deleted, 2)
        self.assertEqual(dev.deleted, [5, 6])

    def test_lists_when_tracks_not_provided(self) -> None:
        tracks = [_ref(9)]
        dev = _FakeDevice(tracks)
        result = delete_all_tracks(dev)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(dev.deleted, [9])

    def test_empty(self) -> None:
        dev = _FakeDevice([])
        result = delete_all_tracks(dev, [])
        self.assertEqual(result.total, 0)
        self.assertEqual(result.deleted, 0)
        self.assertFalse(result.aborted)

    def test_cancel_stops_remaining(self) -> None:
        tracks = [_ref(1), _ref(2), _ref(3)]
        dev = _FakeDevice(tracks)
        cancel = {"flag": False}

        def should_cancel() -> bool:
            # Cancel after first delete completes (checked before each item).
            return cancel["flag"]

        class _CancelAfterOne(_FakeDevice):
            def delete_object(self, object_id: int) -> None:
                super().delete_object(object_id)
                cancel["flag"] = True

        dev = _CancelAfterOne(tracks)
        result = delete_all_tracks(dev, tracks, should_cancel=should_cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.aborted)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.total, 3)
        self.assertEqual(dev.deleted, [1])


if __name__ == "__main__":
    unittest.main()
