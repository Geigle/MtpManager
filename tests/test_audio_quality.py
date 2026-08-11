"""Quality-aware encode policy: never claim more fidelity than the source."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.app.transfer import prepare_track
from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    estimate_settings_bitrate_kbps,
    get_preset,
)
from mtpmanager.domain.audio_quality import (
    DeviceCapabilities,
    QualityTier,
    clamp_settings_to_source,
    decide_action,
    normalize_bitrate_kbps,
    settings_strictly_lower_quality,
    source_metrics_from_meta,
    target_bitrate_for_tier,
    tier_from_bitrate_kbps,
    tier_from_settings,
)
from mtpmanager.domain.models import Track, TrackMetadata


class TierMapTests(unittest.TestCase):
    def test_bitrate_normalization(self) -> None:
        self.assertEqual(normalize_bitrate_kbps(320000), 320)
        self.assertEqual(normalize_bitrate_kbps(128), 128)
        self.assertIsNone(normalize_bitrate_kbps(0))

    def test_tier_from_bitrate(self) -> None:
        self.assertEqual(tier_from_bitrate_kbps(64), QualityTier.SPEECH)
        self.assertEqual(tier_from_bitrate_kbps(128), QualityTier.LOW_MEDIUM)
        self.assertEqual(tier_from_bitrate_kbps(192), QualityTier.MEDIUM)
        self.assertEqual(tier_from_bitrate_kbps(256), QualityTier.HIGH)
        self.assertEqual(tier_from_bitrate_kbps(320), QualityTier.VERY_HIGH)
        self.assertEqual(
            tier_from_bitrate_kbps(None, lossless=True), QualityTier.LOSSLESS
        )
        # Unknown → conservative Medium
        self.assertEqual(tier_from_bitrate_kbps(None), QualityTier.MEDIUM)

    def test_target_map_never_above_tier_ceiling(self) -> None:
        for tier in (
            QualityTier.SPEECH,
            QualityTier.LOW_MEDIUM,
            QualityTier.MEDIUM,
            QualityTier.HIGH,
            QualityTier.VERY_HIGH,
        ):
            for fmt in ("mp3", "aac", "opus", "wma"):
                br = target_bitrate_for_tier(fmt, tier)
                # Map targets stay at or under the MP3-equivalent ceiling.
                from mtpmanager.domain.audio_quality import TIER_CEILING_KBPS

                self.assertLessEqual(br, TIER_CEILING_KBPS[tier] + 32)


class DecideActionTests(unittest.TestCase):
    def _meta(
        self,
        *,
        bitrate_bps: int = 128000,
        sr: int = 44100,
        ch: int = 2,
    ) -> TrackMetadata:
        return TrackMetadata(
            title="T",
            artist="A",
            album="B",
            bitrate=bitrate_bps,
            sample_rate=sr,
            channels=ch,
            length_sec=60.0,
        )

    def test_copy_when_device_supports_mp3(self) -> None:
        d = decide_action(
            "song.mp3",
            {"mp3", "wma", "wav"},
            meta=self._meta(bitrate_bps=320000),
            preferred_settings=get_preset("mp3_cbr_320").settings
            if get_preset("mp3_cbr_320")
            else AudioEncodeSettings(bitrate_kbps=320, rate_control="cbr"),
        )
        self.assertEqual(d.action, "COPY")
        self.assertIn("supports", d.reason)

    def test_transcode_flac_on_zen_clamped_to_very_high(self) -> None:
        d = decide_action(
            "album.flac",
            {"mp3", "wma", "wav"},
            meta=TrackMetadata(
                title="T",
                bitrate=0,
                sample_rate=44100,
                channels=2,
            ),
            preferred_settings=None,
            target_format="mp3",
        )
        self.assertEqual(d.action, "TRANSCODE")
        self.assertEqual(d.reason, "device incompatibility")
        assert d.settings is not None
        self.assertEqual(d.settings.normalized_format(), "mp3")
        est = estimate_settings_bitrate_kbps(d.settings)
        self.assertIsNotNone(est)
        assert est is not None
        self.assertLessEqual(est, 320)

    def test_lossy_source_high_settings_clamped(self) -> None:
        # 128 kbps AAC → device needs MP3; config wants 320 → clamp.
        high = get_preset("mp3_cbr_320")
        assert high is not None
        d = decide_action(
            "clip.m4a",
            {"mp3", "wma", "wav"},
            meta=self._meta(bitrate_bps=128000),
            preferred_settings=high.settings,
        )
        self.assertEqual(d.action, "TRANSCODE")
        assert d.settings is not None
        est = estimate_settings_bitrate_kbps(d.settings)
        assert est is not None
        self.assertLessEqual(est, 128 + 16)

    def test_speech_preset_on_native_mp3_transcodes(self) -> None:
        speech = get_preset("mp3_cbr_32_mono")
        assert speech is not None
        d = decide_action(
            "pod.mp3",
            {"mp3", "wma", "wav"},
            meta=self._meta(bitrate_bps=320000),
            preferred_settings=speech.settings,
        )
        self.assertEqual(d.action, "TRANSCODE")
        self.assertIn("below source", d.reason)

    def test_never_lossy_to_lossless(self) -> None:
        flac = get_preset("flac_16_44")
        assert flac is not None
        d = decide_action(
            "talk.mp3",
            {"mp3", "flac", "wav"},
            meta=self._meta(bitrate_bps=160000),
            preferred_settings=flac.settings,
            force_transcode=True,
        )
        # force with lossless settings on lossy source → clamp away from FLAC
        if d.action == "TRANSCODE":
            assert d.settings is not None
            self.assertNotEqual(d.settings.rate_control, "lossless")
        else:
            self.assertEqual(d.action, "COPY")

    def test_force_transcode_same_quality_skipped(self) -> None:
        high = get_preset("mp3_cbr_320")
        assert high is not None
        d = decide_action(
            "a.mp3",
            {"mp3"},
            meta=self._meta(bitrate_bps=320000),
            preferred_settings=high.settings,
            force_transcode=True,
        )
        self.assertEqual(d.action, "COPY")

    def test_force_transcode_lower_quality_runs(self) -> None:
        low = get_preset("mp3_cbr_64")
        assert low is not None
        d = decide_action(
            "a.mp3",
            {"mp3"},
            meta=self._meta(bitrate_bps=320000),
            preferred_settings=low.settings,
            force_transcode=True,
        )
        self.assertEqual(d.action, "TRANSCODE")

    def test_tempo_always_transcodes(self) -> None:
        s = AudioEncodeSettings(
            format="mp3",
            rate_control="cbr",
            bitrate_kbps=192,
            playback_speed=1.5,
            label="1.5x",
        )
        d = decide_action(
            "a.mp3",
            {"mp3"},
            meta=self._meta(bitrate_bps=192000),
            preferred_settings=s,
            force_tempo=True,
        )
        self.assertEqual(d.action, "TRANSCODE")
        self.assertIn("speed", d.reason)

    def test_sample_rate_never_increases(self) -> None:
        src = source_metrics_from_meta(
            "x.mp3",
            TrackMetadata(bitrate=128000, sample_rate=22050, channels=1),
        )
        s = AudioEncodeSettings(
            format="mp3",
            rate_control="cbr",
            bitrate_kbps=96,
            sample_rate=44100,
        )
        out = clamp_settings_to_source(s, src)
        self.assertEqual(out.sample_rate, 22050)

    def test_settings_strictly_lower(self) -> None:
        src = source_metrics_from_meta(
            "x.mp3", TrackMetadata(bitrate=320000, sample_rate=44100, channels=2)
        )
        low = get_preset("mp3_cbr_64")
        assert low is not None
        self.assertTrue(settings_strictly_lower_quality(low.settings, src))
        high = get_preset("mp3_cbr_320")
        assert high is not None
        self.assertFalse(settings_strictly_lower_quality(high.settings, src))


class PrepareTrackQualityTests(unittest.TestCase):
    class _FakeTr:
        def __init__(self, tmp: str) -> None:
            self.tmp = tmp
            self.calls: list[tuple] = []

        def convert(
            self,
            src_path: str,
            target_format: str,
            *,
            slot: int = 0,
            settings=None,
            force: bool = False,
        ) -> str:
            out = os.path.join(self.tmp, f"TRANSCODE_{slot}.{target_format}")
            self.calls.append((src_path, target_format, settings, force))
            Path(out).write_text("x", encoding="utf-8")
            return out

        def cleanup(self, path: str | None) -> None:
            if path and os.path.isfile(path):
                os.remove(path)

    def test_prepare_passthrough_native_wma(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "clip.wma")
            Path(src).write_bytes(b"x")
            tr = self._FakeTr(tmp)
            prep = prepare_track(
                Track(
                    path=src,
                    meta=TrackMetadata(title="T", bitrate=128000),
                ),
                target_format="mp3",
                transcoder=tr,
                reread_tags_after_convert=False,
                device_formats=frozenset({"mp3", "wma", "wav"}),
            )
            self.assertEqual(tr.calls, [])
            self.assertEqual(prep.send_path, src)

    def test_prepare_downsize_native_mp3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "pod.mp3")
            Path(src).write_bytes(b"x")
            tr = self._FakeTr(tmp)
            speech = get_preset("mp3_cbr_32_mono")
            assert speech is not None
            prep = prepare_track(
                Track(
                    path=src,
                    meta=TrackMetadata(
                        title="T", bitrate=320000, sample_rate=44100, channels=2
                    ),
                ),
                target_format="mp3",
                transcoder=tr,
                reread_tags_after_convert=False,
                device_formats=frozenset({"mp3", "wma", "wav"}),
                encode_settings=speech.settings,
            )
            self.assertEqual(len(tr.calls), 1)
            self.assertTrue(tr.calls[0][3])  # force
            self.assertTrue(prep.send_path.endswith(".mp3"))
            tr.cleanup(prep.cleanup_path)

    def test_prepare_clamps_upgrade_from_low_aac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "low.m4a")
            Path(src).write_bytes(b"x")
            tr = self._FakeTr(tmp)
            high = get_preset("mp3_cbr_320")
            assert high is not None
            prep = prepare_track(
                Track(
                    path=src,
                    meta=TrackMetadata(title="T", bitrate=96000, sample_rate=44100),
                ),
                target_format="mp3",
                transcoder=tr,
                reread_tags_after_convert=False,
                device_formats=frozenset({"mp3", "wma", "wav"}),
                encode_settings=high.settings,
            )
            self.assertEqual(len(tr.calls), 1)
            settings = tr.calls[0][2]
            est = estimate_settings_bitrate_kbps(settings)
            self.assertIsNotNone(est)
            assert est is not None
            self.assertLessEqual(est, 96 + 16)
            tr.cleanup(prep.cleanup_path)


if __name__ == "__main__":
    unittest.main()
