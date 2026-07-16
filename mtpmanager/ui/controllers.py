"""Map UI events to application services."""

from __future__ import annotations

import logging
import os
from tkinter import END, filedialog, messagebox

from mtpmanager.app import device_ops
from mtpmanager.app.scan_library import scan_library
from mtpmanager.app.transfer import transfer_track, transfer_tracks
from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import load_library_index, save_library_index
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.infra.pymtp_device import PymtpDevice
from mtpmanager.ports.transport import TransportError
from mtpmanager.ui.formatting import device_info_summary, folder_line, track_summary
from mtpmanager.ui.window import MainWindow

logger = logging.getLogger(__name__)

# Tk event state bit for Shift (same on macOS/Linux/Windows).
# Shift-click on Select/Scan remains an alias for Change Library.
_SHIFT_MASK = 0x0001


class AppController:
    def __init__(self, window: MainWindow, device: PymtpDevice | None = None):
        self.win = window
        self.device = device or PymtpDevice()
        self.library = Library()
        self.transcoder = FFmpegTranscoder()
        self._wire()
        self._restore_library_from_index()


    def _wire(self) -> None:
        w = self.win
        w.btn_connect.configure(command=self.on_connect)
        w.btn_disconnect.configure(command=self.on_disconnect)
        w.btn_device_info.configure(command=self.on_device_info)
        # Bind (not command=) so Shift-click can alias Change Library.
        w.btn_select_library.bind("<ButtonRelease-1>", self.on_library_button)
        w.btn_change_library.configure(command=self.on_change_library)
        w.btn_action.configure(command=self.on_action)
        w.notebook.bind("<<NotebookTabChanged>>", self.on_mode_tab_changed)


    def _transport(self):
        if self.win.active_mode() == "stable":
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


    def on_mode_tab_changed(self, _event=None) -> None:
        mode = self.win.active_mode()
        self.win.apply_mode_actions()
        logger.info(
            "Mode now %s (%s)",
            mode,
            "CMD" if mode == "stable" else "PyMTP",
        )


    def on_connect(self) -> None:
        try:
            device_ops.connect(self.device)
        except Exception as e:
            logger.exception("Connect failed")
            messagebox.showerror("Connect", str(e))


    def on_disconnect(self) -> None:
        device_ops.disconnect(self.device)


    def on_device_info(self) -> None:
        try:
            info = device_ops.get_device_info(self.device)
            messagebox.showinfo("Device Info", device_info_summary(info))
        except Exception as e:
            logger.exception("Device info failed")
            messagebox.showerror("Device Info", str(e))


    def _sync_library_chrome(self) -> None:
        """Update Select/Scan label, path, and track count on the library toolbar."""
        label = "Scan Library" if self.library.root_path else "Select Library"
        self.win.set_library_button_label(label)
        self.win.set_library_status(self.library.root_path, len(self.library))

    def _restore_library_from_index(self) -> None:
        """Load durable index at startup so tracks appear without a full rescan."""
        loaded = load_library_index()
        if loaded is None:
            self._sync_library_chrome()
            return
        if not loaded.root_path or not os.path.isdir(loaded.root_path):
            logger.warning(
                "Library index root missing or not a directory: %r",
                loaded.root_path,
            )
            self._sync_library_chrome()
            return
        self.library = loaded
        self._populate_listbox(self.library)
        self._sync_library_chrome()
        logger.info(
            "Restored %d tracks from index (root=%s)",
            len(self.library),
            self.library.root_path,
        )

    def _apply_scanned_library(self, path: str) -> None:
        self.library = scan_library(path)
        self._populate_listbox(self.library)
        try:
            save_library_index(self.library)
        except OSError as e:
            logger.exception("Failed to save library index")
            messagebox.showwarning(
                "Library Index",
                f"Library loaded but could not save index:\n{e}",
            )
        self._sync_library_chrome()
        logger.info("Loaded %d tracks from %s", len(self.library), path)

    def _pick_library_directory(self) -> str | None:
        root = self.library.root_path
        initial = root if root else "~/Music/"
        path = filedialog.askdirectory(
            initialdir=initial,
            title="Select Music Library Directory",
        )
        return path or None

    def on_change_library(self) -> None:
        """Explicitly pick a new root, rescan, and rewrite the index."""
        path = self._pick_library_directory()
        if not path:
            return
        logger.info("Change Library → %s", path)
        self._apply_scanned_library(path)

    def on_library_button(self, event=None) -> None:
        """Select Library (first run) or Scan Library (rescan stored root).

        Shift-click aliases Change Library (folder picker + replace root).
        If the stored root is missing, falls back to Select.
        """
        force_select = bool(event is not None and (event.state & _SHIFT_MASK))
        if force_select:
            logger.info("Re-selecting library root (Shift-click)")
            self.on_change_library()
            return

        root = self.library.root_path
        has_usable_root = bool(root) and os.path.isdir(root)

        if not has_usable_root:
            path = self._pick_library_directory()
            if not path:
                return
        else:
            path = root

        self._apply_scanned_library(path)

    # Back-compat alias if anything still calls the old name.
    def on_select_library(self, event=None) -> None:
        self.on_library_button(event)


    def _log_transport_error(self, label: str, exc: TransportError) -> None:
        logger.exception(
            "%s path=%s fatal=%s rc=%s",
            label,
            exc.path,
            exc.fatal,
            exc.returncode,
        )
        if exc.stderr:
            logger.error("Transport stderr:\n%s", exc.stderr)


    def _transfer_recovery_hint(self, *, batch: bool = False) -> str:
        """User-facing next steps after a failed transfer (mode-aware)."""
        if self.win.active_mode() == "experimental":
            lines = [
                "Experimental (PyMTP) send failed and was not retried automatically.",
                "",
                "Recommended recovery:",
                "1. Click Disconnect on the Experimental tab "
                "(unplug/replug the player if Disconnect errors).",
                "2. Switch to the Stable Mode tab.",
                "3. Retry the transfer there (uses mtp-sendtr).",
                "",
                "Stay on Experimental only if you are debugging PyMTP/libmtp; "
                "check ~/Library/Logs/MtpManager for the full error stack.",
            ]
            if batch:
                lines.insert(
                    1,
                    "The batch was stopped so remaining tracks are not sent "
                    "into a dead session.",
                )
            return "\n".join(lines)

        if batch:
            return (
                "Batch stopped so remaining tracks are not sent into a dead "
                "MTP session. Unplug/replug the player, free space if needed, "
                "then resume from the failed track."
            )
        return (
            "If the player froze or was unplugged, disconnect/reconnect it "
            "before trying again."
        )


    def _show_transfer_error(
        self,
        title: str,
        exc: TransportError,
        *,
        batch: bool = False,
    ) -> None:
        # Prefer a short primary line; keep full detail available in the dialog
        # but cap very long libmtp stacks so the recovery steps stay visible.
        detail = str(exc).strip()
        if len(detail) > 900:
            detail = detail[:900].rstrip() + "\n…"
        messagebox.showerror(
            title,
            f"{detail}\n\n{self._transfer_recovery_hint(batch=batch)}",
        )


    def _transfer_one(self, track: Track, fmt: str) -> None:
        session_handler = None
        try:
            session_handler = start_transfer_log()
        except OSError as exc:
            logger.warning("Could not open transfer session log: %s", exc)
        try:
            logger.info(
                "Single-track transfer start: path=%s target_format=%s",
                track.path,
                fmt,
            )
            transfer_track(
                track,
                target_format=fmt,
                transport=self._transport(),
                transcoder=self.transcoder,
            )
            logger.info("Single-track transfer done: path=%s", track.path)
        except TransportError as e:
            self._log_transport_error("Single-track transfer failed", e)
            self._show_transfer_error("Transfer failed", e, batch=False)
        finally:
            stop_transfer_log(session_handler)


    def _transfer_many(self, tracks: list[Track], fmt: str = "mp3") -> None:
        try:
            transfer_tracks(
                tracks,
                target_format=fmt,
                transport=self._transport(),
                transcoder=self.transcoder,
                on_progress=self._progress,
            )
        except TransportError as e:
            self._log_transport_error("Batch transfer aborted", e)
            title = "Transfer aborted" if e.fatal else "Transfer failed"
            self._show_transfer_error(title, e, batch=True)


    def action_single_track(self, fmt: str = "mp3") -> None:
        track = self._selected_track()
        if track is None:
            return
        self._transfer_one(track, fmt)


    def action_all_from_artist(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        matches = self.library.filter_by_artist(track)
        matches.sort(key=lambda t: t.path)
        logger.info(
            "Artist %s: %d tracks",
            track.meta.artist,
            len(matches),
        )
        self._transfer_many(matches, "mp3")


    def action_all_from_album(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        matches = self.library.filter_by_album(track)
        matches.sort(key=lambda t: t.path)
        logger.info(
            "Album %s: %d tracks",
            track.meta.album,
            len(matches),
        )
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
            logger.exception("Set device name failed")
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
            logger.exception("Create folder failed")
            messagebox.showerror("Create Folder", str(e))


    def action_read_folder_list(self) -> None:
        try:
            folders = device_ops.list_folders(self.device)
        except Exception as e:
            logger.exception("List folders failed")
            messagebox.showerror("Folders", str(e))
            return
        self.win.listbox.delete(0, END)
        for entry in folders:
            logger.debug("Folder: %s", entry.name)
            self.win.listbox.insert(END, folder_line(entry))


    def action_delete_all_tracks(self) -> None:
        """Stub: lists storage ids only (same as previous incomplete behavior)."""
        try:
            alltracks = self.device.get_tracklisting()
        except Exception as e:
            logger.exception("Delete tracks listing failed")
            messagebox.showerror("Delete", str(e))
            return
        for x in alltracks:
            logger.debug("Track storage_id=%s", x.storage_id)
        messagebox.showinfo(
            "Delete All Tracks",
            "Not fully implemented — listed track storage IDs to console only.",
        )


    def action_get_file_info(self) -> None:
        obid = 2654
        try:
            fmd = self.device.get_file_metadata(obid)
            logger.debug("File metadata for %s: %s", obid, fmd)
            messagebox.showinfo("File Info", str(fmd))
        except Exception as e:
            logger.exception("Get file info failed")
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
            logger.exception("Send test file failed")
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
            logger.exception("Send test track failed")
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
            logger.exception("Action failed: %s", option)
            messagebox.showerror("Action failed", str(e))
