"""CLI transport via libmtp's mtp-sendtr (no shell interpolation)."""

from __future__ import annotations

import os
import subprocess

from mtpmanager.domain.models import TrackMetadata


class CmdTransport:
    """Send tracks using the mtp-sendtr example program."""

    def __init__(self, binary: str = "mtp-sendtr"):
        self.binary = binary

    def send_track(self, path: str, meta: TrackMetadata) -> None:
        _, file_extension = os.path.splitext(path)
        remote = (
            f"Music/{meta.artist}/{meta.album}/"
            f"{meta.artist} - {meta.album} - {meta.tracknumber} {meta.title}"
        )
        cmd = [
            self.binary,
            "-q",
            "-t",
            str(meta.title),
            "-a",
            str(meta.artist),
            "-A",
            str(meta.albumartist),
            "-w",
            str(meta.composer),
            "-l",
            str(meta.album),
            "-c",
            file_extension,
            "-g",
            str(meta.genre),
            "-n",
            str(meta.tracknumber),
            "-y",
            str(meta.date),
            "-d",
            str(meta.length_sec),
            path,
            remote,
        ]
        print("CMD:", " ".join(cmd))
        subprocess.run(cmd, check=False)
