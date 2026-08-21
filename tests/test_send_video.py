"""Unit tests for Device → Send Video (no USB / device)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtpmanager.app.device_ops import (
    VIDEO_PARENT_CHOICES,
    SendVideoResult,
    pick_library_root,
    prepare_and_send_video,
    send_video,
    suggested_library_relpath,
)
from mtpmanager.domain.models import DeviceTrackInfo, DeviceTrackRef
from mtpmanager.domain.device_profiles import (
    ZEN_AVI_XVID_MP3,
    ZEN_VISION_M,
    ZEN_VISION_M_VIDEO,
    ZEN_VISION_M_VIDEO_OPTIONS,
    ZEN_WMV_WMA,
)
from mtpmanager.domain.models import TrackMetadata
from mtpmanager.domain.track_id import new_track_guid
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

    def test_zen_profile_has_video_options(self) -> None:
        opts = ZEN_VISION_M.video_options
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertIs(opts, ZEN_VISION_M_VIDEO_OPTIONS)
        self.assertEqual(len(opts.presets), 3)
        self.assertEqual(opts.default_preset_id, ZEN_AVI_XVID_MP3.id)
        self.assertEqual(ZEN_VISION_M_VIDEO.id, opts.default_preset().id)
        self.assertEqual(opts.default_preset().video_tag, "XVID")

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

    def test_send_video_ignores_guid_for_object_name(self) -> None:
        """Video ObjectFileName is title/basename, never library GUID.

        GUID may still be passed for durable index recording by callers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature.mp4"
            path.write_bytes(b"fake")
            guid = new_track_guid()
            transport = _FakeTransport()
            result = send_video(
                transport,
                str(path),
                parent_id=DEFAULT_VIDEO_FOLDER_ID,
                guid=guid,
                preferred_basename="feature.mp4",
                title="Feature Film",
            )
            self.assertEqual(result.remote_basename, "feature.mp4")
            call = transport.calls[0]
            self.assertIsNone(call["guid"])
            self.assertEqual(call["preferred_basename"], "feature.mp4")
            self.assertEqual(call["parent_id"], DEFAULT_VIDEO_FOLDER_ID)
            self.assertEqual(call["meta"].title, "Feature Film")

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

    def test_send_video_rejects_disallowed_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.wmv"
            path.write_bytes(b"fake")
            with self.assertRaises(ValueError):
                send_video(
                    _FakeTransport(),
                    str(path),
                    parent_id=DEFAULT_MUSIC_FOLDER_ID,
                    allowed_parents=frozenset(
                        {DEFAULT_VIDEO_FOLDER_ID, DEFAULT_TV_FOLDER_ID}
                    ),
                )
            # Without allowed_parents, any positive folder id is accepted
            # (firmware-specific Video/TV ids).
            result = send_video(
                _FakeTransport(),
                str(path),
                parent_id=108,  # Video on Music-88 firmware map
            )
            self.assertEqual(result.parent_id, 108)

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
            kwargs = convert.call_args.kwargs
            self.assertFalse(kwargs.get("ignore_max_fps", False))
            self.assertTrue(result.encoded)
            self.assertFalse(result.encode_skipped_compatible)
            self.assertEqual(result.parent_id, DEFAULT_TV_FOLDER_ID)
            self.assertEqual(transport.calls[0]["path"], str(encoded))
            self.assertTrue(
                str(transport.calls[0]["preferred_basename"]).endswith(".avi")
            )
            self.assertEqual(phases, ["transcode", "send"])
            cleanup.assert_called()

    def test_prepare_passes_ignore_max_fps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "hi.mp4"
            src.write_bytes(b"src")
            encoded = Path(tmp) / "VIDEO_TRANSCODE_xyz.avi"
            encoded.write_bytes(b"enc")
            transport = _FakeTransport()
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
            ):
                prepare_and_send_video(
                    transport,
                    str(src),
                    parent_id=DEFAULT_VIDEO_FOLDER_ID,
                    encode_profile=ZEN_VISION_M_VIDEO,
                    encode_for_device=True,
                    ignore_max_fps=True,
                )
            self.assertTrue(convert.call_args.kwargs.get("ignore_max_fps"))

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


class LibraryPullPathTests(unittest.TestCase):
    def test_suggested_library_relpath_from_tags(self) -> None:
        ref = DeviceTrackRef(
            item_id=42,
            name="xyz.mp3",
            title="Song",
            artist="Artist",
            album="Album",
        )
        rel = suggested_library_relpath(ref)
        self.assertEqual(rel, os.path.join("Artist", "Album", "Song.mp3"))

    def test_suggested_library_relpath_prefers_info(self) -> None:
        ref = DeviceTrackRef(item_id=1, name="clip.avi", title="Old")
        info = DeviceTrackInfo(
            item_id=1,
            name="clip.avi",
            title="New Title",
            artist="Dir",
            album="Show",
        )
        rel = suggested_library_relpath(ref, info=info)
        self.assertEqual(
            rel, os.path.join("Dir", "Show", "New Title.avi")
        )

    def test_suggested_library_relpath_prefers_file_meta(self) -> None:
        from mtpmanager.domain.models import TrackMetadata

        ref = DeviceTrackRef(
            item_id=1,
            name="dump.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
            album="Unknown Album",
        )
        info = DeviceTrackInfo(
            item_id=1,
            name="dump.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
            album="Unknown Album",
        )
        file_meta = TrackMetadata(
            title="Recovered",
            artist="Real Artist",
            album="Real Album",
        )
        rel = suggested_library_relpath(ref, info=info, file_meta=file_meta)
        self.assertEqual(
            rel,
            os.path.join("Real Artist", "Real Album", "Recovered.mp3"),
        )

    def test_pick_library_root_prefers_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a")
            b = os.path.join(tmp, "b")
            os.makedirs(b)
            # First root whose parent exists is accepted (a is creatable under tmp).
            self.assertEqual(pick_library_root([a, b]), a)
            self.assertEqual(pick_library_root([b]), b)
            self.assertIsNone(pick_library_root([]))
            # Prefer an existing directory over a non-existent sibling first.
            missing = os.path.join(tmp, "no", "such", "deep")
            self.assertEqual(pick_library_root([missing, b]), b)


class VideoEncodeProfileProbeTests(unittest.TestCase):
    def test_parse_ratio_pair(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _parse_ratio_pair

        self.assertEqual(_parse_ratio_pair("853:720"), (853, 720))
        self.assertEqual(_parse_ratio_pair("16/9"), (16, 9))
        self.assertIsNone(_parse_ratio_pair("N/A"))
        self.assertIsNone(_parse_ratio_pair("0:1"))
        self.assertIsNone(_parse_ratio_pair(None))

    def test_aspect_anamorphic_flag(self) -> None:
        from mtpmanager.infra.ffmpeg_video import VideoAspectInfo

        square = VideoAspectInfo(width=640, height=480, sar_num=1, sar_den=1)
        self.assertFalse(square.is_anamorphic)
        ana = VideoAspectInfo(
            width=720,
            height=472,
            sar_num=853,
            sar_den=720,
            dar_num=853,
            dar_den=472,
        )
        self.assertTrue(ana.is_anamorphic)
        self.assertAlmostEqual(ana.dar, 853 / 472, places=5)

    def test_vf_filter_keeps_source_fps_by_default(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _vf_filter

        vf = _vf_filter(ZEN_VISION_M_VIDEO)
        self.assertIn("640:480", vf)
        self.assertNotIn("fps=", vf)
        self.assertIn("yuv420p", vf)
        # Fit storage pixels proportionally, then pad (not stretch-to-fill).
        self.assertIn("force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=640:480", vf)
        # Square-pixel / unknown aspect: SAR cleared before fit scale.
        self.assertTrue(vf.startswith("setsar=1,scale="))
        self.assertNotIn("iw*", vf)

    def test_vf_filter_expands_anamorphic_sar(self) -> None:
        from mtpmanager.infra.ffmpeg_video import VideoAspectInfo, _vf_filter

        aspect = VideoAspectInfo(
            width=720,
            height=472,
            sar_num=853,
            sar_den=720,
            dar_num=853,
            dar_den=472,
        )
        vf = _vf_filter(ZEN_VISION_M_VIDEO, aspect=aspect)
        # Expand probed SAR before setsar=1 + fit/pad.
        self.assertTrue(vf.startswith("scale=trunc(iw*853/720/2)*2:trunc(ih/2)*2,"))
        self.assertIn("setsar=1", vf)
        self.assertIn("force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=640:480", vf)
        # Expand step must come before the device fit scale.
        self.assertLess(vf.index("iw*853/720"), vf.index("scale=640:480"))

    def test_vf_filter_square_sar_skips_expand(self) -> None:
        from mtpmanager.infra.ffmpeg_video import VideoAspectInfo, _vf_filter

        aspect = VideoAspectInfo(width=1280, height=720, sar_num=1, sar_den=1)
        vf = _vf_filter(ZEN_VISION_M_VIDEO, aspect=aspect)
        self.assertTrue(vf.startswith("setsar=1,scale=640:480"))
        self.assertNotIn("iw*", vf)

    def test_vf_filter_caps_when_force_fps(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _vf_filter

        vf = _vf_filter(ZEN_VISION_M_VIDEO, force_fps=30.0)
        self.assertIn("fps=30", vf)

    def test_vf_filter_default_uses_decrease(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _vf_filter

        vf = _vf_filter(ZEN_VISION_M_VIDEO)
        self.assertIn("force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=640:480", vf)
        self.assertNotIn("crop=", vf)

    def test_output_fps_for_source(self) -> None:
        from mtpmanager.infra.ffmpeg_video import output_fps_for_source

        # Default (max_fps=0): always keep source, even for 60 fps.
        self.assertIsNone(output_fps_for_source(60.0, 0.0))
        self.assertIsNone(output_fps_for_source(25.0, 0.0))
        # ZEN Vision:M max_fps=30: keep demo rates, cap only when higher.
        self.assertIsNone(output_fps_for_source(25.0, 30.0))
        self.assertIsNone(output_fps_for_source(30000 / 1001, 30.0))  # ~29.97
        self.assertIsNone(output_fps_for_source(24.0, 30.0))
        self.assertIsNone(output_fps_for_source(30.0, 30.0))
        self.assertEqual(output_fps_for_source(60.0, 30.0), 30.0)
        self.assertEqual(output_fps_for_source(59.94, 30.0), 30.0)
        # Unknown source → leave alone.
        self.assertIsNone(output_fps_for_source(0.0, 30.0))

    def test_zen_profile_has_max_fps_default_does_not(self) -> None:
        from mtpmanager.domain.device_profile import VideoEncodePreset

        default = VideoEncodePreset(id="x", display_name="x")
        self.assertEqual(default.max_fps, 0.0)
        self.assertEqual(ZEN_VISION_M_VIDEO.max_fps, 30.0)
        self.assertEqual(ZEN_WMV_WMA.max_fps, 30.0)

    def test_build_output_options_wmv(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _build_output_options

        opts = _build_output_options(ZEN_WMV_WMA, force_fps=None, container_ext="wmv")
        self.assertEqual(opts["c:v"], "wmv2")
        self.assertEqual(opts["c:a"], "wmav2")
        self.assertEqual(opts["b:v"], "480k")
        self.assertEqual(opts["f"], "asf")
        self.assertNotIn("vtag", opts)
        self.assertNotIn("qscale:v", opts)

    def test_build_output_options_xvid(self) -> None:
        from mtpmanager.infra.ffmpeg_video import _build_output_options

        opts = _build_output_options(
            ZEN_AVI_XVID_MP3, force_fps=30.0, container_ext="avi"
        )
        self.assertEqual(opts["c:v"], "mpeg4")
        self.assertEqual(opts["vtag"], "XVID")
        self.assertEqual(opts["qscale:v"], "5")
        self.assertIn("fps=30", opts["vf"])
        self.assertNotIn("mbd", opts)

    def test_build_output_options_slow_hq_mpeg4(self) -> None:
        from dataclasses import replace

        from mtpmanager.infra.ffmpeg_video import _build_output_options

        slow = replace(ZEN_AVI_XVID_MP3, qscale_v=2, slow_encode=True)
        opts = _build_output_options(slow, force_fps=None, container_ext="avi")
        self.assertEqual(opts["qscale:v"], "2")
        self.assertEqual(opts["mbd"], "rd")
        self.assertEqual(opts["trellis"], "2")
        self.assertEqual(opts["flags"], "+mv4+aic")
        self.assertEqual(opts["cmp"], "2")
        self.assertEqual(opts["subcmp"], "2")

        # Slow flags must not attach to WMV even if the flag is set.
        wmv_slow = replace(ZEN_WMV_WMA, slow_encode=True)
        wopts = _build_output_options(
            wmv_slow, force_fps=None, container_ext="wmv"
        )
        self.assertNotIn("mbd", wopts)
        self.assertEqual(wopts["b:v"], "480k")

    def test_vf_filter_qvga_and_qqvga(self) -> None:
        from mtpmanager.domain.video_encode import RES_QQVGA, RES_QVGA, apply_resolution
        from mtpmanager.infra.ffmpeg_video import _vf_filter

        qvga = apply_resolution(ZEN_AVI_XVID_MP3, RES_QVGA)
        vf = _vf_filter(qvga)
        self.assertIn("320:240", vf)
        self.assertIn("pad=320:240", vf)

        qqvga = apply_resolution(ZEN_AVI_XVID_MP3, RES_QQVGA)
        vf2 = _vf_filter(qqvga)
        self.assertIn("160:120", vf2)
        self.assertIn("pad=160:120", vf2)

    def test_build_output_options_merges_audio_settings_without_vn(self) -> None:
        from mtpmanager.domain.audio_encode import get_preset
        from mtpmanager.infra.ffmpeg_video import _build_output_options

        audio = get_preset("mp3_cbr_64")
        assert audio is not None
        opts = _build_output_options(
            ZEN_AVI_XVID_MP3,
            force_fps=None,
            container_ext="avi",
            audio_settings=audio.settings,
        )
        self.assertEqual(opts["map"], ["0:v:0", "0:a:0?"])
        self.assertNotIn("vn", opts)
        self.assertEqual(opts["c:a"], "libmp3lame")
        self.assertEqual(opts["b:a"], "64k")
        self.assertIn("ar", opts)
        self.assertIn("ac", opts)


class AudioStillVideoEncodeTests(unittest.TestCase):
    """Live ffmpeg: audio + black/still → device AVI (experimental podcast path)."""

    @staticmethod
    def _have_ffmpeg() -> bool:
        import shutil

        return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

    def test_audio_still_black_produces_video_with_audio(self) -> None:
        if not self._have_ffmpeg():
            self.skipTest("ffmpeg/ffprobe not on PATH")
        import subprocess

        from mtpmanager.infra.ffmpeg_video import (
            convert_audio_still_to_video_for_profile,
            probe_media,
        )

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "tone.mp3"
            # ~2s mono MP3 via lavfi.
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=2",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(audio),
                ],
                check=True,
                capture_output=True,
            )
            dest = Path(tmp) / "still.avi"
            out = convert_audio_still_to_video_for_profile(
                str(audio),
                ZEN_AVI_XVID_MP3,
                image_path=None,
                dest_path=str(dest),
                still_fps=2.0,
                width=128,
                height=96,
            )
            self.assertEqual(out, str(dest))
            self.assertTrue(dest.is_file())
            audio_size = audio.stat().st_size
            out_size = dest.stat().st_size
            # Still track should not dominate: overhead modest vs audio.
            self.assertGreater(out_size, audio_size)
            # ZVM-proven small frame + 2 fps should stay under 5× audio for 2s.
            self.assertLess(out_size, audio_size * 5)

            data = probe_media(str(dest))
            streams = data.get("streams") or []
            kinds = {s.get("codec_type") for s in streams}
            self.assertIn("video", kinds)
            self.assertIn("audio", kinds)
            vs = [s for s in streams if s.get("codec_type") == "video"]
            self.assertEqual(int(vs[0].get("width") or 0), 128)
            self.assertEqual(int(vs[0].get("height") or 0), 96)
            self.assertEqual(
                str(vs[0].get("codec_tag_string") or "").upper(), "XVID"
            )
            r_fps = str(vs[0].get("r_frame_rate") or "")
            self.assertTrue(
                r_fps in ("2/1", "2") or r_fps.startswith("2"),
                f"expected ~2 fps, got {r_fps!r}",
            )

    def test_audio_still_with_image(self) -> None:
        if not self._have_ffmpeg():
            self.skipTest("ffmpeg/ffprobe not on PATH")
        import subprocess

        from mtpmanager.infra.ffmpeg_video import (
            convert_audio_still_to_video_for_profile,
            probe_media,
        )

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "tone.mp3"
            img = Path(tmp) / "art.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:duration=1.5",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "96k",
                    str(audio),
                ],
                check=True,
                capture_output=True,
            )
            # Solid color still (no Pillow required).
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x240:d=0.1",
                    "-frames:v",
                    "1",
                    str(img),
                ],
                check=True,
                capture_output=True,
            )
            dest = Path(tmp) / "art_still.avi"
            convert_audio_still_to_video_for_profile(
                str(audio),
                ZEN_AVI_XVID_MP3,
                image_path=str(img),
                dest_path=str(dest),
                still_fps=2.0,
                width=128,
                height=96,
            )
            data = probe_media(str(dest))
            vs = [
                s
                for s in (data.get("streams") or [])
                if s.get("codec_type") == "video"
            ]
            self.assertEqual(int(vs[0].get("width") or 0), 128)
            self.assertEqual(int(vs[0].get("height") or 0), 96)
            r_fps = str(vs[0].get("r_frame_rate") or "")
            self.assertTrue(
                r_fps in ("2/1", "2") or r_fps.startswith("2"),
                f"expected ~2 fps, got {r_fps!r}",
            )


class PodcastFullMotionVideoEncodeTests(unittest.TestCase):
    """Full-motion podcast video must use SAR-aware convert_video_for_profile."""

    def test_keep_download_path_calls_convert_video_for_profile(self) -> None:
        from mtpmanager.app.podcast_ops import (
            PodcastVideoJob,
            send_podcast_video_to_zencast,
        )
        from mtpmanager.domain.track_id import new_track_guid
        from mtpmanager.infra.podcast_index import Podcast, PodcastEpisode

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ep.mp4"
            src.write_bytes(b"fake-video")
            guid = new_track_guid()
            ep = PodcastEpisode(
                id=1,
                podcast_id=9,
                guid=guid,
                feed_guid="fg",
                title="Anamorphic Ep",
                local_path=str(src),
            )
            show = Podcast(
                id=9,
                feed_url="https://example.com/rss",
                title="Show",
            )
            job = PodcastVideoJob(
                episode=ep,
                podcast=show,
                local_path=str(src),
                from_audio_still=False,
            )
            encoded = Path(tmp) / f"{guid}_device.avi"
            encoded.write_bytes(b"enc-avi")

            with patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile",
                return_value=str(encoded),
            ) as conv, patch(
                "mtpmanager.app.podcast_ops.episode_cache_dir",
                return_value=Path(tmp),
            ), patch(
                "mtpmanager.app.device_ops.prepare_and_send_video",
                return_value=SendVideoResult(
                    object_id=42,
                    parent_id=128,
                    remote_basename="Anamorphic Ep.avi",
                    path=str(encoded),
                    source_path=str(encoded),
                    encoded=False,
                ),
            ) as prep:
                send_podcast_video_to_zencast(
                    _FakeTransport(),
                    job,
                    parent_id=128,
                    encode_profile=ZEN_AVI_XVID_MP3,
                    encode_for_device=True,
                    keep_download=True,
                )
            conv.assert_called_once()
            self.assertEqual(conv.call_args.args[0], str(src))
            self.assertIs(conv.call_args.args[1], ZEN_AVI_XVID_MP3)
            # Encode already done; prepare sends the device AVI as-is.
            prep.assert_called_once()
            self.assertFalse(prep.call_args.kwargs.get("encode_for_device"))

    def test_no_cache_path_encodes_via_prepare_and_send_video(self) -> None:
        from mtpmanager.app.podcast_ops import (
            PodcastVideoJob,
            send_podcast_video_to_zencast,
        )
        from mtpmanager.infra.podcast_index import Podcast, PodcastEpisode

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ep.mp4"
            src.write_bytes(b"fake-video")
            ep = PodcastEpisode(
                id=2,
                podcast_id=9,
                guid="not-a-valid-guid-shape",
                feed_guid="fg",
                title="Live Encode Ep",
                local_path=str(src),
            )
            show = Podcast(
                id=9,
                feed_url="https://example.com/rss",
                title="Show",
            )
            job = PodcastVideoJob(
                episode=ep,
                podcast=show,
                local_path=str(src),
                from_audio_still=False,
            )
            with patch(
                "mtpmanager.infra.ffmpeg_video.convert_video_for_profile"
            ) as conv, patch(
                "mtpmanager.app.device_ops.prepare_and_send_video",
                return_value=SendVideoResult(
                    object_id=7,
                    parent_id=128,
                    remote_basename="Live Encode Ep.avi",
                    path=str(src),
                    source_path=str(src),
                    encoded=True,
                ),
            ) as prep:
                send_podcast_video_to_zencast(
                    _FakeTransport(),
                    job,
                    parent_id=128,
                    encode_profile=ZEN_AVI_XVID_MP3,
                    encode_for_device=True,
                    keep_download=False,
                )
            # No durable cache encode; prepare_and_send_video does the convert
            # (which probes SAR inside convert_video_for_profile).
            conv.assert_not_called()
            prep.assert_called_once()
            self.assertTrue(prep.call_args.kwargs.get("encode_for_device"))
            self.assertIs(
                prep.call_args.kwargs.get("encode_profile"), ZEN_AVI_XVID_MP3
            )


class ConvertVideoAspectProbeTests(unittest.TestCase):
    def test_convert_video_for_profile_probes_aspect(self) -> None:
        from mtpmanager.infra.ffmpeg_video import VideoAspectInfo

        aspect = VideoAspectInfo(
            width=720,
            height=472,
            sar_num=853,
            sar_den=720,
            dar_num=853,
            dar_den=472,
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            src.write_bytes(b"x")
            dest = Path(tmp) / "out.avi"
            dest.write_bytes(b"encoded")
            with patch(
                "mtpmanager.infra.ffmpeg_video.probe_duration_seconds",
                return_value=1.0,
            ), patch(
                "mtpmanager.infra.ffmpeg_video.probe_video_fps",
                return_value=24.0,
            ), patch(
                "mtpmanager.infra.ffmpeg_video.probe_video_aspect",
                return_value=aspect,
            ) as probe, patch(
                "mtpmanager.infra.ffmpeg_video._build_output_options",
                return_value={"map": ["0:v:0"], "c:v": "mpeg4", "f": "avi"},
            ) as build, patch(
                "mtpmanager.infra.ffmpeg_video.FFmpeg",
            ), patch(
                "mtpmanager.infra.ffmpeg_video.run_ffmpeg_builder",
            ):
                from mtpmanager.infra.ffmpeg_video import convert_video_for_profile

                # Pretend ffmpeg wrote the dest (convert checks size after run).
                def _run(*_a, **_k):
                    dest.write_bytes(b"encoded-ok")

                with patch(
                    "mtpmanager.infra.ffmpeg_video.run_ffmpeg_builder",
                    side_effect=_run,
                ):
                    out = convert_video_for_profile(
                        str(src),
                        ZEN_AVI_XVID_MP3,
                        dest_path=str(dest),
                    )
            self.assertEqual(out, str(dest))
            probe.assert_called_once_with(str(src))
            self.assertIs(build.call_args.kwargs.get("aspect"), aspect)


class PodcastAudioAsVideoSendTests(unittest.TestCase):
    def test_send_still_job_uses_audio_still_convert(self) -> None:
        from mtpmanager.app.podcast_ops import (
            PodcastVideoJob,
            send_podcast_video_to_zencast,
        )
        from mtpmanager.domain.track_id import new_track_guid
        from mtpmanager.infra.podcast_index import Podcast, PodcastEpisode

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "ep.mp3"
            audio.write_bytes(b"fake-audio")
            guid = new_track_guid()
            ep = PodcastEpisode(
                id=1,
                podcast_id=9,
                guid=guid,
                feed_guid="fg",
                title="Still Ep",
                local_path=str(audio),
            )
            show = Podcast(
                id=9,
                feed_url="https://example.com/rss",
                title="Show",
            )
            job = PodcastVideoJob(
                episode=ep,
                podcast=show,
                local_path=str(audio),
                from_audio_still=True,
                image_path="",
            )
            transport = _FakeTransport()
            encoded = Path(tmp) / "VIDEO_TRANSCODE_still.avi"
            encoded.write_bytes(b"enc-avi")

            with patch(
                "mtpmanager.infra.ffmpeg_video.convert_audio_still_to_video_for_profile",
                return_value=str(encoded),
            ) as still_conv, patch(
                "mtpmanager.infra.ffmpeg_video.default_temp_video_path",
                return_value=str(encoded),
            ), patch(
                "mtpmanager.infra.ffmpeg_video.cleanup_video_temp"
            ), patch(
                "mtpmanager.app.device_ops.prepare_and_send_video",
                return_value=SendVideoResult(
                    object_id=99,
                    parent_id=128,
                    remote_basename="Still Ep.avi",
                    path=str(encoded),
                    source_path=str(encoded),
                    encoded=False,
                ),
            ) as prep:
                result = send_podcast_video_to_zencast(
                    transport,
                    job,
                    parent_id=128,
                    encode_profile=ZEN_AVI_XVID_MP3,
                    encode_for_device=True,
                    keep_download=False,
                )
            still_conv.assert_called_once()
            self.assertEqual(still_conv.call_args.args[0], str(audio))
            prep.assert_called_once()
            # prepare receives the encoded AVI, not the raw audio.
            self.assertEqual(prep.call_args.args[1], str(encoded))
            self.assertFalse(prep.call_args.kwargs.get("encode_for_device"))
            self.assertEqual(result.object_id, 99)
            self.assertEqual(result.parent_id, 128)


if __name__ == "__main__":
    unittest.main()
