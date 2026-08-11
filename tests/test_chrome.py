"""Unit tests for main-window chrome (visual pass phases 1–3)."""

from __future__ import annotations

import unittest
from tkinter import Tk

import sys

from mtpmanager.ui.chrome import (
    GLYPH_ADD,
    GLYPH_DISMISS,
    GLYPH_REMOVE,
    LABEL_MOVE_DOWN,
    LABEL_MOVE_UP,
    LABEL_REFRESH,
    STYLE_TREE_COMPACT,
    STYLE_TREE_THUMB,
    apply_chrome_baseline,
    flat_frame,
    make_ttk_scale,
    reveal_in_file_manager_label,
    secondary_label_kwargs,
    snap_scale_value,
    time_of_day_row,
)
from mtpmanager.ui.window import (
    CTX_PODCAST_REVEAL_DOWNLOAD,
    STABLE_MODE_CAPTION,
    STABLE_MODE_HELP,
    MainWindow,
)


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

    def test_playlists_master_detail_like_podcasts(self) -> None:
        """Host + Device Playlists use list Treeview + tracks (not Combobox)."""
        win = MainWindow(self.root)
        self.assertEqual(win.playlist_list_tree.winfo_class(), "Treeview")
        self.assertIs(win.playlist_combo, win.playlist_list_tree)
        self.assertEqual(str(win.playlist_list_tree.cget("style")), STYLE_TREE_COMPACT)
        self.assertEqual(str(win.playlist_tree.cget("style")), STYLE_TREE_COMPACT)
        self.assertEqual(win.device_playlist_list_tree.winfo_class(), "Treeview")
        self.assertIs(win.device_playlist_combo, win.device_playlist_list_tree)
        win.set_playlist_combo_values(
            ["A", "B"], selected="B", ids=[10, 20]
        )
        self.assertEqual(win.var_playlist_choice.get(), "B")
        self.assertIn("pln:20", win.playlist_list_tree.selection())
        win.set_device_playlist_combo_values(
            ["Dev"], selected="Dev", interactive=True, playlist_ids=[99]
        )
        self.assertEqual(win.var_device_playlist_choice.get(), "Dev")
        self.assertIn("dpln:99", win.device_playlist_list_tree.selection())

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

    def test_phase3_button_grammar_ascii_glyphs(self) -> None:
        win = MainWindow(self.root)
        self.assertEqual(win.btn_library_search_clear.cget("text"), GLYPH_DISMISS)
        self.assertEqual(win.btn_playback_close.cget("text"), GLYPH_DISMISS)
        self.assertEqual(win.btn_podcast_add.cget("text"), GLYPH_ADD)
        self.assertEqual(win.btn_podcast_remove.cget("text"), GLYPH_REMOVE)
        self.assertEqual(win.btn_podcast_refresh.cget("text"), LABEL_REFRESH)
        self.assertEqual(win.btn_playlist_move_up.cget("text"), LABEL_MOVE_UP)
        self.assertEqual(win.btn_playlist_move_down.cget("text"), LABEL_MOVE_DOWN)
        self.assertEqual(win.btn_playlist_new.cget("text"), GLYPH_ADD)
        self.assertEqual(win.btn_device_playlist_move_up.cget("text"), LABEL_MOVE_UP)

    def test_phase3_snap_scale_and_ttk_scale(self) -> None:
        self.assertEqual(
            snap_scale_value(2.4, from_=0, to=10, resolution=0.5), 2.5
        )
        self.assertEqual(snap_scale_value(7.2, from_=0, to=12, resolution=1), 7)
        scale, var = make_ttk_scale(
            self.root, from_=0, to=10, value=3, resolution=1
        )
        self.assertEqual(scale.winfo_class(), "TScale")
        self.assertAlmostEqual(float(var.get()), 3.0)

    def test_phase3_time_of_day_comboboxes(self) -> None:
        frame, getter = time_of_day_row(self.root, initial_hhmm="18:30")
        self.assertEqual(getter(), "18:30")
        frame2, getter2 = time_of_day_row(self.root, initial_hhmm="00:05")
        self.assertEqual(getter2(), "00:05")
        frame3, getter3 = time_of_day_row(self.root, initial_hhmm="12:00")
        self.assertEqual(getter3(), "12:00")

    def test_phase4a_stable_mode_short_caption_and_about(self) -> None:
        self.assertLess(len(STABLE_MODE_CAPTION), len(STABLE_MODE_HELP))
        self.assertIn("mtp-sendtr", STABLE_MODE_CAPTION)
        win = MainWindow(self.root)
        win.apply_mode_ui("stable")
        self.root.update_idletasks()
        self.assertEqual(win.lbl_device_caption.cget("text"), STABLE_MODE_CAPTION)
        self.assertEqual(win.btn_stable_mode_about.winfo_manager(), "pack")
        win.apply_mode_ui("experimental")
        self.root.update_idletasks()
        self.assertEqual(win.btn_stable_mode_about.winfo_manager(), "")

    def test_phase4a_reveal_wording_platform(self) -> None:
        label = reveal_in_file_manager_label(download=True)
        self.assertEqual(CTX_PODCAST_REVEAL_DOWNLOAD, label)
        if sys.platform == "darwin":
            self.assertIn("Finder", label)
        else:
            self.assertNotIn("Finder", label)

    def test_phase4a_secondary_label_dark_safe(self) -> None:
        kw = secondary_label_kwargs()
        if sys.platform == "darwin":
            self.assertEqual(kw.get("fg"), "systemSecondaryLabelColor")
        else:
            # No hard-coded light-only gray — inherit theme default.
            self.assertEqual(kw, {})


if __name__ == "__main__":
    unittest.main()
