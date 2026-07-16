from __future__ import annotations

from typing import Protocol


class Transcoder(Protocol):
    def convert(self, src_path: str, target_format: str, *, slot: int = 0) -> str:
        """Transcode src to target_format into dual-buffer *slot*; return path to send.

        Implementations should use at least two slots (0/1) so convert of track
        N+1 cannot clobber the temp file still being transferred for track N.
        """
        ...

    def cleanup(self, path: str | None) -> None:
        """Remove a temp file produced by convert, if any."""
        ...
