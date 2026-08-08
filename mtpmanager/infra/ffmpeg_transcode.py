"""ffmpeg-based audio transcoder with dual temp-file slots."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any

from ffmpeg import FFmpeg

from mtpmanager.domain.audio_encode import (
    AudioEncodeSettings,
    clamp_settings_for_format,
)
from mtpmanager.domain.library import is_format

logger = logging.getLogger(__name__)

# TRANSCODE_0.mp3 / TRANSCODE_1.wma — bounce slots so convert N+1 cannot
# clobber the file still being sent for track N.
_TEMP_NAME_RE = re.compile(r"^TRANSCODE(?:_[01])?\.[A-Za-z0-9]+$")
NUM_SLOTS = 2


def _audio_only_map_options() -> dict[str, Any]:
    """Never carry FLAC/MP4 cover-art video streams into temp audio files.

    Default ffmpeg stream mapping copies attached pictures (often a large
    MJPEG/PNG "video" stream). That makes low-bitrate MP3s look huge while
    the audio is actually compressed — and confuses device bitrate metadata.
    """
    return {
        # First audio stream only (? = optional on newer ffmpeg; omit if
        # the host ffmpeg is ancient and rejects it — see convert fallback).
        "map": "0:a:0",
        "vn": None,
        "sn": None,
        "dn": None,
        # Drop global metadata/chapters that can re-embed huge APIC frames.
        "map_metadata": "-1",
        "map_chapters": "-1",
    }


def build_ffmpeg_audio_options(settings: AudioEncodeSettings) -> dict[str, Any]:
    """Map *settings* to python-ffmpeg output kwargs (audio only)."""
    s = clamp_settings_for_format(settings)
    fmt = s.normalized_format()
    opts: dict[str, Any] = dict(_audio_only_map_options())

    if fmt == "mp3":
        opts["codec:a"] = "libmp3lame"
        if s.rate_control == "vbr" and s.vbr_quality is not None:
            # LAME VBR quality 0 (best) … 9 (worst). Prefer qscale:a — the
            # name python-ffmpeg / ffmpeg docs use for libmp3lame VBR.
            q = max(0, min(9, int(round(float(s.vbr_quality)))))
            opts["qscale:a"] = str(q)
        else:
            br = int(s.bitrate_kbps or 192)
            opts["b:a"] = f"{br}k"
            if s.rate_control == "abr":
                # Approximate ABR via VBR constrained around target.
                opts["abr"] = "1"

    elif fmt == "wma":
        opts["codec:a"] = "wmav2"
        br = int(s.bitrate_kbps or 128)
        opts["b:a"] = f"{br}k"

    elif fmt == "wav":
        depth = s.bit_depth or 16
        if depth >= 32:
            opts["codec:a"] = "pcm_s32le"
        elif depth >= 24:
            opts["codec:a"] = "pcm_s24le"
        else:
            opts["codec:a"] = "pcm_s16le"

    elif fmt == "flac":
        opts["codec:a"] = "flac"
        level = s.compression_level if s.compression_level is not None else 5
        opts["compression_level"] = str(max(0, min(12, int(level))))
        # Sample format hint when we force bit depth.
        if s.bit_depth and s.bit_depth >= 24:
            opts["sample_fmt"] = "s32"
        else:
            opts["sample_fmt"] = "s16"

    elif fmt in ("aac", "m4a"):
        opts["codec:a"] = "aac"
        if s.rate_control == "vbr" and s.vbr_quality is not None:
            # ffmpeg aac encoder quality roughly 0.1–2.
            q = max(0.1, min(2.0, float(s.vbr_quality)))
            opts["q:a"] = str(q)
        else:
            br = int(s.bitrate_kbps or 192)
            opts["b:a"] = f"{br}k"

    elif fmt == "ogg":
        opts["codec:a"] = "libvorbis"
        if s.vbr_quality is not None:
            q = max(0.0, min(10.0, float(s.vbr_quality)))
            opts["q:a"] = str(q)
        elif s.bitrate_kbps:
            opts["b:a"] = f"{int(s.bitrate_kbps)}k"
        else:
            opts["q:a"] = "4"

    elif fmt == "opus":
        opts["codec:a"] = "libopus"
        br = int(s.bitrate_kbps or 96)
        opts["b:a"] = f"{br}k"
        # Prefer VBR for Opus unless user forced CBR.
        if s.rate_control == "cbr":
            opts["vbr"] = "off"
        else:
            opts["vbr"] = "on"

    else:
        # Fallback: extension-driven mux + high quality MP3-ish.
        opts["qscale:a"] = "0"

    if s.sample_rate and s.sample_rate > 0:
        opts["ar"] = str(int(s.sample_rate))
    if s.channels in (1, 2):
        opts["ac"] = str(int(s.channels))

    return opts


class FFmpegTranscoder:
    def __init__(self, temp_dir: str | None = None):
        self.temp_dir = temp_dir or tempfile.gettempdir()

    def temp_path(self, target_format: str, *, slot: int = 0) -> str:
        """Return the fixed temp path for a dual-buffer *slot* (0 or 1)."""
        target_format = target_format.lower().lstrip(".")
        slot = int(slot) % NUM_SLOTS
        return os.path.join(self.temp_dir, f"TRANSCODE_{slot}.{target_format}")

    def convert(
        self,
        src_path: str,
        target_format: str,
        *,
        slot: int = 0,
        settings: AudioEncodeSettings | None = None,
    ) -> str:
        """Transcode *src_path* into dual-buffer *slot*; return path to send.

        If *src_path* is already the target format, returns *src_path* unchanged
        (caller must not cleanup the original).

        When *settings* is provided, ffmpeg options come from the encode
        recipe (bitrate, VBR quality, channels, sample rate, etc.). Otherwise
        a format-only default is used (legacy behavior).
        """
        if settings is not None:
            s = clamp_settings_for_format(settings)
            target_format = s.file_extension()
        else:
            target_format = target_format.lower().lstrip(".")
            s = None

        if is_format(src_path, target_format):
            return src_path

        output_file = self.temp_path(target_format, slot=slot)
        if os.path.exists(output_file):
            self.cleanup(output_file)

        if s is not None:
            output_details = build_ffmpeg_audio_options(s)
        else:
            output_details = _legacy_format_options(target_format)

        logger.info(
            "Converting %s → %s (slot=%d, %s) opts=%s",
            src_path,
            output_file,
            int(slot) % NUM_SLOTS,
            (s.summary_line() if s is not None else target_format),
            {k: v for k, v in output_details.items() if k not in ()},
        )
        try:
            FFmpeg().input(src_path).output(output_file, output_details).execute()
        except Exception as e:
            # Older ffmpeg builds may reject map_chapters / optional map syntax.
            logger.warning(
                "FFMPEG convert failed (%s); retrying with minimal audio-only map",
                e,
            )
            retry = {
                k: v
                for k, v in output_details.items()
                if k not in ("map_chapters", "map_metadata")
            }
            retry["map"] = "0:a:0"
            retry["vn"] = None
            try:
                if os.path.exists(output_file):
                    self.cleanup(output_file)
                FFmpeg().input(src_path).output(output_file, retry).execute()
            except Exception as e2:
                logger.error("FFMPEG FAILED: %s", e2)
                raise
        logger.info("Done converting %s", src_path)
        return output_file

    def extract_audio(
        self,
        src_path: str,
        dest_path: str,
        *,
        target_format: str = "mp3",
        settings: AudioEncodeSettings | None = None,
    ) -> str:
        """Demux/encode audio only from a media file (e.g. video podcast).

        Writes *dest_path* and returns it. Always re-encodes (does not
        short-circuit on source extension).
        """
        if settings is not None:
            s = clamp_settings_for_format(settings)
            target_format = s.file_extension()
            output_details = build_ffmpeg_audio_options(s)
        else:
            target_format = (target_format or "mp3").lower().lstrip(".")
            s = None
            output_details = _legacy_format_options(target_format)

        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass

        logger.info(
            "Extracting audio %s → %s (%s)",
            src_path,
            dest_path,
            s.summary_line() if s is not None else target_format,
        )
        try:
            FFmpeg().input(src_path).output(dest_path, output_details).execute()
        except Exception as e:
            logger.error("FFMPEG audio extract failed: %s", e)
            raise
        if not os.path.isfile(dest_path):
            raise RuntimeError(f"ffmpeg produced no audio for {src_path}")
        logger.info("Done extracting audio → %s", dest_path)
        return dest_path

    def cleanup(self, path: str | None) -> None:
        if not path:
            return
        # Never delete the original source — only known temp outputs
        base = os.path.basename(path)
        if not _TEMP_NAME_RE.match(base):
            return
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            logger.warning("%s not found for deletion.", path)
        except PermissionError:
            logger.warning("No permission to delete %s", path)
        except Exception as e:
            logger.warning("Error while deleting %s: %s", path, e)


def _legacy_format_options(target_format: str) -> dict[str, Any]:
    """Pre-preset defaults (match historical MtpManager behavior)."""
    opts = dict(_audio_only_map_options())
    if target_format == "wma":
        opts["codec:a"] = "wmav2"
    elif target_format == "wav":
        opts["codec:a"] = "pcm_s16le"
    elif target_format == "flac":
        opts["codec:a"] = "flac"
    elif target_format in ("aac", "m4a"):
        opts["codec:a"] = "aac"
        opts["b:a"] = "192k"
    elif target_format == "ogg":
        opts["codec:a"] = "libvorbis"
        opts["q:a"] = "4"
    elif target_format == "opus":
        opts["codec:a"] = "libopus"
        opts["b:a"] = "128k"
    else:
        # Default: MP3 via high-quality VBR (legacy qscale:a 0).
        opts["codec:a"] = "libmp3lame"
        opts["qscale:a"] = "0"
    return opts
