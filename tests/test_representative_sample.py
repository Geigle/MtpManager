"""Bindings for libmtp representative samples (album art experiment)."""

from __future__ import annotations

import inspect
import unittest

import mtpmanager.infra.pymtp_wrapper as pymtp


class RepresentativeSampleBindingTests(unittest.TestCase):
    def test_filesample_struct_fields(self) -> None:
        fields = {name for name, _ty in pymtp.LIBMTP_FileSampleData._fields_}
        self.assertEqual(
            fields,
            {"width", "height", "duration", "filetype", "size", "data"},
        )
        # data must not be c_char_p (NUL-safe binary).
        import ctypes

        data_type = dict(pymtp.LIBMTP_FileSampleData._fields_)["data"]
        self.assertEqual(data_type, ctypes.POINTER(ctypes.c_char))

    def test_album_struct_fields(self) -> None:
        fields = {name for name, _ty in pymtp.LIBMTP_Album._fields_}
        self.assertIn("album_id", fields)
        self.assertIn("tracks", fields)
        self.assertIn("name", fields)

    def test_methods_patched(self) -> None:
        self.assertIs(
            pymtp.MTP.get_representative_sample_format,
            pymtp._get_representative_sample_format,
        )
        self.assertIs(
            pymtp.MTP.send_representative_sample,
            pymtp._send_representative_sample,
        )
        self.assertIs(
            pymtp.MTP.get_representative_sample,
            pymtp._get_representative_sample,
        )
        self.assertIs(pymtp.MTP.create_new_album, pymtp._create_new_album)

    def test_send_uses_pointer_not_c_char_p(self) -> None:
        src = inspect.getsource(pymtp._send_representative_sample)
        self.assertIn("POINTER(ctypes.c_char)", src)
        self.assertIn("from_buffer_copy", src)
        self.assertNotIn("c_char_p(data", src)

    def test_requires_connection(self) -> None:
        mtp = pymtp.MTP()
        with self.assertRaises(pymtp.NotConnected):
            mtp.get_representative_sample_format(int(pymtp.LIBMTP_Filetype["MP3"]))
        with self.assertRaises(pymtp.NotConnected):
            mtp.send_representative_sample(
                1, b"\xff\xd8\xff", width=1, height=1
            )
        with self.assertRaises(pymtp.NotConnected):
            mtp.create_new_album("Test", track_ids=[1])

    def test_sample_format_ctypes_argtypes(self) -> None:
        import ctypes

        lib = pymtp._pymtp._libmtp
        self.assertTrue(hasattr(lib, "LIBMTP_Get_Representative_Sample_Format"))
        self.assertTrue(hasattr(lib, "LIBMTP_Send_Representative_Sample"))
        at = lib.LIBMTP_Send_Representative_Sample.argtypes
        self.assertIsNotNone(at)
        self.assertEqual(len(at), 3)


if __name__ == "__main__":
    unittest.main()
