"""ffmpeg-based audio transcoder with dual temp-file slots."""

from __future__ import annotations

import logging
import os
import re
import tempfile

from ffmpeg import FFmpeg

from mtpmanager.domain.library import is_format

logger = logging.getLogger(__name__)

# TRANSCODE_0.mp3 / TRANSCODE_1.wma — bounce slots so convert N+1 cannot
# clobber the file still being sent for track N.
_TEMP_NAME_RE = re.compile(r"^TRANSCODE(?:_[01])?\.[A-Za-z0-9]+$")
NUM_SLOTS = 2


class FFmpegTranscoder:
    def __init__(self, temp_dir: str | None = None):
        self.temp_dir = temp_dir or tempfile.gettempdir()

    def temp_path(self, target_format: str, *, slot: int = 0) -> str:
        """Return the fixed temp path for a dual-buffer *slot* (0 or 1)."""
        target_format = target_format.lower().lstrip(".")
        slot = int(slot) % NUM_SLOTS
        return os.path.join(self.temp_dir, f"TRANSCODE_{slot}.{target_format}")

    def convert(self, src_path: str, target_format: str, *, slot: int = 0) -> str:
        """Transcode *src_path* into dual-buffer *slot*; return path to send.

        If *src_path* is already the target format, returns *src_path* unchanged
        (caller must not cleanup the original).
        """
        target_format = target_format.lower().lstrip(".")
        if is_format(src_path, target_format):
            return src_path

        output_file = self.temp_path(target_format, slot=slot)
        if os.path.exists(output_file):
            self.cleanup(output_file)

        if target_format == "wma":
            output_details: dict = {"codec:a": "wmav2"}
        elif target_format == "wav":
            # PCM WAV — widely accepted by older DAP / MTP players.
            output_details = {"codec:a": "pcm_s16le"}
        else:
            # Default: MP3 (or other) via ffmpeg's extension-based muxer.
            output_details = {"qscale:a": "0"}

        logger.info(
            "Converting %s → %s (slot=%d)",
            src_path,
            output_file,
            int(slot) % NUM_SLOTS,
        )
        ffmpeg = FFmpeg().input(src_path).output(output_file, output_details)
        try:
            ffmpeg.execute()
        except Exception as e:
            logger.error("FFMPEG FAILED: %s", e)
            raise
        logger.info("Done converting %s", src_path)
        return output_file

    def extract_audio(
        self,
        src_path: str,
        dest_path: str,
        *,
        target_format: str = "mp3",
    ) -> str:
        """Demux/encode audio only from a media file (e.g. video podcast).

        Writes *dest_path* and returns it. Always re-encodes (does not
        short-circuit on source extension).
        """
        target_format = (target_format or "mp3").lower().lstrip(".")
        if target_format == "wma":
            output_details: dict = {"vn": None, "codec:a": "wmav2"}
        elif target_format == "wav":
            output_details = {"vn": None, "codec:a": "pcm_s16le"}
        else:
            output_details = {"vn": None, "qscale:a": "0"}

        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass

        logger.info("Extracting audio %s → %s", src_path, dest_path)
        ffmpeg = FFmpeg().input(src_path).output(dest_path, output_details)
        try:
            ffmpeg.execute()
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
