"""Modal dialogs for device admin (no persistent main-window entry field)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    Listbox,
    Menu,
    Radiobutton,
    Scrollbar,
    StringVar,
    Toplevel,
    Y,
    messagebox,
    simpledialog,
)
from tkinter import ttk

from mtpmanager.domain.device_profile import DeviceVideoOptions, VideoEncodePreset
from mtpmanager.domain.models import DeviceInfo
from mtpmanager.infra.app_config import VALID_SEND_FORMATS
from mtpmanager.infra.remote_naming import (
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    ZEN_VISION_M_FOLDER_IDS,
)
from mtpmanager.ui.formatting import folder_line


def ask_text(
    parent,
    *,
    title: str,
    prompt: str,
    initialvalue: str = "",
) -> str | None:
    """Return stripped text, or None if cancelled / empty after strip."""
    raw = simpledialog.askstring(
        title,
        prompt,
        parent=parent,
        initialvalue=initialvalue,
    )
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def show_device_info_dialog(
    parent,
    info: DeviceInfo,
    *,
    apply_name: Callable[[str], None],
) -> None:
    """Modal Device Info dialog with editable name.

    On Close: if the name field differs from the original and is non-empty,
    call *apply_name(new_name)*. On rename failure, keep the dialog open.
    """
    original = (info.name or "").strip()
    dlg = Toplevel(parent)
    dlg.title("Device Info")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    # Name (editable)
    row_name = Frame(body)
    row_name.pack(fill="x", pady=2)
    Label(row_name, text="Name:", width=14, anchor="w").pack(side=LEFT)
    name_entry = Entry(row_name, width=40)
    name_entry.pack(side=LEFT, fill="x", expand=True)
    name_entry.insert(0, original)
    name_entry.focus_set()

    used_mb = (info.used or 0) / 1_000_000
    total_mb = (info.total or 0) / 1_000_000
    readonly_rows = (
        ("Serial", info.serial or ""),
        ("Manufacturer", info.manufacturer or ""),
        ("Model", info.model or ""),
        ("Version", info.version or ""),
        ("Battery", "" if info.battery is None else str(info.battery)),
        ("Used", f"{used_mb:.2f} / {total_mb:.2f} MB"),
        ("Used %", f"{info.used_percent:.2f}"),
        ("Free", str(info.free)),
    )
    for label, value in readonly_rows:
        row = Frame(body)
        row.pack(fill="x", pady=2)
        Label(row, text=f"{label}:", width=14, anchor="w").pack(side=LEFT)
        Label(row, text=value, anchor="w").pack(side=LEFT, fill="x", expand=True)

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(12, 0))

    def try_close() -> None:
        new_name = name_entry.get().strip()
        if new_name and new_name != original:
            try:
                apply_name(new_name)
            except Exception as e:
                messagebox.showerror("Device Name", str(e), parent=dlg)
                return
        dlg.destroy()

    Button(btn_row, text="Close", width=10, command=try_close).pack(side=RIGHT)
    dlg.protocol("WM_DELETE_WINDOW", try_close)

    dlg.grab_set()
    dlg.update_idletasks()
    # Center roughly over parent
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dlg.winfo_width()) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dlg.winfo_height()) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)


@dataclass(frozen=True)
class ConfigDialogResult:
    """Values saved from Config → Config…"""

    send_format: str
    show_broken_video_presets: bool = False


def show_config_dialog(
    parent,
    *,
    send_format: str,
    show_broken_video_presets: bool = False,
) -> ConfigDialogResult | None:
    """Edit app preferences. Returns result on Save, or None if cancelled.

    Transfer mode (Stable vs PyMTP) is a separate Config menu checkbutton.
    """
    from tkinter import ttk

    initial = (send_format or "mp3").lower().lstrip(".")
    if initial not in VALID_SEND_FORMATS:
        initial = "mp3"

    dlg = Toplevel(parent)
    dlg.title("Config")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    Label(body, text="Output format when conversion is needed:").pack(anchor="w")
    fmt_var = StringVar(value=initial.upper())
    combo = ttk.Combobox(
        body,
        textvariable=fmt_var,
        values=("MP3", "WMA", "WAV"),
        state="readonly",
        width=12,
    )
    combo.pack(anchor="w", pady=(6, 8))

    Label(
        body,
        text=(
            "Tracks already in a device-supported format "
            "(e.g. MP3/WMA/WAV on ZEN Vision:M) are sent as-is.\n\n"
            "Transfer engine is under Config → Stable Mode:\n"
            "off = PyMTP (default, Device menu + auto-connect);\n"
            "on = mtp-sendtr subprocess per track."
        ),
        justify=LEFT,
        wraplength=360,
    ).pack(anchor="w", pady=(0, 12))

    Label(
        body,
        text="Send Video (device-specific)",
        font=("", 11, "bold"),
        anchor="w",
    ).pack(fill="x", pady=(4, 2))

    broken_var = BooleanVar(value=bool(show_broken_video_presets))
    Checkbutton(
        body,
        text="Show broken video encode presets (experimental)",
        variable=broken_var,
        anchor="w",
        justify=LEFT,
    ).pack(fill="x", pady=(0, 2))
    Label(
        body,
        text=(
            "When enabled, Device → Send Video can offer recipes marked "
            "broken (e.g. ZEN Vision:M WMV · WMA). They are hidden by "
            "default because they do not play reliably."
        ),
        justify=LEFT,
        wraplength=360,
    ).pack(anchor="w", pady=(0, 12))

    result: list[ConfigDialogResult | None] = [None]

    def on_save() -> None:
        raw = (fmt_var.get() or "MP3").strip().lower()
        if raw not in VALID_SEND_FORMATS:
            messagebox.showerror("Config", f"Invalid format: {raw}", parent=dlg)
            return
        result[0] = ConfigDialogResult(
            send_format=raw,
            show_broken_video_presets=bool(broken_var.get()),
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x")
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Save", width=10, command=on_save).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 380) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 280) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]


class ManageLibraryDialog:
    """Modeless Library roots manager (add / remove / rescan).

    Kept modeless so background scan/update can finish and refresh the list
    without blocking the main window.
    """

    def __init__(
        self,
        parent,
        *,
        get_roots: Callable[[], list[str]],
        on_add: Callable[[], None],
        on_remove: Callable[[list[str]], None],
        on_update: Callable[[], None],
        is_busy: Callable[[], bool],
        can_update: Callable[[], bool],
        on_exclusions: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._get_roots = get_roots
        self._on_add = on_add
        self._on_remove = on_remove
        self._on_update = on_update
        self._is_busy = is_busy
        self._can_update = can_update
        self._on_exclusions = on_exclusions
        self._on_close = on_close

        dlg = Toplevel(parent)
        dlg.title("Manage Library")
        dlg.transient(parent)
        dlg.minsize(480, 320)
        dlg.geometry("560x360")
        self._dlg = dlg

        body = Frame(dlg, padx=14, pady=12)
        body.pack(fill=BOTH, expand=True)

        Label(
            body,
            text="Library roots — folders scanned into the track list.",
            anchor="w",
            justify=LEFT,
        ).pack(fill="x")

        list_frame = Frame(body)
        list_frame.pack(fill=BOTH, expand=True, pady=(8, 8))
        scroll = Scrollbar(list_frame)
        scroll.pack(side=RIGHT, fill=Y)
        self._lb = Listbox(
            list_frame,
            yscrollcommand=scroll.set,
            selectmode="extended",
            activestyle="dotbox",
            exportselection=False,
        )
        self._lb.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=self._lb.yview)
        try:
            self._lb.configure(font=("Menlo", 11))
        except Exception:
            try:
                self._lb.configure(font=("Courier", 11))
            except Exception:
                pass

        self._status = Label(body, text="", anchor="w", justify=LEFT)
        self._status.pack(fill="x", pady=(0, 8))

        row = Frame(body)
        row.pack(fill="x")
        self._btn_add = Button(row, text="Add Root…", width=12, command=self._click_add)
        self._btn_add.pack(side=LEFT)
        self._btn_remove = Button(
            row, text="Remove Selected", width=14, command=self._click_remove
        )
        self._btn_remove.pack(side=LEFT, padx=(8, 0))
        self._btn_update = Button(
            row, text="Update Library", width=14, command=self._click_update
        )
        self._btn_update.pack(side=LEFT, padx=(8, 0))
        self._btn_exclusions = Button(
            row, text="Exclusions…", width=12, command=self._click_exclusions
        )
        self._btn_exclusions.pack(side=LEFT, padx=(8, 0))
        if on_exclusions is None:
            self._btn_exclusions.configure(state=DISABLED)
        Button(row, text="Close", width=10, command=self.close).pack(side=RIGHT)

        dlg.protocol("WM_DELETE_WINDOW", self.close)
        self._lb.bind("<Delete>", lambda _e: self._click_remove())
        self._lb.bind("<BackSpace>", lambda _e: self._click_remove())

        try:
            px = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - 560) // 2
            )
            py = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - 360) // 3
            )
            dlg.geometry(f"+{px}+{py}")
        except Exception:
            pass

        self.refresh()
        try:
            dlg.focus_set()
        except Exception:
            pass

    @property
    def window(self) -> Toplevel:
        """Underlying Tk window (for parenting file pickers / messageboxes)."""
        return self._dlg

    def is_open(self) -> bool:
        try:
            return bool(self._dlg.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        if not self.is_open():
            return
        try:
            self._dlg.lift()
            self._dlg.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        dlg = self._dlg
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def refresh(self) -> None:
        """Reload root list and button enablement from callbacks."""
        if not self.is_open():
            return
        roots = list(self._get_roots() or [])
        selected = set(self._selected_paths())
        self._lb.delete(0, END)
        for path in roots:
            self._lb.insert(END, path)
            if path in selected:
                self._lb.selection_set(END)

        busy = False
        try:
            busy = bool(self._is_busy())
        except Exception:
            busy = False
        can_up = False
        try:
            can_up = bool(self._can_update())
        except Exception:
            can_up = False

        if busy:
            self._status.configure(text="Library is scanning or a job is running…")
        elif not roots:
            self._status.configure(text="No roots yet. Add a folder to build the library.")
        elif not can_up:
            self._status.configure(
                text="No reachable roots — reconnect volumes or add another folder."
            )
        else:
            n = len(roots)
            self._status.configure(
                text=f"{n} library root{'s' if n != 1 else ''}."
            )

        add_state = DISABLED if busy else NORMAL
        rem_state = DISABLED if busy or not roots else NORMAL
        upd_state = DISABLED if busy or not can_up else NORMAL
        try:
            self._btn_add.configure(state=add_state)
            self._btn_remove.configure(state=rem_state)
            self._btn_update.configure(state=upd_state)
        except Exception:
            pass

    def _selected_paths(self) -> list[str]:
        try:
            idxs = self._lb.curselection()
        except Exception:
            return []
        out: list[str] = []
        for i in idxs:
            try:
                out.append(str(self._lb.get(i)))
            except Exception:
                continue
        return out

    def _click_add(self) -> None:
        if self._is_busy():
            return
        self._on_add()

    def _click_remove(self) -> None:
        if self._is_busy():
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo(
                "Manage Library",
                "Select one or more roots to remove.",
                parent=self._dlg,
            )
            return
        if len(paths) == 1:
            msg = f"Remove this library root?\n\n{paths[0]}"
        else:
            msg = f"Remove {len(paths)} library roots?"
        if not messagebox.askyesno("Remove Library Root", msg, parent=self._dlg):
            return
        self._on_remove(paths)

    def _click_update(self) -> None:
        if self._is_busy() or not self._can_update():
            return
        self._on_update()

    def _click_exclusions(self) -> None:
        if self._on_exclusions is None:
            return
        self._on_exclusions()


def open_manage_library_dialog(
    parent,
    *,
    get_roots: Callable[[], list[str]],
    on_add: Callable[[], None],
    on_remove: Callable[[list[str]], None],
    on_update: Callable[[], None],
    is_busy: Callable[[], bool],
    can_update: Callable[[], bool],
    on_exclusions: Callable[[], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ManageLibraryDialog:
    """Open (or the caller reuses) the Manage Library window."""
    return ManageLibraryDialog(
        parent,
        get_roots=get_roots,
        on_add=on_add,
        on_remove=on_remove,
        on_update=on_update,
        is_busy=is_busy,
        can_update=can_update,
        on_exclusions=on_exclusions,
        on_close=on_close,
    )


class ExclusionsManagerDialog:
    """Modeless list of excluded file/folder paths with de-exclude actions."""

    def __init__(
        self,
        parent,
        *,
        get_exclusions: Callable[[], list[tuple[str, str]]],
        on_remove: Callable[[list[str]], None],
        is_busy: Callable[[], bool],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._get_exclusions = get_exclusions
        self._on_remove = on_remove
        self._is_busy = is_busy
        self._on_close = on_close
        # Display label → absolute path for selection mapping.
        self._path_by_display: dict[str, str] = {}

        dlg = Toplevel(parent)
        dlg.title("Exclusions Manager")
        dlg.transient(parent)
        dlg.minsize(520, 340)
        dlg.geometry("640x400")
        self._dlg = dlg

        body = Frame(dlg, padx=14, pady=12)
        body.pack(fill=BOTH, expand=True)

        Label(
            body,
            text=(
                "Excluded paths are skipped when scanning and removed from the "
                "library list. GUIDs stay in the index for device joins."
            ),
            anchor="w",
            justify=LEFT,
            wraplength=600,
        ).pack(fill="x")

        list_frame = Frame(body)
        list_frame.pack(fill=BOTH, expand=True, pady=(8, 8))
        scroll = Scrollbar(list_frame)
        scroll.pack(side=RIGHT, fill=Y)
        self._lb = Listbox(
            list_frame,
            yscrollcommand=scroll.set,
            selectmode="extended",
            activestyle="dotbox",
            exportselection=False,
        )
        self._lb.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=self._lb.yview)
        try:
            self._lb.configure(font=("Menlo", 11))
        except Exception:
            try:
                self._lb.configure(font=("Courier", 11))
            except Exception:
                pass

        self._status = Label(body, text="", anchor="w", justify=LEFT)
        self._status.pack(fill="x", pady=(0, 8))

        row = Frame(body)
        row.pack(fill="x")
        self._btn_remove = Button(
            row,
            text="Remove from Exclusions",
            width=20,
            command=self._click_remove,
        )
        self._btn_remove.pack(side=LEFT)
        Button(row, text="Close", width=10, command=self.close).pack(side=RIGHT)

        self._menu = Menu(dlg, tearoff=0)
        self._menu.add_command(
            label="Remove from exclusions",
            command=self._click_remove,
        )
        self._lb.bind("<Button-3>", self._popup_menu)
        self._lb.bind("<Button-2>", self._popup_menu)
        self._lb.bind("<Delete>", lambda _e: self._click_remove())
        self._lb.bind("<BackSpace>", lambda _e: self._click_remove())
        self._lb.bind("<Double-Button-1>", lambda _e: self._click_remove())

        dlg.protocol("WM_DELETE_WINDOW", self.close)
        try:
            px = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - 640) // 2
            )
            py = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - 400) // 3
            )
            dlg.geometry(f"+{px}+{py}")
        except Exception:
            pass

        self.refresh()
        try:
            dlg.focus_set()
        except Exception:
            pass

    @property
    def window(self) -> Toplevel:
        return self._dlg

    def is_open(self) -> bool:
        try:
            return bool(self._dlg.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        if not self.is_open():
            return
        try:
            self._dlg.lift()
            self._dlg.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        dlg = self._dlg
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def refresh(self) -> None:
        if not self.is_open():
            return
        selected = set(self._selected_paths())
        rows = list(self._get_exclusions() or [])
        self._path_by_display.clear()
        self._lb.delete(0, END)
        for path, kind in rows:
            label = f"[{kind}] {path}"
            self._path_by_display[label] = path
            self._lb.insert(END, label)
            if path in selected:
                self._lb.selection_set(END)

        busy = False
        try:
            busy = bool(self._is_busy())
        except Exception:
            busy = False
        n = len(rows)
        if busy:
            self._status.configure(text="Library is busy…")
        elif n == 0:
            self._status.configure(text="No exclusions. Right-click media to exclude.")
        else:
            self._status.configure(
                text=f"{n} exclusion{'s' if n != 1 else ''}."
            )
        rem_state = DISABLED if busy or n == 0 else NORMAL
        try:
            self._btn_remove.configure(state=rem_state)
        except Exception:
            pass

    def _selected_paths(self) -> list[str]:
        try:
            idxs = self._lb.curselection()
        except Exception:
            return []
        out: list[str] = []
        for i in idxs:
            try:
                label = str(self._lb.get(i))
            except Exception:
                continue
            path = self._path_by_display.get(label)
            if path:
                out.append(path)
        return out

    def _popup_menu(self, event) -> str | None:
        try:
            idx = self._lb.nearest(event.y)
            if idx >= 0 and idx not in self._lb.curselection():
                self._lb.selection_clear(0, END)
                self._lb.selection_set(idx)
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass
        return "break"

    def _click_remove(self) -> None:
        if self._is_busy():
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo(
                "Exclusions Manager",
                "Select one or more exclusions to remove.",
                parent=self._dlg,
            )
            return
        if len(paths) == 1:
            msg = (
                "Stop excluding this path?\n\n"
                f"{paths[0]}\n\n"
                "It will be scanned again if it is still under a library root."
            )
        else:
            msg = (
                f"Stop excluding {len(paths)} paths?\n\n"
                "They will be scanned again if still under a library root."
            )
        if not messagebox.askyesno(
            "Remove Exclusion", msg, parent=self._dlg
        ):
            return
        self._on_remove(paths)



@dataclass(frozen=True)
class SendVideoDialogResult:
    """User choices from Device → Send Video…"""

    parent_id: int
    encode_for_device: bool
    # Selected VideoEncodePreset.id when encoding with device options.
    preset_id: str | None = None
    # When True, encode ignores preset.max_fps (e.g. try 60 fps on ZEN).
    ignore_max_fps: bool = False


def _fill_preset_panel(parent: Frame, preset: VideoEncodePreset) -> None:
    """Render container / video / audio detail blocks for one preset tab."""
    for child in parent.winfo_children():
        child.destroy()

    sections = (
        ("Container", preset.container_detail or preset.container.upper()),
        ("Video codec", preset.video_detail or preset.video_codec),
        ("Audio codec", preset.audio_detail or preset.probe_audio_codec),
    )
    for title, text in sections:
        Label(parent, text=title, font=("", 11, "bold"), anchor="w").pack(
            fill="x", pady=(6, 0)
        )
        Label(
            parent,
            text=text,
            justify=LEFT,
            wraplength=420,
            anchor="w",
        ).pack(fill="x", padx=(8, 0))

    extra: list[str] = []
    if preset.max_fps and preset.max_fps > 0:
        extra.append(
            f"Frame rate: keep source if ≤ {preset.max_fps:g} fps, else cap"
        )
    else:
        extra.append("Frame rate: keep source")
    if preset.qscale_v is not None:
        extra.append(f"Video quality: qscale {preset.qscale_v}")
    elif preset.video_bitrate:
        extra.append(f"Video bitrate: {preset.video_bitrate}")
    extra.append(
        f"Audio: {preset.audio_bitrate} · {preset.audio_sample_rate} Hz · "
        f"{preset.audio_channels} channel(s)"
    )
    if preset.broken:
        extra.append("⚠ Broken — does not play reliably on this device")
    elif preset.experimental:
        extra.append("⚠ Experimental — may not play on this device")

    Label(parent, text="Parameters", font=("", 11, "bold"), anchor="w").pack(
        fill="x", pady=(10, 0)
    )
    Label(
        parent,
        text="\n".join(extra),
        justify=LEFT,
        wraplength=420,
        anchor="w",
    ).pack(fill="x", padx=(8, 0), pady=(0, 6))


def ask_video_destination(
    parent,
    *,
    filename: str = "",
    video_options: DeviceVideoOptions | None = None,
    encode_default: bool = True,
    include_broken_presets: bool = False,
) -> SendVideoDialogResult | None:
    """Ask Video/TV parent and optional device encode preset. None if cancelled.

    *video_options* is only set for known players (e.g. ZEN Vision:M). The
    generic device profile passes None — no format notebook is shown.
    *include_broken_presets* comes from Config (show broken recipes like WMV).
    """
    dlg = Toplevel(parent)
    dlg.title("Send Video")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    label = filename.strip() or "selected file"
    Label(
        body,
        text=f"Send to device:\n\n{label}",
        justify=LEFT,
        wraplength=440,
    ).pack(anchor="w", pady=(0, 10))

    Label(body, text="Destination folder:", anchor="w").pack(fill="x")
    choice = StringVar(value="video")
    Radiobutton(
        body,
        text=f"Video  (folder {DEFAULT_VIDEO_FOLDER_ID} — "
        f"{ZEN_VISION_M_FOLDER_IDS[DEFAULT_VIDEO_FOLDER_ID]})",
        variable=choice,
        value="video",
        anchor="w",
    ).pack(fill="x", pady=2)
    Radiobutton(
        body,
        text=f"TV show  (folder {DEFAULT_TV_FOLDER_ID} — "
        f"{ZEN_VISION_M_FOLDER_IDS[DEFAULT_TV_FOLDER_ID]})",
        variable=choice,
        value="tv",
        anchor="w",
    ).pack(fill="x", pady=2)

    visible: tuple[VideoEncodePreset, ...] = ()
    if video_options is not None:
        visible = video_options.visible_presets(
            include_broken=bool(include_broken_presets)
        )
    has_options = bool(visible)
    encode_var = BooleanVar(value=bool(encode_default and has_options))
    ignore_max_fps_var = BooleanVar(value=False)
    high_fps_cb: Checkbutton | None = None
    notebook: ttk.Notebook | None = None
    preset_by_tab: dict[int, VideoEncodePreset] = {}
    default_preset: VideoEncodePreset | None = None
    if has_options and video_options is not None:
        default_preset = video_options.default_preset()
        if default_preset not in visible:
            default_preset = visible[0]

    def _selected_preset() -> VideoEncodePreset | None:
        if notebook is None or not has_options:
            return default_preset
        try:
            idx = int(notebook.index("current"))
        except Exception:
            return default_preset
        return preset_by_tab.get(idx, default_preset)

    def _sync_high_fps_for_preset(preset: VideoEncodePreset | None) -> None:
        if high_fps_cb is None:
            return
        cap = float(preset.max_fps or 0) if preset is not None else 0.0
        encode_on = bool(encode_var.get())
        if encode_on and cap > 0:
            try:
                high_fps_cb.configure(
                    state="normal",
                    text=(
                        f"Ignore max frame rate ({cap:g} fps) (experimental)"
                    ),
                )
            except Exception:
                pass
        else:
            ignore_max_fps_var.set(False)
            try:
                high_fps_cb.configure(state="disabled")
            except Exception:
                pass

    def _on_tab_changed(_event=None) -> None:
        _sync_high_fps_for_preset(_selected_preset())

    if has_options and video_options is not None:
        Label(
            body,
            text=f"Device options — {video_options.device_display_name}",
            font=("", 11, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(14, 2))

        Checkbutton(
            body,
            text="Encode for this device",
            variable=encode_var,
            anchor="w",
            justify=LEFT,
        ).pack(fill="x", pady=(2, 4))

        Label(
            body,
            text=(
                "Each tab is a mutually exclusive format recipe "
                "(container + video + audio). Default is "
                "AVI · XviD / MPEG-4 SP · MP3."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(0, 6))

        notebook = ttk.Notebook(body)
        notebook.pack(fill=BOTH, expand=True, pady=(0, 4))

        for i, preset in enumerate(visible):
            tab = Frame(notebook, padx=10, pady=6)
            notebook.add(tab, text=preset.tab_label)
            _fill_preset_panel(tab, preset)
            preset_by_tab[i] = preset
            if default_preset is not None and preset.id == default_preset.id:
                notebook.select(i)

        notebook.bind("<<NotebookTabChanged>>", _on_tab_changed)

        high_fps_cb = Checkbutton(
            body,
            text="Ignore max frame rate (experimental)",
            variable=ignore_max_fps_var,
            anchor="w",
            justify=LEFT,
        )
        high_fps_cb.pack(fill="x", pady=(6, 2))
        Label(
            body,
            text=(
                "Keeps the source frame rate even when above the selected "
                "recipe’s cap. Likely to fail or glitch — experiments only."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(0, 4))

        def _sync_encode_state(*_args) -> None:
            on = bool(encode_var.get())
            try:
                if notebook is not None:
                    for i in range(len(visible)):
                        notebook.tab(i, state="normal" if on else "disabled")
            except Exception:
                pass
            _sync_high_fps_for_preset(_selected_preset() if on else None)

        encode_var.trace_add("write", _sync_encode_state)
        _sync_encode_state()
        _on_tab_changed()
    else:
        Label(
            body,
            text=(
                "No device-specific video options for this player — "
                "file will be sent as-is.\n"
                "ObjectFileName is the sanitized host basename."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(12, 8))

    result: list[SendVideoDialogResult | None] = [None]

    def on_send() -> None:
        parent_id = (
            DEFAULT_TV_FOLDER_ID
            if choice.get() == "tv"
            else DEFAULT_VIDEO_FOLDER_ID
        )
        do_encode = bool(encode_var.get()) and has_options
        preset = _selected_preset() if do_encode else None
        cap = float(preset.max_fps or 0) if preset is not None else 0.0
        result[0] = SendVideoDialogResult(
            parent_id=int(parent_id),
            encode_for_device=do_encode,
            preset_id=preset.id if preset is not None else None,
            ignore_max_fps=(
                do_encode and cap > 0 and bool(ignore_max_fps_var.get())
            ),
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(10, 0))
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Send", width=10, command=on_send).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 480) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]


def show_folder_list_dialog(parent, folders: list) -> None:
    """Modal scrollable list of device folders (does not touch the library tree)."""
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    dlg = Toplevel(parent)
    dlg.title("Device Folders")
    dlg.transient(parent)
    dlg.geometry("420x360")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(body, text=f"{len(folders)} folder(s)").pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    scroll = Scrollbar(list_frame)
    scroll.pack(side=RIGHT, fill=Y)
    lb = Listbox(list_frame, yscrollcommand=scroll.set)
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    scroll.config(command=lb.yview)
    for entry in folders:
        lb.insert(END, folder_line(entry))

    Button(body, text="Close", command=dlg.destroy).pack(anchor="e")
    dlg.grab_set()
    parent.wait_window(dlg)


def show_file_list_dialog(parent, files: list) -> None:
    """Modal scrollable list of device files (experimental List Files)."""
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.ui.formatting import file_line

    dlg = Toplevel(parent)
    dlg.title("Device Files (experimental)")
    dlg.transient(parent)
    dlg.geometry("720x420")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=(
            f"{len(files)} object(s) — full MTP file listing. "
            "May be large/slow on big libraries."
        ),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
    )
    # Prefer monospaced font for aligned columns when available.
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)
    for entry in files:
        lb.insert(END, file_line(entry))

    Button(body, text="Close", command=dlg.destroy).pack(anchor="e")
    dlg.grab_set()
    parent.wait_window(dlg)


def show_track_list_dialog(
    parent,
    tracks: list,
    *,
    on_load_tags: Callable | None = None,
) -> None:
    """Modal scrollable list of device tracks (experimental List Tracks).

    Rows come from the fast file listing (ids/filenames). Optional
    *on_load_tags(selected_refs, apply_updates)* starts a background tag
    fetch; *apply_updates(updated_refs)* must be called on the UI thread
    with the enriched refs for those ids.
    """
    from tkinter import BOTH, END, EXTENDED, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.ui.formatting import track_line

    rows = list(tracks or [])
    loading = {"active": False}

    dlg = Toplevel(parent)
    dlg.title("Device Tracks (experimental)")
    dlg.transient(parent)
    dlg.geometry("860x460")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)

    note_var = StringVar()
    status_var = StringVar(value="")

    def _tagged_count() -> int:
        return sum(
            1
            for t in rows
            if (getattr(t, "title", None) or getattr(t, "artist", None) or "").strip()
        )

    def _refresh_note() -> None:
        tagged = _tagged_count()
        note_var.set(
            f"{len(rows)} track(s) from file listing "
            f"({tagged} with artist/title tags). "
            "Filenames first — select rows and Load tags for on-device "
            "metadata (per-object USB; keep selections small)."
        )

    _refresh_note()
    Label(
        body,
        textvariable=note_var,
        wraplength=820,
        justify=LEFT,
    ).pack(anchor="w")
    Label(
        body,
        textvariable=status_var,
        wraplength=820,
        justify=LEFT,
        fg="#444",
    ).pack(anchor="w", pady=(2, 0))

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
        selectmode=EXTENDED,
        exportselection=False,
    )
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)

    def _rebuild_list(*, keep_ids: set[int] | None = None) -> None:
        selected_ids = keep_ids
        if selected_ids is None:
            selected_ids = set()
            for idx in lb.curselection():
                i = int(idx)
                if 0 <= i < len(rows):
                    selected_ids.add(int(rows[i].item_id or 0))
        lb.delete(0, END)
        for entry in rows:
            lb.insert(END, track_line(entry))
        if selected_ids:
            for i, entry in enumerate(rows):
                if int(entry.item_id or 0) in selected_ids:
                    lb.selection_set(i)

    _rebuild_list(keep_ids=set())

    btn_row = Frame(body)
    btn_row.pack(fill="x")

    def on_close() -> None:
        if loading["active"]:
            if not messagebox.askyesno(
                "Close track list",
                "Tag loading is still running.\n\nClose the dialog anyway?",
                parent=dlg,
            ):
                return
        dlg.destroy()

    def apply_updates(updated_refs: list) -> None:
        """Merge enriched refs into the open dialog (UI thread)."""
        if not dlg.winfo_exists():
            return
        by_id = {int(r.item_id or 0): r for r in (updated_refs or []) if int(r.item_id or 0) > 0}
        if by_id:
            for i, ref in enumerate(rows):
                oid = int(ref.item_id or 0)
                if oid in by_id:
                    rows[i] = by_id[oid]
            _rebuild_list()
            _refresh_note()
        loading["active"] = False
        try:
            btn_tags.configure(state="normal")
        except Exception:
            pass

    def on_load_clicked() -> None:
        if on_load_tags is None:
            return
        if loading["active"]:
            return
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo(
                "Load tags",
                "Select one or more tracks first.\n\n"
                "Tip: keep selections small — each tag fetch is a USB round-trip.",
                parent=dlg,
            )
            return
        selected = []
        for idx in sel:
            i = int(idx)
            if 0 <= i < len(rows):
                selected.append(rows[i])
        if not selected:
            return
        # Soft cap: warn on large selections (still allowed).
        if len(selected) > 25 and not messagebox.askyesno(
            "Load tags",
            f"Load on-device tags for {len(selected)} tracks?\n\n"
            "Each object is a separate USB metadata call. Large batches "
            "can take a long time and stress the device session.\n\n"
            "Continue?",
            parent=dlg,
            icon=messagebox.WARNING,
        ):
            return
        loading["active"] = True
        btn_tags.configure(state="disabled")
        status_var.set(f"Loading tags for {len(selected)} track(s)…")

        def apply_and_status(updated_refs: list, *, message: str = "") -> None:
            apply_updates(updated_refs)
            if dlg.winfo_exists():
                status_var.set(message or "")

        on_load_tags(selected, apply_and_status)

    Button(btn_row, text="Close", width=10, command=on_close).pack(side=RIGHT)
    btn_tags = Button(
        btn_row,
        text="Load tags for selection",
        command=on_load_clicked,
        state="normal" if on_load_tags is not None else "disabled",
    )
    btn_tags.pack(side=RIGHT, padx=(0, 8))

    dlg.protocol("WM_DELETE_WINDOW", on_close)
    dlg.grab_set()
    parent.wait_window(dlg)


def pick_file_entry_dialog(
    parent,
    files: list,
    *,
    title: str = "Select Object",
    prompt: str = "Select an object from the list.",
    action_label: str = "Select",
    confirm_message=None,
):
    """Modal picker over a file listing; returns selected FileEntry or None.

    Used by experimental Device admin paths that start from get_filelisting /
    list_files (Delete Track, Get File Info). Optional *confirm_message(entry)*
    returns a yes/no body string, or None to skip confirmation.
    """
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.domain.models import FileEntry
    from mtpmanager.ui.formatting import file_line

    entries = list(files or [])
    result: list[FileEntry | None] = [None]

    dlg = Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.geometry("720x420")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=f"{len(entries)} object(s) — {prompt}",
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
        exportselection=False,
    )
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)
    for entry in entries:
        lb.insert(END, file_line(entry))

    btn_row = Frame(body)
    btn_row.pack(fill="x")

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    def on_choose() -> None:
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo(
                title,
                "Select an object from the list first.",
                parent=dlg,
            )
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(entries):
            return
        entry = entries[idx]
        if confirm_message is not None:
            body_text = confirm_message(entry)
            if body_text and not messagebox.askyesno(
                "Confirm",
                body_text,
                parent=dlg,
            ):
                return
        result[0] = entry
        dlg.destroy()

    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text=action_label, width=10, command=on_choose).pack(side=RIGHT)

    def on_double(_event=None) -> None:
        on_choose()

    lb.bind("<Double-Button-1>", on_double)
    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 720) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    if entries:
        lb.selection_set(0)
        lb.activate(0)
        lb.focus_set()
    parent.wait_window(dlg)
    return result[0]


def show_file_info_dialog(parent, entry, *, note: str | None = None) -> None:
    """Modal display of one object's metadata (Get File Info).

    Optional *note* is shown under the summary (e.g. listing-snapshot fallback
    when live Get_Filemetadata fails on ZEN).
    """
    from mtpmanager.ui.formatting import file_metadata_summary

    dlg = Toplevel(parent)
    dlg.title("File Info (experimental)")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=file_metadata_summary(entry),
        justify=LEFT,
        anchor="w",
        font=("Menlo", 11),
    ).pack(anchor="w")
    if note:
        Label(
            body,
            text=note,
            justify=LEFT,
            anchor="w",
            wraplength=420,
            fg="#555555",
        ).pack(anchor="w", pady=(10, 0))
    Button(body, text="Close", width=10, command=dlg.destroy).pack(
        anchor="e", pady=(12, 0)
    )
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 420) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 240) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)


def show_track_info_dialog(parent, info) -> None:
    """Modal display of on-device track tags (Get Track Info)."""
    from mtpmanager.ui.formatting import track_metadata_summary

    dlg = Toplevel(parent)
    dlg.title("Track Info (experimental)")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=track_metadata_summary(info),
        justify=LEFT,
        anchor="w",
        font=("Menlo", 11),
    ).pack(anchor="w")
    Button(body, text="Close", width=10, command=dlg.destroy).pack(
        anchor="e", pady=(12, 0)
    )
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 460) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
