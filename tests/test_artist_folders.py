"""Unit tests for experimental artist folder ensure (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.app.artist_folders import (
    artist_folder_name,
    ensure_artist_folder,
    find_child_folder,
)
from mtpmanager.domain.models import FolderEntry, TrackMetadata
from mtpmanager.infra.remote_naming import DEFAULT_MUSIC_FOLDER_ID


class _FakeDevice:
    def __init__(self, folders: list[FolderEntry] | None = None) -> None:
        self.folders = list(folders or [])
        self.created: list[tuple[str, int]] = []
        self._next_id = 5000

    def list_folders(self) -> list[FolderEntry]:
        return list(self.folders)

    def create_folder(self, name: str, parent: int = 100) -> int:
        self._next_id += 1
        fid = self._next_id
        self.created.append((name, parent))
        self.folders.append(
            FolderEntry(folder_id=fid, name=name, parent_id=parent)
        )
        return fid


class ArtistFolderTests(unittest.TestCase):
    def test_artist_folder_name_prefers_albumartist(self) -> None:
        meta = TrackMetadata(
            artist="Main feat. Guest",
            albumartist="Main Band",
            title="T",
        )
        self.assertEqual(artist_folder_name(meta), "Main Band")

    def test_find_child_folder(self) -> None:
        dev = _FakeDevice(
            [
                FolderEntry(100, "Music", 0),
                FolderEntry(200, "Blind Guardian", 100),
                FolderEntry(201, "Other", 100),
            ]
        )
        self.assertEqual(
            find_child_folder(dev, name="Blind Guardian", parent_id=100),
            200,
        )
        self.assertIsNone(
            find_child_folder(dev, name="Blind Guardian", parent_id=0)
        )

    def test_ensure_creates_once_and_caches(self) -> None:
        dev = _FakeDevice([FolderEntry(100, "Music", 0)])
        meta = TrackMetadata(artist="Relient K", albumartist="Relient K")
        cache: dict[str, int] = {}
        a = ensure_artist_folder(dev, meta, cache=cache)
        b = ensure_artist_folder(dev, meta, cache=cache)
        self.assertEqual(a, b)
        self.assertEqual(len(dev.created), 1)
        self.assertEqual(dev.created[0], ("Relient K", DEFAULT_MUSIC_FOLDER_ID))

    def test_ensure_reuses_existing(self) -> None:
        dev = _FakeDevice(
            [
                FolderEntry(100, "Music", 0),
                FolderEntry(445, "Relient K", 100),
            ]
        )
        meta = TrackMetadata(artist="Relient K")
        fid = ensure_artist_folder(dev, meta)
        self.assertEqual(fid, 445)
        self.assertEqual(dev.created, [])


if __name__ == "__main__":
    unittest.main()
