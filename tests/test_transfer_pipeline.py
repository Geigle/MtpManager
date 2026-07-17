"""Unit tests for dual-slot prepare/send pipeline (no device / ffmpeg required)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.app.transfer import prepare_track, transfer_tracks
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder, NUM_SLOTS
from mtpmanager.ports.transport import TransportError


class _FakeTranscoder:
    """Records convert slots; writes tiny marker files as temp outputs."""

    def __init__(self, temp_dir: str) -> None:
        self.temp_dir = temp_dir
        self.calls: list[tuple[str, str, int]] = []

    def convert(self, src_path: str, target_format: str, *, slot: int = 0) -> str:
        target_format = target_format.lower().lstrip(".")
        slot = int(slot) % NUM_SLOTS
        out = os.path.join(self.temp_dir, f"TRANSCODE_{slot}.{target_format}")
        self.calls.append((src_path, target_format, slot))
        Path(out).write_text(f"from:{src_path}", encoding="utf-8")
        return out

    def cleanup(self, path: str | None) -> None:
        if path and os.path.isfile(path):
            os.remove(path)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (path, title)
        self.hold_paths: list[str] = []

    def send_track(self, path: str, meta: TrackMetadata) -> None:
        # Prove the file still exists at send time (not clobbered/deleted).
        self.hold_paths.append(path)
        assert os.path.isfile(path), f"missing at send: {path}"
        self.sent.append((path, meta.title))


class _FailSecondTransport(_RecordingTransport):
    def send_track(self, path: str, meta: TrackMetadata) -> None:
        super().send_track(path, meta)
        if len(self.sent) == 2:
            raise TransportError("boom", fatal=True, path=path)


def _track(path: str, title: str) -> Track:
    # .flac so prepare always "converts" via fake transcoder
    return Track(
        path=path,
        meta=TrackMetadata(title=title, artist="A", album="B"),
    )


class DualSlotPipelineTests(unittest.TestCase):
    def test_temp_path_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = FFmpegTranscoder(temp_dir=tmp)
            p0 = t.temp_path("mp3", slot=0)
            p1 = t.temp_path("mp3", slot=1)
            p2 = t.temp_path("mp3", slot=2)
            self.assertNotEqual(p0, p1)
            self.assertEqual(p0, p2)
            self.assertTrue(p0.endswith("TRANSCODE_0.mp3"))
            self.assertTrue(p1.endswith("TRANSCODE_1.mp3"))

    def test_cleanup_matches_slot_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = FFmpegTranscoder(temp_dir=tmp)
            path = t.temp_path("mp3", slot=0)
            Path(path).write_text("x", encoding="utf-8")
            t.cleanup(path)
            self.assertFalse(os.path.exists(path))
            # Must not delete non-temp names
            other = os.path.join(tmp, "song.mp3")
            Path(other).write_text("y", encoding="utf-8")
            t.cleanup(other)
            self.assertTrue(os.path.exists(other))

    def test_prepare_uses_requested_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.flac")
            Path(src).write_bytes(b"x")
            tr = _FakeTranscoder(tmp)
            prep = prepare_track(
                _track(src, "T"),
                target_format="mp3",
                transcoder=tr,
                slot=1,
                reread_tags_after_convert=False,
            )
            self.assertEqual(tr.calls[-1][2], 1)
            self.assertTrue(prep.send_path.endswith("TRANSCODE_1.mp3"))
            self.assertEqual(prep.cleanup_path, prep.send_path)
            tr.cleanup(prep.cleanup_path)

    def test_batch_alternates_slots_and_keeps_file_during_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("a.flac", "b.flac", "c.flac"):
                p = os.path.join(tmp, name)
                Path(p).write_bytes(b"x")
                paths.append(p)
            tracks = [
                _track(paths[0], "One"),
                _track(paths[1], "Two"),
                _track(paths[2], "Three"),
            ]
            tr = _FakeTranscoder(tmp)
            transport = _RecordingTransport()
            n = transfer_tracks(
                tracks,
                target_format="mp3",
                transport=transport,
                transcoder=tr,
                session_log=False,
            )
            self.assertEqual(n, 3)
            self.assertEqual([t for _, t in transport.sent], ["One", "Two", "Three"])
            # Convert order uses slots 0, 1, 0
            slots = [c[2] for c in tr.calls]
            self.assertEqual(slots, [0, 1, 0])
            # After batch, temps should be cleaned up
            self.assertFalse(os.path.exists(os.path.join(tmp, "TRANSCODE_0.mp3")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "TRANSCODE_1.mp3")))

    def test_fatal_aborts_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("a.flac", "b.flac", "c.flac"):
                p = os.path.join(tmp, name)
                Path(p).write_bytes(b"x")
                paths.append(p)
            tracks = [_track(p, f"T{i}") for i, p in enumerate(paths)]
            tr = _FakeTranscoder(tmp)
            transport = _FailSecondTransport()
            with self.assertRaises(TransportError):
                transfer_tracks(
                    tracks,
                    target_format="mp3",
                    transport=transport,
                    transcoder=tr,
                    session_log=False,
                )
            self.assertEqual(len(transport.sent), 2)

    def test_track_status_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("a.flac", "b.flac"):
                p = os.path.join(tmp, name)
                Path(p).write_bytes(b"x")
                paths.append(p)
            tracks = [_track(paths[0], "One"), _track(paths[1], "Two")]
            tr = _FakeTranscoder(tmp)
            transport = _RecordingTransport()
            events: list[tuple[str, str]] = []

            def on_status(path: str, status: str) -> None:
                events.append((os.path.basename(path), status))

            n = transfer_tracks(
                tracks,
                target_format="mp3",
                transport=transport,
                transcoder=tr,
                on_track_status=on_status,
                session_log=False,
            )
            self.assertEqual(n, 2)
            # Each flac: transcoding → transferring → done
            self.assertIn(("a.flac", "transcoding"), events)
            self.assertIn(("a.flac", "transferring"), events)
            self.assertIn(("a.flac", "done"), events)
            self.assertIn(("b.flac", "transcoding"), events)
            self.assertIn(("b.flac", "transferring"), events)
            self.assertIn(("b.flac", "done"), events)
            # transferring before done for first track
            i_xfer = events.index(("a.flac", "transferring"))
            i_done = events.index(("a.flac", "done"))
            self.assertLess(i_xfer, i_done)


if __name__ == "__main__":
    unittest.main()
