"""Tk layout only — widgets and packing."""

from __future__ import annotations

from tkinter import (
    BOTH,
    BOTTOM,
    LEFT,
    RIGHT,
    TOP,
    Y,
    Button,
    Checkbutton,
    Entry,
    Frame,
    IntVar,
    Label,
    Listbox,
    Scrollbar,
    Tk,
    ttk,
)

SENDTYPE_OPTIONS = [
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


class MainWindow:
    def __init__(self, root: Tk | None = None):
        self.root = root or Tk()
        self.root.title("MTP Manager")
        self.root.geometry("1000x600")
        self.root["borderwidth"] = 3
        self.root["relief"] = "sunken"

        frame = Frame(self.root)
        frame.pack()

        leftframe = Frame(self.root)
        leftframe["borderwidth"] = 3
        leftframe["relief"] = "sunken"
        leftframe.pack(side=LEFT)

        rightframe = Frame(self.root)
        rightframe["borderwidth"] = 3
        rightframe["relief"] = "sunken"
        rightframe.pack(side=RIGHT)

        bottomframe = Frame(self.root)
        bottomframe["borderwidth"] = 3
        bottomframe["relief"] = "sunken"
        bottomframe.pack(side=BOTTOM, fill=BOTH)

        Label(frame, text="MTP Manager").pack()

        self.sendtype_combo = ttk.Combobox(leftframe, values=SENDTYPE_OPTIONS)
        self.sendtype_combo.set("Single Track MP3")
        self.sendtype_combo.pack(padx=3, pady=3)

        self.use_cmd = IntVar(value=0)
        self.cmd_checkbox = Checkbutton(
            leftframe,
            text="Use CMD alternative",
            variable=self.use_cmd,
            onvalue=1,
            offvalue=0,
        )
        self.cmd_checkbox.pack(padx=3, pady=3, side=TOP)

        self.btn_connect = Button(leftframe, width=20, text="Connect")
        self.btn_connect.pack(padx=3, pady=3, side=TOP)

        self.btn_disconnect = Button(leftframe, width=20, text="Disconnect")
        self.btn_disconnect.pack(padx=3, pady=3, side=TOP)

        self.btn_device_info = Button(leftframe, width=20, text="Device Info")
        self.btn_device_info.pack(padx=3, pady=3, side=TOP)

        self.btn_select_library = Button(leftframe, width=20, text="Select Library")
        self.btn_select_library.pack(padx=3, pady=3, side=TOP)

        self.btn_action = Button(leftframe, width=20, text="MTP Action")
        self.btn_action.pack(padx=3, pady=3, side=TOP)

        self.file_entry = Entry(rightframe, width=60)
        self.file_entry.insert(0, "")
        self.file_entry.pack(padx=5, pady=5)

        Label(rightframe, text="Tracks").pack()
        tscroll = Scrollbar(rightframe)
        tscroll.pack(side=RIGHT, fill=Y)
        self.listbox = Listbox(rightframe, yscrollcommand=tscroll.set)
        self.listbox.pack(fill=BOTH)
        tscroll.config(command=self.listbox.yview)

        self.progress = ttk.Progressbar(bottomframe)
        self.progress.pack(side=BOTTOM, fill=BOTH)

    def mainloop(self) -> None:
        self.root.mainloop()
