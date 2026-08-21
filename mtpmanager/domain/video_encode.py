"""Video encode resolutions and recipe ⊕ resolution ⊕ audio helpers.

Send Video recipes (`VideoEncodePreset`) stay as container/codec tabs.
Geometry and audio quality are orthogonal axes applied at encode time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    clamp_settings_for_format,
    get_preset,
    resolve_settings,
)
from mtpmanager.domain.device_profile import VideoEncodePreset


@dataclass(frozen=True)
class VideoResolution:
    """Named frame size from the common catalog (or a device-specific alias)."""

    id: str
    width: int
    height: int
    display_name: str
    # Optional short hint shown in UI (e.g. "device screen", "A/V Out").
    label: str = ""

    def summary_line(self) -> str:
        if self.label:
            return f"{self.display_name} — {self.label}"
        return self.display_name


# ---------------------------------------------------------------------------
# Common resolution catalog (devices pick a subset via DeviceVideoOptions)
# ---------------------------------------------------------------------------

RES_QQVGA = VideoResolution(
    id="qqvga",
    width=160,
    height=120,
    display_name="160×120 (QQVGA)",
    label="low bitrate",
)
RES_QCIF = VideoResolution(
    id="qcif",
    width=176,
    height=144,
    display_name="176×144 (QCIF)",
)
RES_QVGA = VideoResolution(
    id="qvga",
    width=320,
    height=240,
    display_name="320×240 (QVGA)",
    label="device screen",
)
RES_CIF = VideoResolution(
    id="cif",
    width=352,
    height=288,
    display_name="352×288 (CIF)",
)
RES_VGA = VideoResolution(
    id="vga",
    width=640,
    height=480,
    display_name="640×480 (VGA)",
    label="A/V Out / retail",
)

COMMON_VIDEO_RESOLUTIONS: tuple[VideoResolution, ...] = (
    RES_QQVGA,
    RES_QCIF,
    RES_QVGA,
    RES_CIF,
    RES_VGA,
)

_RESOLUTIONS_BY_ID: dict[str, VideoResolution] = {
    r.id: r for r in COMMON_VIDEO_RESOLUTIONS
}


def resolution_by_id(resolution_id: str | None) -> VideoResolution | None:
    """Look up a catalog resolution by id (case-insensitive)."""
    if not resolution_id:
        return None
    return _RESOLUTIONS_BY_ID.get(str(resolution_id).strip().casefold())


def resolve_resolution(
    *,
    resolution_id: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fallback: VideoResolution | None = None,
) -> VideoResolution | None:
    """Resolve by id, else exact width×height match in the catalog."""
    by_id = resolution_by_id(resolution_id)
    if by_id is not None:
        return by_id
    if width and height and int(width) > 0 and int(height) > 0:
        w, h = int(width), int(height)
        for r in COMMON_VIDEO_RESOLUTIONS:
            if r.width == w and r.height == h:
                return r
    return fallback


def audio_formats_for_video_preset(preset: VideoEncodePreset) -> frozenset[str]:
    """Formats the audio picker may offer for *preset*'s container/recipe.

    AVI · mpeg4 (XviD/DivX) → MP3 only (reliable mux on ZEN).
    WMV / ASF → WMA only.
    Unknown containers → empty (caller should not offer a picker).
    """
    container = (preset.container or "").strip().casefold()
    probe_a = (preset.probe_audio_codec or "").strip().casefold()
    audio_codec = (preset.audio_codec or "").strip().casefold()

    if container in ("wmv", "asf") or probe_a in ("wmav2", "wma") or "wma" in audio_codec:
        return frozenset({"wma"})
    if container == "avi" or probe_a == "mp3" or "mp3" in audio_codec or "lame" in audio_codec:
        return frozenset({"mp3"})
    return frozenset()


def default_video_audio_settings(
    preset: VideoEncodePreset | None = None,
) -> AudioEncodeSettings:
    """Audio recipe matching a video preset's baked audio_* fields.

    Falls back to MP3 128 kbps CBR stereo 44.1 kHz (retail-like default).
    """
    if preset is None:
        p = get_preset("mp3_cbr_128")
        if p is not None:
            return clamp_settings_for_format(p.settings)
        return clamp_settings_for_format(
            AudioEncodeSettings(
                format="mp3",
                preset_id="mp3_cbr_128",
                rate_control="cbr",
                bitrate_kbps=128,
                vbr_quality=None,
                sample_rate=44100,
                channels=2,
                label="MP3 128 kbps CBR",
            )
        )

    allowed = audio_formats_for_video_preset(preset)
    fmt = "mp3"
    if "wma" in allowed and "mp3" not in allowed:
        fmt = "wma"
    elif allowed:
        # Prefer the preset's probe codec when it maps to an allowed format.
        probe = (preset.probe_audio_codec or "").casefold()
        if probe in ("wmav2", "wma") and "wma" in allowed:
            fmt = "wma"
        elif probe == "mp3" and "mp3" in allowed:
            fmt = "mp3"
        else:
            fmt = sorted(allowed)[0]

    br = _parse_bitrate_kbps(preset.audio_bitrate)
    sr = int(preset.audio_sample_rate) if preset.audio_sample_rate else 44100
    ch = int(preset.audio_channels) if preset.audio_channels in (1, 2) else 2

    if fmt == "wma":
        pid = f"wma_cbr_{br}" if br else "wma_cbr_128"
        catalog = get_preset(pid) or get_preset("wma_cbr_128")
        if catalog is not None:
            return resolve_settings(
                settings=replace(
                    catalog.settings,
                    bitrate_kbps=br or catalog.settings.bitrate_kbps,
                    sample_rate=sr,
                    channels=ch,
                ),
                allowed_formats=allowed or frozenset({"wma"}),
            )
        return clamp_settings_for_format(
            AudioEncodeSettings(
                format="wma",
                preset_id="custom",
                rate_control="cbr",
                bitrate_kbps=br or 128,
                sample_rate=sr,
                channels=ch,
                label=f"WMA {br or 128} kbps CBR",
            )
        )

    pid = f"mp3_cbr_{br}" if br else "mp3_cbr_128"
    catalog = get_preset(pid) or get_preset("mp3_cbr_128")
    if catalog is not None:
        return resolve_settings(
            settings=replace(
                catalog.settings,
                bitrate_kbps=br or catalog.settings.bitrate_kbps,
                sample_rate=sr,
                channels=ch,
            ),
            allowed_formats=allowed or frozenset({"mp3"}),
        )
    return clamp_settings_for_format(
        AudioEncodeSettings(
            format="mp3",
            preset_id="custom",
            rate_control="cbr",
            bitrate_kbps=br or 128,
            sample_rate=sr,
            channels=ch,
            label=f"MP3 {br or 128} kbps CBR",
        )
    )


def apply_resolution(
    preset: VideoEncodePreset,
    resolution: VideoResolution,
) -> VideoEncodePreset:
    """Return *preset* with frame size set to *resolution* (detail refreshed)."""
    w, h = int(resolution.width), int(resolution.height)
    detail = _video_detail_with_frame(preset, w, h)
    return replace(
        preset,
        width=w,
        height=h,
        video_detail=detail,
    )


def apply_audio_settings(
    preset: VideoEncodePreset,
    settings: AudioEncodeSettings,
) -> VideoEncodePreset:
    """Sync preset audio_* fields from *settings* (clamped to recipe formats)."""
    allowed = audio_formats_for_video_preset(preset)
    s = resolve_settings(
        settings=settings,
        allowed_formats=allowed if allowed else None,
    )
    s = clamp_settings_for_format(s)
    fmt = s.normalized_format()
    if allowed and fmt not in allowed:
        # Fall back to recipe default when clamp could not land in-set.
        s = default_video_audio_settings(preset)
        fmt = s.normalized_format()

    codec, probe = _ffmpeg_audio_codec_for_format(fmt)
    br = s.bitrate_kbps
    br_s = f"{int(br)}k" if br else preset.audio_bitrate or "128k"
    sr = int(s.sample_rate) if s.sample_rate and int(s.sample_rate) > 0 else (
        int(preset.audio_sample_rate) or 44100
    )
    ch = int(s.channels) if s.channels in (1, 2) else (
        int(preset.audio_channels) or 2
    )
    detail = s.summary_line()
    return replace(
        preset,
        audio_codec=codec,
        probe_audio_codec=probe,
        audio_bitrate=br_s,
        audio_sample_rate=sr,
        audio_channels=ch,
        audio_detail=f"Audio: {detail}",
    )


def effective_video_preset(
    preset: VideoEncodePreset,
    *,
    resolution: VideoResolution | None = None,
    audio_settings: AudioEncodeSettings | None = None,
) -> VideoEncodePreset:
    """Apply optional resolution and audio axes onto a recipe preset."""
    out = preset
    if resolution is not None:
        out = apply_resolution(out, resolution)
    if audio_settings is not None:
        out = apply_audio_settings(out, audio_settings)
    return out


def _parse_bitrate_kbps(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip().casefold().rstrip("k")
    try:
        n = int(float(text))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _ffmpeg_audio_codec_for_format(fmt: str) -> tuple[str, str]:
    """Return (ffmpeg -c:a, ffprobe codec_name) for a send format."""
    key = (fmt or "mp3").lower().lstrip(".")
    if key == "wma":
        return "wmav2", "wmav2"
    if key == "wav":
        return "pcm_s16le", "pcm_s16le"
    return "libmp3lame", "mp3"


def _video_detail_with_frame(
    preset: VideoEncodePreset, width: int, height: int
) -> str:
    """Rebuild video_detail with the active frame size."""
    tag = f" · FourCC {preset.video_tag}" if preset.video_tag else ""
    codec = preset.video_codec or "mpeg4"
    if preset.video_tag and preset.video_tag.upper() == "XVID":
        head = "Video: MPEG-4 Part 2 Simple Profile"
    elif preset.video_tag and preset.video_tag.upper() in ("DX50", "DIVX"):
        head = "Video: MPEG-4 Part 2 (DivX-style)"
    elif (preset.probe_video_codec or "").casefold() == "wmv2":
        head = "Video: WMV2 (Windows Media Video)"
    else:
        head = f"Video: {codec}"
    quality = ""
    if preset.qscale_v is not None:
        quality = f" · qscale {preset.qscale_v}"
    elif preset.video_bitrate:
        quality = f" · {preset.video_bitrate}"
    return (
        f"{head}{tag} · {width}×{height} pad{quality} · yuv420p"
    )
