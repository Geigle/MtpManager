"""Map UI events to application services."""

from __future__ import annotations

import logging
import os
import threading
import time
from tkinter import DISABLED, NORMAL, filedialog, messagebox

from mtpmanager.app import device_ops
from mtpmanager.app.artist_folders import ensure_album_folder, ensure_artist_folder
from mtpmanager.app.cancellation import JobCancelled
from mtpmanager.app.scan_library import scan_library
from mtpmanager.app.transfer import transfer_track, transfer_tracks
from mtpmanager.domain.device_profile import DeviceProfile, match_device_profile
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES
from mtpmanager.domain.library import Library, primary_artist
from mtpmanager.domain.library_sort import (
    SortPrimary,
    group_by_album,
    group_by_artist_album,
    group_by_year,
    iter_track_cells,
    sort_tracks_flat,
)
from mtpmanager.domain.device_media import looks_like_track
from mtpmanager.domain.models import DeviceInfo, Track, TrackMetadata
from mtpmanager.infra.album_art import (
    DEFAULT_THUMB_SIZE,
    ensure_cached_thumb,
    warm_album_thumbs,
)
from mtpmanager.infra.app_config import AppConfig, load_app_config, save_app_config
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.device_assets import device_graphic_path
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import load_library_index, save_library_index
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.infra.pymtp_device import PymtpDevice
from mtpmanager.infra.sync_job import (
    SyncJobState,
    load_sync_job,
    new_sync_job,
    save_sync_job,
)
from mtpmanager.ports.transport import TransportError
from mtpmanager.ui.bg import TkBackgroundRunner
from mtpmanager.ui.dialogs import (
    ask_text,
    pick_file_entry_dialog,
    show_config_dialog,
    show_device_info_dialog,
    show_file_info_dialog,
    show_file_list_dialog,
    show_folder_list_dialog,
    show_track_info_dialog,
    show_track_list_dialog,
)
from mtpmanager.ui.window import MainWindow

logger = logging.getLogger(__name__)

# Insert this many tree rows per idle slice to keep the UI responsive.
_TREE_CHUNK = 80

# Experimental auto-connect poll interval (ms).
_DEVICE_POLL_MS = 3000
# After a heavy USB job (listing/transfer), skip probes so a recovering
# ZEN session is not torn down by get_modelname / get_device_info.
_DEVICE_USB_COOLDOWN_S = 12.0
# Consecutive soft probe failures before disconnect/reconnect.
_DEVICE_PROBE_FAIL_LIMIT = 2


class AppController:
    def __init__(self, window: MainWindow, device: PymtpDevice | None = None):
        self.win = window
        self.device = device or PymtpDevice()
        self.library = Library()
        self.transcoder = FFmpegTranscoder()
        self._config: AppConfig = load_app_config()
        self._bg = TkBackgroundRunner(window.root)
        self._library_busy = False
        self._transfer_busy = False
        # Cooperative cancel for transfer / device batch jobs (checked between items).
        self._job_cancel = threading.Event()
        # Durable multi-track sync plan (resume after failure / cancel).
        self._active_sync_job: SyncJobState | None = None
        # Path → Track for the active batch (progress status label).
        self._batch_track_by_path: dict[str, Track] = {}
        self._populate_after_id: str | None = None
        self._device_poll_after_id: str | None = None
        self._device_poll_gen = 0
        self._device_connect_inflight = False
        self._logged_no_device = False
        # monotonic deadline: auto-connect must not touch USB until then
        self._usb_quiet_until = 0.0
        self._device_probe_fails = 0
        # When False, experimental poll is stopped until Device → Connect.
        self._device_auto_reconnect = True
        self._active_profile: DeviceProfile | None = None
        self._sort_primary = SortPrimary.ARTIST
        self._sort_reverse = False
        self._track_by_iid: dict[str, Track] = {}
        self._iid_by_path: dict[str, str] = {}
        # Group header iid → seed Track for filter_by_artist / filter_by_album.
        self._group_seed_by_iid: dict[str, Track] = {}
        self._context_group_seed: Track | None = None
        self._pending_album_art: list[tuple[str, str]] = []  # (iid, track_path)
        self._album_art_job_gen = 0
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
            on_sync_selected=self.action_sync_selected,
            on_resume_sync=self.action_resume_sync,
            on_cancel_job=self.on_cancel_job,
        )
        w.set_config_menu_commands(
            on_config=self.on_config,
            on_stable_mode_toggle=self.on_stable_mode_toggle,
            on_artist_folders_toggle=self.on_artist_folders_toggle,
            on_album_folders_toggle=self.on_album_folders_toggle,
        )
        artist_on = bool(self._config.store_tracks_in_artist_folder)
        album_on = bool(self._config.store_tracks_in_album_folder) and artist_on
        w.var_artist_folders.set(artist_on)
        w.var_album_folders.set(album_on)
        w.set_album_folders_menu_enabled(artist_on)
        w.set_device_menu_commands(
            on_connect=self.on_connect,
            on_disconnect=self.on_disconnect,
            on_device_info=self.on_device_info,
            on_create_folder=self.action_create_folder,
            on_list_folders=self.action_read_folder_list,
            on_list_files=self.action_read_file_list,
            on_list_tracks=self.action_read_track_list,
            on_delete_track=self.action_delete_track,
            on_get_track_info=self.action_get_track_info,
            on_get_file_info=self.action_get_file_info,
            on_delete_all=self.action_delete_all_tracks,
        )
        w.set_track_context_commands(
            on_sync_track=self.action_sync_this_track,
            on_sync_album=self.action_all_from_album,
            on_sync_artist=self.action_all_from_artist,
            on_sync_artist_group=self.action_sync_artist_group,
            on_sync_album_group=self.action_sync_album_group,
            on_sync_selected=self.action_sync_selected,
        )
        w.set_prepare_context_menu(self._prepare_context_menu)
        w.set_sort_heading_handler(self.on_sort_heading)
        w.set_cancel_job_command(self.on_cancel_job)
        # Context menu: Button-3 (most platforms), Button-2.
        # Do not bind Control-Button-1 here — extended selectmode uses
        # Ctrl+click (Windows/Linux) / Cmd+click (macOS) for multi-select.
        # On macOS, Control-click is still available as a secondary context
        # gesture via the platform binding when present; prefer right-click.
        w.tree.bind("<Button-3>", w.popup_track_context)
        w.tree.bind("<Button-2>", w.popup_track_context)
        import sys as _sys

        if _sys.platform == "darwin":
            # macOS: Control-click = context menu; multi-toggle is Command-click.
            w.tree.bind("<Control-Button-1>", w.popup_track_context)
        w.tree.bind("<<TreeviewSelect>>", self._on_tree_selection_changed)
        # Apply persisted mode (PyMTP default; Stable only if config says so).
        self._apply_transfer_mode(
            self._config.active_mode(),
            persist=False,
            reason="startup",
        )
        # Restore resumable sync job (if any) for Transfer → Resume Sync.
        self._load_sync_job_for_resume()


    def _transport(self):
        if self.win.active_mode() == "stable":
            return CmdTransport()
        return self.device

    def _target_format(self) -> str:
        return self._config.normalized_send_format()

    def _device_audio_formats(self) -> frozenset[str] | None:
        """Native playable formats from the USB-matched profile, if any.

        Only set after a device is detected and profile-matched (e.g. ZEN
        Vision:M). When no session/profile is active, returns None so prepare
        only skips convert when the source already matches the Config target.
        """
        if self._active_profile is None:
            return None
        return self._active_profile.supported_audio_formats

    def on_config(self) -> None:
        """Open Config dialog; persist send format on Save."""
        new_fmt = show_config_dialog(
            self.win.root,
            send_format=self._config.normalized_send_format(),
        )
        if new_fmt is None:
            return
        self._config.send_format = new_fmt
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save config")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config send_format=%s", new_fmt)

    def on_stable_mode_toggle(self) -> None:
        """Config → Stable Mode checkbutton: switch transport and persist."""
        stable = bool(self.win.var_stable_mode.get())
        mode = "stable" if stable else "experimental"
        if stable and (
            self._config.store_tracks_in_artist_folder
            or self._config.store_tracks_in_album_folder
        ):
            # Artist/album folders need PyMTP create_folder + an open session.
            self._clear_artist_album_folder_prefs(
                reason="incompatible with Stable Mode"
            )
        self._apply_transfer_mode(mode, persist=True, reason="config_menu")

    def on_artist_folders_toggle(self) -> None:
        """Config → Store tracks in artist folder (experimental)."""
        enabled = bool(self.win.var_artist_folders.get())
        if enabled and self._config.stable_mode:
            messagebox.showinfo(
                "Artist folders",
                "Store tracks in artist folder needs PyMTP "
                "(uncheck Config → Stable Mode).\n\n"
                "It creates Music/<Artist> on the device and sends tracks "
                "into that folder id.",
            )
            self.win.var_artist_folders.set(False)
            return
        self._config.store_tracks_in_artist_folder = enabled
        if not enabled:
            # Album folders require artist folders.
            self._config.store_tracks_in_album_folder = False
            self.win.var_album_folders.set(False)
        self.win.set_album_folders_menu_enabled(enabled)
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save store_tracks_in_artist_folder")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info(
            "Config store_tracks_in_artist_folder=%s store_tracks_in_album_folder=%s",
            enabled,
            self._config.store_tracks_in_album_folder,
        )

    def on_album_folders_toggle(self) -> None:
        """Config → Store tracks in album folder (experimental)."""
        enabled = bool(self.win.var_album_folders.get())
        if enabled and not self._config.store_tracks_in_artist_folder:
            messagebox.showinfo(
                "Album folders",
                "Store tracks in album folder requires "
                "Config → Store tracks in artist folder.\n\n"
                "It creates Music/<Artist>/<Album> on the device and sends "
                "tracks into that folder id.",
            )
            self.win.var_album_folders.set(False)
            return
        if enabled and self._config.stable_mode:
            messagebox.showinfo(
                "Album folders",
                "Store tracks in album folder needs PyMTP "
                "(uncheck Config → Stable Mode).\n\n"
                "It creates Music/<Artist>/<Album> on the device and sends "
                "tracks into that folder id.",
            )
            self.win.var_album_folders.set(False)
            return
        self._config.store_tracks_in_album_folder = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save store_tracks_in_album_folder")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config store_tracks_in_album_folder=%s", enabled)

    def _clear_artist_album_folder_prefs(self, *, reason: str) -> None:
        """Turn off artist/album folder options and update the Config menu."""
        self._config.store_tracks_in_artist_folder = False
        self._config.store_tracks_in_album_folder = False
        self.win.var_artist_folders.set(False)
        self.win.var_album_folders.set(False)
        self.win.set_album_folders_menu_enabled(False)
        logger.info("Disabled artist/album folder prefs (%s)", reason)

    def _parent_folder_resolver(self):
        """Return a resolve_parent_folder callback, or None when feature is off."""
        if not self._config.store_tracks_in_artist_folder:
            return None
        if self.win.active_mode() != "experimental":
            return None
        if not self.device.is_connected():
            return None

        cache: dict[str, int] = {}
        device = self.device
        use_album = bool(self._config.store_tracks_in_album_folder)

        def resolve(meta) -> int | None:
            if use_album:
                return ensure_album_folder(device, meta, cache=cache)
            return ensure_artist_folder(device, meta, cache=cache)

        return resolve

    def _apply_transfer_mode(
        self,
        mode: str,
        *,
        persist: bool,
        reason: str,
    ) -> None:
        """Switch Stable (mtp-sendtr) vs Experimental (PyMTP) and update UI."""
        if mode not in ("stable", "experimental"):
            mode = "experimental"
        prev = self.win.active_mode()
        self._config.stable_mode = mode == "stable"
        if mode == "stable" and (
            self._config.store_tracks_in_artist_folder
            or self._config.store_tracks_in_album_folder
        ):
            self._clear_artist_album_folder_prefs(
                reason="incompatible with Stable Mode"
            )
        self.win.apply_mode_ui(mode)  # type: ignore[arg-type]
        if persist:
            try:
                save_app_config(self._config)
            except OSError as e:
                logger.exception("Failed to save stable_mode")
                messagebox.showerror("Config", f"Could not save settings:\n{e}")
        if mode == prev and reason != "startup":
            return
        logger.info(
            "Mode now %s (%s) [%s]",
            mode,
            "CMD" if mode == "stable" else "PyMTP",
            reason,
        )
        if mode == "experimental":
            # Allow auto-reconnect unless user later chooses Device → Disconnect.
            self._device_auto_reconnect = True
            self._start_device_poll()
        else:
            # Stable (mtp-sendtr) fails if a PyMTP session is already open.
            self._stop_device_poll()
            self._disconnect_for_stable()

    def _library_root_reachable(self) -> bool:
        root = self.library.root_path
        return bool(root) and os.path.isdir(root)

    def _require_experimental_connected(self) -> bool:
        """In PyMTP mode, require an open session before sync."""
        if self.win.active_mode() != "experimental":
            return True
        if self.device.is_connected():
            return True
        messagebox.showwarning(
            "Device not connected",
            "PyMTP send needs an open session with the player.\n\n"
            "• Use Device → Connect, or wait for auto-connect, or\n"
            "• Enable Config → Stable Mode (mtp-sendtr; no Connect required).",
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
        """Library usable + PyMTP connection gate when not in Stable Mode."""
        if not self._require_usable_library():
            return False
        return self._require_experimental_connected()

    def _require_device_ready(self) -> bool:
        """Device admin ops: require PyMTP mode (Stable Mode off) and a session."""
        if self.win.active_mode() != "experimental":
            messagebox.showinfo(
                "Device",
                "Device tools need PyMTP. Uncheck Config → Stable Mode first.",
            )
            return False
        if not self.device.is_connected():
            messagebox.showwarning(
                "Device not connected",
                "Use Device → Connect first (or wait for auto-connect).",
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
            # Multi-select may include only groups — try first track in selection.
            tracks = self._tracks_from_selected_iids(quiet=True)
            if len(tracks) == 1:
                return tracks[0]
            messagebox.showinfo("Index", "Select a track (not a group heading).")
            return None
        return track

    def _tracks_from_selected_iids(self, *, quiet: bool = False) -> list[Track]:
        """Resolve tree multi-selection to Track list (group headers expand)."""
        iids = self.win.selected_tree_iids()
        if not iids:
            return []
        return self._tracks_from_iids(iids)

    def _tracks_from_iids(self, iids: list[str]) -> list[Track]:
        """Map row iids to tracks; group headers include all descendant tracks."""
        tracks: list[Track] = []
        seen: set[str] = set()

        def add_from_iid(iid: str) -> None:
            track = self._track_by_iid.get(iid)
            if track is not None:
                if track.path not in seen:
                    tracks.append(track)
                    seen.add(track.path)
                return
            for child in self.win.tree.get_children(iid):
                add_from_iid(child)

        for iid in iids:
            add_from_iid(iid)
        tracks.sort(key=lambda t: t.path)
        return tracks

    def _on_tree_selection_changed(self, _event=None) -> None:
        """Refresh Transfer → Sync Selected enablement from multi-select."""
        if self._library_busy or not self.win._tracks_interactive:
            self.win.set_sync_selected_enabled(False)
            return
        tracks = self._tracks_from_selected_iids(quiet=True)
        self.win.set_sync_selected_enabled(bool(tracks), count=len(tracks))

    def _prepare_context_menu(self, row_iid: str, tags) -> None:
        """Update group/multi-select menu labels before popup."""
        tagset = set(tags)
        seed = self._group_seed_by_iid.get(row_iid)
        self._context_group_seed = seed

        # Multi-select bulk action (track rows and expanded groups).
        selected_tracks = self._tracks_from_selected_iids(quiet=True)
        n = len(selected_tracks)
        try:
            if n >= 1:
                label = (
                    f"Sync {n} selected track{'s' if n != 1 else ''}"
                )
                self.win.menu_track_ctx.entryconfig(
                    0,  # CTX_SYNC_SELECTED is first
                    label=label,
                    state=NORMAL if n >= 1 else DISABLED,
                )
            else:
                self.win.menu_track_ctx.entryconfig(
                    0, label="Sync selected tracks", state=DISABLED
                )
        except Exception:
            pass

        if seed is None:
            return
        if "group_artist" in tagset:
            artist = primary_artist(seed)
            self.win.menu_artist_ctx.entryconfig(
                0, label=f"Sync all from {artist}"
            )
        elif "group_album" in tagset:
            album = seed.meta.album or "Unknown Album"
            self.win.menu_album_ctx.entryconfig(
                0, label=f"Sync album {album}"
            )

    def _sync_from_seed(self, seed: Track | None, *, kind: str) -> None:
        """Run filter_by_artist / filter_by_album from a seed track."""
        if not self._require_sync_ready():
            return
        if seed is None:
            messagebox.showinfo("Sync", "No tracks found for this group.")
            return
        if kind == "artist":
            matches = self.library.filter_by_artist(seed)
            matches.sort(key=lambda t: t.path)
            logger.info(
                "Artist %s: %d tracks",
                primary_artist(seed),
                len(matches),
            )
        else:
            matches = self.library.filter_by_album(seed)
            matches.sort(key=lambda t: t.path)
            logger.info(
                "Album %s: %d tracks",
                seed.meta.album,
                len(matches),
            )
        if not matches:
            messagebox.showinfo("Sync", "No matching tracks found.")
            return
        if kind == "artist":
            label = f"Artist: {primary_artist(seed)}"
            job_kind = "artist"
        else:
            label = f"Album: {seed.meta.album or 'Unknown Album'}"
            job_kind = "album"
        self._transfer_many(
            matches,
            self._target_format(),
            kind=job_kind,
            label=label,
        )

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
        self._group_seed_by_iid.clear()
        self._context_group_seed = None
        self._pending_album_art = []
        tracks = list(self.library.tracks)
        if not tracks:
            self.win.set_tracks_usable(self._library_root_reachable())
            return

        primary = self._sort_primary
        reverse = self._sort_reverse

        # Build insert plan as list of ops for chunked UI work.
        # group op: ("group", parent, iid, label, tags, seed_track|None)
        # track op: ("track", parent, track)
        ops: list = []

        if primary == SortPrimary.ARTIST:
            groups = group_by_artist_album(tracks)
            if reverse:
                groups = list(reversed(groups))
            for ag in groups:
                artist_iid = ag.key
                # Seed: first track under first album (for filter_by_artist).
                artist_seed = None
                for album in ag.children:
                    if album.tracks:
                        artist_seed = album.tracks[0]
                        break
                ops.append(
                    (
                        "group",
                        "",
                        artist_iid,
                        ag.label,
                        ("group", "group_artist"),
                        artist_seed,
                    )
                )
                children = list(ag.children)
                if reverse:
                    children = list(reversed(children))
                for album in children:
                    album_seed = album.tracks[0] if album.tracks else None
                    ops.append(
                        (
                            "group",
                            artist_iid,
                            album.key,
                            album.label,
                            ("group", "group_album"),
                            album_seed,
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
                seed = g.tracks[0] if g.tracks else None
                ops.append(
                    ("group", "", g.key, g.label, ("group", "group_album"), seed)
                )
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
                # Year headers: no sync context menu (group without artist/album tag).
                ops.append(
                    ("group", "", g.key, g.label, ("group", "group_year"), None)
                )
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
                    _, parent, iid, label, tags, seed = op
                    if not tree.exists(iid):
                        # Treeview cannot colspan; full group label in Title.
                        # #0: expander + optional thumb (only from disk cache here).
                        image = ""
                        if seed is not None and "group_album" in tags:
                            photo = self.win.album_art_photo_from_disk(
                                seed.path,
                                cache_key=iid,
                                size=DEFAULT_THUMB_SIZE,
                            )
                            if photo is not None:
                                image = photo
                            else:
                                self._pending_album_art.append((iid, seed.path))
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            image=image,
                            values=(label, "", "", ""),
                            tags=tags,
                            open=True,
                        )
                        if seed is not None:
                            self._group_seed_by_iid[iid] = seed
                else:
                    _, parent, track = op
                    self._insert_track_row(parent, track)
            if end < len(ops):
                self._populate_after_id = self.win.root.after(
                    1, lambda: run_chunk(end)
                )
            else:
                self.win.set_tracks_usable(self._library_root_reachable())
                self._start_background_album_art()

        run_chunk(0)

    def _album_seed_paths(self) -> list[str]:
        """One seed track path per album (for warm cache)."""
        seen: set[tuple[str, str]] = set()
        paths: list[str] = []
        for t in self.library.tracks:
            key = (
                (t.meta.artist or "").casefold(),
                (t.meta.album or "").casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            paths.append(t.path)
        return paths

    def _start_background_album_art(self) -> None:
        """Build missing thumbs off the UI thread; apply when ready."""
        pending = list(self._pending_album_art)
        # Also warm all albums even if not visible in current sort (for later).
        warm_paths = self._album_seed_paths()
        if not pending and not warm_paths:
            return

        self._album_art_job_gen += 1
        gen = self._album_art_job_gen
        size = DEFAULT_THUMB_SIZE

        def work() -> list[tuple[str, str]]:
            # Warm full library album set first (disk only; no Tk).
            warm_album_thumbs(warm_paths, size=size)
            ready: list[tuple[str, str]] = []
            for iid, path in pending:
                if ensure_cached_thumb(path, size=size) is not None:
                    ready.append((iid, path))
            return ready

        def on_done(ready: list[tuple[str, str]]) -> None:
            if gen != self._album_art_job_gen:
                return
            for iid, path in ready:
                self.win.apply_album_art_photo(
                    iid, path, cache_key=iid, size=size
                )
            if ready:
                logger.info("Applied %d album art thumbnail(s)", len(ready))

        def on_error(exc: BaseException) -> None:
            logger.debug("Album art background job failed: %s", exc)

        def runner() -> None:
            try:
                result = work()
                self.win.root.after(0, lambda: on_done(result))
            except BaseException as exc:
                self.win.root.after(0, lambda e=exc: on_error(e))

        threading.Thread(
            target=runner, name="mtpmanager-album-art", daemon=True
        ).start()

    def _populate_listbox(self, library: Library) -> None:
        """Rebuild the track tree (name kept for call-site compatibility)."""
        self.library = library
        self._rebuild_track_tree()

    def _track_for_progress_path(self, path: str) -> Track | None:
        """Resolve a source path to Track for the progress status line."""
        if not path:
            return None
        track = self._batch_track_by_path.get(path)
        if track is not None:
            return track
        iid = self._iid_by_path.get(path)
        if iid:
            return self._track_by_iid.get(iid)
        for t in self.library.tracks:
            if t.path == path:
                return t
        return None

    def _format_sync_status_line(
        self, path: str, done: int, total: int
    ) -> str:
        """Build ``Artist/Album/Title - current/N`` for the progress bar."""
        track = self._track_for_progress_path(path)
        if track is not None:
            artist = primary_artist(track) or track.meta.artist or "Unknown Artist"
            album = (track.meta.album or "").strip() or "Unknown Album"
            title = (track.meta.title or "").strip() or "Unknown Title"
            head = f"{artist}/{album}/{title}"
        elif path:
            head = os.path.basename(path)
        else:
            head = "…"

        job = self._active_sync_job
        if job is not None and job.total > 0 and path:
            try:
                current = job.paths.index(path) + 1
                n = job.total
            except ValueError:
                current = min(max(done + 1, 1), total) if total else 0
                n = total
        else:
            if total <= 0:
                return head
            if path:
                current = min(max(done + 1, 1), total)
            else:
                current = total
            n = total
        return f"{head} - {current}/{n}"

    def _progress(self, done: int, total: int, path: str) -> None:
        if total <= 0:
            return
        pct = round((done / total) * 100)
        if done >= total and not path:
            pct = 100
        try:
            self.win.progress["value"] = pct
            if path or done < total:
                self.win.set_progress_status(
                    self._format_sync_status_line(path, done, total)
                )
            elif done >= total:
                self.win.set_progress_status("Done")
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
        self._batch_track_by_path = {t.path: t for t in tracks}
        for t in tracks:
            self._apply_track_status(t.path, "queued")

    def _clear_transfer_highlights(self) -> None:
        self.win.clear_transfer_styles()

    def _on_transfer_ui_event(self, kind: str, *rest) -> None:
        """Handle progress / track-status events from the transfer worker."""
        if kind == "track_status":
            if len(rest) >= 2:
                path = str(rest[0])
                status = str(rest[1])
                self._apply_track_status(path, status)
                self._note_sync_job_track(path, status)
                # Keep status line current during transcode/transfer phases.
                if status in ("transcoding", "transferring") and path:
                    job = self._active_sync_job
                    total = job.total if job and job.total else len(
                        self._batch_track_by_path
                    ) or 1
                    done = 0
                    if job and path in job.paths:
                        try:
                            done = job.paths.index(path)
                        except ValueError:
                            done = job.succeeded
                    self.win.set_progress_status(
                        self._format_sync_status_line(path, done, total)
                    )
            return
        if kind == "progress":
            if len(rest) >= 3:
                self._progress(int(rest[0]), int(rest[1]), str(rest[2]))
            return
        if kind == "status":
            # Long USB listing: show text in the library count slot and bar.
            if rest:
                msg = str(rest[0]).strip()
                if msg:
                    try:
                        self.win.lbl_library_count.configure(text=msg)
                    except Exception:
                        pass
                    self.win.set_progress_status(msg)
            return


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

        # Avoid racing libmtp during library/transfer work, and give the
        # session a quiet window after heavy USB (list_tracks can leave the
        # ZEN bus flaky for several seconds).
        if (
            self._library_busy
            or self._transfer_busy
            or self._device_connect_inflight
            or self._usb_is_quiet()
        ):
            self._schedule_device_poll(gen)
            return

        self._device_connect_inflight = True
        local_gen = gen
        need_identity = self._active_profile is None

        def work() -> tuple[str, DeviceInfo | None]:
            """Return (status, info). status: ok | soft_fail | gone | absent.

            Minimum USB: connect + optional identity (name/mfr/model). Never
            battery/storage here — those are Device → Device Info only.
            """
            if self.device.is_connected():
                if self.device.session_alive():
                    if not need_identity:
                        return ("ok", None)
                    try:
                        return ("ok", device_ops.get_device_identity(self.device))
                    except Exception:
                        # Probe passed; identity still failed — keep session.
                        return ("soft_fail", None)
                return ("soft_fail", None)

            try:
                device_ops.connect(self.device)
            except Exception:
                return ("absent", None)
            if not need_identity:
                return ("ok", None)
            try:
                return ("ok", device_ops.get_device_identity(self.device))
            except Exception:
                # Connected enough to open a session; profile can wait.
                logger.debug(
                    "Auto-connect: identity read failed after connect",
                    exc_info=True,
                )
                return ("ok", DeviceInfo())

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
            if status == "ok":
                self._device_probe_fails = 0
                self._logged_no_device = False
                # Only re-apply art/log when profile missing or first connect.
                if info is not None and self._active_profile is None:
                    self._apply_device_profile(info)
            elif status == "soft_fail":
                self._device_probe_fails += 1
                if self._device_probe_fails < _DEVICE_PROBE_FAIL_LIMIT:
                    logger.info(
                        "Experimental auto-connect: session probe soft-fail "
                        "%s/%s (keeping session; common after long listings)",
                        self._device_probe_fails,
                        _DEVICE_PROBE_FAIL_LIMIT,
                    )
                    self._mark_usb_quiet(_DEVICE_USB_COOLDOWN_S)
                else:
                    logger.info(
                        "Experimental auto-connect: session probe failed %s "
                        "times — disconnecting to recover",
                        self._device_probe_fails,
                    )
                    try:
                        device_ops.disconnect(self.device)
                    except Exception:
                        pass
                    self._device_probe_fails = 0
                    self._logged_no_device = False
                    self._clear_device_profile()
            elif status == "gone":
                logger.info("Experimental auto-connect: device disconnected")
                self._device_probe_fails = 0
                self._logged_no_device = False  # allow one "no device" log on next fails
                self._clear_device_profile()
            else:
                self._device_probe_fails = 0
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
        """Manual connect; re-enables auto-reconnect polling on Experimental.

        Opens the session and loads **identity only** (name / manufacturer /
        model) for the left-panel profile. Battery and storage are not queried
        here — use Device → Device Info for full diagnostics.
        """
        self._device_auto_reconnect = True
        try:
            device_ops.connect(self.device)
            self._logged_no_device = False
            try:
                info = device_ops.get_device_identity(self.device)
                self._apply_device_profile(info)
            except Exception:
                # Session is up; missing identity must not undo connect.
                logger.exception(
                    "Connected but could not load device identity "
                    "(name/manufacturer/model)"
                )
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
        """Device → Device Info: full diagnostics (battery, storage, …)."""
        if not self._require_device_ready():
            return
        try:
            # Full probe is intentional here; fields soft-fail individually.
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
    def _warm_art_for_library(library: Library) -> None:
        seeds: list[str] = []
        seen: set[tuple[str, str]] = set()
        for t in library.tracks:
            key = (
                (t.meta.artist or "").casefold(),
                (t.meta.album or "").casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            seeds.append(t.path)
        n = warm_album_thumbs(seeds, size=DEFAULT_THUMB_SIZE)
        logger.info("Warmed %d album art cache entr(y/ies)", n)

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
            lib = Library(tracks=live, root_path=loaded.root_path)
        else:
            logger.warning(
                "Library index root not reachable: %r — showing stale index",
                loaded.root_path,
            )
            lib = loaded
        try:
            AppController._warm_art_for_library(lib)
        except Exception:
            logger.debug("Album art warm after index load failed", exc_info=True)
        return lib

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
        # Warm album thumbs while still off the UI thread (same job as scan).
        try:
            AppController._warm_art_for_library(library)
        except Exception:
            logger.debug("Album art warm after scan failed", exc_info=True)
        return library, None

    def _on_library_job_done(self, library: Library | None, *, kind: str) -> None:
        self._library_busy = False
        if library is None:
            self.library = Library()
            self._cancel_populate()
            self.win.clear_track_tree()
            self._track_by_iid.clear()
            self._iid_by_path.clear()
            self._group_seed_by_iid.clear()
            self._context_group_seed = None
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
        resume_line = ""
        job = self._active_sync_job
        if batch and job is not None and job.is_resumable():
            resume_line = (
                f"Then Transfer → Resume Sync "
                f"({job.succeeded}/{job.total} already sent)."
            )

        if self.win.active_mode() == "experimental":
            lines = [
                "PyMTP send failed and was not retried automatically.",
                "",
                "Recommended recovery:",
                "1. Device → Disconnect "
                "(unplug/replug the player if Disconnect errors).",
                "2. Enable Config → Stable Mode.",
                "3. Transfer → Resume Sync (or retry the same selection).",
                "",
                "Leave Stable Mode off only if you are debugging PyMTP/libmtp; "
                "check ~/Library/Logs/MtpManager for the full error stack.",
            ]
            if batch:
                lines.insert(
                    1,
                    "The batch was stopped so remaining tracks are not sent "
                    "into a dead session.",
                )
            if resume_line:
                lines.insert(-2, resume_line)
            return "\n".join(lines)

        if batch:
            base = (
                "Batch stopped so remaining tracks are not sent into a dead "
                "MTP session. Unplug/replug the player, free space if needed."
            )
            if resume_line:
                return f"{base}\n{resume_line}"
            return f"{base}\nThen Transfer → Resume Sync from the failed track."
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
                "Busy",
                "A background job is already running.\n\n"
                "Wait for it to finish, or click Cancel to stop after the "
                "current item.",
            )
            return False
        self._transfer_busy = True
        self._job_cancel.clear()
        self.win.set_cancel_job_enabled(True)
        self._clear_transfer_highlights()
        # Hold auto-connect off the bus for the whole job + cooldown tail.
        self._mark_usb_quiet(_DEVICE_USB_COOLDOWN_S)
        try:
            self.win.progress["value"] = 0
        except Exception:
            pass
        self.win.set_progress_status("")
        return True

    def _end_transfer_job(self) -> None:
        self._transfer_busy = False
        self._job_cancel.clear()
        self.win.set_cancel_job_enabled(False)
        try:
            self.win.btn_cancel_job.configure(text="Cancel")
        except Exception:
            pass
        self._clear_transfer_highlights()
        self._stop_busy_progress()
        self._batch_track_by_path = {}
        self.win.set_progress_status("")
        # Listing/transfer just finished — pause auto-connect USB probes.
        self._device_probe_fails = 0
        self._mark_usb_quiet()

    def on_cancel_job(self) -> None:
        """Progress-bar Cancel: stop after the current track/delete finishes."""
        if not self._transfer_busy:
            return
        if self._job_cancel.is_set():
            return
        self._job_cancel.set()
        logger.info("User requested cancel of current background job")
        try:
            self.win.btn_cancel_job.configure(text="Cancelling…", state=DISABLED)
        except Exception:
            pass

    def _should_cancel_job(self) -> bool:
        return self._job_cancel.is_set()

    def _handle_job_cancelled(self, exc: JobCancelled, *, title: str) -> None:
        """User-facing feedback after cooperative cancel (main thread)."""
        completed = exc.completed
        total = exc.total
        if total > 0:
            detail = f"Stopped after {completed} of {total} item(s)."
        elif completed:
            detail = f"Stopped after {completed} item(s)."
        else:
            detail = "Stopped before any items finished."
        logger.info("%s: %s", title, detail)
        messagebox.showinfo(title, f"{title}.\n\n{detail}")

    def _mark_usb_quiet(self, seconds: float = _DEVICE_USB_COOLDOWN_S) -> None:
        """Defer auto-connect probes until *seconds* after now (monotonic)."""
        until = time.monotonic() + max(0.0, float(seconds))
        if until > self._usb_quiet_until:
            self._usb_quiet_until = until

    def _usb_is_quiet(self) -> bool:
        return time.monotonic() < self._usb_quiet_until

    def _start_busy_progress(self) -> None:
        """Indeterminate bar while a USB listing (etc.) runs off the UI thread."""
        try:
            self.win.progress.configure(mode="indeterminate")
            self.win.progress.start(12)
        except Exception:
            try:
                self.win.progress["value"] = 0
            except Exception:
                pass

    def _stop_busy_progress(self) -> None:
        try:
            self.win.progress.stop()
        except Exception:
            pass
        try:
            self.win.progress.configure(mode="determinate")
            self.win.progress["value"] = 0
        except Exception:
            pass

    def _run_device_bg(
        self,
        *,
        title: str,
        name: str,
        work,
        on_success,
        busy_message: str | None = None,
        on_progress=None,
        progress_mode: str = "indeterminate",
    ) -> None:
        """Run blocking device I/O off the Tk thread; deliver UI on main thread.

        USB listings (track/file) can take tens of seconds and emit libmtp
        panics to stderr — never call them on the main thread.

        *on_progress* is a main-thread handler for worker progress events
        (same shape as transfer: kind + args). When *progress_mode* is
        ``\"determinate\"``, the bar starts at 0% instead of pulsing.
        """
        if not self._require_device_ready():
            return
        if not self._begin_transfer_job():
            return
        if busy_message:
            logger.info("%s: %s", title, busy_message)
        device = self.device
        if progress_mode == "determinate":
            try:
                self.win.progress.configure(mode="determinate")
                self.win.progress["value"] = 0
            except Exception:
                pass
        else:
            self._start_busy_progress()

        def _work():
            return work(device)

        def on_done(result) -> None:
            self._end_transfer_job()
            # Restore library count if listing overwrote the toolbar status.
            try:
                self._sync_library_chrome()
            except Exception:
                pass
            try:
                on_success(result)
            except Exception:
                logger.exception("%s UI handler failed", title)
                messagebox.showerror(title, "Could not show results (see log).")

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            try:
                self._sync_library_chrome()
            except Exception:
                pass
            logger.exception("%s failed", title)
            messagebox.showerror(title, str(exc))

        self._bg.submit(
            _work,
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
            name=name,
        )

    def _transfer_one(self, track: Track, fmt: str) -> None:
        if not self._begin_transfer_job():
            return
        # Capture transport / formats on main thread (mode may change later).
        transport = self._transport()
        transcoder = self.transcoder
        device_formats = self._device_audio_formats()
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
                    "Single-track transfer start: path=%s target_format=%s "
                    "device_formats=%s",
                    path,
                    fmt,
                    sorted(device_formats) if device_formats else None,
                )
                self._batch_track_by_path = {track.path: track}
                report("progress", 0, 1, track.path)
                transfer_track(
                    track,
                    target_format=fmt,
                    transport=transport,
                    transcoder=transcoder,
                    slot=0,
                    on_track_status=on_track_status,
                    resolve_parent_folder=self._parent_folder_resolver(),
                    device_formats=device_formats,
                    should_cancel=self._should_cancel_job,
                )
                report("progress", 1, 1, "")
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
            if isinstance(exc, JobCancelled):
                self._handle_job_cancelled(exc, title="Transfer cancelled")
                return
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

    def _load_sync_job_for_resume(self) -> None:
        """Load durable job from disk; enable Resume Sync when applicable."""
        job = load_sync_job()
        if job is None:
            self._active_sync_job = None
            self.win.set_resume_sync_enabled(False)
            return
        # Stale "running" after a crash → treat as failed so Resume is offered.
        if job.status == "running" and job.is_resumable():
            job.status = "failed"
            job.last_error = job.last_error or "Interrupted (app quit or crash)"
            try:
                save_sync_job(job)
            except OSError:
                logger.exception("Could not update interrupted sync job")
        self._active_sync_job = job
        self.win.set_resume_sync_enabled(job.is_resumable())
        if job.is_resumable():
            logger.info("Resumable sync job: %s", job.summary_line())

    def _persist_sync_job(self) -> None:
        job = self._active_sync_job
        if job is None:
            return
        try:
            save_sync_job(job)
        except OSError:
            logger.exception("Failed to save sync job progress")

    def _refresh_resume_menu(self) -> None:
        job = self._active_sync_job
        self.win.set_resume_sync_enabled(bool(job and job.is_resumable()))

    def _note_sync_job_track(self, path: str, status: str) -> None:
        """Update durable job progress from per-track status (main thread)."""
        job = self._active_sync_job
        if job is None or job.status == "completed":
            return
        if status == "done":
            if job.mark_path_done(path):
                self._persist_sync_job()
        elif status == "failed":
            job.mark_path_failed(path)
            self._persist_sync_job()
            self._refresh_resume_menu()

    def _finish_sync_job_success(self) -> None:
        job = self._active_sync_job
        if job is None:
            return
        job.mark_completed()
        self._persist_sync_job()
        self._refresh_resume_menu()
        logger.info("Sync job completed: %s", job.summary_line())

    def _finish_sync_job_cancelled(self, exc: JobCancelled) -> None:
        job = self._active_sync_job
        if job is None:
            return
        # next_index already advanced for completed items via track_status.
        job.mark_cancelled()
        self._persist_sync_job()
        self._refresh_resume_menu()
        logger.info(
            "Sync job cancelled: %s (session completed=%s)",
            job.summary_line(),
            exc.completed,
        )

    def _finish_sync_job_failed(self, exc: BaseException) -> None:
        job = self._active_sync_job
        if job is None:
            return
        path = ""
        if isinstance(exc, TransportError):
            path = (exc.path or "").strip()
        if not path:
            path = job.last_failed_path or (
                job.paths[job.next_index]
                if job.next_index < job.total
                else ""
            )
        job.mark_path_failed(path, str(exc))
        self._persist_sync_job()
        self._refresh_resume_menu()
        logger.info("Sync job failed: %s", job.summary_line())

    def _tracks_for_paths(self, paths: list[str]) -> list[Track]:
        """Map source paths to Track objects (library first, else re-read tags)."""
        by_path = {t.path: t for t in self.library.tracks}
        out: list[Track] = []
        for p in paths:
            if p in by_path:
                out.append(by_path[p])
                continue
            if os.path.isfile(p):
                try:
                    meta = read_metadata(p)
                except Exception:
                    logger.warning("Could not read tags for resume path %s", p)
                    meta = TrackMetadata()
                out.append(Track(path=p, meta=meta))
            else:
                logger.warning("Resume: skipping missing path %s", p)
        return out

    def _skip_missing_job_head(self, job: SyncJobState) -> None:
        """Advance past missing files at the resume head so we do not stall."""
        by_path = {t.path for t in self.library.tracks}
        while job.next_index < job.total:
            p = job.paths[job.next_index]
            if p in by_path or os.path.isfile(p):
                break
            logger.warning("Resume: advance past missing %s", p)
            job.next_index += 1
            job.updated_at = job.updated_at  # touch via mark later
        if job.next_index >= job.total:
            job.mark_completed()

    def _transfer_many(
        self,
        tracks: list[Track],
        fmt: str = "mp3",
        *,
        kind: str = "batch",
        label: str = "",
        resume_job: SyncJobState | None = None,
    ) -> None:
        if not tracks:
            messagebox.showinfo("Transfer", "No tracks to transfer.")
            return
        if not self._begin_transfer_job():
            return

        transport = self._transport()
        transcoder = self.transcoder
        device_formats = self._device_audio_formats()
        # Snapshot the list so library changes during transfer do not race.
        batch = list(tracks)
        mode = self.win.active_mode()

        if resume_job is not None:
            job = resume_job
            job.mark_running()
            # Remaining batch must align with job.paths[job.next_index:].
            self._active_sync_job = job
        else:
            job = new_sync_job(
                paths=[t.path for t in batch],
                kind=kind,
                label=label or kind,
                target_format=fmt,
                mode=mode,
            )
            self._active_sync_job = job
        self._persist_sync_job()
        self.win.set_resume_sync_enabled(False)
        self._mark_batch_queued(batch)
        logger.info(
            "Sync job start: %s (batch_now=%d)",
            job.summary_line(),
            len(batch),
        )

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
                resolve_parent_folder=self._parent_folder_resolver(),
                device_formats=device_formats,
                should_cancel=self._should_cancel_job,
            )

        def on_done(succeeded: int) -> None:
            self._finish_sync_job_success()
            self._end_transfer_job()
            logger.info("Background batch finished: succeeded=%s", succeeded)

        def on_error(exc: BaseException) -> None:
            if isinstance(exc, JobCancelled):
                self._finish_sync_job_cancelled(exc)
                self._end_transfer_job()
                self._handle_job_cancelled(exc, title="Transfer cancelled")
                return
            self._finish_sync_job_failed(exc)
            self._end_transfer_job()
            if isinstance(exc, TransportError):
                self._log_transport_error("Batch transfer aborted", exc)
                title = "Transfer aborted" if exc.fatal else "Transfer failed"
                self._show_transfer_error(title, exc, batch=True)
                job_now = self._active_sync_job
                if job_now and job_now.is_resumable():
                    logger.info("Resume available: %s", job_now.summary_line())
                return
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
        # If multiple tracks are selected, treat as bulk selection sync.
        selected = self._tracks_from_selected_iids(quiet=True)
        if len(selected) > 1:
            self.action_sync_selected()
            return
        track = self._selected_track()
        if track is None:
            return
        self._transfer_one(track, self._target_format())

    def action_sync_selected(self) -> None:
        """Sync multi-selected tracks (Shift/Ctrl/Cmd selection) as one job."""
        if not self._require_sync_ready():
            return
        tracks = self._tracks_from_selected_iids(quiet=True)
        if not tracks:
            messagebox.showinfo(
                "Sync Selected",
                "Select one or more tracks first.\n\n"
                "• Click a track to select it\n"
                "• Shift+click for a range\n"
                "• Ctrl+click (Windows/Linux) or ⌘+click (macOS) to toggle\n"
                "• Group headers include all tracks under that group",
            )
            return
        if len(tracks) == 1:
            self._transfer_one(tracks[0], self._target_format())
            return
        n = len(tracks)
        fmt = self._target_format().upper()
        mode = (
            "Stable (mtp-sendtr)"
            if self.win.active_mode() == "stable"
            else "PyMTP"
        )
        if not messagebox.askyesno(
            "Sync Selected Tracks",
            f"Send {n} selected track(s) as {fmt} using {mode}?\n\n"
            "Progress is saved; use Transfer → Resume Sync after a failure.",
        ):
            return
        self._transfer_many(
            tracks,
            self._target_format(),
            kind="selection",
            label=f"Selection ({n} tracks)",
        )

    def action_all_from_artist(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        self._sync_from_seed(track, kind="artist")

    def action_all_from_album(self) -> None:
        track = self._selected_track()
        if track is None:
            return
        self._sync_from_seed(track, kind="album")

    def action_sync_artist_group(self) -> None:
        """Context menu on an artist header row."""
        seed = self._context_group_seed
        if seed is None:
            iid = self.win.selected_tree_iid()
            seed = self._group_seed_by_iid.get(iid or "")
        self._sync_from_seed(seed, kind="artist")

    def action_sync_album_group(self) -> None:
        """Context menu on an album header row."""
        seed = self._context_group_seed
        if seed is None:
            iid = self.win.selected_tree_iid()
            seed = self._group_seed_by_iid.get(iid or "")
        self._sync_from_seed(seed, kind="album")

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
            f"{'Stable (mtp-sendtr)' if self.win.active_mode() == 'stable' else 'PyMTP'}?\n\n"
            "This may take a long time.\n"
            "Progress is saved; use Transfer → Resume Sync after a failure.",
        ):
            return
        tracks = list(self.library.tracks)
        tracks.sort(key=lambda t: t.path)
        self._transfer_many(
            tracks,
            self._target_format(),
            kind="entire_library",
            label="Entire library",
        )

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
        tracks = list(album_lib.tracks)
        tracks.sort(key=lambda t: t.path)
        self._transfer_many(
            tracks,
            self._target_format(),
            kind="folder",
            label=f"Folder: {path}",
        )

    def action_resume_sync(self) -> None:
        """Transfer → Resume Sync: continue durable job from last failure."""
        if not self._require_sync_ready():
            return
        job = self._active_sync_job or load_sync_job()
        if job is None or not job.is_resumable():
            messagebox.showinfo(
                "Resume Sync",
                "No interrupted sync job to resume.\n\n"
                "Start a multi-track sync (Entire Library, Folder, Album, "
                "or Artist); progress is saved if it fails or is cancelled.",
            )
            self.win.set_resume_sync_enabled(False)
            return

        self._skip_missing_job_head(job)
        if not job.is_resumable():
            self._active_sync_job = job
            self._persist_sync_job()
            self._refresh_resume_menu()
            messagebox.showinfo(
                "Resume Sync",
                "Nothing left to send for the saved job "
                f"({job.succeeded}/{job.total} already done).",
            )
            return

        remaining_paths = job.remaining_paths()
        tracks = self._tracks_for_paths(remaining_paths)
        if not tracks:
            messagebox.showinfo(
                "Resume Sync",
                "Saved job has remaining paths, but none are available on disk.",
            )
            return

        # If some middle paths were missing, remaining_paths may be longer than
        # tracks; align by only sending resolved tracks and leave job.paths as-is
        # (mark_path_done advances by path).
        fmt = job.target_format or self._target_format()
        mode_label = (
            "Stable (mtp-sendtr)"
            if self.win.active_mode() == "stable"
            else "PyMTP"
        )
        if not messagebox.askyesno(
            "Resume Sync",
            f"{job.summary_line()}\n\n"
            f"Resume {len(tracks)} remaining track(s) as {fmt.upper()} "
            f"using {mode_label}?\n\n"
            "Starts at the last failed / next unsent track.",
        ):
            return

        # Ensure next_index points at first path we will actually send.
        first = tracks[0].path
        try:
            idx = job.paths.index(first)
            if idx > job.next_index:
                job.next_index = idx
        except ValueError:
            pass
        self._active_sync_job = job
        self._transfer_many(
            tracks,
            fmt,
            kind=job.kind or "resume",
            label=job.label or "Resume",
            resume_job=job,
        )

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
        """Device → List Folders (USB; run off the Tk thread)."""

        def on_success(folders) -> None:
            for entry in folders:
                logger.debug("Folder: %s", entry.name)
            show_folder_list_dialog(self.win.root, folders)

        self._run_device_bg(
            title="Folders",
            name="list-folders",
            work=lambda device: device_ops.list_folders(device),
            on_success=on_success,
            busy_message="listing device folders in background…",
        )

    def action_read_file_list(self) -> None:
        """Experimental Device → List Files (full MTP file listing)."""

        def on_success(files) -> None:
            logger.info("List Files (experimental): %d object(s)", len(files))
            for entry in files[:50]:
                logger.debug(
                    "File id=%s parent=%s type=%s size=%s name=%r",
                    entry.item_id,
                    entry.parent_id,
                    entry.filetype,
                    entry.filesize,
                    entry.name,
                )
            if len(files) > 50:
                logger.debug(
                    "… %d more file(s) not logged at DEBUG", len(files) - 50
                )
            show_file_list_dialog(self.win.root, files)

        self._run_device_bg(
            title="Files",
            name="list-files",
            work=lambda device: device_ops.list_files(device),
            on_success=on_success,
            busy_message="listing device files in background…",
        )

    def action_read_track_list(self) -> None:
        """Experimental Device → List Tracks (fast file listing + optional tags)."""

        def on_success(tracks) -> None:
            logger.info(
                "List Tracks (experimental): %d track(s) from file listing",
                len(tracks),
            )
            for entry in tracks[:50]:
                logger.debug(
                    "Track id=%s parent=%s type=%s artist=%r title=%r name=%r",
                    entry.item_id,
                    entry.parent_id,
                    entry.filetype,
                    entry.artist,
                    entry.title,
                    entry.name,
                )
            if len(tracks) > 50:
                logger.debug(
                    "… %d more track(s) not logged at DEBUG", len(tracks) - 50
                )

            def on_load_tags(selected, apply_updates) -> None:
                """Background get_track_metadata for dialog selection only."""
                batch = list(selected or [])
                if not batch:
                    apply_updates([], message="No tracks selected.")
                    return
                if not self.device.is_connected():
                    apply_updates(
                        [],
                        message="Device not connected — reconnect and try again.",
                    )
                    messagebox.showerror(
                        "Load tags",
                        "Device is not connected. Use Device → Connect first.",
                    )
                    return
                if not self._begin_transfer_job():
                    apply_updates(
                        [],
                        message="Another device job is busy — try again shortly.",
                    )
                    return

                def work():
                    gen = self._bg.generation
                    report = self._bg.progress_callback(gen)

                    def on_progress(done: int, total: int, message: str) -> None:
                        report("status", message)
                        report("progress", done, total, message)

                    return device_ops.enrich_track_refs(
                        self.device,
                        batch,
                        on_progress=on_progress,
                    )

                def on_done(result) -> None:
                    self._end_transfer_job()
                    try:
                        self.win.progress["value"] = 100
                    except Exception:
                        pass
                    msg = (
                        f"Updated {result.updated} of {len(batch)} "
                        f"(failed {result.failed})."
                    )
                    if result.aborted:
                        msg = (
                            f"Aborted after fatal error at id={result.failed_id}. "
                            f"Updated {result.updated} before stop. "
                            "Disconnect/replug if the session looks stuck."
                        )
                        messagebox.showerror("Load tags aborted", msg)
                    elif result.failed and result.updated == 0:
                        messagebox.showwarning(
                            "Load tags",
                            f"Could not load tags for the selection "
                            f"({result.failed} failed).",
                        )
                    apply_updates(list(result.refs), message=msg)
                    logger.info(
                        "List Tracks load tags: updated=%s failed=%s aborted=%s",
                        result.updated,
                        result.failed,
                        result.aborted,
                    )

                def on_error(exc: BaseException) -> None:
                    self._end_transfer_job()
                    logger.exception("Load tags failed")
                    messagebox.showerror("Load tags", str(exc))
                    apply_updates([], message=f"Failed: {exc}")

                self._bg.submit(
                    work,
                    on_done=on_done,
                    on_error=on_error,
                    on_progress=self._on_transfer_ui_event,
                    name="list-tracks-enrich",
                )

            show_track_list_dialog(
                self.win.root,
                tracks,
                on_load_tags=on_load_tags,
            )

        self._run_device_bg(
            title="Tracks",
            name="list-tracks",
            work=lambda device: device_ops.list_tracks(device),
            on_success=on_success,
            busy_message="listing device tracks (file listing)…",
        )

    def action_delete_track(self) -> None:
        """Experimental Device → Delete Track: pick from file listing, delete by id."""

        def on_listed(files) -> None:
            if not files:
                messagebox.showinfo(
                    "Delete Track",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Delete Track (experimental): %d object(s) listed", len(files)
            )

            def _confirm(entry) -> str:
                name = (entry.name or "").strip() or "(unnamed)"
                return (
                    f"Delete object id={entry.item_id}?\n\n"
                    f"{name}\n"
                    f"parent={entry.parent_id}  type={entry.filetype}\n\n"
                    "This cannot be undone from the app."
                )

            entry = pick_file_entry_dialog(
                self.win.root,
                files,
                title="Delete Track (experimental)",
                prompt=(
                    "select one to delete by object id. "
                    "Folders and system objects are included; choose carefully."
                ),
                action_label="Delete…",
                confirm_message=_confirm,
            )
            if entry is None:
                return
            try:
                device_ops.delete_object(self.device, entry.item_id)
            except TransportError as e:
                logger.exception("Delete track failed id=%s", entry.item_id)
                messagebox.showerror("Delete Track", str(e))
                return
            except Exception as e:
                logger.exception("Delete track failed id=%s", entry.item_id)
                messagebox.showerror("Delete Track", str(e))
                return
            name = (entry.name or "").strip() or "(unnamed)"
            messagebox.showinfo(
                "Delete Track",
                f"Deleted object id={entry.item_id}\n{name}",
            )

        self._run_device_bg(
            title="Delete Track",
            name="delete-track-list",
            work=lambda device: device_ops.list_files(device),
            on_success=on_listed,
            busy_message="listing device files for delete picker…",
        )

    def action_delete_all_tracks(self) -> None:
        """Experimental Device → Delete All Tracks: list tracks, confirm, batch delete."""

        def on_listed(tracks) -> None:
            if not tracks:
                messagebox.showinfo(
                    "Delete All Tracks",
                    "No tracks found on the device.",
                )
                return

            n = len(tracks)
            logger.info(
                "Delete All Tracks (experimental): %d track(s) listed", n
            )
            if not messagebox.askyesno(
                "Delete All Tracks",
                f"Delete all {n} track(s) from the device?\n\n"
                "This deletes music/video tracks from the device track "
                "listing (folders and photos are not deleted).\n\n"
                "This cannot be undone from the app.",
                icon=messagebox.WARNING,
                default=messagebox.NO,
            ):
                return
            # Second confirm for large libraries.
            if n >= 10 and not messagebox.askyesno(
                "Delete All Tracks — confirm",
                f"Really permanently delete {n} tracks?",
                icon=messagebox.WARNING,
                default=messagebox.NO,
            ):
                return

            if not self._begin_transfer_job():
                return
            device = self.device
            batch = list(tracks)

            def work():
                gen = self._bg.generation
                report = self._bg.progress_callback(gen)

                def on_progress(done: int, total: int, current) -> None:
                    label = ""
                    if current is not None:
                        label = (
                            (current.name or current.title or "").strip()
                            or f"id={current.item_id}"
                        )
                    report("progress", done, total, label)

                return device_ops.delete_all_tracks(
                    device,
                    batch,
                    on_progress=on_progress,
                    should_cancel=self._should_cancel_job,
                )

            def on_done(result) -> None:
                self._end_transfer_job()
                try:
                    self.win.progress["value"] = 100
                except Exception:
                    pass
                if result.cancelled:
                    messagebox.showinfo(
                        "Delete All Tracks cancelled",
                        f"Stopped after deleting {result.deleted} of "
                        f"{result.total} track(s).",
                    )
                    return
                if result.aborted:
                    messagebox.showerror(
                        "Delete All Tracks aborted",
                        f"Deleted {result.deleted} of {result.total} track(s).\n"
                        f"Stopped at object id={result.failed_id}.\n\n"
                        "Session may be poisoned — disconnect/replug before "
                        "retrying, or use Config → Stable Mode for transfers.",
                    )
                    return
                messagebox.showinfo(
                    "Delete All Tracks",
                    f"Deleted {result.deleted} of {result.total} track(s).",
                )

            def on_error(exc: BaseException) -> None:
                self._end_transfer_job()
                if isinstance(exc, JobCancelled):
                    self._handle_job_cancelled(
                        exc, title="Delete All Tracks cancelled"
                    )
                    return
                logger.exception("Delete All Tracks failed")
                messagebox.showerror("Delete All Tracks", str(exc))

            self._bg.submit(
                work,
                on_done=on_done,
                on_error=on_error,
                on_progress=self._on_transfer_ui_event,
                name="delete-all-tracks",
            )

        self._run_device_bg(
            title="Delete All Tracks",
            name="delete-all-list",
            work=lambda device: device_ops.list_tracks(device),
            on_success=on_listed,
            busy_message="listing device tracks before delete (file listing)…",
        )

    def action_get_file_info(self) -> None:
        """Experimental Device → Get File Info: pick from listing, fetch metadata."""

        def on_listed(files) -> None:
            if not files:
                messagebox.showinfo(
                    "File Info",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Get File Info (experimental): %d object(s) listed", len(files)
            )
            entry = pick_file_entry_dialog(
                self.win.root,
                files,
                title="Get File Info (experimental)",
                prompt=(
                    "select one object to inspect by id "
                    "(LIBMTP_Get_Filemetadata)."
                ),
                action_label="Get Info",
            )
            if entry is None:
                return
            # Prefer a live Get_Filemetadata refresh; on ZEN some listed/playable
            # handles still return NULL (proplist path). Listing already has every
            # field File Info shows — fall back instead of claiming "not found".
            meta = entry
            source = "listing"
            try:
                meta = device_ops.get_file_metadata(self.device, entry.item_id)
                source = "live"
            except TransportError as e:
                if e.fatal:
                    logger.exception("Get file info failed id=%s", entry.item_id)
                    messagebox.showerror("File Info", str(e))
                    return
                logger.warning(
                    "Get file info live refresh failed id=%s (%s); "
                    "showing listing snapshot",
                    entry.item_id,
                    e,
                )
                meta = entry
                source = "listing"
            except Exception as e:
                logger.exception("Get file info failed id=%s", entry.item_id)
                messagebox.showerror("File Info", str(e))
                return
            logger.info(
                "File Info id=%s name=%r parent=%s type=%s size=%s source=%s",
                meta.item_id,
                meta.name,
                meta.parent_id,
                meta.filetype,
                meta.filesize,
                source,
            )
            note = None
            if source == "listing":
                note = (
                    "Source: file listing snapshot "
                    "(live Get_Filemetadata failed for this id — common on ZEN "
                    "when MTP property-list refresh fails; object is still listed)."
                )
            show_file_info_dialog(self.win.root, meta, note=note)

        self._run_device_bg(
            title="File Info",
            name="get-file-info-list",
            work=lambda device: device_ops.list_files(device),
            on_success=on_listed,
            busy_message="listing device files for File Info picker…",
        )

    def action_get_track_info(self) -> None:
        """Experimental Device → Get Track Info: pick audio-ish object, fetch tags."""

        def on_listed(files) -> None:
            # Prefer objects that look like tracks (audio/video filetypes or
            # common extensions). Folders and non-media still appear if nothing
            # matches — libmtp will reject non-tracks with ObjectNotFound.
            candidates = [e for e in files if looks_like_track(e)]
            pool = candidates if candidates else list(files or [])
            if not pool:
                messagebox.showinfo(
                    "Track Info",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Get Track Info (experimental): %d candidate(s) of %d listed",
                len(pool),
                len(files or []),
            )
            entry = pick_file_entry_dialog(
                self.win.root,
                pool,
                title="Get Track Info (experimental)",
                prompt=(
                    "select one track to inspect "
                    "(LIBMTP_Get_Trackmetadata — on-device tags; USB-heavy)."
                ),
                action_label="Get Track Info",
            )
            if entry is None:
                return
            try:
                info = device_ops.get_track_metadata(self.device, entry.item_id)
            except TransportError as e:
                logger.exception("Get track info failed id=%s", entry.item_id)
                messagebox.showerror("Track Info", str(e))
                return
            except Exception as e:
                logger.exception("Get track info failed id=%s", entry.item_id)
                messagebox.showerror("Track Info", str(e))
                return
            logger.info(
                "Track Info id=%s name=%r title=%r artist=%r album=%r",
                info.item_id,
                info.name,
                info.title,
                info.artist,
                info.album,
            )
            show_track_info_dialog(self.win.root, info)

        self._run_device_bg(
            title="Track Info",
            name="get-track-info-list",
            work=lambda device: device_ops.list_files(device),
            on_success=on_listed,
            busy_message="listing device files for Track Info picker…",
        )
