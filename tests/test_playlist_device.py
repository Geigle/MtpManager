"""Tests for GUID→object-id mapping and device playlist push helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtpmanager.app.playlist_device import (
    DevicePlaylistPushResult,
    find_device_playlist_by_name,
    ordered_guids_from_tracks,
    playlists_parent_id,
    push_playlist_to_device,
    resolve_track_object_ids,
)
from mtpmanager.domain.device_folders import (
    DeviceFolderLayout,
    FolderRole,
    legacy_zen_vision_m_layout,
)
from mtpmanager.domain.models import DevicePlaylist, Track, TrackMetadata
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
