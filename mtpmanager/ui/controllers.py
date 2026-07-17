"""Map UI events to application services."""

from __future__ import annotations

import logging
import os
import threading
from tkinter import filedialog, messagebox

from mtpmanager.app import device_ops
from mtpmanager.app.scan_library import scan_library
from mtpmanager.app.transfer import transfer_track, transfer_tracks
from mtpmanager.domain.device_profile import DeviceProfile, match_device_profile
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES
from mtpmanager.domain.library import Library
from mtpmanager.domain.library_sort import (
    SortPrimary,
    group_by_album,
    group_by_artist_album,
    group_by_year,
    iter_track_cells,
    sort_tracks_flat,
)
from mtpmanager.domain.models import DeviceInfo, Track
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.device_assets import device_graphic_path
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import load_library_index, save_library_index
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.pymtp_device import PymtpDevice
from mtpmanager.ports.transport import TransportError
from mtpmanager.ui.bg import TkBackgroundRunner
from mtpmanager.ui.dialogs import ask_text, show_device_info_dialog, show_folder_list_dialog
from mtpmanager.ui.window import MainWindow

logger = logging.getLogger(__name__)

# Insert this many tree rows per idle slice to keep the UI responsive.
_TREE_CHUNK = 80

# Experimental auto-connect poll interval (ms).
_DEVICE_POLL_MS = 3000


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
        self._device_poll_after_id: str | None = None
        self._device_poll_gen = 0
        self._device_connect_inflight = False
        self._logged_no_device = False
        # When False, experimental poll is stopped until Device → Connect.
        self._device_auto_reconnect = True
        self._active_profile: DeviceProfile | None = None
        self._sort_primary = SortPrimary.ARTIST
        self._sort_reverse = False
        self._track_by_iid: dict[str, Track] = {}
        self._iid_by_path: dict[str, str] = {}
        self._wire()
        # Defer restore so mainloop can start before any index I/O.
        self.win.root.after(0, self._start_index_restore)


    def _wire(self) -> None:
        w = self.win
        w.set_library_menu_commands(
            on_select_root=self.on_select_library_root,
            on_update=self.on_update_library,
        )
        w.set_transfer_menu_commands(
            on_sync_entire=self.action_entire_library,
            on_sync_folder=self.action_sync_folder,
        )
        w.set_device_menu_commands(
            on_connect=self.on_connect,
            on_disconnect=self.on_disconnect,
            on_device_info=self.on_device_info,
            on_create_folder=self.action_create_folder,
            on_list_folders=self.action_read_folder_list,
            on_get_file_info=self.action_get_file_info,
            on_delete_all=self.action_delete_all_tracks,
        )
        w.set_track_context_commands(
            on_sync_track=self.action_sync_this_track,
            on_sync_album=self.action_all_from_album,
            on_sync_artist=self.action_all_from_artist,
        )
        w.set_sort_heading_handler(self.on_sort_heading)
        # Context menu: Button-3 (most platforms), Button-2, Control-click (macOS).
        w.tree.bind("<Button-3>", w.popup_track_context)
        w.tree.bind("<Button-2>", w.popup_track_context)
        w.tree.bind("<Control-Button-1>", w.popup_track_context)
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
            "• Use Device → Connect (Experimental Mode), or\n"
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
                "Use Device → Connect first (Experimental Mode).",
            )
            return False
        return True

    def _selected_track(self) -> Track | None:
        if not self._require_sync_ready():
            return None
        iid = self.win.selected_tree_iid()
        if not iid:
            messagebox.showinfo("Index", "You forgot to select a track.")
            return None
        track = self._track_by_iid.get(iid)
        if track is None:
            messagebox.showinfo("Index", "Select a track (not a group heading).")
            return None
        return track

    def on_sort_heading(self, col: str) -> None:
        """Column heading click: set primary sort (toggle reverse if same)."""
        mapping = {
            "#0": SortPrimary.TITLE,  # track # column → title-like flat order
            "title": SortPrimary.TITLE,
            "artist": SortPrimary.ARTIST,
            "album": SortPrimary.ALBUM,
            "year": SortPrimary.YEAR,
        }
        primary = mapping.get(col, SortPrimary.ARTIST)
        if primary == self._sort_primary:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_primary = primary
            self._sort_reverse = False
        logger.info(
            "Library sort primary=%s reverse=%s",
            self._sort_primary.value,
            self._sort_reverse,
        )
        self._rebuild_track_tree()

    def _cancel_populate(self) -> None:
        if self._populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._populate_after_id)
            except Exception:
                pass
            self._populate_after_id = None

    def _track_iid(self, track: Track) -> str:
        # Paths are unique; avoid characters Treeview rejects in iids.
        return "t:" + track.path.replace("\\", "/")

    def _insert_track_row(self, parent: str, track: Track) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._track_iid(track)
        # Avoid duplicate iids if path appears twice
        if self.win.tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        self.win.tree.insert(
            parent,
            "end",
            iid=iid,
            text=num,
            values=(title, artist, album, year),
            tags=("track",),
            open=False,
        )
        self._track_by_iid[iid] = track
        self._iid_by_path[track.path] = iid

    def _rebuild_track_tree(self) -> None:
        """Rebuild Treeview from library using current sort primary."""
        self._cancel_populate()
        self.win.clear_track_tree()
        self._track_by_iid.clear()
        self._iid_by_path.clear()
        tracks = list(self.library.tracks)
        if not tracks:
            self.win.set_tracks_usable(self._library_root_reachable())
            return

        primary = self._sort_primary
        reverse = self._sort_reverse

        # Build insert plan as list of callables for chunked UI work.
        ops: list = []

        if primary == SortPrimary.ARTIST:
            groups = group_by_artist_album(tracks)
            if reverse:
                groups = list(reversed(groups))
            for ag in groups:
                artist_iid = ag.key
                ops.append(
                    (
                        "group",
                        "",
                        artist_iid,
                        ag.label,
                        ("group", "group_artist"),
                    )
                )
                children = list(ag.children)
                if reverse:
                    children = list(reversed(children))
                for album in children:
                    ops.append(
                        (
                            "group",
                            artist_iid,
                            album.key,
                            album.label,
                            ("group",),
                        )
                    )
                    album_tracks = list(album.tracks)
                    if reverse:
                        album_tracks = list(reversed(album_tracks))
                    for t in album_tracks:
                        ops.append(("track", album.key, t))
        elif primary == SortPrimary.ALBUM:
            groups = group_by_album(tracks)
            if reverse:
                groups = list(reversed(groups))
            for g in groups:
                ops.append(("group", "", g.key, g.label, ("group",)))
                gtracks = list(g.tracks)
                if reverse:
                    gtracks = list(reversed(gtracks))
                for t in gtracks:
                    ops.append(("track", g.key, t))
        elif primary == SortPrimary.YEAR:
            groups = group_by_year(tracks)
            if reverse:
                groups = list(reversed(groups))
            for g in groups:
                ops.append(("group", "", g.key, g.label, ("group", "group_artist")))
                gtracks = list(g.tracks)
                if reverse:
                    gtracks = list(reversed(gtracks))
                for t in gtracks:
                    ops.append(("track", g.key, t))
        else:
            # TITLE or ARTIST_ALBUM flat
            flat_primary = (
                SortPrimary.ARTIST_ALBUM
                if primary == SortPrimary.ARTIST_ALBUM
                else SortPrimary.TITLE
            )
            ordered = sort_tracks_flat(tracks, flat_primary, reverse=reverse)
            for t in ordered:
                ops.append(("track", "", t))

        def run_chunk(start: int) -> None:
            self._populate_after_id = None
            end = min(start + _TREE_CHUNK, len(ops))
            tree = self.win.tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text=label,
                            values=("", "", "", ""),
                            tags=tags,
                            open=True,
                        )
                else:
                    _, parent, track = op
                    self._insert_track_row(parent, track)
            if end < len(ops):
                self._populate_after_id = self.win.root.after(
                    1, lambda: run_chunk(end)
                )
            else:
                self.win.set_tracks_usable(self._library_root_reachable())

        run_chunk(0)

    def _populate_listbox(self, library: Library) -> None:
        """Rebuild the track tree (name kept for call-site compatibility)."""
        self.library = library
        self._rebuild_track_tree()

    def _progress(self, done: int, total: int, path: str) -> None:
        if total <= 0:
            return
        pct = round((done / total) * 100)
        try:
            self.win.progress["value"] = pct
            self.win.root.update_idletasks()
        except Exception:
            pass

    def _apply_track_status(self, source_path: str, status: str) -> None:
        """Update tree row tint for a source path (main thread only)."""
        iid = self._iid_by_path.get(source_path)
        if iid:
            self.win.set_track_transfer_style(iid, status)

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
        if mode == "experimental":
            # Fresh Experimental session: allow auto-reconnect unless user
            # later chooses Device → Disconnect.
            self._device_auto_reconnect = True
            self._start_device_poll()
        else:
            # Stable (mtp-sendtr) fails if a PyMTP session is already open.
            self._stop_device_poll()
            self._disconnect_for_stable()

    def _start_device_poll(self) -> None:
        """Begin Experimental auto-connect polling (immediate + every 3s)."""
        if not self._device_auto_reconnect:
            return
        self._stop_device_poll(cancel_only=True)
        self._device_poll_gen += 1
        self._experimental_device_tick(self._device_poll_gen)

    def _stop_device_poll(self, *, cancel_only: bool = False) -> None:
        self._device_poll_gen += 1
        if self._device_poll_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_poll_after_id)
            except Exception:
                pass
            self._device_poll_after_id = None
        if not cancel_only:
            # Leaving Experimental: clear art; disconnect handled separately.
            pass

    def _schedule_device_poll(self, gen: int) -> None:
        if gen != self._device_poll_gen:
            return
        if self.win.active_mode() != "experimental":
            return
        if not self._device_auto_reconnect:
            return
        self._device_poll_after_id = self.win.root.after(
            _DEVICE_POLL_MS,
            lambda: self._experimental_device_tick(gen),
        )

    def _experimental_device_tick(self, gen: int) -> None:
        """Quiet auto-connect / liveness check while Experimental is active.

        When a session looks open, probe the device so sudden unplug is
        detected; then clear state and keep retrying connect every interval
        (unless the user disabled auto-reconnect via Device → Disconnect).
        """
        if gen != self._device_poll_gen:
            return
        if self.win.active_mode() != "experimental":
            return
        if not self._device_auto_reconnect:
            return

        # Avoid racing libmtp during library/transfer work.
        if self._library_busy or self._transfer_busy or self._device_connect_inflight:
            self._schedule_device_poll(gen)
            return

        self._device_connect_inflight = True
        local_gen = gen

        def work() -> tuple[str, DeviceInfo | None]:
            """Return (status, info). status: ok | gone | absent."""
            if self.device.is_connected():
                if self.device.session_alive():
                    try:
                        return ("ok", device_ops.get_device_info(self.device))
                    except Exception:
                        # Probe passed earlier but info failed — treat as gone.
                        pass
                # Stale or dead session after unplug: tear down so connect can retry.
                try:
                    device_ops.disconnect(self.device)
                except Exception:
                    pass
                return ("gone", None)

            try:
                device_ops.connect(self.device)
                return ("ok", device_ops.get_device_info(self.device))
            except Exception:
                return ("absent", None)

        def on_done(result: tuple[str, DeviceInfo | None]) -> None:
            self._device_connect_inflight = False
            stale = (
                local_gen != self._device_poll_gen
                or self.win.active_mode() != "experimental"
            )
            if stale:
                if self.win.active_mode() != "experimental" and self.device.is_connected():
                    try:
                        device_ops.disconnect(self.device)
                    except Exception:
                        pass
                return
            if not self._device_auto_reconnect:
                return

            status, info = result
            if status == "ok" and info is not None:
                self._logged_no_device = False
                # Only re-apply art/log when profile missing or first connect.
                if self._active_profile is None:
                    self._apply_device_profile(info)
            elif status == "gone":
                logger.info("Experimental auto-connect: device disconnected")
                self._logged_no_device = False  # allow one "no device" log on next fails
                self._clear_device_profile()
            else:
                self._note_no_device()
                self._clear_device_profile()
            self._schedule_device_poll(local_gen)

        def on_error(_exc: BaseException) -> None:
            self._device_connect_inflight = False
            if (
                local_gen != self._device_poll_gen
                or self.win.active_mode() != "experimental"
                or not self._device_auto_reconnect
            ):
                return
            self._note_no_device()
            self._clear_device_profile()
            self._schedule_device_poll(local_gen)

        def runner() -> None:
            try:
                result = work()
                self.win.root.after(0, lambda: on_done(result))
            except BaseException as exc:
                self.win.root.after(0, lambda e=exc: on_error(e))

        threading.Thread(
            target=runner, name="mtpmanager-device-poll", daemon=True
        ).start()

    def _note_no_device(self) -> None:
        if not self._logged_no_device:
            logger.info("Experimental auto-connect: no MTP device available")
            self._logged_no_device = True

    def _apply_device_profile(self, info: DeviceInfo) -> None:
        profile = match_device_profile(info, BUILTIN_PROFILES)
        self._active_profile = profile
        path = device_graphic_path(profile.graphic_filename)
        self.win.set_device_graphic(path, caption=profile.display_name)
        logger.info(
            "Device profile %s (%s) manufacturer=%r model=%r",
            profile.id,
            profile.display_name,
            info.manufacturer,
            info.model,
        )

    def _clear_device_profile(self) -> None:
        self._active_profile = None
        self.win.set_device_graphic(None)

    def _disconnect_for_stable(self) -> None:
        """Drop PyMTP session so Stable mtp-sendtr can claim the device."""
        self._clear_device_profile()
        if not self.device.is_connected():
            return
        try:
            device_ops.disconnect(self.device)
            logger.info("Disconnected PyMTP session for Stable Mode")
        except Exception:
            logger.exception("Disconnect for Stable Mode failed")

    def on_connect(self) -> None:
        """Manual connect; re-enables auto-reconnect polling on Experimental."""
        self._device_auto_reconnect = True
        try:
            device_ops.connect(self.device)
            self._logged_no_device = False
            try:
                info = device_ops.get_device_info(self.device)
                self._apply_device_profile(info)
            except Exception:
                logger.exception("Connected but could not load device info")
        except Exception as e:
            logger.exception("Connect failed")
            messagebox.showerror("Connect", str(e))
        # Resume monitoring (liveness + reconnect after unplug).
        if self.win.active_mode() == "experimental":
            self._start_device_poll()

    def on_disconnect(self) -> None:
        """Manual disconnect; stop auto-reconnect until Device → Connect."""
        self._device_auto_reconnect = False
        self._stop_device_poll()
        device_ops.disconnect(self.device)
        self._clear_device_profile()
        self._logged_no_device = False
        logger.info("Device → Disconnect: auto-reconnect paused")


    def on_device_info(self) -> None:
        if not self._require_device_ready():
            return
        try:
            info = device_ops.get_device_info(self.device)
        except Exception as e:
            logger.exception("Device info failed")
            messagebox.showerror("Device Info", str(e))
            return

        def apply_name(new_name: str) -> None:
            device_ops.set_device_name(self.device, new_name)
            logger.info("Device renamed to %r", new_name)

        show_device_info_dialog(
            self.win.root,
            info,
            apply_name=apply_name,
        )


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
            self.win.clear_track_tree()
            self._track_by_iid.clear()
            self._iid_by_path.clear()
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
                "1. Device → Disconnect "
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

    def action_create_folder(self) -> None:
        if not self._require_device_ready():
            return
        name = ask_text(
            self.win.root,
            title="Create Folder",
            prompt="Folder name:",
        )
        if not name:
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
        for entry in folders:
            logger.debug("Folder: %s", entry.name)
        show_folder_list_dialog(self.win.root, folders)

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
