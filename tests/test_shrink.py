"""Unit tests for Shrink playlist id rewrite."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from mtpmanager.app.shrink import (
    PlaylistRewriteWorkspace,
    rewrite_playlists_item_id,
    snapshot_playlists_for_rewrite,
)
from mtpmanager.domain.models import DevicePlaylist


@dataclass
class FakeDevice:
    playlists: list[DevicePlaylist] = field(default_factory=list)
    updates: list[tuple[int, str, list[int], int | None]] = field(
        default_factory=list
    )
    # When True, re-list as if the device already dropped deleted track ids.
    prune_missing: set[int] = field(default_factory=set)

    def list_playlists(self) -> list[DevicePlaylist]:
        out: list[DevicePlaylist] = []
        for pl in self.playlists:
            ids = tuple(
                x for x in pl.track_ids if int(x) not in self.prune_missing
            )
            out.append(
                DevicePlaylist(
                    playlist_id=pl.playlist_id,
                    name=pl.name,
                    parent_id=pl.parent_id,
                    track_ids=ids,
                )
            )
        return out

    def update_playlist(
        self,
        playlist_id: int,
        name: str,
        track_ids,
        *,
        parent_id=None,
        storage_id=None,
    ) -> int:
        ids = [int(x) for x in track_ids]
        self.updates.append((int(playlist_id), name, ids, parent_id))
        for i, pl in enumerate(self.playlists):
            if int(pl.playlist_id) == int(playlist_id):
                self.playlists[i] = DevicePlaylist(
                    playlist_id=pl.playlist_id,
                    name=name,
                    parent_id=pl.parent_id,
                    track_ids=tuple(ids),
                )
                break
        return int(playlist_id)


class ShrinkPlaylistRewriteTests(unittest.TestCase):
    def test_rewrite_uses_workspace_not_live_list(self) -> None:
        """After delete, live list loses old_id; workspace still remaps."""
        device = FakeDevice(
            playlists=[
                DevicePlaylist(
                    playlist_id=10,
                    name="Day.zpl",
                    parent_id=122,
                    track_ids=(100, 200, 300),
                ),
            ]
        )
        # Pre-delete snapshot (old_id still present).
        workspace = [
            PlaylistRewriteWorkspace(
                playlist_id=10,
                name="Day.zpl",
                parent_id=122,
                track_ids=[100, 200, 300],
            )
        ]
        # Device would already have pruned 200 if we re-listed.
        device.prune_missing.add(200)

        n = rewrite_playlists_item_id(
            device, old_id=200, new_id=999, workspace=workspace
        )
        self.assertEqual(n, 1)
        self.assertEqual(len(device.updates), 1)
        pid, name, ids, parent = device.updates[0]
        self.assertEqual(pid, 10)
        self.assertEqual(name, "Day.zpl")
        self.assertEqual(ids, [100, 999, 300])
        self.assertEqual(parent, 122)
        # Workspace mutated for subsequent remaps in the same batch.
        self.assertEqual(workspace[0].track_ids, [100, 999, 300])

    def test_rewrite_skips_unrelated_playlists(self) -> None:
        device = FakeDevice()
        workspace = [
            PlaylistRewriteWorkspace(
                playlist_id=1,
                name="A",
                parent_id=0,
                track_ids=[1, 2],
            )
        ]
        n = rewrite_playlists_item_id(
            device, old_id=99, new_id=100, workspace=workspace
        )
        self.assertEqual(n, 0)
        self.assertEqual(device.updates, [])

    def test_rewrite_dedupes_when_new_id_already_present(self) -> None:
        device = FakeDevice()
        workspace = [
            PlaylistRewriteWorkspace(
                playlist_id=1,
                name="A",
                parent_id=0,
                track_ids=[1, 2, 3],
            )
        ]
        n = rewrite_playlists_item_id(
            device, old_id=2, new_id=3, workspace=workspace
        )
        self.assertEqual(n, 1)
        self.assertEqual(device.updates[0][2], [1, 3])

    def test_snapshot_from_device_list(self) -> None:
        device = FakeDevice(
            playlists=[
                DevicePlaylist(
                    playlist_id=7,
                    name="Pods",
                    parent_id=122,
                    track_ids=(5, 6),
                ),
            ]
        )
        # load_device_playlists_for_lookup falls back to list_playlists.
        ws = snapshot_playlists_for_rewrite(device, serial="")
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0].playlist_id, 7)
        self.assertEqual(ws[0].track_ids, [5, 6])


if __name__ == "__main__":
    unittest.main()
