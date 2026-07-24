"""Unit tests for Device → Send Video (no USB / device)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtpmanager.app.device_ops import (
    VIDEO_PARENT_CHOICES,
    SendVideoResult,
    prepare_and_send_video,
    send_video,
)
from mtpmanager.domain.device_profiles import ZEN_VISION_M, ZEN_VISION_M_VIDEO
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

    def test_zen_profile_has_video_encode(self) -> None:
        self.assertIsNotNone(ZEN_VISION_M.video_encode)
        assert ZEN_VISION_M.video_encode is not None
        self.assertEqual(ZEN_VISION_M.video_encode.container, "avi")
        self.assertEqual(ZEN_VISION_M.video_encode.video_tag, "XVID")
        self.assertEqual(ZEN_VISION_M.video_encode.width, 640)
        self.assertEqual(ZEN_VISION_M.video_encode.height, 480)
        self.assertEqual(ZEN_VISION_M_VIDEO.id, ZEN_VISION_M.video_encode.id)

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

    def test_prepare_skips_encode_when_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.avi"
            path.write_bytes(b"fake")
            transport = _FakeTransport()
            events: list[tuple] = []

            def on_progress(kind, *args):
                events.append((kind, *args))

            with patch(
                "mtpmanager.infra.ffmpeg_video.video_matches_encode_profile",
                return_value=True,
            ), patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile"
            ) as convert:
                result = prepare_and_send_video(
                    transport,
                    str(path),
                    parent_id=DEFAULT_VIDEO_FOLDER_ID,
                    encode_profile=ZEN_VISION_M_VIDEO,
                    encode_for_device=True,
                    on_progress=on_progress,
                )
            convert.assert_not_called()
            self.assertFalse(result.encoded)
            self.assertTrue(result.encode_skipped_compatible)
            self.assertEqual(transport.calls[0]["path"], str(path))
            kinds = [e[0] for e in events]
            self.assertIn("phase", kinds)
            self.assertIn("send", [e[1] for e in events if e[0] == "phase"])

    def test_prepare_encodes_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "movie.wmv"
            src.write_bytes(b"src")
            encoded = Path(tmp) / "VIDEO_TRANSCODE_abc123.avi"
            encoded.write_bytes(b"enc")
            transport = _FakeTransport()
            phases: list[str] = []

            def on_progress(kind, *args):
                if kind == "phase":
                    phases.append(str(args[0]))

            with patch(
                "mtpmanager.infra.ffmpeg_video.video_matches_encode_profile",
                return_value=False,
            ), patch(
                "mtpmanager.infra.ffmpeg_video.default_temp_video_path",
                return_value=str(encoded),
            ), patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile",
                return_value=str(encoded),
            ) as convert, patch(
                "mtpmanager.infra.ffmpeg_video.cleanup_video_temp"
            ) as cleanup:
                result = prepare_and_send_video(
                    transport,
                    str(src),
                    parent_id=DEFAULT_TV_FOLDER_ID,
                    encode_profile=ZEN_VISION_M_VIDEO,
                    encode_for_device=True,
                    on_progress=on_progress,
                )
            convert.assert_called_once()
            self.assertTrue(result.encoded)
            self.assertFalse(result.encode_skipped_compatible)
            self.assertEqual(result.parent_id, DEFAULT_TV_FOLDER_ID)
            self.assertEqual(transport.calls[0]["path"], str(encoded))
            self.assertTrue(
                str(transport.calls[0]["preferred_basename"]).endswith(".avi")
            )
            self.assertEqual(phases, ["transcode", "send"])
            cleanup.assert_called()

    def test_prepare_no_encode_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.mp4"
            path.write_bytes(b"x")
            transport = _FakeTransport()
            with patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile"
            ) as convert:
                result = prepare_and_send_video(
                    transport,
                    str(path),
                    parent_id=DEFAULT_VIDEO_FOLDER_ID,
                    encode_profile=ZEN_VISION_M_VIDEO,
                    encode_for_device=False,
                )
            convert.assert_not_called()
            self.assertFalse(result.encoded)
            self.assertEqual(transport.calls[0]["path"], str(path))


class VideoEncodeProfileProbeTests(unittest.TestCase):
    def test_vf_filter_and_match_helpers(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _vf_filter

        vf = _vf_filter(ZEN_VISION_M_VIDEO)
        self.assertIn("640:480", vf)
        self.assertIn("fps=25", vf)
        self.assertIn("yuv420p", vf)


if __name__ == "__main__":
    unittest.main()
