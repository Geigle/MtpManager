"""CLI transport via libmtp's mtp-sendtr (no shell interpolation)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    build_remote_path,
    year_arg,
)
from mtpmanager.ports.transport import TransportError

# Re-export for callers that imported build_remote_path from this module.
__all__ = ["CmdTransport", "TransportError", "build_remote_path"]

logger = logging.getLogger(__name__)
mtp_sendtr_log = logging.getLogger(__name__ + ".mtp_sendtr")

# Patterns that indicate the device/session is dead or this send cannot complete.
_FATAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"PTP I/O Error",
        r"Error 02ff",
        r"all device storage is full or corrupt",
        r"Could not close session",
        r"usb_get_endpoint_status",
        r"LIBMTP_Send_Track_From_File",
        r"Could not retrieve updated metadata",
        r"check_if_file_fits\(\):\s*error checking free storage",
        r"get_storage_freespace\(\):\s*could not get storage info",
        r"get_writeable_storageid\(\)",
        r"get_suggested_storage_id\(\)",
        r"add_object_to_cache\(\)",
        r"Error sending track",
        r"Parent folder could not be found",
    )
)

_FAIL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"LIBMTP_Send_File",
        r"Could not send object",
        r"Sending track failed",
        r"Unable to open",
        r"No raw devices found",
        r"Device .* not found",
    )
)

# After a known hard failure, mtp-sendtr often hangs in album association
# (LIBMTP_Get_Album_List). Kill instead of waiting out the full transfer timeout.
_POST_FATAL_GRACE_SEC = 8.0

_DEFAULT_TIMEOUT_SEC = 300.0
_MIN_TIMEOUT_SEC = 90.0
_MAX_TIMEOUT_SEC = 900.0
_BYTES_PER_SEC_ASSUMPTION = 256 * 1024
_TIMEOUT_OVERHEAD_SEC = 60.0


def _timeout_for(path: str, override: float | None) -> float:
    if override is not None:
        return max(1.0, float(override))
    try:
        size = os.path.getsize(path)
    except OSError:
        return _DEFAULT_TIMEOUT_SEC
    scaled = size / _BYTES_PER_SEC_ASSUMPTION + _TIMEOUT_OVERHEAD_SEC
    return min(_MAX_TIMEOUT_SEC, max(_MIN_TIMEOUT_SEC, scaled))


def _match_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _duration_arg(length_sec: float) -> str:
    try:
        return str(max(0, int(round(float(length_sec)))))
    except (TypeError, ValueError):
        return "0"


class _StreamWatch:
    """Collect output, tee live, and signal when a fatal pattern appears."""

    def __init__(self) -> None:
        self.out_lines: list[str] = []
        self.err_lines: list[str] = []
        self.fatal_hit: str | None = None
        self.fatal_at: float | None = None
        self._lock = threading.Lock()

    def combined(self) -> str:
        with self._lock:
            return "".join(self.out_lines) + "".join(self.err_lines)

    def note_line(self, line: str, *, is_err: bool) -> None:
        with self._lock:
            (self.err_lines if is_err else self.out_lines).append(line)
            if self.fatal_hit is None:
                hit = _match_any(line, _FATAL_PATTERNS)
                if hit:
                    self.fatal_hit = hit
                    self.fatal_at = time.monotonic()


def _tee_stream(stream, watch: _StreamWatch, *, is_err: bool) -> None:
    """Collect lines for pattern matching and log each at DEBUG.

    Console visibility is controlled by the StreamHandler level (INFO by default;
    DEBUG when MTP_MANAGER_DEBUG=1). File handlers always capture DEBUG.
    """
    try:
        for line in iter(stream.readline, ""):
            watch.note_line(line, is_err=is_err)
            stripped = line.rstrip("\n\r")
            if stripped:
                mtp_sendtr_log.debug("%s", stripped)
    finally:
        stream.close()


def _run_sendtr(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run mtp-sendtr, stream output to logs, kill on hang or post-fatal stall."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    watch = _StreamWatch()
    threads = [
        threading.Thread(
            target=_tee_stream,
            args=(proc.stdout, watch),
            kwargs={"is_err": False},
            daemon=True,
        ),
        threading.Thread(
            target=_tee_stream,
            args=(proc.stderr, watch),
            kwargs={"is_err": True},
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    deadline = time.monotonic() + timeout
    killed_for_fatal = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    cmd=cmd,
                    timeout=timeout,
                    output="".join(watch.out_lines),
                    stderr="".join(watch.err_lines),
                )
            try:
                proc.wait(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                if (
                    watch.fatal_hit is not None
                    and watch.fatal_at is not None
                    and (time.monotonic() - watch.fatal_at) >= _POST_FATAL_GRACE_SEC
                    and proc.poll() is None
                ):
                    killed_for_fatal = True
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    break
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for t in threads:
            t.join(timeout=2)
        raise

    for t in threads:
        t.join(timeout=5)

    output = watch.combined()
    returncode = proc.returncode if proc.returncode is not None else -1
    if killed_for_fatal:
        # Distinct non-zero so send_track reports the fatal pattern, not success.
        returncode = returncode if returncode not in (0, None) else 1

    result = subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout="".join(watch.out_lines),
        stderr="".join(watch.err_lines),
    )
    # Stash for callers that want the matched pattern (optional attribute).
    result.fatal_hit = watch.fatal_hit  # type: ignore[attr-defined]
    result.killed_for_fatal = killed_for_fatal  # type: ignore[attr-defined]
    _ = output
    return result


class CmdTransport:
    """Send tracks using the mtp-sendtr example program."""

    def __init__(
        self,
        binary: str = "mtp-sendtr",
        *,
        timeout_sec: float | None = None,
        storage_id: int = DEFAULT_STORAGE_ID,
        music_folder_id: int = DEFAULT_MUSIC_FOLDER_ID,
    ):
        self.binary = binary
        self.timeout_sec = timeout_sec
        self.storage_id = storage_id
        self.music_folder_id = music_folder_id

    def send_track(
        self,
        path: str,
        meta: TrackMetadata,
        *,
        parent_id: int | None = None,
        guid: str | None = None,
        preferred_basename: str | None = None,
    ) -> int | None:
        _, file_extension = os.path.splitext(path)
        # GUID mode: always flat under Music (ignore artist/album parents).
        if guid:
            folder_id = int(self.music_folder_id)
        else:
            folder_id = (
                int(parent_id) if parent_id is not None else int(self.music_folder_id)
            )
        remote = build_remote_path(
            meta,
            file_extension or ".mp3",
            music_folder_id=folder_id,
            guid=guid,
            preferred_basename=preferred_basename,
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
            file_extension or ".mp3",
            "-g",
            str(meta.genre),
            "-n",
            str(meta.tracknumber),
            "-y",
            year_arg(meta.date),
            "-d",
            _duration_arg(meta.length_sec),
            "-s",
            str(self.storage_id),
            path,
            remote,
        ]
        logger.debug(
            "CMD: %s",
            " ".join(cmd),
        )
        logger.debug(
            "Remote object: %s (storage=0x%08x)",
            remote,
            self.storage_id,
        )
        timeout = _timeout_for(path, self.timeout_sec)
        try:
            result = _run_sendtr(cmd, timeout)
        except FileNotFoundError as exc:
            logger.error(
                "mtp-sendtr binary not found: %s path=%s",
                self.binary,
                path,
            )
            raise TransportError(
                f"mtp-sendtr binary not found: {self.binary}",
                fatal=True,
                path=path,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.output:
                partial += (
                    exc.output
                    if isinstance(exc.output, str)
                    else exc.output.decode("utf-8", errors="replace")
                )
            if exc.stderr:
                if partial:
                    partial += "\n"
                partial += (
                    exc.stderr
                    if isinstance(exc.stderr, str)
                    else exc.stderr.decode("utf-8", errors="replace")
                )
            fatal_hit = _match_any(partial, _FATAL_PATTERNS)
            if fatal_hit:
                logger.error(
                    "mtp-sendtr failed path=%s remote=%s storage=0x%08x "
                    "rc=timeout fatal=%s\n%s",
                    path,
                    remote,
                    self.storage_id,
                    fatal_hit,
                    partial,
                )
                raise TransportError(
                    f"mtp-sendtr failed then hung ({fatal_hit}). "
                    f"Often a finalize/metadata error at ~99% on Creative ZEN. "
                    f"Path: {path}",
                    fatal=True,
                    path=path,
                    stderr=partial,
                ) from exc
            logger.error(
                "mtp-sendtr timed out path=%s remote=%s storage=0x%08x "
                "timeout=%.0fs\n%s",
                path,
                remote,
                self.storage_id,
                timeout,
                partial,
            )
            raise TransportError(
                f"mtp-sendtr timed out after {timeout:.0f}s (device likely hung). "
                f"Unplug/replug the player before retrying. Path: {path}",
                fatal=True,
                path=path,
                stderr=partial,
            ) from exc

        output = (result.stdout or "") + (
            "\n" if result.stdout and result.stderr else ""
        ) + (result.stderr or "")

        fatal_hit = getattr(result, "fatal_hit", None) or _match_any(
            output, _FATAL_PATTERNS
        )
        fail_hit = _match_any(output, _FAIL_PATTERNS)
        killed = getattr(result, "killed_for_fatal", False)

        if result.returncode != 0 or fatal_hit or fail_hit or killed:
            if fatal_hit:
                reason = f"fatal MTP error ({fatal_hit})"
            elif killed:
                reason = "killed after fatal MTP diagnostics (post-send hang)"
            elif result.returncode != 0:
                reason = f"exit code {result.returncode}"
            else:
                reason = f"send failed ({fail_hit})"

            logger.error(
                "mtp-sendtr failed path=%s remote=%s storage=0x%08x rc=%s fatal=%s\n%s",
                path,
                remote,
                self.storage_id,
                result.returncode,
                fatal_hit or fail_hit or killed,
                output,
            )
            raise TransportError(
                f"mtp-sendtr failed: {reason}. Remote={remote}. Path: {path}",
                fatal=True,
                path=path,
                stderr=output,
                returncode=result.returncode,
            )
        return None
