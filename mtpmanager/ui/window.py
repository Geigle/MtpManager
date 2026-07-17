"""Tk layout only — widgets and packing."""

from __future__ import annotations

from typing import Literal

from pathlib import Path

from tkinter import (
    BOTH,
    BOTTOM,
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
    Listbox,
    Menu,
    PhotoImage,
    Scrollbar,
    Tk,
    ttk,
)

Mode = Literal["stable", "experimental"]

FORMAT_OPTIONS = ("MP3", "WMA")

_PATH_DISPLAY_MAX = 72
_DEAD_TRACK_FG = "gray50"

# Desaturated transfer-state backgrounds (listbox itemconfig).
# Selection highlight (blue) remains for the active selection; these tint the row.
BG_TRANSFER_QUEUED = "#b8cbb8"  # desaturated green — in batch, waiting
BG_TRANSFER_TRANSCODING = "#8faf8f"  # desaturated green — converting
BG_TRANSFER_TRANSFERRING = "#bf8f8f"  # desaturated red — sending to device

# Library menu labels (used for entryconfig by label).
MENU_SELECT_ROOT = "Select Library Root…"
MENU_UPDATE_LIBRARY = "Update Library"

# Transfer menu
MENU_SYNC_ENTIRE = "Sync Entire Library"
MENU_SYNC_FOLDER = "Sync Folder…"

# Device menu (Experimental)
MENU_CONNECT = "Connect"
MENU_DISCONNECT = "Disconnect"
MENU_DEVICE_INFO = "Device Info"
MENU_CREATE_FOLDER = "Create Folder…"
MENU_LIST_FOLDERS = "List Folders"
MENU_GET_FILE_INFO = "Get File Info…"
MENU_DELETE_ALL = "Delete All Tracks…"

# Track context menu
CTX_SYNC_TRACK = "Sync this track"
CTX_SYNC_ALBUM = "Sync Album"
CTX_SYNC_ARTIST = "Sync all from Artist"

_DEVICE_MENU_LABELS = (
    MENU_CONNECT,
    MENU_DISCONNECT,
    MENU_DEVICE_INFO,
    MENU_CREATE_FOLDER,
    MENU_LIST_FOLDERS,
    MENU_GET_FILE_INFO,
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

        # Menubar: Library | Transfer | Device
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

        self.menu_device = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Device", menu=self.menu_device)
        for label in _DEVICE_MENU_LABELS:
            self.menu_device.add_command(label=label, state=DISABLED)

        # Track list context menu (commands wired by controller).
        self.menu_track_ctx = Menu(self.root, tearoff=0)
        self.menu_track_ctx.add_command(label=CTX_SYNC_TRACK)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ALBUM)
        self.menu_track_ctx.add_command(label=CTX_SYNC_ARTIST)

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

        body = Frame(self.root)
        body.pack(side=TOP, fill=BOTH, expand=True)

        leftframe = Frame(body)
        leftframe["borderwidth"] = 3
        leftframe["relief"] = "sunken"
        leftframe.pack(side=LEFT, fill=Y)

        rightframe = Frame(body)
        rightframe["borderwidth"] = 3
        rightframe["relief"] = "sunken"
        rightframe.pack(side=RIGHT, fill=BOTH, expand=True)

        bottomframe = Frame(self.root)
        bottomframe["borderwidth"] = 3
        bottomframe["relief"] = "sunken"
        bottomframe.pack(side=BOTTOM, fill=X)

        # Mode tabs: Stable (CMD) first, Experimental (PyMTP) second.
        self.notebook = ttk.Notebook(leftframe)
        self.notebook.pack(padx=3, pady=3, fill=X)

        stable_tab = Frame(self.notebook)
        experimental_tab = Frame(self.notebook)
        self.notebook.add(stable_tab, text="Stable Mode")
        self.notebook.add(experimental_tab, text="Experimental Mode")

        Label(
            stable_tab,
            text="Transfers via mtp-sendtr (recommended).",
            wraplength=180,
            justify=LEFT,
        ).pack(padx=6, pady=6, anchor="w")

        Label(
            experimental_tab,
            text="PyMTP / libmtp device tools and experimental send.",
            wraplength=180,
            justify=LEFT,
        ).pack(padx=6, pady=6, anchor="w")

        # Device session status + auto-detect graphic (connect/disconnect via Device menu).
        Label(experimental_tab, text="Device", font=("", 11, "bold")).pack(
            padx=6, pady=(4, 0), anchor="w"
        )
        self.lbl_device_caption = Label(
            experimental_tab, text="", wraplength=180, justify=LEFT
        )
        self.lbl_device_caption.pack(padx=6, pady=(6, 0), anchor="w")
        self.lbl_device_graphic = Label(experimental_tab)
        self.lbl_device_graphic.pack(padx=6, pady=6)
        self._device_photo: PhotoImage | None = None
        self._device_photo_cache: dict[str, PhotoImage] = {}

        # Global format preference (all Sync actions).
        format_frame = Frame(leftframe)
        format_frame.pack(padx=3, pady=6, fill=X)
        Label(format_frame, text="Send as", font=("", 11, "bold")).pack(
            padx=3, pady=(0, 2), anchor="w"
        )
        self.format_combo = ttk.Combobox(
            format_frame, values=FORMAT_OPTIONS, state="readonly", width=18
        )
        self.format_combo.set(FORMAT_OPTIONS[0])
        self.format_combo.pack(padx=3, pady=3, anchor="w")

        Label(
            leftframe,
            text="Right-click a track to sync.",
            wraplength=180,
            justify=LEFT,
        ).pack(padx=6, pady=4, anchor="w")

        Label(rightframe, text="Tracks").pack()
        tscroll = Scrollbar(rightframe)
        tscroll.pack(side=RIGHT, fill=Y)
        self.listbox = Listbox(rightframe, yscrollcommand=tscroll.set)
        self.listbox.pack(fill=BOTH, expand=True)
        tscroll.config(command=self.listbox.yview)

        self.progress = ttk.Progressbar(bottomframe)
        self.progress.pack(side=BOTTOM, fill=X)

        # Ensure Stable Mode is the first-run selection.
        self.notebook.select(0)
        self.apply_mode_actions()

    def active_mode(self) -> Mode:
        try:
            idx = self.notebook.index(self.notebook.select())
        except Exception:
            return "stable"
        return "stable" if idx == 0 else "experimental"

    def target_format(self) -> str:
        """Lowercase format extension from the global Send as control."""
        raw = (self.format_combo.get() or "MP3").strip().lower()
        return raw if raw in ("mp3", "wma") else "mp3"

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
    ) -> None:
        self.menu_transfer.entryconfig(MENU_SYNC_ENTIRE, command=on_sync_entire)
        self.menu_transfer.entryconfig(MENU_SYNC_FOLDER, command=on_sync_folder)

    def set_device_menu_commands(
        self,
        *,
        on_connect,
        on_disconnect,
        on_device_info,
        on_create_folder,
        on_list_folders,
        on_get_file_info,
        on_delete_all,
    ) -> None:
        self.menu_device.entryconfig(MENU_CONNECT, command=on_connect)
        self.menu_device.entryconfig(MENU_DISCONNECT, command=on_disconnect)
        self.menu_device.entryconfig(MENU_DEVICE_INFO, command=on_device_info)
        self.menu_device.entryconfig(MENU_CREATE_FOLDER, command=on_create_folder)
        self.menu_device.entryconfig(MENU_LIST_FOLDERS, command=on_list_folders)
        self.menu_device.entryconfig(MENU_GET_FILE_INFO, command=on_get_file_info)
        self.menu_device.entryconfig(MENU_DELETE_ALL, command=on_delete_all)

    def set_track_context_commands(
        self,
        *,
        on_sync_track,
        on_sync_album,
        on_sync_artist,
    ) -> None:
        self.menu_track_ctx.entryconfig(CTX_SYNC_TRACK, command=on_sync_track)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ALBUM, command=on_sync_album)
        self.menu_track_ctx.entryconfig(CTX_SYNC_ARTIST, command=on_sync_artist)

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

    def set_tracks_usable(self, usable: bool) -> None:
        """Enable listbox interaction, or grey out and disable when media is dead.

        Call after the listbox has been populated. When *usable* is False,
        entries stay visible but cannot be selected for transfer.
        """
        # Must re-enable before itemconfig when recovering from disabled.
        self.listbox.configure(state=NORMAL)
        size = self.listbox.size()
        if usable:
            for i in range(size):
                self.listbox.itemconfig(i, fg="")
            return
        for i in range(size):
            self.listbox.itemconfig(i, fg=_DEAD_TRACK_FG)
        if size > 0:
            self.listbox.configure(state=DISABLED)

    def set_track_transfer_style(self, index: int, status: str | None) -> None:
        """Tint a listbox row for transfer state; *status* None/done/failed clears.

        Status values: ``queued``, ``transcoding``, ``transferring``,
        ``done``, ``failed``, or None to clear.
        """
        size = self.listbox.size()
        if index < 0 or index >= size:
            return
        # itemconfig requires a normal-state listbox
        was_disabled = str(self.listbox.cget("state")) == str(DISABLED)
        if was_disabled:
            return
        if status in (None, "done", "failed", ""):
            self.listbox.itemconfig(index, bg="", selectbackground="")
            return
        if status == "transferring":
            color = BG_TRANSFER_TRANSFERRING
        elif status == "transcoding":
            color = BG_TRANSFER_TRANSCODING
        else:
            # queued / unknown → desaturated green
            color = BG_TRANSFER_QUEUED
        self.listbox.itemconfig(index, bg=color, selectbackground=color)

    def clear_transfer_styles(self) -> None:
        """Clear all transfer tinting from listbox rows."""
        was_disabled = str(self.listbox.cget("state")) == str(DISABLED)
        if was_disabled:
            return
        for i in range(self.listbox.size()):
            self.listbox.itemconfig(i, bg="", selectbackground="")

    def popup_track_context(self, event) -> str | None:
        """Select the row under the pointer and show the track context menu."""
        try:
            if str(self.listbox.cget("state")) == str(DISABLED):
                return "break"
            idx = self.listbox.nearest(event.y)
            if idx < 0 or idx >= self.listbox.size():
                return "break"
            self.listbox.selection_clear(0, END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self.listbox.see(idx)
            self.menu_track_ctx.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu_track_ctx.grab_release()
            except Exception:
                pass
        return "break"

    def apply_mode_actions(self) -> None:
        """Enable Device menu only in Experimental mode."""
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
        max_width: int = 180,
    ) -> None:
        """Show device art on the Experimental tab, or clear when *image_path* is None."""
        if image_path is None:
            self._device_photo = None
            self.lbl_device_graphic.configure(image="")
            self.lbl_device_caption.configure(text="")
            return

        path = Path(image_path)
        key = f"{path.resolve()}:{max_width}"
        photo = self._device_photo_cache.get(key)
        if photo is None:
            if not path.is_file():
                self.set_device_graphic(None)
                return
            try:
                raw = PhotoImage(file=str(path))
                # Downscale large PNGs to fit the left column.
                factor = max(1, int(raw.width() / max_width))
                photo = raw.subsample(factor, factor) if factor > 1 else raw
                self._device_photo_cache[key] = photo
            except Exception:
                self.set_device_graphic(None)
                return

        self._device_photo = photo  # prevent GC
        self.lbl_device_graphic.configure(image=photo)
        self.lbl_device_caption.configure(text=caption or "")

    def mainloop(self) -> None:
        self.root.mainloop()
