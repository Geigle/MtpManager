"""Quality-aware encode policy for MTP sync (stdlib + domain only).

Source measured quality is a hard ceiling: never re-encode in a way that
claims higher fidelity than the source contains. Prefer COPY when the device
already plays the file; TRANSCODE only for compatibility or explicit lower-
quality normalization (Shrink, speech presets, tempo).

See comparable-format quality map in module constants and unit tests.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Literal

from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    clamp_settings_for_format,
    closest_preset_for_bitrate,
    default_audio_encode_settings,
    estimate_settings_bitrate_kbps,
)
from mtpmanager.domain.device_profile import normalize_audio_formats
from mtpmanager.domain.library import extension_of
from mtpmanager.domain.models import TrackMetadata

# ---------------------------------------------------------------------------
# Quality tiers (ordered low → high). Higher int = more information claimed.
# ---------------------------------------------------------------------------


class QualityTier(IntEnum):
    SPEECH = 10  # ~64–96 kbps MP3 class
    LOW_MEDIUM = 20  # ~128 kbps MP3
    MEDIUM = 30  # ~160–192 kbps MP3
    HIGH = 40  # ~256 kbps MP3
    VERY_HIGH = 50  # ~320 kbps MP3
    LOSSLESS = 100  # full PCM / lossless compressed


# Approximate MP3-equivalent ceiling (kbps) per tier — for mapping only.
TIER_CEILING_KBPS: Mapping[QualityTier, int] = {
    QualityTier.SPEECH: 96,
    QualityTier.LOW_MEDIUM: 128,
    QualityTier.MEDIUM: 192,
    QualityTier.HIGH: 256,
    QualityTier.VERY_HIGH: 320,
    QualityTier.LOSSLESS: 1411,  # CD PCM ballpark; not used as a lossy target
}

# Per-codec target bitrate (kbps) when encoding *at* a tier (lossy only).
# Prefer efficient codecs when the device whitelist allows them.
TIER_TARGET_KBPS: Mapping[QualityTier, Mapping[str, int]] = {
    QualityTier.SPEECH: {
        "mp3": 64,
        "wma": 64,
        "aac": 64,
        "m4a": 64,
        "ogg": 64,
        "opus": 48,
    },
    QualityTier.LOW_MEDIUM: {
        "mp3": 128,
        "wma": 128,
        "aac": 112,
        "m4a": 112,
        "ogg": 112,
        "opus": 80,
    },
    QualityTier.MEDIUM: {
        "mp3": 192,
        "wma": 160,
        "aac": 160,
        "m4a": 160,
        "ogg": 160,
        "opus": 112,
    },
    QualityTier.HIGH: {
        "mp3": 256,
        "wma": 192,
        "aac": 208,
        "m4a": 208,
        "ogg": 224,
        "opus": 144,
    },
    QualityTier.VERY_HIGH: {
        "mp3": 320,
        "wma": 256,
        "aac": 288,
        "m4a": 288,
        "ogg": 288,
        "opus": 176,
    },
}

# Codec preference when choosing among device-supported formats (lower = better).
_CODEC_EFFICIENCY_RANK: Mapping[str, int] = {
    "opus": 10,
    "aac": 20,
    "m4a": 20,
    "mp3": 30,
    "ogg": 40,
    "vorbis": 40,
    "wma": 50,
    "wav": 90,
    "flac": 80,
}

LOSSLESS_CODECS = frozenset(
    {"flac", "alac", "wav", "aiff", "aif", "pcm", "wv", "wavpack", "ape"}
)
LOSSY_CODECS = frozenset(
    {"mp3", "aac", "m4a", "ogg", "vorbis", "opus", "wma"}
)

EncodeAction = Literal["COPY", "REMUX", "TRANSCODE"]


@dataclass(frozen=True)
class SourceAudioMetrics:
    """Measured (or inferred) properties of one source file."""

    codec: str
    container: str
    bitrate_kbps: int | None
    sample_rate: int | None
    bit_depth: int | None
    channels: int | None
    duration_sec: float | None
    tier: QualityTier
    is_lossless: bool

    def summary(self) -> str:
        br = f"{self.bitrate_kbps}kbps" if self.bitrate_kbps else "br?"
        sr = f"{self.sample_rate}Hz" if self.sample_rate else "sr?"
        return (
            f"{self.codec}/{self.container} {br} {sr} "
            f"tier={self.tier.name} lossless={self.is_lossless}"
        )


@dataclass(frozen=True)
class DeviceCapabilities:
    """Declarative device audio support for decide_action.

    *formats*:
      - Non-empty: device-native playable extensions (e.g. ZEN mp3/wma/wav).
      - Empty: no whitelist known — a source is only treated as playable when
        it already matches the requested send *target_format* (legacy
        ``needs_transcode`` behavior when ``device_formats`` is None).
    """

    formats: frozenset[str]
    max_bitrate_kbps: int | None = None
    max_sample_rate: int | None = None

    @classmethod
    def from_formats(
        cls,
        formats: Collection[str] | None,
        *,
        max_bitrate_kbps: int | None = None,
        max_sample_rate: int | None = None,
    ) -> DeviceCapabilities:
        return cls(
            formats=normalize_audio_formats(formats),
            max_bitrate_kbps=max_bitrate_kbps,
            max_sample_rate=max_sample_rate,
        )

    def supports_codec(self, codec: str, *, target_format: str = "") -> bool:
        key = (codec or "").lower().lstrip(".")
        if not key:
            return False
        if self.formats:
            return key in self.formats
        # No whitelist: only passthrough when already the send target format.
        tgt = (target_format or "").lower().lstrip(".")
        return bool(tgt) and key == tgt


@dataclass(frozen=True)
class EncodeDecision:
    """Outcome of quality-aware prepare policy."""

    action: EncodeAction
    reason: str
    source: SourceAudioMetrics
    settings: AudioEncodeSettings | None = None
    target_format: str = ""
    expected_bitrate_kbps: int | None = None

    def log_line(self, path: str = "") -> str:
        tgt = ""
        if self.action == "TRANSCODE" and self.settings is not None:
            est = self.expected_bitrate_kbps
            br = f"~{est}kbps" if est else self.settings.summary_line()
            tgt = f" target={self.target_format or self.settings.normalized_format()} {br}"
        prefix = f"{path} " if path else ""
        return (
            f"{prefix}source=[{self.source.summary()}] "
            f"action={self.action} reason={self.reason!r}{tgt}"
        )


# ---------------------------------------------------------------------------
# Metrics / tier helpers
# ---------------------------------------------------------------------------


def normalize_bitrate_kbps(bitrate: int | float | None) -> int | None:
    """Convert mutagen/stream bitrate to kbps (handles bps values)."""
    if bitrate is None:
        return None
    try:
        n = int(bitrate)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # Stream info is almost always bits/sec when large.
    if n >= 1000:
        return max(1, n // 1000)
    return n


def is_lossless_codec(codec: str) -> bool:
    return (codec or "").lower().lstrip(".") in LOSSLESS_CODECS


def is_lossy_codec(codec: str) -> bool:
    return (codec or "").lower().lstrip(".") in LOSSY_CODECS


def tier_from_bitrate_kbps(
    bitrate_kbps: int | None,
    *,
    lossless: bool = False,
) -> QualityTier:
    """Map measured bitrate (or lossless flag) to a quality tier."""
    if lossless:
        return QualityTier.LOSSLESS
    if bitrate_kbps is None or bitrate_kbps <= 0:
        # No reliable bitrate → conservative Medium (do not assume Very High).
        return QualityTier.MEDIUM
    br = int(bitrate_kbps)
    if br <= 96:
        return QualityTier.SPEECH
    if br <= 128:
        return QualityTier.LOW_MEDIUM
    if br <= 192:
        return QualityTier.MEDIUM
    if br <= 256:
        return QualityTier.HIGH
    return QualityTier.VERY_HIGH


def tier_from_settings(settings: AudioEncodeSettings | None) -> QualityTier | None:
    """Infer claimed tier of an encode recipe (None if unset)."""
    if settings is None:
        return None
    s = clamp_settings_for_format(settings)
    if s.rate_control in ("lossless", "pcm"):
        return QualityTier.LOSSLESS
    est = estimate_settings_bitrate_kbps(s)
    return tier_from_bitrate_kbps(est, lossless=False)


def source_metrics_from_meta(
    path: str,
    meta: TrackMetadata | None = None,
) -> SourceAudioMetrics:
    """Build metrics from path extension + optional TrackMetadata (no I/O)."""
    container = extension_of(path) or "unknown"
    codec = container
    if codec == "vorbis":
        codec = "ogg"
    lossless = is_lossless_codec(codec)
    br = normalize_bitrate_kbps(getattr(meta, "bitrate", None) if meta else None)
    sr = int(getattr(meta, "sample_rate", 0) or 0) if meta else 0
    ch = int(getattr(meta, "channels", 0) or 0) if meta else 0
    dur = float(getattr(meta, "length_sec", 0) or 0) if meta else 0.0
    # Bit depth is not always on TrackMetadata; leave None for now.
    bit_depth = None
    tier = tier_from_bitrate_kbps(br, lossless=lossless)
    return SourceAudioMetrics(
        codec=codec,
        container=container,
        bitrate_kbps=br,
        sample_rate=sr if sr > 0 else None,
        bit_depth=bit_depth,
        channels=ch if ch > 0 else None,
        duration_sec=dur if dur > 0 else None,
        tier=tier,
        is_lossless=lossless,
    )


def min_tier(a: QualityTier, b: QualityTier) -> QualityTier:
    return a if int(a) <= int(b) else b


# ---------------------------------------------------------------------------
# Target settings construction under a ceiling
# ---------------------------------------------------------------------------


def target_bitrate_for_tier(fmt: str, tier: QualityTier) -> int:
    """kbps target for *fmt* at *tier* (lossy)."""
    if tier == QualityTier.LOSSLESS:
        # Lossy target from lossless source: Very High ceiling.
        tier = QualityTier.VERY_HIGH
    fmt = (fmt or "mp3").lower().lstrip(".")
    table = TIER_TARGET_KBPS.get(tier) or TIER_TARGET_KBPS[QualityTier.MEDIUM]
    if fmt in table:
        return int(table[fmt])
    if fmt == "vorbis":
        return int(table.get("ogg", 160))
    return int(table.get("mp3", TIER_CEILING_KBPS.get(tier, 192)))


def pick_target_format(
    device: DeviceCapabilities,
    *,
    preferred_format: str | None = None,
) -> str:
    """Best device-compatible lossy (or allowed) format for a convert."""
    preferred = (preferred_format or "").lower().lstrip(".")
    allowed = device.formats
    if preferred and (not allowed or preferred in allowed):
        return preferred
    if not allowed:
        return preferred or "mp3"
    # Prefer efficient lossy codecs the device accepts.
    candidates = sorted(
        allowed,
        key=lambda f: (
            _CODEC_EFFICIENCY_RANK.get(f, 100),
            f,
        ),
    )
    for c in candidates:
        if c in LOSSY_CODECS or c in ("wav", "flac"):
            # Prefer lossy for re-encode from lossy; wav/flac only if sole options.
            if c in LOSSY_CODECS:
                return c
    return candidates[0] if candidates else (preferred or "mp3")


def _settings_at_bitrate(
    fmt: str,
    bitrate_kbps: int,
    *,
    template: AudioEncodeSettings | None = None,
) -> AudioEncodeSettings:
    """Build clamped lossy settings near *bitrate_kbps* for *fmt*."""
    fmt = (fmt or "mp3").lower().lstrip(".")
    preset = closest_preset_for_bitrate(fmt, bitrate_kbps)
    if preset is not None:
        s = preset.settings
    else:
        s = AudioEncodeSettings(
            format=fmt,
            preset_id="custom",
            rate_control="cbr",
            bitrate_kbps=bitrate_kbps,
            vbr_quality=None,
            label=f"{fmt.upper()} ~{bitrate_kbps} kbps",
        )
    if template is not None:
        # Preserve intentional geometry/tempo from user recipe when not upsizing.
        s = replace(
            s,
            playback_speed=template.playback_speed,
            channels=template.channels if template.channels in (1, 2) else s.channels,
            sample_rate=template.sample_rate,
            label=template.label or s.label,
            preset_id=template.preset_id or s.preset_id,
        )
    return clamp_settings_for_format(s)


def clamp_settings_to_source(
    settings: AudioEncodeSettings,
    source: SourceAudioMetrics,
    *,
    device: DeviceCapabilities | None = None,
) -> AudioEncodeSettings:
    """Ensure *settings* do not claim more fidelity than *source*.

    - Lossy→lossless recipes become lossy at the source tier.
    - Bitrate/VBR quality is capped to the source tier ceiling.
    - Sample rate / bit depth never increase above measured source values.
    - Device max bitrate / max sample rate applied when provided.
    """
    s = clamp_settings_for_format(settings)
    fmt = s.normalized_format()
    ceiling_tier = source.tier
    if ceiling_tier == QualityTier.LOSSLESS and is_lossy_codec(fmt):
        # Lossless source → lossy target: allow up to Very High.
        ceiling_tier = QualityTier.VERY_HIGH
    elif source.is_lossless is False and s.rate_control in ("lossless", "pcm"):
        # Never "restore" lossy to lossless.
        fmt = pick_target_format(
            device or DeviceCapabilities(formats=frozenset()),
            preferred_format="mp3",
        )
        br = target_bitrate_for_tier(fmt, min_tier(source.tier, QualityTier.VERY_HIGH))
        s = _settings_at_bitrate(fmt, br, template=s)
        fmt = s.normalized_format()

    claimed = tier_from_settings(s)
    if claimed is not None and int(claimed) > int(ceiling_tier):
        br = target_bitrate_for_tier(fmt, ceiling_tier)
        if device and device.max_bitrate_kbps:
            br = min(br, int(device.max_bitrate_kbps))
        s = _settings_at_bitrate(fmt, br, template=s)

    # Explicit bitrate still above source measured kbps → hard clamp.
    if (
        not source.is_lossless
        and source.bitrate_kbps
        and s.rate_control in ("cbr", "abr", "vbr")
    ):
        est = estimate_settings_bitrate_kbps(s)
        cap = int(source.bitrate_kbps)
        if device and device.max_bitrate_kbps:
            cap = min(cap, int(device.max_bitrate_kbps))
        if est is not None and est > cap:
            s = _settings_at_bitrate(fmt, cap, template=s)

    # Never increase sample rate.
    if s.sample_rate and source.sample_rate:
        if int(s.sample_rate) > int(source.sample_rate):
            s = replace(s, sample_rate=int(source.sample_rate))
    if device and device.max_sample_rate and s.sample_rate:
        if int(s.sample_rate) > int(device.max_sample_rate):
            s = replace(s, sample_rate=int(device.max_sample_rate))

    # Never increase bit depth (when both known).
    if s.bit_depth and source.bit_depth:
        if int(s.bit_depth) > int(source.bit_depth):
            s = replace(s, bit_depth=int(source.bit_depth))

    return clamp_settings_for_format(s)


def settings_strictly_lower_quality(
    settings: AudioEncodeSettings,
    source: SourceAudioMetrics,
) -> bool:
    """True when *settings* claim a lower tier (or lower kbps) than *source*."""
    s = clamp_settings_for_format(settings)
    if s.rate_control in ("lossless", "pcm"):
        return False  # not a downsize of lossy
    st = tier_from_settings(s)
    if st is None:
        return False
    if int(st) < int(source.tier):
        return True
    if (
        not source.is_lossless
        and source.bitrate_kbps
        and int(st) == int(source.tier)
    ):
        est = estimate_settings_bitrate_kbps(s)
        # Require a clear downsize (~12% or 16 kbps) to avoid thrashing VBR.
        if est is not None and est + 16 < int(source.bitrate_kbps) * 0.9:
            return True
    # Geometry-only normalization (mono / lower rate) counts as lower.
    if s.channels == 1 and source.channels and source.channels > 1:
        return True
    if (
        s.sample_rate
        and source.sample_rate
        and int(s.sample_rate) < int(source.sample_rate)
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# decide_action
# ---------------------------------------------------------------------------


def decide_action(
    path: str,
    device: DeviceCapabilities | Collection[str] | None = None,
    *,
    meta: TrackMetadata | None = None,
    preferred_settings: AudioEncodeSettings | None = None,
    force_transcode: bool = False,
    force_tempo: bool = False,
    target_format: str | None = None,
) -> EncodeDecision:
    """Decide COPY vs TRANSCODE under the quality ceiling policy.

    Pure function of path extension, optional metrics, device whitelist, and
    user encode recipe. Does not touch the filesystem beyond *path* string use
    for extension.

    Parameters
    ----------
    path:
        Source file path (extension used as codec/container).
    device:
        :class:`DeviceCapabilities` or a collection of playable extensions.
        Empty/None formats = unrestricted.
    meta:
        Optional stream/tag metrics (bitrate, rate, channels, duration).
    preferred_settings:
        User/config encode recipe (may be clamped down, never up).
    force_transcode:
        Shrink / explicit re-encode request. Still will not up-convert.
    force_tempo:
        Playback-speed filter requires a convert; quality still clamped.
    target_format:
        Preferred container when settings are absent.
    """
    if isinstance(device, DeviceCapabilities):
        caps = device
    else:
        caps = DeviceCapabilities.from_formats(device)

    source = source_metrics_from_meta(path, meta)
    preferred = (
        clamp_settings_for_format(preferred_settings)
        if preferred_settings is not None
        else None
    )
    pref_fmt = (
        preferred.file_extension()
        if preferred is not None
        else (target_format or "mp3").lower().lstrip(".")
    )

    def _transcode(
        settings: AudioEncodeSettings,
        reason: str,
    ) -> EncodeDecision:
        clamped = clamp_settings_to_source(settings, source, device=caps)
        est = estimate_settings_bitrate_kbps(clamped)
        return EncodeDecision(
            action="TRANSCODE",
            reason=reason,
            source=source,
            settings=clamped,
            target_format=clamped.file_extension(),
            expected_bitrate_kbps=est,
        )

    def _copy(reason: str) -> EncodeDecision:
        return EncodeDecision(
            action="COPY",
            reason=reason,
            source=source,
            settings=None,
            target_format=source.container,
            expected_bitrate_kbps=source.bitrate_kbps,
        )

    # --- Tempo always needs a convert; still honor quality ceiling. ---
    if force_tempo:
        base = preferred or default_audio_encode_settings()
        if (
            not caps.supports_codec(
                base.normalized_format(), target_format=pref_fmt
            )
            and caps.formats
        ):
            base = replace(
                base,
                format=pick_target_format(
                    caps, preferred_format=base.normalized_format()
                ),
            )
            base = clamp_settings_for_format(base)
        return _transcode(base, "user normalization (playback speed)")

    device_ok = caps.supports_codec(source.codec, target_format=pref_fmt)

    # --- Explicit force (Shrink): only when settings reduce quality or
    # format must change; never up-convert. ---
    if force_transcode:
        if preferred is None:
            preferred = default_audio_encode_settings()
        if device_ok and not settings_strictly_lower_quality(preferred, source):
            # Would re-encode at same/higher quality for no gain.
            return _copy(
                "force_transcode ignored (settings do not lower quality; "
                "device supports source)"
            )
        return _transcode(
            preferred,
            "user normalization (forced re-encode / shrink)",
        )

    # --- Device plays source natively. ---
    if device_ok:
        if preferred is not None and settings_strictly_lower_quality(
            preferred, source
        ):
            # Podcast/speech preset or other intentional downsize.
            return _transcode(
                preferred,
                "user normalization (settings below source quality)",
            )
        return _copy("device supports source codec/container")

    # --- Compatibility convert. ---
    # Prefer user format if device allows it; else best efficient codec.
    out_fmt = pick_target_format(caps, preferred_format=pref_fmt)
    if preferred is not None:
        if preferred.normalized_format() == out_fmt or caps.supports_codec(
            preferred.normalized_format(), target_format=pref_fmt
        ):
            if caps.supports_codec(
                preferred.normalized_format(), target_format=pref_fmt
            ):
                out_fmt = preferred.normalized_format()
            recipe = preferred
        else:
            recipe = replace(preferred, format=out_fmt)
            recipe = clamp_settings_for_format(recipe)
    else:
        # Map source tier → target settings for out_fmt.
        map_tier = source.tier
        if map_tier == QualityTier.LOSSLESS:
            map_tier = QualityTier.VERY_HIGH
        br = target_bitrate_for_tier(out_fmt, map_tier)
        if caps.max_bitrate_kbps:
            br = min(br, int(caps.max_bitrate_kbps))
        recipe = _settings_at_bitrate(out_fmt, br)

    return _transcode(recipe, "device incompatibility")
