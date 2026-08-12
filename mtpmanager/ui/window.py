"""Tk layout only — widgets and packing.

Main-window chrome baseline (phase 1): flat frames + ttk interactive
controls via ``mtpmanager.ui.chrome``. See ``docs/ui-visual-pass.md``.
"""

from __future__ import annotations

import math
import sys
from typing import Callable, Literal

from pathlib import Path

from tkinter import (
    BOTH,
    BOTTOM,
    BooleanVar,
    DISABLED,
    DoubleVar,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    TOP,
    WORD,
    X,
    Y,
    Frame,
    Label,
    Menu,
    PhotoImage,
    StringVar,
    Text,
    Tk,
    Toplevel,
    ttk,
)

from mtpmanager.ui.chrome import (
    GLYPH_ADD,
    GLYPH_DISMISS,
    GLYPH_REMOVE,
    LABEL_MOVE_DOWN,
    LABEL_MOVE_UP,
    LABEL_REFRESH,
    STYLE_BTN_COMPACT,
    STYLE_BTN_TOOL,
    STYLE_TREE_COMPACT,
    STYLE_TREE_THUMB,
    apply_chrome_baseline,
    flat_frame,
    h_separator,
    reveal_in_file_manager_label,
    v_separator,
)

# Device tab category labels (phase 2: combobox strip, not nested Notebook).
_DEVICE_CATEGORY_LABELS = (
    "Music",
    "Video",
    "Audiobooks",
    "Podcasts",
    "Playlists",
)

Mode = Literal["stable", "experimental"]

# Full help for About Stable Mode… (dialog). Short caption stays in the panel.
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
# Phase 4a / O9: short left-panel caption (not the full help wall).
STABLE_MODE_CAPTION = (
    "Transfers use mtp-sendtr (one process per track). "
    "Device tools and auto-connect are off."
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
# Label is updated dynamically with today's day-playlist name.
MENU_FINISH_DAY_PODCAST_SYNC = "Finish Sync (day playlist)"
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
MENU_SYNC_ALBUM_ART = "Sync album art (PyMTP)"
MENU_ENABLE_EXPERIMENTAL_TOOLS = "Enable Experimental Tools"
MENU_ARTIST_FOLDERS = "Store tracks in artist folder (experimental)"
MENU_ALBUM_FOLDERS = "Store tracks in album folder (experimental)"
MENU_PODCAST_FOLDERS = "Store Podcasts in Identifiable Folders (experimental)"
MENU_ALLOW_VIDEO_PODCASTS = "Allow video podcasts to Sync (experimental)"
MENU_AUDIO_PODCASTS_AS_VIDEO = (
    "Sync Audio Podcasts as Video (experimental)"
)
MENU_KEEP_DOWNLOADED_PODCASTS = "Keep downloaded podcasts"
MENU_CLEAR_DOWNLOADED_PODCASTS = "Clear downloaded podcasts…"
MENU_REVEAL_PODCAST_DOWNLOADS = "Reveal podcast downloads folder"
MENU_PODCAST_SETTINGS = "Podcast Settings…"
MENU_AUDIOBOOK_ENCODE = "Audiobook Encode…"
MENU_CONFIG = "Config…"

# Video podcast episode row (teal / blue-green; Tk Treeview has no gradient outline).
BG_VIDEO_PODCAST = "#c5e8e6"

# Podcasts tab
CTX_PODCAST_SYNC_LATEST = "Sync Latest"
CTX_PODCAST_ENCODE = "Encode Settings…"
CTX_PODCAST_SPECIAL_SYNC = "Special Sync…"
CTX_PODCAST_EPISODE_SYNC = "Sync Episodes Now"
CTX_PODCAST_EPISODE_SPECIAL_SYNC = "Special Sync…"
CTX_PODCAST_PLAY_EPISODE = "Play This Episode"
CTX_PODCAST_PLAY_EPISODES = "Play These Episodes"
CTX_PODCAST_REVEAL_DOWNLOAD = reveal_in_file_manager_label(download=True)
# Labels updated dynamically with today's day-playlist name.
CTX_PODCAST_ADD_TO_DAY_PLAYLIST = "Add This Episode to Day Playlist"
CTX_PODCAST_REMOVE_FROM_DAY_PLAYLIST = "Remove This Episode from Day Playlist"

# Device menu (PyMTP / default)
MENU_CONNECT = "Connect"
MENU_DISCONNECT = "Disconnect"
MENU_DEVICE_INFO = "Device Info"
MENU_CREATE_FOLDER = "Create Folder…"
MENU_SEND_VIDEO = "Send Video…"
MENU_LIST_FOLDERS = "List Folders (experimental)"
MENU_LIST_FILES = "List Files (experimental)"
MENU_LIST_TRACKS = "List Tracks (experimental)"
MENU_GET_TRACKS_FROM_DEVICE = "Get Tracks from Device… (experimental)"
MENU_DELETE_TRACK = "Delete Track (experimental)"
MENU_GET_FILE_INFO = "Get File Info (experimental)"
MENU_GET_TRACK_INFO = "Get Track Info (experimental)"
MENU_DELETE_ALL = "Delete All Tracks… (experimental)"
MENU_REFRESH_DEVICE_INDEX = "Refresh Device Index…"

# Track context menu
CTX_SYNC_SELECTED = "Sync selected tracks"
CTX_SYNC_TRACK = "Sync this track"
CTX_SYNC_ALBUM = "Sync Album"
CTX_SYNC_ARTIST = "Sync all from Artist"
CTX_SPECIAL_SYNC = "Special Sync…"
CTX_PLAY_TRACK = "Play This Track"
CTX_PLAY_TRACKS = "Play These Tracks"
CTX_ADD_TO_PLAYLIST = "Add This Track to Playlist…"
CTX_ADD_TRACKS_TO_PLAYLIST = "Add These Tracks to Playlist…"
CTX_EXCLUDE_FILE = "Exclude this file…"
CTX_EXCLUDE_FOLDER = "Exclude this folder…"

# Group header context menus (labels updated dynamically before popup)
CTX_SYNC_ARTIST_GROUP = "Sync all from Artist"
CTX_SYNC_ALBUM_GROUP = "Sync album"
CTX_SPECIAL_SYNC_GROUP = "Special Sync…"
CTX_PLAY_ARTIST_GROUP = "Play All from Artist"
CTX_PLAY_ALBUM_GROUP = "Play Album"
CTX_ADD_ARTIST_TO_PLAYLIST = "Add All from Artist to Playlist…"
CTX_ADD_ALBUM_TO_PLAYLIST = "Add Album to Playlist…"
CTX_EXCLUDE_GROUP_FOLDER = "Exclude this folder…"

# Playlists tab context menu
CTX_PLAYLIST_REMOVE = "Remove from Playlist"
# Linux: Alt+arrows; macOS: Option (⌥)+arrows — both bound (see _bind_playlist_reorder_keys).
if sys.platform == "darwin":
    CTX_PLAYLIST_MOVE_UP = "Move Up (⌥↑)"
    CTX_PLAYLIST_MOVE_DOWN = "Move Down (⌥↓)"
else:
    CTX_PLAYLIST_MOVE_UP = "Move Up (Alt+↑)"
    CTX_PLAYLIST_MOVE_DOWN = "Move Down (Alt+↓)"
CTX_PLAYLIST_PLAY_TRACK = "Play This Track"
CTX_PLAYLIST_SHUFFLE_ARTIST = "Shuffle by Artist (Merge)…"
CTX_PLAYLIST_SHUFFLE_SPOTIFY = "Shuffle (Spotify-style)…"
CTX_PLAYLIST_SYNC = "Sync playlist to device"

# Device → Playlists tab (on-device MTP playlists; same chrome as host Playlists)
CTX_DEVICE_PLAYLIST_PLAY_TRACK = "Play This Track"
CTX_DEVICE_PLAYLIST_REMOVE = "Remove from Playlist"
CTX_DEVICE_PLAYLIST_MOVE_UP = CTX_PLAYLIST_MOVE_UP
CTX_DEVICE_PLAYLIST_MOVE_DOWN = CTX_PLAYLIST_MOVE_DOWN
CTX_DEVICE_PLAYLIST_SHUFFLE_ARTIST = CTX_PLAYLIST_SHUFFLE_ARTIST
CTX_DEVICE_PLAYLIST_SHUFFLE_SPOTIFY = CTX_PLAYLIST_SHUFFLE_SPOTIFY
CTX_DEVICE_PLAYLIST_REFRESH = "Refresh from device"
CTX_DEVICE_PLAYLIST_RECREATE_LOCAL = "Recreate playlist locally…"


def _bind_playlist_reorder_keys(
    widget,
    *,
    on_up: Callable[[], None] | None,
    on_down: Callable[[], None] | None,
) -> None:
    """Bind Alt (Linux) / Option (macOS ⌥) + Up/Down for playlist reorder.

    Aqua Tk accepts both ``Alt`` and ``Option`` as names for the Option key, but
    which sequence actually fires varies by build — register every common form.
    Plain Up/Down still move the tree selection (not rebound here).
    """

    def _call(cb: Callable[[], None] | None):
        def _handler(_event=None):
            if cb is not None:
                cb()
            return "break"

        return _handler

    up_h = _call(on_up)
    down_h = _call(on_down)
    for seq, handler in (
        ("<Alt-Up>", up_h),
        ("<Option-Up>", up_h),
        ("<Alt-Key-Up>", up_h),
        ("<Option-Key-Up>", up_h),
        ("<Alt-Down>", down_h),
        ("<Option-Down>", down_h),
        ("<Alt-Key-Down>", down_h),
        ("<Option-Key-Down>", down_h),
    ):
        try:
            widget.bind(seq, handler)
        except Exception:
            pass

# Device media context menus (on-device Music / Video / Audiobooks trees)
CTX_DEVICE_DELETE = "Delete from device…"
CTX_DEVICE_PULL = "Pull to library…"
CTX_DEVICE_PULL_FOLDER = "Pull to folder…"
CTX_DEVICE_FETCH_TAGS = "Fetch track tags…"
CTX_DEVICE_TRACK_INFO = "Track Info…"
CTX_DEVICE_ADD_TO_PLAYLIST = "Add to Device Playlist…"
CTX_DEVICE_SHRINK = "Shrink…"
CTX_DEVICE_DELETE_ARTIST = "Delete all from Artist…"
CTX_DEVICE_DELETE_ALBUM = "Delete album from device…"
CTX_DEVICE_DELETE_FOLDER = "Delete all in folder…"
CTX_DEVICE_ADD_ARTIST_TO_PLAYLIST = "Add Artist to Device Playlist…"
CTX_DEVICE_ADD_ALBUM_TO_PLAYLIST = "Add Album to Device Playlist…"
CTX_DEVICE_ADD_FOLDER_TO_PLAYLIST = "Add Folder to Device Playlist…"
CTX_DEVICE_INFO = "Device Info"
CTX_DEVICE_DELETE_ALL = "Delete All Tracks…"

# Always shown under Device (when PyMTP mode is active).
_DEVICE_MENU_STANDARD = (
    MENU_CONNECT,
    MENU_DISCONNECT,
    MENU_DEVICE_INFO,
    MENU_CREATE_FOLDER,
    MENU_SEND_VIDEO,
    MENU_REFRESH_DEVICE_INDEX,
)

# Shown only when Config → Enable Experimental Tools is on.
_DEVICE_MENU_EXPERIMENTAL = (
    MENU_LIST_FOLDERS,
    MENU_LIST_FILES,
    MENU_LIST_TRACKS,
    MENU_GET_TRACKS_FROM_DEVICE,
    MENU_DELETE_TRACK,
    MENU_GET_FILE_INFO,
    MENU_GET_TRACK_INFO,
    MENU_DELETE_ALL,
)

def _device_menu_labels(*, experimental_tools: bool) -> tuple[str, ...]:
    """Device menu labels for the current experimental-tools setting."""
    if experimental_tools:
        return _DEVICE_MENU_STANDARD + _DEVICE_MENU_EXPERIMENTAL
    return _DEVICE_MENU_STANDARD


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


class _DeviceSubviewNotebook:
    """Notebook-like API over a single visible Device category frame (phase 2).

    Call sites use ``device_notebook.select()`` / ``select(frame)`` the same way
    as ``ttk.Notebook``. The UI is a combobox strip, not nested tabs.
    """

    def __init__(self, window: "MainWindow") -> None:
        self._win = window

    def select(self, tab_id=None):
        if tab_id is None:
            frame = getattr(self._win, "_device_subview_frame", None)
            return str(frame) if frame is not None else ""
        self._win.show_device_subview(tab_id)
        return None


class MainWindow:
    def __init__(self, root: Tk | None = None):
        self.root = root or Tk()
        self.root.title("MTP Manager")
        self.root.geometry("1000x600")
        # Flat chrome (phase 1): no sunken root well — see ui/chrome.py / D17.
        try:
            self.root.configure(borderwidth=0, highlightthickness=0)
        except Exception:
            pass
        self._style = apply_chrome_baseline(self.root)

        # Menubar: Library | Transfer | Device | View | Config
        self.menubar = Menu(self.root)
        self.root.config(menu=self.menubar)

        self.menu_library = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Library", menu=self.menu_library)
        self.menu_library.add_command(label=MENU_MANAGE_LIBRARY)
        self.menu_library.add_command(label=MENU_MANAGE_PLAYLISTS)
        self.menu_library.add_separator()
        self.menu_library.add_command(
            label=MENU_FINISH_DAY_PODCAST_SYNC, state=DISABLED
        )
        self._finish_day_podcast_menu_label = MENU_FINISH_DAY_PODCAST_SYNC

        self.menu_transfer = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Transfer", menu=self.menu_transfer)
        # Built by set_experimental_tools_enabled / _rebuild_transfer_menu.
        self._transfer_menu_commands: dict | None = None
        self._sync_selected_enabled = False
        self._sync_selected_count = 0
        self._resume_sync_enabled = False
        self._cancel_job_enabled = False

        self.menu_device = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Device", menu=self.menu_device)
        self._device_menu_commands: dict | None = None
        # Built by set_experimental_tools_enabled / _rebuild_device_menu.

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
        self.var_sync_album_art = BooleanVar(value=True)
        self.var_enable_experimental_tools = BooleanVar(value=False)
        self.var_artist_folders = BooleanVar(value=False)
        self.var_album_folders = BooleanVar(value=False)
        self.var_podcast_folders = BooleanVar(value=False)
        self.var_allow_video_podcasts = BooleanVar(value=False)
        self.var_audio_podcasts_as_video = BooleanVar(value=False)
        self.var_keep_downloaded_podcasts = BooleanVar(value=True)
        self._enable_experimental_tools = False
        self._config_menu_commands: dict | None = None
        self.menu_config = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Config", menu=self.menu_config)
        # Built by set_experimental_tools_enabled / _rebuild_config_menu.
        # Default: experimental tools off (simpler menus).
        self.set_experimental_tools_enabled(False)

        # Track / group / podcast context menus rebuilt when experimental tools
        # toggle (Special Sync is experimental-only). Handlers re-applied.
        self._track_ctx_cmds: dict = {}
        self._podcast_ctx_cmds: dict = {}
        self.menu_track_ctx = Menu(self.root, tearoff=0)
        self.menu_artist_ctx = Menu(self.root, tearoff=0)
        self.menu_album_ctx = Menu(self.root, tearoff=0)
        self.menu_podcast_show_ctx = Menu(self.root, tearoff=0)
        self.menu_podcast_episode_ctx = Menu(self.root, tearoff=0)
        self._rebuild_library_context_menus()
        self._rebuild_podcast_context_menus()

        self.menu_playlist_ctx = Menu(self.root, tearoff=0)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_PLAY_TRACK)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_REMOVE)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_MOVE_UP)
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_MOVE_DOWN)
        self.menu_playlist_shuffle = Menu(self.menu_playlist_ctx, tearoff=0)
        self.menu_playlist_shuffle.add_command(label=CTX_PLAYLIST_SHUFFLE_ARTIST)
        self.menu_playlist_shuffle.add_command(label=CTX_PLAYLIST_SHUFFLE_SPOTIFY)
        self.menu_playlist_ctx.add_cascade(
            label="Shuffle playlist…", menu=self.menu_playlist_shuffle
        )
        self.menu_playlist_ctx.add_separator()
        self.menu_playlist_ctx.add_command(label=CTX_PLAYLIST_SYNC)

        self.menu_device_playlist_ctx = Menu(self.root, tearoff=0)
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_PLAY_TRACK
        )
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_REMOVE
        )
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_MOVE_UP
        )
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_MOVE_DOWN
        )
        self.menu_device_playlist_shuffle = Menu(
            self.menu_device_playlist_ctx, tearoff=0
        )
        self.menu_device_playlist_shuffle.add_command(
            label=CTX_DEVICE_PLAYLIST_SHUFFLE_ARTIST
        )
        self.menu_device_playlist_shuffle.add_command(
            label=CTX_DEVICE_PLAYLIST_SHUFFLE_SPOTIFY
        )
        self.menu_device_playlist_ctx.add_cascade(
            label="Shuffle playlist…", menu=self.menu_device_playlist_shuffle
        )
        self.menu_device_playlist_ctx.add_separator()
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_RECREATE_LOCAL
        )
        self.menu_device_playlist_ctx.add_command(
            label=CTX_DEVICE_PLAYLIST_REFRESH
        )

        # Device on-media context menus (delete / pull / on-demand tags).
        self.menu_device_track_ctx = Menu(self.root, tearoff=0)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_PULL)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_PULL_FOLDER)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_FETCH_TAGS)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_TRACK_INFO)
        self.menu_device_track_ctx.add_separator()
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_ADD_TO_PLAYLIST)
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_SHRINK)
        self.menu_device_track_ctx.add_separator()
        self.menu_device_track_ctx.add_command(label=CTX_DEVICE_DELETE)

        self.menu_device_artist_ctx = Menu(self.root, tearoff=0)
        self.menu_device_artist_ctx.add_command(
            label=CTX_DEVICE_ADD_ARTIST_TO_PLAYLIST
        )
        self.menu_device_artist_ctx.add_command(label=CTX_DEVICE_SHRINK)
        self.menu_device_artist_ctx.add_separator()
        self.menu_device_artist_ctx.add_command(label=CTX_DEVICE_DELETE_ARTIST)

        self.menu_device_album_ctx = Menu(self.root, tearoff=0)
        self.menu_device_album_ctx.add_command(
            label=CTX_DEVICE_ADD_ALBUM_TO_PLAYLIST
        )
        self.menu_device_album_ctx.add_command(label=CTX_DEVICE_SHRINK)
        self.menu_device_album_ctx.add_separator()
        self.menu_device_album_ctx.add_command(label=CTX_DEVICE_DELETE_ALBUM)

        self.menu_device_folder_ctx = Menu(self.root, tearoff=0)
        self.menu_device_folder_ctx.add_command(
            label=CTX_DEVICE_ADD_FOLDER_TO_PLAYLIST
        )
        self.menu_device_folder_ctx.add_separator()
        self.menu_device_folder_ctx.add_command(label=CTX_DEVICE_DELETE_FOLDER)

        # Device panel / graphic (same actions as Device menu).
        self.menu_device_panel_ctx = Menu(self.root, tearoff=0)
        self.menu_device_panel_ctx.add_command(label=CTX_DEVICE_INFO)
        self.menu_device_panel_ctx.add_separator()
        self.menu_device_panel_ctx.add_command(label=CTX_DEVICE_DELETE_ALL)

        self._prepare_device_context_menu = None

        # Status toolbar: path + fuzzy search + track count (flat strip).
        library_toolbar = flat_frame(self.root)
        library_toolbar.pack(side=TOP, fill=X, padx=4, pady=(4, 2))
        self.library_toolbar = library_toolbar

        Label(library_toolbar, text="Library:").pack(side=LEFT, padx=(6, 2), pady=4)

        self.lbl_library_path = Label(
            library_toolbar,
            text="No library selected",
            anchor="w",
        )
        self.lbl_library_path.pack(side=LEFT, fill=X, expand=True, padx=2, pady=4)
        self._library_path_tip = _HoverTip(self.lbl_library_path)

        Label(library_toolbar, text="Search:").pack(side=LEFT, padx=(8, 2), pady=4)
        self.var_library_search = StringVar(value="")
        self.entry_library_search = ttk.Entry(
            library_toolbar,
            textvariable=self.var_library_search,
            width=22,
        )
        self.entry_library_search.pack(side=LEFT, padx=(0, 2), pady=3)
        self._library_search_tip = _HoverTip(self.entry_library_search)
        self._library_search_tip.set_text(
            "Fuzzy search (flat ranked list).\n"
            "Field boosts: artist: album: title: genre: …\n"
            "Example: artist:nightwish ocean\n"
            "⌘F / Ctrl+F focus · Esc clear"
        )
        self.btn_library_search_clear = ttk.Button(
            library_toolbar,
            text=GLYPH_DISMISS,
            width=2,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_library_search_clear.pack(side=LEFT, padx=(0, 4), pady=2)
        self._on_library_search_change = None
        self._on_library_search_clear = None

        self.lbl_library_count = Label(library_toolbar, text="0 tracks")
        self.lbl_library_count.pack(side=LEFT, padx=(4, 8), pady=4)

        h_separator(self.root).pack(side=TOP, fill=X)

        # Pack bottom bar *before* the expanding body so it always keeps a
        # visible strip (Tk expand can otherwise starve a late BOTTOM pack).
        bottomframe = flat_frame(self.root)
        bottomframe.pack(side=BOTTOM, fill=X)
        self.bottomframe = bottomframe

        # Playback controls (hidden unless playing or View → always show).
        self.playback_row = flat_frame(bottomframe)
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

        self.btn_playback_prev = ttk.Button(
            self.playback_row,
            text="Prev",
            width=5,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_playback_play = ttk.Button(
            self.playback_row,
            text="Play",
            width=6,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_playback_next = ttk.Button(
            self.playback_row,
            text="Next",
            width=5,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
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
        self.btn_playback_close = ttk.Button(
            self.playback_row,
            text=GLYPH_DISMISS,
            width=3,
            style=STYLE_BTN_COMPACT,
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
        self.progress_row = flat_frame(bottomframe)
        self.progress_row.pack(side=TOP, fill=X, padx=4, pady=(2, 4))
        self.btn_cancel_job = ttk.Button(
            self.progress_row,
            text="Cancel",
            width=12,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        # Pack Cancel first on the right so the progress bar cannot cover it.
        self.btn_cancel_job.pack(side=RIGHT, padx=(8, 2), pady=2)
        self.progress = ttk.Progressbar(self.progress_row, length=200)
        self.progress.pack(side=LEFT, fill=X, expand=True, padx=(2, 0), pady=2)

        # Hairline above the bottom strip (after bottomframe so it sits above it).
        h_separator(self.root).pack(side=BOTTOM, fill=X)

        body = flat_frame(self.root)
        body.pack(side=TOP, fill=BOTH, expand=True)

        # Fixed-width left column: context (selection) + device subframes.
        leftframe = flat_frame(body, width=_LEFT_PANEL_WIDTH)
        leftframe.pack(side=LEFT, fill=Y)
        leftframe.pack_propagate(False)
        self.leftframe = leftframe

        v_separator(body).pack(side=LEFT, fill=Y)

        rightframe = flat_frame(body)
        rightframe.pack(side=RIGHT, fill=BOTH, expand=True)

        # --- Device subframe: fixed height, locked to bottom of leftframe ---
        # Pack BOTTOM first so Selection fills the remaining space above.
        self.device_panel = flat_frame(
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
        # Phase 4a / O9: About… for full Stable Mode help (not a text wall).
        self.btn_stable_mode_about = ttk.Button(
            self.device_panel,
            text="About Stable Mode…",
            style=STYLE_BTN_TOOL,
            command=self._show_stable_mode_about,
        )
        # Fixed-height slot so profile art cannot grow the device panel.
        self.device_graphic_slot = flat_frame(
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
        self.context_panel = flat_frame(leftframe)
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
        # Scrollable body: long podcast descriptions / multi-line selection text.
        self.context_body = flat_frame(self.context_panel)
        self.context_body.pack(
            side=TOP, fill=BOTH, expand=True, padx=4, pady=(4, 0)
        )
        self.context_scroll = ttk.Scrollbar(self.context_body)
        self.context_scroll.pack(side=RIGHT, fill=Y)
        self.txt_context_detail = Text(
            self.context_body,
            wrap=WORD,
            height=8,
            width=28,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            yscrollcommand=self.context_scroll.set,
            takefocus=0,
        )
        self.txt_context_detail.pack(side=LEFT, fill=BOTH, expand=True)
        self.context_scroll.config(command=self.txt_context_detail.yview)
        # Match panel background; keep a normal (non-bold) body font.
        try:
            bg = self.context_panel.cget("background")
            self.txt_context_detail.configure(background=bg, font=("", 11))
        except Exception:
            pass
        self.txt_context_detail.insert("1.0", EXPERIMENTAL_HINT)
        self.txt_context_detail.configure(state=DISABLED)
        # Mouse-wheel scroll when cursor is over the context body.
        self.txt_context_detail.bind(
            "<MouseWheel>", self._on_context_mousewheel, add="+"
        )
        self.txt_context_detail.bind(
            "<Button-4>", self._on_context_mousewheel, add="+"
        )
        self.txt_context_detail.bind(
            "<Button-5>", self._on_context_mousewheel, add="+"
        )
        # Back-compat aliases (some code may still reference label names).
        self.lbl_context_detail = self.txt_context_detail
        # Full host path / URL for single selection (italic, secondary).
        self.lbl_context_path = Label(
            self.context_panel,
            text="",
            wraplength=_LEFT_TEXT_WRAP,
            justify=LEFT,
            font=("", 10, "italic"),
        )
        self.lbl_context_path.pack(side=BOTTOM, fill=X, padx=6, pady=(2, 6), anchor="nw")

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

        # Podcasts tab (phase 2 / O2): P4-like toolbar + master–detail
        # (shows Treeview + episodes Treeview). Intentional exception to pure
        # P3 hierarchy — see docs/ui-visual-pass.md.
        # Full-sync schedule: Config → Podcast Settings…
        # TODO(follow-up): OPML import/export
        pod_outer = flat_frame(self.podcastsLibrary_tab)
        pod_outer.pack(fill=BOTH, expand=True, padx=4, pady=4)

        # Toolbar mirrors Playlists (P4): compact actions + status.
        pod_toolbar = flat_frame(pod_outer)
        pod_toolbar.pack(side=TOP, fill=X, pady=(0, 2))
        Label(pod_toolbar, text="Show:").pack(side=LEFT, padx=(2, 4))
        self.btn_podcast_add = ttk.Button(
            pod_toolbar, text=GLYPH_ADD, width=3, style=STYLE_BTN_COMPACT
        )
        self.btn_podcast_add.pack(side=LEFT, padx=2)
        self.btn_podcast_remove = ttk.Button(
            pod_toolbar,
            text=GLYPH_REMOVE,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_podcast_remove.pack(side=LEFT, padx=2)
        # Manual re-fetch of selected show feed(s) for new episodes.
        self.btn_podcast_refresh = ttk.Button(
            pod_toolbar,
            text=LABEL_REFRESH,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_podcast_refresh.pack(side=LEFT, padx=2)
        self.btn_podcast_sync_latest = ttk.Button(
            pod_toolbar,
            text="Sync Latest",
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_podcast_sync_latest.pack(side=LEFT, padx=(8, 2))
        self.btn_podcast_more = ttk.Button(
            pod_toolbar,
            text="More Episodes",
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_podcast_more.pack(side=LEFT, padx=2)
        self.lbl_podcast_status = Label(pod_toolbar, text="", anchor="w")
        self.lbl_podcast_status.pack(side=LEFT, fill=X, expand=True, padx=6)

        # Master: subscriptions (compact Treeview — not Listbox).
        pod_sub_header = flat_frame(pod_outer)
        pod_sub_header.pack(side=TOP, fill=X, pady=(4, 2))
        Label(
            pod_sub_header, text="Subscriptions", font=("", 11, "bold"), anchor="w"
        ).pack(side=LEFT)
        pod_sub_frame = flat_frame(pod_outer)
        pod_sub_frame.pack(side=TOP, fill=BOTH, expand=False)
        pod_sub_scroll = ttk.Scrollbar(pod_sub_frame)
        pod_sub_scroll.pack(side=RIGHT, fill=Y)
        self.podcast_show_tree = ttk.Treeview(
            pod_sub_frame,
            columns=("show",),
            show="headings",
            selectmode="extended",
            height=8,
            style=STYLE_TREE_COMPACT,
            yscrollcommand=pod_sub_scroll.set,
        )
        self.podcast_show_tree.pack(side=LEFT, fill=BOTH, expand=True)
        pod_sub_scroll.config(command=self.podcast_show_tree.yview)
        self.podcast_show_tree.heading("show", text="Show", anchor="w")
        self.podcast_show_tree.column("show", width=400, minwidth=120, stretch=True)
        # Back-compat alias (older call sites / mental model: "list").
        self.podcast_show_list = self.podcast_show_tree

        # Detail: episodes for the selected show(s).
        pod_ep_header = flat_frame(pod_outer)
        pod_ep_header.pack(side=TOP, fill=X, pady=(8, 2))
        self.lbl_podcast_episodes = Label(
            pod_ep_header, text="Episodes", font=("", 11, "bold"), anchor="w"
        )
        self.lbl_podcast_episodes.pack(side=LEFT, fill=X, expand=True)

        pod_ep_frame = flat_frame(pod_outer)
        pod_ep_frame.pack(side=TOP, fill=BOTH, expand=True)
        pod_ep_yscroll = ttk.Scrollbar(pod_ep_frame)
        pod_ep_yscroll.pack(side=RIGHT, fill=Y)
        pod_ep_xscroll = ttk.Scrollbar(pod_ep_frame, orient="horizontal")
        pod_ep_xscroll.pack(side=BOTTOM, fill=X)
        self.podcast_episode_tree = ttk.Treeview(
            pod_ep_frame,
            columns=("date", "title", "duration", "status"),
            show="headings",
            selectmode="extended",
            style=STYLE_TREE_COMPACT,
            yscrollcommand=pod_ep_yscroll.set,
            xscrollcommand=pod_ep_xscroll.set,
        )
        self.podcast_episode_tree.pack(side=LEFT, fill=BOTH, expand=True)
        pod_ep_yscroll.config(command=self.podcast_episode_tree.yview)
        pod_ep_xscroll.config(command=self.podcast_episode_tree.xview)
        self.podcast_episode_tree.heading("date", text="Date", anchor="w")
        self.podcast_episode_tree.heading("title", text="Title", anchor="w")
        self.podcast_episode_tree.heading("duration", text="Duration", anchor="w")
        self.podcast_episode_tree.heading("status", text="Status", anchor="w")
        self.podcast_episode_tree.column("date", width=100, minwidth=80, stretch=False)
        self.podcast_episode_tree.column("title", width=320, minwidth=120, stretch=True)
        self.podcast_episode_tree.column(
            "duration", width=72, minwidth=56, stretch=False
        )
        # Teal fill marks video (or dual) episodes — Treeview cannot draw borders.
        self.podcast_episode_tree.tag_configure(
            "video_episode", background=BG_VIDEO_PODCAST
        )
        self.podcast_episode_tree.column(
            "status", width=90, minwidth=70, stretch=False
        )

        # Playlists tab: Podcasts-style master–detail (list Treeview + tracks).
        # Intentional twin of Podcasts presentation — see docs/ui-visual-pass.md.
        pl_outer = flat_frame(self.playlists_tab)
        pl_outer.pack(fill=BOTH, expand=True, padx=4, pady=4)

        # Toolbar: compact actions + status (picker is the master list below).
        pl_toolbar = flat_frame(pl_outer)
        pl_toolbar.pack(side=TOP, fill=X, pady=(0, 2))
        Label(pl_toolbar, text="Playlist:").pack(side=LEFT, padx=(2, 4))
        # Hidden StringVar kept for call sites that set/get the current name.
        self.var_playlist_choice = StringVar(value="")
        self.btn_playlist_new = ttk.Button(
            pl_toolbar, text=GLYPH_ADD, width=3, style=STYLE_BTN_COMPACT
        )
        self.btn_playlist_new.pack(side=LEFT, padx=2)
        self.btn_playlist_delete = ttk.Button(
            pl_toolbar,
            text=GLYPH_REMOVE,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_playlist_delete.pack(side=LEFT, padx=2)
        self.btn_playlist_rename = ttk.Button(
            pl_toolbar,
            text="Rename…",
            width=9,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_playlist_rename.pack(side=LEFT, padx=2)
        self.btn_playlist_sync = ttk.Button(
            pl_toolbar,
            text="Sync playlist to device",
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_playlist_sync.pack(side=LEFT, padx=(8, 2))
        self.btn_playlist_move_up = ttk.Button(
            pl_toolbar,
            text=LABEL_MOVE_UP,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_playlist_move_up.pack(side=LEFT, padx=(8, 1))
        self.btn_playlist_move_down = ttk.Button(
            pl_toolbar,
            text=LABEL_MOVE_DOWN,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_playlist_move_down.pack(side=LEFT, padx=1)
        self.lbl_playlist_status = Label(pl_toolbar, text="", anchor="w")
        self.lbl_playlist_status.pack(side=LEFT, fill=X, expand=True, padx=6)

        # Master: host playlists (compact Treeview — not Combobox).
        pl_list_header = flat_frame(pl_outer)
        pl_list_header.pack(side=TOP, fill=X, pady=(4, 2))
        Label(
            pl_list_header, text="Playlists", font=("", 11, "bold"), anchor="w"
        ).pack(side=LEFT)
        pl_list_frame = flat_frame(pl_outer)
        pl_list_frame.pack(side=TOP, fill=BOTH, expand=False)
        pl_list_scroll = ttk.Scrollbar(pl_list_frame)
        pl_list_scroll.pack(side=RIGHT, fill=Y)
        self.playlist_list_tree = ttk.Treeview(
            pl_list_frame,
            columns=("name",),
            show="headings",
            selectmode="browse",
            height=8,
            style=STYLE_TREE_COMPACT,
            yscrollcommand=pl_list_scroll.set,
        )
        self.playlist_list_tree.pack(side=LEFT, fill=BOTH, expand=True)
        pl_list_scroll.config(command=self.playlist_list_tree.yview)
        self.playlist_list_tree.heading("name", text="Name", anchor="w")
        self.playlist_list_tree.column("name", width=400, minwidth=120, stretch=True)
        # Back-compat alias for older mental model ("combo" picker).
        self.playlist_combo = self.playlist_list_tree

        # Detail: tracks for the selected playlist.
        pl_tracks_header = flat_frame(pl_outer)
        pl_tracks_header.pack(side=TOP, fill=X, pady=(8, 2))
        self.lbl_playlist_tracks = Label(
            pl_tracks_header, text="Tracks", font=("", 11, "bold"), anchor="w"
        )
        self.lbl_playlist_tracks.pack(side=LEFT, fill=X, expand=True)

        pl_tree_frame = flat_frame(pl_outer)
        pl_tree_frame.pack(side=TOP, fill=BOTH, expand=True)

        # Device tab (phase 2 / O3): category combobox strip + one content frame
        # (no nested Notebook competing with the outer media tabs).
        dev_cat_bar = flat_frame(self.device_tab)
        dev_cat_bar.pack(side=TOP, fill=X, padx=4, pady=(4, 2))
        Label(dev_cat_bar, text="On device:").pack(side=LEFT, padx=(2, 4))
        self.var_device_category = StringVar(value=_DEVICE_CATEGORY_LABELS[0])
        self.device_category_combo = ttk.Combobox(
            dev_cat_bar,
            textvariable=self.var_device_category,
            values=list(_DEVICE_CATEGORY_LABELS),
            state="readonly",
            width=14,
        )
        self.device_category_combo.pack(side=LEFT, padx=2)
        self.device_category_combo.bind(
            "<<ComboboxSelected>>", self._on_device_category_combo, add="+"
        )

        self.device_content = flat_frame(self.device_tab)
        self.device_content.pack(side=TOP, fill=BOTH, expand=True)

        self.device_music_tab = flat_frame(self.device_content)
        self.device_video_tab = flat_frame(self.device_content)
        self.device_audiobooks_tab = flat_frame(self.device_content)
        self.device_podcasts_tab = flat_frame(self.device_content)
        self.device_playlists_tab = flat_frame(self.device_content)
        self._device_subview_by_label = {
            "Music": self.device_music_tab,
            "Video": self.device_video_tab,
            "Audiobooks": self.device_audiobooks_tab,
            "Podcasts": self.device_podcasts_tab,
            "Playlists": self.device_playlists_tab,
        }
        self._device_subview_frame = self.device_music_tab
        # Notebook-compatible shim: .select() / .select(frame) for call sites.
        self.device_notebook = _DeviceSubviewNotebook(self)
        self.device_music_tab.pack(fill=BOTH, expand=True)

        d_tree_frame = Frame(self.device_music_tab)
        d_tree_frame.pack(fill=BOTH, expand=True)

        dv_tree_frame = Frame(self.device_video_tab)
        dv_tree_frame.pack(fill=BOTH, expand=True)

        dab_tree_frame = Frame(self.device_audiobooks_tab)
        dab_tree_frame.pack(fill=BOTH, expand=True)

        dp_tree_frame = Frame(self.device_podcasts_tab)
        dp_tree_frame.pack(fill=BOTH, expand=True)

        # Device → Playlists: same master–detail chrome as host Playlists.
        dpl_outer = flat_frame(self.device_playlists_tab)
        dpl_outer.pack(fill=BOTH, expand=True, padx=4, pady=4)

        dpl_toolbar = flat_frame(dpl_outer)
        dpl_toolbar.pack(side=TOP, fill=X, pady=(0, 2))
        Label(dpl_toolbar, text="Playlist:").pack(side=LEFT, padx=(2, 4))
        self.var_device_playlist_choice = StringVar(value="")
        self.btn_device_playlist_new = ttk.Button(
            dpl_toolbar,
            text=GLYPH_ADD,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_device_playlist_new.pack(side=LEFT, padx=2)
        self.btn_device_playlist_delete = ttk.Button(
            dpl_toolbar,
            text=GLYPH_REMOVE,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_device_playlist_delete.pack(side=LEFT, padx=2)
        self.btn_device_playlist_rename = ttk.Button(
            dpl_toolbar,
            text="Rename…",
            width=9,
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_device_playlist_rename.pack(side=LEFT, padx=2)
        self.btn_device_playlist_refresh = ttk.Button(
            dpl_toolbar,
            text="Refresh from device",
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_device_playlist_refresh.pack(side=LEFT, padx=(8, 2))
        self.btn_device_playlist_recreate = ttk.Button(
            dpl_toolbar,
            text="Recreate locally…",
            style=STYLE_BTN_TOOL,
            state=DISABLED,
        )
        self.btn_device_playlist_recreate.pack(side=LEFT, padx=2)
        self.btn_device_playlist_move_up = ttk.Button(
            dpl_toolbar,
            text=LABEL_MOVE_UP,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_device_playlist_move_up.pack(side=LEFT, padx=(8, 1))
        self.btn_device_playlist_move_down = ttk.Button(
            dpl_toolbar,
            text=LABEL_MOVE_DOWN,
            width=3,
            style=STYLE_BTN_COMPACT,
            state=DISABLED,
        )
        self.btn_device_playlist_move_down.pack(side=LEFT, padx=1)
        self.lbl_device_playlist_status = Label(
            dpl_toolbar, text="", anchor="w"
        )
        self.lbl_device_playlist_status.pack(
            side=LEFT, fill=X, expand=True, padx=6
        )

        dpl_list_header = flat_frame(dpl_outer)
        dpl_list_header.pack(side=TOP, fill=X, pady=(4, 2))
        Label(
            dpl_list_header,
            text="On-device playlists",
            font=("", 11, "bold"),
            anchor="w",
        ).pack(side=LEFT)
        dpl_list_frame = flat_frame(dpl_outer)
        dpl_list_frame.pack(side=TOP, fill=BOTH, expand=False)
        dpl_list_scroll = ttk.Scrollbar(dpl_list_frame)
        dpl_list_scroll.pack(side=RIGHT, fill=Y)
        self.device_playlist_list_tree = ttk.Treeview(
            dpl_list_frame,
            columns=("name",),
            show="headings",
            selectmode="browse",
            height=8,
            style=STYLE_TREE_COMPACT,
            yscrollcommand=dpl_list_scroll.set,
        )
        self.device_playlist_list_tree.pack(side=LEFT, fill=BOTH, expand=True)
        dpl_list_scroll.config(command=self.device_playlist_list_tree.yview)
        self.device_playlist_list_tree.heading("name", text="Name", anchor="w")
        self.device_playlist_list_tree.column(
            "name", width=400, minwidth=120, stretch=True
        )
        self.device_playlist_combo = self.device_playlist_list_tree

        dpl_tracks_header = flat_frame(dpl_outer)
        dpl_tracks_header.pack(side=TOP, fill=X, pady=(8, 2))
        self.lbl_device_playlist_tracks = Label(
            dpl_tracks_header, text="Tracks", font=("", 11, "bold"), anchor="w"
        )
        self.lbl_device_playlist_tracks.pack(side=LEFT, fill=X, expand=True)

        dpl_tree_frame = flat_frame(dpl_outer)
        dpl_tree_frame.pack(side=TOP, fill=BOTH, expand=True)

        yscroll = ttk.Scrollbar(tree_frame)
        yscroll.pack(side=RIGHT, fill=Y)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal")
        xscroll.pack(side=BOTTOM, fill=X)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            # extended: Shift+click range, Ctrl/Cmd+click toggle multi-select.
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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
        self._compact_tree_rowheight = 28
        # O11: Thumb.Treeview for art rows; Compact for flat lists.
        self._style = apply_chrome_baseline(
            self.root,
            compact_tree_rowheight=self._compact_tree_rowheight,
            thumb_tree_rowheight=self._tree_rowheight,
        )
        try:
            self.tree.configure(style=STYLE_TREE_THUMB)
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
        d_yscroll = ttk.Scrollbar(d_tree_frame)
        d_yscroll.pack(side=RIGHT, fill=Y)
        d_xscroll = ttk.Scrollbar(d_tree_frame, orient="horizontal")
        d_xscroll.pack(side=BOTTOM, fill=X)

        self.device_tree = ttk.Treeview(
            d_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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
        dv_yscroll = ttk.Scrollbar(dv_tree_frame)
        dv_yscroll.pack(side=RIGHT, fill=Y)
        dv_xscroll = ttk.Scrollbar(dv_tree_frame, orient="horizontal")
        dv_xscroll.pack(side=BOTTOM, fill=X)

        self.device_video_tree = ttk.Treeview(
            dv_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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
        vl_yscroll = ttk.Scrollbar(vl_tree_frame)
        vl_yscroll.pack(side=RIGHT, fill=Y)
        vl_xscroll = ttk.Scrollbar(vl_tree_frame, orient="horizontal")
        vl_xscroll.pack(side=BOTTOM, fill=X)

        self.videos_tree = ttk.Treeview(
            vl_tree_frame,
            columns=("title",),
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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
        ab_yscroll = ttk.Scrollbar(ab_tree_frame)
        ab_yscroll.pack(side=RIGHT, fill=Y)
        ab_xscroll = ttk.Scrollbar(ab_tree_frame, orient="horizontal")
        ab_xscroll.pack(side=BOTTOM, fill=X)

        self.audiobooks_tree = ttk.Treeview(
            ab_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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
        pl_yscroll = ttk.Scrollbar(pl_tree_frame)
        pl_yscroll.pack(side=RIGHT, fill=Y)
        pl_xscroll = ttk.Scrollbar(pl_tree_frame, orient="horizontal")
        pl_xscroll.pack(side=BOTTOM, fill=X)

        self.playlist_tree = ttk.Treeview(
            pl_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_COMPACT,
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
        dab_yscroll = ttk.Scrollbar(dab_tree_frame)
        dab_yscroll.pack(side=RIGHT, fill=Y)
        dab_xscroll = ttk.Scrollbar(dab_tree_frame, orient="horizontal")
        dab_xscroll.pack(side=BOTTOM, fill=X)

        self.device_audiobooks_tree = ttk.Treeview(
            dab_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_THUMB,
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

        # Device podcasts tree (ZENcast / show folder → episodes; audio + video).
        dp_yscroll = ttk.Scrollbar(dp_tree_frame)
        dp_yscroll.pack(side=RIGHT, fill=Y)
        dp_xscroll = ttk.Scrollbar(dp_tree_frame, orient="horizontal")
        dp_xscroll.pack(side=BOTTOM, fill=X)

        self.device_podcasts_tree = ttk.Treeview(
            dp_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_COMPACT,
            yscrollcommand=dp_yscroll.set,
            xscrollcommand=dp_xscroll.set,
        )
        self.device_podcasts_tree.pack(side=LEFT, fill=BOTH, expand=True)
        dp_yscroll.config(command=self.device_podcasts_tree.yview)
        dp_xscroll.config(command=self.device_podcasts_tree.xview)

        self.device_podcasts_tree.heading("#0", text="#", anchor="w")
        self.device_podcasts_tree.heading("title", text="Title", anchor="w")
        self.device_podcasts_tree.heading("artist", text="Show", anchor="w")
        self.device_podcasts_tree.heading("album", text="Album", anchor="w")
        self.device_podcasts_tree.heading("year", text="Year", anchor="w")
        self.device_podcasts_tree.column(
            "#0",
            width=self._thumb_size + 28,
            minwidth=self._thumb_size + 20,
            stretch=False,
        )
        self.device_podcasts_tree.column(
            "title", width=280, minwidth=120, stretch=True
        )
        self.device_podcasts_tree.column("artist", width=140, minwidth=60)
        self.device_podcasts_tree.column("album", width=140, minwidth=60)
        self.device_podcasts_tree.column(
            "year", width=56, minwidth=40, stretch=False
        )

        # Device → Playlists tree (flat ordered list; same columns as host Playlists).
        dpl_yscroll = ttk.Scrollbar(dpl_tree_frame)
        dpl_yscroll.pack(side=RIGHT, fill=Y)
        dpl_xscroll = ttk.Scrollbar(dpl_tree_frame, orient="horizontal")
        dpl_xscroll.pack(side=BOTTOM, fill=X)

        self.device_playlist_tree = ttk.Treeview(
            dpl_tree_frame,
            columns=TREE_COLS,
            show="tree headings",
            selectmode="extended",
            style=STYLE_TREE_COMPACT,
            yscrollcommand=dpl_yscroll.set,
            xscrollcommand=dpl_xscroll.set,
        )
        self.device_playlist_tree.pack(side=LEFT, fill=BOTH, expand=True)
        dpl_yscroll.config(command=self.device_playlist_tree.yview)
        dpl_xscroll.config(command=self.device_playlist_tree.xview)

        self.device_playlist_tree.heading("#0", text="#", anchor="w")
        self.device_playlist_tree.heading("title", text="Title", anchor="w")
        self.device_playlist_tree.heading("artist", text="Artist", anchor="w")
        self.device_playlist_tree.heading("album", text="Album", anchor="w")
        self.device_playlist_tree.heading("year", text="Year", anchor="w")
        self.device_playlist_tree.column(
            "#0", width=48, minwidth=40, stretch=False
        )
        self.device_playlist_tree.column(
            "title", width=280, minwidth=120, stretch=True
        )
        self.device_playlist_tree.column("artist", width=140, minwidth=60)
        self.device_playlist_tree.column("album", width=140, minwidth=60)
        self.device_playlist_tree.column(
            "year", width=56, minwidth=40, stretch=False
        )
        self.device_playlist_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.device_playlist_tree.tag_configure(
            "playing", background=BG_PLAYING, foreground=FG_PLAYING
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
        self.device_podcasts_tree.tag_configure("group", font=("", 11, "bold"))
        self.device_podcasts_tree.tag_configure(
            "group_folder", font=("", 12, "bold")
        )
        self.device_podcasts_tree.tag_configure("dead", foreground=_DEAD_TRACK_FG)
        self.device_podcasts_tree.tag_configure(
            "video_episode", background=BG_VIDEO_PODCAST
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

    def _show_stable_mode_about(self) -> None:
        """Modal with full Stable Mode help (panel shows a short caption only)."""
        from tkinter import messagebox

        try:
            messagebox.showinfo(
                "Stable Mode",
                STABLE_MODE_HELP,
                parent=self.root,
            )
        except Exception:
            pass

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
            self.lbl_device_caption.configure(text=STABLE_MODE_CAPTION)
            self.lbl_device_graphic.configure(image="")
            if self.device_graphic_slot.winfo_ismapped():
                self.device_graphic_slot.pack_forget()
            try:
                self.btn_stable_mode_about.pack(
                    padx=6, pady=(6, 8), anchor="w"
                )
            except Exception:
                pass
        else:
            self.lbl_device_title.configure(text="Device")
            self.lbl_device_caption.configure(text=self._device_caption)
            try:
                self.btn_stable_mode_about.pack_forget()
            except Exception:
                pass
            if self._device_photo is not None:
                self.lbl_device_graphic.configure(image=self._device_photo)
            if not self.device_graphic_slot.winfo_ismapped():
                self.device_graphic_slot.pack(padx=6, pady=6, fill=X)
        self.apply_mode_actions()

    def is_startup_hint_active(self) -> bool:
        """True while the context subframe still shows the first-run blurb."""
        return bool(self._startup_hint_active)

    def _on_context_mousewheel(self, event) -> str | None:
        """Scroll the selection detail Text on wheel / trackpad."""
        try:
            if getattr(event, "num", None) == 4 or (
                getattr(event, "delta", 0) > 0
            ):
                self.txt_context_detail.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5 or (
                getattr(event, "delta", 0) < 0
            ):
                self.txt_context_detail.yview_scroll(1, "units")
        except Exception:
            pass
        return "break"

    def set_context_detail(
        self, text: str, *, path: str | None = None
    ) -> None:
        """Update the context subframe (selection metadata).

        Replaces the first-run experimental hint. Always updates the visible
        body (including under Stable Mode) so selection still has a home.
        Long text scrolls inside the left panel. *path* is the full host path
        or URL for a single item (shown italic below the scroll area).
        """
        self._startup_hint_active = False
        self._context_detail = text or ""
        self._context_path = (path or "").strip()
        try:
            self.txt_context_detail.configure(state=NORMAL)
            self.txt_context_detail.delete("1.0", END)
            if self._context_detail:
                self.txt_context_detail.insert("1.0", self._context_detail)
            self.txt_context_detail.configure(state=DISABLED)
            self.txt_context_detail.yview_moveto(0)
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
        on_finish_day_podcast_sync=None,
        on_select_root=None,
        on_update=None,
        **_legacy,
    ) -> None:
        """Wire Library menu entries (called once from the controller).

        *on_manage_library* opens the roots manager (add/remove/update).
        *on_manage_playlists* focuses the Playlists notebook tab.
        *on_finish_day_podcast_sync* pushes today's day podcast playlist to the device.
        *on_select_root* / *on_update* / legacy kwargs are ignored.
        """
        del on_select_root, on_update, _legacy
        self.menu_library.entryconfig(
            MENU_MANAGE_LIBRARY, command=on_manage_library
        )
        if on_manage_playlists is not None:
            self.menu_library.entryconfig(
                MENU_MANAGE_PLAYLISTS, command=on_manage_playlists
            )
        if on_finish_day_podcast_sync is not None:
            self.menu_library.entryconfig(
                MENU_FINISH_DAY_PODCAST_SYNC,
                command=on_finish_day_podcast_sync,
            )

    def set_finish_day_podcast_sync_menu(
        self,
        *,
        playlist_name: str,
        enabled: bool,
        episode_count: int = 0,
    ) -> None:
        """Update Library → Finish Sync label and enabled state."""
        name = (playlist_name or "").strip() or "day playlist"
        if episode_count > 0:
            label = f"Finish Sync ({name}) — {episode_count}"
        else:
            label = f"Finish Sync ({name})"
        try:
            prev = getattr(
                self, "_finish_day_podcast_menu_label", MENU_FINISH_DAY_PODCAST_SYNC
            )
            try:
                self.menu_library.entryconfig(
                    prev,
                    label=label,
                    state=NORMAL if enabled else DISABLED,
                )
            except Exception:
                end = int(self.menu_library.index(END) or 0)
                for i in range(end + 1):
                    try:
                        lab = str(self.menu_library.entrycget(i, "label") or "")
                    except Exception:
                        continue
                    if lab.startswith("Finish Sync ("):
                        self.menu_library.entryconfig(
                            i,
                            label=label,
                            state=NORMAL if enabled else DISABLED,
                        )
                        break
            self._finish_day_podcast_menu_label = label
        except Exception:
            pass

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
        self._transfer_menu_commands = {
            "on_sync_entire": on_sync_entire,
            "on_sync_folder": on_sync_folder,
            "on_sync_selected": on_sync_selected,
            "on_resume_sync": on_resume_sync,
            "on_cancel_job": on_cancel_job,
            "on_package_retail": on_package_retail,
            "on_restore_retail": on_restore_retail,
        }
        if on_cancel_job is not None:
            self._cancel_job_command = on_cancel_job
        self._apply_transfer_menu_commands()

    def _apply_transfer_menu_commands(self) -> None:
        cmds = self._transfer_menu_commands
        if not cmds:
            return
        on_sync_entire = cmds.get("on_sync_entire")
        on_sync_folder = cmds.get("on_sync_folder")
        on_sync_selected = cmds.get("on_sync_selected")
        on_resume_sync = cmds.get("on_resume_sync")
        on_cancel_job = cmds.get("on_cancel_job")
        on_package_retail = cmds.get("on_package_retail")
        on_restore_retail = cmds.get("on_restore_retail")
        if on_sync_entire is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_SYNC_ENTIRE, command=on_sync_entire
            )
        if on_sync_folder is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_SYNC_FOLDER, command=on_sync_folder
            )
        if on_sync_selected is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_SYNC_SELECTED, command=on_sync_selected
            )
        if on_resume_sync is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_RESUME_SYNC, command=on_resume_sync
            )
        if on_package_retail is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_PACKAGE_RETAIL, command=on_package_retail
            )
        if on_restore_retail is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_RESTORE_RETAIL, command=on_restore_retail
            )
        if on_cancel_job is not None:
            self._menu_entryconfig(
                self.menu_transfer, MENU_CANCEL_JOB, command=on_cancel_job
            )
            self._cancel_job_command = on_cancel_job

    def set_sync_selected_enabled(self, enabled: bool, *, count: int = 0) -> None:
        """Enable Transfer → Sync Selected when one or more tracks are selected."""
        self._sync_selected_enabled = bool(enabled)
        self._sync_selected_count = int(count) if enabled else 0
        state = NORMAL if enabled else DISABLED
        label = MENU_SYNC_SELECTED
        if enabled and count > 0:
            label = f"Sync Selected Tracks ({count})"
        self._menu_entryconfig(
            self.menu_transfer, MENU_SYNC_SELECTED, state=state, label=label
        )

    def set_resume_sync_enabled(self, enabled: bool) -> None:
        """Enable Transfer → Resume Sync when a durable job can continue."""
        self._resume_sync_enabled = bool(enabled)
        self._menu_entryconfig(
            self.menu_transfer,
            MENU_RESUME_SYNC,
            state=NORMAL if enabled else DISABLED,
        )

    def set_config_menu_commands(
        self,
        *,
        on_config,
        on_stable_mode_toggle=None,
        on_sync_album_art_toggle=None,
        on_enable_experimental_tools_toggle=None,
        on_artist_folders_toggle=None,
        on_album_folders_toggle=None,
        on_podcast_folders_toggle=None,
        on_allow_video_podcasts_toggle=None,
        on_audio_podcasts_as_video_toggle=None,
        on_keep_downloaded_podcasts_toggle=None,
        on_clear_downloaded_podcasts=None,
        on_reveal_podcast_downloads=None,
        on_podcast_settings=None,
        on_audiobook_encode=None,
    ) -> None:
        self._config_menu_commands = {
            "on_config": on_config,
            "on_stable_mode_toggle": on_stable_mode_toggle,
            "on_sync_album_art_toggle": on_sync_album_art_toggle,
            "on_enable_experimental_tools_toggle": on_enable_experimental_tools_toggle,
            "on_artist_folders_toggle": on_artist_folders_toggle,
            "on_album_folders_toggle": on_album_folders_toggle,
            "on_podcast_folders_toggle": on_podcast_folders_toggle,
            "on_allow_video_podcasts_toggle": on_allow_video_podcasts_toggle,
            "on_audio_podcasts_as_video_toggle": on_audio_podcasts_as_video_toggle,
            "on_keep_downloaded_podcasts_toggle": on_keep_downloaded_podcasts_toggle,
            "on_clear_downloaded_podcasts": on_clear_downloaded_podcasts,
            "on_reveal_podcast_downloads": on_reveal_podcast_downloads,
            "on_podcast_settings": on_podcast_settings,
            "on_audiobook_encode": on_audiobook_encode,
        }
        self._apply_config_menu_commands()

    def _apply_config_menu_commands(self) -> None:
        cmds = self._config_menu_commands
        if not cmds:
            return
        on_config = cmds.get("on_config")
        if on_config is not None:
            self._menu_entryconfig(self.menu_config, MENU_CONFIG, command=on_config)
        pairs = (
            (MENU_STABLE_MODE, cmds.get("on_stable_mode_toggle")),
            (MENU_SYNC_ALBUM_ART, cmds.get("on_sync_album_art_toggle")),
            (
                MENU_ENABLE_EXPERIMENTAL_TOOLS,
                cmds.get("on_enable_experimental_tools_toggle"),
            ),
            (MENU_ARTIST_FOLDERS, cmds.get("on_artist_folders_toggle")),
            (MENU_ALBUM_FOLDERS, cmds.get("on_album_folders_toggle")),
            (MENU_PODCAST_FOLDERS, cmds.get("on_podcast_folders_toggle")),
            (
                MENU_ALLOW_VIDEO_PODCASTS,
                cmds.get("on_allow_video_podcasts_toggle"),
            ),
            (
                MENU_AUDIO_PODCASTS_AS_VIDEO,
                cmds.get("on_audio_podcasts_as_video_toggle"),
            ),
            (
                MENU_KEEP_DOWNLOADED_PODCASTS,
                cmds.get("on_keep_downloaded_podcasts_toggle"),
            ),
            (
                MENU_REVEAL_PODCAST_DOWNLOADS,
                cmds.get("on_reveal_podcast_downloads"),
            ),
            (
                MENU_CLEAR_DOWNLOADED_PODCASTS,
                cmds.get("on_clear_downloaded_podcasts"),
            ),
            (MENU_PODCAST_SETTINGS, cmds.get("on_podcast_settings")),
            (MENU_AUDIOBOOK_ENCODE, cmds.get("on_audiobook_encode")),
        )
        for label, cmd in pairs:
            if cmd is not None:
                self._menu_entryconfig(self.menu_config, label, command=cmd)

    def set_podcast_tab_commands(
        self,
        *,
        on_add=None,
        on_remove=None,
        on_refresh=None,
        on_more=None,
        on_sync_latest=None,
        on_show_select=None,
        on_episode_select=None,
        on_show_sync=None,
        on_show_encode=None,
        on_show_special_sync=None,
        on_episode_sync=None,
        on_episode_special_sync=None,
        on_episode_play=None,
        on_episode_reveal_download=None,
        on_episode_add_to_day_playlist=None,
        on_episode_remove_from_day_playlist=None,
    ) -> None:
        if on_add is not None:
            self.btn_podcast_add.configure(command=on_add)
        if on_remove is not None:
            self.btn_podcast_remove.configure(command=on_remove)
        if on_refresh is not None:
            self.btn_podcast_refresh.configure(command=on_refresh)
        if on_more is not None:
            self.btn_podcast_more.configure(command=on_more)
        if on_sync_latest is not None:
            self.btn_podcast_sync_latest.configure(command=on_sync_latest)
        if on_show_select is not None:
            self.podcast_show_tree.bind(
                "<<TreeviewSelect>>", lambda _e: on_show_select()
            )
        if on_episode_select is not None:
            self.podcast_episode_tree.bind(
                "<<TreeviewSelect>>", lambda _e: on_episode_select()
            )
        # Merge into stored cmds (preserve handlers across rebuilds).
        prev = getattr(self, "_podcast_ctx_cmds", {}) or {}
        self._podcast_ctx_cmds = {
            **prev,
            "on_sync_latest": on_show_sync or prev.get("on_sync_latest"),
            "on_encode": on_show_encode or prev.get("on_encode"),
            "on_special_sync_show": on_show_special_sync
            or prev.get("on_special_sync_show"),
            "on_episode_play": on_episode_play or prev.get("on_episode_play"),
            "on_episode_sync": on_episode_sync or prev.get("on_episode_sync"),
            "on_special_sync_episodes": on_episode_special_sync
            or prev.get("on_special_sync_episodes"),
            "on_episode_reveal_download": on_episode_reveal_download
            or prev.get("on_episode_reveal_download"),
            "on_episode_add_to_day_playlist": on_episode_add_to_day_playlist
            or prev.get("on_episode_add_to_day_playlist"),
            "on_episode_remove_from_day_playlist": (
                on_episode_remove_from_day_playlist
                or prev.get("on_episode_remove_from_day_playlist")
            ),
        }
        self._apply_podcast_context_commands(self._podcast_ctx_cmds)
        # Day-playlist / reveal use labels (stable across Special Sync insert).
        cmds = self._podcast_ctx_cmds
        if cmds.get("on_episode_reveal_download") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_REVEAL_DOWNLOAD,
                    command=cmds["on_episode_reveal_download"],
                )
            except Exception:
                pass
        if cmds.get("on_episode_add_to_day_playlist") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_ADD_TO_DAY_PLAYLIST,
                    command=cmds["on_episode_add_to_day_playlist"],
                )
            except Exception:
                pass
        if cmds.get("on_episode_remove_from_day_playlist") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_REMOVE_FROM_DAY_PLAYLIST,
                    command=cmds["on_episode_remove_from_day_playlist"],
                )
            except Exception:
                pass

    def set_podcast_day_playlist_episode_menu(
        self,
        *,
        playlist_name: str,
        can_add: bool,
        can_remove: bool,
    ) -> None:
        """Update Add/Remove day-playlist labels and enabled state."""
        name = (playlist_name or "").strip() or "day playlist"
        add_label = f"Add This Episode to {name}"
        rem_label = f"Remove This Episode from {name}"
        try:
            # Prefer labels (indices shift when Special Sync is present).
            self.menu_podcast_episode_ctx.entryconfig(
                CTX_PODCAST_ADD_TO_DAY_PLAYLIST,
                label=add_label,
                state=NORMAL if can_add else DISABLED,
            )
            self.menu_podcast_episode_ctx.entryconfig(
                CTX_PODCAST_REMOVE_FROM_DAY_PLAYLIST,
                label=rem_label,
                state=NORMAL if can_remove else DISABLED,
            )
        except Exception:
            pass

    def popup_podcast_show_context(self, event) -> str | None:
        try:
            row = self.podcast_show_tree.identify_row(event.y)
            if row:
                if row not in self.podcast_show_tree.selection():
                    self.podcast_show_tree.selection_set(row)
                try:
                    self.podcast_show_tree.event_generate("<<TreeviewSelect>>")
                except Exception:
                    pass
            self.menu_podcast_show_ctx.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu_podcast_show_ctx.grab_release()
            except Exception:
                pass
        return "break"

    def popup_podcast_episode_context(self, event) -> str | None:
        try:
            row = self.podcast_episode_tree.identify_row(event.y)
            if row:
                if row not in self.podcast_episode_tree.selection():
                    self.podcast_episode_tree.selection_set(row)
            # Controller may refresh Play/Sync labels for multi-select.
            try:
                self.podcast_episode_tree.event_generate("<<TreeviewSelect>>")
            except Exception:
                pass
            self.menu_podcast_episode_ctx.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu_podcast_episode_ctx.grab_release()
            except Exception:
                pass
        return "break"

    def set_view_menu_commands(self, *, on_always_show_playback_toggle=None) -> None:
        if on_always_show_playback_toggle is not None:
            self.menu_view.entryconfig(
                MENU_ALWAYS_SHOW_PLAYBACK,
                command=on_always_show_playback_toggle,
            )

    def set_album_folders_menu_enabled(self, enabled: bool) -> None:
        """Enable/disable album-folder checkbutton (requires artist folders)."""
        self._menu_entryconfig(
            self.menu_config,
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
        self._device_menu_commands = {
            "on_connect": on_connect,
            "on_disconnect": on_disconnect,
            "on_device_info": on_device_info,
            "on_create_folder": on_create_folder,
            "on_list_folders": on_list_folders,
            "on_list_files": on_list_files,
            "on_list_tracks": on_list_tracks,
            "on_get_tracks_from_device": on_get_tracks_from_device,
            "on_delete_track": on_delete_track,
            "on_get_file_info": on_get_file_info,
            "on_get_track_info": on_get_track_info,
            "on_delete_all": on_delete_all,
            "on_refresh_device_index": on_refresh_device_index,
            "on_send_video": on_send_video,
        }
        self._apply_device_menu_commands()

    def _apply_device_menu_commands(self) -> None:
        cmds = self._device_menu_commands
        if not cmds:
            return
        pairs = (
            (MENU_CONNECT, cmds.get("on_connect")),
            (MENU_DISCONNECT, cmds.get("on_disconnect")),
            (MENU_DEVICE_INFO, cmds.get("on_device_info")),
            (MENU_CREATE_FOLDER, cmds.get("on_create_folder")),
            (MENU_SEND_VIDEO, cmds.get("on_send_video")),
            (MENU_REFRESH_DEVICE_INDEX, cmds.get("on_refresh_device_index")),
            (MENU_LIST_FOLDERS, cmds.get("on_list_folders")),
            (MENU_LIST_FILES, cmds.get("on_list_files")),
            (MENU_LIST_TRACKS, cmds.get("on_list_tracks")),
            (MENU_GET_TRACKS_FROM_DEVICE, cmds.get("on_get_tracks_from_device")),
            (MENU_DELETE_TRACK, cmds.get("on_delete_track")),
            (MENU_GET_FILE_INFO, cmds.get("on_get_file_info")),
            (MENU_GET_TRACK_INFO, cmds.get("on_get_track_info")),
            (MENU_DELETE_ALL, cmds.get("on_delete_all")),
        )
        for label, cmd in pairs:
            if cmd is not None:
                self._menu_entryconfig(self.menu_device, label, command=cmd)

    @staticmethod
    def _menu_has_label(menu: Menu, entry: str) -> bool:
        try:
            menu.index(entry)
            return True
        except Exception:
            return False

    def _menu_entryconfig(self, menu: Menu, entry: str, **kwargs) -> None:
        """entryconfig by menu entry label; no-op if the entry is absent.

        *entry* is the menu item's identity string (what ``Menu.index`` uses).
        Pass Tk options such as ``command=``, ``state=``, or ``label=`` (to
        retitle the item) via **kwargs.
        """
        if not self._menu_has_label(menu, entry):
            return
        try:
            menu.entryconfig(entry, **kwargs)
        except Exception:
            pass

    def experimental_tools_enabled(self) -> bool:
        """True when Config → Enable Experimental Tools is on."""
        return bool(self._enable_experimental_tools)

    def set_experimental_tools_enabled(self, enabled: bool) -> None:
        """Show or hide experimental Device/Transfer/Config menu commands.

        Send Video is a standard Device tool and is not gated.
        List Folders is experimental and is gated.
        Special Sync on library/podcast context menus is experimental.
        """
        enabled = bool(enabled)
        self._enable_experimental_tools = enabled
        try:
            self.var_enable_experimental_tools.set(enabled)
        except Exception:
            pass
        self._rebuild_device_menu()
        self._rebuild_transfer_menu()
        self._rebuild_config_menu()
        # Context menus are created after the first call during __init__.
        if getattr(self, "menu_track_ctx", None) is not None:
            self._rebuild_library_context_menus()
        if getattr(self, "menu_podcast_show_ctx", None) is not None:
            self._rebuild_podcast_context_menus()
        # apply_mode_ui sets _mode late in __init__; skip until then.
        if getattr(self, "_mode", None) is not None:
            self.apply_mode_actions()

    def _rebuild_library_context_menus(self) -> None:
        """Rebuild Music/Audiobook track + group context menus."""
        exp = bool(getattr(self, "_enable_experimental_tools", False))
        cmds = getattr(self, "_track_ctx_cmds", {}) or {}

        def _cmd(key: str):
            return cmds.get(key)

        try:
            self.menu_track_ctx.delete(0, END)
        except Exception:
            pass
        self.menu_track_ctx.add_command(
            label=CTX_SYNC_SELECTED, state=DISABLED
        )
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_SYNC_TRACK)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ALBUM)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ARTIST)
        if exp:
            self.menu_track_ctx.add_command(label=CTX_SPECIAL_SYNC)
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_PLAY_TRACK)
        self.menu_track_ctx.add_command(label=CTX_ADD_TO_PLAYLIST)
        self.menu_track_ctx.add_separator()
        self.menu_track_ctx.add_command(label=CTX_EXCLUDE_FILE)
        self.menu_track_ctx.add_command(label=CTX_EXCLUDE_FOLDER)

        try:
            self.menu_artist_ctx.delete(0, END)
        except Exception:
            pass
        self.menu_artist_ctx.add_command(label=CTX_SYNC_ARTIST_GROUP)
        if exp:
            self.menu_artist_ctx.add_command(label=CTX_SPECIAL_SYNC_GROUP)
        self.menu_artist_ctx.add_separator()
        self.menu_artist_ctx.add_command(label=CTX_PLAY_ARTIST_GROUP)
        self.menu_artist_ctx.add_command(label=CTX_ADD_ARTIST_TO_PLAYLIST)

        try:
            self.menu_album_ctx.delete(0, END)
        except Exception:
            pass
        self.menu_album_ctx.add_command(label=CTX_SYNC_ALBUM_GROUP)
        if exp:
            self.menu_album_ctx.add_command(label=CTX_SPECIAL_SYNC_GROUP)
        self.menu_album_ctx.add_separator()
        self.menu_album_ctx.add_command(label=CTX_PLAY_ALBUM_GROUP)
        self.menu_album_ctx.add_command(label=CTX_ADD_ALBUM_TO_PLAYLIST)
        self.menu_album_ctx.add_separator()
        self.menu_album_ctx.add_command(label=CTX_EXCLUDE_GROUP_FOLDER)

        # Re-apply stored handlers if present
        if cmds:
            self._apply_track_context_commands(cmds)

    def _rebuild_podcast_context_menus(self) -> None:
        """Rebuild podcast show/episode context menus (Special Sync gated)."""
        exp = bool(getattr(self, "_enable_experimental_tools", False))
        cmds = getattr(self, "_podcast_ctx_cmds", {}) or {}
        try:
            self.menu_podcast_show_ctx.delete(0, END)
        except Exception:
            pass
        self.menu_podcast_show_ctx.add_command(label=CTX_PODCAST_SYNC_LATEST)
        if exp:
            self.menu_podcast_show_ctx.add_command(
                label=CTX_PODCAST_SPECIAL_SYNC
            )
        self.menu_podcast_show_ctx.add_command(label=CTX_PODCAST_ENCODE)

        try:
            self.menu_podcast_episode_ctx.delete(0, END)
        except Exception:
            pass
        self.menu_podcast_episode_ctx.add_command(
            label=CTX_PODCAST_PLAY_EPISODE
        )
        self.menu_podcast_episode_ctx.add_command(
            label=CTX_PODCAST_EPISODE_SYNC
        )
        if exp:
            self.menu_podcast_episode_ctx.add_command(
                label=CTX_PODCAST_EPISODE_SPECIAL_SYNC
            )
        self.menu_podcast_episode_ctx.add_separator()
        self.menu_podcast_episode_ctx.add_command(
            label=CTX_PODCAST_ADD_TO_DAY_PLAYLIST, state=DISABLED
        )
        self.menu_podcast_episode_ctx.add_command(
            label=CTX_PODCAST_REMOVE_FROM_DAY_PLAYLIST, state=DISABLED
        )
        self.menu_podcast_episode_ctx.add_separator()
        self.menu_podcast_episode_ctx.add_command(
            label=CTX_PODCAST_REVEAL_DOWNLOAD, state=DISABLED
        )
        if cmds:
            self._apply_podcast_context_commands(cmds)

    def _apply_track_context_commands(self, cmds: dict) -> None:
        """Wire labels → callables for library context menus."""
        exp = bool(getattr(self, "_enable_experimental_tools", False))
        mapping = [
            (CTX_SYNC_SELECTED, cmds.get("on_sync_selected")),
            (CTX_SYNC_TRACK, cmds.get("on_sync_track")),
            (CTX_SYNC_ALBUM, cmds.get("on_sync_album")),
            (CTX_SYNC_ARTIST, cmds.get("on_sync_artist")),
            (CTX_PLAY_TRACK, cmds.get("on_play_track")),
            (CTX_ADD_TO_PLAYLIST, cmds.get("on_add_to_playlist")),
            (CTX_EXCLUDE_FILE, cmds.get("on_exclude_file")),
            (CTX_EXCLUDE_FOLDER, cmds.get("on_exclude_folder")),
        ]
        if exp:
            mapping.append((CTX_SPECIAL_SYNC, cmds.get("on_special_sync")))
        for label, handler in mapping:
            if handler is not None:
                try:
                    self.menu_track_ctx.entryconfig(label, command=handler)
                except Exception:
                    pass
        # Artist / album groups
        try:
            self.menu_artist_ctx.entryconfig(
                CTX_SYNC_ARTIST_GROUP,
                command=cmds.get("on_sync_artist_group"),
            )
        except Exception:
            pass
        if exp and cmds.get("on_special_sync_group") is not None:
            try:
                self.menu_artist_ctx.entryconfig(
                    CTX_SPECIAL_SYNC_GROUP,
                    command=cmds.get("on_special_sync_group"),
                )
            except Exception:
                pass
        if cmds.get("on_play_artist_group") is not None:
            try:
                self.menu_artist_ctx.entryconfig(
                    CTX_PLAY_ARTIST_GROUP,
                    command=cmds.get("on_play_artist_group"),
                )
            except Exception:
                pass
        if cmds.get("on_add_artist_to_playlist") is not None:
            try:
                self.menu_artist_ctx.entryconfig(
                    CTX_ADD_ARTIST_TO_PLAYLIST,
                    command=cmds.get("on_add_artist_to_playlist"),
                )
            except Exception:
                pass
        try:
            self.menu_album_ctx.entryconfig(
                CTX_SYNC_ALBUM_GROUP,
                command=cmds.get("on_sync_album_group"),
            )
        except Exception:
            pass
        if exp and cmds.get("on_special_sync_group") is not None:
            try:
                self.menu_album_ctx.entryconfig(
                    CTX_SPECIAL_SYNC_GROUP,
                    command=cmds.get("on_special_sync_group"),
                )
            except Exception:
                pass
        if cmds.get("on_play_album_group") is not None:
            try:
                self.menu_album_ctx.entryconfig(
                    CTX_PLAY_ALBUM_GROUP,
                    command=cmds.get("on_play_album_group"),
                )
            except Exception:
                pass
        if cmds.get("on_add_album_to_playlist") is not None:
            try:
                self.menu_album_ctx.entryconfig(
                    CTX_ADD_ALBUM_TO_PLAYLIST,
                    command=cmds.get("on_add_album_to_playlist"),
                )
            except Exception:
                pass
        if cmds.get("on_exclude_group_folder") is not None:
            try:
                self.menu_album_ctx.entryconfig(
                    CTX_EXCLUDE_GROUP_FOLDER,
                    command=cmds.get("on_exclude_group_folder"),
                )
            except Exception:
                pass

    def _apply_podcast_context_commands(self, cmds: dict) -> None:
        exp = bool(getattr(self, "_enable_experimental_tools", False))
        if cmds.get("on_sync_latest") is not None:
            try:
                self.menu_podcast_show_ctx.entryconfig(
                    CTX_PODCAST_SYNC_LATEST, command=cmds["on_sync_latest"]
                )
            except Exception:
                pass
        if exp and cmds.get("on_special_sync_show") is not None:
            try:
                self.menu_podcast_show_ctx.entryconfig(
                    CTX_PODCAST_SPECIAL_SYNC,
                    command=cmds["on_special_sync_show"],
                )
            except Exception:
                pass
        if cmds.get("on_encode") is not None:
            try:
                self.menu_podcast_show_ctx.entryconfig(
                    CTX_PODCAST_ENCODE, command=cmds["on_encode"]
                )
            except Exception:
                pass
        if cmds.get("on_episode_play") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_PLAY_EPISODE, command=cmds["on_episode_play"]
                )
            except Exception:
                pass
        if cmds.get("on_episode_sync") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_EPISODE_SYNC, command=cmds["on_episode_sync"]
                )
            except Exception:
                pass
        if exp and cmds.get("on_special_sync_episodes") is not None:
            try:
                self.menu_podcast_episode_ctx.entryconfig(
                    CTX_PODCAST_EPISODE_SPECIAL_SYNC,
                    command=cmds["on_special_sync_episodes"],
                )
            except Exception:
                pass

    def _rebuild_device_menu(self) -> None:
        try:
            self.menu_device.delete(0, END)
        except Exception:
            pass
        labels = _device_menu_labels(
            experimental_tools=self._enable_experimental_tools
        )
        for label in labels:
            self.menu_device.add_command(label=label, state=DISABLED)
        self._apply_device_menu_commands()

    def _rebuild_transfer_menu(self) -> None:
        # Preserve dynamic enable state across rebuild.
        sync_en = bool(getattr(self, "_sync_selected_enabled", False))
        sync_count = int(getattr(self, "_sync_selected_count", 0) or 0)
        resume_en = bool(getattr(self, "_resume_sync_enabled", False))
        cancel_en = bool(getattr(self, "_cancel_job_enabled", False))
        try:
            self.menu_transfer.delete(0, END)
        except Exception:
            pass
        self.menu_transfer.add_command(label=MENU_SYNC_ENTIRE)
        self.menu_transfer.add_command(label=MENU_SYNC_FOLDER)
        self.menu_transfer.add_command(label=MENU_SYNC_SELECTED, state=DISABLED)
        self.menu_transfer.add_command(label=MENU_RESUME_SYNC, state=DISABLED)
        if self._enable_experimental_tools:
            self.menu_transfer.add_separator()
            self.menu_transfer.add_command(label=MENU_PACKAGE_RETAIL)
            self.menu_transfer.add_command(label=MENU_RESTORE_RETAIL)
        self.menu_transfer.add_separator()
        self.menu_transfer.add_command(label=MENU_CANCEL_JOB, state=DISABLED)
        self._apply_transfer_menu_commands()
        self.set_sync_selected_enabled(sync_en, count=sync_count)
        self.set_resume_sync_enabled(resume_en)
        self.set_cancel_job_enabled(cancel_en)

    def _rebuild_config_menu(self) -> None:
        album_enabled = bool(self.var_artist_folders.get())
        try:
            self.menu_config.delete(0, END)
        except Exception:
            pass
        self.menu_config.add_checkbutton(
            label=MENU_STABLE_MODE,
            variable=self.var_stable_mode,
            onvalue=True,
            offvalue=False,
        )
        self.menu_config.add_checkbutton(
            label=MENU_SYNC_ALBUM_ART,
            variable=self.var_sync_album_art,
            onvalue=True,
            offvalue=False,
        )
        self.menu_config.add_checkbutton(
            label=MENU_ENABLE_EXPERIMENTAL_TOOLS,
            variable=self.var_enable_experimental_tools,
            onvalue=True,
            offvalue=False,
        )
        if self._enable_experimental_tools:
            self.menu_config.add_separator()
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
                state=NORMAL if album_enabled else DISABLED,
            )
            self.menu_config.add_checkbutton(
                label=MENU_PODCAST_FOLDERS,
                variable=self.var_podcast_folders,
                onvalue=True,
                offvalue=False,
            )
            self.menu_config.add_checkbutton(
                label=MENU_ALLOW_VIDEO_PODCASTS,
                variable=self.var_allow_video_podcasts,
                onvalue=True,
                offvalue=False,
            )
            self.menu_config.add_checkbutton(
                label=MENU_AUDIO_PODCASTS_AS_VIDEO,
                variable=self.var_audio_podcasts_as_video,
                onvalue=True,
                offvalue=False,
            )
        self.menu_config.add_separator()
        self.menu_config.add_checkbutton(
            label=MENU_KEEP_DOWNLOADED_PODCASTS,
            variable=self.var_keep_downloaded_podcasts,
            onvalue=True,
            offvalue=False,
        )
        self.menu_config.add_command(label=MENU_REVEAL_PODCAST_DOWNLOADS)
        self.menu_config.add_command(label=MENU_CLEAR_DOWNLOADED_PODCASTS)
        self.menu_config.add_command(label=MENU_PODCAST_SETTINGS)
        self.menu_config.add_separator()
        self.menu_config.add_command(label=MENU_AUDIOBOOK_ENCODE)
        self.menu_config.add_separator()
        self.menu_config.add_command(label=MENU_CONFIG)
        self._apply_config_menu_commands()

    def set_track_context_commands(
        self,
        *,
        on_sync_track,
        on_sync_album,
        on_sync_artist,
        on_sync_artist_group,
        on_sync_album_group,
        on_sync_selected=None,
        on_special_sync=None,
        on_special_sync_group=None,
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
        self._track_ctx_cmds = {
            "on_sync_track": on_sync_track,
            "on_sync_album": on_sync_album,
            "on_sync_artist": on_sync_artist,
            "on_sync_artist_group": on_sync_artist_group,
            "on_sync_album_group": on_sync_album_group,
            "on_sync_selected": on_sync_selected,
            "on_special_sync": on_special_sync,
            "on_special_sync_group": on_special_sync_group,
            "on_play_track": on_play_track,
            "on_play_artist_group": on_play_artist_group,
            "on_play_album_group": on_play_album_group,
            "on_add_to_playlist": on_add_to_playlist,
            "on_add_artist_to_playlist": on_add_artist_to_playlist,
            "on_add_album_to_playlist": on_add_album_to_playlist,
            "on_exclude_file": on_exclude_file,
            "on_exclude_folder": on_exclude_folder,
            "on_exclude_group_folder": on_exclude_group_folder,
        }
        self._apply_track_context_commands(self._track_ctx_cmds)

    def set_playlist_tab_commands(
        self,
        *,
        on_combo_selected=None,
        on_list_selected=None,
        on_new=None,
        on_delete=None,
        on_rename=None,
        on_sync=None,
        on_remove_tracks=None,
        on_move_up=None,
        on_move_down=None,
        on_shuffle_artist=None,
        on_shuffle_spotify=None,
        on_play_track=None,
    ) -> None:
        """Wire Playlists tab toolbar + context menu."""
        # on_list_selected is preferred; on_combo_selected kept as alias.
        select_cb = on_list_selected if on_list_selected is not None else on_combo_selected
        if select_cb is not None:
            self.playlist_list_tree.bind(
                "<<TreeviewSelect>>", lambda _e: select_cb()
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
        if on_move_up is not None:
            self.btn_playlist_move_up.configure(command=on_move_up)
            self.menu_playlist_ctx.entryconfig(
                CTX_PLAYLIST_MOVE_UP, command=on_move_up
            )
        if on_move_down is not None:
            self.btn_playlist_move_down.configure(command=on_move_down)
            self.menu_playlist_ctx.entryconfig(
                CTX_PLAYLIST_MOVE_DOWN, command=on_move_down
            )
        if on_move_up is not None or on_move_down is not None:
            _bind_playlist_reorder_keys(
                self.playlist_tree,
                on_up=on_move_up,
                on_down=on_move_down,
            )
            # Also on the tab so a click on empty chrome still has a path when
            # the tree later receives focus; primary target remains the tree.
            try:
                _bind_playlist_reorder_keys(
                    self.playlists_tab,
                    on_up=on_move_up,
                    on_down=on_move_down,
                )
            except Exception:
                pass
        if on_shuffle_artist is not None:
            self.menu_playlist_shuffle.entryconfig(
                CTX_PLAYLIST_SHUFFLE_ARTIST, command=on_shuffle_artist
            )
        if on_shuffle_spotify is not None:
            self.menu_playlist_shuffle.entryconfig(
                CTX_PLAYLIST_SHUFFLE_SPOTIFY, command=on_shuffle_spotify
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
        ids: list[int] | None = None,
    ) -> None:
        """Refresh host playlist master list and selection.

        *names* are display labels. Optional *ids* (same length) become Treeview
        iids ``pln:{id}``; otherwise iids are ``pln:i:{index}``.
        """
        values = list(names or [])
        tree = self.playlist_list_tree
        for iid in tree.get_children(""):
            tree.delete(iid)
        if not values:
            self.var_playlist_choice.set("")
            self.btn_playlist_rename.configure(state=DISABLED)
            self.btn_playlist_delete.configure(state=DISABLED)
            self.btn_playlist_sync.configure(state=DISABLED)
            self.btn_playlist_move_up.configure(state=DISABLED)
            self.btn_playlist_move_down.configure(state=DISABLED)
            try:
                self.lbl_playlist_tracks.configure(text="Tracks")
            except Exception:
                pass
            return
        id_list = list(ids) if ids is not None else []
        for i, name in enumerate(values):
            if i < len(id_list) and id_list[i] is not None:
                iid = f"pln:{int(id_list[i])}"
            else:
                iid = f"pln:i:{i}"
            tree.insert("", "end", iid=iid, values=(name,))
        pick = selected if selected in values else values[0]
        self.var_playlist_choice.set(pick)
        # Select matching row by name (or first).
        pick_iid = ""
        for iid in tree.get_children(""):
            try:
                vals = tree.item(iid, "values")
            except Exception:
                vals = ()
            if vals and str(vals[0]) == pick:
                pick_iid = iid
                break
        if not pick_iid:
            kids = tree.get_children("")
            pick_iid = kids[0] if kids else ""
        if pick_iid:
            try:
                tree.selection_set(pick_iid)
                tree.focus(pick_iid)
                tree.see(pick_iid)
            except Exception:
                pass
        self.btn_playlist_rename.configure(state=NORMAL)
        self.btn_playlist_delete.configure(state=NORMAL)
        self.btn_playlist_sync.configure(state=NORMAL)
        self.btn_playlist_move_up.configure(state=NORMAL)
        self.btn_playlist_move_down.configure(state=NORMAL)
        try:
            self.lbl_playlist_tracks.configure(text=f"Tracks — {pick}")
        except Exception:
            pass

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

    def set_device_playlist_tab_commands(
        self,
        *,
        on_combo_selected=None,
        on_list_selected=None,
        on_new=None,
        on_delete=None,
        on_rename=None,
        on_refresh=None,
        on_recreate_local=None,
        on_remove_tracks=None,
        on_move_up=None,
        on_move_down=None,
        on_shuffle_artist=None,
        on_shuffle_spotify=None,
        on_play_track=None,
    ) -> None:
        """Wire Device → Playlists toolbar + context menu."""
        select_cb = on_list_selected if on_list_selected is not None else on_combo_selected
        if select_cb is not None:
            self.device_playlist_list_tree.bind(
                "<<TreeviewSelect>>", lambda _e: select_cb()
            )
        if on_new is not None:
            self.btn_device_playlist_new.configure(command=on_new)
        if on_delete is not None:
            self.btn_device_playlist_delete.configure(command=on_delete)
        if on_rename is not None:
            self.btn_device_playlist_rename.configure(command=on_rename)
        if on_refresh is not None:
            self.btn_device_playlist_refresh.configure(command=on_refresh)
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_REFRESH, command=on_refresh
            )
        if on_recreate_local is not None:
            self.btn_device_playlist_recreate.configure(command=on_recreate_local)
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_RECREATE_LOCAL, command=on_recreate_local
            )
        if on_remove_tracks is not None:
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_REMOVE, command=on_remove_tracks
            )
        if on_move_up is not None:
            self.btn_device_playlist_move_up.configure(command=on_move_up)
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_MOVE_UP, command=on_move_up
            )
        if on_move_down is not None:
            self.btn_device_playlist_move_down.configure(command=on_move_down)
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_MOVE_DOWN, command=on_move_down
            )
        if on_move_up is not None or on_move_down is not None:
            _bind_playlist_reorder_keys(
                self.device_playlist_tree,
                on_up=on_move_up,
                on_down=on_move_down,
            )
            try:
                _bind_playlist_reorder_keys(
                    self.device_playlists_tab,
                    on_up=on_move_up,
                    on_down=on_move_down,
                )
            except Exception:
                pass
        if on_shuffle_artist is not None:
            self.menu_device_playlist_shuffle.entryconfig(
                CTX_DEVICE_PLAYLIST_SHUFFLE_ARTIST, command=on_shuffle_artist
            )
        if on_shuffle_spotify is not None:
            self.menu_device_playlist_shuffle.entryconfig(
                CTX_DEVICE_PLAYLIST_SHUFFLE_SPOTIFY, command=on_shuffle_spotify
            )
        if on_play_track is not None:
            self.menu_device_playlist_ctx.entryconfig(
                CTX_DEVICE_PLAYLIST_PLAY_TRACK, command=on_play_track
            )

    def show_device_playlists_tab(self) -> None:
        """Select Device tab and the Playlists category subview."""
        try:
            self.media_notebook.select(self.device_tab)
            self.show_device_subview(self.device_playlists_tab)
        except Exception:
            pass

    def _on_device_category_combo(self, _event=None) -> None:
        label = (self.var_device_category.get() or "").strip()
        frame = self._device_subview_by_label.get(label)
        if frame is not None:
            self.show_device_subview(frame)

    def show_device_subview(self, tab_id) -> None:
        """Show one Device category frame (Music / Video / … / Playlists).

        *tab_id* may be a frame widget or ``str(frame)`` (Notebook-compatible).
        """
        frames = list(self._device_subview_by_label.values())
        target = None
        if tab_id is None:
            return
        if not isinstance(tab_id, str):
            target = tab_id
        else:
            for f in frames:
                if str(f) == tab_id:
                    target = f
                    break
            if target is None:
                # Label form ("Playlists") from combobox or tests.
                target = self._device_subview_by_label.get(tab_id)
        if target is None or target not in frames:
            return
        for f in frames:
            try:
                if f is not target and f.winfo_ismapped():
                    f.pack_forget()
            except Exception:
                pass
        try:
            if not target.winfo_ismapped():
                target.pack(fill=BOTH, expand=True)
        except Exception:
            try:
                target.pack(fill=BOTH, expand=True)
            except Exception:
                pass
        self._device_subview_frame = target
        # Keep combobox label in sync.
        for label, frame in self._device_subview_by_label.items():
            if frame is target:
                try:
                    if self.var_device_category.get() != label:
                        self.var_device_category.set(label)
                except Exception:
                    pass
                break

    def set_device_playlist_combo_values(
        self,
        names: list[str],
        *,
        selected: str = "",
        interactive: bool = True,
        playlist_ids: list[int] | None = None,
    ) -> None:
        """Refresh Device → Playlists master list and selection."""
        values = list(names or [])
        tree = self.device_playlist_list_tree
        for iid in tree.get_children(""):
            tree.delete(iid)
        # Always allow Refresh when a session can list playlists.
        refresh_state = NORMAL if interactive else DISABLED
        self.btn_device_playlist_refresh.configure(state=refresh_state)
        self.btn_device_playlist_new.configure(
            state=NORMAL if interactive else DISABLED
        )
        if not values:
            self.var_device_playlist_choice.set("")
            self.btn_device_playlist_rename.configure(state=DISABLED)
            self.btn_device_playlist_delete.configure(state=DISABLED)
            self.btn_device_playlist_recreate.configure(state=DISABLED)
            self.btn_device_playlist_move_up.configure(state=DISABLED)
            self.btn_device_playlist_move_down.configure(state=DISABLED)
            try:
                self.lbl_device_playlist_tracks.configure(text="Tracks")
            except Exception:
                pass
            return
        id_list = list(playlist_ids) if playlist_ids is not None else []
        for i, name in enumerate(values):
            if i < len(id_list) and id_list[i] is not None:
                iid = f"dpln:{int(id_list[i])}"
            else:
                iid = f"dpln:i:{i}"
            tree.insert("", "end", iid=iid, values=(name,))
        pick = selected if selected in values else values[0]
        self.var_device_playlist_choice.set(pick)
        pick_iid = ""
        for iid in tree.get_children(""):
            try:
                vals = tree.item(iid, "values")
            except Exception:
                vals = ()
            if vals and str(vals[0]) == pick:
                pick_iid = iid
                break
        if not pick_iid:
            kids = tree.get_children("")
            pick_iid = kids[0] if kids else ""
        if pick_iid:
            try:
                tree.selection_set(pick_iid)
                tree.focus(pick_iid)
                tree.see(pick_iid)
            except Exception:
                pass
        btn_state = NORMAL if interactive else DISABLED
        self.btn_device_playlist_rename.configure(state=btn_state)
        self.btn_device_playlist_delete.configure(state=btn_state)
        self.btn_device_playlist_recreate.configure(state=btn_state)
        self.btn_device_playlist_move_up.configure(state=btn_state)
        self.btn_device_playlist_move_down.configure(state=btn_state)
        try:
            self.lbl_device_playlist_tracks.configure(text=f"Tracks — {pick}")
        except Exception:
            pass

    def clear_device_playlist_tree(self) -> None:
        tree = self.device_playlist_tree
        for iid in tree.get_children(""):
            tree.delete(iid)

    def popup_device_playlist_context(self, event) -> str | None:
        """Context menu for Device → Playlists tree."""
        menu = self.menu_device_playlist_ctx
        try:
            tree = self.device_playlist_tree
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

    def set_library_search_commands(
        self,
        *,
        on_change=None,
        on_clear=None,
    ) -> None:
        """Wire library toolbar search entry (debounced change handled by controller)."""
        self._on_library_search_change = on_change
        self._on_library_search_clear = on_clear
        if on_change is not None:
            self.var_library_search.trace_add(
                "write", lambda *_a: on_change()
            )
        if on_clear is not None:
            self.btn_library_search_clear.configure(command=on_clear)
            self.entry_library_search.bind("<Escape>", lambda _e: on_clear())
        # Focus search: Cmd/Ctrl+F (macOS Command is often Meta/Command).
        try:
            self.root.bind_all("<Control-f>", self._focus_library_search)
            self.root.bind_all("<Command-f>", self._focus_library_search)
            self.root.bind_all("<Meta-f>", self._focus_library_search)
        except Exception:
            pass

    def _focus_library_search(self, _event=None):
        try:
            self.entry_library_search.focus_set()
            self.entry_library_search.selection_range(0, END)
        except Exception:
            pass
        return "break"

    def library_search_query(self) -> str:
        try:
            return str(self.var_library_search.get() or "")
        except Exception:
            return ""

    def set_library_search_query(self, text: str) -> None:
        try:
            self.var_library_search.set(text or "")
        except Exception:
            pass

    def set_library_search_clear_enabled(self, enabled: bool) -> None:
        try:
            self.btn_library_search_clear.configure(
                state=NORMAL if enabled else DISABLED
            )
        except Exception:
            pass

    def set_library_status(
        self,
        root_path: str = "",
        track_count: int = 0,
        *,
        root_paths: list[str] | None = None,
        root_reachable: bool = True,
        busy_message: str | None = None,
        shown_count: int | None = None,
        filter_active: bool = False,
    ) -> None:
        """Update toolbar path label and track count.

        When multiple *root_paths* are present, the label shows
        ``Multiple Library Roots`` and the hover tip lists every root.
        A single root shows an elided path (full path on hover).

        When *busy_message* is set (e.g. during a background scan), the count
        label shows that status instead of a numeric track total.

        When *filter_active* and *shown_count* are set, shows
        ``N of M tracks`` for the fuzzy search filter.
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
        if filter_active and shown_count is not None:
            self.lbl_library_count.configure(
                text=f"{shown_count} of {track_count} tracks"
            )
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

    def clear_device_podcasts_tree(self) -> None:
        """Clear Device → Podcasts tree."""
        try:
            self.device_podcasts_tree.delete(
                *self.device_podcasts_tree.get_children()
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
        """All on-device Treeviews (Music, Video, Audiobooks, Podcasts)."""
        return (
            self.device_tree,
            self.device_video_tree,
            self.device_audiobooks_tree,
            self.device_podcasts_tree,
        )

    def active_device_tree(self):
        """Treeview for the selected device media category."""
        try:
            current = self._device_subview_frame
        except Exception:
            return self.device_tree
        try:
            if current is self.device_video_tab:
                return self.device_video_tree
            if current is self.device_audiobooks_tab:
                return self.device_audiobooks_tree
            if current is self.device_podcasts_tab:
                return self.device_podcasts_tree
            if current is self.device_playlists_tab:
                return self.device_playlist_tree
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
        on_fetch_tags=None,
        on_track_info=None,
        on_add_to_playlist=None,
        on_shrink=None,
        on_delete_artist=None,
        on_delete_album=None,
        on_delete_folder=None,
        on_add_artist_to_playlist=None,
        on_add_album_to_playlist=None,
        on_add_folder_to_playlist=None,
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
        if on_fetch_tags is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_FETCH_TAGS, command=on_fetch_tags
            )
        if on_shrink is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_SHRINK, command=on_shrink
            )
            self.menu_device_artist_ctx.entryconfig(
                CTX_DEVICE_SHRINK, command=on_shrink
            )
            self.menu_device_album_ctx.entryconfig(
                CTX_DEVICE_SHRINK, command=on_shrink
            )
        if on_track_info is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_TRACK_INFO, command=on_track_info
            )
        if on_add_to_playlist is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_ADD_TO_PLAYLIST, command=on_add_to_playlist
            )
        if on_delete is not None:
            self.menu_device_track_ctx.entryconfig(
                CTX_DEVICE_DELETE, command=on_delete
            )
        if on_add_artist_to_playlist is not None:
            self.menu_device_artist_ctx.entryconfig(
                CTX_DEVICE_ADD_ARTIST_TO_PLAYLIST,
                command=on_add_artist_to_playlist,
            )
        if on_delete_artist is not None:
            # Layout: Add, Shrink, separator, Delete — never index a separator.
            self.menu_device_artist_ctx.entryconfig(
                CTX_DEVICE_DELETE_ARTIST, command=on_delete_artist
            )
        if on_add_album_to_playlist is not None:
            self.menu_device_album_ctx.entryconfig(
                CTX_DEVICE_ADD_ALBUM_TO_PLAYLIST,
                command=on_add_album_to_playlist,
            )
        if on_delete_album is not None:
            self.menu_device_album_ctx.entryconfig(
                CTX_DEVICE_DELETE_ALBUM, command=on_delete_album
            )
        if on_add_folder_to_playlist is not None:
            self.menu_device_folder_ctx.entryconfig(
                CTX_DEVICE_ADD_FOLDER_TO_PLAYLIST,
                command=on_add_folder_to_playlist,
            )
        if on_delete_folder is not None:
            self.menu_device_folder_ctx.entryconfig(
                CTX_DEVICE_DELETE_FOLDER, command=on_delete_folder
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
        self._cancel_job_enabled = bool(enabled)
        state = NORMAL if enabled else DISABLED
        try:
            self.btn_cancel_job.configure(
                state=state,
                text="Cancel",
            )
        except Exception:
            pass
        self._menu_entryconfig(self.menu_transfer, MENU_CANCEL_JOB, state=state)

    def apply_mode_actions(self) -> None:
        """Enable Device menu only when PyMTP (non-Stable) is active."""
        pymtp = self.active_mode() == "experimental"
        state = NORMAL if pymtp else DISABLED
        labels = _device_menu_labels(
            experimental_tools=self._enable_experimental_tools
        )
        for label in labels:
            self._menu_entryconfig(self.menu_device, label, state=state)

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
