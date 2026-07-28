"""Host-side audio playback via ffplay (ffmpeg companion).

Uses a short-lived ``ffplay`` process with wall-clock position tracking so
pause / seek work without a heavy media framework dependency. Formats match
whatever the installed ffmpeg build can decode (FLAC, MP3, WAV, AAC, …).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Literal

logger = logging.getLogger(__name__)

PlayerState = Literal["idle", "playing", "paused", "ended"]


def ffplay_bin() -> str | None:
    """Return path to ``ffplay``, or None if not on PATH."""
    return shutil.which("ffplay")


class AudioPlayer:
    """Play one local audio file at a time with pause/seek support."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._path: str = ""
        self._duration: float = 0.0
        self._base_pos: float = 0.0
        self._started_at: float = 0.0
        self._paused: bool = False
        self._active: bool = False

    @property
    def path(self) -> str:
        return self._path

    @property
    def duration_sec(self) -> float:
        return self._duration

    @property
    def is_active(self) -> bool:
        """True while a track is playing or paused (controls should show)."""
        return self._active

    @property
    def is_playing(self) -> bool:
        return self._active and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._active and self._paused

    def position_sec(self) -> float:
        """Estimated playback position in seconds."""
        if not self._active:
            return 0.0
        if self._paused:
            pos = self._base_pos
        else:
            pos = self._base_pos + (time.monotonic() - self._started_at)
        if self._duration > 0:
            pos = min(pos, self._duration)
        return max(0.0, pos)

    def play(
        self,
        path: str,
        *,
        start_sec: float = 0.0,
        duration_sec: float = 0.0,
    ) -> None:
        """Start (or restart) playback of *path* from *start_sec*."""
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"audio file not found: {path!r}")
        bin_path = ffplay_bin()
        if not bin_path:
            raise RuntimeError(
                "ffplay not found on PATH. Install ffmpeg (includes ffplay) "
                "to enable library playback."
            )
        self.stop()
        self._path = path
        self._duration = max(0.0, float(duration_sec or 0.0))
        self._base_pos = max(0.0, float(start_sec or 0.0))
        if self._duration > 0:
            self._base_pos = min(self._base_pos, self._duration)
        self._paused = False
        self._active = True
        self._spawn(bin_path)
        logger.info(
            "Playback start path=%s start=%.2fs duration=%.2fs",
            path,
            self._base_pos,
            self._duration,
        )

    def pause(self) -> None:
        if not self._active or self._paused:
            return
        pos = self.position_sec()
        self._terminate_proc()
        self._base_pos = pos
        self._paused = True

    def resume(self) -> None:
        if not self._active or not self._paused:
            return
        if self._duration > 0 and self._base_pos >= self._duration - 0.05:
            # At end while paused — treat resume as restart from 0.
            self._base_pos = 0.0
        bin_path = ffplay_bin()
        if not bin_path:
            self.stop()
            raise RuntimeError("ffplay not found on PATH")
        self._paused = False
        self._spawn(bin_path)

    def toggle_pause(self) -> None:
        if not self._active:
            return
        if self._paused:
            self.resume()
        else:
            self.pause()

    def seek(self, position_sec: float) -> None:
        """Jump to *position_sec* (clamped). Keeps pause/play state."""
        if not self._active or not self._path:
            return
        pos = max(0.0, float(position_sec))
        if self._duration > 0:
            pos = min(pos, max(0.0, self._duration - 0.05))
        was_paused = self._paused
        self._terminate_proc()
        self._base_pos = pos
        if was_paused:
            self._paused = True
            return
        bin_path = ffplay_bin()
        if not bin_path:
            self.stop()
            raise RuntimeError("ffplay not found on PATH")
        self._paused = False
        self._spawn(bin_path)

    def stop(self) -> None:
        """End playback and clear active state."""
        self._terminate_proc()
        self._active = False
        self._paused = False
        self._base_pos = 0.0
        self._started_at = 0.0
        self._path = ""
        self._duration = 0.0

    def poll(self) -> PlayerState:
        """Check process state; returns ``ended`` once when a track finishes."""
        if not self._active:
            return "idle"
        if self._paused:
            return "paused"
        if self._proc is None:
            self._active = False
            return "ended"
        code = self._proc.poll()
        if code is None:
            # Guard against clock drift past known duration.
            if self._duration > 0 and self.position_sec() >= self._duration:
                self._terminate_proc()
                self._active = False
                self._base_pos = self._duration
                return "ended"
            return "playing"
        # Process exited.
        self._proc = None
        self._active = False
        return "ended"

    def _spawn(self, bin_path: str) -> None:
        cmd: list[str] = [
            bin_path,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
        ]
        if self._base_pos > 0.05:
            cmd.extend(["-ss", f"{self._base_pos:.3f}"])
        cmd.append(self._path)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            self._proc = None
            self._active = False
            self._paused = False
            raise RuntimeError(f"failed to start ffplay: {e}") from e
        self._started_at = time.monotonic()

    def _terminate_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
