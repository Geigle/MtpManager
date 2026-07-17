"""Unit tests for device profile matching (no hardware)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.device_profile import match_device_profile, profile_matches
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


if __name__ == "__main__":
    unittest.main()
