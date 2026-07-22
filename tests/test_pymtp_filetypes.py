"""Lock pymtp filetype values to libmtp 1.1.x (FOLDER=0, MP3=2, ...)."""

from __future__ import annotations

import inspect
import unittest

import mtpmanager.infra.pymtp_wrapper as pymtp


class PymtpFiletypeTests(unittest.TestCase):
    def test_folder_is_zero(self) -> None:
        self.assertEqual(int(pymtp.LIBMTP_Filetype["FOLDER"]), 0)

    def test_mp3_is_two_not_one(self) -> None:
        # Stock pymtp wrongly used MP3=1 (WAV in modern libmtp).
        self.assertEqual(int(pymtp.LIBMTP_Filetype["MP3"]), 2)
        self.assertEqual(int(pymtp.LIBMTP_Filetype["WAV"]), 1)
        self.assertEqual(int(pymtp.LIBMTP_Filetype["WMA"]), 3)

    def test_unknown_is_last(self) -> None:
        self.assertEqual(int(pymtp.LIBMTP_Filetype["UNKNOWN"]), 44)

    def test_find_filetype_mp3(self) -> None:
        mtp = pymtp.MTP()
        self.assertEqual(int(mtp.find_filetype("/tmp/track.mp3")), 2)
        self.assertEqual(int(mtp.find_filetype("song.MP3")), 2)

    def test_find_filetype_wma(self) -> None:
        mtp = pymtp.MTP()
        self.assertEqual(int(mtp.find_filetype("clip.wma")), 3)

    def test_find_filetype_unknown(self) -> None:
        mtp = pymtp.MTP()
        self.assertEqual(int(mtp.find_filetype("file.xyz")), 44)


class PymtpFolderListPy3Tests(unittest.TestCase):
    """Stock pymtp.get_folder_list uses dict.has_key (Python 2 only)."""

    def test_get_folder_list_patched_no_has_key(self) -> None:
        src = inspect.getsource(pymtp.MTP.get_folder_list)
        # Stock used ret.has_key(...); our patch uses ``in``.
        self.assertNotIn(".has_key(", src)
        self.assertIs(pymtp.MTP.get_folder_list, pymtp._get_folder_list)

    def test_get_parent_folders_patched_no_has_key(self) -> None:
        src = inspect.getsource(pymtp.MTP.get_parent_folders)
        self.assertNotIn(".has_key(", src)
        self.assertIs(pymtp.MTP.get_parent_folders, pymtp._get_parent_folders)

    def test_get_folder_list_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_folder_list()


class PymtpCreateFolderStringTests(unittest.TestCase):
    """Stock create_folder passed Python str without c_char_p → first char only."""

    def test_create_folder_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.create_folder, pymtp._create_folder)
        src = inspect.getsource(pymtp.MTP.create_folder)
        self.assertIn("create_string_buffer", src)
        self.assertIn("_as_c_char_p", src)
        self.assertIn("utf-8", inspect.getsource(pymtp._as_c_char_p))

    def test_as_c_char_p_encodes_full_string(self) -> None:
        # Regression for “Blargh” → “B”: full UTF-8 bytes must be preserved.
        self.assertEqual(pymtp._as_c_char_p("Blargh"), b"Blargh")
        self.assertEqual(pymtp._as_c_char_p(b"Blargh"), b"Blargh")
        self.assertEqual(pymtp._as_c_char_p("café"), "café".encode("utf-8"))

    def test_create_folder_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.create_folder("Blargh", parent=100)

    def test_create_folder_ctypes_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Create_Folder
        self.assertEqual(len(fn.argtypes), 4)
        self.assertIs(fn.argtypes[1], ctypes.c_char_p)
        self.assertEqual(fn.restype, ctypes.c_uint32)


class PymtpFileListingTests(unittest.TestCase):
    """Experimental List Files uses patched get_filelisting."""

    def test_get_filelisting_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.get_filelisting, pymtp._get_filelisting)
        src = inspect.getsource(pymtp.MTP.get_filelisting)
        self.assertIn("_ptr_truthy", src)

    def test_get_filelisting_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_filelisting()

    def test_filelisting_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Get_Filelisting_With_Callback
        self.assertEqual(len(fn.argtypes), 3)
        self.assertIs(fn.argtypes[1], ctypes.c_void_p)


class PymtpDeleteObjectTests(unittest.TestCase):
    """Experimental Delete Track uses patched delete_object."""

    def test_delete_object_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.delete_object, pymtp._delete_object)
        src = inspect.getsource(pymtp.MTP.delete_object)
        self.assertIn("_device_ptr", src)
        self.assertIn("LIBMTP_Delete_Object", src)

    def test_delete_object_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.delete_object(1234)

    def test_delete_object_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Delete_Object
        self.assertEqual(len(fn.argtypes), 2)
        self.assertIs(fn.argtypes[0], ctypes.c_void_p)
        self.assertIs(fn.argtypes[1], ctypes.c_uint32)
        self.assertEqual(fn.restype, ctypes.c_int)


class PymtpTrackListingTests(unittest.TestCase):
    """Patched get_tracklisting (diagnostic); product List Tracks uses file+meta."""

    def test_get_tracklisting_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.get_tracklisting, pymtp._get_tracklisting)
        src = inspect.getsource(pymtp.MTP.get_tracklisting)
        self.assertIn("_device_ptr", src)
        self.assertIn("LIBMTP_Get_Tracklisting_With_Callback", src)
        self.assertIn("_snapshot_track", src)
        self.assertIn("LIBMTP_destroy_track_t", src)
        self.assertIn("_ptr_truthy", src)
        self.assertIn("_ProgressFunc", src)
        self.assertIn("callback", src)
        # Linked-list next must be captured before destroy.
        self.assertIn("_next_node_ptr", src)

    def test_next_node_ptr_null_safe(self) -> None:
        from types import SimpleNamespace

        from mtpmanager.infra import pymtp_wrapper as wrap

        self.assertIsNone(wrap._next_node_ptr(SimpleNamespace(next=None)))
        self.assertIsNone(wrap._next_node_ptr(SimpleNamespace()))

    def test_get_tracklisting_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_tracklisting()

    def test_tracklisting_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Get_Tracklisting_With_Callback
        self.assertEqual(len(fn.argtypes), 3)
        self.assertIs(fn.argtypes[0], ctypes.c_void_p)
        self.assertIs(fn.argtypes[1], ctypes.c_void_p)
        self.assertIs(fn.argtypes[2], ctypes.c_void_p)


class PymtpGetFileMetadataTests(unittest.TestCase):
    """Experimental Get File Info uses patched get_file_metadata."""

    def test_get_file_metadata_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.get_file_metadata, pymtp._get_file_metadata)
        src = inspect.getsource(pymtp.MTP.get_file_metadata)
        self.assertIn("_device_ptr", src)
        self.assertIn("LIBMTP_Get_Filemetadata", src)
        self.assertIn("_ptr_truthy", src)

    def test_get_file_metadata_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_file_metadata(1234)

    def test_get_file_metadata_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Get_Filemetadata
        self.assertEqual(len(fn.argtypes), 2)
        self.assertIs(fn.argtypes[0], ctypes.c_void_p)
        self.assertIs(fn.argtypes[1], ctypes.c_uint32)

    def test_get_file_metadata_null_dumps_stack(self) -> None:
        """NULL from libmtp should dump errorstack then raise ObjectNotFound."""
        src = inspect.getsource(pymtp.MTP.get_file_metadata)
        self.assertIn("_debug_stack", src)
        self.assertIn("ObjectNotFound", src)
        self.assertIn("proplist", src.lower())


class PymtpGetTrackMetadataTests(unittest.TestCase):
    """Experimental Get Track Info uses patched get_track_metadata."""

    def test_get_track_metadata_is_patched(self) -> None:
        self.assertIs(pymtp.MTP.get_track_metadata, pymtp._get_track_metadata)
        src = inspect.getsource(pymtp.MTP.get_track_metadata)
        self.assertIn("_device_ptr", src)
        self.assertIn("LIBMTP_Get_Trackmetadata", src)
        self.assertIn("_snapshot_track", src)
        self.assertIn("LIBMTP_destroy_track_t", src)

    def test_get_track_metadata_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_track_metadata(1234)

    def test_get_track_metadata_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_Get_Trackmetadata
        self.assertEqual(len(fn.argtypes), 2)
        self.assertIs(fn.argtypes[0], ctypes.c_void_p)
        self.assertIs(fn.argtypes[1], ctypes.c_uint32)

    def test_destroy_track_argtypes(self) -> None:
        import ctypes

        fn = pymtp._pymtp._libmtp.LIBMTP_destroy_track_t
        self.assertEqual(len(fn.argtypes), 1)
        self.assertEqual(fn.restype, None)


class FileLineFormatTests(unittest.TestCase):
    def test_file_line(self) -> None:
        from mtpmanager.domain.models import FileEntry
        from mtpmanager.ui.formatting import file_line

        line = file_line(
            FileEntry(
                item_id=445003,
                name="Blargh.mp3",
                parent_id=100,
                filesize=1_500_000,
                filetype=2,
            )
        )
        self.assertIn("445003", line)
        self.assertIn("parent=100", line)
        self.assertIn("Blargh.mp3", line)
        self.assertIn("MB", line)

    def test_track_line(self) -> None:
        from mtpmanager.domain.models import DeviceTrackRef
        from mtpmanager.ui.formatting import track_line

        line = track_line(
            DeviceTrackRef(
                item_id=398401,
                name="song.mp3",
                title="Black To The Future",
                artist="Fury Weekend",
                parent_id=100,
                filetype=2,
            )
        )
        self.assertIn("398401", line)
        self.assertIn("parent=100", line)
        self.assertIn("Fury Weekend", line)
        self.assertIn("Black To The Future", line)
        self.assertIn("song.mp3", line)

    def test_file_metadata_summary(self) -> None:
        from mtpmanager.domain.models import FileEntry
        from mtpmanager.ui.formatting import file_metadata_summary

        text = file_metadata_summary(
            FileEntry(
                item_id=445003,
                name="Blargh.mp3",
                parent_id=100,
                storage_id=0x00010001,
                filesize=1_500_000,
                filetype=2,
                modificationdate=1_700_000_000,
            )
        )
        self.assertIn("445003", text)
        self.assertIn("Blargh.mp3", text)
        self.assertIn("0x00010001", text)
        self.assertIn("Filetype: 2", text)
        self.assertIn("UTC", text)

    def test_track_metadata_summary(self) -> None:
        from mtpmanager.domain.models import DeviceTrackInfo
        from mtpmanager.ui.formatting import track_metadata_summary

        text = track_metadata_summary(
            DeviceTrackInfo(
                item_id=398401,
                name="song.mp3",
                parent_id=100,
                storage_id=0x00010001,
                filesize=3_000_000,
                filetype=2,
                modificationdate=1_700_000_000,
                title="Black To The Future",
                artist="Fury Weekend",
                album="Avengers After Dark",
                genre="Synthwave",
                tracknumber=1,
                duration_ms=215_000,
                sample_rate=44100,
                channels=2,
                bitrate=320_000,
            )
        )
        self.assertIn("398401", text)
        self.assertIn("Black To The Future", text)
        self.assertIn("Fury Weekend", text)
        self.assertIn("Avengers After Dark", text)
        self.assertIn("3:35", text)
        self.assertIn("44100 Hz", text)


# looks_like_track / track_refs_from_files: tests/test_device_media.py


if __name__ == "__main__":
    unittest.main()
