"""Unit tests for durable app config."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtpmanager.infra.app_config import (
    AppConfig,
    load_app_config,
    save_app_config,
)


class AppConfigTests(unittest.TestCase):
    def test_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_app_config(path=Path(tmp) / "nope.json")
            self.assertEqual(cfg.normalized_send_format(), "mp3")

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            save_app_config(AppConfig(send_format="wma"), path=dest)
            loaded = load_app_config(path=dest)
            self.assertEqual(loaded.normalized_send_format(), "wma")
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["send_format"], "wma")

    def test_invalid_format_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text(
                json.dumps({"version": 1, "send_format": "flac"}),
                encoding="utf-8",
            )
            cfg = load_app_config(path=dest)
            self.assertEqual(cfg.normalized_send_format(), "mp3")


if __name__ == "__main__":
    unittest.main()
