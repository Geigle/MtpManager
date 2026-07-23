"""Unit tests for Device → Send Video (no USB / device)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtpmanager.app.device_ops import (
    VIDEO_PARENT_CHOICES,
    SendVideoResult,
    send_video,
)
from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    ZEN_VISION_M_FOLDER_IDS,
    build_remote_path,
    split_remote_path,
)


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.return_id: int | None = 4242

    def send_track(
        self,
        path: str,
        meta: TrackMetadata,
        *,
        parent_id: int | None = None,
        guid: str | None = None,
        preferred_basename: str | None = None,
    ) -> int | None:
        self.calls.append(
            {
                "path": path,
                "meta": meta,
                "parent_id": parent_id,
                "guid": guid,
                "preferred_basename": preferred_basename,
            }
        )
        return self.return_id


class SendVideoTests(unittest.TestCase):
    def test_folder_constants(self) -> None:
        self.assertEqual(DEFAULT_VIDEO_FOLDER_ID, 120)
        self.assertEqual(DEFAULT_TV_FOLDER_ID, 124)
        self.assertEqual(ZEN_VISION_M_FOLDER_IDS[120], "Video")
        self.assertEqual(ZEN_VISION_M_FOLDER_IDS[124], "TV")
        self.assertEqual(
            VIDEO_PARENT_CHOICES,
            frozenset({DEFAULT_VIDEO_FOLDER_ID, DEFAULT_TV_FOLDER_ID}),
        )

    def test_build_remote_path_under_video_parent(self) -> None:
        remote = build_remote_path(
            TrackMetadata(title="Clip"),
            ".wmv",
            music_folder_id=DEFAULT_VIDEO_FOLDER_ID,
            preferred_basename="My Clip.wmv",
        )
        parent, basename = split_remote_path(remote)
        self.assertEqual(parent, DEFAULT_VIDEO_FOLDER_ID)
        self.assertEqual(basename, "My Clip.wmv")
        self.assertNotEqual(parent, DEFAULT_MUSIC_FOLDER_ID)

    def test_send_video_video_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.wmv"
            path.write_bytes(b"fake")
            transport = _FakeTransport()
            result = send_video(
                transport,
                str(path),
                parent_id=DEFAULT_VIDEO_FOLDER_ID,
            )
            self.assertIsInstance(result, SendVideoResult)
            self.assertEqual(result.object_id, 4242)
            self.assertEqual(result.parent_id, DEFAULT_VIDEO_FOLDER_ID)
            self.assertEqual(result.remote_basename, "demo.wmv")
            self.assertEqual(len(transport.calls), 1)
            call = transport.calls[0]
            self.assertEqual(call["parent_id"], DEFAULT_VIDEO_FOLDER_ID)
            self.assertIsNone(call["guid"])
            self.assertEqual(call["preferred_basename"], "demo.wmv")
            self.assertEqual(call["meta"].title, "demo")

    def test_send_video_tv_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.avi"
            path.write_bytes(b"fake")
            transport = _FakeTransport()
            result = send_video(
                transport,
                str(path),
                parent_id=DEFAULT_TV_FOLDER_ID,
                title="Episode 1",
            )
            self.assertEqual(result.parent_id, DEFAULT_TV_FOLDER_ID)
            self.assertEqual(result.remote_basename, "episode.avi")
            self.assertEqual(transport.calls[0]["meta"].title, "Episode 1")

    def test_send_video_rejects_music_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.wmv"
            path.write_bytes(b"fake")
            with self.assertRaises(ValueError):
                send_video(
                    _FakeTransport(),
                    str(path),
                    parent_id=DEFAULT_MUSIC_FOLDER_ID,
                )

    def test_send_video_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            send_video(
                _FakeTransport(),
                "/no/such/video.wmv",
                parent_id=DEFAULT_VIDEO_FOLDER_ID,
            )

    def test_send_video_sanitizes_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Ampersand is unsafe on ZEN ObjectFileNames.
            path = Path(tmp) / "A&B Video.wmv"
            path.write_bytes(b"fake")
            transport = _FakeTransport()
            result = send_video(
                transport,
                str(path),
                parent_id=DEFAULT_VIDEO_FOLDER_ID,
            )
            self.assertNotIn("&", result.remote_basename)
            self.assertTrue(result.remote_basename.endswith(".wmv"))


if __name__ == "__main__":
    unittest.main()
