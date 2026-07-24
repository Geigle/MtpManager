"""Built-in device profile registry (extend here for new players)."""

from __future__ import annotations

from mtpmanager.domain.device_profile import (
    DeviceProfile,
    DeviceVideoOptions,
    VideoEncodePreset,
)

# ---------------------------------------------------------------------------
# Creative ZEN Vision:M — theoretical / marketed video recipes
# (AVI·XviD/DivX·MP3, WMV·WMA; retail demos used AVI+XVID+MP3).
# max_fps=30 is ZEN-specific; generic has no video_options.
# ---------------------------------------------------------------------------

ZEN_AVI_XVID_MP3 = VideoEncodePreset(
    id="zen_avi_xvid_mp3",
    tab_label="AVI · XviD · MP3",
    display_name="AVI · MPEG-4 SP / XviD · MP3",
    container="avi",
    probe_containers=("avi",),
    video_codec="mpeg4",
    video_tag="XVID",
    acceptable_video_tags=("XVID",),
    probe_video_codec="mpeg4",
    width=640,
    height=480,
    max_fps=30.0,
    qscale_v=5,
    video_bitrate=None,
    audio_codec="libmp3lame",
    probe_audio_codec="mp3",
    audio_bitrate="128k",
    audio_sample_rate=44100,
    audio_channels=2,
    container_detail="Container: AVI (RIFF)",
    video_detail=(
        "Video: MPEG-4 Part 2 Simple Profile · FourCC XVID · "
        "640×480 pad · qscale 5 · yuv420p"
    ),
    audio_detail="Audio: MP3 (CBR) · 128 kbps · 44.1 kHz · stereo",
    summary="Default retail-like path: AVI + XviD + MP3",
    experimental=False,
)

ZEN_AVI_DIVX_MP3 = VideoEncodePreset(
    id="zen_avi_divx_mp3",
    tab_label="AVI · DivX · MP3",
    display_name="AVI · DivX · MP3",
    container="avi",
    probe_containers=("avi",),
    video_codec="mpeg4",
    video_tag="DX50",
    acceptable_video_tags=("DX50", "DIVX"),
    probe_video_codec="mpeg4",
    width=640,
    height=480,
    max_fps=30.0,
    qscale_v=5,
    video_bitrate=None,
    audio_codec="libmp3lame",
    probe_audio_codec="mp3",
    audio_bitrate="128k",
    audio_sample_rate=44100,
    audio_channels=2,
    container_detail="Container: AVI (RIFF)",
    video_detail=(
        "Video: MPEG-4 Part 2 (DivX-style) · FourCC DX50 · "
        "640×480 pad · qscale 5 · yuv420p"
    ),
    audio_detail="Audio: MP3 (CBR) · 128 kbps · 44.1 kHz · stereo",
    summary="AVI + DivX FourCC + MP3 (also seen on stock promos)",
    experimental=False,
)

ZEN_WMV_WMA = VideoEncodePreset(
    id="zen_wmv_wma",
    tab_label="WMV · WMA (broken)",
    display_name="WMV · WMA (broken)",
    container="wmv",
    probe_containers=("asf", "wmv"),
    video_codec="wmv2",
    video_tag="",
    acceptable_video_tags=(),
    probe_video_codec="wmv2",
    width=640,
    height=480,
    max_fps=30.0,
    qscale_v=None,
    video_bitrate="480k",
    audio_codec="wmav2",
    probe_audio_codec="wmav2",
    audio_bitrate="128k",
    audio_sample_rate=44100,
    audio_channels=2,
    container_detail="Container: WMV / ASF",
    video_detail=(
        "Video: WMV2 (Windows Media Video) · 480 kbps · "
        "640×480 pad · yuv420p"
    ),
    audio_detail="Audio: WMA v2 · 128 kbps · 44.1 kHz · stereo",
    summary="Broken — does not play reliably; enable in Config to show",
    experimental=True,
    broken=True,
)

# Default preset alias (tests / simple imports).
ZEN_VISION_M_VIDEO = ZEN_AVI_XVID_MP3

ZEN_VISION_M_VIDEO_OPTIONS = DeviceVideoOptions(
    device_display_name="Creative ZEN Vision:M",
    presets=(
        ZEN_AVI_XVID_MP3,
        ZEN_AVI_DIVX_MP3,
        ZEN_WMV_WMA,
    ),
    default_preset_id=ZEN_AVI_XVID_MP3.id,
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
    video_options=ZEN_VISION_M_VIDEO_OPTIONS,
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
    video_options=None,
)

BUILTIN_PROFILES: tuple[DeviceProfile, ...] = (
    ZEN_VISION_M,
    GENERIC,
)
