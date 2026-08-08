"""Unit tests for audio encode presets and ffmpeg option mapping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    default_audio_encode_settings,
    formats_allowed,
    get_preset,
    presets_for_format,
    resolve_settings,
    settings_from_legacy_format,
)
from mtpmanager.domain.device_profiles import GENERIC, ZEN_VISION_M
from mtpmanager.infra.app_config import AppConfig, load_app_config, save_app_config
from mtpmanager.infra.ffmpeg_transcode import build_ffmpeg_audio_options


class AudioEncodeCatalogTests(unittest.TestCase):
    def test_mp3_ladder_low_to_high(self) -> None:
        ladder = presets_for_format("mp3")
        self.assertGreaterEqual(len(ladder), 8)
        ranks = [p.rank for p in ladder]
        self.assertEqual(ranks, sorted(ranks))
        self.assertTrue(any("32" in p.display_name for p in ladder))
        self.assertTrue(any("320" in p.display_name for p in ladder))

    def test_formats_unrestricted(self) -> None:
        fmts = formats_allowed(None)
        self.assertIn("mp3", fmts)
        self.assertIn("flac", fmts)
        self.assertIn("ogg", fmts)
        self.assertIn("m4a", fmts)

    def test_formats_zen_restricted(self) -> None:
        allowed = ZEN_VISION_M.send_formats_for_config()
        self.assertEqual(allowed, frozenset({"mp3", "wma", "wav"}))
        fmts = formats_allowed(allowed)
        self.assertEqual(set(fmts), {"mp3", "wma", "wav"})
        self.assertNotIn("flac", fmts)

    def test_generic_unrestricted_send(self) -> None:
        self.assertIsNone(GENERIC.send_formats_for_config())

    def test_resolve_clamps_to_zen(self) -> None:
        flac = get_preset("flac_16_44")
        assert flac is not None
        s = resolve_settings(
            settings=flac.settings,
            allowed_formats=ZEN_VISION_M.allowed_send_formats,
        )
        self.assertIn(s.normalized_format(), {"mp3", "wma", "wav"})

    def test_legacy_format_mapping(self) -> None:
        s = settings_from_legacy_format("wma")
        self.assertEqual(s.normalized_format(), "wma")
        self.assertEqual(s.rate_control, "cbr")
        self.assertEqual(default_audio_encode_settings().normalized_format(), "mp3")

    def test_settings_round_trip_dict(self) -> None:
        p = get_preset("mp3_cbr_192")
        assert p is not None
        d = p.settings.to_dict()
        back = AudioEncodeSettings.from_dict(d)
        self.assertEqual(back.normalized_format(), "mp3")
        self.assertEqual(back.bitrate_kbps, 192)
        self.assertEqual(back.rate_control, "cbr")


class FFmpegOptionsTests(unittest.TestCase):
    def test_mp3_vbr(self) -> None:
        p = get_preset("mp3_vbr_q0")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "libmp3lame")
        self.assertEqual(opts["qscale:a"], "0")
        # Cover-art / attached-pic streams must not be mapped into temps.
        self.assertEqual(opts.get("map"), "0:a:0")
        self.assertIn("vn", opts)
        self.assertEqual(opts.get("map_metadata"), "-1")

    def test_mp3_cbr(self) -> None:
        p = get_preset("mp3_cbr_128")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["b:a"], "128k")
        self.assertEqual(opts.get("ac"), "2")
        self.assertEqual(opts.get("map"), "0:a:0")

    def test_wma(self) -> None:
        p = get_preset("wma_cbr_128")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "wmav2")
        self.assertEqual(opts["b:a"], "128k")

    def test_wav_24bit(self) -> None:
        p = get_preset("wav_pcm_24_44")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "pcm_s24le")
        self.assertEqual(opts["ar"], "44100")

    def test_flac(self) -> None:
        p = get_preset("flac_16_best")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "flac")
        self.assertEqual(opts["compression_level"], "12")

    def test_ogg_vorbis(self) -> None:
        p = get_preset("ogg_vbr_q4")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "libvorbis")
        self.assertEqual(opts["q:a"], "4.0")

    def test_opus(self) -> None:
        p = get_preset("opus_vbr_128")
        assert p is not None
        opts = build_ffmpeg_audio_options(p.settings)
        self.assertEqual(opts["codec:a"], "libopus")
        self.assertEqual(opts["b:a"], "128k")


class AppConfigAudioEncodeTests(unittest.TestCase):
    def test_save_load_encode(self) -> None:
        p = get_preset("mp3_cbr_320")
        assert p is not None
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            cfg = AppConfig()
            cfg.apply_audio_encode(p.settings)
            save_app_config(cfg, path=dest)
            loaded = load_app_config(path=dest)
            self.assertEqual(loaded.normalized_send_format(), "mp3")
            enc = loaded.resolved_audio_encode()
            self.assertEqual(enc.preset_id, "mp3_cbr_320")
            self.assertEqual(enc.bitrate_kbps, 320)

    def test_legacy_config_without_audio_encode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text(
                '{\n  "version": 1,\n  "send_format": "wma"\n}\n',
                encoding="utf-8",
            )
            loaded = load_app_config(path=dest)
            self.assertEqual(loaded.normalized_send_format(), "wma")
            self.assertEqual(loaded.resolved_audio_encode().normalized_format(), "wma")


if __name__ == "__main__":
    unittest.main()
