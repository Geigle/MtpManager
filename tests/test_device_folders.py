"""Unit tests for name-based device folder layout resolution."""

from __future__ import annotations

import unittest

from mtpmanager.domain.device_folders import (
    DeviceFolderLayout,
    FolderRole,
    legacy_zen_vision_m_layout,
    resolve_device_folder_layout,
    role_for_folder_name,
)
from mtpmanager.domain.models import FolderEntry
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
)


def _folders(*pairs: tuple[int, str, int]) -> list[FolderEntry]:
    """(folder_id, name, parent_id) → FolderEntry list."""
    return [
        FolderEntry(folder_id=fid, name=name, parent_id=parent)
        for fid, name, parent in pairs
    ]


class RoleNameTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(role_for_folder_name("Music"), FolderRole.MUSIC)
        self.assertEqual(role_for_folder_name("VIDEO"), FolderRole.VIDEO)
        self.assertEqual(role_for_folder_name("TV"), FolderRole.TV)
        self.assertEqual(role_for_folder_name("My Playlists"), FolderRole.PLAYLISTS)
        self.assertIsNone(role_for_folder_name("Random Stuff"))


class LayoutResolveTests(unittest.TestCase):
    def test_legacy_fallback_empty_listing(self) -> None:
        layout = resolve_device_folder_layout([])
        self.assertEqual(layout.source, "fallback")
        self.assertEqual(layout.music_id, DEFAULT_MUSIC_FOLDER_ID)
        self.assertEqual(layout.video_id, DEFAULT_VIDEO_FOLDER_ID)
        self.assertEqual(layout.tv_id, DEFAULT_TV_FOLDER_ID)

    def test_documented_vision_m_map(self) -> None:
        # Classic Music=100 map from device-contract.md.
        folders = _folders(
            (100, "Music", 0),
            (104, "My Playlists", 0),
            (108, "My Recordings", 0),
            (112, "My Organizer", 0),
            (116, "Pictures", 0),
            (120, "Video", 0),
            (124, "TV", 0),
            (128, "ZENcast", 0),
            (132, "My Slideshows", 0),
        )
        layout = resolve_device_folder_layout(folders)
        self.assertEqual(layout.source, "listed")
        self.assertEqual(layout.music_id, 100)
        self.assertEqual(layout.video_id, 120)
        self.assertEqual(layout.tv_id, 124)
        self.assertEqual(layout.name_for(100), "Music")

    def test_alternate_firmware_music_88(self) -> None:
        # User-reported Vision:M 1.40.02 layout (Music is 88, not 100).
        folders = _folders(
            (88, "Music", 0),
            (92, "My Playlists", 0),
            (96, "My Recordings", 0),
            (100, "My Organizer", 0),
            (104, "Pictures", 0),
            (108, "Video", 0),
            (112, "TV", 0),
            (116, "ZENcast", 0),
            (120, "My Slideshows", 0),
        )
        layout = resolve_device_folder_layout(folders)
        self.assertEqual(layout.music_id, 88)
        self.assertEqual(layout.video_id, 108)
        self.assertEqual(layout.tv_id, 112)
        # Must not treat Organizer 100 as Music.
        self.assertNotEqual(layout.music_id, 100)
        self.assertEqual(layout.role_for_id(100), FolderRole.ORGANIZER)
        self.assertEqual(layout.video_folder_label(108), "Video")
        self.assertEqual(layout.video_folder_label(112), "TV")
        self.assertIn(108, layout.video_parent_ids())
        self.assertIn(112, layout.video_parent_ids())
        self.assertNotIn(88, layout.non_music_parent_ids())
        self.assertIn(108, layout.non_music_parent_ids())

    def test_prefers_top_level_over_nested_music(self) -> None:
        folders = _folders(
            (88, "Music", 0),
            (900, "Music", 88),  # nested/spurious
            (108, "Video", 0),
            (112, "TV", 0),
        )
        layout = resolve_device_folder_layout(folders)
        self.assertEqual(layout.music_id, 88)

    def test_legacy_helper(self) -> None:
        layout = legacy_zen_vision_m_layout()
        self.assertIsInstance(layout, DeviceFolderLayout)
        self.assertEqual(layout.music_id, 100)


if __name__ == "__main__":
    unittest.main()
