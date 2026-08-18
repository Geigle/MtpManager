"""Public hooks for optional private GUID adapter (absent = no-op)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.infra import private_hooks
from mtpmanager.infra.library_index import save_library_index


def _track(path: str, **meta_kw) -> Track:
    guid = meta_kw.pop("guid", "")
    defaults = dict(
        artist="A",
        album="B",
        title="T",
        tracknumber="01",
        length_sec=1.0,
    )
    defaults.update(meta_kw)
    return Track(path=path, meta=TrackMetadata(**defaults), guid=guid)


class PrivateHooksAbsentTests(unittest.TestCase):
    def setUp(self) -> None:
        private_hooks._adapter = False  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        private_hooks._adapter = False  # type: ignore[attr-defined]

    def test_enrich_passthrough_when_absent(self) -> None:
        with mock.patch.object(
            private_hooks, "library_guid_adapter", return_value=None
        ):
            m = {"/a": "a" * 32}
            self.assertEqual(
                private_hooks.enrich_path_guid_map([], m),
                m,
            )
            self.assertEqual(private_hooks.obsolete_guid_paths(), {})

    def test_save_calls_hooks_when_adapter_present(self) -> None:
        g_old = new_track_guid()
        g_new = new_track_guid()
        adapter = mock.Mock()
        adapter.enrich_path_guid_map.side_effect = (
            lambda tracks, path_map: {list(tracks)[0].path: g_new}
        )
        adapter.after_library_saved = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f1.write_bytes(b"x")
            dest = Path(tmp) / "library_index.db"
            # Seed SQLite with old guid for the path.
            lib0 = Library(
                tracks=[_track(str(f1), guid=g_old)],
                root_paths=[str(root)],
            )
            with mock.patch.object(
                private_hooks, "library_guid_adapter", return_value=None
            ):
                save_library_index(lib0, path=dest)

            lib1 = Library(
                tracks=[_track(str(f1))],
                root_paths=[str(root)],
            )
            with mock.patch.object(
                private_hooks, "library_guid_adapter", return_value=adapter
            ):
                save_library_index(lib1, path=dest)

            self.assertEqual(lib1.tracks[0].guid, g_new)
            adapter.enrich_path_guid_map.assert_called()
            adapter.after_library_saved.assert_called()
            self.assertTrue(is_track_guid(lib1.tracks[0].guid))


if __name__ == "__main__":
    unittest.main()
