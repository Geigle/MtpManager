"""Map UI events to application services."""

from __future__ import annotations

import logging
from tkinter import END, filedialog, messagebox

from mtpmanager.app import device_ops
from mtpmanager.app.scan_library import scan_library
from mtpmanager.app.transfer import transfer_track, transfer_tracks
from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.infra.pymtp_device import PymtpDevice
from mtpmanager.ui.formatting import device_info_summary, folder_line, track_summary
from mtpmanager.ui.window import MainWindow


class AppController:
    def __init__(self, window: MainWindow, device: PymtpDevice | None = None):
        self.win = window
        self.device = device or PymtpDevice()
        self.library = Library()
        self.transcoder = FFmpegTranscoder()
        self._wire()

    def _wire(self) -> None:
        w = self.win
        w.btn_connect.configure(command=self.on_connect)
        w.btn_disconnect.configure(command=self.on_disconnect)
        w.btn_device_info.configure(command=self.on_device_info)
        w.btn_select_library.configure(command=self.on_select_library)
        w.btn_action.configure(command=self.on_action)
        w.cmd_checkbox.configure(command=self.on_toggle_cmd)

    def _transport(self):
        if self.win.use_cmd.get() == 1:
            return CmdTransport()
        return self.device

    def _selected_index(self) -> int | None:
        sel = self.win.listbox.curselection()
        if not sel:
            messagebox.showinfo("Index", "You forgot to select a track.")
            return None
        return int(sel[0])

    def _selected_track(self) -> Track | None:
        idx = self._selected_index()
        if idx is None:
            return None
        if idx < 0 or idx >= len(self.library):
            messagebox.showinfo("Index", "Selection is out of range.")
            return None
        return self.library.get(idx)

    def _populate_listbox(self, library: Library) -> None:
        self.win.listbox.delete(0, END)
        for track in library.tracks:
            self.win.listbox.insert(END, track_summary(track))

    def _progress(self, done: int, total: int, path: str) -> None:
        if total <= 0:
            return
        pct = round((done / total) * 100)
        try:
            self.win.progress["value"] = pct
            self.win.root.update_idletasks()
        except Exception:
            pass

    def on_toggle_cmd(self) -> None:
        print(f"Use CMD now {self.win.use_cmd.get()}")

    def on_connect(self) -> None:
        try:
            device_ops.connect(self.device)
        except Exception as e:
            messagebox.showerror("Connect", str(e))

    def on_disconnect(self) -> None:
        device_ops.disconnect(self.device)

    def on_device_info(self) -> None:
        try:
            info = device_ops.get_device_info(self.device)
            messagebox.showinfo("Device Info", device_info_summary(info))
        except Exception as e:
            messagebox.showerror("Device Info", str(e))

    def on_select_library(self) -> None:
        path = filedialog.askdirectory(
            initialdir="~/Music/",
            title="Select Music Library Directory",
        )
        if not path:
            return
        self.library = scan_library(path)
        self._populate_listbox(self.library)
        print(f"Loaded {len(self.library)} tracks from {path}")

    def _transfer_one(self, track: Track, fmt: str) -> None:
        transfer_track(
            track,
            target_format=fmt,
            transport=self._transport(),
            transcoder=self.transcoder,
        )

    def _transfer_many(self, tracks: list[Track], fmt: str = "mp3") -> None:
        transfer_tracks(
            tracks,
            target_format=fmt,
            transport=self._transport(),
            transcoder=self.transcoder,
            on_progress=self._progress,
        )

    def action_single_track(self, fmt: str = "mp3") -> None:
        track = self._selected_track()
        if track is None:
            return
        self._transfer_one(track, fmt)

    def action_all_from_artist(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        matches = self.library.filter_by_artist(track.meta.artist)
        matches.sort(key=lambda t: t.path)
        print(f"Artist {track.meta.artist}: {len(matches)} tracks")
        self._transfer_many(matches, "mp3")

    def action_all_from_album(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        matches = self.library.filter_by_album(track.meta.artist, track.meta.album)
        matches.sort(key=lambda t: t.path)
        print(f"Album {track.meta.album}: {len(matches)} tracks")
        self._transfer_many(matches, "mp3")

    def action_entire_library(self) -> None:
        if not self.library.tracks:
            messagebox.showinfo("Library", "Load a library first.")
            return
        self._transfer_many(list(self.library.tracks), "mp3")

    def action_set_device_name(self) -> None:
        name = self.win.file_entry.get().strip()
        if not name:
            messagebox.showinfo("Usage", "Enter a device name in the text field.")
            return
        if not messagebox.askyesno(
            "Confirm New Device Name",
            f"Device will be renamed to {name}.\nProceed?",
        ):
            return
        try:
            device_ops.set_device_name(self.device, name)
        except Exception as e:
            messagebox.showerror("Set Device Name", str(e))

    def action_create_folder(self) -> None:
        name = self.win.file_entry.get().strip()
        if not name:
            messagebox.showinfo("Usage", "Enter a folder name in the text field.")
            return
        if not messagebox.askyesno(
            "Confirm New Folder Name",
            f"Will create new folder: {name}\nProceed?",
        ):
            return
        try:
            device_ops.create_folder(self.device, name)
        except Exception as e:
            messagebox.showerror("Create Folder", str(e))

    def action_read_folder_list(self) -> None:
        try:
            folders = device_ops.list_folders(self.device)
        except Exception as e:
            messagebox.showerror("Folders", str(e))
            return
        self.win.listbox.delete(0, END)
        for entry in folders:
            print(entry.name)
            self.win.listbox.insert(END, folder_line(entry))

    def action_delete_all_tracks(self) -> None:
        """Stub: lists storage ids only (same as previous incomplete behavior)."""
        try:
            alltracks = self.device.get_tracklisting()
        except Exception as e:
            messagebox.showerror("Delete", str(e))
            return
        for x in alltracks:
            print(x.storage_id)
        messagebox.showinfo(
            "Delete All Tracks",
            "Not fully implemented — listed track storage IDs to console only.",
        )

    def action_get_file_info(self) -> None:
        obid = 2654
        try:
            fmd = self.device.get_file_metadata(obid)
            print(fmd)
            messagebox.showinfo("File Info", str(fmd))
        except Exception as e:
            messagebox.showerror("File Info", str(e))

    def action_convert_and_transfer_album(self) -> None:
        """Pick a directory, scan it, transfer every track as MP3 via pipeline."""
        path = filedialog.askdirectory(
            initialdir="~/",
            title="Select Music Album Directory",
        )
        if not path:
            return
        album_lib = scan_library(path)
        if not album_lib.tracks:
            messagebox.showinfo("Album", "No music files found.")
            return
        self._transfer_many(list(album_lib.tracks), "mp3")

    def action_send_test_file(self) -> None:
        path = self.win.file_entry.get().strip()
        if not path:
            messagebox.showinfo("Usage", "Enter a local file path in the text field.")
            return
        try:
            device_ops.send_test_file(self.device, path)
        except Exception as e:
            messagebox.showerror("Send File", str(e))

    def action_send_test_track(self) -> None:
        path = self.win.file_entry.get().strip()
        if not path:
            messagebox.showinfo("Usage", "Enter a local track path in the text field.")
            return
        meta = read_metadata(path)
        track = Track(path=path, meta=meta)
        try:
            self._transfer_one(track, "mp3")
        except Exception as e:
            messagebox.showerror("Send Track", str(e))

    def on_action(self) -> None:
        option = self.win.sendtype_combo.get()
        handlers = {
            "Single Track MP3": lambda: self.action_single_track("mp3"),
            "Single Track WMA": lambda: self.action_single_track("wma"),
            "All from Artist": self.action_all_from_artist,
            "All from Album": self.action_all_from_album,
            "Entire Library": self.action_entire_library,
            "Set Device Name": self.action_set_device_name,
            "Read Folder List": self.action_read_folder_list,
            "Create a New Folder": self.action_create_folder,
            "Delete All Tracks": self.action_delete_all_tracks,
            "Get File Info": self.action_get_file_info,
            "Convert and Transfer Album": self.action_convert_and_transfer_album,
            "Send Test File": self.action_send_test_file,
            "Send Test Track": self.action_send_test_track,
        }
        handler = handlers.get(option)
        if handler is None:
            messagebox.showinfo("Usage", "This option is not ready.")
            return
        try:
            handler()
        except Exception as e:
            logging.exception("Action failed: %s", option)
            messagebox.showerror("Action failed", str(e))
