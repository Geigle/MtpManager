"""Map UI events to application services."""

from __future__ import annotations

import logging
import os
from tkinter import END, NORMAL, filedialog, messagebox

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
from mtpmanager.ui.bg import TkBackgroundRunner
from mtpmanager.ui.formatting import device_info_summary, folder_line, track_summary
from mtpmanager.ui.window import MainWindow

logger = logging.getLogger(__name__)

# Insert this many listbox rows per idle slice to keep the UI responsive.
_LISTBOX_CHUNK = 100


class AppController:
    def __init__(self, window: MainWindow, device: PymtpDevice | None = None):
        self.win = window
        self.device = device or PymtpDevice()
        self.library = Library()
        self.transcoder = FFmpegTranscoder()
        self._bg = TkBackgroundRunner(window.root)
        self._library_busy = False
        self._transfer_busy = False
        self._populate_after_id: str | None = None
        self._wire()
        # Defer restore so mainloop can start before any index I/O.
        self.win.root.after(0, self._start_index_restore)


    def _wire(self) -> None:
        w = self.win
        w.btn_connect.configure(command=self.on_connect)
        w.btn_disconnect.configure(command=self.on_disconnect)
        w.set_library_menu_commands(
            on_select_root=self.on_select_library_root,
            on_update=self.on_update_library,
        )
        w.set_transfer_menu_commands(
            on_sync_entire=self.action_entire_library,
            on_sync_folder=self.action_sync_folder,
        )
        w.set_device_menu_commands(
            on_device_info=self.on_device_info,
            on_set_name=self.action_set_device_name,
            on_create_folder=self.action_create_folder,
            on_list_folders=self.action_read_folder_list,
            on_send_test_file=self.action_send_test_file,
            on_send_test_track=self.action_send_test_track,
            on_get_file_info=self.action_get_file_info,
            on_delete_all=self.action_delete_all_tracks,
        )
        w.set_track_context_commands(
            on_sync_track=self.action_sync_this_track,
            on_sync_album=self.action_all_from_album,
            on_sync_artist=self.action_all_from_artist,
        )
        # Context menu: Button-3 (most platforms), Button-2, Control-click (macOS).
        w.listbox.bind("<Button-3>", w.popup_track_context)
        w.listbox.bind("<Button-2>", w.popup_track_context)
        w.listbox.bind("<Control-Button-1>", w.popup_track_context)
        w.notebook.bind("<<NotebookTabChanged>>", self.on_mode_tab_changed)


    def _transport(self):
        if self.win.active_mode() == "stable":
            return CmdTransport()
        return self.device

    def _target_format(self) -> str:
        return self.win.target_format()

    def _library_root_reachable(self) -> bool:
        root = self.library.root_path
        return bool(root) and os.path.isdir(root)

    def _require_experimental_connected(self) -> bool:
        """In Experimental mode, require an open PyMTP session before sync."""
        if self.win.active_mode() != "experimental":
            return True
        if self.device.is_connected():
            return True
        messagebox.showwarning(
            "Device not connected",
            "Experimental Mode sends via PyMTP and needs an open session.\n\n"
            "• Click Connect on the Experimental tab, or\n"
            "• Switch to Stable Mode (mtp-sendtr; no Connect required).",
        )
        return False

    def _require_usable_library(self) -> bool:
        """True when library media can be transferred; shows a dialog otherwise."""
        if self._library_busy:
            messagebox.showinfo(
                "Library",
                "Library is still loading or scanning. Try again in a moment.",
            )
            return False
        if self._transfer_busy:
            messagebox.showinfo(
                "Transfer",
                "A transfer is already in progress. Wait for it to finish.",
            )
            return False
        if not self.library.root_path:
            messagebox.showinfo(
                "Library",
                "Select a library root first (Library → Select Library Root…).",
            )
            return False
        if not self._library_root_reachable():
            messagebox.showinfo(
                "Library",
                "Library root is not reachable.\n"
                "Reconnect the volume or choose a new root "
                "(Library → Select Library Root…).",
            )
            return False
        return True

    def _require_sync_ready(self) -> bool:
        """Library usable + Experimental connection gate."""
        if not self._require_usable_library():
            return False
        return self._require_experimental_connected()

    def _require_device_ready(self) -> bool:
        """Experimental admin ops: must be on Experimental tab and connected."""
        if self.win.active_mode() != "experimental":
            messagebox.showinfo(
                "Device",
                "Device tools are available on the Experimental Mode tab.",
            )
            return False
        if not self.device.is_connected():
            messagebox.showwarning(
                "Device not connected",
                "Connect on the Experimental tab first.",
            )
            return False
        return True

    def _selected_index(self) -> int | None:
        if not self._require_sync_ready():
            return None
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


    def _cancel_populate(self) -> None:
        if self._populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._populate_after_id)
            except Exception:
                pass
            self._populate_after_id = None

    def _populate_listbox(self, library: Library) -> None:
        """Fill the listbox in chunks so large libraries do not freeze the UI."""
        self._cancel_populate()
        self.win.listbox.configure(state=NORMAL)
        self.win.listbox.delete(0, END)
        tracks = library.tracks
        total = len(tracks)
        if total == 0:
            self.win.set_tracks_usable(self._library_root_reachable())
            return

        def chunk(start: int) -> None:
            self._populate_after_id = None
            end = min(start + _LISTBOX_CHUNK, total)
            for i in range(start, end):
                self.win.listbox.insert(END, track_summary(tracks[i]))
            if end < total:
                self._populate_after_id = self.win.root.after(1, lambda: chunk(end))
            else:
                self.win.set_tracks_usable(self._library_root_reachable())

        chunk(0)


    def _progress(self, done: int, total: int, path: str) -> None:
        if total <= 0:
            return
        pct = round((done / total) * 100)
        try:
            self.win.progress["value"] = pct
            self.win.root.update_idletasks()
        except Exception:
            pass

    def _indices_for_path(self, path: str) -> list[int]:
        return [i for i, t in enumerate(self.library.tracks) if t.path == path]

    def _apply_track_status(self, source_path: str, status: str) -> None:
        """Update listbox row tint for a source path (main thread only)."""
        for idx in self._indices_for_path(source_path):
            self.win.set_track_transfer_style(idx, status)

    def _mark_batch_queued(self, tracks: list[Track]) -> None:
        """Highlight every track in a bulk operation as queued (green)."""
        for t in tracks:
            self._apply_track_status(t.path, "queued")

    def _clear_transfer_highlights(self) -> None:
        self.win.clear_transfer_styles()

    def _on_transfer_ui_event(self, kind: str, *rest) -> None:
        """Handle progress / track-status events from the transfer worker."""
        if kind == "track_status":
            if len(rest) >= 2:
                self._apply_track_status(str(rest[0]), str(rest[1]))
            return
        if kind == "progress":
            if len(rest) >= 3:
                self._progress(int(rest[0]), int(rest[1]), str(rest[2]))
            return


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
        if not self._require_device_ready():
            return
        try:
            info = device_ops.get_device_info(self.device)
            messagebox.showinfo("Device Info", device_info_summary(info))
        except Exception as e:
            logger.exception("Device info failed")
            messagebox.showerror("Device Info", str(e))


    def _set_library_busy(self, busy: bool, *, message: str | None = None) -> None:
        self._library_busy = busy
        if busy:
            self.win.set_library_menu_state(
                update_enabled=False,
                select_enabled=False,
            )
            self.win.set_library_status(
                self.library.root_path,
                len(self.library),
                root_reachable=self._library_root_reachable()
                if self.library.root_path
                else True,
                busy_message=message or "Working…",
            )
        else:
            self._sync_library_chrome()

    def _sync_library_chrome(self) -> None:
        """Update toolbar status, menu enablement, and dead/live list appearance."""
        if self._library_busy:
            return
        reachable = self._library_root_reachable()
        self.win.set_library_status(
            self.library.root_path,
            len(self.library),
            root_reachable=reachable if self.library.root_path else True,
        )
        self.win.set_library_menu_state(
            update_enabled=reachable,
            select_enabled=True,
        )
        self.win.set_tracks_usable(reachable)

    @staticmethod
    def _load_index_worker() -> Library | None:
        """Worker: load durable index and filter missing files if root is live."""
        loaded = load_library_index(drop_missing_files=False)
        if loaded is None or not loaded.root_path:
            return None
        if os.path.isdir(loaded.root_path):
            live = [t for t in loaded.tracks if os.path.isfile(t.path)]
            dropped = len(loaded.tracks) - len(live)
            if dropped:
                logger.info(
                    "Library index: dropped %d missing file(s); kept %d",
                    dropped,
                    len(live),
                )
            return Library(tracks=live, root_path=loaded.root_path)
        logger.warning(
            "Library index root not reachable: %r — showing stale index",
            loaded.root_path,
        )
        return loaded

    @staticmethod
    def _scan_and_save_worker(path: str) -> tuple[Library, str | None]:
        """Worker: full tree scan + persist index (no Tk).

        Returns (library, save_error_message). Scan failures raise; save failures
        are returned so the UI can still show the scanned library.
        """
        library = scan_library(path)
        try:
            save_library_index(library)
        except OSError as e:
            logger.exception("Failed to save library index")
            return library, str(e)
        return library, None

    def _on_library_job_done(self, library: Library | None, *, kind: str) -> None:
        self._library_busy = False
        if library is None:
            self.library = Library()
            self._cancel_populate()
            self.win.listbox.configure(state=NORMAL)
            self.win.listbox.delete(0, END)
            self._sync_library_chrome()
            logger.info("No library index to restore")
            return

        self.library = library
        self._populate_listbox(self.library)
        self._sync_library_chrome()
        logger.info(
            "%s %d tracks (root=%s, reachable=%s)",
            kind,
            len(self.library),
            self.library.root_path,
            self._library_root_reachable(),
        )

    def _on_scan_done(self, result: tuple[Library, str | None]) -> None:
        library, save_err = result
        self._on_library_job_done(library, kind="Scanned")
        if save_err:
            messagebox.showwarning(
                "Library Index",
                f"Library loaded but could not save index:\n{save_err}",
            )

    def _on_library_job_error(self, exc: BaseException, *, title: str) -> None:
        self._library_busy = False
        self._sync_library_chrome()
        logger.exception("%s", title)
        messagebox.showerror(title, str(exc))

    def _start_index_restore(self) -> None:
        """Background load of durable index (startup; non-blocking)."""
        self._set_library_busy(True, message="Loading index…")
        self._bg.submit(
            self._load_index_worker,
            on_done=lambda lib: self._on_library_job_done(lib, kind="Restored"),
            on_error=lambda e: self._on_library_job_error(
                e, title="Library index failed"
            ),
            name="library-restore",
        )

    def _start_library_scan(self, path: str) -> None:
        """Background full scan of *path*; previous library kept until done."""
        # Do not replace self.library until the worker succeeds (stale root safe).
        self._library_busy = True
        self.win.set_library_menu_state(update_enabled=False, select_enabled=False)
        self.win.set_library_status(
            path,
            len(self.library),
            root_reachable=True,
            busy_message="Scanning…",
        )
        self._bg.submit(
            lambda: self._scan_and_save_worker(path),
            on_done=self._on_scan_done,
            on_error=lambda e: self._on_library_job_error(
                e, title="Library scan failed"
            ),
            name="library-scan",
        )

    def _pick_library_directory(self) -> str | None:
        root = self.library.root_path
        initial = root if root else "~/Music/"
        path = filedialog.askdirectory(
            initialdir=initial,
            title="Select Music Library Directory",
        )
        return path or None

    def on_select_library_root(self) -> None:
        """Pick a library root, full scan, and rewrite the durable index."""
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Library",
                "A background job is already running. Wait for it to finish.",
            )
            return
        path = self._pick_library_directory()
        if not path:
            return
        logger.info("Select Library Root → %s", path)
        self._start_library_scan(path)

    def on_update_library(self) -> None:
        """Rescan the stored root and rewrite the index (menu is disabled if unusable)."""
        if self._library_busy or self._transfer_busy:
            return
        if not self._library_root_reachable():
            messagebox.showinfo(
                "Library",
                "Cannot update: library root is not selected or not reachable.",
            )
            return
        path = self.library.root_path
        logger.info("Update Library → %s", path)
        self._start_library_scan(path)

    # Back-compat aliases for older call sites / mental models.
    def on_change_library(self) -> None:
        self.on_select_library_root()

    def on_select_library(self, event=None) -> None:
        self.on_select_library_root()


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


    def _begin_transfer_job(self) -> bool:
        """Return False if another library/transfer job is already running."""
        if self._library_busy:
            messagebox.showinfo(
                "Library",
                "Library is still loading or scanning. Try again in a moment.",
            )
            return False
        if self._transfer_busy or self._bg.busy:
            messagebox.showinfo(
                "Transfer",
                "A background job is already running. Wait for it to finish.",
            )
            return False
        self._transfer_busy = True
        self._clear_transfer_highlights()
        try:
            self.win.progress["value"] = 0
        except Exception:
            pass
        return True

    def _end_transfer_job(self) -> None:
        self._transfer_busy = False
        self._clear_transfer_highlights()

    def _transfer_one(self, track: Track, fmt: str) -> None:
        if not self._begin_transfer_job():
            return
        # Capture transport on main thread (mode tab may change later).
        transport = self._transport()
        transcoder = self.transcoder
        path = track.path
        self._mark_batch_queued([track])

        def work() -> None:
            session_handler = None
            try:
                session_handler = start_transfer_log()
            except OSError as exc:
                logger.warning("Could not open transfer session log: %s", exc)
            try:
                gen = self._bg.generation
                report = self._bg.progress_callback(gen)

                def on_track_status(src: str, status: str) -> None:
                    report("track_status", src, status)

                logger.info(
                    "Single-track transfer start: path=%s target_format=%s",
                    path,
                    fmt,
                )
                transfer_track(
                    track,
                    target_format=fmt,
                    transport=transport,
                    transcoder=transcoder,
                    slot=0,
                    on_track_status=on_track_status,
                )
                logger.info("Single-track transfer done: path=%s", path)
            finally:
                stop_transfer_log(session_handler)

        def on_done(_result: None) -> None:
            self._end_transfer_job()
            try:
                self.win.progress["value"] = 100
            except Exception:
                pass

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, TransportError):
                self._log_transport_error("Single-track transfer failed", exc)
                self._show_transfer_error("Transfer failed", exc, batch=False)
            else:
                logger.exception("Single-track transfer failed")
                messagebox.showerror("Transfer failed", str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="transfer-one",
        )

    def _transfer_many(self, tracks: list[Track], fmt: str = "mp3") -> None:
        if not tracks:
            messagebox.showinfo("Transfer", "No tracks to transfer.")
            return
        if not self._begin_transfer_job():
            return

        transport = self._transport()
        transcoder = self.transcoder
        # Snapshot the list so library changes during transfer do not race.
        batch = list(tracks)
        self._mark_batch_queued(batch)

        def work() -> int:
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def on_progress(done: int, total: int, path: str) -> None:
                report("progress", done, total, path)

            def on_track_status(src: str, status: str) -> None:
                report("track_status", src, status)

            return transfer_tracks(
                batch,
                target_format=fmt,
                transport=transport,
                transcoder=transcoder,
                on_progress=on_progress,
                on_track_status=on_track_status,
            )

        def on_done(succeeded: int) -> None:
            self._end_transfer_job()
            logger.info("Background batch finished: succeeded=%s", succeeded)

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, TransportError):
                self._log_transport_error("Batch transfer aborted", exc)
                title = "Transfer aborted" if exc.fatal else "Transfer failed"
                self._show_transfer_error(title, exc, batch=True)
            else:
                logger.exception("Batch transfer failed")
                messagebox.showerror("Transfer failed", str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="transfer-batch",
        )


    def action_sync_this_track(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        self._transfer_one(track, self._target_format())

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
        self._transfer_many(matches, self._target_format())

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
        self._transfer_many(matches, self._target_format())

    def action_entire_library(self) -> None:
        if not self._require_sync_ready():
            return
        if not self.library.tracks:
            messagebox.showinfo("Library", "Load a library first.")
            return
        n = len(self.library.tracks)
        fmt = self._target_format().upper()
        if not messagebox.askyesno(
            "Sync Entire Library",
            f"Send all {n} track(s) as {fmt} using "
            f"{'Stable (mtp-sendtr)' if self.win.active_mode() == 'stable' else 'Experimental (PyMTP)'}?\n\n"
            "This may take a long time.",
        ):
            return
        self._transfer_many(list(self.library.tracks), self._target_format())

    def action_sync_folder(self) -> None:
        """Pick a directory, scan it, transfer every track (global format)."""
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Transfer",
                "A background job is already running. Wait for it to finish.",
            )
            return
        if not self._require_experimental_connected():
            return
        path = filedialog.askdirectory(
            initialdir="~/",
            title="Select Folder to Sync",
        )
        if not path:
            return
        album_lib = scan_library(path)
        if not album_lib.tracks:
            messagebox.showinfo("Sync Folder", "No music files found.")
            return
        self._transfer_many(list(album_lib.tracks), self._target_format())

    def action_set_device_name(self) -> None:
        if not self._require_device_ready():
            return
        name = self.win.file_entry.get().strip()
        if not name:
            messagebox.showinfo(
                "Usage",
                "Enter a device name in the Path / name field.",
            )
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
        if not self._require_device_ready():
            return
        name = self.win.file_entry.get().strip()
        if not name:
            messagebox.showinfo(
                "Usage",
                "Enter a folder name in the Path / name field.",
            )
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
        if not self._require_device_ready():
            return
        try:
            folders = device_ops.list_folders(self.device)
        except Exception as e:
            logger.exception("List folders failed")
            messagebox.showerror("Folders", str(e))
            return
        # Temporary view: does not replace the in-memory library.
        self.win.listbox.configure(state=NORMAL)
        self.win.listbox.delete(0, END)
        for entry in folders:
            logger.debug("Folder: %s", entry.name)
            self.win.listbox.insert(END, folder_line(entry))
        messagebox.showinfo(
            "Folders",
            f"Listed {len(folders)} folder(s) in the track list.\n"
            "Use Library → Update Library to restore tracks.",
        )

    def action_delete_all_tracks(self) -> None:
        """Stub: lists storage ids only (same as previous incomplete behavior)."""
        if not self._require_device_ready():
            return
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
        if not self._require_device_ready():
            return
        obid = 2654
        try:
            fmd = self.device.get_file_metadata(obid)
            logger.debug("File metadata for %s: %s", obid, fmd)
            messagebox.showinfo("File Info", str(fmd))
        except Exception as e:
            logger.exception("Get file info failed")
            messagebox.showerror("File Info", str(e))

    def action_send_test_file(self) -> None:
        if not self._require_device_ready():
            return
        path = self.win.file_entry.get().strip()
        if not path:
            messagebox.showinfo(
                "Usage",
                "Enter a local file path in the Path / name field.",
            )
            return
        try:
            device_ops.send_test_file(self.device, path)
        except Exception as e:
            logger.exception("Send test file failed")
            messagebox.showerror("Send File", str(e))

    def action_send_test_track(self) -> None:
        if not self._require_device_ready():
            return
        path = self.win.file_entry.get().strip()
        if not path:
            messagebox.showinfo(
                "Usage",
                "Enter a local track path in the Path / name field.",
            )
            return
        meta = read_metadata(path)
        track = Track(path=path, meta=meta)
        self._transfer_one(track, self._target_format())
