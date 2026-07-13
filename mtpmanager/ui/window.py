"""Tk layout only — widgets and packing."""

from __future__ import annotations

from typing import Literal

from tkinter import (
    BOTH,
    BOTTOM,
    LEFT,
    RIGHT,
    TOP,
    X,
    Y,
    Button,
    Entry,
    Frame,
    Label,
    Listbox,
    Scrollbar,
    Tk,
    ttk,
)

Mode = Literal["stable", "experimental"]

# Transfer actions available via CMD (mtp-sendtr) — Stable Mode.
STABLE_ACTIONS = [
    "Single Track MP3",
    "Single Track WMA",
    "All from Album",
    "All from Artist",
    "Entire Library",
    "Convert and Transfer Album",
    "Send Test Track",
]

# Full action set including PyMTP device administration — Experimental Mode.
EXPERIMENTAL_ACTIONS = [
    "Single Track MP3",
    "Single Track WMA",
    "All from Album",
    "All from Artist",
    "Entire Library",
    "Set Device Name",
    "Read Folder List",
    "Create a New Folder",
    "Copy Track to PC",
    "Delete All Tracks",
    "Get File Info",
    "Convert and Transfer Album",
    "Send Test File",
    "Send Test Track",
]

# Backward-compatible alias for callers that imported the old name.
SENDTYPE_OPTIONS = EXPERIMENTAL_ACTIONS


class MainWindow:
    def __init__(self, root: Tk | None = None):
        self.root = root or Tk()
        self.root.title("MTP Manager")
        self.root.geometry("1000x600")
        self.root["borderwidth"] = 3
        self.root["relief"] = "sunken"

        header = Frame(self.root)
        header.pack(side=TOP, fill=X)
        Label(header, text="MTP Manager").pack()

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

        self.btn_connect = Button(experimental_tab, width=20, text="Connect")
        self.btn_connect.pack(padx=3, pady=3, side=TOP)

        self.btn_disconnect = Button(experimental_tab, width=20, text="Disconnect")
        self.btn_disconnect.pack(padx=3, pady=3, side=TOP)

        self.btn_device_info = Button(experimental_tab, width=20, text="Device Info")
        self.btn_device_info.pack(padx=3, pady=3, side=TOP)

        # Shared action strip (library + transfers); combobox values follow active tab.
        shared = Frame(leftframe)
        shared.pack(padx=3, pady=6, fill=X)

        self.sendtype_combo = ttk.Combobox(shared, values=STABLE_ACTIONS, state="readonly")
        self.sendtype_combo.set(STABLE_ACTIONS[0])
        self.sendtype_combo.pack(padx=3, pady=3)

        self.btn_select_library = Button(shared, width=20, text="Select Library")
        self.btn_select_library.pack(padx=3, pady=3, side=TOP)

        self.btn_action = Button(shared, width=20, text="MTP Action")
        self.btn_action.pack(padx=3, pady=3, side=TOP)

        self.file_entry = Entry(rightframe, width=60)
        self.file_entry.insert(0, "")
        self.file_entry.pack(padx=5, pady=5)

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

    def actions_for_mode(self, mode: Mode | None = None) -> list[str]:
        mode = mode or self.active_mode()
        return list(STABLE_ACTIONS if mode == "stable" else EXPERIMENTAL_ACTIONS)

    def apply_mode_actions(self) -> None:
        """Refresh combobox values for the active tab; keep selection when possible."""
        options = self.actions_for_mode()
        current = self.sendtype_combo.get()
        self.sendtype_combo.configure(values=options)
        if current in options:
            self.sendtype_combo.set(current)
        else:
            self.sendtype_combo.set(options[0])

    def mainloop(self) -> None:
        self.root.mainloop()
