"""Audio transcoding settings and named presets (stdlib only).

Presets are ladders of quality per container/codec. Advanced/custom settings
map 1:1 to ffmpeg output options via ``infra.ffmpeg_transcode``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Formats the app can encode to (unrestricted / generic profile).
# Device profiles may restrict which of these appear in Config.
# ---------------------------------------------------------------------------

ALL_AUDIO_SEND_FORMATS: tuple[str, ...] = (
    "mp3",
    "wma",
    "wav",
    "flac",
    "aac",
    "m4a",
    "ogg",
    "opus",
)

# Display labels for UI.
FORMAT_LABELS: dict[str, str] = {
    "mp3": "MP3",
    "wma": "WMA",
    "wav": "WAV (PCM)",
    "flac": "FLAC",
    "aac": "AAC",
    "m4a": "M4A (AAC)",
    "ogg": "OGG Vorbis",
    "opus": "Opus",
}

RateControl = Literal["cbr", "vbr", "abr", "lossless", "pcm"]
ChannelMode = Literal["source", "mono", "stereo"]
SampleRateMode = Literal["source", "fixed"]

# Common sample rates offered in advanced UI.
SAMPLE_RATE_CHOICES: tuple[int, ...] = (
    8000,
    11025,
    16000,
    22050,
    32000,
    44100,
    48000,
    88200,
    96000,
)

# Bit depths for PCM / lossless.
BIT_DEPTH_CHOICES: tuple[int, ...] = (16, 24, 32)

# MP3 VBR quality 0 (best) … 9 (worst) — maps to ffmpeg -q:a.
MP3_VBR_QUALITY_RANGE = (0, 9)
# Vorbis/Opus-style quality scales used in UI (mapped in ffmpeg builder).
VORBIS_QUALITY_RANGE = (0, 10)
AAC_VBR_QUALITY_RANGE = (0.1, 2.0)


@dataclass(frozen=True)
class AudioEncodeSettings:
    """Full encode recipe for one convert (format + rate control + geometry).

    *preset_id* is a catalog key, or ``\"custom\"`` when the user edited
    advanced controls. Empty string is treated as default for the format.
    """

    format: str = "mp3"
    preset_id: str = "mp3_vbr_192"
    rate_control: RateControl = "vbr"
    # CBR/ABR target in kbps (ignored for pure VBR quality / lossless / PCM).
    bitrate_kbps: int | None = 192
    # Lossy VBR quality: codec-specific (MP3 0–9, Vorbis 0–10, AAC 0.1–2).
    vbr_quality: float | None = 2.0
    # Sample rate: None / 0 = keep source; else Hz.
    sample_rate: int | None = None
    # Channels: None = keep source; 1 mono; 2 stereo.
    channels: int | None = None
    # PCM / FLAC bit depth (16/24/32); ignored for lossy.
    bit_depth: int | None = 16
    # FLAC compression 0–12 (ffmpeg flac).
    compression_level: int | None = 5
    # Playback speed multiplier for encode-time tempo (1.0 = unchanged).
    # Applied via ffmpeg atempo; used for podcasts (and any convert with settings).
    playback_speed: float = 1.0
    # Human label for status / dialogs.
    label: str = "MP3 VBR ~192 kbps"

    def normalized_format(self) -> str:
        fmt = (self.format or "mp3").lower().lstrip(".")
        if fmt == "m4a":
            return "m4a"
        if fmt not in ALL_AUDIO_SEND_FORMATS and fmt != "m4a":
            return "mp3"
        return fmt

    def file_extension(self) -> str:
        """Container extension written to TRANSCODE_N.<ext>."""
        fmt = self.normalized_format()
        if fmt == "aac":
            # Raw ADTS AAC; m4a is preferred for players that want a box.
            return "aac"
        return fmt

    def normalized_playback_speed(self) -> float:
        """Clamped speed for ffmpeg atempo (1.0 when unset/invalid)."""
        return normalize_playback_speed(self.playback_speed)

    def needs_tempo_filter(self) -> bool:
        return abs(self.normalized_playback_speed() - 1.0) >= 0.01

    def summary_line(self) -> str:
        """One-line description for Config / status."""
        if self.label:
            base = self.label
        else:
            fmt = self.normalized_format().upper()
            parts = [fmt, self.rate_control.upper()]
            if self.rate_control in ("cbr", "abr") and self.bitrate_kbps:
                parts.append(f"{self.bitrate_kbps} kbps")
            elif self.rate_control == "vbr" and self.vbr_quality is not None:
                parts.append(f"q={self.vbr_quality:g}")
            if self.sample_rate:
                parts.append(f"{self.sample_rate} Hz")
            if self.channels == 1:
                parts.append("mono")
            elif self.channels == 2:
                parts.append("stereo")
            if self.bit_depth and self.rate_control in ("pcm", "lossless"):
                parts.append(f"{self.bit_depth}-bit")
            base = " · ".join(parts)
        if self.needs_tempo_filter():
            return f"{base} · {self.normalized_playback_speed():g}×"
        return base

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["format"] = self.normalized_format()
        return d

    @classmethod
    def from_dict(cls, raw: object | None) -> AudioEncodeSettings:
        if not isinstance(raw, dict):
            return default_audio_encode_settings()
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, val in raw.items():
            if key in known:
                kwargs[key] = val
        base = default_audio_encode_settings()
        try:
            fmt = str(kwargs.get("format") or base.format).lower().lstrip(".")
            preset_id = str(kwargs.get("preset_id") or "")
            rate = str(kwargs.get("rate_control") or base.rate_control).lower()
            if rate not in ("cbr", "vbr", "abr", "lossless", "pcm"):
                rate = base.rate_control
            bitrate = _opt_int(kwargs.get("bitrate_kbps"), base.bitrate_kbps)
            vbr_q = _opt_float(kwargs.get("vbr_quality"), base.vbr_quality)
            sr = _opt_int(kwargs.get("sample_rate"), None)
            if sr is not None and sr <= 0:
                sr = None
            ch = _opt_int(kwargs.get("channels"), None)
            if ch is not None and ch not in (1, 2):
                ch = None
            depth = _opt_int(kwargs.get("bit_depth"), base.bit_depth)
            if depth is not None and depth not in BIT_DEPTH_CHOICES:
                depth = 16
            comp = _opt_int(kwargs.get("compression_level"), base.compression_level)
            if comp is not None:
                comp = max(0, min(12, comp))
            speed = normalize_playback_speed(
                kwargs.get("playback_speed", base.playback_speed)
            )
            label = str(kwargs.get("label") or "").strip() or base.label
            settings = AudioEncodeSettings(
                format=fmt,
                preset_id=preset_id or "custom",
                rate_control=rate,  # type: ignore[arg-type]
                bitrate_kbps=bitrate,
                vbr_quality=vbr_q,
                sample_rate=sr,
                channels=ch,
                bit_depth=depth,
                compression_level=comp,
                playback_speed=speed,
                label=label,
            )
            return clamp_settings_for_format(settings)
        except Exception:
            return default_audio_encode_settings()


# ffmpeg atempo accepts 0.5–2.0 per filter; we chain for a wider UI range.
PLAYBACK_SPEED_MIN = 0.5
PLAYBACK_SPEED_MAX = 3.0


def normalize_playback_speed(value: object) -> float:
    """Return a safe playback-speed multiplier (default 1.0)."""
    try:
        s = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if s <= 0 or s != s:  # non-positive or NaN
        return 1.0
    return max(PLAYBACK_SPEED_MIN, min(PLAYBACK_SPEED_MAX, s))


def atempo_filter_chain(speed: float) -> str | None:
    """Build an ffmpeg audio filter chain for *speed*, or None at 1×."""
    s = normalize_playback_speed(speed)
    if abs(s - 1.0) < 0.01:
        return None
    factors: list[float] = []
    remaining = s
    # Each atempo stage must stay within [0.5, 2.0].
    while remaining > 2.0 + 1e-9:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={f:.6g}" for f in factors)


def _opt_int(value: object, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _opt_float(value: object, default: float | None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AudioEncodePreset:
    """Named step on a quality ladder for one format."""

    id: str
    format: str
    display_name: str
    # Sort key low → high quality within format.
    rank: int
    settings: AudioEncodeSettings
    blurb: str = ""

    def for_ui(self) -> str:
        return self.display_name


def clamp_settings_for_format(s: AudioEncodeSettings) -> AudioEncodeSettings:
    """Coerce rate_control / fields that do not apply to the container."""
    fmt = s.normalized_format()
    speed = normalize_playback_speed(s.playback_speed)
    if fmt in ("wav",):
        return replace(
            s,
            format=fmt,
            rate_control="pcm",
            bitrate_kbps=None,
            vbr_quality=None,
            compression_level=None,
            bit_depth=s.bit_depth if s.bit_depth in BIT_DEPTH_CHOICES else 16,
            playback_speed=speed,
        )
    if fmt == "flac":
        return replace(
            s,
            format=fmt,
            rate_control="lossless",
            bitrate_kbps=None,
            vbr_quality=None,
            compression_level=(
                s.compression_level
                if s.compression_level is not None
                else 5
            ),
            bit_depth=s.bit_depth if s.bit_depth in (16, 24) else 16,
            playback_speed=speed,
        )
    if fmt == "wma":
        # FFmpeg wmav2 is effectively CBR/ABR via bitrate.
        br = s.bitrate_kbps or 128
        return replace(
            s,
            format=fmt,
            rate_control="cbr",
            bitrate_kbps=max(32, min(320, br)),
            vbr_quality=None,
            compression_level=None,
            playback_speed=speed,
        )
    if fmt in ("mp3", "aac", "m4a", "ogg", "opus"):
        rc = s.rate_control
        if rc not in ("cbr", "vbr", "abr"):
            rc = "vbr" if fmt in ("mp3", "ogg", "opus") else "cbr"
        return replace(
            s,
            format=fmt,
            rate_control=rc,  # type: ignore[arg-type]
            compression_level=None,
            bit_depth=None,
            playback_speed=speed,
        )
    return replace(s, format=fmt, playback_speed=speed)


def default_audio_encode_settings() -> AudioEncodeSettings:
    """App default: good quality MP3 VBR (legacy ffmpeg qscale:a 0 ≈ best)."""
    return AudioEncodeSettings(
        format="mp3",
        preset_id="mp3_vbr_q0",
        rate_control="vbr",
        bitrate_kbps=None,
        vbr_quality=0.0,
        sample_rate=None,
        channels=None,
        bit_depth=None,
        compression_level=None,
        label="MP3 VBR max quality (q=0)",
    )


def settings_from_legacy_format(fmt: str) -> AudioEncodeSettings:
    """Map old config that only stored send_format to a sensible preset."""
    key = (fmt or "mp3").lower().lstrip(".")
    if key == "wma":
        return get_preset("wma_cbr_128").settings
    if key == "wav":
        return get_preset("wav_pcm_16_44").settings
    if key == "flac":
        return get_preset("flac_16_44").settings
    if key in ("aac", "m4a"):
        return get_preset("aac_cbr_192").settings
    if key == "ogg":
        return get_preset("ogg_vbr_q4").settings
    if key == "opus":
        return get_preset("opus_vbr_128").settings
    # Historical default was qscale:a 0 (best VBR).
    return default_audio_encode_settings()


# ---------------------------------------------------------------------------
# Preset catalog
# ---------------------------------------------------------------------------


def _mp3(
    pid: str,
    rank: int,
    name: str,
    *,
    rc: RateControl,
    bitrate: int | None = None,
    q: float | None = None,
    channels: int | None = None,
    sr: int | None = None,
    blurb: str = "",
) -> AudioEncodePreset:
    label = name
    return AudioEncodePreset(
        id=pid,
        format="mp3",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="mp3",
            preset_id=pid,
            rate_control=rc,
            bitrate_kbps=bitrate,
            vbr_quality=q,
            sample_rate=sr,
            channels=channels,
            bit_depth=None,
            compression_level=None,
            label=label,
        ),
    )


def _wma(
    pid: str,
    rank: int,
    name: str,
    *,
    bitrate: int,
    channels: int | None = 2,
    sr: int | None = 44100,
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format="wma",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="wma",
            preset_id=pid,
            rate_control="cbr",
            bitrate_kbps=bitrate,
            vbr_quality=None,
            sample_rate=sr,
            channels=channels,
            bit_depth=None,
            compression_level=None,
            label=name,
        ),
    )


def _wav(
    pid: str,
    rank: int,
    name: str,
    *,
    depth: int = 16,
    sr: int | None = 44100,
    channels: int | None = 2,
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format="wav",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="wav",
            preset_id=pid,
            rate_control="pcm",
            bitrate_kbps=None,
            vbr_quality=None,
            sample_rate=sr,
            channels=channels,
            bit_depth=depth,
            compression_level=None,
            label=name,
        ),
    )


def _flac(
    pid: str,
    rank: int,
    name: str,
    *,
    depth: int = 16,
    sr: int | None = None,
    level: int = 5,
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format="flac",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="flac",
            preset_id=pid,
            rate_control="lossless",
            bitrate_kbps=None,
            vbr_quality=None,
            sample_rate=sr,
            channels=None,
            bit_depth=depth,
            compression_level=level,
            label=name,
        ),
    )


def _aac(
    pid: str,
    rank: int,
    name: str,
    *,
    rc: RateControl = "cbr",
    bitrate: int | None = 128,
    q: float | None = None,
    channels: int | None = 2,
    sr: int | None = 44100,
    container: str = "m4a",
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format=container,
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format=container,
            preset_id=pid,
            rate_control=rc,
            bitrate_kbps=bitrate,
            vbr_quality=q,
            sample_rate=sr,
            channels=channels,
            bit_depth=None,
            compression_level=None,
            label=name,
        ),
    )


def _ogg(
    pid: str,
    rank: int,
    name: str,
    *,
    q: float = 4.0,
    channels: int | None = 2,
    sr: int | None = 44100,
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format="ogg",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="ogg",
            preset_id=pid,
            rate_control="vbr",
            bitrate_kbps=None,
            vbr_quality=q,
            sample_rate=sr,
            channels=channels,
            bit_depth=None,
            compression_level=None,
            label=name,
        ),
    )


def _opus(
    pid: str,
    rank: int,
    name: str,
    *,
    bitrate: int = 96,
    channels: int | None = 2,
    sr: int | None = 48000,
    blurb: str = "",
) -> AudioEncodePreset:
    return AudioEncodePreset(
        id=pid,
        format="opus",
        display_name=name,
        rank=rank,
        blurb=blurb,
        settings=AudioEncodeSettings(
            format="opus",
            preset_id=pid,
            rate_control="vbr",
            bitrate_kbps=bitrate,
            vbr_quality=None,
            sample_rate=sr,
            channels=channels,
            bit_depth=None,
            compression_level=None,
            label=name,
        ),
    )


_PRESETS: tuple[AudioEncodePreset, ...] = (
    # ---- MP3: low → high (VBR + CBR ladder) ----
    _mp3(
        "mp3_cbr_32_mono",
        10,
        "MP3 32 kbps CBR mono",
        rc="cbr",
        bitrate=32,
        channels=1,
        sr=22050,
        blurb="Speech / podcasts; smallest files",
    ),
    _mp3(
        "mp3_vbr_32",
        20,
        "MP3 ~32 kbps VBR (q=9)",
        rc="vbr",
        q=9.0,
        channels=1,
        sr=22050,
        blurb="Lowest reasonable VBR",
    ),
    _mp3(
        "mp3_cbr_64",
        30,
        "MP3 64 kbps CBR stereo",
        rc="cbr",
        bitrate=64,
        channels=2,
        sr=44100,
    ),
    _mp3(
        "mp3_vbr_64",
        40,
        "MP3 ~64 kbps VBR (q=7)",
        rc="vbr",
        q=7.0,
        channels=2,
    ),
    _mp3(
        "mp3_cbr_96",
        50,
        "MP3 96 kbps CBR",
        rc="cbr",
        bitrate=96,
        channels=2,
        sr=44100,
    ),
    _mp3(
        "mp3_vbr_96",
        60,
        "MP3 ~96 kbps VBR (q=6)",
        rc="vbr",
        q=6.0,
        channels=2,
    ),
    _mp3(
        "mp3_cbr_128",
        70,
        "MP3 128 kbps CBR",
        rc="cbr",
        bitrate=128,
        channels=2,
        sr=44100,
        blurb="Classic portable default",
    ),
    _mp3(
        "mp3_vbr_128",
        80,
        "MP3 ~128 kbps VBR (q=4)",
        rc="vbr",
        q=4.0,
        channels=2,
    ),
    _mp3(
        "mp3_cbr_192",
        90,
        "MP3 192 kbps CBR",
        rc="cbr",
        bitrate=192,
        channels=2,
        sr=44100,
    ),
    _mp3(
        "mp3_vbr_192",
        100,
        "MP3 ~192 kbps VBR (q=2)",
        rc="vbr",
        q=2.0,
        channels=2,
        blurb="Transparent for most ears",
    ),
    _mp3(
        "mp3_cbr_256",
        110,
        "MP3 256 kbps CBR",
        rc="cbr",
        bitrate=256,
        channels=2,
        sr=44100,
    ),
    _mp3(
        "mp3_vbr_256",
        120,
        "MP3 ~256 kbps VBR (q=1)",
        rc="vbr",
        q=1.0,
        channels=2,
    ),
    _mp3(
        "mp3_cbr_320",
        130,
        "MP3 320 kbps CBR",
        rc="cbr",
        bitrate=320,
        channels=2,
        sr=44100,
        blurb="Highest MPEG-1 Layer III CBR",
    ),
    _mp3(
        "mp3_vbr_q0",
        140,
        "MP3 VBR max quality (q=0)",
        rc="vbr",
        q=0.0,
        channels=None,
        sr=None,
        blurb="App default (legacy qscale:a 0)",
    ),
    # ---- WMA (wmav2; bitrate ladder) ----
    _wma("wma_cbr_32_mono", 10, "WMA 32 kbps mono", bitrate=32, channels=1, sr=22050),
    _wma("wma_cbr_64", 20, "WMA 64 kbps", bitrate=64, channels=2, sr=44100),
    _wma("wma_cbr_96", 30, "WMA 96 kbps", bitrate=96),
    _wma("wma_cbr_128", 40, "WMA 128 kbps", bitrate=128, blurb="ZEN-friendly default"),
    _wma("wma_cbr_160", 50, "WMA 160 kbps", bitrate=160),
    _wma("wma_cbr_192", 60, "WMA 192 kbps", bitrate=192),
    _wma("wma_cbr_256", 70, "WMA 256 kbps", bitrate=256),
    _wma("wma_cbr_320", 80, "WMA 320 kbps", bitrate=320, blurb="Highest typical WMA CBR"),
    # ---- WAV PCM ----
    _wav(
        "wav_pcm_16_22_mono",
        10,
        "WAV 16-bit 22.05 kHz mono",
        depth=16,
        sr=22050,
        channels=1,
        blurb="Small PCM / speech",
    ),
    _wav("wav_pcm_16_44", 20, "WAV 16-bit 44.1 kHz stereo", depth=16, sr=44100, channels=2),
    _wav("wav_pcm_16_48", 30, "WAV 16-bit 48 kHz stereo", depth=16, sr=48000, channels=2),
    _wav("wav_pcm_24_44", 40, "WAV 24-bit 44.1 kHz stereo", depth=24, sr=44100, channels=2),
    _wav("wav_pcm_24_48", 50, "WAV 24-bit 48 kHz stereo", depth=24, sr=48000, channels=2),
    _wav(
        "wav_pcm_24_96",
        60,
        "WAV 24-bit 96 kHz stereo",
        depth=24,
        sr=96000,
        channels=2,
        blurb="Studio-ish PCM",
    ),
    _wav(
        "wav_pcm_source",
        70,
        "WAV 16-bit (keep rate/channels)",
        depth=16,
        sr=None,
        channels=None,
        blurb="Only force PCM 16-bit",
    ),
    # ---- FLAC ----
    _flac("flac_16_44", 10, "FLAC 16-bit (level 5)", depth=16, level=5),
    _flac("flac_16_fast", 20, "FLAC 16-bit fast (level 0)", depth=16, level=0),
    _flac("flac_16_best", 30, "FLAC 16-bit best (level 12)", depth=16, level=12),
    _flac("flac_24", 40, "FLAC 24-bit (level 5)", depth=24, level=5),
    _flac(
        "flac_source",
        50,
        "FLAC keep source (level 5)",
        depth=16,
        sr=None,
        level=5,
        blurb="Lossless re-pack; depth may be forced by encoder",
    ),
    # ---- AAC / M4A ----
    _aac("aac_cbr_64", 10, "AAC 64 kbps", bitrate=64, container="m4a"),
    _aac("aac_cbr_96", 20, "AAC 96 kbps", bitrate=96, container="m4a"),
    _aac("aac_cbr_128", 30, "AAC 128 kbps", bitrate=128, container="m4a"),
    _aac("aac_cbr_192", 40, "AAC 192 kbps", bitrate=192, container="m4a"),
    _aac("aac_cbr_256", 50, "AAC 256 kbps", bitrate=256, container="m4a"),
    _aac(
        "aac_vbr_high",
        60,
        "AAC VBR high quality",
        rc="vbr",
        bitrate=None,
        q=1.5,
        container="m4a",
        blurb="ffmpeg aac -q:a",
    ),
    _aac(
        "aac_adts_128",
        70,
        "AAC ADTS 128 kbps (.aac)",
        bitrate=128,
        container="aac",
        blurb="Raw ADTS stream",
    ),
    # ---- OGG Vorbis ----
    _ogg("ogg_vbr_q0", 10, "OGG Vorbis q=0 (~64k)", q=0.0),
    _ogg("ogg_vbr_q2", 20, "OGG Vorbis q=2 (~96k)", q=2.0),
    _ogg("ogg_vbr_q4", 30, "OGG Vorbis q=4 (~128k)", q=4.0),
    _ogg("ogg_vbr_q6", 40, "OGG Vorbis q=6 (~192k)", q=6.0),
    _ogg("ogg_vbr_q8", 50, "OGG Vorbis q=8 (~256k)", q=8.0),
    _ogg("ogg_vbr_q10", 60, "OGG Vorbis q=10 (max)", q=10.0),
    # ---- Opus ----
    _opus("opus_vbr_32", 10, "Opus 32 kbps", bitrate=32),
    _opus("opus_vbr_64", 20, "Opus 64 kbps", bitrate=64),
    _opus("opus_vbr_96", 30, "Opus 96 kbps", bitrate=96),
    _opus("opus_vbr_128", 40, "Opus 128 kbps", bitrate=128),
    _opus("opus_vbr_192", 50, "Opus 192 kbps", bitrate=192),
    _opus("opus_vbr_256", 60, "Opus 256 kbps", bitrate=256),
)

_PRESET_BY_ID: dict[str, AudioEncodePreset] = {p.id: p for p in _PRESETS}


def all_presets() -> tuple[AudioEncodePreset, ...]:
    return _PRESETS


def get_preset(preset_id: str | None) -> AudioEncodePreset | None:
    if not preset_id:
        return None
    return _PRESET_BY_ID.get(preset_id)


def estimate_settings_bitrate_kbps(settings: AudioEncodeSettings) -> int | None:
    """Rough kbps for ranking/compare (None if unknown / lossless)."""
    s = clamp_settings_for_format(settings)
    if s.rate_control in ("lossless", "pcm"):
        return None
    if s.bitrate_kbps:
        return int(s.bitrate_kbps)
    # Rough VBR maps for UI ranking only.
    if s.rate_control == "vbr" and s.vbr_quality is not None:
        q = float(s.vbr_quality)
        fmt = s.normalized_format()
        if fmt == "mp3":
            # q 0≈245 … 9≈65
            return int(max(32, min(320, 245 - q * 20)))
        if fmt == "ogg":
            return int(max(32, min(320, 64 + q * 32)))
        if fmt in ("aac", "m4a"):
            return int(max(32, min(320, q * 128)))
    return None


def closest_preset_for_bitrate(
    fmt: str,
    bitrate_kbps: int | None,
    *,
    allowed_formats: Collection[str] | None = None,
) -> AudioEncodePreset | None:
    """Pick the ladder step nearest to *bitrate_kbps* (same format)."""
    ladder = presets_for_format(fmt)
    if allowed_formats is not None:
        allowed = {str(x).lower().lstrip(".") for x in allowed_formats}
        ladder = [p for p in ladder if p.format in allowed or fmt in allowed]
    if not ladder:
        return None
    if bitrate_kbps is None or bitrate_kbps <= 0:
        # Mid ladder.
        return ladder[min(len(ladder) - 1, max(0, len(ladder) // 2))]
    best = ladder[0]
    best_d = 10**9
    for p in ladder:
        est = estimate_settings_bitrate_kbps(p.settings)
        if est is None:
            continue
        d = abs(est - int(bitrate_kbps))
        if d < best_d:
            best_d = d
            best = p
    return best


def shrink_presets_at_or_below(
    fmt: str,
    *,
    max_bitrate_kbps: int | None = None,
    allowed_formats: Collection[str] | None = None,
) -> list[AudioEncodePreset]:
    """Presets for *fmt* ordered low→high, optionally capped by bitrate."""
    ladder = list(presets_for_format(fmt))
    if allowed_formats is not None:
        allowed = {str(x).lower().lstrip(".") for x in allowed_formats}
        ladder = [p for p in ladder if p.format in allowed or p.format == fmt]
    if max_bitrate_kbps is not None and max_bitrate_kbps > 0:
        capped = []
        for p in ladder:
            est = estimate_settings_bitrate_kbps(p.settings)
            if est is None or est <= int(max_bitrate_kbps):
                capped.append(p)
        if capped:
            ladder = capped
    return ladder


def presets_for_format(fmt: str) -> list[AudioEncodePreset]:
    key = (fmt or "").lower().lstrip(".")
    # Treat aac and m4a as one family for the format picker when listing AAC.
    if key == "aac":
        return sorted(
            [p for p in _PRESETS if p.format in ("aac", "m4a")],
            key=lambda p: p.rank,
        )
    return sorted([p for p in _PRESETS if p.format == key], key=lambda p: p.rank)


def formats_allowed(
    allowed: Collection[str] | None,
) -> tuple[str, ...]:
    """Intersect catalog formats with *allowed*; None = unrestricted."""
    if allowed is None:
        # Present aac as "AAC / M4A" via m4a primary; keep aac for ADTS preset.
        # UI lists unique format keys that have presets.
        order = ("mp3", "wma", "wav", "flac", "m4a", "ogg", "opus")
        return order
    allowed_norm = {a.lower().lstrip(".") for a in allowed}
    # Map m4a/aac: if either allowed, show m4a bucket.
    out: list[str] = []
    for fmt in ("mp3", "wma", "wav", "flac", "m4a", "aac", "ogg", "opus"):
        if fmt in allowed_norm:
            if fmt == "aac" and "m4a" in out:
                continue
            if fmt not in out:
                out.append(fmt)
        elif fmt == "m4a" and "aac" in allowed_norm and "m4a" not in out:
            out.append("m4a")
    return tuple(out) if out else ("mp3",)


def resolve_settings(
    *,
    settings: AudioEncodeSettings | None = None,
    preset_id: str | None = None,
    send_format: str | None = None,
    allowed_formats: Collection[str] | None = None,
) -> AudioEncodeSettings:
    """Pick concrete settings for a convert, clamped to *allowed_formats*."""
    if settings is not None:
        s = clamp_settings_for_format(settings)
    elif preset_id and get_preset(preset_id):
        s = get_preset(preset_id).settings  # type: ignore[union-attr]
    elif send_format:
        s = settings_from_legacy_format(send_format)
    else:
        s = default_audio_encode_settings()

    allowed = formats_allowed(allowed_formats)
    fmt = s.normalized_format()
    # Normalize aac→m4a when only m4a is listed.
    if fmt == "aac" and "aac" not in allowed and "m4a" in allowed:
        s = replace(s, format="m4a")
        fmt = "m4a"
    if fmt == "m4a" and "m4a" not in allowed and "aac" in allowed:
        s = replace(s, format="aac")
        fmt = "aac"
    if fmt not in allowed and not (
        fmt in ("aac", "m4a") and any(x in allowed for x in ("aac", "m4a"))
    ):
        # Fall back to first allowed format's default preset.
        fallback_fmt = allowed[0] if allowed else "mp3"
        ladder = presets_for_format(fallback_fmt)
        if ladder:
            # Prefer mid-high quality step.
            mid = ladder[min(len(ladder) - 1, max(0, len(ladder) // 2 + 1))]
            s = mid.settings
        else:
            s = settings_from_legacy_format(fallback_fmt)
    return clamp_settings_for_format(s)


def bitrate_choices_for_format(fmt: str) -> tuple[int, ...]:
    key = (fmt or "mp3").lower().lstrip(".")
    if key == "opus":
        return (16, 24, 32, 48, 64, 96, 128, 160, 192, 256, 320)
    if key == "wma":
        return (32, 48, 64, 80, 96, 128, 160, 192, 256, 320)
    if key in ("aac", "m4a"):
        return (32, 48, 64, 80, 96, 128, 160, 192, 224, 256, 320)
    # mp3 and others
    return (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


def format_display_name(fmt: str) -> str:
    key = (fmt or "").lower().lstrip(".")
    if key == "m4a":
        return "AAC (M4A)"
    return FORMAT_LABELS.get(key, key.upper())
