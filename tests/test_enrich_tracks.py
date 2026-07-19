"""Unit tests for on-demand track tag enrichment (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.app.device_ops import enrich_track_refs
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef
from mtpmanager.ports.transport import TransportError


class _FakeDevice:
    def __init__(
        self,
        meta: dict[int, DeviceTrackInfo] | None = None,
        *,
        fatal_at: int | None = None,
        miss: set[int] | None = None,
    ) -> None:
        self._meta = dict(meta or {})
        self._fatal_at = fatal_at
        self._miss = set(miss or ())
        self.calls: list[int] = []

    def get_track_metadata(self, object_id: int) -> DeviceTrackInfo:
        oid = int(object_id)
        self.calls.append(oid)
        if self._fatal_at is not None and oid == self._fatal_at:
            raise TransportError(f"fatal at {oid}", fatal=True)
        if oid in self._miss:
            raise TransportError(f"miss {oid}", fatal=False)
        if oid not in self._meta:
            raise TransportError(f"missing {oid}", fatal=False)
        return self._meta[oid]


def _ref(oid: int, name: str = "") -> DeviceTrackRef:
    return DeviceTrackRef(
        item_id=oid,
        name=name or f"{oid}.mp3",
        title="",
        artist="",
        filetype=2,
    )


def _info(oid: int, title: str, artist: str) -> DeviceTrackInfo:
    return DeviceTrackInfo(
        item_id=oid,
        name=f"{oid}.mp3",
        title=title,
        artist=artist,
        filetype=2,
    )


class EnrichTrackRefsTests(unittest.TestCase):
    def test_updates_selection(self) -> None:
        refs = [_ref(1), _ref(2)]
        dev = _FakeDevice(
            {
                1: _info(1, "One", "A"),
                2: _info(2, "Two", "B"),
            }
        )
        progress: list[tuple[int, int, str]] = []

        def on_progress(done: int, total: int, message: str) -> None:
            progress.append((done, total, message))

        result = enrich_track_refs(dev, refs, on_progress=on_progress)
        self.assertEqual(result.updated, 2)
        self.assertEqual(result.failed, 0)
        self.assertFalse(result.aborted)
        self.assertEqual(result.refs[0].title, "One")
        self.assertEqual(result.refs[0].artist, "A")
        self.assertEqual(result.refs[1].title, "Two")
        self.assertEqual(dev.calls, [1, 2])
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], 2)

    def test_soft_fail_keeps_original(self) -> None:
        refs = [_ref(1), _ref(2)]
        dev = _FakeDevice({2: _info(2, "Two", "B")}, miss={1})
        result = enrich_track_refs(dev, refs)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.aborted)
        self.assertEqual(result.refs[0].title, "")
        self.assertEqual(result.refs[1].title, "Two")

    def test_fatal_aborts_remaining(self) -> None:
        refs = [_ref(1), _ref(2), _ref(3)]
        dev = _FakeDevice(
            {1: _info(1, "One", "A"), 3: _info(3, "Three", "C")},
            fatal_at=2,
        )
        result = enrich_track_refs(dev, refs)
        self.assertTrue(result.aborted)
        self.assertEqual(result.failed_id, 2)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.failed, 1)
        # id=3 never called after fatal at 2
        self.assertEqual(dev.calls, [1, 2])
        self.assertEqual(result.refs[0].title, "One")
        self.assertEqual(result.refs[1].title, "")
        self.assertEqual(result.refs[2].title, "")

    def test_empty(self) -> None:
        dev = _FakeDevice()
        result = enrich_track_refs(dev, [])
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.refs, [])
        self.assertEqual(dev.calls, [])


if __name__ == "__main__":
    unittest.main()
