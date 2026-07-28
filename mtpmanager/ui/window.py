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
    DoubleVar,
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
    StringVar,
    Tk,
    Toplevel,
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
# Currently playing track (deep dark purple; light text for contrast).
BG_PLAYING = "#4a1f6b"
FG_PLAYING = "#f0e6fa"
# Playback title label (Tk character-cell width) + marquee speed.
_PLAYBACK_TITLE_WIDTH = 28
_PLAYBACK_MARQUEE_MS = 250
# Gap between end and start when the title scrolls.
_PLAYBACK_MARQUEE_GAP = "   "

# Tree column ids (values order).
TREE_COLS = ("title", "artist", "album", "year")

# Library menu labels (used for entryconfig by label).
MENU_MANAGE_LIBRARY = "Manage Library…"
MENU_MANAGE_PLAYLISTS = "Manage Playlists…"
# Back-compat aliases (older docs / call sites).
MENU_SELECT_ROOT = MENU_MANAGE_LIBRARY
MENU_UPDATE_LIBRARY = MENU_MANAGE_LIBRARY

# Transfer menu
MENU_SYNC_ENTIRE = "Sync Entire Library"
MENU_SYNC_FOLDER = "Sync Folder…"
MENU_SYNC_SELECTED = "Sync Selected Tracks"
MENU_RESUME_SYNC = "Resume Sync"
MENU_CANCEL_JOB = "Cancel Current Job"
MENU_PACKAGE_RETAIL = "Package Retail Demos… (experimental)"
MENU_RESTORE_RETAIL = "Restore Retail Package… (experimental)"

# View menu
MENU_ALWAYS_SHOW_PLAYBACK = "Always show playback controls"

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
CTX_PLAY_TRACK = "Play This Track"
CTX_PLAY_TRACKS = "Play These Tracks"
CTX_ADD_TO_PLAYLIST = "Add This Track to Playlist…"
CTX_ADD_TRACKS_TO_PLAYLIST = "Add These Tracks to Playlist…"
CTX_EXCLUDE_FILE = "Exclude this file…"
CTX_EXCLUDE_FOLDER = "Exclude this folder…"

# Group header context menus (labels updated dynamically before popup)
CTX_SYNC_ARTIST_GROUP = "Sync all from Artist"
CTX_SYNC_ALBUM_GROUP = "Sync album"
CTX_PLAY_ARTIST_GROUP = "Play All from Artist"
CTX_PLAY_ALBUM_GROUP = "Play Album"
CTX_ADD_ARTIST_TO_PLAYLIST = "Add All from Artist to Playlist…"
CTX_ADD_ALBUM_TO_PLAYLIST = "Add Album to Playlist…"
CTX_EXCLUDE_GROUP_FOLDER = "Exclude this folder…"

# Playlists tab context menu
CTX_PLAYLIST_REMOVE = "Remove from Playlist"
CTX_PLAYLIST_PLAY_TRACK = "Play This Track"
CTX_PLAYLIST_SYNC = "Sync playlist to device"

# Device media context menus (on-device Music / Video / Audiobooks trees)
CTX_DEVICE_DELETE = "Delete from device…"
CTX_DEVICE_PULL = "Pull to library…"
CTX_DEVICE_PULL_FOLDER = "Pull to folder…"
CTX_DEVICE_DELETE_ARTIST = "Delete all from Artist…"
CTX_DEVICE_DELETE_ALBUM = "Delete album from device…"
CTX_DEVICE_DELETE_FOLDER = "Delete all in folder…"
CTX_DEVICE_INFO = "Device Info"
CTX_DEVICE_DELETE_ALL = "Delete All Tracks…"

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


class _HoverTip:
    """Delayed hover tip for a widget (full path / multi-root list).

    macOS Tk Labels default to ``systemTextColor``. In dark mode that is a
    *light* color; pairing it with a cream tooltip background makes the text
    look blank. Always set an explicit dark foreground on a light panel.
    """

    # Cool frosted panel — solid stand-in for Liquid Glass (Tk has no blur).
    # Explicit colors only; never systemTextColor (invisible on light panels
    # in dark mode).
    _BG = "#e6eaef"  # cloud gray, slight cool bias
    _FG = "#1d1d1f"  # near-black label text
    _EDGE = "#b4bcc6"  # soft cool rim
    _DELAY_MS = 400
    _WRAP = 520

    def __init__(self, widget) -> None:
        self.widget = widget
        self._text = ""
        self._tip: Toplevel | None = None
        self._label: Label | None = None
        self._after_id = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip()
        if not self._text:
            self._hide()
            return
        # Keep an open tip in sync when library status refreshes under the cursor.
        if self._label is not None:
            try:
                self._label.configure(text=self._text)
            except Exception:
                self._hide()

    def _on_enter(self, _event=None) -> None:
        self._schedule()

    def _on_leave(self, _event=None) -> None:
        self._cancel_schedule()
        self._hide()

    def _schedule(self) -> None:
        self._cancel_schedule()
        if not self._text:
            return
        try:
            self._after_id = self.widget.after(self._DELAY_MS, self._show)
        except Exception:
            self._after_id = None

    def _cancel_schedule(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        text = self._text
        if not text:
            return
        if self._tip is not None:
            if self._label is not None:
                try:
                    self._label.configure(text=text)
                except Exception:
                    self._hide()
                else:
                    return
            else:
                self._hide()

        try:
            master = self.widget.winfo_toplevel()
            x = int(self.widget.winfo_rootx()) + 12
            y = int(self.widget.winfo_rooty()) + int(self.widget.winfo_height()) + 6
        except Exception:
            return

        tip = Toplevel(master)
        # Build off-screen, then map — avoids blank overrideredirect windows
        # on some Tk/macOS builds.
        try:
            tip.withdraw()
        except Exception:
            pass
        tip.wm_overrideredirect(True)
        try:
            # macOS: true tooltip chrome; does not activate the app.
            tip.tk.call(
                "::tk::unsupported::MacWindowStyle",
                "style",
                tip._w,
                "help",
                "noActivates",
            )
        except Exception:
            pass
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass

        # 1px cool rim (Frame) around the panel so it reads against light
        # window chrome without the old warm “sticky note” look.
        rim = Frame(tip, background=self._EDGE, borderwidth=0)
        rim.pack(fill=BOTH, expand=True)
        label = Label(
            rim,
            text=text,
            justify=LEFT,
            background=self._BG,
            foreground=self._FG,
            activebackground=self._BG,
            activeforeground=self._FG,
            disabledforeground=self._FG,
            relief="flat",
            borderwidth=0,
            padx=9,
            pady=6,
            wraplength=self._WRAP,
            # Explicit family avoids theme fonts that can render invisibly
            # in borderless help windows on some Aqua builds.
            font=("TkDefaultFont", 12),
        )
        label.pack(padx=1, pady=1)
        try:
            tip.update_idletasks()
            tip.wm_geometry(f"+{x}+{y}")
            tip.deiconify()
            tip.lift()
        except Exception:
            try:
                tip.destroy()
            except Exception:
                pass
            return

        self._tip = tip
        self._label = label

    def _hide(self) -> None:
        tip = self._tip
        self._tip = None
        self._label = None
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass


class MainWindow:
    def __init__(self, root: Tk | None = None):
        self.root = root or Tk()
        self.root.title("MTP Manager")
        self.root.geometry("1000x600")
        self.root["borderwidth"] = 1
        self.root["relief"] = "sunken"

        # Menubar: Library | Transfer | Device | View | Config
        self.menubar = Menu(self.root)
        self.root.config(menu=self.menubar)

        self.menu_library = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Library", menu=self.menu_library)
        self.menu_library.add_command(label=MENU_MANAGE_LIBRARY)
        self.menu_library.add_command(label=MENU_MANAGE_PLAYLISTS)

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

        self.var_always_show_playback = BooleanVar(value=False)
        self.menu_view = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="View", menu=self.menu_view)
        self.menu_view.add_checkbutton(
            label=MENU_ALWAYS_SHOW_PLAYBACK,
            variable=self.var_always_show_playback,
            onvalue=True,
            offvalue=False,
        )

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
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_PLAY_TRACK)
        self.menu_track_ctx.add_command(label=CTX_ADD_TO_PLAYLIST)
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_EXCLUDE_FILE)
        self.menu_track_ctx.add_command(label=CTX_EXCLUDE_FOLDER)

        self.menu_artist_ctx = Menu(self.root, tearoff=0)
        self.menu_artist_ctx.add_command(label=CTX_SYNC_ARTIST_GROUP)
        self.menu_artist_ctx.add_separator()
        self.menu_artist_ctx.add_command(label=CTX_PLAY_ARTIST_GROUP)
        self.menu_artist_ctx.add_command(label=CTX_ADD_ARTIST_TO_PLAYLIST)

        self.menu_album_ctx = Menu(self.root, tearoff=0)
        self.menu_album_ctx.add_command(label=CTX_SYNC_ALBUM_GROUP)
        self.menu_album_ctx.add_separator()
        self.menu_album_ctx.add_command(label=CTX_PLAY_ALBUM_GROUP)
        self.menu_album_ctx.add_command(label=CTX_ADD_ALBUM_TO_PLAYLIST)
        self.menu_album_ctx.add_separator()
        self.menu_album_ctx.add_command(label=CTX_EXCLUDE_GROUP_FOLDER)

        self.menu_playlist_ctx = Menu(self.root, tearoff=0)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_PLAY_TRACK)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_REMOVE)
        self.menu_playlist_ctx.add_separator()
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_SYNC)

        # Device on-media context menus (delete / pull).
        self.menu_device_track_ctx = Menu(self.root, tearoff=0)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_PULL)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_PULL_FOLDER)
        self.menu_device_track_ctx.add_separator()
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_DELETE)

        self.menu_device_artist_ctx = Menu(self.root, tearoff=0)
        self.menu_device_artist_ctx.add_command(label=CTX_DEVICE_DELETE_ARTIST)

        self.menu_device_album_ctx = Menu(self.root, tearoff=0)
        self.menu_device_album_ctx.add_command(label=CTX_DEVICE_DELETE_ALBUM)

        self.menu_device_folder_ctx = Menu(self.root, tearoff=0)
        self.menu_device_folder_ctx.add_command(label=CTX_DEVICE_DELETE_FOLDER)

        # Device panel / graphic (same actions as Device menu).
        self.menu_device_panel_ctx = Menu(self.root, tearoff=0)
        self.menu_device_panel_ctx.add_command(label=CTX_DEVICE_INFO)
        self.menu_device_panel_ctx.add_separator()
        self.menu_device_panel_ctx.add_command(label=CTX_DEVICE_DELETE_ALL)

        self._prepare_device_context_menu = None

        # Status toolbar: path + track count only (no duplicate title header).
        library_toolbar = Frame(self.root, borderwidth=1, relief="sunken")
        library_toolbar.pack(side=TOP, fill=X, padx=2, pady=2)

        Label(library_toolbar, text="Library:").pack(side=LEFT, padx=(6, 2), pady=4)

        self.lbl_library_path = Label(
            library_toolbar,
            text="No library selected",
            anchor="w",
        )
        self.lbl_library_path.pack(side=LEFT, fill=X, expand=True, padx=2, pady=4)
        self._library_path_tip = _HoverTip(self.lbl_library_path)

        self.lbl_library_count = Label(library_toolbar, text="0 tracks")
        self.lbl_library_count.pack(side=LEFT, padx=(6, 8), pady=4)

        # Pack bottom bar *before* the expanding body so it always keeps a
        # visible strip (Tk expand can otherwise starve a late BOTTOM pack).
        bottomframe = Frame(self.root)
        bottomframe["borderwidth"] = 1
        bottomframe["relief"] = "sunken"
        bottomframe.pack(side=BOTTOM, fill=X)
        self.bottomframe = bottomframe

        # Playback controls (hidden unless playing or View → always show).
        self.playback_row = Frame(bottomframe)
        self._playback_row_visible = False
        self._playback_always_show = False
        self._playback_session_active = False
        self._playback_duration = 0.0
        self._playback_show_nav = False
        self._scrub_dragging = False
        self._scrub_programmatic = False
        self._on_playback_play_pause = None
        self._on_playback_prev = None
        self._on_playback_next = None
        self._on_playback_close = None
        self._on_playback_seek = None
        self._playing_iid: str | None = None
        self._playback_title_full = ""
        self._playback_title_offset = 0
        self._playback_marquee_after_id: str | None = None

        self.btn_playback_prev = Button(
            self.playback_row, text="Prev", width=5, state=DISABLED
        )
        self.btn_playback_play = Button(
            self.playback_row, text="Play", width=6, state=DISABLED
        )
        self.btn_playback_next = Button(
            self.playback_row, text="Next", width=5, state=DISABLED
        )
        self.var_playback_scrub = DoubleVar(value=0.0)
        self.playback_scrub = ttk.Scale(
            self.playback_row,
            from_=0.0,
            to=1000.0,
            orient="horizontal",
            variable=self.var_playback_scrub,
            command=self._on_scrub_command,
        )
        self.lbl_playback_time = Label(
            self.playback_row, text="0:00 / 0:00", width=12, anchor="e"
        )
        self.lbl_playback_title = Label(
            self.playback_row,
            text="",
            anchor="w",
            width=_PLAYBACK_TITLE_WIDTH,
        )
        self.btn_playback_close = Button(
            self.playback_row, text="×", width=3
        )
        # Layout: [Prev] [Play] [Next] [title] [====scrub====] [time] [×]
        self.btn_playback_prev.pack(side=LEFT, padx=(4, 2), pady=4)
        self.btn_playback_play.pack(side=LEFT, padx=2, pady=4)
        self.btn_playback_next.pack(side=LEFT, padx=2, pady=4)
        self.lbl_playback_title.pack(side=LEFT, padx=(6, 4), pady=4)
        self.btn_playback_close.pack(side=RIGHT, padx=(2, 4), pady=4)
        self.lbl_playback_time.pack(side=RIGHT, padx=(4, 2), pady=4)
        self.playback_scrub.pack(
            side=LEFT, fill=X, expand=True, padx=(4, 4), pady=4
        )
        self.playback_scrub.bind("<ButtonPress-1>", self._on_scrub_press, add="+")
        self.playback_scrub.bind(
            "<ButtonRelease-1>", self._on_scrub_release, add="+"
        )

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
        leftframe["borderwidth"] = 1
        leftframe["relief"] = "sunken"
        leftframe.pack(side=LEFT, fill=Y)
        leftframe.pack_propagate(False)
        self.leftframe = leftframe

        rightframe = Frame(body)
        rightframe["borderwidth"] = 1
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
        self._context_path = ""
        self.lbl_context_detail = Label(
            self.context_panel,
            text=EXPERIMENTAL_HINT,
            wraplength=_LEFT_TEXT_WRAP,
            justify=LEFT,
        )
        self.lbl_context_detail.pack(padx=6, pady=(4, 0), anchor="nw")
        # Full host path for single-track selection (italic, secondary).
        self.lbl_context_path = Label(
            self.context_panel,
            text="",
            wraplength=_LEFT_TEXT_WRAP,
            justify=LEFT,
            font=("", 10, "italic"),
        )
        self.lbl_context_path.pack(padx=6, pady=(2, 6), anchor="nw")

        self.media_notebook = ttk.Notebook(rightframe)
        self.media_notebook.pack(side=TOP, fill=BOTH, expand=True, padx=2, pady=2)
        self.musicLibrary_tab = Frame(self.media_notebook)
        self.videoLibrary_tab = Frame(self.media_notebook)
        self.audiobooksLibrary_tab = Frame(self.media_notebook)
        self.podcastsLibrary_tab = Frame(self.media_notebook)
        self.playlists_tab = Frame(self.media_notebook)
        self.device_tab = Frame(self.media_notebook)
        self.media_notebook.add(self.musicLibrary_tab, text="Music")
        self.media_notebook.add(self.videoLibrary_tab, text="Video")
        self.media_notebook.add(self.audiobooksLibrary_tab, text="Audiobooks")
        self.media_notebook.add(self.podcastsLibrary_tab, text="Podcasts")
        self.media_notebook.add(self.playlists_tab, text="Playlists")
        self.media_notebook.add(self.device_tab, text="Device")

        tree_frame = Frame(self.musicLibrary_tab)
        tree_frame.pack(fill=BOTH, expand=True)

        vl_tree_frame = Frame(self.videoLibrary_tab)
        vl_tree_frame.pack(fill=BOTH, expand=True)

        ab_tree_frame = Frame(self.audiobooksLibrary_tab)
        ab_tree_frame.pack(fill=BOTH, expand=True)

        p_tree_frame = Frame(self.podcastsLibrary_tab)
        p_tree_frame.pack(fill=BOTH, expand=True)

        # Playlists tab: combobox + toolbar + flat track list.
        pl_toolbar = Frame(self.playlists_tab)
        pl_toolbar.pack(side=TOP, fill=X, padx=4, pady=(4, 2))
        Label(pl_toolbar, text="Playlist:").pack(side=LEFT, padx=(2, 4))
        self.var_playlist_choice = StringVar(value="")
        self.playlist_combo = ttk.Combobox(
            pl_toolbar,
            textvariable=self.var_playlist_choice,
            state="disabled",
            width=36,
        )
        self.playlist_combo.pack(side=LEFT, padx=2)
        self.btn_playlist_rename = Button(
            pl_toolbar, text="Rename…", width=9, state=DISABLED
        )
        self.btn_playlist_rename.pack(side=LEFT, padx=2)
        self.btn_playlist_new = Button(pl_toolbar, text="+", width=3)
        self.btn_playlist_new.pack(side=LEFT, padx=2)
        self.btn_playlist_delete = Button(
            pl_toolbar, text="−", width=3, state=DISABLED
        )
        self.btn_playlist_delete.pack(side=LEFT, padx=2)
        self.btn_playlist_sync = Button(
            pl_toolbar, text="Sync playlist to device", state=DISABLED
        )
        self.btn_playlist_sync.pack(side=LEFT, padx=(8, 2))
        self.lbl_playlist_status = Label(pl_toolbar, text="", anchor="w")
        self.lbl_playlist_status.pack(side=LEFT, fill=X, expand=True, padx=6)

        pl_tree_frame = Frame(self.playlists_tab)
        pl_tree_frame.pack(side=TOP, fill=BOTH, expand=True)

        # Device tab: nested notebook by media category.
        # Music + Video + Audiobooks; Podcasts deferred.
        self.device_notebook = ttk.Notebook(self.device_tab)
        self.device_notebook.pack(side=TOP, fill=BOTH, expand=True)

        self.device_music_tab = Frame(self.device_notebook)
        self.device_video_tab = Frame(self.device_notebook)
        self.device_audiobooks_tab = Frame(self.device_notebook)
        self.device_podcasts_tab = Frame(self.device_notebook)
        self.device_notebook.add(self.device_music_tab, text="Music")
        self.device_notebook.add(self.device_video_tab, text="Video")
        self.device_notebook.add(self.device_audiobooks_tab, text="Audiobooks")
        self.device_notebook.add(self.device_podcasts_tab, text="Podcasts")

        d_tree_frame = Frame(self.device_music_tab)
        d_tree_frame.pack(fill=BOTH, expand=True)

        dv_tree_frame = Frame(self.device_video_tab)
        dv_tree_frame.pack(fill=BOTH, expand=True)

        dab_tree_frame = Frame(self.device_audiobooks_tab)
        dab_tree_frame.pack(fill=BOTH, expand=True)

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

        # Device music tree (same columns/grouping as the library tree).
        d_yscroll = Scrollbar(d_tree_frame)
        d_yscroll.pack(side=RIGHT, fill=Y)
        d_xscroll = Scrollbar(d_tree_frame, orient="horizontal")
        d_xscroll.pack(side=BOTTOM, fill=X)

        self.device_tree = ttk.Treeview(
            d_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            yscrollcommand=d_yscroll.set,
            xscrollcommand=d_xscroll.set,
        )
        self.device_tree.pack(side=LEFT, fill=BOTH, expand=True)
        d_yscroll.config(command=self.device_tree.yview)
        d_xscroll.config(command=self.device_tree.xview)

        self.device_tree.heading("#0", text="#", anchor="w")
        self.device_tree.heading("title", text="Title", anchor="w")
        self.device_tree.heading("artist", text="Artist", anchor="w")
        self.device_tree.heading("album", text="Album", anchor="w")
        self.device_tree.heading("year", text="Year", anchor="w")
        self.device_tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        self.device_tree.column("title", width=280, minwidth=120, stretch=True)
        self.device_tree.column("artist", width=140, minwidth=60)
        self.device_tree.column("album", width=140, minwidth=60)
        self.device_tree.column("year", width=56, minwidth=40, stretch=False)

        # Device video tree (same columns; grouped by Video / TV folder).
        dv_yscroll = Scrollbar(dv_tree_frame)
        dv_yscroll.pack(side=RIGHT, fill=Y)
        dv_xscroll = Scrollbar(dv_tree_frame, orient="horizontal")
        dv_xscroll.pack(side=BOTTOM, fill=X)

        self.device_video_tree = ttk.Treeview(
            dv_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            yscrollcommand=dv_yscroll.set,
            xscrollcommand=dv_xscroll.set,
        )
        self.device_video_tree.pack(side=LEFT, fill=BOTH, expand=True)
        dv_yscroll.config(command=self.device_video_tree.yview)
        dv_xscroll.config(command=self.device_video_tree.xview)

        self.device_video_tree.heading("#0", text="#", anchor="w")
        self.device_video_tree.heading("title", text="Title", anchor="w")
        self.device_video_tree.heading("artist", text="Artist", anchor="w")
        self.device_video_tree.heading("album", text="Album", anchor="w")
        self.device_video_tree.heading("year", text="Year", anchor="w")
        self.device_video_tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        self.device_video_tree.column("title", width=280, minwidth=120, stretch=True)
        self.device_video_tree.column("artist", width=140, minwidth=60)
        self.device_video_tree.column("album", width=140, minwidth=60)
        self.device_video_tree.column("year", width=56, minwidth=40, stretch=False)

        # Library video tree: folder → files; title column only (filename).
        vl_yscroll = Scrollbar(vl_tree_frame)
        vl_yscroll.pack(side=RIGHT, fill=Y)
        vl_xscroll = Scrollbar(vl_tree_frame, orient="horizontal")
        vl_xscroll.pack(side=BOTTOM, fill=X)

        self.videos_tree = ttk.Treeview(
            vl_tree_frame,
            columns=("title",),
            show="tree headings",
            selectmode="extended",
            yscrollcommand=vl_yscroll.set,
            xscrollcommand=vl_xscroll.set,
        )
        self.videos_tree.pack(side=LEFT, fill=BOTH, expand=True)
        vl_yscroll.config(command=self.videos_tree.yview)
        vl_xscroll.config(command=self.videos_tree.xview)

        self.videos_tree.heading("#0", text="", anchor="w")
        self.videos_tree.heading("title", text="Title", anchor="w")
        self.videos_tree.column(
            "#0",
            width=28,
            minwidth=24,
            stretch=False,
        )
        self.videos_tree.column("title", width=420, minwidth=120, stretch=True)

        # Library audiobooks tree (same columns; Author → Album - Year grouping).
        ab_yscroll = Scrollbar(ab_tree_frame)
        ab_yscroll.pack(side=RIGHT, fill=Y)
        ab_xscroll = Scrollbar(ab_tree_frame, orient="horizontal")
        ab_xscroll.pack(side=BOTTOM, fill=X)

        self.audiobooks_tree = ttk.Treeview(
            ab_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            yscrollcommand=ab_yscroll.set,
            xscrollcommand=ab_xscroll.set,
        )
        self.audiobooks_tree.pack(side=LEFT, fill=BOTH, expand=True)
        ab_yscroll.config(command=self.audiobooks_tree.yview)
        ab_xscroll.config(command=self.audiobooks_tree.xview)

        self.audiobooks_tree.heading("#0", text="#", anchor="w")
        self.audiobooks_tree.heading("title", text="Title", anchor="w")
        self.audiobooks_tree.heading("artist", text="Author", anchor="w")
        self.audiobooks_tree.heading("album", text="Album", anchor="w")
        self.audiobooks_tree.heading("year", text="Year", anchor="w")
        self.audiobooks_tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        self.audiobooks_tree.column("title", width=280, minwidth=120, stretch=True)
        self.audiobooks_tree.column("artist", width=140, minwidth=60)
        self.audiobooks_tree.column("album", width=140, minwidth=60)
        self.audiobooks_tree.column("year", width=56, minwidth=40, stretch=False)

        # Playlists tab tree (flat ordered list; same columns as Music).
        pl_yscroll = Scrollbar(pl_tree_frame)
        pl_yscroll.pack(side=RIGHT, fill=Y)
        pl_xscroll = Scrollbar(pl_tree_frame, orient="horizontal")
        pl_xscroll.pack(side=BOTTOM, fill=X)

        self.playlist_tree = ttk.Treeview(
            pl_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            yscrollcommand=pl_yscroll.set,
            xscrollcommand=pl_xscroll.set,
        )
        self.playlist_tree.pack(side=LEFT, fill=BOTH, expand=True)
        pl_yscroll.config(command=self.playlist_tree.yview)
        pl_xscroll.config(command=self.playlist_tree.xview)

        self.playlist_tree.heading("#0", text="#", anchor="w")
        self.playlist_tree.heading("title", text="Title", anchor="w")
        self.playlist_tree.heading("artist", text="Artist", anchor="w")
        self.playlist_tree.heading("album", text="Album", anchor="w")
        self.playlist_tree.heading("year", text="Year", anchor="w")
        self.playlist_tree.column(
            "#0", width=48, minwidth=40, stretch=False
        )
        self.playlist_tree.column("title", width=280, minwidth=120, stretch=True)
        self.playlist_tree.column("artist", width=140, minwidth=60)
        self.playlist_tree.column("album", width=140, minwidth=60)
        self.playlist_tree.column("year", width=56, minwidth=40, stretch=False)
        self.playlist_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.playlist_tree.tag_configure(
            "playing", background=BG_PLAYING, foreground=FG_PLAYING
        )
        self.playlist_tree.tag_configure("xfer_queued", background=BG_TRANSFER_QUEUED)
        self.playlist_tree.tag_configure(
            "xfer_transcoding", background=BG_TRANSFER_TRANSCODING
        )
        self.playlist_tree.tag_configure(
            "xfer_transferring", background=BG_TRANSFER_TRANSFERRING
        )

        # Device audiobooks tree (same columns/grouping as library audiobooks).
        dab_yscroll = Scrollbar(dab_tree_frame)
        dab_yscroll.pack(side=RIGHT, fill=Y)
        dab_xscroll = Scrollbar(dab_tree_frame, orient="horizontal")
        dab_xscroll.pack(side=BOTTOM, fill=X)

        self.device_audiobooks_tree = ttk.Treeview(
            dab_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            yscrollcommand=dab_yscroll.set,
            xscrollcommand=dab_xscroll.set,
        )
        self.device_audiobooks_tree.pack(side=LEFT, fill=BOTH, expand=True)
        dab_yscroll.config(command=self.device_audiobooks_tree.yview)
        dab_xscroll.config(command=self.device_audiobooks_tree.xview)

        self.device_audiobooks_tree.heading("#0", text="#", anchor="w")
        self.device_audiobooks_tree.heading("title", text="Title", anchor="w")
        self.device_audiobooks_tree.heading("artist", text="Author", anchor="w")
        self.device_audiobooks_tree.heading("album", text="Album", anchor="w")
        self.device_audiobooks_tree.heading("year", text="Year", anchor="w")
        self.device_audiobooks_tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        self.device_audiobooks_tree.column(
            "title", width=280, minwidth=120, stretch=True
        )
        self.device_audiobooks_tree.column("artist", width=140, minwidth=60)
        self.device_audiobooks_tree.column("album", width=140, minwidth=60)
        self.device_audiobooks_tree.column(
            "year", width=56, minwidth=40, stretch=False
        )

        # Group headers bold (label lives in Title values[0]); transfer tags tint rows.
        self.tree.tag_configure("group", font=("", 11, "bold"))
        self.tree.tag_configure("group_artist", font=("", 12, "bold"))
        self.tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.tree.tag_configure("xfer_queued", background=BG_TRANSFER_QUEUED)
        self.tree.tag_configure("xfer_transcoding", background=BG_TRANSFER_TRANSCODING)
        self.tree.tag_configure(
            "xfer_transferring", background=BG_TRANSFER_TRANSFERRING
        )
        self.tree.tag_configure(
            "playing", background=BG_PLAYING, foreground=FG_PLAYING
        )
        self.videos_tree.tag_configure("group", font=("", 11, "bold"))
        self.videos_tree.tag_configure("group_directory", font=("", 12, "bold"))
        self.videos_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.videos_tree.tag_configure("xfer_queued", background=BG_TRANSFER_QUEUED)
        self.videos_tree.tag_configure(
            "xfer_transcoding", background=BG_TRANSFER_TRANSCODING
        )
        self.videos_tree.tag_configure(
            "xfer_transferring", background=BG_TRANSFER_TRANSFERRING
        )
        self.videos_tree.tag_configure(
            "playing", background=BG_PLAYING, foreground=FG_PLAYING
        )
        self.audiobooks_tree.tag_configure("group", font=("", 11, "bold"))
        self.audiobooks_tree.tag_configure("group_artist", font=("", 12, "bold"))
        self.audiobooks_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.audiobooks_tree.tag_configure("xfer_queued", background=BG_TRANSFER_QUEUED)
        self.audiobooks_tree.tag_configure(
            "xfer_transcoding", background=BG_TRANSFER_TRANSCODING
        )
        self.audiobooks_tree.tag_configure(
            "xfer_transferring", background=BG_TRANSFER_TRANSFERRING
        )
        self.audiobooks_tree.tag_configure(
            "playing", background=BG_PLAYING, foreground=FG_PLAYING
        )
        self.device_tree.tag_configure("group", font=("", 11, "bold"))
        self.device_tree.tag_configure("group_artist", font=("", 12, "bold"))
        self.device_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.device_video_tree.tag_configure("group", font=("", 11, "bold"))
        self.device_video_tree.tag_configure("group_folder", font=("", 12, "bold"))
        self.device_video_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.device_audiobooks_tree.tag_configure("group", font=("", 11, "bold"))
        self.device_audiobooks_tree.tag_configure(
            "group_artist", font=("", 12, "bold")
        )
        self.device_audiobooks_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)

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

    def set_context_detail(
        self, text: str, *, path: str | None = None
    ) -> None:
        """Update the context subframe (selection metadata).

        Replaces the first-run experimental hint. Always updates the visible
        label (including under Stable Mode) so selection still has a home.
        *path* is the full host path for a single track (shown italic below).
        """
        self._startup_hint_active = False
        self._context_detail = text or ""
        self._context_path = (path or "").strip()
        try:
            self.lbl_context_detail.configure(text=self._context_detail)
        except Exception:
            pass
        try:
            self.lbl_context_path.configure(text=self._context_path)
        except Exception:
            pass

    def set_library_menu_commands(
        self,
        *,
        on_manage_library,
        on_manage_playlists=None,
        on_select_root=None,
        on_update=None,
    ) -> None:
        """Wire Library menu entries (called once from the controller).

        *on_manage_library* opens the roots manager (add/remove/update).
        *on_manage_playlists* focuses the Playlists notebook tab.
        *on_select_root* / *on_update* are ignored legacy kwargs.
        """
        del on_select_root, on_update
        self.menu_library.entryconfig(
            MENU_MANAGE_LIBRARY, command=on_manage_library
        )
        if on_manage_playlists is not None:
            self.menu_library.entryconfig(
                MENU_MANAGE_PLAYLISTS, command=on_manage_playlists
            )

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

    def set_view_menu_commands(self, *, on_always_show_playback_toggle=None) -> None:
        if on_always_show_playback_toggle is not None:
            self.menu_view.entryconfig(
                MENU_ALWAYS_SHOW_PLAYBACK,
                command=on_always_show_playback_toggle,
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
        on_play_track=None,
        on_play_artist_group=None,
        on_play_album_group=None,
        on_add_to_playlist=None,
        on_add_artist_to_playlist=None,
        on_add_album_to_playlist=None,
        on_exclude_file=None,
        on_exclude_folder=None,
        on_exclude_group_folder=None,
    ) -> None:
        if on_sync_selected is not None:
            self.menu_track_ctx.entryconfig(
                CTX_SYNC_SELECTED, command=on_sync_selected
            )
        self.menu_track_ctx.entryconfig(CTX_SYNC_TRACK, command=on_sync_track)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ALBUM, command=on_sync_album)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ARTIST, command=on_sync_artist)
        if on_play_track is not None:
            # Index (not label): label toggles Play This / These Tracks.
            self.menu_track_ctx.entryconfig(6, command=on_play_track)
        if on_add_to_playlist is not None:
            # Index 7: Add to playlist (label toggles This/These).
            self.menu_track_ctx.entryconfig(7, command=on_add_to_playlist)
        if on_exclude_file is not None:
            self.menu_track_ctx.entryconfig(
                CTX_EXCLUDE_FILE, command=on_exclude_file
            )
        if on_exclude_folder is not None:
            self.menu_track_ctx.entryconfig(
                CTX_EXCLUDE_FOLDER, command=on_exclude_folder
            )
        self.menu_artist_ctx.entryconfig(0, command=on_sync_artist_group)
        if on_play_artist_group is not None:
            # Index 2 (label changes with artist name).
            self.menu_artist_ctx.entryconfig(2, command=on_play_artist_group)
        if on_add_artist_to_playlist is not None:
            self.menu_artist_ctx.entryconfig(3, command=on_add_artist_to_playlist)
        self.menu_album_ctx.entryconfig(0, command=on_sync_album_group)
        if on_play_album_group is not None:
            # Index 2 (label changes with album/folder name).
            self.menu_album_ctx.entryconfig(2, command=on_play_album_group)
        if on_add_album_to_playlist is not None:
            self.menu_album_ctx.entryconfig(3, command=on_add_album_to_playlist)
        if on_exclude_group_folder is not None:
            # Index 5 after Play + Add + separator.
            self.menu_album_ctx.entryconfig(5, command=on_exclude_group_folder)

    def set_playlist_tab_commands(
        self,
        *,
        on_combo_selected=None,
        on_new=None,
        on_delete=None,
        on_rename=None,
        on_sync=None,
        on_remove_tracks=None,
        on_play_track=None,
    ) -> None:
        """Wire Playlists tab toolbar + context menu."""
        if on_combo_selected is not None:
            self.playlist_combo.bind(
                "<<ComboboxSelected>>", lambda _e: on_combo_selected()
            )
        if on_new is not None:
            self.btn_playlist_new.configure(command=on_new)
        if on_delete is not None:
            self.btn_playlist_delete.configure(command=on_delete)
        if on_rename is not None:
            self.btn_playlist_rename.configure(command=on_rename)
        if on_sync is not None:
            self.btn_playlist_sync.configure(command=on_sync)
            self.menu_playlist_ctx.entryconfig(
                CTX_PLAYLIST_SYNC, command=on_sync
            )
        if on_remove_tracks is not None:
            self.menu_playlist_ctx.entryconfig(
                CTX_PLAYLIST_REMOVE, command=on_remove_tracks
            )
        if on_play_track is not None:
            self.menu_playlist_ctx.entryconfig(
                CTX_PLAYLIST_PLAY_TRACK, command=on_play_track
            )

    def show_playlists_tab(self) -> None:
        """Select the Playlists notebook tab."""
        try:
            self.media_notebook.select(self.playlists_tab)
        except Exception:
            pass

    def set_playlist_combo_values(
        self,
        names: list[str],
        *,
        selected: str = "",
    ) -> None:
        """Refresh playlist dropdown options and selection."""
        values = list(names or [])
        if not values:
            self.playlist_combo.configure(values=[], state="disabled")
            self.var_playlist_choice.set("")
            self.btn_playlist_rename.configure(state=DISABLED)
            self.btn_playlist_delete.configure(state=DISABLED)
            self.btn_playlist_sync.configure(state=DISABLED)
            return
        self.playlist_combo.configure(values=values, state="readonly")
        pick = selected if selected in values else values[0]
        self.var_playlist_choice.set(pick)
        self.btn_playlist_rename.configure(state=NORMAL)
        self.btn_playlist_delete.configure(state=NORMAL)
        self.btn_playlist_sync.configure(state=NORMAL)

    def clear_playlist_tree(self) -> None:
        tree = self.playlist_tree
        for iid in tree.get_children(""):
            tree.delete(iid)

    def popup_playlist_context(self, event) -> str | None:
        """Context menu for the Playlists tab tree."""
        menu = self.menu_playlist_ctx
        try:
            tree = self.playlist_tree
            row = tree.identify_row(event.y)
            if row:
                current = tree.selection()
                if row not in current:
                    tree.selection_set(row)
                tree.focus(row)
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def set_playback_commands(
        self,
        *,
        on_play_pause=None,
        on_prev=None,
        on_next=None,
        on_close=None,
        on_seek=None,
    ) -> None:
        """Wire bottom-frame playback control callbacks."""
        self._on_playback_play_pause = on_play_pause
        self._on_playback_prev = on_prev
        self._on_playback_next = on_next
        self._on_playback_close = on_close
        self._on_playback_seek = on_seek
        if on_play_pause is not None:
            self.btn_playback_play.configure(command=on_play_pause)
        if on_prev is not None:
            self.btn_playback_prev.configure(command=on_prev)
        if on_next is not None:
            self.btn_playback_next.configure(command=on_next)
        if on_close is not None:
            self.btn_playback_close.configure(command=on_close)

    @staticmethod
    def _format_playback_time(seconds: float) -> str:
        sec = max(0, int(seconds or 0))
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _on_scrub_press(self, _event=None) -> None:
        self._scrub_dragging = True

    def _on_scrub_command(self, _value=None) -> None:
        # Live time label while dragging; commit seek on release.
        if self._scrub_programmatic or not self._scrub_dragging:
            return
        # Duration is stored on the scale's last update via update_playback_state.
        duration = float(getattr(self, "_playback_duration", 0.0) or 0.0)
        if duration <= 0:
            return
        frac = float(self.var_playback_scrub.get()) / 1000.0
        pos = max(0.0, min(duration, frac * duration))
        self.lbl_playback_time.configure(
            text=(
                f"{self._format_playback_time(pos)} / "
                f"{self._format_playback_time(duration)}"
            )
        )

    def _on_scrub_release(self, _event=None) -> None:
        if not self._scrub_dragging:
            return
        self._scrub_dragging = False
        duration = float(getattr(self, "_playback_duration", 0.0) or 0.0)
        if duration <= 0 or self._on_playback_seek is None:
            return
        frac = float(self.var_playback_scrub.get()) / 1000.0
        pos = max(0.0, min(duration, frac * duration))
        try:
            self._on_playback_seek(pos)
        except Exception:
            pass

    def set_playback_always_show(self, always: bool) -> None:
        """Persist View → Always show playback controls into the bar layout."""
        self._playback_always_show = bool(always)
        # Close is only useful when the bar can auto-hide.
        if self._playback_always_show:
            try:
                self.btn_playback_close.pack_forget()
            except Exception:
                pass
        else:
            try:
                self.btn_playback_close.pack(side=RIGHT, padx=(2, 4), pady=4)
            except Exception:
                pass
        self._refresh_playback_row_visibility(force=True)

    def set_playback_active(self, active: bool) -> None:
        """Show/hide the playback bar based on activity + always-show pref."""
        self._playback_session_active = bool(active)
        self._refresh_playback_row_visibility()

    def _refresh_playback_row_visibility(self, *, force: bool = False) -> None:
        show = bool(
            getattr(self, "_playback_always_show", False)
            or getattr(self, "_playback_session_active", False)
        )
        if show == self._playback_row_visible and not force:
            return
        if show:
            # Above status/progress so controls stay readable during jobs.
            self.playback_row.pack(
                side=TOP, fill=X, padx=2, pady=(4, 0), before=self.lbl_progress_status
            )
            self._playback_row_visible = True
            # Resume marquee if a long title is already set.
            if (
                len(self._playback_title_full or "") > _PLAYBACK_TITLE_WIDTH
                and self._playback_marquee_after_id is None
            ):
                self._schedule_playback_marquee()
        else:
            try:
                self.playback_row.pack_forget()
            except Exception:
                pass
            self._playback_row_visible = False
            self._cancel_playback_marquee()

    @staticmethod
    def marquee_window(
        text: str,
        offset: int,
        width: int = _PLAYBACK_TITLE_WIDTH,
        *,
        gap: str = _PLAYBACK_MARQUEE_GAP,
    ) -> str:
        """Return a *width*-char slice of *text* starting at *offset* (wraps).

        When *text* fits in *width*, returns the full string (no padding).
        Long titles use *gap* between end and start so the loop reads cleanly.
        """
        full = text or ""
        if width <= 0:
            return ""
        if len(full) <= width:
            return full
        cycle = full + (gap or "")
        if not cycle:
            return ""
        n = len(cycle)
        start = int(offset) % n
        doubled = cycle + cycle
        return doubled[start : start + width]

    def _cancel_playback_marquee(self) -> None:
        aid = self._playback_marquee_after_id
        self._playback_marquee_after_id = None
        if aid is not None:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass

    def _apply_playback_title_slice(self) -> None:
        """Paint the current marquee window onto the title label."""
        visible = self.marquee_window(
            self._playback_title_full,
            self._playback_title_offset,
            _PLAYBACK_TITLE_WIDTH,
        )
        try:
            self.lbl_playback_title.configure(text=visible)
        except Exception:
            pass

    def _schedule_playback_marquee(self) -> None:
        """Advance long titles by 1 character every 250ms."""
        self._cancel_playback_marquee()
        full = self._playback_title_full or ""
        if len(full) <= _PLAYBACK_TITLE_WIDTH:
            return
        self._playback_marquee_after_id = self.root.after(
            _PLAYBACK_MARQUEE_MS, self._on_playback_marquee_tick
        )

    def _on_playback_marquee_tick(self) -> None:
        self._playback_marquee_after_id = None
        full = self._playback_title_full or ""
        if len(full) <= _PLAYBACK_TITLE_WIDTH:
            self._apply_playback_title_slice()
            return
        cycle_len = len(full) + len(_PLAYBACK_MARQUEE_GAP)
        if cycle_len <= 0:
            return
        self._playback_title_offset = (
            self._playback_title_offset + 1
        ) % cycle_len
        self._apply_playback_title_slice()
        self._schedule_playback_marquee()

    def set_playback_title(self, title: str) -> None:
        """Set the full playback title; start marquee when it overflows."""
        text = title or ""
        if text == self._playback_title_full:
            # Same track — keep offset; ensure timer is running if needed.
            if (
                len(text) > _PLAYBACK_TITLE_WIDTH
                and self._playback_marquee_after_id is None
            ):
                self._schedule_playback_marquee()
            return
        self._playback_title_full = text
        self._playback_title_offset = 0
        self._cancel_playback_marquee()
        self._apply_playback_title_slice()
        self._schedule_playback_marquee()

    def update_playback_state(
        self,
        *,
        title: str = "",
        position_sec: float = 0.0,
        duration_sec: float = 0.0,
        playing: bool = False,
        paused: bool = False,
        show_nav: bool = False,
        enabled: bool = False,
    ) -> None:
        """Refresh labels, scrubber, and button enablement."""
        self._playback_duration = max(0.0, float(duration_sec or 0.0))
        self._playback_show_nav = bool(show_nav)

        self.set_playback_title(title or "")
        self.lbl_playback_time.configure(
            text=(
                f"{self._format_playback_time(position_sec)} / "
                f"{self._format_playback_time(self._playback_duration)}"
            )
        )

        # Prev/Next only when a multi-track queue is loaded.
        nav_state = NORMAL if (enabled and show_nav) else DISABLED
        self.btn_playback_prev.configure(state=nav_state)
        self.btn_playback_next.configure(state=nav_state)
        if show_nav:
            try:
                self.btn_playback_prev.pack(
                    side=LEFT, padx=(4, 2), pady=4, before=self.btn_playback_play
                )
                self.btn_playback_next.pack(
                    side=LEFT, padx=2, pady=4, after=self.btn_playback_play
                )
            except Exception:
                pass
        else:
            try:
                self.btn_playback_prev.pack_forget()
                self.btn_playback_next.pack_forget()
            except Exception:
                pass

        if playing and not paused:
            self.btn_playback_play.configure(
                text="Pause", state=NORMAL if enabled else DISABLED
            )
        else:
            self.btn_playback_play.configure(
                text="Play", state=NORMAL if enabled else DISABLED
            )

        scrub_state = NORMAL if enabled else DISABLED
        try:
            self.playback_scrub.configure(state=scrub_state)
        except Exception:
            pass

        if not self._scrub_dragging:
            if self._playback_duration > 0:
                frac = max(
                    0.0,
                    min(1.0, float(position_sec) / self._playback_duration),
                )
            else:
                frac = 0.0
            self._scrub_programmatic = True
            try:
                self.var_playback_scrub.set(frac * 1000.0)
            finally:
                self._scrub_programmatic = False

    def set_playing_row(self, iid: str | None) -> None:
        """Highlight the currently playing track row (deep dark purple)."""
        prev = self._playing_iid
        if prev and prev != iid:
            self._clear_playing_tag(prev)
        self._playing_iid = iid
        if iid:
            self._set_playing_tag(iid)

    def _set_playing_tag(self, iid: str) -> None:
        for tree in self.library_media_trees():
            if not tree.exists(iid):
                continue
            tags = list(tree.item(iid, "tags") or ())
            if "playing" not in tags:
                tags.append("playing")
                tree.item(iid, tags=tags)
            return

    def _clear_playing_tag(self, iid: str) -> None:
        for tree in self.library_media_trees():
            if not tree.exists(iid):
                continue
            tags = [t for t in (tree.item(iid, "tags") or ()) if t != "playing"]
            tree.item(iid, tags=tags)
            return

    def clear_playing_styles(self) -> None:
        """Remove playing highlight from all library trees."""
        self._playing_iid = None
        for tree in self.library_media_trees():
            for iid in self._all_iids(tree):
                tags = [
                    t for t in (tree.item(iid, "tags") or ()) if t != "playing"
                ]
                tree.item(iid, tags=tags)

    def set_library_menu_state(
        self,
        *,
        manage_enabled: bool = True,
        update_enabled: bool | None = None,
        select_enabled: bool | None = None,
    ) -> None:
        """Enable/disable Library → Manage Library….

        *update_enabled* / *select_enabled* are legacy aliases: the menu stays
        enabled when either would have been true (roots manager is always
        useful to add a first root).
        """
        if update_enabled is not None or select_enabled is not None:
            # Legacy dual-flag call sites: keep the manager openable whenever
            # select was allowed; update-only disable no longer hides the menu.
            legacy_select = True if select_enabled is None else bool(select_enabled)
            manage_enabled = legacy_select and manage_enabled
        self.menu_library.entryconfig(
            MENU_MANAGE_LIBRARY,
            state=NORMAL if manage_enabled else DISABLED,
        )

    def set_library_status(
        self,
        root_path: str = "",
        track_count: int = 0,
        *,
        root_paths: list[str] | None = None,
        root_reachable: bool = True,
        busy_message: str | None = None,
    ) -> None:
        """Update toolbar path label and track count.

        When multiple *root_paths* are present, the label shows
        ``Multiple Library Roots`` and the hover tip lists every root.
        A single root shows an elided path (full path on hover).

        When *busy_message* is set (e.g. during a background scan), the count
        label shows that status instead of a numeric track total.
        """
        if root_paths is not None:
            paths = [p for p in root_paths if p]
        elif root_path:
            paths = [root_path]
        else:
            paths = []

        if len(paths) > 1:
            display = "Multiple Library Roots"
            if not root_reachable:
                display = f"(unreachable) {display}"
            self.lbl_library_path.configure(text=display)
            self._library_path_tip.set_text("\n".join(paths))
        elif len(paths) == 1:
            display = _elide_path(paths[0])
            if not root_reachable:
                display = f"(unreachable) {display}"
            self.lbl_library_path.configure(text=display)
            self._library_path_tip.set_text(paths[0])
        else:
            self.lbl_library_path.configure(text="No library selected")
            self._library_path_tip.set_text("")

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

    def clear_videos_tree(self) -> None:
        """Clear Library → Video tree."""
        try:
            self.videos_tree.delete(*self.videos_tree.get_children())
        except Exception:
            pass

    def clear_audiobooks_tree(self) -> None:
        """Clear Library → Audiobooks tree."""
        try:
            self.audiobooks_tree.delete(*self.audiobooks_tree.get_children())
        except Exception:
            pass

    def clear_device_track_tree(self) -> None:
        """Clear Device → Music tree (album-art cache shared with library)."""
        try:
            self.device_tree.delete(*self.device_tree.get_children())
        except Exception:
            pass

    def clear_device_video_tree(self) -> None:
        """Clear Device → Video tree."""
        try:
            self.device_video_tree.delete(*self.device_video_tree.get_children())
        except Exception:
            pass

    def clear_device_audiobooks_tree(self) -> None:
        """Clear Device → Audiobooks tree."""
        try:
            self.device_audiobooks_tree.delete(
                *self.device_audiobooks_tree.get_children()
            )
        except Exception:
            pass

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
        trees = (self.tree, self.videos_tree, self.audiobooks_tree)
        if usable:
            for tree in trees:
                tree.configure(selectmode="extended")
                # Drop dead tag from all items
                for iid in self._all_iids(tree):
                    tags = [t for t in tree.item(iid, "tags") if t != "dead"]
                    tree.item(iid, tags=tags)
            return
        for tree in trees:
            tree.configure(selectmode="none")
            for iid in self._all_iids(tree):
                tags = list(tree.item(iid, "tags"))
                if "dead" not in tags:
                    tags.append("dead")
                tree.item(iid, tags=tags)

    def active_library_tree(self):
        """Treeview for the selected library media tab (Music / Video / Audiobooks)."""
        try:
            current = self.media_notebook.select()
        except Exception:
            return self.tree
        try:
            if current == str(self.videoLibrary_tab):
                return self.videos_tree
            if current == str(self.audiobooksLibrary_tab):
                return self.audiobooks_tree
        except Exception:
            pass
        return self.tree

    def library_media_trees(self):
        """All host library Treeviews (Music, Video, Audiobooks)."""
        return (self.tree, self.videos_tree, self.audiobooks_tree)

    def device_media_trees(self):
        """All on-device Treeviews (Music, Video, Audiobooks)."""
        return (
            self.device_tree,
            self.device_video_tree,
            self.device_audiobooks_tree,
        )

    def active_device_tree(self):
        """Treeview for the selected device media tab."""
        try:
            current = self.device_notebook.select()
        except Exception:
            return self.device_tree
        try:
            if current == str(self.device_video_tab):
                return self.device_video_tree
            if current == str(self.device_audiobooks_tab):
                return self.device_audiobooks_tree
        except Exception:
            pass
        return self.device_tree

    def _all_iids(self, tree=None) -> list[str]:
        tree = tree if tree is not None else self.tree
        out: list[str] = []

        def walk(parent: str) -> None:
            for child in tree.get_children(parent):
                out.append(child)
                walk(child)

        walk("")
        return out

    def set_track_transfer_style(self, iid: str, status: str | None) -> None:
        """Tint a track row for transfer state via tags."""
        tree = None
        for candidate in (*self.library_media_trees(), self.playlist_tree):
            try:
                if candidate.exists(iid):
                    tree = candidate
                    break
            except Exception:
                continue
        if tree is None:
            return
        tags = [
            t
            for t in tree.item(iid, "tags")
            if not str(t).startswith("xfer_")
        ]
        # Prefer transfer tint over playing highlight while a job is active;
        # re-apply playing when transfer style is cleared.
        if status in (None, "done", "failed", "skipped", ""):
            if self._playing_iid == iid and "playing" not in tags:
                tags.append("playing")
            tree.item(iid, tags=tags)
            return
        tags = [t for t in tags if t != "playing"]
        if status == "transferring":
            tags.append("xfer_transferring")
        elif status == "transcoding":
            tags.append("xfer_transcoding")
        else:
            tags.append("xfer_queued")
        tree.item(iid, tags=tags)

    def clear_transfer_styles(self) -> None:
        """Clear all transfer tint tags from library trees."""
        for tree in self.library_media_trees():
            for iid in self._all_iids(tree):
                tags = [
                    t
                    for t in tree.item(iid, "tags")
                    if not str(t).startswith("xfer_")
                ]
                if self._playing_iid == iid and "playing" not in tags:
                    tags.append("playing")
                tree.item(iid, tags=tags)

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
            # Prefer the widget that received the click (Music / Video / Audiobooks).
            tree = event.widget if event is not None else self.tree
            if tree not in self.library_media_trees():
                tree = self.active_library_tree()
            row = tree.identify_row(event.y)
            if not row:
                return "break"
            tags = set(tree.item(row, "tags"))
            # Preserve multi-select when right-clicking inside the selection.
            current = tree.selection()
            if row not in current:
                tree.selection_set(row)
            tree.focus(row)
            tree.see(row)

            if "track" in tags:
                menu = self.menu_track_ctx
            elif "group_artist" in tags:
                menu = self.menu_artist_ctx
            elif "group_directory" in tags or "group_album" in tags:
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

    def set_prepare_device_context_menu(self, handler) -> None:
        """Optional hook(tree, row_iid, tags) before a device context menu."""
        self._prepare_device_context_menu = handler

    def set_device_context_commands(
        self,
        *,
        on_delete=None,
        on_pull=None,
        on_pull_folder=None,
        on_delete_artist=None,
        on_delete_album=None,
        on_delete_folder=None,
        on_device_info=None,
        on_delete_all=None,
    ) -> None:
        """Wire Device tree / panel context menu commands."""
        if on_pull is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_PULL, command=on_pull
            )
        if on_pull_folder is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_PULL_FOLDER, command=on_pull_folder
            )
        if on_delete is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_DELETE, command=on_delete
            )
        if on_delete_artist is not None:
            self.menu_device_artist_ctx.entryconfig(
                0, command=on_delete_artist
            )
        if on_delete_album is not None:
            self.menu_device_album_ctx.entryconfig(
                0, command=on_delete_album
            )
        if on_delete_folder is not None:
            self.menu_device_folder_ctx.entryconfig(
                0, command=on_delete_folder
            )
        if on_device_info is not None:
            self.menu_device_panel_ctx.entryconfig(
                CTX_DEVICE_INFO, command=on_device_info
            )
        if on_delete_all is not None:
            self.menu_device_panel_ctx.entryconfig(
                CTX_DEVICE_DELETE_ALL, command=on_delete_all
            )

    def popup_device_context(self, event) -> str | None:
        """Show on-device media context menu for the row under the pointer."""
        menu = None
        try:
            if self._mode == "stable":
                return "break"
            tree = event.widget if event is not None else self.device_tree
            if tree not in self.device_media_trees():
                tree = self.active_device_tree()
            row = tree.identify_row(event.y)
            if not row:
                return "break"
            tags = set(tree.item(row, "tags"))
            current = tree.selection()
            if row not in current:
                tree.selection_set(row)
            tree.focus(row)
            tree.see(row)

            if "track" in tags:
                menu = self.menu_device_track_ctx
            elif "group_artist" in tags:
                menu = self.menu_device_artist_ctx
            elif "group_album" in tags:
                menu = self.menu_device_album_ctx
            elif "group_folder" in tags:
                menu = self.menu_device_folder_ctx
            else:
                return "break"

            if self._prepare_device_context_menu is not None:
                self._prepare_device_context_menu(tree, row, tags)

            menu.tk_popup(event.x_root, event.y_root)
        finally:
            if menu is not None:
                try:
                    menu.grab_release()
                except Exception:
                    pass
        return "break"

    def popup_device_panel_context(self, event) -> str | None:
        """Show Device Info / Delete All on the device panel or graphic."""
        menu = None
        try:
            if self._mode == "stable":
                return "break"
            menu = self.menu_device_panel_ctx
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            if menu is not None:
                try:
                    menu.grab_release()
                except Exception:
                    pass
        return "break"

    def selected_tree_iid(self) -> str | None:
        """Primary selected row (focus preferred, else first in selection)."""
        tree = self.active_library_tree()
        focus = tree.focus()
        if focus and tree.exists(focus):
            return focus
        sel = tree.selection()
        if not sel:
            return None
        return sel[0]

    def selected_tree_iids(self) -> list[str]:
        """All selected row iids (multi-select) from the active library tab."""
        return list(self.active_library_tree().selection())

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
