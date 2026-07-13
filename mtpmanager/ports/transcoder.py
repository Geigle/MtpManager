from __future__ import annotations

from typing import Protocol


class Transcoder(Protocol):
    def convert(self, src_path: str, target_format: str) -> str:
        """Transcode src to target_format; return path of output file to send."""
        ...

    def cleanup(self, path: str | None) -> None:
        """Remove a temp file produced by convert, if any."""
        ...
