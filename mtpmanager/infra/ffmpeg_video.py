"""ffmpeg video encode for device-specific Send Video profiles.

Uses stock ffmpeg ``mpeg4`` + ``-vtag XVID`` (no libxvid). Progress is parsed
from ffmpeg stderr ``time=`` lines (safe UTF-8 replace decode).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

from ffmpeg import FFmpeg

from mtpmanager.domain.audio_encode import AudioEncodeSettings
from mtpmanager.domain.device_profile import VideoEncodePreset, VideoEncodeProfile
from mtpmanager.infra.ffmpeg_exec import run_ffmpeg_builder
from mtpmanager.infra.ffmpeg_transcode import build_ffmpeg_audio_options

logger = logging.getLogger(__name__)

# Temp outputs only — never delete user source files.
_TEMP_VIDEO_RE = re.compile(r"^VIDEO_TRANSCODE_[A-Za-z0-9_]+\.[A-Za-z0-9]+$")

ProgressCallback = Callable[[float, float, str], None]
# done_seconds, total_seconds (0 if unknown), status message


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def probe_media(path: str) -> dict:
    """Return ffprobe JSON for *path* (format + streams). Empty dict on failure."""
    if not path or not os.path.isfile(path):
        return {}
    cmd = [
        _ffprobe_bin(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe failed path=%s: %s", path, exc)
        return {}
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        logger.warning(
            "ffprobe non-zero path=%s rc=%s stderr=%s",
            path,
            proc.returncode,
            (proc.stderr or "")[:300],
        )
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def probe_duration_seconds(path: str) -> float:
    """Best-effort media duration in seconds (0 if unknown)."""
    data = probe_media(path)
    fmt = data.get("format") or {}
    try:
        d = float(fmt.get("duration") or 0)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    for s in data.get("streams") or []:
        try:
            d = float(s.get("duration") or 0)
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return 0.0


def _parse_rate(value: object) -> float:
    """Parse ffprobe rate strings like ``25/1`` or ``30000/1001``."""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text in ("0/0", "N/A", "nan"):
        return 0.0
    if "/" in text:
        num_s, den_s = text.split("/", 1)
        try:
            num, den = float(num_s), float(den_s)
        except (TypeError, ValueError):
            return 0.0
        if den == 0:
            return 0.0
        return num / den
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _parse_ratio_pair(value: object) -> tuple[int, int] | None:
    """Parse ffprobe ratio strings like ``853:720`` or ``16/9`` → (num, den)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in ("N/A", "NAN", "0:1", "0/1", "0:0", "0/0"):
        return None
    sep = ":" if ":" in text else ("/" if "/" in text else "")
    if not sep:
        try:
            f = float(text)
        except (TypeError, ValueError):
            return None
        if f <= 0:
            return None
        # Approximate as integer pair with den=1000 when given a bare float.
        return max(1, int(round(f * 1000))), 1000
    num_s, den_s = text.split(sep, 1)
    try:
        num, den = int(round(float(num_s))), int(round(float(den_s)))
    except (TypeError, ValueError):
        return None
    if num <= 0 or den <= 0:
        return None
    return num, den


@dataclass(frozen=True)
class VideoAspectInfo:
    """Storage size + sample/display aspect from ffprobe (pre-encode)."""

    width: int
    height: int
    # Sample aspect ratio as reduced-ish integers (1:1 when unknown/square).
    sar_num: int = 1
    sar_den: int = 1
    # Display aspect when ffprobe provides it; else derived from storage×SAR.
    dar_num: int | None = None
    dar_den: int | None = None

    @property
    def sar(self) -> float:
        if self.sar_den <= 0:
            return 1.0
        return float(self.sar_num) / float(self.sar_den)

    @property
    def dar(self) -> float:
        if self.dar_num and self.dar_den and self.dar_den > 0:
            return float(self.dar_num) / float(self.dar_den)
        if self.width <= 0 or self.height <= 0:
            return 0.0
        return (float(self.width) * self.sar) / float(self.height)

    @property
    def is_anamorphic(self) -> bool:
        """True when sample aspect is meaningfully non-square."""
        return abs(self.sar - 1.0) > 0.01

    def summary(self) -> str:
        sar_s = f"{self.sar_num}:{self.sar_den}"
        if self.dar_num and self.dar_den:
            dar_s = f"{self.dar_num}:{self.dar_den}"
        else:
            dar_s = f"{self.dar:.4g}"
        kind = "anamorphic" if self.is_anamorphic else "square-pixel"
        return (
            f"{self.width}x{self.height} SAR={sar_s} DAR={dar_s} ({kind})"
        )


def probe_video_aspect(path: str) -> VideoAspectInfo | None:
    """Return storage size + SAR/DAR for the first video stream, or None."""
    data = probe_media(path)
    vs, _ = _stream_types(data)
    if not vs:
        return None
    v = vs[0]
    try:
        width = int(v.get("width") or 0)
        height = int(v.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None

    sar_pair = _parse_ratio_pair(v.get("sample_aspect_ratio"))
    if sar_pair is None:
        sar_num, sar_den = 1, 1
    else:
        sar_num, sar_den = sar_pair

    dar_pair = _parse_ratio_pair(v.get("display_aspect_ratio"))
    dar_num = dar_pair[0] if dar_pair else None
    dar_den = dar_pair[1] if dar_pair else None

    return VideoAspectInfo(
        width=width,
        height=height,
        sar_num=sar_num,
        sar_den=sar_den,
        dar_num=dar_num,
        dar_den=dar_den,
    )


def probe_video_fps(path: str) -> float:
    """Best-effort source video frame rate (0 if unknown)."""
    data = probe_media(path)
    vs, _ = _stream_types(data)
    if not vs:
        return 0.0
    v = vs[0]
    # Prefer average when present; fall back to r_frame_rate.
    fps = _parse_rate(v.get("avg_frame_rate"))
    if fps <= 0:
        fps = _parse_rate(v.get("r_frame_rate"))
    return fps if fps > 0 else 0.0


def output_fps_for_source(source_fps: float, max_fps: float) -> float | None:
    """Return fps to force in the filter, or None to keep the source rate.

    *max_fps* ≤ 0 means no cap (always keep source) — the default for
    ``VideoEncodePreset``. Device profiles (e.g. ZEN Vision:M) set a
    positive cap:

    - Source unknown (≤0) → None
    - Source ≤ *max_fps* → None (keep 25, 29.97, 24, …)
    - Source > *max_fps* → *max_fps* (e.g. 60 → 30)
    """
    cap = float(max_fps) if max_fps and max_fps > 0 else 0.0
    src = float(source_fps) if source_fps else 0.0
    if cap <= 0:
        return None  # default: never force a frame rate
    if src <= 0:
        return None
    if src > cap + 1e-6:
        return cap
    return None


def _stream_types(data: dict) -> tuple[list[dict], list[dict]]:
    streams = data.get("streams") or []
    vs = [s for s in streams if s.get("codec_type") == "video"]
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    return vs, aus


def video_matches_encode_profile(
    path: str, profile: VideoEncodePreset | VideoEncodeProfile
) -> bool:
    """True when *path* already matches the selected encode preset closely.

    Strict enough to skip re-encode of stock Creative AVIs; loose enough that
    a slightly different bitrate/fps demo still passes (Xtreme @ 29.97 + DX50).
    """
    data = probe_media(path)
    if not data:
        return False
    fmt = data.get("format") or {}
    format_name = str(fmt.get("format_name") or "").casefold()
    tokens = {t.strip() for t in format_name.split(",") if t.strip()}
    want = tuple(
        c.casefold()
        for c in (profile.probe_containers or (profile.container or "avi",))
        if c
    )
    if not any(c in tokens or c in format_name for c in want):
        return False

    vs, aus = _stream_types(data)
    if len(vs) != 1 or len(aus) != 1:
        return False

    v, a = vs[0], aus[0]
    if str(v.get("codec_name") or "").casefold() != profile.probe_video_codec.casefold():
        return False
    ok_tags = {t.upper() for t in profile.acceptable_video_tags if t}
    if ok_tags:
        tag = str(v.get("codec_tag_string") or "").strip().upper()
        if tag not in ok_tags:
            return False
    if str(v.get("pix_fmt") or "").casefold() != "yuv420p":
        return False
    try:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0 or (w % 16) or (h % 16):
        return False
    # Must match the device frame (e.g. 640×480). Do not skip re-encode for
    # same-codec files at a different storage size (e.g. 720×480 XviD).
    if w != int(profile.width) or h != int(profile.height):
        return False

    if str(a.get("codec_name") or "").casefold() != profile.probe_audio_codec.casefold():
        return False
    try:
        rate = int(a.get("sample_rate") or 0)
        ch = int(a.get("channels") or 0)
    except (TypeError, ValueError):
        return False
    if rate != int(profile.audio_sample_rate) or ch != int(profile.audio_channels):
        return False
    return True


def _vf_filter(
    profile: VideoEncodePreset | VideoEncodeProfile,
    *,
    force_fps: float | None = None,
    aspect: VideoAspectInfo | None = None,
) -> str:
    """Build the video filter chain for device frame geometry.

    Full picture (no crop), square-pixel wire file:

    1. When *aspect* is anamorphic (SAR ≠ 1), expand storage pixels by the
       probed SAR first (``iw*sar_num/sar_den``) so widescreen DVD/TV rips
       keep correct DAR. Square-pixel sources skip this step.
    2. ``setsar=1`` — subsequent fit uses storage width×height only.
    3. ``scale=W:H:force_original_aspect_ratio=decrease`` — fit inside the
       device box (both axes together).
    4. ``pad=W:H`` — letter/pillar-box to the device resolution.
    5. ``setsar=1`` — square-pixel wire file for picky DAPs.

    Example (square): 720×480 → ~640×426, pad to 640×480.
    Example (anamorphic 720×472 SAR 853:720): expand ≈853×472, then fit/pad.

    *force_fps*: when set (source above profile.max_fps), insert ``fps=…``.
    """
    w, h = int(profile.width), int(profile.height)
    parts: list[str] = []

    # Discover-and-choose: only expand when probe says non-square SAR.
    if aspect is not None and aspect.is_anamorphic:
        sn, sd = int(aspect.sar_num), int(aspect.sar_den)
        # Bake probed ratio (not filtergraph `sar`) so metadata quirks cannot
        # silently fall back to 1:1 mid-graph.
        parts.append(
            f"scale=trunc(iw*{sn}/{sd}/2)*2:trunc(ih/2)*2"
        )

    parts.extend(
        [
            "setsar=1",
            (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2"
            ),
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
        ]
    )
    if force_fps is not None and force_fps > 0:
        parts.append(f"fps={force_fps:g}")
    parts.append("format=yuv420p")
    return ",".join(parts)


# Keys from build_ffmpeg_audio_options that must not enter a video mux
# (they drop or remux only audio / strip video).
_AUDIO_ONLY_OPT_KEYS = frozenset(
    {
        "vn",
        "sn",
        "dn",
        "map",
        "map_metadata",
        "map_chapters",
    }
)


def _audio_opts_for_video_mux(
    settings: AudioEncodeSettings | None,
    profile: VideoEncodePreset | VideoEncodeProfile,
) -> dict:
    """Audio encoder options for a video container (never -vn / audio-only map).

    Device video expects explicit sample rate / channel count (match-skip and
    ZEN playback). When the music ladder leaves those as “keep source”, fall
    back to the effective preset’s audio_* fields.
    """
    if settings is not None:
        raw = build_ffmpeg_audio_options(settings)
        out: dict = {}
        for key, val in raw.items():
            if key in _AUDIO_ONLY_OPT_KEYS:
                continue
            # Normalize codec key used by python-ffmpeg builders.
            if key in ("codec:a", "c:a"):
                out["c:a"] = val
                continue
            out[key] = val
        if "ar" not in out:
            out["ar"] = str(int(profile.audio_sample_rate))
        if "ac" not in out:
            out["ac"] = str(int(profile.audio_channels))
        return out
    return {
        "c:a": profile.audio_codec,
        "b:a": profile.audio_bitrate,
        "ac": str(int(profile.audio_channels)),
        "ar": str(int(profile.audio_sample_rate)),
    }


def _mpeg4_slow_encode_opts() -> dict[str, str]:
    """Extra ffmpeg flags for slower, higher-quality mpeg4 encodes.

    Classic high-quality mpeg4 recipe: rate-distortion macroblock decisions,
    quarter-pel + AIC, trellis quantization, and better comparison functions.
    Meaningful at low resolutions where spending CPU improves clarity.
    """
    return {
        "mbd": "rd",
        "flags": "+mv4+aic",
        "trellis": "2",
        "cmp": "2",
        "subcmp": "2",
    }


def _codec_supports_mpeg4_slow(profile: VideoEncodePreset | VideoEncodeProfile) -> bool:
    codec = (profile.video_codec or "").strip().casefold()
    return codec in ("mpeg4", "libxvid")


def _append_video_rate_opts(
    cmd: list[str], profile: VideoEncodePreset | VideoEncodeProfile
) -> None:
    """Append ``-qscale:v`` / ``-b:v`` and optional slow-encode flags to *cmd*."""
    if profile.qscale_v is not None:
        cmd += ["-qscale:v", str(int(profile.qscale_v))]
    elif profile.video_bitrate:
        cmd += ["-b:v", profile.video_bitrate]
    if profile.slow_encode and _codec_supports_mpeg4_slow(profile):
        for key, value in _mpeg4_slow_encode_opts().items():
            cmd += [f"-{key}", value]


def _build_output_options(
    profile: VideoEncodePreset | VideoEncodeProfile,
    *,
    force_fps: float | None,
    container_ext: str,
    audio_settings: AudioEncodeSettings | None = None,
    aspect: VideoAspectInfo | None = None,
) -> dict:
    """ffmpeg output options for AVI/mpeg4 or WMV/WMA-style presets."""
    out: dict = {
        "map": ["0:v:0", "0:a:0?"],
        "c:v": profile.video_codec,
        "vf": _vf_filter(profile, force_fps=force_fps, aspect=aspect),
        "f": container_ext if container_ext != "wmv" else "asf",
    }
    out.update(_audio_opts_for_video_mux(audio_settings, profile))
    if profile.video_tag:
        out["vtag"] = profile.video_tag
    if profile.qscale_v is not None:
        out["qscale:v"] = str(int(profile.qscale_v))
    elif profile.video_bitrate:
        out["b:v"] = profile.video_bitrate
    if profile.slow_encode and _codec_supports_mpeg4_slow(profile):
        out.update(_mpeg4_slow_encode_opts())
    return out


def convert_video_for_profile(
    src_path: str,
    profile: VideoEncodePreset | VideoEncodeProfile,
    *,
    dest_path: str | None = None,
    temp_dir: str | None = None,
    on_progress: ProgressCallback | None = None,
    ignore_max_fps: bool = False,
    audio_settings: AudioEncodeSettings | None = None,
) -> str:
    """Re-encode *src_path* to the selected device video preset; return path.

    *ignore_max_fps*: when True, do not apply *profile.max_fps* (keep source
    rate even if above the device cap — experimental; may break playback).

    *audio_settings*: when set, drives the audio encoder via the shared
    ``AudioEncodeSettings`` ladder (same as music/podcasts). Video map is
    preserved; audio-only flags from the audio builder are stripped.
    When unset, uses *profile.audio_** fields (back-compat).

    *on_progress(done_sec, total_sec, message)* is optional (worker thread).
    Raises ``RuntimeError`` / ``OSError`` / ffmpeg errors on failure.
    """
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError(f"Video source not found: {src_path!r}")

    ext = (profile.container or "avi").lstrip(".")
    if dest_path is None:
        base = tempfile.mktemp(
            prefix="VIDEO_TRANSCODE_",
            suffix=f".{ext}",
            dir=temp_dir or tempfile.gettempdir(),
        )
        # mktemp is fine: we immediately write via ffmpeg; unique name for cleanup.
        dest_path = base
    else:
        dest_path = str(dest_path)

    parent = os.path.dirname(dest_path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest_path):
        try:
            os.remove(dest_path)
        except OSError:
            pass

    duration = probe_duration_seconds(src_path)
    source_fps = probe_video_fps(src_path)
    aspect = probe_video_aspect(src_path)
    max_fps = 0.0 if ignore_max_fps else float(profile.max_fps or 0)
    force_fps = output_fps_for_source(source_fps, max_fps)
    logger.info(
        "Video convert start src=%s dest=%s preset=%s duration=%.1fs "
        "source_fps=%.3f max_fps=%s force_fps=%s ignore_max_fps=%s "
        "frame=%sx%s qscale=%s slow=%s aspect=%s audio=%s",
        src_path,
        dest_path,
        profile.id,
        duration,
        source_fps,
        f"{max_fps:g}" if max_fps > 0 else "none",
        f"{force_fps:g}" if force_fps is not None else "keep",
        ignore_max_fps,
        profile.width,
        profile.height,
        profile.qscale_v if profile.qscale_v is not None else profile.video_bitrate,
        bool(profile.slow_encode),
        aspect.summary() if aspect is not None else "unknown",
        (
            audio_settings.summary_line()
            if audio_settings is not None
            else f"{profile.audio_codec}/{profile.audio_bitrate}"
        ),
    )
    if on_progress is not None:
        try:
            on_progress(0.0, duration, "encoding for device…")
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    out_opts = _build_output_options(
        profile,
        force_fps=force_fps,
        container_ext=ext,
        audio_settings=audio_settings,
        aspect=aspect,
    )

    ff = FFmpeg().option("y").input(src_path).output(dest_path, out_opts)

    # ffmpeg status lines: time=00:01:23.45  (ASCII; safe after replace decode)
    _time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

    def _on_stderr_line(line: str) -> None:
        if on_progress is None:
            return
        m = _time_re.search(line)
        if not m:
            return
        try:
            h, mi, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
            done = h * 3600 + mi * 60 + sec
        except (TypeError, ValueError):
            return
        total = duration if duration > 0 else 0.0
        if total > 0:
            done = min(done, total)
        try:
            on_progress(done, total, "encoding for device…")
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    try:
        # Safe stderr decode — python-ffmpeg execute() raises UnicodeDecodeError
        # on truncated ID3 dumps in ffmpeg metadata output.
        run_ffmpeg_builder(
            ff,
            on_stderr_line=_on_stderr_line if on_progress else None,
        )
    except Exception as exc:
        logger.error("Video ffmpeg failed src=%s: %s", src_path, exc)
        cleanup_video_temp(dest_path)
        raise

    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) <= 0:
        cleanup_video_temp(dest_path)
        raise RuntimeError(f"ffmpeg produced no output for {src_path}")

    if on_progress is not None:
        try:
            on_progress(
                duration if duration > 0 else 1.0,
                duration if duration > 0 else 1.0,
                "encode complete",
            )
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    logger.info(
        "Video convert done dest=%s size=%s",
        dest_path,
        os.path.getsize(dest_path),
    )
    return dest_path


def _audio_stream_matches_profile(
    audio_path: str, profile: VideoEncodePreset | VideoEncodeProfile
) -> bool:
    """True when *audio_path* can be stream-copied into the profile container."""
    data = probe_media(audio_path)
    _, aus = _stream_types(data)
    if len(aus) != 1:
        return False
    a = aus[0]
    if str(a.get("codec_name") or "").casefold() != profile.probe_audio_codec.casefold():
        return False
    try:
        rate = int(a.get("sample_rate") or 0)
        ch = int(a.get("channels") or 0)
    except (TypeError, ValueError):
        return False
    return rate == int(profile.audio_sample_rate) and ch == int(profile.audio_channels)


def _build_audio_still_cmd(
    audio_path: str,
    profile: VideoEncodePreset | VideoEncodeProfile,
    dest_path: str,
    *,
    image_path: str | None,
    still_fps: float,
    duration: float,
    copy_audio: bool,
    width: int | None = None,
    height: int | None = None,
) -> list[str]:
    """Build argv for still-image + audio → device video.

    Uses subprocess (not python-ffmpeg) so full stderr is available on failure.
    Caps the image input with ``-t duration`` so we do not rely on infinite
    ``-loop`` + ``-shortest`` alone (that path often ends as bare
    ``Conversion failed!`` with no useful last-line message from python-ffmpeg).

    *width*/*height* override the device profile frame (audio-still size ladder).
    """
    w = int(width) if width and int(width) > 0 else int(profile.width)
    h = int(height) if height and int(height) > 0 else int(profile.height)
    w = max(16, w - (w % 2))
    h = max(16, h - (h % 2))
    fps = float(still_fps) if still_fps and still_fps > 0 else 2.0
    ext = (profile.container or "avi").lstrip(".")
    # rgb24 first: feed pal8/PNG palette artwork through a known pixel format
    # before scale/pad/yuv420p (palette → yuv420p can fail on some builds).
    vf = (
        f"format=rgb24,"
        f"setsar=1,"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format=yuv420p"
    )
    # Slight pad on duration so audio is never truncated by an early video end.
    t_img = f"{duration + 0.5:.3f}" if duration > 0 else None

    cmd: list[str] = [_ffmpeg_bin(), "-y", "-hide_banner"]
    if image_path:
        cmd += ["-loop", "1", "-framerate", f"{fps:g}"]
        if t_img:
            cmd += ["-t", t_img]
        cmd += ["-i", image_path]
    else:
        color = f"color=c=black:s={w}x{h}:r={fps:g}"
        if duration > 0:
            color += f":d={duration + 0.5:.3f}"
        cmd += ["-f", "lavfi", "-i", color]

    cmd += ["-i", audio_path]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        profile.video_codec,
        "-vf",
        vf,
        "-r",
        f"{fps:g}",
    ]
    if profile.video_tag:
        cmd += ["-vtag", profile.video_tag]
    _append_video_rate_opts(cmd, profile)

    if copy_audio:
        cmd += ["-c:a", "copy"]
    else:
        cmd += [
            "-c:a",
            profile.audio_codec,
            "-b:a",
            profile.audio_bitrate,
            "-ac",
            str(int(profile.audio_channels)),
            "-ar",
            str(int(profile.audio_sample_rate)),
        ]
    cmd += [
        "-shortest",
        "-f",
        ext if ext != "wmv" else "asf",
        dest_path,
    ]
    return cmd


def convert_audio_still_to_video_for_profile(
    audio_path: str,
    profile: VideoEncodePreset | VideoEncodeProfile,
    *,
    image_path: str | None = None,
    dest_path: str | None = None,
    temp_dir: str | None = None,
    on_progress: ProgressCallback | None = None,
    still_fps: float = 2.0,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Mux *audio_path* with a still image (or black) into a device video file.

    Experimental path for audio podcasts that must appear under ZENcast (ZVM
    only lists video objects there). Video track is a low-rate still
    (ZVM-proven default 2 fps · 128×96 when *width*/*height* are set by
    config; otherwise the device profile frame). Artwork or solid black.

    Returns the path written. Raises on missing audio / ffmpeg failure.
    """
    if not audio_path or not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio source not found: {audio_path!r}")

    ext = (profile.container or "avi").lstrip(".")
    if dest_path is None:
        dest_path = tempfile.mktemp(
            prefix="VIDEO_TRANSCODE_",
            suffix=f".{ext}",
            dir=temp_dir or tempfile.gettempdir(),
        )
    else:
        dest_path = str(dest_path)

    parent = os.path.dirname(dest_path) or "."
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest_path):
        try:
            os.remove(dest_path)
        except OSError:
            pass

    fps = float(still_fps) if still_fps and still_fps > 0 else 2.0
    # Prefer explicit still geometry; else profile (full Send Video frame).
    if width and int(width) > 0 and height and int(height) > 0:
        frame_w, frame_h = int(width), int(height)
    else:
        frame_w, frame_h = int(profile.width), int(profile.height)
    frame_w = max(16, frame_w - (frame_w % 2))
    frame_h = max(16, frame_h - (frame_h % 2))
    duration = probe_duration_seconds(audio_path)
    use_image = bool(image_path and os.path.isfile(image_path))
    copy_audio = _audio_stream_matches_profile(audio_path, profile)

    try:
        audio_size = os.path.getsize(audio_path)
    except OSError:
        audio_size = 0
    logger.info(
        "Audio-still video start audio=%s image=%s dest=%s preset=%s "
        "duration=%.1fs still_fps=%g frame=%dx%d audio_bytes=%s copy_audio=%s",
        audio_path,
        image_path if use_image else "(black)",
        dest_path,
        profile.id,
        duration,
        fps,
        frame_w,
        frame_h,
        audio_size,
        copy_audio,
    )
    if on_progress is not None:
        try:
            on_progress(0.0, duration, "encoding still-image podcast video…")
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    def _run(img: str | None) -> subprocess.CompletedProcess[str]:
        cmd = _build_audio_still_cmd(
            audio_path,
            profile,
            dest_path,
            image_path=img,
            still_fps=fps,
            duration=duration,
            copy_audio=copy_audio,
            width=frame_w,
            height=frame_h,
        )
        logger.info("Audio-still ffmpeg cmd: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            # Long podcasts: allow hours of wall time (audio copy is near mux-only).
            timeout=None,
        )

    tried_image = use_image
    proc = _run(str(image_path) if use_image else None)
    if proc.returncode != 0 and use_image:
        # Artwork can be odd (pal8, corrupt, huge); fall back to solid black.
        err_tail = (proc.stderr or "")[-1200:]
        logger.warning(
            "Audio-still with artwork failed (rc=%s); retrying black frame. "
            "stderr tail:\n%s",
            proc.returncode,
            err_tail,
        )
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        tried_image = False
        proc = _run(None)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        # Log a generous tail — python-ffmpeg only exposed the last line.
        logger.error(
            "Audio-still ffmpeg failed audio=%s image=%s rc=%s stderr:\n%s",
            audio_path,
            image_path if tried_image else "(black)",
            proc.returncode,
            err[-2500:] if err else "(empty stderr)",
        )
        cleanup_video_temp(dest_path)
        # Prefer a short actionable message for UI/logs.
        last = err.splitlines()[-1] if err else "Conversion failed"
        raise RuntimeError(
            f"Still-image podcast encode failed (rc={proc.returncode}): {last}"
        )

    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) <= 0:
        cleanup_video_temp(dest_path)
        raise RuntimeError(f"ffmpeg produced no still-video output for {audio_path}")

    try:
        out_size = os.path.getsize(dest_path)
    except OSError:
        out_size = 0
    overhead = out_size - audio_size if audio_size > 0 else out_size
    logger.info(
        "Audio-still video done dest=%s out_bytes=%s audio_bytes=%s "
        "overhead_bytes=%s (+%.1f%% vs audio) copy_audio=%s image=%s "
        "frame=%dx%d still_fps=%g",
        dest_path,
        out_size,
        audio_size,
        overhead,
        (100.0 * overhead / audio_size) if audio_size > 0 else 0.0,
        copy_audio,
        image_path if use_image and tried_image else "(black)",
        frame_w,
        frame_h,
        fps,
    )
    if on_progress is not None:
        try:
            on_progress(
                duration if duration > 0 else 1.0,
                duration if duration > 0 else 1.0,
                "encode complete",
            )
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)
    return dest_path


def cleanup_video_temp(path: str | None) -> None:
    """Delete a known VIDEO_TRANSCODE temp file only."""
    if not path:
        return
    base = os.path.basename(path)
    if not _TEMP_VIDEO_RE.match(base):
        return
    if not os.path.exists(path):
        return
    try:
        size = 0
        try:
            size = int(os.path.getsize(path))
        except OSError:
            pass
        t0 = time.perf_counter()
        os.remove(path)
        elapsed = time.perf_counter() - t0
        if elapsed >= 1.0 or size >= 50 * 1024 * 1024:
            logger.info(
                "Deleted video temp %s (%.1f MiB) in %.1fs",
                path,
                size / (1024 * 1024),
                elapsed,
            )
    except OSError as exc:
        logger.warning("Could not delete video temp %s: %s", path, exc)


def default_temp_video_path(
    profile: VideoEncodePreset | VideoEncodeProfile,
    *,
    temp_dir: str | None = None,
) -> str:
    """Return a unique temp path for a profile encode."""
    ext = (profile.container or "avi").lstrip(".")
    fd, name = tempfile.mkstemp(
        prefix="VIDEO_TRANSCODE_",
        suffix=f".{ext}",
        dir=temp_dir or tempfile.gettempdir(),
    )
    os.close(fd)
    # Leave empty file for ffmpeg -y overwrite; ensure cleanup pattern matches.
    return name
