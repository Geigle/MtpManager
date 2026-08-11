"""Run ffmpeg without strict UTF-8 stderr decoding.

python-ffmpeg's ``FFmpeg.execute()`` does ``line.decode()`` (strict UTF-8) on
every stderr line. When ffmpeg dumps ID3 frames (e.g. truncated USLT lyrics)
it often emits non-UTF-8 byte sequences. The convert itself can succeed while
``execute()`` raises ``UnicodeDecodeError`` and aborts the batch.

Use :func:`run_ffmpeg_builder` (or :func:`run_ffmpeg_argv`) so stderr is
decoded with ``errors="replace"`` and failures surface real ffmpeg exit codes.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

ProgressLineCallback = Callable[[str], None]


def decode_ffmpeg_bytes(data: bytes | None) -> str:
    """Decode ffmpeg stdout/stderr; never raise on invalid UTF-8."""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def run_ffmpeg_argv(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    on_stderr_line: ProgressLineCallback | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run an ffmpeg argv list; raise RuntimeError on non-zero exit.

    When *on_stderr_line* is set, streams stderr line-by-line (for progress
    parsing). Otherwise captures full stdout/stderr in one shot.
    """
    cmd = [str(a) for a in args]
    logger.debug("ffmpeg argv: %s", " ".join(cmd))

    if on_stderr_line is None:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg not found on PATH (install ffmpeg / Homebrew)"
            ) from exc
        if proc.returncode != 0:
            err = decode_ffmpeg_bytes(proc.stderr).strip()
            last = err.splitlines()[-1] if err else f"exit {proc.returncode}"
            logger.error(
                "ffmpeg failed rc=%s stderr tail:\n%s",
                proc.returncode,
                err[-2500:] if err else "(empty)",
            )
            raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {last}")
        return proc

    # Streaming stderr for progress (video encode).
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg not found on PATH (install ffmpeg / Homebrew)"
        ) from exc

    assert proc.stderr is not None
    stderr_chunks: list[bytes] = []
    try:
        for raw in proc.stderr:
            # Popen with bufsize=0 still yields chunks; split on lines.
            stderr_chunks.append(raw)
            text = decode_ffmpeg_bytes(raw)
            for line in text.splitlines():
                if line:
                    try:
                        on_stderr_line(line)
                    except Exception:
                        logger.debug("on_stderr_line failed", exc_info=True)
        stdout = proc.stdout.read() if proc.stdout else b""
        rc = proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    finally:
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass

    stderr = b"".join(stderr_chunks)
    if rc != 0:
        err = decode_ffmpeg_bytes(stderr).strip()
        last = err.splitlines()[-1] if err else f"exit {rc}"
        logger.error(
            "ffmpeg failed rc=%s stderr tail:\n%s",
            rc,
            err[-2500:] if err else "(empty)",
        )
        raise RuntimeError(f"ffmpeg failed (rc={rc}): {last}")
    return subprocess.CompletedProcess(cmd, rc, stdout or b"", stderr)


def run_ffmpeg_builder(
    ff: Any,
    *,
    timeout: float | None = None,
    on_stderr_line: ProgressLineCallback | None = None,
) -> bytes:
    """Execute a python-ffmpeg ``FFmpeg`` builder via safe argv run.

    Builds argv from ``ff.arguments`` (same flags as ``execute()``) but does
    not use python-ffmpeg's strict UTF-8 stderr reader.
    """
    args = list(getattr(ff, "arguments", None) or [])
    if not args:
        raise RuntimeError("ffmpeg builder produced empty arguments")
    proc = run_ffmpeg_argv(
        args, timeout=timeout, on_stderr_line=on_stderr_line
    )
    return proc.stdout or b""
