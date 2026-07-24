"""Built-in device profile registry (extend here for new players)."""

from __future__ import annotations

from mtpmanager.domain.device_profile import DeviceProfile, VideoEncodeProfile

# Retail demo fingerprint for ZEN Vision:M (measured from stock Creative AVIs):
# container AVI, MPEG-4 Part 2 XVID (or DX50), yuv420p, usually 640×480@25,
# single MP3 stereo 44.1 kHz. Encode path uses stock ffmpeg mpeg4 + vtag XVID
# (no libxvid). See Device → Send Video and docs/transfer-and-modes.md.
ZEN_VISION_M_VIDEO = VideoEncodeProfile(
    id="zen_vision_m_retail_avi",
    display_name="ZEN Vision:M retail AVI",
    container="avi",
    video_codec="mpeg4",
    video_tag="XVID",
    acceptable_video_tags=("XVID", "DX50"),
    probe_video_codec="mpeg4",
    width=640,
    height=480,
    fps=25.0,
    qscale_v=5,
    audio_codec="libmp3lame",
    probe_audio_codec="mp3",
    audio_bitrate="128k",
    audio_sample_rate=44100,
    audio_channels=2,
    summary="AVI · XVID · 640×480 @ 25 fps · MP3 stereo 44.1 kHz (retail demos)",
)

# First match wins (excluding generic, which is always fallback).
ZEN_VISION_M = DeviceProfile(
    id="creative_zen_vision_m",
    display_name="Creative ZEN Vision:M",
    manufacturer_needles=("creative",),
    model_needles=("vision:m", "vision m", "zen vision"),
    name_needles=(),
    graphic_filename="zen_vision_m.png",
    # Official Creative formats for Vision:M: MP3, WMA, WAV (PCM).
    supported_audio_formats=frozenset({"mp3", "wma", "wav"}),
    video_encode=ZEN_VISION_M_VIDEO,
)

GENERIC = DeviceProfile(
    id="generic",
    display_name="MTP Player",
    manufacturer_needles=(),
    model_needles=(),
    name_needles=(),
    graphic_filename="generic_player.png",
    # Conservative default when the player is unknown.
    supported_audio_formats=frozenset({"mp3"}),
)

BUILTIN_PROFILES: tuple[DeviceProfile, ...] = (
    ZEN_VISION_M,
    GENERIC,
)
