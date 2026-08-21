"""Unit tests for video resolution catalog and recipe ⊕ audio helpers."""

from __future__ import annotations

import unittest

from mtpmanager.domain.audio_encode import AudioEncodeSettings, get_preset
from mtpmanager.domain.device_profiles import (
    ZEN_AVI_XVID_MP3,
    ZEN_AVI_DIVX_MP3,
    ZEN_VISION_M,
    ZEN_WMV_WMA,
)
from mtpmanager.domain.video_encode import (
    COMMON_VIDEO_RESOLUTIONS,
    RES_QQVGA,
    RES_QVGA,
    RES_VGA,
    PodcastVideoEncodeSettings,
    apply_audio_settings,
    apply_resolution,
    audio_formats_for_video_preset,
    default_video_audio_settings,
    effective_video_preset,
    resolution_by_id,
    resolve_podcast_video_preset,
    resolve_resolution,
)


class VideoResolutionCatalogTests(unittest.TestCase):
    def test_common_catalog_includes_zen_sizes(self) -> None:
        ids = {r.id for r in COMMON_VIDEO_RESOLUTIONS}
        self.assertIn("qqvga", ids)
        self.assertIn("qvga", ids)
        self.assertIn("vga", ids)
        self.assertEqual(RES_QQVGA.width, 160)
        self.assertEqual(RES_QQVGA.height, 120)
        self.assertEqual(RES_QVGA.width, 320)
        self.assertEqual(RES_QVGA.height, 240)
        self.assertEqual(RES_VGA.width, 640)
        self.assertEqual(RES_VGA.height, 480)

    def test_resolution_by_id(self) -> None:
        self.assertIs(resolution_by_id("QVGA"), RES_QVGA)
        self.assertIsNone(resolution_by_id("nope"))
        self.assertIsNone(resolution_by_id(None))

    def test_resolve_resolution_by_geometry(self) -> None:
        r = resolve_resolution(width=320, height=240)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.id, "qvga")


class ZenVideoOptionsResolutionTests(unittest.TestCase):
    def test_zen_allows_three_resolutions_default_qvga(self) -> None:
        opts = ZEN_VISION_M.video_options
        self.assertIsNotNone(opts)
        assert opts is not None
        ids = [r.id for r in opts.allowed_resolutions]
        self.assertEqual(ids, ["qqvga", "qvga", "vga"])
        self.assertEqual(opts.default_resolution_id, "qvga")
        d = opts.default_resolution()
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.width, 320)
        self.assertEqual(d.height, 240)


class ApplyAxesTests(unittest.TestCase):
    def test_apply_resolution_updates_geometry(self) -> None:
        p = apply_resolution(ZEN_AVI_XVID_MP3, RES_QQVGA)
        self.assertEqual(p.width, 160)
        self.assertEqual(p.height, 120)
        self.assertIn("160×120", p.video_detail)
        # Original recipe literal unchanged.
        self.assertEqual(ZEN_AVI_XVID_MP3.width, 640)

    def test_audio_formats_avi_mp3_wmv_wma(self) -> None:
        self.assertEqual(
            audio_formats_for_video_preset(ZEN_AVI_XVID_MP3), frozenset({"mp3"})
        )
        self.assertEqual(
            audio_formats_for_video_preset(ZEN_AVI_DIVX_MP3), frozenset({"mp3"})
        )
        self.assertEqual(
            audio_formats_for_video_preset(ZEN_WMV_WMA), frozenset({"wma"})
        )

    def test_default_video_audio_settings_mp3_128(self) -> None:
        s = default_video_audio_settings(ZEN_AVI_XVID_MP3)
        self.assertEqual(s.normalized_format(), "mp3")
        self.assertEqual(s.bitrate_kbps, 128)
        self.assertEqual(s.channels, 2)
        self.assertEqual(s.sample_rate, 44100)

    def test_apply_audio_settings_syncs_preset_fields(self) -> None:
        preset = get_preset("mp3_cbr_192")
        assert preset is not None
        p = apply_audio_settings(ZEN_AVI_XVID_MP3, preset.settings)
        self.assertEqual(p.audio_codec, "libmp3lame")
        self.assertEqual(p.probe_audio_codec, "mp3")
        self.assertEqual(p.audio_bitrate, "192k")
        self.assertEqual(p.audio_sample_rate, 44100)
        self.assertEqual(p.audio_channels, 2)

    def test_apply_audio_clamps_wma_off_avi(self) -> None:
        wma = get_preset("wma_cbr_128")
        assert wma is not None
        p = apply_audio_settings(ZEN_AVI_XVID_MP3, wma.settings)
        # AVI recipe must stay on MP3.
        self.assertEqual(p.probe_audio_codec, "mp3")
        self.assertIn("mp3", p.audio_codec.casefold() + p.probe_audio_codec)

    def test_effective_video_preset_combines_axes(self) -> None:
        audio = AudioEncodeSettings(
            format="mp3",
            preset_id="mp3_cbr_64",
            rate_control="cbr",
            bitrate_kbps=64,
            sample_rate=22050,
            channels=1,
            label="MP3 64 mono",
        )
        p = effective_video_preset(
            ZEN_AVI_XVID_MP3,
            resolution=RES_QVGA,
            audio_settings=audio,
            qscale_v=2,
            slow_encode=True,
        )
        self.assertEqual(p.width, 320)
        self.assertEqual(p.height, 240)
        self.assertEqual(p.audio_bitrate, "64k")
        self.assertEqual(p.audio_sample_rate, 22050)
        self.assertEqual(p.audio_channels, 1)
        self.assertEqual(p.qscale_v, 2)
        self.assertTrue(p.slow_encode)
        self.assertIn("slow HQ", p.video_detail)

    def test_apply_video_quality_clamps_and_skips_wmv_qscale(self) -> None:
        from mtpmanager.domain.video_encode import apply_video_quality

        hq = apply_video_quality(ZEN_AVI_XVID_MP3, qscale_v=2, slow_encode=True)
        self.assertEqual(hq.qscale_v, 2)
        self.assertTrue(hq.slow_encode)

        # Out of range clamps.
        soft = apply_video_quality(ZEN_AVI_XVID_MP3, qscale_v=99)
        self.assertEqual(soft.qscale_v, 15)

        # WMV is bitrate-only: qscale ignored; slow still stored on preset.
        wmv = apply_video_quality(ZEN_WMV_WMA, qscale_v=2, slow_encode=True)
        self.assertIsNone(wmv.qscale_v)
        self.assertEqual(wmv.video_bitrate, "480k")
        self.assertTrue(wmv.slow_encode)


class PodcastVideoEncodeSettingsTests(unittest.TestCase):
    def test_round_trip_dict(self) -> None:
        preset = get_preset("mp3_cbr_128")
        assert preset is not None
        s = PodcastVideoEncodeSettings(
            preset_id="zen_avi_xvid_mp3",
            resolution_id="qvga",
            audio_encode=preset.settings,
            qscale_v=3,
            slow_encode=True,
        )
        again = PodcastVideoEncodeSettings.from_dict(s.to_dict())
        self.assertIsNotNone(again)
        assert again is not None
        self.assertEqual(again.preset_id, "zen_avi_xvid_mp3")
        self.assertEqual(again.resolution_id, "qvga")
        self.assertEqual(again.qscale_v, 3)
        self.assertTrue(again.slow_encode)
        self.assertIsNotNone(again.audio_encode)
        assert again.audio_encode is not None
        self.assertEqual(again.audio_encode.bitrate_kbps, 128)
        self.assertIn("qscale 3", s.summary_line())
        self.assertIn("slow", s.summary_line())

    def test_resolve_podcast_video_preset_axes(self) -> None:
        opts = ZEN_VISION_M.video_options
        assert opts is not None
        preset = get_preset("mp3_cbr_64")
        assert preset is not None
        s = PodcastVideoEncodeSettings(
            preset_id="zen_avi_divx_mp3",
            resolution_id="qqvga",
            audio_encode=preset.settings,
            qscale_v=2,
            slow_encode=True,
        )
        p = resolve_podcast_video_preset(opts, s)
        self.assertEqual(p.id, "zen_avi_divx_mp3")
        self.assertEqual((p.width, p.height), (160, 120))
        self.assertEqual(p.audio_bitrate, "64k")
        self.assertEqual(p.qscale_v, 2)
        self.assertTrue(p.slow_encode)

    def test_resolve_none_uses_device_defaults(self) -> None:
        opts = ZEN_VISION_M.video_options
        assert opts is not None
        p = resolve_podcast_video_preset(opts, None)
        self.assertEqual(p.id, opts.default_preset_id)
        dres = opts.default_resolution()
        assert dres is not None
        self.assertEqual((p.width, p.height), (dres.width, dres.height))
        self.assertEqual(p.qscale_v, 5)
        self.assertFalse(p.slow_encode)


if __name__ == "__main__":
    unittest.main()
