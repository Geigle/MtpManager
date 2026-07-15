"""ffmpeg-based audio transcoder."""

from __future__ import annotations

import logging
import os
import tempfile

from ffmpeg import FFmpeg

from mtpmanager.domain.library import is_format

logger = logging.getLogger(__name__)


class FFmpegTranscoder:
    def __init__(self, temp_dir: str | None = None):
        self.temp_dir = temp_dir or tempfile.gettempdir()

    def convert(self, src_path: str, target_format: str) -> str:
        target_format = target_format.lower().lstrip(".")
        if is_format(src_path, target_format):
            return src_path

        output_file = os.path.join(self.temp_dir, f"TRANSCODE.{target_format}")
        if os.path.exists(output_file):
            self.cleanup(output_file)

        output_details: dict
        if target_format == "wma":
            output_details = {"codec:a": "wmav2"}
        else:
            output_details = {"qscale:a": "0"}

        logger.info("Converting %s to %s", src_path, output_file)
        ffmpeg = FFmpeg().input(src_path).output(output_file, output_details)
        try:
            ffmpeg.execute()
        except Exception as e:
            logger.error("FFMPEG FAILED: %s", e)
            raise
        logger.info("Done converting %s", src_path)
        return output_file

    def cleanup(self, path: str | None) -> None:
        if not path:
            return
        # Never delete the original source — only known temp outputs
        base = os.path.basename(path)
        if not base.startswith("TRANSCODE."):
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
