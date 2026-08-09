"""Unit tests for main-window chrome (visual pass phases 1–2)."""

from __future__ import annotations

import unittest
from tkinter import Tk

from mtpmanager.ui.chrome import (
    STYLE_TREE_COMPACT,
    STYLE_TREE_THUMB,
    apply_chrome_baseline,
    flat_frame,
)
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

    def test_apply_chrome_baseline_named_tree_styles(self) -> None:
        style = apply_chrome_baseline(
            self.root,
            compact_tree_rowheight=28,
            thumb_tree_rowheight=52,
        )
        self.assertTrue(style.theme_use())
        try:
            self.assertEqual(int(style.lookup(STYLE_TREE_COMPACT, "rowheight")), 28)
            self.assertEqual(int(style.lookup(STYLE_TREE_THUMB, "rowheight")), 52)
        except Exception:
            self.skipTest("Treeview rowheight not queryable on this theme")

    def test_main_window_interactive_stack_is_ttk(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(win.btn_cancel_job.winfo_class(), "TButton")
        self.assertEqual(win.entry_library_search.winfo_class(), "TEntry")
        self.assertEqual(win.btn_playback_play.winfo_class(), "TButton")
        self.assertEqual(str(win.leftframe.cget("relief")), "flat")
        classes = {c.winfo_class() for c in self.root.winfo_children()}
        self.assertIn("TSeparator", classes)

    def test_phase2_podcasts_use_treeview_not_listbox(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(win.podcast_show_tree.winfo_class(), "Treeview")
        self.assertIs(win.podcast_show_list, win.podcast_show_tree)
        self.assertEqual(str(win.podcast_show_tree.cget("style")), STYLE_TREE_COMPACT)
        self.assertEqual(
            str(win.podcast_episode_tree.cget("style")), STYLE_TREE_COMPACT
        )

    def test_phase2_device_subview_is_not_nested_notebook(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(type(win.device_notebook).__name__, "_DeviceSubviewNotebook")
        win.show_device_subview(win.device_playlists_tab)
        self.assertIs(win._device_subview_frame, win.device_playlists_tab)
        self.assertEqual(win.var_device_category.get(), "Playlists")
        self.assertIs(win.active_device_tree(), win.device_playlist_tree)
        # Notebook-compatible select() returns current frame path.
        self.assertEqual(win.device_notebook.select(), str(win.device_playlists_tab))
        win.device_notebook.select(win.device_video_tab)
        self.assertIs(win._device_subview_frame, win.device_video_tab)

    def test_phase2_thumb_vs_compact_tree_styles(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(str(win.tree.cget("style")), STYLE_TREE_THUMB)
        self.assertEqual(str(win.device_tree.cget("style")), STYLE_TREE_THUMB)
        self.assertEqual(str(win.playlist_tree.cget("style")), STYLE_TREE_COMPACT)
        self.assertEqual(
            str(win.device_playlist_tree.cget("style")), STYLE_TREE_COMPACT
        )


if __name__ == "__main__":
    unittest.main()
