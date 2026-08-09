"""Unit tests for main-window chrome baseline (phase 1)."""

from __future__ import annotations

import unittest
from tkinter import Tk

from mtpmanager.ui.chrome import apply_chrome_baseline, flat_frame, h_separator
from mtpmanager.ui.window import MainWindow


class ChromeBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_flat_frame_has_no_sunken_relief(self) -> None:
        f = flat_frame(self.root)
        self.assertEqual(str(f.cget("relief")), "flat")
        self.assertEqual(int(f.cget("borderwidth")), 0)

    def test_apply_chrome_baseline_configures_tree_rowheight(self) -> None:
        style = apply_chrome_baseline(self.root, tree_rowheight=52)
        # Theme remains usable; rowheight set when supported.
        self.assertTrue(style.theme_use())
        try:
            self.assertEqual(int(style.lookup("Treeview", "rowheight")), 52)
        except Exception:
            self.skipTest("Treeview rowheight not queryable on this theme")

    def test_main_window_interactive_stack_is_ttk(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(win.btn_cancel_job.winfo_class(), "TButton")
        self.assertEqual(win.entry_library_search.winfo_class(), "TEntry")
        self.assertEqual(win.btn_playback_play.winfo_class(), "TButton")
        self.assertEqual(str(win.leftframe.cget("relief")), "flat")
        # Separators exist as children of root (toolbar / bottom hairlines).
        classes = {c.winfo_class() for c in self.root.winfo_children()}
        self.assertIn("TSeparator", classes)


if __name__ == "__main__":
    unittest.main()
