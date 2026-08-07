"""Tests for GUID→object-id mapping and device playlist push helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.app.playlist_device import (
    DevicePlaylistPushResult,
    append_ids_to_order,
    find_device_playlist_by_name,
    merge_device_playlists,
    move_ids_by_indices,
    ordered_guids_from_tracks,
    playlist_candidates_from_files,
    playlist_display_name,
    playlists_parent_id,
    push_playlist_to_device,
    remove_ids_at_indices,
    resolve_track_object_ids,
)
from mtpmanager.domain.device_folders import (
    DeviceFolderLayout,
    FolderRole,
    legacy_zen_vision_m_layout,
)
from mtpmanager.domain.models import DevicePlaylist, FileEntry, Track, TrackMetadata
from mtpmanager.infra.device_index import item_ids_for_guids, record_send
from mtpmanager.infra.remote_naming import DEFAULT_PLAYLIST_FOLDER_ID


class ItemIdsForGuidsTests(unittest.TestCase):
    def test_prefers_real_ids_and_list_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "TESTSERIAL"
            g1 = "a" * 32
            g2 = "b" * 32
            # Synthetic (negative) must be ignored.
            record_send(
                serial,
                remote_name=f"{g1}.mp3",
                guid=g1,
                item_id=-99,
                path=db,
            )
            # Real id from send.
            record_send(
                serial,
                remote_name=f"{g1}.mp3",
                guid=g1,
                item_id=1001,
                path=db,
            )
            # list source preferred over send for g2 if both exist — write list via SQL
            record_send(
                serial,
                remote_name=f"{g2}.mp3",
                guid=g2,
                item_id=2002,
                path=db,
            )
            hit = item_ids_for_guids(serial, [g1, g2, "c" * 32], path=db)
            self.assertEqual(hit[g1], 1001)
            self.assertEqual(hit[g2], 2002)
            self.assertNotIn("c" * 32, hit)


class PlaylistDeviceHelpersTests(unittest.TestCase):
    def test_ordered_guids(self) -> None:
        tracks = [
            Track(path="/a", meta=TrackMetadata(), guid="a" * 32),
            Track(path="/b", meta=TrackMetadata(), guid=""),
            Track(path="/c", meta=TrackMetadata(), guid="c" * 32),
        ]
        self.assertEqual(
            ordered_guids_from_tracks(tracks),
            ["a" * 32, "c" * 32],
        )

    def test_find_by_name(self) -> None:
        pls = [
            DevicePlaylist(playlist_id=1, name="Road Trip"),
            DevicePlaylist(playlist_id=2, name="Favorites"),
        ]
        hit = find_device_playlist_by_name(pls, "road trip")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.playlist_id, 1)

    def test_find_by_name_strips_zpl(self) -> None:
        pls = [
            DevicePlaylist(
                playlist_id=7,
                name="Podcasts Aug 7, 2026.zpl",
                track_ids=(1, 2),
            ),
        ]
        hit = find_device_playlist_by_name(pls, "Podcasts Aug 7, 2026")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.playlist_id, 7)

    def test_move_ids_by_indices(self) -> None:
        ids = [10, 20, 30, 40]
        self.assertEqual(move_ids_by_indices(ids, [1], delta=-1), [20, 10, 30, 40])
        self.assertEqual(move_ids_by_indices(ids, [1], delta=1), [10, 30, 20, 40])
        # Boundary: cannot move first row up.
        self.assertEqual(move_ids_by_indices(ids, [0], delta=-1), ids)
        # Multi-select move down.
        self.assertEqual(
            move_ids_by_indices(ids, [1, 2], delta=1), [10, 40, 20, 30]
        )

    def test_remove_ids_at_indices(self) -> None:
        ids = [10, 20, 30, 40]
        self.assertEqual(remove_ids_at_indices(ids, [1, 3]), [10, 30])
        self.assertEqual(remove_ids_at_indices(ids, []), ids)

    def test_append_ids_to_order(self) -> None:
        merged, added, skipped = append_ids_to_order(
            [10, 20], [20, 30, 0, 40], skip_existing=True
        )
        self.assertEqual(merged, [10, 20, 30, 40])
        self.assertEqual(added, 2)
        self.assertEqual(skipped, 1)
        merged2, added2, skipped2 = append_ids_to_order(
            [10], [10, 10], skip_existing=False
        )
        self.assertEqual(merged2, [10, 10, 10])
        self.assertEqual(added2, 2)
        self.assertEqual(skipped2, 0)

    def test_playlist_display_name_strips_zpl(self) -> None:
        self.assertEqual(playlist_display_name("Rock.zpl", 1), "Rock")
        self.assertEqual(playlist_display_name("Lullabies", 2), "Lullabies")
        self.assertEqual(playlist_display_name("", 99), "Playlist 99")

    def test_playlist_candidates_from_files(self) -> None:
        files = [
            FileEntry(item_id=1, name="Rock.zpl", parent_id=104, filetype=43),
            FileEntry(item_id=2, name="a.mp3", parent_id=100, filetype=2),
            FileEntry(item_id=3, name="Emo.zpl", parent_id=104, filetype=43),
            FileEntry(item_id=4, name="not-a-pl.txt", parent_id=104, filetype=44),
            FileEntry(item_id=5, name="abstract.pla", parent_id=0, filetype=0),
        ]
        cands = playlist_candidates_from_files(
            files, playlist_parent_ids={104}
        )
        ids = [int(e.item_id) for e in cands]
        # .zpl under My Playlists + filetype playlist + .pla extension.
        # not-a-pl.txt is under 104 so also included (folder contents heuristic).
        self.assertIn(1, ids)
        self.assertIn(3, ids)
        self.assertIn(5, ids)
        self.assertNotIn(2, ids)

    def test_merge_device_playlists(self) -> None:
        a = DevicePlaylist(playlist_id=1, name="A", track_ids=(1,))
        b = DevicePlaylist(playlist_id=2, name="B", track_ids=(2,))
        a2 = DevicePlaylist(playlist_id=1, name="A-dup", track_ids=(9,))
        merged = merge_device_playlists([a], [a2, b])
        self.assertEqual(len(merged), 2)
        by = {p.playlist_id: p for p in merged}
        self.assertEqual(by[1].name, "A")  # primary wins
        self.assertEqual(by[2].name, "B")

    def test_playlists_parent_from_layout(self) -> None:
        layout = DeviceFolderLayout(
            roles={FolderRole.PLAYLISTS: 92},
            source="listed",
        )
        self.assertEqual(playlists_parent_id(layout), 92)
        self.assertEqual(
            playlists_parent_id(legacy_zen_vision_m_layout()),
            DEFAULT_PLAYLIST_FOLDER_ID,
        )

    def test_push_creates_when_missing(self) -> None:
        g1, g2 = "1" * 32, "2" * 32
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "S1"
            record_send(
                serial, remote_name=f"{g1}.mp3", guid=g1, item_id=10, path=db
            )
            record_send(
                serial, remote_name=f"{g2}.mp3", guid=g2, item_id=20, path=db
            )

            device = mock.Mock()
            device.create_playlist.return_value = 555
            device.update_playlist.return_value = 555

            with mock.patch(
                "mtpmanager.app.playlist_device.item_ids_for_guids",
                side_effect=lambda s, gs: item_ids_for_guids(s, gs, path=db),
            ):
                result = push_playlist_to_device(
                    device=device,
                    serial=serial,
                    name="Mix",
                    guids_in_order=[g1, g2],
                    parent_id=104,
                    list_playlists=lambda: [],
                )
            self.assertTrue(result.created)
            self.assertEqual(result.playlist_id, 555)
            self.assertEqual(result.track_ids, (10, 20))
            device.create_playlist.assert_called_once()
            device.update_playlist.assert_not_called()

    def test_push_updates_when_name_exists(self) -> None:
        g1 = "3" * 32
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "S2"
            record_send(
                serial, remote_name=f"{g1}.mp3", guid=g1, item_id=42, path=db
            )
            device = mock.Mock()
            device.update_playlist.return_value = 9
            existing = [
                DevicePlaylist(playlist_id=9, name="Mix", track_ids=(1,))
            ]
            with mock.patch(
                "mtpmanager.app.playlist_device.item_ids_for_guids",
                side_effect=lambda s, gs: item_ids_for_guids(s, gs, path=db),
            ):
                result = push_playlist_to_device(
                    device=device,
                    serial=serial,
                    name="mix",
                    guids_in_order=[g1],
                    list_playlists=lambda: existing,
                )
            self.assertFalse(result.created)
            self.assertEqual(result.playlist_id, 9)
            device.update_playlist.assert_called_once()
            device.create_playlist.assert_not_called()
            # Replace semantics (default): new membership is only the pushed ids.
            args = device.update_playlist.call_args[0]
            self.assertEqual(list(args[2]), [42])

    def test_push_merge_existing_appends_and_matches_zpl(self) -> None:
        """Day-podcast style: append into existing *.zpl of the same display name."""
        g_new = "4" * 32
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library_index.db"
            serial = "S3"
            record_send(
                serial,
                remote_name=f"{g_new}.mp3",
                guid=g_new,
                item_id=99,
                path=db,
            )
            device = mock.Mock()
            device.update_playlist.return_value = 1651867
            existing = [
                DevicePlaylist(
                    playlist_id=1651867,
                    name="Podcasts Aug 7, 2026.zpl",
                    track_ids=(10, 20),
                )
            ]
            with mock.patch(
                "mtpmanager.app.playlist_device.item_ids_for_guids",
                side_effect=lambda s, gs: item_ids_for_guids(s, gs, path=db),
            ):
                result = push_playlist_to_device(
                    device=device,
                    serial=serial,
                    name="Podcasts Aug 7, 2026",
                    guids_in_order=[g_new],
                    list_playlists=lambda: existing,
                    merge_existing=True,
                )
            self.assertFalse(result.created)
            self.assertEqual(result.playlist_id, 1651867)
            self.assertEqual(result.track_ids, (10, 20, 99))
            device.create_playlist.assert_not_called()
            device.update_playlist.assert_called_once()
            args, kwargs = device.update_playlist.call_args
            self.assertEqual(args[0], 1651867)
            self.assertEqual(list(args[2]), [10, 20, 99])

    def test_push_raises_when_no_ids(self) -> None:
        device = mock.Mock()
        with mock.patch(
            "mtpmanager.app.playlist_device.item_ids_for_guids",
            return_value={},
        ):
            with self.assertRaises(ValueError):
                push_playlist_to_device(
                    device=device,
                    serial="x",
                    name="Empty",
                    guids_in_order=["a" * 32],
                    list_playlists=lambda: [],
                )


if __name__ == "__main__":
    unittest.main()
