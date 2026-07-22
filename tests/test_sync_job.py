"""Unit tests for durable sync job progress / resume."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.sync_job import (
    load_sync_job,
    new_sync_job,
    save_sync_job,
)


class SyncJobTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sync_job.json"
            job = new_sync_job(
                paths=["/a.flac", "/b.flac", "/c.flac"],
                kind="entire_library",
                label="Entire library",
                target_format="mp3",
                mode="stable",
            )
            self.assertTrue(job.mark_path_done("/a.flac"))
            save_sync_job(job, path=dest)
            loaded = load_sync_job(path=dest)
            assert loaded is not None
            self.assertEqual(loaded.paths, ["/a.flac", "/b.flac", "/c.flac"])
            self.assertEqual(loaded.next_index, 1)
            self.assertEqual(loaded.kind, "entire_library")
            self.assertEqual(loaded.target_format, "mp3")
            self.assertTrue(loaded.is_resumable())
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["next_index"], 1)

    def test_mark_done_sequential(self) -> None:
        job = new_sync_job(paths=["a", "b", "c"])
        self.assertTrue(job.mark_path_done("a"))
        self.assertEqual(job.next_index, 1)
        self.assertFalse(job.mark_path_done("c"))  # out of order
        self.assertEqual(job.next_index, 1)
        self.assertTrue(job.mark_path_done("b"))
        self.assertEqual(job.next_index, 2)

    def test_mark_failed_does_not_skip_ahead(self) -> None:
        job = new_sync_job(paths=["a", "b", "c"])
        job.mark_path_done("a")
        # Dual-slot prep for "c" reports failed while head is still "b".
        job.mark_path_failed("c", "cancel prep")
        self.assertEqual(job.next_index, 1)
        self.assertEqual(job.status, "failed")
        # Real failure at current head.
        job.mark_path_failed("b", "boom")
        self.assertEqual(job.next_index, 1)
        self.assertEqual(job.last_failed_path, "b")
        self.assertEqual(job.remaining_paths(), ["b", "c"])

    def test_completed_not_resumable(self) -> None:
        job = new_sync_job(paths=["a", "b"])
        job.mark_path_done("a")
        job.mark_path_done("b")
        job.mark_completed()
        self.assertFalse(job.is_resumable())
        self.assertEqual(job.remaining, 0)

    def test_cancelled_resumable(self) -> None:
        job = new_sync_job(paths=["a", "b", "c"])
        job.mark_path_done("a")
        job.mark_cancelled()
        self.assertTrue(job.is_resumable())
        self.assertEqual(job.remaining_paths(), ["b", "c"])

    def test_missing_file_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nope.json"
            self.assertIsNone(load_sync_job(path=dest))

    def test_append_paths_dedupes(self) -> None:
        job = new_sync_job(paths=["a", "b"])
        job.mark_path_done("a")
        added = job.append_paths(["b", "c", "c", "d"])
        self.assertEqual(added, ["c", "d"])
        self.assertEqual(job.paths, ["a", "b", "c", "d"])
        self.assertEqual(job.next_index, 1)
        self.assertEqual(job.remaining, 3)


if __name__ == "__main__":
    unittest.main()
