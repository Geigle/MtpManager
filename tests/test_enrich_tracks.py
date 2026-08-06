"""Unit tests for on-demand track tag enrichment (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.app.device_ops import (
    enrich_track_refs,
    enrich_track_refs_with_embedded_fallback,
)
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef, TrackMetadata
from mtpmanager.ports.transport import TransportError


class _FakeDevice:
    def __init__(
        self,
        meta: dict[int, DeviceTrackInfo] | None = None,
        *,
        fatal_at: int | None = None,
        miss: set[int] | None = None,
        embedded: dict[int, TrackMetadata] | None = None,
    ) -> None:
        self._meta = dict(meta or {})
        self._fatal_at = fatal_at
        self._miss = set(miss or ())
        self._embedded = dict(embedded or {})
        self.calls: list[int] = []
        self.download_calls: list[int] = []

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

    def get_file_to_file(self, object_id: int, dest: str) -> None:
        oid = int(object_id)
        self.download_calls.append(oid)
        meta = self._embedded.get(oid)
        if meta is None:
            raise TransportError(f"download miss {oid}", fatal=False)
        # Minimal ID3-ish file not needed; we patch read_metadata in the test.
        with open(dest, "wb") as fh:
            fh.write(b"fake")
        # Stash path→meta for the patched reader via attribute on dest parent.
        setattr(self, f"_meta_for_{oid}", meta)


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
        # id 3 never called after fatal on 2
        self.assertEqual(dev.calls, [1, 2])

    def test_embedded_fallback_on_placeholder(self) -> None:
        refs = [_ref(1, "ghost.mp3"), _ref(2)]
        # Device returns placeholder / missing; file tags recover id=1.
        placeholder = DeviceTrackInfo(
            item_id=1,
            name="ghost.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
            album="Unknown Album",
            filetype=2,
        )
        good = _info(2, "Two", "B")
        embedded = TrackMetadata(
            artist="File Artist",
            album="File Album",
            title="File Title",
        )
        dev = _FakeDevice(
            {1: placeholder, 2: good},
            embedded={1: embedded},
        )

        import mtpmanager.app.device_ops as ops

        real_read = ops.read_metadata

        def fake_read(path: str):
            # Map download path prefix back to oid via open file content.
            if path and "mtpmanager_meta_1_" in path:
                return embedded
            return real_read(path)

        ops.read_metadata = fake_read  # type: ignore[assignment]
        try:
            result = enrich_track_refs_with_embedded_fallback(dev, refs)
        finally:
            ops.read_metadata = real_read  # type: ignore[assignment]

        self.assertEqual(result.updated, 2)
        self.assertEqual(result.from_device, 1)
        self.assertEqual(result.from_embedded, 1)
        self.assertEqual(result.refs[0].title, "File Title")
        self.assertEqual(result.refs[0].artist, "File Artist")
        self.assertEqual(result.refs[1].title, "Two")
        self.assertEqual(dev.download_calls, [1])

    def test_empty(self) -> None:
        dev = _FakeDevice()
        result = enrich_track_refs(dev, [])
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.refs, [])
        self.assertEqual(dev.calls, [])


if __name__ == "__main__":
    unittest.main()
