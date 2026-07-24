"""ffmpeg video encode for device-specific Send Video profiles.

Uses stock ffmpeg ``mpeg4`` + ``-vtag XVID`` (no libxvid). Progress comes from
python-ffmpeg ``Progress`` events when a duration is known.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import timedelta

from ffmpeg import FFmpeg, Progress

from mtpmanager.domain.device_profile import VideoEncodeProfile

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
    ``VideoEncodeProfile``. Device profiles (e.g. ZEN Vision:M) set a
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


def video_matches_encode_profile(path: str, profile: VideoEncodeProfile) -> bool:
    """True when *path* already looks like a retail-demo-compatible encode.

    Strict enough to skip re-encode of stock Creative AVIs; loose enough that
    a slightly different bitrate/fps demo still passes (Xtreme @ 29.97 + DX50).
    """
    data = probe_media(path)
    if not data:
        return False
    fmt = data.get("format") or {}
    format_name = str(fmt.get("format_name") or "").casefold()
    want_container = (profile.container or "avi").casefold()
    if want_container not in format_name.split(","):
        # format_name can be "avi" or compound; require avi token.
        if want_container not in format_name:
            return False

    vs, aus = _stream_types(data)
    if len(vs) != 1 or len(aus) != 1:
        return False

    v, a = vs[0], aus[0]
    if str(v.get("codec_name") or "").casefold() != profile.probe_video_codec.casefold():
        return False
    tag = str(v.get("codec_tag_string") or "").strip().upper()
    ok_tags = {t.upper() for t in profile.acceptable_video_tags}
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
    profile: VideoEncodeProfile,
    *,
    force_fps: float | None = None,
) -> str:
    """Build the video filter chain.

    *force_fps*: when set (source above profile.max_fps), insert ``fps=…``.
    When None, source frame rate is left unchanged (25, 29.97, etc.).
    """
    w, h = int(profile.width), int(profile.height)
    parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ]
    if force_fps is not None and force_fps > 0:
        parts.append(f"fps={force_fps:g}")
    parts.append("format=yuv420p")
    return ",".join(parts)


def convert_video_for_profile(
    src_path: str,
    profile: VideoEncodeProfile,
    *,
    dest_path: str | None = None,
    temp_dir: str | None = None,
    on_progress: ProgressCallback | None = None,
    ignore_max_fps: bool = False,
) -> str:
    """Re-encode *src_path* to the device video profile; return output path.

    *ignore_max_fps*: when True, do not apply *profile.max_fps* (keep source
    rate even if above the device cap — experimental; may break playback).

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
    max_fps = 0.0 if ignore_max_fps else float(profile.max_fps or 0)
    force_fps = output_fps_for_source(source_fps, max_fps)
    logger.info(
        "Video convert start src=%s dest=%s profile=%s duration=%.1fs "
        "source_fps=%.3f max_fps=%s force_fps=%s ignore_max_fps=%s",
        src_path,
        dest_path,
        profile.id,
        duration,
        source_fps,
        f"{max_fps:g}" if max_fps > 0 else "none",
        f"{force_fps:g}" if force_fps is not None else "keep",
        ignore_max_fps,
    )
    if on_progress is not None:
        try:
            on_progress(0.0, duration, "encoding for device…")
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    out_opts: dict = {
        "map": ["0:v:0", "0:a:0?"],
        "c:v": profile.video_codec,
        "vtag": profile.video_tag,
        "qscale:v": str(int(profile.qscale_v)),
        "vf": _vf_filter(profile, force_fps=force_fps),
        "c:a": profile.audio_codec,
        "b:a": profile.audio_bitrate,
        "ac": str(int(profile.audio_channels)),
        "ar": str(int(profile.audio_sample_rate)),
        "f": ext,
    }

    ff = FFmpeg().option("y").input(src_path).output(dest_path, out_opts)

    def _on_prog(p: Progress) -> None:
        if on_progress is None:
            return
        t = p.time
        if isinstance(t, timedelta):
            done = max(0.0, t.total_seconds())
        else:
            try:
                done = float(t or 0)
            except (TypeError, ValueError):
                done = 0.0
        total = duration if duration > 0 else 0.0
        if total > 0:
            done = min(done, total)
        try:
            on_progress(done, total, "encoding for device…")
        except Exception:
            logger.debug("video on_progress failed", exc_info=True)

    ff.on("progress", _on_prog)
    try:
        ff.execute()
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
        os.remove(path)
    except OSError as exc:
        logger.warning("Could not delete video temp %s: %s", path, exc)


def default_temp_video_path(
    profile: VideoEncodeProfile,
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
