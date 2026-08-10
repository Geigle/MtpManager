"""ffmpeg_exec: stderr must not raise UnicodeDecodeError on bad UTF-8."""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from mtpmanager.infra.ffmpeg_exec import (
    decode_ffmpeg_bytes,
    run_ffmpeg_argv,
    run_ffmpeg_builder,
)


class FfmpegExecTests(unittest.TestCase):
    def test_decode_replaces_invalid_utf8(self) -> None:
        # Incomplete multi-byte sequence (classic "unexpected end of data").
        raw = b"title: Toxic Empathy\xe2\x80"
        text = decode_ffmpeg_bytes(raw)
        self.assertIn("Toxic Empathy", text)
        self.assertNotEqual(text, raw.decode("latin-1"))  # we use replace not latin-1

    def test_run_argv_succeeds_when_stderr_has_invalid_utf8(self) -> None:
        bad = b"Metadata:\n  USLT: " + b"\xe2\x80" + b" rest\n"
        fake = subprocess.CompletedProcess(
            args=["ffmpeg", "-y"],
            returncode=0,
            stdout=b"",
            stderr=bad,
        )
        with patch("mtpmanager.infra.ffmpeg_exec.subprocess.run", return_value=fake):
            proc = run_ffmpeg_argv(["ffmpeg", "-y", "-version"])
        self.assertEqual(proc.returncode, 0)
        # Decode path used by callers must not raise.
        _ = decode_ffmpeg_bytes(proc.stderr)

    def test_run_argv_raises_on_nonzero_with_replaced_stderr(self) -> None:
        bad = b"Error: broken \xff\xfe tag\n"
        fake = subprocess.CompletedProcess(
            args=["ffmpeg"],
            returncode=1,
            stdout=b"",
            stderr=bad,
        )
        with patch("mtpmanager.infra.ffmpeg_exec.subprocess.run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                run_ffmpeg_argv(["ffmpeg", "-i", "x", "y"])
        msg = str(ctx.exception)
        self.assertIn("rc=1", msg)

    def test_run_builder_uses_arguments(self) -> None:
        class _FF:
            arguments = ["ffmpeg", "-version"]

        # Real ffmpeg -version if available; else skip.
        try:
            out = run_ffmpeg_builder(_FF())
        except RuntimeError as e:
            if "not found" in str(e).lower():
                self.skipTest("ffmpeg not on PATH")
            raise
        self.assertIsInstance(out, (bytes, bytearray))


if __name__ == "__main__":
    unittest.main()
