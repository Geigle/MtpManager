"""Device profile model and matching (stdlib only, no I/O)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from mtpmanager.domain.library import extension_of
from mtpmanager.domain.models import DeviceInfo


@dataclass(frozen=True)
class VideoEncodePreset:
    """One mutually exclusive Send Video encode recipe (one notebook tab).

    Frame rate defaults to **source as-is**. Set *max_fps* > 0 to cap only
    when the source is higher (e.g. ZEN Vision:M 60 → 30). ``max_fps=0``
    means no cap.
    """

    id: str
    display_name: str
    # Short tab label (mutually exclusive choice). Defaults to display_name.
    tab_label: str = ""
    # Output container extension (no dot), e.g. "avi" or "wmv".
    container: str = "avi"
    # ffprobe format_name tokens that count as this container.
    probe_containers: tuple[str, ...] = ("avi",)
    # Video encoder name for ffmpeg (-c:v).
    video_codec: str = "mpeg4"
    # FourCC / -vtag when needed (XVID, DX50); empty to omit.
    video_tag: str = ""
    # Acceptable tags when deciding "already device-ready" (empty = any).
    acceptable_video_tags: tuple[str, ...] = ()
    # libavcodec name expected on already-good files.
    probe_video_codec: str = "mpeg4"
    # Target geometry (letterbox/pad into this frame).
    width: int = 640
    height: int = 480
    # Cap when source exceeds this (fps). 0 = always keep source rate.
    max_fps: float = 0.0
    # Quality scale for mpeg4-style encoders (None = use video_bitrate).
    qscale_v: int | None = 5
    # Constant bitrate for video when not using qscale (e.g. "800k").
    video_bitrate: str | None = None
    # Audio encoder / probe names.
    audio_codec: str = "libmp3lame"
    probe_audio_codec: str = "mp3"
    audio_bitrate: str = "128k"
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    # Human-readable sections for the dialog.
    container_detail: str = ""
    video_detail: str = ""
    audio_detail: str = ""
    summary: str = ""
    experimental: bool = False

    def detail_lines(self) -> list[str]:
        """Lines for the options panel (container / video / audio)."""
        lines: list[str] = []
        c = self.container_detail or f"Container: {self.container.upper()}"
        lines.append(c)
        v = self.video_detail or (
            f"Video: {self.video_codec}"
            + (f" ({self.video_tag})" if self.video_tag else "")
            + f" · {self.width}×{self.height}"
        )
        lines.append(v)
        a = self.audio_detail or (
            f"Audio: {self.probe_audio_codec.upper()} "
            f"{self.audio_bitrate} · {self.audio_sample_rate} Hz · "
            f"{self.audio_channels}ch"
        )
        lines.append(a)
        if self.max_fps and self.max_fps > 0:
            lines.append(f"Frame rate: keep source if ≤ {self.max_fps:g} fps, else cap")
        else:
            lines.append("Frame rate: keep source")
        if self.experimental:
            lines.append("⚠ Experimental — may not play on device")
        return lines


# Back-compat name used in older call sites / tests.
VideoEncodeProfile = VideoEncodePreset


@dataclass(frozen=True)
class DeviceVideoOptions:
    """Device-specific Send Video options (absent on the generic profile).

    *presets* are mutually exclusive encode recipes shown as notebook tabs.
    """

    device_display_name: str
    presets: tuple[VideoEncodePreset, ...]
    default_preset_id: str

    def default_preset(self) -> VideoEncodePreset:
        p = self.preset_by_id(self.default_preset_id)
        if p is not None:
            return p
        if self.presets:
            return self.presets[0]
        raise ValueError(f"no video presets for {self.device_display_name}")

    def preset_by_id(self, preset_id: str | None) -> VideoEncodePreset | None:
        if not preset_id:
            return None
        for p in self.presets:
            if p.id == preset_id:
                return p
        return None


@dataclass(frozen=True)
class DeviceProfile:
    """Describes a known player family for UI and device-specific behavior.

    Needle groups use casefold substring match. An empty needle tuple means
    that field is ignored. Non-empty groups must all match (AND).

    *supported_audio_formats* lists extensions the player can play natively
    (lowercase, no dot). Sources already in these formats are sent as-is
    instead of re-encoding to the user's preferred target format.

    *video_options*, when set, is the Device → Send Video notebook of encode
    presets for this player (e.g. ZEN Vision:M). Generic has None.
    """

    id: str
    display_name: str
    manufacturer_needles: tuple[str, ...]
    model_needles: tuple[str, ...]
    name_needles: tuple[str, ...] = ()
    graphic_filename: str = "generic_player.png"
    # Native playable audio extensions (e.g. frozenset({"mp3", "wma", "wav"})).
    supported_audio_formats: frozenset[str] = frozenset({"mp3"})
    video_options: DeviceVideoOptions | None = None

    @property
    def video_encode(self) -> VideoEncodePreset | None:
        """Default encode preset when present (compat with older call sites)."""
        if self.video_options is None:
            return None
        return self.video_options.default_preset()

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
