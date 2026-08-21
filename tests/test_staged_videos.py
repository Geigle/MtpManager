"""Unit tests for staged video manifest + encode-for-stage helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from mtpmanager.domain.device_profiles import ZEN_AVI_XVID_MP3
from mtpmanager.infra.staged_videos import (
    STAGED_TTL,
    StagedVideoEntry,
    find_staged_by_source,
    list_syncable_staged,
    load_staged_videos,
    new_staged_path,
    purge_expired_staged_videos,
    remove_staged_entry,
    upsert_staged_entry,
    utc_now_iso,
)


class StagedVideosManifestTests(unittest.TestCase):
    def test_round_trip_and_find_by_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid, dest = new_staged_path(container="avi", data_dir=root)
            Path(dest).write_bytes(b"fake-avi")
            entry = StagedVideoEntry(
                id=sid,
                source_path="/lib/clip.mp4",
                staged_path=dest,
                parent_id=120,
                created_at=utc_now_iso(),
                title="Clip",
                preferred_basename="clip.avi",
                guid="a" * 32,
                encoded=True,
                preset_id="zen_avi_xvid_mp3",
                resolution_id="qvga",
            )
            upsert_staged_entry(entry, data_dir=root)
            loaded = load_staged_videos(data_dir=root)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].preferred_basename, "clip.avi")
            found = find_staged_by_source("/lib/clip.mp4", data_dir=root)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found.id, sid)

            # Upsert same source replaces prior staged file.
            sid2, dest2 = new_staged_path(container="avi", data_dir=root)
            Path(dest2).write_bytes(b"fake-avi-2")
            entry2 = StagedVideoEntry(
                id=sid2,
                source_path="/lib/clip.mp4",
                staged_path=dest2,
                parent_id=124,
                created_at=utc_now_iso(),
                title="Clip",
                preferred_basename="clip.avi",
                encoded=True,
            )
            upsert_staged_entry(entry2, data_dir=root)
            loaded = load_staged_videos(data_dir=root)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].parent_id, 124)
            self.assertFalse(os.path.isfile(dest))
            self.assertTrue(os.path.isfile(dest2))

            removed = remove_staged_entry(sid2, data_dir=root)
            self.assertIsNotNone(removed)
            self.assertEqual(load_staged_videos(data_dir=root), [])
            self.assertFalse(os.path.isfile(dest2))

    def test_purge_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sid, dest = new_staged_path(container="avi", data_dir=root)
            Path(dest).write_bytes(b"old")
            old = (datetime.now(timezone.utc) - STAGED_TTL - timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            upsert_staged_entry(
                StagedVideoEntry(
                    id=sid,
                    source_path="/a.mp4",
                    staged_path=dest,
                    parent_id=120,
                    created_at=old,
                ),
                data_dir=root,
            )
            purged = purge_expired_staged_videos(data_dir=root)
            self.assertEqual(len(purged), 1)
            self.assertEqual(list_syncable_staged(data_dir=root), [])
            self.assertFalse(os.path.isfile(dest))


class StageVideoForSyncTests(unittest.TestCase):
    def test_stage_copies_when_encode_off(self) -> None:
        from mtpmanager.app.device_ops import stage_video_for_sync

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.avi"
            src.write_bytes(b"source-bytes")
            entry = stage_video_for_sync(
                str(src),
                parent_id=120,
                encode_for_device=False,
                title="Source",
                preferred_basename="source.avi",
                data_dir=root,
            )
            self.assertTrue(os.path.isfile(entry.staged_path))
            self.assertEqual(Path(entry.staged_path).read_bytes(), b"source-bytes")
            self.assertFalse(entry.encoded)
            self.assertEqual(entry.parent_id, 120)

    def test_stage_encodes_when_requested(self) -> None:
        from mtpmanager.app.device_ops import stage_video_for_sync

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "clip.mp4"
            src.write_bytes(b"not-really-video")
            fake_out = root / "encoded.avi"
            fake_out.write_bytes(b"encoded")

            def fake_convert(src_path, profile, *, dest_path=None, **kwargs):
                assert dest_path is not None
                Path(dest_path).write_bytes(b"encoded-avi")
                return dest_path

            with mock.patch(
                "mtpmanager.infra.ffmpeg_video.video_matches_encode_profile",
                return_value=False,
            ), mock.patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile",
                side_effect=fake_convert,
            ):
                entry = stage_video_for_sync(
                    str(src),
                    parent_id=120,
                    encode_profile=ZEN_AVI_XVID_MP3,
                    encode_for_device=True,
                    title="Clip",
                    preferred_basename="clip.mp4",
                    preset_id=ZEN_AVI_XVID_MP3.id,
                    resolution_id="qvga",
                    data_dir=root,
                )
            self.assertTrue(entry.encoded)
            self.assertTrue(os.path.isfile(entry.staged_path))
            self.assertEqual(Path(entry.staged_path).read_bytes(), b"encoded-avi")
            self.assertTrue(entry.preferred_basename.endswith(".avi"))
            self.assertEqual(entry.preset_id, ZEN_AVI_XVID_MP3.id)


if __name__ == "__main__":
    unittest.main()
