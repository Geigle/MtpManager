"""Modal dialogs for device admin (no persistent main-window entry field)."""

from __future__ import annotations

from collections.abc import Callable
from tkinter import (
    BOTH,
    LEFT,
    RIGHT,
    Button,
    Entry,
    Frame,
    Label,
    Radiobutton,
    StringVar,
    Toplevel,
    messagebox,
    simpledialog,
)

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


def show_config_dialog(parent, *, send_format: str) -> str | None:
    """Edit app preferences. Returns new send_format on Save, or None if cancelled.

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
        wraplength=340,
    ).pack(anchor="w", pady=(0, 12))

    result: list[str | None] = [None]

    def on_save() -> None:
        raw = (fmt_var.get() or "MP3").strip().lower()
        if raw not in VALID_SEND_FORMATS:
            messagebox.showerror("Config", f"Invalid format: {raw}", parent=dlg)
            return
        result[0] = raw
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x")
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(side=RIGHT, padx=(6, 0))
    Button(btn_row, text="Save", width=10, command=on_save).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 340) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 200) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]


def ask_video_destination(
    parent,
    *,
    filename: str = "",
) -> int | None:
    """Ask Video (120) vs TV (124) parent folder. Returns folder id or None."""
    dlg = Toplevel(parent)
    dlg.title("Send Video")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    label = filename.strip() or "selected file"
    Label(
        body,
        text=f"Send to device as:\n\n{label}",
        justify=LEFT,
        wraplength=360,
    ).pack(anchor="w", pady=(0, 10))

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

    Label(
        body,
        text=(
            "Parent folder only — ObjectFileName stays the file basename\n"
            "(sanitized). ZEN Vision:M expects WMV/AVI-style video."
        ),
        justify=LEFT,
        wraplength=360,
    ).pack(anchor="w", pady=(10, 12))

    result: list[int | None] = [None]

    def on_send() -> None:
        if choice.get() == "tv":
            result[0] = DEFAULT_TV_FOLDER_ID
        else:
            result[0] = DEFAULT_VIDEO_FOLDER_ID
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x")
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Send", width=10, command=on_send).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 380) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 220) // 3)
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
