"""Tk layout only — widgets and packing."""

from __future__ import annotations

import math
from typing import Literal

from pathlib import Path

from tkinter import (
    BOTH,
    BOTTOM,
    BooleanVar,
    Button,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    TOP,
    X,
    Y,
    Frame,
    Label,
    Menu,
    PhotoImage,
    Scrollbar,
    Tk,
    ttk,
)

Mode = Literal["stable", "experimental"]

# Shown in the left panel when Config → Stable Mode is checked.
STABLE_MODE_HELP = (
    "Stable Mode is on.\n\n"
    "Transfers use mtp-sendtr (one subprocess per track) "
    "instead of in-process PyMTP.\n\n"
    "• No Device → Connect required\n"
    "• Device menu tools and auto-connect are off\n"
    "• PyMTP session is closed so mtp-sendtr can claim the player\n\n"
    "Uncheck Config → Stable Mode to return to PyMTP "
    "(device graphic, Connect, and in-process send)."
)

EXPERIMENTAL_HINT = (
    "PyMTP is the default: auto-connect when a player is present, "
    "Device menu tools, and in-process send.\n\n"
    "Right-click a track to sync. Output format: Config → Config…\n\n"
    "If send fails, try Config → Stable Mode (mtp-sendtr)."
)

# Fixed left column (context + device). Text wrap stays slightly inside width.
_LEFT_PANEL_WIDTH = 220
_LEFT_TEXT_WRAP = 200
# Device subframe: fixed height for title + caption + graphic / Stable help.
_DEVICE_PANEL_HEIGHT = 240
# Device profile art is scaled into this fixed slot (height-priority).
_DEVICE_GRAPHIC_HEIGHT = 140
_DEVICE_GRAPHIC_MAX_WIDTH = 180

_PATH_DISPLAY_MAX = 72
_DEAD_TRACK_FG = "gray50"

# Desaturated transfer-state tags (Treeview tag_configure).
BG_TRANSFER_QUEUED = "#b8cbb8"  # desaturated green — in batch, waiting
BG_TRANSFER_TRANSCODING = "#8faf8f"  # desaturated green — converting
BG_TRANSFER_TRANSFERRING = "#bf8f8f"  # desaturated red — sending to device

# Tree column ids (values order).
TREE_COLS = ("title", "artist", "album", "year")

# Library menu labels (used for entryconfig by label).
MENU_SELECT_ROOT = "Select Library Root…"
MENU_UPDATE_LIBRARY = "Update Library"

# Transfer menu
MENU_SYNC_ENTIRE = "Sync Entire Library"
MENU_SYNC_FOLDER = "Sync Folder…"
MENU_SYNC_SELECTED = "Sync Selected Tracks"
MENU_RESUME_SYNC = "Resume Sync"
MENU_CANCEL_JOB = "Cancel Current Job"
MENU_PACKAGE_RETAIL = "Package Retail Demos… (experimental)"
MENU_RESTORE_RETAIL = "Restore Retail Package… (experimental)"

# Config menu
MENU_STABLE_MODE = "Stable Mode"
MENU_ARTIST_FOLDERS = "Store tracks in artist folder (experimental)"
MENU_ALBUM_FOLDERS = "Store tracks in album folder (experimental)"
MENU_CONFIG = "Config…"

# Device menu (PyMTP / default)
MENU_CONNECT = "Connect"
MENU_DISCONNECT = "Disconnect"
MENU_DEVICE_INFO = "Device Info"
MENU_CREATE_FOLDER = "Create Folder…"
MENU_SEND_VIDEO = "Send Video… (experimental)"
MENU_LIST_FOLDERS = "List Folders"
MENU_LIST_FILES = "List Files (experimental)"
MENU_LIST_TRACKS = "List Tracks (experimental)"
MENU_GET_TRACKS_FROM_DEVICE = "Get Tracks from Device… (experimental)"
MENU_DELETE_TRACK = "Delete Track (experimental)"
MENU_GET_FILE_INFO = "Get File Info (experimental)"
MENU_GET_TRACK_INFO = "Get Track Info (experimental)"
MENU_DELETE_ALL = "Delete All Tracks…"
MENU_REFRESH_DEVICE_INDEX = "Refresh Device Index…"

# Track context menu
CTX_SYNC_SELECTED = "Sync selected tracks"
CTX_SYNC_TRACK = "Sync this track"
CTX_SYNC_ALBUM = "Sync Album"
CTX_SYNC_ARTIST = "Sync all from Artist"

# Group header context menus (labels updated dynamically before popup)
CTX_SYNC_ARTIST_GROUP = "Sync all from Artist"
CTX_SYNC_ALBUM_GROUP = "Sync album"

_DEVICE_MENU_LABELS = (
    MENU_CONNECT,
    MENU_DISCONNECT,
    MENU_DEVICE_INFO,
    MENU_CREATE_FOLDER,
    MENU_SEND_VIDEO,
    MENU_LIST_FOLDERS,
    MENU_LIST_FILES,
    MENU_LIST_TRACKS,
    MENU_GET_TRACKS_FROM_DEVICE,
    MENU_REFRESH_DEVICE_INDEX,
    MENU_DELETE_TRACK,
    MENU_GET_FILE_INFO,
    MENU_GET_TRACK_INFO,
    MENU_DELETE_ALL,
)


def _elide_path(path: str, max_len: int = _PATH_DISPLAY_MAX) -> str:
    """Shorten a path for the toolbar; keep the end (basename) visible."""
    if not path or len(path) <= max_len:
        return path
    keep = max_len - 1  # room for ellipsis
    head = keep // 3
    tail = keep - head
    return path[:head] + "…" + path[-tail:]


class MainWindow:
    def __init__(self, root: Tk | None = None):
        self.root = root or Tk()
        self.root.title("MTP Manager")
        self.root.geometry("1000x600")
        self.root["borderwidth"] = 3
        self.root["relief"] = "sunken"

        # Menubar: Library | Transfer | Device | Config
        self.menubar = Menu(self.root)
        self.root.config(menu=self.menubar)

        self.menu_library = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Library", menu=self.menu_library)
        self.menu_library.add_command(label=MENU_SELECT_ROOT)
        self.menu_library.add_command(label=MENU_UPDATE_LIBRARY, state=DISABLED)

        self.menu_transfer = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Transfer", menu=self.menu_transfer)
        self.menu_transfer.add_command(label=MENU_SYNC_ENTIRE)
        self.menu_transfer.add_command(label=MENU_SYNC_FOLDER)
        self.menu_transfer.add_command(label=MENU_SYNC_SELECTED, state=DISABLED)
        self.menu_transfer.add_command(label=MENU_RESUME_SYNC, state=DISABLED)
        self.menu_transfer.add_separator()
        self.menu_transfer.add_command(label=MENU_PACKAGE_RETAIL)
        self.menu_transfer.add_command(label=MENU_RESTORE_RETAIL)
        self.menu_transfer.add_separator()
        self.menu_transfer.add_command(label=MENU_CANCEL_JOB, state=DISABLED)

        self.menu_device = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Device", menu=self.menu_device)
        for label in _DEVICE_MENU_LABELS:
            self.menu_device.add_command(label=label, state=DISABLED)

        self.var_stable_mode = BooleanVar(value=False)
        self.var_artist_folders = BooleanVar(value=False)
        self.var_album_folders = BooleanVar(value=False)
        self.menu_config = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Config", menu=self.menu_config)
        self.menu_config.add_checkbutton(
            label=MENU_STABLE_MODE,
            variable=self.var_stable_mode,
            onvalue=True,
            offvalue=False,
        )
        self.menu_config.add_checkbutton(
            label=MENU_ARTIST_FOLDERS,
            variable=self.var_artist_folders,
            onvalue=True,
            offvalue=False,
        )
        self.menu_config.add_checkbutton(
            label=MENU_ALBUM_FOLDERS,
            variable=self.var_album_folders,
            onvalue=True,
            offvalue=False,
            state=DISABLED,
        )
        self.menu_config.add_separator()
        self.menu_config.add_command(label=MENU_CONFIG)

        # Track / group context menus (commands wired by controller).
        self.menu_track_ctx = Menu(self.root, tearoff=0)
        self.menu_track_ctx.add_command(label=CTX_SYNC_SELECTED, state=DISABLED)
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_SYNC_TRACK)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ALBUM)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ARTIST)

        self.menu_artist_ctx = Menu(self.root, tearoff=0)
        self.menu_artist_ctx.add_command(label=CTX_SYNC_ARTIST_GROUP)

        self.menu_album_ctx = Menu(self.root, tearoff=0)
        self.menu_album_ctx.add_command(label=CTX_SYNC_ALBUM_GROUP)

        # Status toolbar: path + track count only (no duplicate title header).
        library_toolbar = Frame(self.root, borderwidth=3, relief="sunken")
        library_toolbar.pack(side=TOP, fill=X, padx=2, pady=2)

        Label(library_toolbar, text="Library:").pack(side=LEFT, padx=(6, 2), pady=4)

        self.lbl_library_path = Label(
            library_toolbar,
            text="No library selected",
            anchor="w",
        )
        self.lbl_library_path.pack(side=LEFT, fill=X, expand=True, padx=2, pady=4)

        self.lbl_library_count = Label(library_toolbar, text="0 tracks")
        self.lbl_library_count.pack(side=LEFT, padx=(6, 8), pady=4)

        # Pack bottom bar *before* the expanding body so it always keeps a
        # visible strip (Tk expand can otherwise starve a late BOTTOM pack).
        bottomframe = Frame(self.root)
        bottomframe["borderwidth"] = 3
        bottomframe["relief"] = "sunken"
        bottomframe.pack(side=BOTTOM, fill=X)

        # Status line above progress (current track during sync / device jobs).
        self.lbl_progress_status = Label(
            bottomframe,
            text="",
            anchor="w",
            justify=LEFT,
        )
        self.lbl_progress_status.pack(side=TOP, fill=X, padx=8, pady=(4, 0))

        # Progress + Cancel (always mapped; Cancel enabled only while a job runs).
        self.progress_row = Frame(bottomframe)
        self.progress_row.pack(side=TOP, fill=X, padx=4, pady=(2, 4))
        self.btn_cancel_job = Button(
            self.progress_row,
            text="Cancel",
            width=12,
            state=DISABLED,
        )
        # Pack Cancel first on the right so the progress bar cannot cover it.
        self.btn_cancel_job.pack(side=RIGHT, padx=(8, 2), pady=2)
        self.progress = ttk.Progressbar(self.progress_row, length=200)
        self.progress.pack(side=LEFT, fill=X, expand=True, padx=(2, 0), pady=2)

        body = Frame(self.root)
        body.pack(side=TOP, fill=BOTH, expand=True)

        # Fixed-width left column: context (selection) + device subframes.
        leftframe = Frame(body, width=_LEFT_PANEL_WIDTH)
        leftframe["borderwidth"] = 3
        leftframe["relief"] = "sunken"
        leftframe.pack(side=LEFT, fill=Y)
        leftframe.pack_propagate(False)
        self.leftframe = leftframe

        rightframe = Frame(body)
        rightframe["borderwidth"] = 3
        rightframe["relief"] = "sunken"
        rightframe.pack(side=RIGHT, fill=BOTH, expand=True)

        # --- Device subframe: fixed height, locked to bottom of leftframe ---
        # Pack BOTTOM first so Selection fills the remaining space above.
        self.device_panel = Frame(
            leftframe, width=_LEFT_PANEL_WIDTH - 6, height=_DEVICE_PANEL_HEIGHT
        )
        self.device_panel.pack(side=BOTTOM, fill=X, padx=3, pady=(2, 6))
        self.device_panel.pack_propagate(False)

        self.lbl_device_title = Label(
            self.device_panel, text="Device", font=("", 11, "bold")
        )
        self.lbl_device_title.pack(padx=6, pady=(2, 0), anchor="w")

        self._device_caption = ""
        self.lbl_device_caption = Label(
            self.device_panel,
            text="",
            wraplength=_LEFT_TEXT_WRAP,
            justify=LEFT,
        )
        self.lbl_device_caption.pack(padx=6, pady=(4, 0), anchor="w")
        # Fixed-height slot so profile art cannot grow the device panel.
        self.device_graphic_slot = Frame(
            self.device_panel, height=_DEVICE_GRAPHIC_HEIGHT
        )
        self.device_graphic_slot.pack(padx=6, pady=6, fill=X)
        self.device_graphic_slot.pack_propagate(False)
        self.lbl_device_graphic = Label(self.device_graphic_slot)
        self.lbl_device_graphic.place(relx=0.5, rely=0.5, anchor="center")
        self._device_photo: PhotoImage | None = None
        self._device_photo_cache: dict[str, PhotoImage] = {}
        # Album art thumbs for group rows (must keep refs for Tk).
        self._album_art_cache: dict[str, PhotoImage] = {}

        # --- Context subframe: startup hint, then selection metadata ---
        # Fills all space above the bottom-locked device panel.
        self.context_panel = Frame(leftframe)
        self.context_panel.pack(
            side=TOP, fill=BOTH, expand=True, padx=3, pady=(6, 2)
        )

        self.lbl_context_title = Label(
            self.context_panel, text="Selection", font=("", 11, "bold")
        )
        self.lbl_context_title.pack(padx=6, pady=(2, 0), anchor="w")

        self._startup_hint_active = True
        self._context_detail = ""
        self.lbl_context_detail = Label(
            self.context_panel,
            text=EXPERIMENTAL_HINT,
            wraplength=_LEFT_TEXT_WRAP,
            justify=LEFT,
        )
        self.lbl_context_detail.pack(padx=6, pady=(4, 6), anchor="nw")

        Label(rightframe, text="Tracks").pack()
        tree_frame = Frame(rightframe)
        tree_frame.pack(fill=BOTH, expand=True)

        yscroll = Scrollbar(tree_frame)
        yscroll.pack(side=RIGHT, fill=Y)
        xscroll = Scrollbar(tree_frame, orient="horizontal")
        xscroll.pack(side=BOTTOM, fill=X)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            # extended: Shift+click range, Ctrl/Cmd+click toggle multi-select.
            selectmode="extended",
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        yscroll.config(command=self.tree.yview)
        xscroll.config(command=self.tree.xview)

        self.tree.heading("#0", text="#", anchor="w")
        self.tree.heading("title", text="Title", anchor="w")
        self.tree.heading("artist", text="Artist", anchor="w")
        self.tree.heading("album", text="Album", anchor="w")
        self.tree.heading("year", text="Year", anchor="w")

        # Album thumbs live in #0 (only Treeview column that supports images).
        # Width + rowheight leave room so thumbs are not cropped and Title text
        # is not drawn under the image.
        from mtpmanager.infra.album_art import DEFAULT_THUMB_SIZE

        self._thumb_size = DEFAULT_THUMB_SIZE
        self._tree_rowheight = max(DEFAULT_THUMB_SIZE + 8, 52)
        style = ttk.Style(self.root)
        try:
            style.configure("Treeview", rowheight=self._tree_rowheight)
        except Exception:
            pass

        # Expander + thumbnail padding; pushes Title column to the right.
        self.tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        # Title is the stretch column — group header text is shown here (full name).
        self.tree.column("title", width=280, minwidth=120, stretch=True)
        self.tree.column("artist", width=140, minwidth=60)
        self.tree.column("album", width=140, minwidth=60)
        self.tree.column("year", width=56, minwidth=40, stretch=False)

        # Group headers bold (label lives in Title values[0]); transfer tags tint rows.
        self.tree.tag_configure("group", font=("", 11, "bold"))
        self.tree.tag_configure("group_artist", font=("", 12, "bold"))
        self.tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.tree.tag_configure("xfer_queued", background=BG_TRANSFER_QUEUED)
        self.tree.tag_configure("xfer_transcoding", background=BG_TRANSFER_TRANSCODING)
        self.tree.tag_configure(
            "xfer_transferring", background=BG_TRANSFER_TRANSFERRING
        )

        # Callbacks set by controller for column-header sort / context menus.
        self._on_sort_heading = None
        self._prepare_context_menu = None
        self._tracks_interactive = True
        self._mode: Mode = "experimental"
        self._cancel_job_command = None

        self.apply_mode_ui("experimental")

    def active_mode(self) -> Mode:
        return self._mode

    def apply_mode_ui(self, mode: Mode) -> None:
        """Refresh device subframe + Device menu for the active transfer mode.

        Context subframe (selection detail) is independent of mode: it keeps
        the startup hint or last selection text either way.
        """
        self._mode = mode
        stable = mode == "stable"
        self.var_stable_mode.set(stable)
        if stable:
            self.lbl_device_title.configure(text="Stable Mode")
            self.lbl_device_caption.configure(text=STABLE_MODE_HELP)
            self.lbl_device_graphic.configure(image="")
            if self.device_graphic_slot.winfo_ismapped():
                self.device_graphic_slot.pack_forget()
        else:
            self.lbl_device_title.configure(text="Device")
            self.lbl_device_caption.configure(text=self._device_caption)
            if self._device_photo is not None:
                self.lbl_device_graphic.configure(image=self._device_photo)
            if not self.device_graphic_slot.winfo_ismapped():
                self.device_graphic_slot.pack(padx=6, pady=6, fill=X)
        self.apply_mode_actions()

    def is_startup_hint_active(self) -> bool:
        """True while the context subframe still shows the first-run blurb."""
        return bool(self._startup_hint_active)

    def set_context_detail(self, text: str) -> None:
        """Update the context subframe (selection metadata).

        Replaces the first-run experimental hint. Always updates the visible
        label (including under Stable Mode) so selection still has a home.
        """
        self._startup_hint_active = False
        self._context_detail = text or ""
        try:
            self.lbl_context_detail.configure(text=self._context_detail)
        except Exception:
            pass

    def set_library_menu_commands(
        self,
        *,
        on_select_root,
        on_update,
    ) -> None:
        """Wire Library menu entries (called once from the controller)."""
        self.menu_library.entryconfig(MENU_SELECT_ROOT, command=on_select_root)
        self.menu_library.entryconfig(MENU_UPDATE_LIBRARY, command=on_update)

    def set_transfer_menu_commands(
        self,
        *,
        on_sync_entire,
        on_sync_folder,
        on_sync_selected=None,
        on_resume_sync=None,
        on_cancel_job=None,
        on_package_retail=None,
        on_restore_retail=None,
    ) -> None:
        self.menu_transfer.entryconfig(MENU_SYNC_ENTIRE, command=on_sync_entire)
        self.menu_transfer.entryconfig(MENU_SYNC_FOLDER, command=on_sync_folder)
        if on_sync_selected is not None:
            self.menu_transfer.entryconfig(
                MENU_SYNC_SELECTED, command=on_sync_selected
            )
        if on_resume_sync is not None:
            self.menu_transfer.entryconfig(MENU_RESUME_SYNC, command=on_resume_sync)
        if on_package_retail is not None:
            self.menu_transfer.entryconfig(
                MENU_PACKAGE_RETAIL, command=on_package_retail
            )
        if on_restore_retail is not None:
            self.menu_transfer.entryconfig(
                MENU_RESTORE_RETAIL, command=on_restore_retail
            )
        if on_cancel_job is not None:
            self.menu_transfer.entryconfig(MENU_CANCEL_JOB, command=on_cancel_job)
            self._cancel_job_command = on_cancel_job

    def set_sync_selected_enabled(self, enabled: bool, *, count: int = 0) -> None:
        """Enable Transfer → Sync Selected when one or more tracks are selected."""
        state = NORMAL if enabled else DISABLED
        label = MENU_SYNC_SELECTED
        if enabled and count > 0:
            label = f"Sync Selected Tracks ({count})"
        try:
            self.menu_transfer.entryconfig(
                MENU_SYNC_SELECTED, state=state, label=label
            )
        except Exception:
            pass

    def set_resume_sync_enabled(self, enabled: bool) -> None:
        """Enable Transfer → Resume Sync when a durable job can continue."""
        try:
            self.menu_transfer.entryconfig(
                MENU_RESUME_SYNC,
                state=NORMAL if enabled else DISABLED,
            )
        except Exception:
            pass

    def set_config_menu_commands(
        self,
        *,
        on_config,
        on_stable_mode_toggle=None,
        on_artist_folders_toggle=None,
        on_album_folders_toggle=None,
    ) -> None:
        self.menu_config.entryconfig(MENU_CONFIG, command=on_config)
        if on_stable_mode_toggle is not None:
            self.menu_config.entryconfig(
                MENU_STABLE_MODE, command=on_stable_mode_toggle
            )
        if on_artist_folders_toggle is not None:
            self.menu_config.entryconfig(
                MENU_ARTIST_FOLDERS, command=on_artist_folders_toggle
            )
        if on_album_folders_toggle is not None:
            self.menu_config.entryconfig(
                MENU_ALBUM_FOLDERS, command=on_album_folders_toggle
            )

    def set_album_folders_menu_enabled(self, enabled: bool) -> None:
        """Enable/disable album-folder checkbutton (requires artist folders)."""
        self.menu_config.entryconfig(
            MENU_ALBUM_FOLDERS,
            state=NORMAL if enabled else DISABLED,
        )

    def set_device_menu_commands(
        self,
        *,
        on_connect,
        on_disconnect,
        on_device_info,
        on_create_folder,
        on_list_folders,
        on_list_files=None,
        on_list_tracks=None,
        on_get_tracks_from_device=None,
        on_delete_track=None,
        on_get_file_info,
        on_get_track_info=None,
        on_delete_all,
        on_refresh_device_index=None,
        on_send_video=None,
    ) -> None:
        self.menu_device.entryconfig(MENU_CONNECT, command=on_connect)
        self.menu_device.entryconfig(MENU_DISCONNECT, command=on_disconnect)
        self.menu_device.entryconfig(MENU_DEVICE_INFO, command=on_device_info)
        self.menu_device.entryconfig(MENU_CREATE_FOLDER, command=on_create_folder)
        if on_send_video is not None:
            self.menu_device.entryconfig(MENU_SEND_VIDEO, command=on_send_video)
        self.menu_device.entryconfig(MENU_LIST_FOLDERS, command=on_list_folders)
        if on_list_files is not None:
            self.menu_device.entryconfig(MENU_LIST_FILES, command=on_list_files)
        if on_list_tracks is not None:
            self.menu_device.entryconfig(MENU_LIST_TRACKS, command=on_list_tracks)
        if on_get_tracks_from_device is not None:
            self.menu_device.entryconfig(
                MENU_GET_TRACKS_FROM_DEVICE, command=on_get_tracks_from_device
            )
        if on_refresh_device_index is not None:
            self.menu_device.entryconfig(
                MENU_REFRESH_DEVICE_INDEX, command=on_refresh_device_index
            )
        if on_delete_track is not None:
            self.menu_device.entryconfig(MENU_DELETE_TRACK, command=on_delete_track)
        self.menu_device.entryconfig(MENU_GET_FILE_INFO, command=on_get_file_info)
        if on_get_track_info is not None:
            self.menu_device.entryconfig(
                MENU_GET_TRACK_INFO, command=on_get_track_info
            )
        self.menu_device.entryconfig(MENU_DELETE_ALL, command=on_delete_all)

    def set_track_context_commands(
        self,
        *,
        on_sync_track,
        on_sync_album,
        on_sync_artist,
        on_sync_artist_group,
        on_sync_album_group,
        on_sync_selected=None,
    ) -> None:
        if on_sync_selected is not None:
            self.menu_track_ctx.entryconfig(
                CTX_SYNC_SELECTED, command=on_sync_selected
            )
        self.menu_track_ctx.entryconfig(CTX_SYNC_TRACK, command=on_sync_track)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ALBUM, command=on_sync_album)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ARTIST, command=on_sync_artist)
        self.menu_artist_ctx.entryconfig(0, command=on_sync_artist_group)
        self.menu_album_ctx.entryconfig(0, command=on_sync_album_group)

    def set_library_menu_state(
        self,
        *,
        update_enabled: bool,
        select_enabled: bool = True,
    ) -> None:
        """Enable/disable Library menu commands."""
        self.menu_library.entryconfig(
            MENU_SELECT_ROOT,
            state=NORMAL if select_enabled else DISABLED,
        )
        self.menu_library.entryconfig(
            MENU_UPDATE_LIBRARY,
            state=NORMAL if update_enabled else DISABLED,
        )

    def set_library_status(
        self,
        root_path: str,
        track_count: int,
        *,
        root_reachable: bool = True,
        busy_message: str | None = None,
    ) -> None:
        """Update toolbar path label and track count.

        When *busy_message* is set (e.g. during a background scan), the count
        label shows that status instead of a numeric track total.
        """
        if root_path:
            display = _elide_path(root_path)
            if not root_reachable:
                display = f"(unreachable) {display}"
            self.lbl_library_path.configure(text=display)
        else:
            self.lbl_library_path.configure(text="No library selected")
        if busy_message:
            self.lbl_library_count.configure(text=busy_message)
            return
        noun = "track" if track_count == 1 else "tracks"
        self.lbl_library_count.configure(text=f"{track_count} {noun}")

    def set_sort_heading_handler(self, handler) -> None:
        """Wire column heading clicks: handler(column_id) where column_id is
        'title'|'artist'|'album'|'year'|'#0'."""
        self._on_sort_heading = handler

        def bind_heading(col: str) -> None:
            self.tree.heading(col, command=lambda c=col: self._fire_sort_heading(c))

        bind_heading("#0")
        for col in TREE_COLS:
            bind_heading(col)

    def _fire_sort_heading(self, col: str) -> None:
        if self._on_sort_heading is not None:
            self._on_sort_heading(col)

    def clear_track_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        # Drop in-memory PhotoImage refs; on-disk thumbs remain.
        self._album_art_cache.clear()

    def album_art_photo_from_disk(
        self,
        track_path: str,
        *,
        cache_key: str | None = None,
        size: int | None = None,
    ) -> PhotoImage | None:
        """Load a *pre-cached* PNG thumb (main thread, no extract).

        Returns None if the disk cache has not been built yet — caller should
        schedule a background ensure + apply.
        """
        from mtpmanager.infra.album_art import (
            DEFAULT_THUMB_SIZE,
            cached_thumb_exists,
            photoimage_from_cache_file,
        )

        size = size if size is not None else getattr(self, "_thumb_size", DEFAULT_THUMB_SIZE)
        key = cache_key or track_path
        if key in self._album_art_cache:
            return self._album_art_cache[key]
        path = cached_thumb_exists(track_path, size=size)
        if path is None:
            return None
        photo = photoimage_from_cache_file(path, master=self.root)
        if photo is not None:
            self._album_art_cache[key] = photo
        return photo

    def apply_album_art_photo(
        self,
        iid: str,
        track_path: str,
        *,
        cache_key: str | None = None,
        size: int | None = None,
    ) -> bool:
        """Set tree item image from disk cache; return True if applied."""
        if not self.tree.exists(iid):
            return False
        photo = self.album_art_photo_from_disk(
            track_path, cache_key=cache_key or iid, size=size
        )
        if photo is None:
            return False
        try:
            self.tree.item(iid, image=photo)
            return True
        except Exception:
            return False

    def set_tracks_usable(self, usable: bool) -> None:
        """Allow interaction, or mark the tree as dead/unreachable."""
        self._tracks_interactive = usable
        if usable:
            self.tree.configure(selectmode="extended")
            # Drop dead tag from all items
            for iid in self._all_iids():
                tags = [t for t in self.tree.item(iid, "tags") if t != "dead"]
                self.tree.item(iid, tags=tags)
            return
        self.tree.configure(selectmode="none")
        for iid in self._all_iids():
            tags = list(self.tree.item(iid, "tags"))
            if "dead" not in tags:
                tags.append("dead")
            self.tree.item(iid, tags=tags)

    def _all_iids(self) -> list[str]:
        out: list[str] = []

        def walk(parent: str) -> None:
            for child in self.tree.get_children(parent):
                out.append(child)
                walk(child)

        walk("")
        return out

    def set_track_transfer_style(self, iid: str, status: str | None) -> None:
        """Tint a track row for transfer state via tags."""
        if not self.tree.exists(iid):
            return
        tags = [
            t
            for t in self.tree.item(iid, "tags")
            if not str(t).startswith("xfer_")
        ]
        if status in (None, "done", "failed", "skipped", ""):
            self.tree.item(iid, tags=tags)
            return
        if status == "transferring":
            tags.append("xfer_transferring")
        elif status == "transcoding":
            tags.append("xfer_transcoding")
        else:
            tags.append("xfer_queued")
        self.tree.item(iid, tags=tags)

    def clear_transfer_styles(self) -> None:
        """Clear all transfer tint tags from the tree."""
        for iid in self._all_iids():
            tags = [
                t
                for t in self.tree.item(iid, "tags")
                if not str(t).startswith("xfer_")
            ]
            self.tree.item(iid, tags=tags)

    def popup_track_context(self, event) -> str | None:
        """Show context menu for the row under the pointer.

        If the row is already part of a multi-selection, keep the selection
        (so bulk Sync Selected works). Otherwise select only that row.

        Track rows get the full sync menu. Artist/album group headers get a
        single “Sync all from …” / “Sync album …” item. Year groups have none.
        """
        menu = None
        try:
            if not self._tracks_interactive:
                return "break"
            row = self.tree.identify_row(event.y)
            if not row:
                return "break"
            tags = set(self.tree.item(row, "tags"))
            # Preserve multi-select when right-clicking inside the selection.
            current = self.tree.selection()
            if row not in current:
                self.tree.selection_set(row)
            self.tree.focus(row)
            self.tree.see(row)

            if "track" in tags:
                menu = self.menu_track_ctx
            elif "group_artist" in tags:
                menu = self.menu_artist_ctx
            elif "group_album" in tags:
                menu = self.menu_album_ctx
            else:
                return "break"

            # Controller may refresh dynamic labels via this hook.
            if self._prepare_context_menu is not None:
                self._prepare_context_menu(row, tags)

            menu.tk_popup(event.x_root, event.y_root)
        finally:
            if menu is not None:
                try:
                    menu.grab_release()
                except Exception:
                    pass
        return "break"

    def set_prepare_context_menu(self, handler) -> None:
        """Optional hook(row_iid, tags) called before a context menu is shown."""
        self._prepare_context_menu = handler

    def selected_tree_iid(self) -> str | None:
        """Primary selected row (focus preferred, else first in selection)."""
        focus = self.tree.focus()
        if focus and self.tree.exists(focus):
            return focus
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0]

    def selected_tree_iids(self) -> list[str]:
        """All selected row iids (multi-select)."""
        return list(self.tree.selection())

    def set_progress_status(self, text: str) -> None:
        """Update the status line above the progress bar (sync track, etc.)."""
        try:
            self.lbl_progress_status.configure(text=text or "")
        except Exception:
            pass

    def set_cancel_job_command(self, command) -> None:
        """Wire Cancel (progress-bar button + Transfer menu + Escape)."""
        self._cancel_job_command = command
        self.btn_cancel_job.configure(command=command)
        try:
            self.menu_transfer.entryconfig(MENU_CANCEL_JOB, command=command)
        except Exception:
            pass
        # Escape cancels when a job is running (no-op if Cancel is disabled).
        self.root.bind("<Escape>", self._on_escape_cancel)

    def _on_escape_cancel(self, _event=None):
        if self._cancel_job_command is None:
            return
        try:
            state = str(self.btn_cancel_job.cget("state"))
        except Exception:
            return
        if state == str(NORMAL) or state == "normal":
            self._cancel_job_command()

    def set_cancel_job_enabled(self, enabled: bool) -> None:
        """Enable Cancel (button + Transfer menu) while a job is running."""
        state = NORMAL if enabled else DISABLED
        try:
            self.btn_cancel_job.configure(
                state=state,
                text="Cancel",
            )
        except Exception:
            pass
        try:
            self.menu_transfer.entryconfig(MENU_CANCEL_JOB, state=state)
        except Exception:
            pass

    def apply_mode_actions(self) -> None:
        """Enable Device menu only when PyMTP (non-Stable) is active."""
        experimental = self.active_mode() == "experimental"
        state = NORMAL if experimental else DISABLED
        for label in _DEVICE_MENU_LABELS:
            try:
                self.menu_device.entryconfig(label, state=state)
            except Exception:
                pass

    def set_device_graphic(
        self,
        image_path: Path | str | None,
        *,
        caption: str = "",
        max_width: int = _DEVICE_GRAPHIC_MAX_WIDTH,
        max_height: int = _DEVICE_GRAPHIC_HEIGHT,
    ) -> None:
        """Show device art in the fixed graphic slot, or clear when *image_path* is None.

        Images are scaled to fit ``max_width`` × ``max_height`` (default 180×140).
        Caption/photo are remembered so Stable Mode can borrow the device
        caption label for help text and restore art when PyMTP mode returns.
        """
        if image_path is None:
            self._device_photo = None
            self._device_caption = ""
            if self._mode != "stable":
                self.lbl_device_graphic.configure(image="")
                self.lbl_device_caption.configure(text="")
            return

        path = Path(image_path)
        key = f"{path.resolve()}:{max_width}x{max_height}"
        photo = self._device_photo_cache.get(key)
        if photo is None:
            if not path.is_file():
                self.set_device_graphic(None)
                return
            try:
                photo = self._load_device_photo(
                    path, max_width=max_width, max_height=max_height
                )
                if photo is None:
                    self.set_device_graphic(None)
                    return
                self._device_photo_cache[key] = photo
            except Exception:
                self.set_device_graphic(None)
                return

        self._device_photo = photo  # prevent GC
        self._device_caption = caption or ""
        if self._mode != "stable":
            self.lbl_device_graphic.configure(image=photo)
            self.lbl_device_caption.configure(text=self._device_caption)
            if not self.device_graphic_slot.winfo_ismapped():
                self.device_graphic_slot.pack(padx=6, pady=6, fill=X)

    def _load_device_photo(
        self,
        path: Path,
        *,
        max_width: int,
        max_height: int,
    ) -> PhotoImage | None:
        """Load *path* scaled to fit inside max_width × max_height.

        Prefers Pillow (LANCZOS) when available so tall assets (e.g. 540×900)
        land cleanly in the 140px slot; falls back to integer PhotoImage
        subsample.
        """
        max_width = max(1, int(max_width))
        max_height = max(1, int(max_height))
        try:
            from PIL import Image, ImageTk

            im = Image.open(path)
            im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
            im.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(im, master=self.root)
        except Exception:
            pass

        raw = PhotoImage(file=str(path), master=self.root)
        # Integer downsample so both width and height stay within the slot.
        sx = raw.width() / max_width
        sy = raw.height() / max_height
        factor = max(1, int(math.ceil(max(sx, sy))))
        return raw.subsample(factor, factor) if factor > 1 else raw

    def mainloop(self) -> None:
        self.root.mainloop()
