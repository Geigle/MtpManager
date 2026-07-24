"""Device profile model and matching (stdlib only, no I/O)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from mtpmanager.domain.library import extension_of
from mtpmanager.domain.models import DeviceInfo


@dataclass(frozen=True)
class VideoEncodeProfile:
    """How to re-encode host video for a picky DAP (Device → Send Video).

    Values mirror measured Creative retail demos for ZEN Vision:M:
    AVI + MPEG-4 Part 2 (XVID/DX50) + single MP3 @ 44.1 kHz stereo,
    typically 640×480 (demos are 25 or ~29.97 fps). Encoding uses stock
    ffmpeg ``mpeg4`` + ``-vtag XVID`` (no libxvid required).

    Frame rate defaults to **source as-is**. Set *max_fps* > 0 on a
    device-specific profile (e.g. ZEN Vision:M) to cap only when the source
    is higher (60 → 30). ``max_fps=0`` means no cap.
    """

    id: str
    display_name: str
    # Output container extension (no dot), e.g. "avi".
    container: str = "avi"
    # Video encoder name for ffmpeg (-c:v).
    video_codec: str = "mpeg4"
    # FourCC / -vtag (demos use XVID; DX50 also seen).
    video_tag: str = "XVID"
    # Acceptable tags when deciding "already device-ready" (casefold).
    acceptable_video_tags: tuple[str, ...] = ("XVID", "DX50")
    # libavcodec name expected on already-good files (mpeg4).
    probe_video_codec: str = "mpeg4"
    # Target geometry (letterbox/pad into this frame).
    width: int = 640
    height: int = 480
    # Cap when source exceeds this (fps). 0 = always keep source rate.
    max_fps: float = 0.0
    # Quality scale for mpeg4 (lower = better; demos ~1–3 Mbps).
    qscale_v: int = 5
    # Audio: demos are MP3 stereo 44.1 kHz ~128k.
    audio_codec: str = "libmp3lame"
    probe_audio_codec: str = "mp3"
    audio_bitrate: str = "128k"
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    # Short summary for UI checkbox help.
    summary: str = (
        "AVI · MPEG-4/XVID · 640×480 · source fps · MP3 stereo 44.1 kHz"
    )


@dataclass(frozen=True)
class DeviceProfile:
    """Describes a known player family for UI and device-specific behavior.

    Needle groups use casefold substring match. An empty needle tuple means
    that field is ignored. Non-empty groups must all match (AND).

    *supported_audio_formats* lists extensions the player can play natively
    (lowercase, no dot). Sources already in these formats are sent as-is
    instead of re-encoding to the user's preferred target format.

    *video_encode*, when set, is the Device → Send Video encode profile
    for this player (e.g. ZEN Vision:M retail-demo AVI fingerprint).
    """

    id: str
    display_name: str
    manufacturer_needles: tuple[str, ...]
    model_needles: tuple[str, ...]
    name_needles: tuple[str, ...] = ()
    graphic_filename: str = "generic_player.png"
    # Native playable audio extensions (e.g. frozenset({"mp3", "wma", "wav"})).
    supported_audio_formats: frozenset[str] = frozenset({"mp3"})
    video_encode: VideoEncodeProfile | None = None

    def accepts_audio_format(self, fmt: str) -> bool:
        """True if *fmt* (extension or dotted) is natively playable."""
        key = (fmt or "").lower().lstrip(".")
        return key in self.supported_audio_formats

    def accepts_source_path(self, path: str) -> bool:
        """True if the file extension is natively playable on this device."""
        return extension_of(path) in self.supported_audio_formats


def normalize_audio_formats(formats: Collection[str] | None) -> frozenset[str]:
    """Lowercase, strip dots; empty/None → empty set."""
    if not formats:
        return frozenset()
    return frozenset(f.lower().lstrip(".") for f in formats if f)


def needs_transcode(
    path: str,
    *,
    target_format: str,
    device_formats: Collection[str] | None = None,
) -> bool:
    """True when *path* must be converted before send.

    Skip convert when the source is already in a device-native format
    (avoids lossy→lossy re-encodes), or when it already matches *target_format*.
    """
    ext = extension_of(path)
    if not ext:
        return True
    allowed = normalize_audio_formats(device_formats)
    if ext in allowed:
        return False
    target = (target_format or "").lower().lstrip(".")
    return ext != target


def _field_matches(value: str, needles: tuple[str, ...]) -> bool:
    if not needles:
        return True
    hay = (value or "").casefold()
    return any(n.casefold() in hay for n in needles if n)


def profile_matches(info: DeviceInfo, profile: DeviceProfile) -> bool:
    """True if *info* satisfies all non-empty needle groups on *profile*."""
    return (
        _field_matches(info.manufacturer, profile.manufacturer_needles)
        and _field_matches(info.model, profile.model_needles)
        and _field_matches(info.name, profile.name_needles)
    )


def match_device_profile(
    info: DeviceInfo,
    profiles: Sequence[DeviceProfile],
    *,
    fallback: DeviceProfile | None = None,
) -> DeviceProfile:
    """Return the first matching profile, or *fallback* / last generic."""
    for profile in profiles:
        if profile.id == "generic":
            continue
        if profile_matches(info, profile):
            return profile
    if fallback is not None:
        return fallback
    for profile in profiles:
        if profile.id == "generic":
            return profile
    if profiles:
        return profiles[-1]
    raise ValueError("no device profiles registered")
