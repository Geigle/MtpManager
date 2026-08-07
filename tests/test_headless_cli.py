"""Headless service + CLI tests (no live device)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from mtpmanager.cli.main import main
from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import new_track_guid
from mtpmanager.headless.dto import ExitCode
from mtpmanager.headless.service import HeadlessService
from mtpmanager.infra.library_index import save_library_index


def _track(path: str, **meta_kw) -> Track:
    defaults = dict(
        artist="Nightwish",
        album="Once",
        title="Nemo",
        tracknumber="04",
        length_sec=260.0,
    )
    defaults.update(meta_kw)
    guid = defaults.pop("guid", "") or new_track_guid()
    return Track(path=path, meta=TrackMetadata(**defaults), guid=guid)


class HeadlessServiceTests(unittest.TestCase):
    def test_doctor_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "nemo.flac"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g, title="Nemo", artist="Nightwish")],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")

            svc = HeadlessService(data_dir=data)
            doc = svc.agent_doctor()
            self.assertTrue(doc.ok)
            self.assertEqual(doc.data["track_count"], 1)
            self.assertEqual(doc.data["library_roots"], [str(root)])

            roots = svc.library_list_roots()
            self.assertTrue(roots.ok)
            self.assertEqual(roots.data["roots"], [str(root)])

            search = svc.library_search("artist:nightwish")
            self.assertTrue(search.ok)
            self.assertGreaterEqual(search.data["total_matched"], 1)
            self.assertEqual(search.data["tracks"][0]["guid"], g)

            track = svc.library_track(guid=g)
            self.assertTrue(track.ok)
            self.assertEqual(track.data["track"]["title"], "Nemo")

            tools = svc.agent_tools()
            self.assertTrue(tools.ok)
            self.assertGreater(tools.data["count"], 5)

            cfg = svc.config_get()
            self.assertTrue(cfg.ok)
            self.assertIn("config", cfg.data)

            status = svc.device_status()
            self.assertTrue(status.ok)
            self.assertFalse(status.data["connected"])

    def test_sync_dry_run_and_confirm_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "a.mp3"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g)],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")
            svc = HeadlessService(data_dir=data)

            plan = svc.sync_tracks(guids=[g], dry_run=True)
            self.assertTrue(plan.ok)
            self.assertEqual(plan.data["track_count"], 1)
            self.assertEqual(plan.data["tracks"][0]["action"], "send")

            gated = svc.sync_tracks(guids=[g], confirm=False, dry_run=False)
            self.assertFalse(gated.ok)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))

    def test_device_busy_on_connect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            holder = HeadlessService(data_dir=data)
            # Simulate GUI holding lock without real USB.
            self.assertTrue(holder._session_lock.try_acquire("gui"))
            other = HeadlessService(data_dir=data)
            # Avoid real pymtp connect: mock after lock check by pre-failing lock.
            r = other.device_connect()
            self.assertFalse(r.ok)
            self.assertEqual(r.code, "DEVICE_BUSY")
            self.assertEqual(r.exit_code, int(ExitCode.DEVICE_BUSY))
            holder._session_lock.release("gui")


class CliMainTests(unittest.TestCase):
    def test_cli_doctor_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--data-dir", str(data), "agent", "doctor"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["data_dir"], str(data))

    def test_cli_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "x.flac"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g, title="Countdown")],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    ["--data-dir", str(data), "library", "search", "countdown"]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertGreaterEqual(payload["data"]["total_matched"], 1)


if __name__ == "__main__":
    unittest.main()
