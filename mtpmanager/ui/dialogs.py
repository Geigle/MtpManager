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
    StringVar,
    Toplevel,
    messagebox,
    simpledialog,
)

from mtpmanager.domain.models import DeviceInfo
from mtpmanager.infra.app_config import VALID_SEND_FORMATS
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

    Label(body, text="Output format for sync / transfer:").pack(anchor="w")
    fmt_var = StringVar(value=initial.upper())
    combo = ttk.Combobox(
        body,
        textvariable=fmt_var,
        values=("MP3", "WMA"),
        state="readonly",
        width=12,
    )
    combo.pack(anchor="w", pady=(6, 8))

    Label(
        body,
        text=(
            "Transfer engine is under Config → Stable Mode:\n"
            "off = PyMTP (default, Device menu + auto-connect);\n"
            "on = mtp-sendtr subprocess per track."
        ),
        justify=LEFT,
        wraplength=320,
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
