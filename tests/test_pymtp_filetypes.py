"""Lock pymtp filetype values to libmtp 1.1.x (FOLDER=0, MP3=2, ...)."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
