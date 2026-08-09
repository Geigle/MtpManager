from __future__ import annotations

from typing import Protocol

from mtpmanager.domain.audio_encode import AudioEncodeSettings


class Transcoder(Protocol):
    def convert(
        self,
        src_path: str,
        target_format: str,
        *,
        slot: int = 0,
        settings: AudioEncodeSettings | None = None,
        force: bool = False,
    ) -> str:
        """Transcode src to target_format into dual-buffer *slot*; return path to send.

        Implementations should use at least two slots (0/1) so convert of track
        N+1 cannot clobber the temp file still being transferred for track N.

        When *settings* is set, bitrate/VBR/channels/etc. come from the recipe;
        *target_format* should match ``settings.file_extension()``.

        *force*: always re-encode even when the source already has *target_format*
        (used by Shrink for lossy→lossy downsizes).
        """
        ...

    def cleanup(self, path: str | None) -> None:
        """Remove a temp file produced by convert, if any."""
        ...
