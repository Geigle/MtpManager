"""Unit tests for device profile matching (no hardware)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.device_profile import (
    match_device_profile,
    needs_transcode,
    profile_matches,
)
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES, GENERIC, ZEN_VISION_M
from mtpmanager.domain.models import DeviceInfo
from mtpmanager.infra.device_assets import device_graphic_path


class DeviceProfileTests(unittest.TestCase):
    def test_zen_matches_creative_vision_m(self) -> None:
        info = DeviceInfo(
            name="My Zen",
            manufacturer="Creative Technology Ltd",
            model="ZEN Vision:M",
        )
        self.assertTrue(profile_matches(info, ZEN_VISION_M))
        self.assertEqual(match_device_profile(info, BUILTIN_PROFILES).id, ZEN_VISION_M.id)

    def test_zen_matches_loose_model_string(self) -> None:
        info = DeviceInfo(manufacturer="CREATIVE", model="Zen Vision M 30GB")
        self.assertEqual(match_device_profile(info, BUILTIN_PROFILES).id, ZEN_VISION_M.id)

    def test_other_device_is_generic(self) -> None:
        info = DeviceInfo(manufacturer="Apple", model="iPod")
        self.assertEqual(match_device_profile(info, BUILTIN_PROFILES).id, GENERIC.id)

    def test_empty_info_is_generic(self) -> None:
        self.assertEqual(
            match_device_profile(DeviceInfo(), BUILTIN_PROFILES).id, GENERIC.id
        )

    def test_graphic_assets_exist(self) -> None:
        for profile in BUILTIN_PROFILES:
            path = device_graphic_path(profile.graphic_filename)
            self.assertTrue(path.is_file(), msg=f"missing {path}")

    def test_zen_supported_formats(self) -> None:
        self.assertEqual(
            ZEN_VISION_M.supported_audio_formats,
            frozenset({"mp3", "wma", "wav"}),
        )
        self.assertTrue(ZEN_VISION_M.accepts_audio_format("WMA"))
        self.assertTrue(ZEN_VISION_M.accepts_source_path("/lib/song.wav"))
        self.assertFalse(ZEN_VISION_M.accepts_source_path("/lib/song.flac"))

    def test_zen_video_encode_profile(self) -> None:
        ve = ZEN_VISION_M.video_encode
        self.assertIsNotNone(ve)
        assert ve is not None
        self.assertEqual(ve.container, "avi")
        self.assertEqual(ve.video_codec, "mpeg4")
        self.assertEqual(ve.video_tag, "XVID")
        self.assertEqual(ve.width, 640)
        self.assertEqual(ve.height, 480)
        self.assertEqual(ve.max_fps, 30.0)
        self.assertEqual(ve.probe_audio_codec, "mp3")
        self.assertIsNone(GENERIC.video_encode)

    def test_needs_transcode_passthrough_native(self) -> None:
        # Prefer passthrough of device-native formats over re-encode to target.
        self.assertFalse(
            needs_transcode(
                "track.wma",
                target_format="mp3",
                device_formats=ZEN_VISION_M.supported_audio_formats,
            )
        )
        self.assertFalse(
            needs_transcode(
                "track.wav",
                target_format="mp3",
                device_formats=ZEN_VISION_M.supported_audio_formats,
            )
        )
        self.assertFalse(
            needs_transcode(
                "track.mp3",
                target_format="wma",
                device_formats=ZEN_VISION_M.supported_audio_formats,
            )
        )

    def test_needs_transcode_when_unsupported(self) -> None:
        self.assertTrue(
            needs_transcode(
                "track.flac",
                target_format="mp3",
                device_formats=ZEN_VISION_M.supported_audio_formats,
            )
        )
        self.assertTrue(
            needs_transcode(
                "track.ogg",
                target_format="wav",
                device_formats=ZEN_VISION_M.supported_audio_formats,
            )
        )

    def test_needs_transcode_without_device_formats(self) -> None:
        # Legacy: only skip when already the target format.
        self.assertFalse(needs_transcode("a.mp3", target_format="mp3"))
        self.assertTrue(needs_transcode("a.wma", target_format="mp3"))


if __name__ == "__main__":
    unittest.main()
