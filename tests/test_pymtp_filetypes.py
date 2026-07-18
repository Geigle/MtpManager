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


if __name__ == "__main__":
    unittest.main()
