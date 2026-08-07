"""Map UI events to application services."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from tkinter import DISABLED, NORMAL, filedialog, messagebox

from mtpmanager.app import device_ops
from mtpmanager.app import retail_ops
from mtpmanager.app.artist_folders import ensure_album_folder, ensure_artist_folder
from mtpmanager.app.cancellation import JobCancelled
from mtpmanager.app.album_art_device import push_album_art_for_tracks
from mtpmanager.app.device_io_gate import DEFAULT_USB_QUIET_S, DeviceIoGate
from mtpmanager.infra.device_session_lock import DeviceSessionLock
from mtpmanager.app.playlist_device import (
    RECREATE_METADATA_AUTO_MAX,
    append_ids_to_order,
    apply_metadata_infos_to_resolved_tracks,
    merge_device_playlists,
    move_ids_by_indices,
    ordered_guids_from_tracks,
    playlist_candidates_from_files,
    playlist_display_name,
    playlists_parent_id,
    push_playlist_to_device,
    remove_ids_at_indices,
    resolve_device_playlist_to_host_tracks,
    resolve_track_object_ids,
    save_resolved_tracks_as_host_playlist,
)
from mtpmanager.app.podcast_ops import (
    INITIAL_EPISODE_LIMIT,
    MORE_EPISODE_STEP,
    clear_downloaded_podcasts,
    discard_episode_local_files,
    due_podcasts_for_schedule,
    load_more_episodes,
    mark_episodes_device_synced,
    pending_episodes_for_device_sync,
    pick_latest_not_on_device,
    prepare_episodes_for_sync,
    refresh_podcast,
    run_full_sync_host_pass,
    send_podcast_video_to_zencast,
    subscribe_feed,
)
from mtpmanager.app.podcast_schedule import (
    format_schedule_summary,
    next_run_after,
    podcast_day_playlist_name,
)
from mtpmanager.infra.day_podcast_playlist import (
    append_day_playlist_guid,
    clear_day_playlist_plan,
    ensure_day_playlist_plan,
    load_day_playlist_plan,
)
from mtpmanager.app.scan_library import scan_library, scan_library_roots
from mtpmanager.app.transfer import transfer_track, transfer_tracks
from mtpmanager.app.transfer_queue import BatchTransferQueue
from mtpmanager.domain.device_profile import DeviceProfile, match_device_profile
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES
from mtpmanager.domain.library import (
    Library,
    is_audiobook_track,
    is_video_track,
    merge_scanned_roots,
    normalize_library_roots,
    partition_library_media,
    primary_artist,
    video_display_title,
    year_from_date,
)
from mtpmanager.domain.library_sort import (
    SortPrimary,
    group_by_album,
    group_by_artist_album,
    group_by_artist_dash_album,
    group_by_artist_album_year,
    group_by_directory,
    group_by_year,
    group_videos_for_library,
    iter_track_cells,
    next_artist_column_sort,
    sort_tracks_flat,
)
from mtpmanager.domain.device_folders import (
    DeviceFolderLayout,
    legacy_zen_vision_m_layout,
)
from mtpmanager.domain.device_media import (
    enrich_refs_from_host,
    expand_podcast_parent_ids,
    looks_like_track,
    refs_needing_device_tags,
    resolve_device_tracks_for_display,
    track_meta_is_usable,
    track_refs_from_files,
    video_folder_label,
)
from mtpmanager.domain.models import (
    DeviceInfo,
    DevicePlaylist,
    DeviceTrackRef,
    Track,
    TrackMetadata,
)
from mtpmanager.domain.track_id import (
    guid_from_remote_name,
    is_track_guid,
    new_track_guid,
)
from mtpmanager.infra.album_art import (
    DEFAULT_THUMB_SIZE,
    ensure_cached_thumb,
    warm_album_thumbs,
)
from mtpmanager.infra.app_config import AppConfig, load_app_config, save_app_config
from mtpmanager.infra.cmd_transport import CmdTransport
from mtpmanager.infra.device_assets import device_graphic_path
from mtpmanager.infra.device_index import (
    device_list_is_complete,
    device_serial_key,
    guid_stems_on_device,
    list_cached_files,
    list_cached_music_refs,
    list_cached_podcast_refs,
    list_cached_video_refs,
    record_send,
    remove_by_item_id,
    replace_device_listing,
    upsert_device,
)
from mtpmanager.infra.audio_player import AudioPlayer, ffplay_bin
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import (
    exclude_library_paths,
    get_tracks_by_guids,
    list_library_exclusions,
    load_exclusion_paths,
    load_library_index,
    remove_library_exclusions,
    save_library_index,
    untrack_library_roots,
)
from mtpmanager.domain.playlist_shuffle import (
    merge_shuffle,
    rng_from_seed_track,
    spotify_shuffle,
)
from mtpmanager.infra.playlists import (
    append_tracks_to_playlist,
    create_playlist,
    delete_playlist,
    get_playlist,
    get_playlist_by_name,
    list_playlists,
    move_paths_in_playlist,
    remove_paths_from_playlist,
    rename_playlist,
    replace_playlist_tracks,
    resolve_playlist_tracks,
)
from mtpmanager.infra.podcast_index import (
    delete_podcast,
    get_episode,
    get_podcast,
    get_tracks_by_podcast_guids,
    known_podcast_guids,
    list_episodes,
    list_podcasts,
)
from mtpmanager.ui.dialogs import ask_add_to_playlist, ask_text
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.infra.pymtp_device import PymtpDevice
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    build_remote_path,
    split_remote_path,
)
from mtpmanager.infra.sync_job import (
    SyncJobState,
    load_sync_job,
    new_sync_job,
    save_sync_job,
)
from mtpmanager.ports.transport import TransportError
from mtpmanager.ui.bg import TkBackgroundRunner
from mtpmanager.ui.dialogs import (
    ExclusionsManagerDialog,
    ManageLibraryDialog,
    ask_text,
    ask_video_destination,
    open_manage_library_dialog,
    pick_file_entry_dialog,
    show_config_dialog,
    show_device_info_dialog,
    show_file_info_dialog,
    show_file_list_dialog,
    show_folder_list_dialog,
    show_podcast_settings_dialog,
    show_track_info_dialog,
    show_track_list_dialog,
)
from mtpmanager.ui.formatting import (
    album_selection_detail,
    artist_selection_detail,
    multi_selection_detail,
    track_selection_detail,
)
from mtpmanager.ui.window import MainWindow

logger = logging.getLogger(__name__)

# Progressive Treeview inserts: Fibonacci chunk sizes (1, 1, 2, 3, 5, …)
# so the first rows appear immediately, then fewer/larger idle slices as the
# index deepens (less after() overhead on huge libraries). Cap avoids one
# multi-second freeze when fib outgrows what Tk can insert smoothly.
_TREE_CHUNK_FIB_FIRST = 1
_TREE_CHUNK_FIB_SECOND = 1
_TREE_CHUNK_CAP = 512

# Podcast full-sync schedule poll interval (catch-up + due check).
_PODCAST_SCHEDULE_POLL_MS = 60_000


def fibonacci_chunk_bounds(
    total: int,
    *,
    first: int = _TREE_CHUNK_FIB_FIRST,
    second: int = _TREE_CHUNK_FIB_SECOND,
    cap: int = _TREE_CHUNK_CAP,
) -> list[tuple[int, int]]:
    """Return ``[(start, end), …]`` covering ``range(total)`` with Fib lengths.

    Lengths follow Fibonacci from *first*/*second*, each clamped to *cap* and
    to the remaining count. Empty *total* yields an empty list.
    """
    if total <= 0:
        return []
    if cap < 1:
        cap = 1
    a = max(1, int(first))
    b = max(1, int(second))
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < total:
        size = min(a, cap, total - start)
        end = start + size
        bounds.append((start, end))
        start = end
        a, b = b, a + b
    return bounds


# Experimental auto-connect poll interval (ms).
_DEVICE_POLL_MS = 3000
# After a heavy USB job (listing/transfer), skip probes so a recovering
# ZEN session is not torn down by Get_Storage / identity walks.
_DEVICE_USB_COOLDOWN_S = DEFAULT_USB_QUIET_S
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
        # Progressive index restore: batches paint before load finishes.
        self._index_stream_active = False
        self._index_stream_total = 0
        # Modeless Library → Manage Library… window (if open).
        self._manage_library_dlg: ManageLibraryDialog | None = None
        self._exclusions_dlg: ExclusionsManagerDialog | None = None
        # Cooperative cancel for transfer / device batch jobs (checked between items).
        self._job_cancel = threading.Event()
        # Durable multi-track sync plan (resume after failure / cancel).
        self._active_sync_job: SyncJobState | None = None
        # Live batch queue (same object the worker drains; UI may extend).
        self._transfer_queue: BatchTransferQueue | None = None
        # Path → Track for the active batch (progress status label).
        self._batch_track_by_path: dict[str, Track] = {}
        self._populate_after_id: str | None = None
        # Debounced library toolbar fuzzy search (Music / Video / Audiobooks).
        self._library_search_after_id: str | None = None
        self._library_search_query: str = ""
        self._library_filter_shown_count: int | None = None
        # Path → score for active filter (debug UI shows scores in #0).
        self._active_search_scores: dict[str, float] = {}
        self._device_populate_after_id: str | None = None
        self._device_poll_after_id: str | None = None
        self._device_poll_gen = 0
        self._logged_no_device = False
        # Exclusive MTP/USB ownership (poll, transfer, seed, enrich, meta).
        self._device_io = DeviceIoGate(quiet_after_s=_DEVICE_USB_COOLDOWN_S)
        # Cross-process lock so headless CLI/MCP agents see the GUI as owner.
        self._device_session_lock = DeviceSessionLock()
        self._device_session_lock_held = self._device_session_lock.try_acquire("gui")
        if not self._device_session_lock_held:
            logger.warning(
                "Device session lock busy at GUI start (another process holds USB); "
                "CLI agents may race until they exit. status=%s",
                self._device_session_lock.status().as_dict(),
            )
        self._device_probe_fails = 0
        # When False, experimental poll is stopped until Device → Connect.
        self._device_auto_reconnect = True
        # Durable device inventory key + session seed flag (list_files once).
        self._device_serial: str | None = None
        self._device_index_seeded = False
        self._device_index_seed_inflight = False
        self._device_tag_enrich_inflight = False
        self._device_music_refs: list[DeviceTrackRef] = []
        self._device_video_refs: list[DeviceTrackRef] = []
        self._device_audiobook_refs: list[DeviceTrackRef] = []
        self._device_podcast_refs: list[DeviceTrackRef] = []
        self._device_track_by_iid: dict[str, Track] = {}
        self._device_video_track_by_iid: dict[str, Track] = {}
        self._device_audiobook_track_by_iid: dict[str, Track] = {}
        self._device_podcast_track_by_iid: dict[str, Track] = {}
        self._device_tree_refresh_after_id: str | None = None
        self._device_context_tree = None
        self._device_context_row: str | None = None
        # Live Music/Video/TV folder ids from list_folders name match.
        self._folder_layout: DeviceFolderLayout = legacy_zen_vision_m_layout()
        # folder_id → parent_id from last list_folders (podcast show folders).
        self._folder_parent_by_id: dict[int, int] = {}
        self._device_video_populate_after_id: str | None = None
        self._device_audiobook_populate_after_id: str | None = None
        self._device_podcast_populate_after_id: str | None = None
        self._audiobooks_populate_after_id: str | None = None
        self._videos_populate_after_id: str | None = None
        # Roots for the in-flight scan (toolbar path while busy_message updates).
        self._scan_roots: list[str] = []
        self._scan_display_roots: list[str] = []
        self._active_profile: DeviceProfile | None = None
        # Default: "{artist} - {album}" (Artist-column option 3; VA via algorithm).
        self._sort_primary = SortPrimary.ARTIST_ALBUM_COMBO
        self._sort_reverse = False
        self._track_by_iid: dict[str, Track] = {}
        self._iid_by_path: dict[str, str] = {}
        # Group header iid → seed Track for filter_by_artist / filter_by_album.
        self._group_seed_by_iid: dict[str, Track] = {}
        self._context_group_seed: Track | None = None
        self._pending_album_art: list[tuple[str, str]] = []  # (iid, track_path)
        self._album_art_job_gen = 0
        self._device_pending_album_art: list[tuple[str, str]] = []
        self._device_album_art_job_gen = 0
        # Host audio playback (ffplay) + playlist state.
        self._audio_player = AudioPlayer()
        self._playback_queue: list[Track] = []
        self._playback_index: int = -1
        self._playback_poll_after_id: str | None = None
        # Host playlists (M3U in library index).
        self._playlist_ids_by_name: dict[str, int] = {}
        self._playlist_track_by_iid: dict[str, Track] = {}
        self._current_playlist_id: int | None = None
        # Device → Playlists (MTP playlist objects via PyMTP).
        self._device_playlists: list[DevicePlaylist] = []
        # Combobox label → playlist (labels unique; may include id disambiguator).
        self._device_playlist_by_name: dict[str, DevicePlaylist] = {}
        self._device_playlist_label_by_id: dict[int, str] = {}
        self._current_device_playlist: DevicePlaylist | None = None
        self._device_playlist_track_by_iid: dict[str, Track] = {}
        self._device_playlist_item_ids: list[int] = []
        self._device_playlist_load_inflight = False
        self._device_playlist_mutate_inflight = False
        # After track sync of kind=playlist: publish MTP playlist object.
        # {name, guids, host_id} or None.
        self._pending_device_playlist: dict | None = None
        # Podcast subscriptions UI state (id ordered with listbox rows).
        self._podcast_ids: list[int] = []
        self._podcast_episode_by_iid: dict[str, int] = {}
        self._selected_podcast_id: int | None = None
        # After video podcast send completes, optional audio batch to start.
        self._pending_podcast_audio_after_video: list | None = None
        self._pending_podcast_audio_label: str = ""
        # Scheduled podcast full-sync (host pass + optional device phase).
        self._podcast_auto_host_inflight = False
        self._podcast_auto_device_inflight = False
        self._pending_auto_podcast: dict | None = None
        # Day playlist from scheduled capture → push after device transfer.
        # {name, guids} or None.
        self._pending_day_podcast_playlist: dict | None = None
        self._podcast_schedule_after_id: str | None = None
        self._wire()
        # Defer restore so mainloop can start before any index I/O.
        self.win.root.after(0, self._start_index_restore)
        self.win.root.after(5_000, self._podcast_schedule_tick)


    def _wire(self) -> None:
        w = self.win
        # Stop ffplay (and timers) when the user closes the main window.
        w.root.protocol("WM_DELETE_WINDOW", self.on_app_close)
        w.set_library_menu_commands(
            on_manage_library=self.on_manage_library,
            on_manage_playlists=self.on_manage_playlists,
            on_podcast_settings=self.on_podcast_settings,
        )
        w.set_library_search_commands(
            on_change=self.on_library_search_changed,
            on_clear=self.on_library_search_clear,
        )
        w.set_transfer_menu_commands(
            on_sync_entire=self.action_entire_library,
            on_sync_folder=self.action_sync_folder,
            on_sync_selected=self.action_sync_selected,
            on_resume_sync=self.action_resume_sync,
            on_cancel_job=self.on_cancel_job,
            on_package_retail=self.action_package_retail_demos,
            on_restore_retail=self.action_restore_retail_package,
        )
        w.set_config_menu_commands(
            on_config=self.on_config,
            on_stable_mode_toggle=self.on_stable_mode_toggle,
            on_sync_album_art_toggle=self.on_sync_album_art_toggle,
            on_enable_experimental_tools_toggle=self.on_enable_experimental_tools_toggle,
            on_artist_folders_toggle=self.on_artist_folders_toggle,
            on_album_folders_toggle=self.on_album_folders_toggle,
            on_podcast_folders_toggle=self.on_podcast_folders_toggle,
            on_allow_video_podcasts_toggle=self.on_allow_video_podcasts_toggle,
            on_audio_podcasts_as_video_toggle=self.on_audio_podcasts_as_video_toggle,
            on_keep_downloaded_podcasts_toggle=self.on_keep_downloaded_podcasts_toggle,
            on_clear_downloaded_podcasts=self.on_clear_downloaded_podcasts,
            on_reveal_podcast_downloads=self.on_reveal_podcast_downloads,
        )
        artist_on = bool(self._config.store_tracks_in_artist_folder)
        album_on = bool(self._config.store_tracks_in_album_folder) and artist_on
        w.var_sync_album_art.set(bool(self._config.sync_album_art))
        w.var_artist_folders.set(artist_on)
        w.var_album_folders.set(album_on)
        w.var_podcast_folders.set(
            bool(self._config.store_podcasts_in_show_folders)
        )
        w.var_allow_video_podcasts.set(
            bool(self._config.allow_video_podcasts_to_sync)
        )
        w.var_audio_podcasts_as_video.set(
            bool(self._config.sync_audio_podcasts_as_video)
        )
        w.var_keep_downloaded_podcasts.set(
            bool(self._config.keep_downloaded_podcasts)
        )
        # Apply experimental-tools visibility after commands are wired so
        # rebuild can re-bind callbacks. Default off simplifies Device/Transfer.
        exp_tools = bool(self._config.enable_experimental_tools)
        w.set_experimental_tools_enabled(exp_tools)
        w.set_album_folders_menu_enabled(artist_on)
        w.set_podcast_tab_commands(
            on_add=self.on_podcast_add,
            on_remove=self.on_podcast_remove,
            on_refresh=self.on_podcast_refresh,
            on_more=None,  # handled via Shift-aware Button-1 bind below
            on_sync_latest=self.on_podcast_sync_latest_all,
            on_show_select=self.on_podcast_show_select,
            on_episode_select=self.on_podcast_episode_select,
            on_show_sync=self.on_podcast_sync_latest_selected,
            on_episode_sync=self.on_podcast_sync_episodes_selected,
            on_episode_play=self.on_podcast_play_episodes_selected,
            on_episode_reveal_download=self.on_podcast_reveal_download,
        )
        try:
            w.podcast_show_list.bind(
                "<Button-3>", w.popup_podcast_show_context
            )
            w.podcast_show_list.bind(
                "<Button-2>", w.popup_podcast_show_context
            )
            w.podcast_episode_tree.bind(
                "<Button-3>", w.popup_podcast_episode_context
            )
            w.podcast_episode_tree.bind(
                "<Button-2>", w.popup_podcast_episode_context
            )
            # Shift+click More Episodes → full history (no default command).
            w.btn_podcast_more.configure(command=lambda: None)
            w.btn_podcast_more.bind(
                "<ButtonRelease-1>", self._on_podcast_more_click
            )
        except Exception:
            pass
        self.win.root.after(80, self._refresh_podcast_tab)
        always_show = bool(self._config.always_show_playback_controls)
        w.var_always_show_playback.set(always_show)
        w.set_playback_always_show(always_show)
        w.set_view_menu_commands(
            on_always_show_playback_toggle=self.on_always_show_playback_toggle,
        )
        w.set_playback_commands(
            on_play_pause=self.on_playback_play_pause,
            on_prev=self.on_playback_prev,
            on_next=self.on_playback_next,
            on_close=self.on_playback_close,
            on_seek=self.on_playback_seek,
        )
        # Idle bar state (Prev/Next hidden until a multi-track queue is loaded).
        self._refresh_playback_ui()
        w.set_device_menu_commands(
            on_connect=self.on_connect,
            on_disconnect=self.on_disconnect,
            on_device_info=self.on_device_info,
            on_create_folder=self.action_create_folder,
            on_send_video=self.action_send_video,
            on_list_folders=self.action_read_folder_list,
            on_list_files=self.action_read_file_list,
            on_list_tracks=self.action_read_track_list,
            on_get_tracks_from_device=self.action_get_tracks_from_device,
            on_delete_track=self.action_delete_track,
            on_get_track_info=self.action_get_track_info,
            on_get_file_info=self.action_get_file_info,
            on_delete_all=self.action_delete_all_tracks,
            on_refresh_device_index=self.action_refresh_device_index,
        )
        w.set_track_context_commands(
            on_sync_track=self.action_sync_this_track,
            on_sync_album=self.action_all_from_album,
            on_sync_artist=self.action_all_from_artist,
            on_sync_artist_group=self.action_sync_artist_group,
            on_sync_album_group=self.action_sync_album_group,
            on_sync_selected=self.action_sync_selected,
            on_play_track=self.action_play_selected_tracks,
            on_play_artist_group=self.action_play_artist_group,
            on_play_album_group=self.action_play_album_group,
            on_add_to_playlist=self.action_add_selected_to_playlist,
            on_add_artist_to_playlist=self.action_add_artist_to_playlist,
            on_add_album_to_playlist=self.action_add_album_to_playlist,
            on_exclude_file=self.action_exclude_file,
            on_exclude_folder=self.action_exclude_folder,
            on_exclude_group_folder=self.action_exclude_group_folder,
        )
        w.set_playlist_tab_commands(
            on_combo_selected=self.on_playlist_combo_selected,
            on_new=self.on_playlist_new,
            on_delete=self.on_playlist_delete,
            on_rename=self.on_playlist_rename,
            on_sync=self.action_sync_current_playlist,
            on_remove_tracks=self.action_playlist_remove_selected,
            on_move_up=lambda: self.action_playlist_move_selected(-1),
            on_move_down=lambda: self.action_playlist_move_selected(1),
            on_shuffle_artist=lambda: self.action_playlist_shuffle("artist"),
            on_shuffle_spotify=lambda: self.action_playlist_shuffle("spotify"),
            on_play_track=self.action_playlist_play_selected,
        )
        try:
            w.playlist_tree.bind("<Button-3>", w.popup_playlist_context)
            w.playlist_tree.bind("<Button-2>", w.popup_playlist_context)
        except Exception:
            pass
        w.set_device_playlist_tab_commands(
            on_combo_selected=self.on_device_playlist_combo_selected,
            on_new=self.on_device_playlist_new,
            on_delete=self.on_device_playlist_delete,
            on_rename=self.on_device_playlist_rename,
            on_refresh=self.action_refresh_device_playlists,
            on_recreate_local=self.action_recreate_device_playlist_locally,
            on_remove_tracks=self.action_device_playlist_remove_selected,
            on_move_up=lambda: self.action_device_playlist_move_selected(-1),
            on_move_down=lambda: self.action_device_playlist_move_selected(1),
            on_shuffle_artist=lambda: self.action_device_playlist_shuffle(
                "artist"
            ),
            on_shuffle_spotify=lambda: self.action_device_playlist_shuffle(
                "spotify"
            ),
            on_play_track=self.action_device_playlist_play_selected,
        )
        try:
            w.device_playlist_tree.bind(
                "<Button-3>", w.popup_device_playlist_context
            )
            w.device_playlist_tree.bind(
                "<Button-2>", w.popup_device_playlist_context
            )
        except Exception:
            pass
        # Load playlist dropdown after index is available (also on restore).
        self.win.root.after(50, self._refresh_playlist_tab)
        w.set_prepare_context_menu(self._prepare_context_menu)
        w.set_prepare_device_context_menu(self._prepare_device_context_menu)
        w.set_device_context_commands(
            on_delete=self.action_device_delete_selected,
            on_pull=self.action_device_pull_selected,
            on_pull_folder=self.action_device_pull_to_folder,
            on_fetch_tags=self.action_device_fetch_tags_selected,
            on_add_to_playlist=self.action_device_add_selected_to_playlist,
            on_delete_artist=self.action_device_delete_artist_group,
            on_delete_album=self.action_device_delete_album_group,
            on_delete_folder=self.action_device_delete_folder_group,
            on_add_artist_to_playlist=self.action_device_add_selected_to_playlist,
            on_add_album_to_playlist=self.action_device_add_selected_to_playlist,
            on_add_folder_to_playlist=self.action_device_add_selected_to_playlist,
            on_device_info=self.on_device_info,
            on_delete_all=self.action_delete_all_tracks,
        )
        w.set_sort_heading_handler(self.on_sort_heading)
        w.set_cancel_job_command(self.on_cancel_job)
        # Context menu: Button-3 (most platforms), Button-2.
        # Do not bind Control-Button-1 here — extended selectmode uses
        # Ctrl+click (Windows/Linux) / Cmd+click (macOS) for multi-select.
        # On macOS, Control-click is still available as a secondary context
        # gesture via the platform binding when present; prefer right-click.
        for lib_tree in w.library_media_trees():
            lib_tree.bind("<Button-3>", w.popup_track_context)
            lib_tree.bind("<Button-2>", w.popup_track_context)
            lib_tree.bind("<<TreeviewSelect>>", self._on_tree_selection_changed)
        for dev_tree in w.device_media_trees():
            dev_tree.bind("<Button-3>", w.popup_device_context)
            dev_tree.bind("<Button-2>", w.popup_device_context)
            dev_tree.bind(
                "<<TreeviewSelect>>", self._on_device_tree_selection_changed
            )
        for panel_w in (
            w.device_panel,
            w.lbl_device_title,
            w.lbl_device_caption,
            w.device_graphic_slot,
            w.lbl_device_graphic,
        ):
            panel_w.bind("<Button-3>", w.popup_device_panel_context)
            panel_w.bind("<Button-2>", w.popup_device_panel_context)
        import sys as _sys

        if _sys.platform == "darwin":
            # macOS: Control-click = context menu; multi-toggle is Command-click.
            for lib_tree in w.library_media_trees():
                lib_tree.bind("<Control-Button-1>", w.popup_track_context)
            for dev_tree in w.device_media_trees():
                dev_tree.bind("<Control-Button-1>", w.popup_device_context)
            for panel_w in (
                w.device_panel,
                w.lbl_device_title,
                w.lbl_device_caption,
                w.device_graphic_slot,
                w.lbl_device_graphic,
            ):
                panel_w.bind(
                    "<Control-Button-1>", w.popup_device_panel_context
                )
        # Apply persisted mode (PyMTP default; Stable only if config says so).
        self._apply_transfer_mode(
            self._config.active_mode(),
            persist=False,
            reason="startup",
        )
        # Restore resumable sync job (if any) for Transfer → Resume Sync.
        self._load_sync_job_for_resume()


    def _folder_layout_or_legacy(self) -> DeviceFolderLayout:
        """Current device folder map (live or legacy fallback)."""
        return self._folder_layout or legacy_zen_vision_m_layout()

    def _music_folder_id(self) -> int:
        return self._folder_layout_or_legacy().music_id

    def _video_folder_id(self) -> int:
        return self._folder_layout_or_legacy().video_id

    def _tv_folder_id(self) -> int:
        return self._folder_layout_or_legacy().tv_id

    def _apply_folder_layout(self, layout: DeviceFolderLayout) -> None:
        """Store layout and push Music parent id onto transports/device."""
        self._folder_layout = layout
        mid = layout.music_id
        try:
            self.device.music_folder_id = mid
        except Exception:
            pass
        logger.info(
            "Device folder layout source=%s music=%s video=%s tv=%s names=%s",
            layout.source,
            layout.music_id,
            layout.video_id,
            layout.tv_id,
            {
                rid: layout.name_for(rid)
                for rid in (
                    layout.music_id,
                    layout.video_id,
                    layout.tv_id,
                )
            },
        )

    def _transport(self):
        mid = self._music_folder_id()
        if self.win.active_mode() == "stable":
            return CmdTransport(music_folder_id=mid)
        try:
            self.device.music_folder_id = mid
        except Exception:
            pass
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
        """Open Config dialog; persist preferences on Save."""
        result = show_config_dialog(
            self.win.root,
            send_format=self._config.normalized_send_format(),
            show_broken_video_presets=bool(
                self._config.show_broken_video_presets
            ),
        )
        if result is None:
            return
        self._config.send_format = result.send_format
        self._config.show_broken_video_presets = bool(
            result.show_broken_video_presets
        )
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save config")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info(
            "Config send_format=%s show_broken_video_presets=%s",
            result.send_format,
            result.show_broken_video_presets,
        )

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

    def on_sync_album_art_toggle(self) -> None:
        """Config → Sync album art (PyMTP): abstract album + JPEG sample after send."""
        enabled = bool(self.win.var_sync_album_art.get())
        if enabled and self._config.stable_mode:
            messagebox.showinfo(
                "Album art",
                "Sync album art needs PyMTP (uncheck Config → Stable Mode).\n\n"
                "On Creative ZEN, cover art is attached to device album objects "
                "after music or podcast transfer — not available via mtp-sendtr.\n"
                "Podcasts use the show’s RSS artwork as the album sample.",
            )
            self.win.var_sync_album_art.set(False)
            return
        self._config.sync_album_art = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save sync_album_art")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            self.win.var_sync_album_art.set(not enabled)
            return
        logger.info("Config sync_album_art=%s", enabled)

    def on_enable_experimental_tools_toggle(self) -> None:
        """Config → Enable Experimental Tools: show/hide experimental menus."""
        enabled = bool(self.win.var_enable_experimental_tools.get())
        prev = bool(self._config.enable_experimental_tools)
        self._config.enable_experimental_tools = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save enable_experimental_tools")
            self._config.enable_experimental_tools = prev
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            self.win.set_experimental_tools_enabled(prev)
            return
        self.win.set_experimental_tools_enabled(enabled)
        logger.info("Config enable_experimental_tools=%s", enabled)

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

    def on_podcast_folders_toggle(self) -> None:
        """Config → Store Podcasts in Identifiable Folders (experimental)."""
        enabled = bool(self.win.var_podcast_folders.get())
        if enabled and self._config.stable_mode:
            messagebox.showinfo(
                "Podcast folders",
                "Identifiable podcast folders need PyMTP "
                "(uncheck Config → Stable Mode).\n\n"
                "When enabled, episodes are sent under ZENcast/<Show Name>/ "
                "so you can test whether the player surfaces those folders.",
            )
            self.win.var_podcast_folders.set(False)
            return
        self._config.store_podcasts_in_show_folders = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save store_podcasts_in_show_folders")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config store_podcasts_in_show_folders=%s", enabled)

    def on_allow_video_podcasts_toggle(self) -> None:
        """Config → Allow video podcasts to Sync (experimental)."""
        enabled = bool(self.win.var_allow_video_podcasts.get())
        if enabled:
            messagebox.showinfo(
                "Video podcasts",
                "When enabled, video podcast episodes are encoded for the "
                "device (XviD on ZEN) and sent under ZENcast.\n\n"
                "Default (safer): video-only enclosures extract audio; dual "
                "feeds prefer the audio enclosure.\n\n"
                "This path is experimental — expect encode/send failures on "
                "picky players. Turn off if syncs start failing.",
            )
        self._config.allow_video_podcasts_to_sync = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save allow_video_podcasts_to_sync")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config allow_video_podcasts_to_sync=%s", enabled)

    def on_audio_podcasts_as_video_toggle(self) -> None:
        """Config → Sync Audio Podcasts as Video (experimental)."""
        enabled = bool(self.win.var_audio_podcasts_as_video.get())
        if enabled:
            messagebox.showinfo(
                "Audio podcasts as video",
                "When enabled, audio episodes are encoded as still-image "
                "XviD plus the episode audio, then sent under ZENcast.\n\n"
                "ZVM: audio-only podcasts often land under Music (genre "
                "Podcast); only video appears in ZENcast.\n\n"
                "Defaults (device-proven): 2 fps · 128×96 (~+9% vs audio). "
                "1 fps failed. Tune in config.json:\n"
                "  audio_podcast_still_fps\n"
                "  audio_podcast_still_width / _height\n\n"
                "This path is experimental — expect encode/send failures. "
                "Re-sync rebuilds *_device.avi.",
            )
        self._config.sync_audio_podcasts_as_video = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save sync_audio_podcasts_as_video")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config sync_audio_podcasts_as_video=%s", enabled)

    def on_keep_downloaded_podcasts_toggle(self) -> None:
        """Config → Keep downloaded podcasts."""
        enabled = bool(self.win.var_keep_downloaded_podcasts.get())
        self._config.keep_downloaded_podcasts = enabled
        try:
            save_app_config(self._config)
        except OSError as e:
            logger.exception("Failed to save keep_downloaded_podcasts")
            messagebox.showerror("Config", f"Could not save settings:\n{e}")
            return
        logger.info("Config keep_downloaded_podcasts=%s", enabled)

    def on_reveal_podcast_downloads(self) -> None:
        """Config → Reveal podcast downloads folder (Finder / file manager)."""
        from mtpmanager.infra.podcast_index import podcasts_cache_root

        root = podcasts_cache_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror(
                "Podcast downloads", f"Could not open cache folder:\n{e}"
            )
            return
        self._reveal_path_in_os(str(root), is_directory=True)

    def on_clear_downloaded_podcasts(self) -> None:
        """Config → Clear downloaded podcasts…"""
        from mtpmanager.infra.podcast_index import podcasts_cache_root

        root = podcasts_cache_root()
        if not messagebox.askyesno(
            "Clear downloaded podcasts",
            "Delete all cached podcast media on this computer?\n\n"
            f"Folder:\n{root}\n\n"
            "Episode index and device files are not removed — only local "
            "downloads (and any kept device-encoded copies).",
        ):
            return
        try:
            result = clear_downloaded_podcasts()
        except Exception as e:
            logger.exception("clear_downloaded_podcasts failed")
            messagebox.showerror(
                "Clear downloaded podcasts", f"Could not clear cache:\n{e}"
            )
            return
        if self._selected_podcast_id is not None:
            self._load_podcast_episodes(self._selected_podcast_id)
        messagebox.showinfo(
            "Clear downloaded podcasts",
            f"Removed {result.get('files', 0)} file(s) "
            f"({int(result.get('bytes', 0) or 0) // 1024} KiB).\n"
            f"Cleared {result.get('rows_cleared', 0)} episode path(s).\n\n"
            f"{result.get('root', root)}",
        )

    def _reveal_path_in_os(self, path: str, *, is_directory: bool = False) -> None:
        """Open Finder (macOS) / explorer / xdg-open for *path*."""
        import subprocess
        import sys

        target = path
        if not target or not os.path.exists(target):
            messagebox.showinfo(
                "Reveal",
                "That path is not on disk yet.\n\n"
                "Play or Sync the episode first (with Keep downloaded "
                "podcasts enabled), or use Config → Reveal podcast downloads "
                "folder.",
            )
            return
        try:
            if sys.platform == "darwin":
                if is_directory:
                    subprocess.run(["open", target], check=False)
                else:
                    # Reveal file in Finder
                    subprocess.run(["open", "-R", target], check=False)
            elif sys.platform.startswith("win"):
                if is_directory:
                    os.startfile(target)  # type: ignore[attr-defined]
                else:
                    subprocess.run(
                        ["explorer", "/select,", target], check=False
                    )
            else:
                subprocess.run(
                    ["xdg-open", target if is_directory else os.path.dirname(target) or target],
                    check=False,
                )
        except Exception as e:
            logger.exception("reveal path failed")
            messagebox.showerror("Reveal", f"Could not open path:\n{e}")

    def on_podcast_reveal_download(self) -> None:
        """Episode context → Reveal Download in Finder."""
        eids = self._selected_episode_ids()
        if not eids:
            messagebox.showinfo("Reveal", "Select an episode first.")
            return
        ep = get_episode(eids[0])
        if ep is None:
            return
        path = (ep.local_path or "").strip()
        if path and os.path.isfile(path):
            self._reveal_path_in_os(path, is_directory=False)
            return
        # Prefer any on-disk media for this guid (source video, encode, audio).
        from mtpmanager.infra.podcast_index import episode_cache_dir

        cache = episode_cache_dir(ep.podcast_id)
        hits: list[str] = []
        if ep.guid:
            try:
                hits = sorted(
                    str(p) for p in cache.glob(f"{ep.guid}*") if p.is_file()
                )
            except OSError:
                hits = []
        if hits:
            # Prefer video source over audio extract for inspect.
            prefer = [h for h in hits if h.endswith(("_video.mp4", ".mp4", ".m4v", ".avi"))]
            self._reveal_path_in_os((prefer or hits)[0], is_directory=False)
            return
        messagebox.showinfo(
            "Reveal",
            "No local download for this episode yet.\n\n"
            "Play or Sync it first (Config → Keep downloaded podcasts is "
            f"{'on' if self._config.keep_downloaded_podcasts else 'off'}).\n\n"
            f"Cache folder:\n{cache}",
        )

    # ------------------------------------------------------------------
    # Podcasts tab
    # ------------------------------------------------------------------

    def _refresh_podcast_tab(self) -> None:
        shows = list_podcasts()
        self._podcast_ids = [p.id for p in shows]
        lb = self.win.podcast_show_list
        try:
            lb.delete(0, "end")
            for p in shows:
                label = (p.title or p.feed_url or f"Podcast {p.id}").strip()
                # Mark shows that have any video episode in the index.
                try:
                    has_video = any(
                        ep.is_video for ep in list_episodes(p.id, limit=50)
                    )
                except Exception:
                    has_video = False
                if has_video:
                    label = f"▶ {label}"
                lb.insert("end", label)
        except Exception:
            logger.debug("refresh podcast list failed", exc_info=True)
        has = bool(shows)
        try:
            self.win.btn_podcast_remove.configure(
                state=NORMAL if has else DISABLED
            )
            self.win.btn_podcast_refresh.configure(
                state=NORMAL if has else DISABLED
            )
            self.win.btn_podcast_sync_latest.configure(
                state=NORMAL if has else DISABLED
            )
        except Exception:
            pass
        if self._selected_podcast_id not in self._podcast_ids:
            self._selected_podcast_id = (
                self._podcast_ids[0] if self._podcast_ids else None
            )
        if self._selected_podcast_id is not None:
            try:
                idx = self._podcast_ids.index(self._selected_podcast_id)
                lb.selection_clear(0, "end")
                lb.selection_set(idx)
                lb.see(idx)
            except Exception:
                pass
            self._load_podcast_episodes(self._selected_podcast_id)
        else:
            self._clear_podcast_episodes()
            try:
                self.win.lbl_podcast_status.configure(text="No subscriptions")
            except Exception:
                pass

    def _clear_podcast_episodes(self) -> None:
        tree = self.win.podcast_episode_tree
        for iid in tree.get_children(""):
            tree.delete(iid)
        self._podcast_episode_by_iid.clear()
        try:
            self.win.btn_podcast_more.configure(state=DISABLED)
            self.win.lbl_podcast_episodes.configure(text="Episodes")
        except Exception:
            pass

    def _load_podcast_episodes(self, podcast_id: int) -> None:
        show = get_podcast(podcast_id)
        episodes = list_episodes(podcast_id)
        tree = self.win.podcast_episode_tree
        for iid in tree.get_children(""):
            tree.delete(iid)
        self._podcast_episode_by_iid.clear()
        stems = self._device_guid_stems_for_skip() or set()
        for ep in episodes:
            date = (ep.pub_date or "")[:10]
            dur = ""
            if ep.duration_sec and ep.duration_sec > 0:
                m, s = divmod(int(ep.duration_sec), 60)
                h, m = divmod(m, 60)
                dur = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            if ep.guid and ep.guid in stems:
                status = "On device"
            elif ep.local_path and os.path.isfile(ep.local_path):
                status = "Downloaded"
            else:
                status = "New"
            if ep.is_video:
                status = f"{status} · Video"
            iid = f"pe:{ep.id}"
            tags = ("video_episode",) if ep.is_video else ()
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(date, ep.title or "Untitled", dur, status),
                tags=tags,
            )
            self._podcast_episode_by_iid[iid] = ep.id
        title = (show.title if show else "") or "Podcast"
        try:
            self.win.lbl_podcast_episodes.configure(
                text=f"Episodes of “{title}” ({len(episodes)})"
            )
            self.win.btn_podcast_more.configure(
                state=NORMAL if show else DISABLED
            )
            self.win.btn_podcast_remove.configure(state=NORMAL)
            self.win.lbl_podcast_status.configure(
                text=f"{len(episodes)} episode(s) indexed"
            )
        except Exception:
            pass

    def on_podcast_show_select(self) -> None:
        try:
            sel = self.win.podcast_show_list.curselection()
        except Exception:
            sel = ()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._podcast_ids):
            return
        self._selected_podcast_id = self._podcast_ids[idx]
        self._load_podcast_episodes(self._selected_podcast_id)
        self._refresh_podcast_context_detail()

    def on_podcast_episode_select(self) -> None:
        self._refresh_podcast_context_detail()
        self._update_podcast_episode_menu_labels()

    def _update_podcast_episode_menu_labels(self) -> None:
        n = len(self._selected_episode_ids())
        try:
            from mtpmanager.ui.window import (
                CTX_PODCAST_PLAY_EPISODE,
                CTX_PODCAST_PLAY_EPISODES,
                CTX_PODCAST_REVEAL_DOWNLOAD,
            )

            play_label = (
                CTX_PODCAST_PLAY_EPISODES if n > 1 else CTX_PODCAST_PLAY_EPISODE
            )
            self.win.menu_podcast_episode_ctx.entryconfig(0, label=play_label)
            if n >= 1:
                noun = "Episode" if n == 1 else "Episodes"
                self.win.menu_podcast_episode_ctx.entryconfig(
                    1, label=f"Sync {n} {noun} Now"
                )
            else:
                self.win.menu_podcast_episode_ctx.entryconfig(
                    1, label="Sync Episodes Now"
                )
            # Reveal is useful when any selected episode has on-disk media.
            can_reveal = False
            for eid in self._selected_episode_ids()[:1]:
                ep = get_episode(eid)
                if ep is None:
                    continue
                if ep.local_path and os.path.isfile(ep.local_path):
                    can_reveal = True
                    break
                if ep.guid:
                    from mtpmanager.infra.podcast_index import episode_cache_dir

                    cache = episode_cache_dir(ep.podcast_id)
                    try:
                        if any(cache.glob(f"{ep.guid}*")):
                            can_reveal = True
                    except OSError:
                        pass
            self.win.menu_podcast_episode_ctx.entryconfig(
                CTX_PODCAST_REVEAL_DOWNLOAD,
                state=NORMAL if can_reveal else DISABLED,
            )
        except Exception:
            pass

    def _refresh_podcast_context_detail(self) -> None:
        """Leftframe: podcast or episode detail when Podcasts tab is active."""
        try:
            current = self.win.media_notebook.select()
            if current != str(self.win.podcastsLibrary_tab):
                return
        except Exception:
            return
        ep_ids = self._selected_episode_ids()
        if len(ep_ids) == 1:
            ep = get_episode(ep_ids[0])
            show = (
                get_podcast(ep.podcast_id)
                if ep is not None
                else None
            )
            if ep is not None:
                if ep.is_video and ep.video_enclosure_url and ep.enclosure_url:
                    media = "Audio + Video"
                elif ep.is_video:
                    media = "Video"
                else:
                    media = "Audio"
                lines = [
                    ep.title or "Untitled episode",
                    f"Show: {(show.title if show else '') or '—'}",
                    f"Published: {(ep.pub_date or '—')[:19]}",
                    f"Media: {media}",
                ]
                if ep.enclosure_type:
                    lines.append(f"Enclosure: {ep.enclosure_type}")
                if ep.duration_sec:
                    lines.append(f"Duration: {int(ep.duration_sec)}s")
                local = (ep.local_path or "").strip()
                if local and os.path.isfile(local):
                    lines.append(f"Downloaded: {local}")
                elif local:
                    lines.append(f"Downloaded: {local} (missing)")
                else:
                    # List sibling cache files for this guid (source video, encode).
                    from mtpmanager.infra.podcast_index import episode_cache_dir

                    cache = episode_cache_dir(ep.podcast_id)
                    extras: list[str] = []
                    if ep.guid:
                        try:
                            extras = sorted(
                                p.name
                                for p in cache.glob(f"{ep.guid}*")
                                if p.is_file()
                            )
                        except OSError:
                            extras = []
                    if extras:
                        lines.append(f"Cache files: {', '.join(extras)}")
                        lines.append(f"Cache dir: {cache}")
                    else:
                        lines.append("Downloaded: (not on disk)")
                if ep.description:
                    desc = ep.description
                    if len(desc) > 600:
                        desc = desc[:600] + "…"
                    lines.append("")
                    lines.append(desc)
                detail_path = local if local and os.path.isfile(local) else ep.enclosure_url
                self.win.set_context_detail("\n".join(lines), path=detail_path)
                return
        if self._selected_podcast_id is not None:
            show = get_podcast(self._selected_podcast_id)
            if show is not None:
                lines = [
                    show.title or "Podcast",
                    f"Author: {show.author or '—'}",
                    f"Episodes indexed: {show.episode_count}",
                    f"Last fetched: {show.last_fetched_at or '—'}",
                ]
                if show.description:
                    desc = show.description
                    if len(desc) > 600:
                        desc = desc[:600] + "…"
                    lines.append("")
                    lines.append(desc)
                self.win.set_context_detail(
                    "\n".join(lines), path=show.feed_url
                )
                return
        self.win.set_context_detail("")

    def _selected_episode_ids(self) -> list[int]:
        try:
            sel = list(self.win.podcast_episode_tree.selection())
        except Exception:
            sel = []
        out: list[int] = []
        for iid in sel:
            eid = self._podcast_episode_by_iid.get(iid)
            if eid is not None:
                out.append(int(eid))
        return out

    def _selected_podcast_ids(self) -> list[int]:
        try:
            sel = self.win.podcast_show_list.curselection()
        except Exception:
            sel = ()
        out: list[int] = []
        for idx in sel:
            i = int(idx)
            if 0 <= i < len(self._podcast_ids):
                out.append(self._podcast_ids[i])
        return out

    def on_podcast_add(self) -> None:
        url = ask_text(
            self.win.root,
            title="Add Podcast",
            prompt="RSS / podcast feed URL:",
        )
        if not url:
            return
        self.win.lbl_podcast_status.configure(text="Fetching feed…")

        def work():
            return subscribe_feed(url, initial_limit=INITIAL_EPISODE_LIMIT)

        def on_done(result) -> None:
            podcast, n = result
            self._selected_podcast_id = podcast.id
            self._refresh_podcast_tab()
            messagebox.showinfo(
                "Podcast",
                f"Subscribed to “{podcast.title}”.\n"
                f"Loaded {n} new episode(s) "
                f"(showing up to {INITIAL_EPISODE_LIMIT} newest).",
            )

        def on_error(exc: BaseException) -> None:
            self.win.lbl_podcast_status.configure(text="")
            logger.exception("subscribe_feed failed")
            messagebox.showerror("Podcast", f"Could not add podcast:\n{exc}")

        self._bg.submit(work, on_done=on_done, on_error=on_error, name="podcast-add")

    def on_podcast_remove(self) -> None:
        ids = self._selected_podcast_ids()
        if not ids and self._selected_podcast_id is not None:
            ids = [self._selected_podcast_id]
        if not ids:
            messagebox.showinfo("Podcast", "Select a podcast to remove.")
            return
        names = []
        for pid in ids:
            p = get_podcast(pid)
            names.append((p.title if p else None) or f"#{pid}")
        if not messagebox.askyesno(
            "Remove Podcast",
            "Unsubscribe and remove local episode index for:\n\n"
            + "\n".join(f"• {n}" for n in names)
            + "\n\nFiles already on the device are not deleted.",
        ):
            return
        for pid in ids:
            delete_podcast(pid)
        self._selected_podcast_id = None
        self._refresh_podcast_tab()

    def on_podcast_refresh(self) -> None:
        """Re-fetch RSS for selected show(s) (or the active show) and add new episodes."""
        ids = self._selected_podcast_ids()
        if not ids and self._selected_podcast_id is not None:
            ids = [self._selected_podcast_id]
        if not ids and self._podcast_ids:
            # No selection: refresh all subscriptions.
            ids = list(self._podcast_ids)
        if not ids:
            messagebox.showinfo("Podcast", "No podcasts to refresh.")
            return
        self.win.lbl_podcast_status.configure(text="Refreshing feed(s)…")
        try:
            self.win.btn_podcast_refresh.configure(state=DISABLED)
        except Exception:
            pass

        def work() -> list[tuple[int, int, str]]:
            # (podcast_id, new_count, title)
            out: list[tuple[int, int, str]] = []
            for pid in ids:
                podcast, n = refresh_podcast(pid)
                out.append((pid, n, podcast.title or f"#{pid}"))
            return out

        def on_done(results: list) -> None:
            try:
                self.win.btn_podcast_refresh.configure(state=NORMAL)
            except Exception:
                pass
            total_new = sum(int(n) for _pid, n, _t in results)
            # Keep selection; reload episode list for the active show.
            self._refresh_podcast_tab()
            if self._selected_podcast_id is not None:
                self._load_podcast_episodes(self._selected_podcast_id)
            if len(results) == 1:
                _pid, n, title = results[0]
                msg = (
                    f"Refreshed “{title}”: {n} new episode(s)."
                    if n
                    else f"Refreshed “{title}”: no new episodes."
                )
            else:
                msg = (
                    f"Refreshed {len(results)} podcast(s): "
                    f"{total_new} new episode(s) total."
                )
            try:
                self.win.lbl_podcast_status.configure(text=msg)
            except Exception:
                pass

        def on_error(exc: BaseException) -> None:
            try:
                self.win.btn_podcast_refresh.configure(state=NORMAL)
            except Exception:
                pass
            try:
                self.win.lbl_podcast_status.configure(text="")
            except Exception:
                pass
            logger.exception("podcast refresh failed")
            messagebox.showerror("Podcast", f"Could not refresh feed:\n{exc}")

        self._bg.submit(
            work, on_done=on_done, on_error=on_error, name="podcast-refresh"
        )

    def _on_podcast_more_click(self, event) -> None:
        """More Episodes: Shift+click → full history with warning."""
        try:
            if str(self.win.btn_podcast_more["state"]) == str(DISABLED):
                return
        except Exception:
            pass
        shift = bool(event.state & 0x0001)
        self.on_podcast_more(full_history=shift)

    def on_podcast_more(self, *, full_history: bool = False) -> None:
        pid = self._selected_podcast_id
        if pid is None:
            ids = self._selected_podcast_ids()
            pid = ids[0] if ids else None
        if pid is None:
            messagebox.showinfo("Podcast", "Select a podcast first.")
            return
        if full_history:
            if not messagebox.askyesno(
                "Fetch full history",
                "Fetch the entire episode history for this podcast?\n\n"
                "This can take a long time and use significant disk/database "
                "space for long-running shows.",
            ):
                return
            count = 0
        else:
            count = MORE_EPISODE_STEP
        self.win.lbl_podcast_status.configure(
            text="Fetching more episodes…" if not full_history else "Fetching full history…"
        )

        def work():
            return load_more_episodes(
                pid, count=count, full_history=full_history
            )

        def on_done(result) -> None:
            _podcast, n = result
            self._load_podcast_episodes(pid)
            self.win.lbl_podcast_status.configure(
                text=f"Added {n} episode(s)"
            )

        def on_error(exc: BaseException) -> None:
            self.win.lbl_podcast_status.configure(text="")
            logger.exception("load_more_episodes failed")
            messagebox.showerror("Podcast", f"Could not load episodes:\n{exc}")

        self._bg.submit(work, on_done=on_done, on_error=on_error, name="podcast-more")

    def on_podcast_settings(self) -> None:
        """Library → Podcast Settings…"""
        cfg = self._config
        status = self._podcast_schedule_status_line()
        result = show_podcast_settings_dialog(
            self.win.root,
            auto_enabled=bool(cfg.podcast_auto_enabled),
            schedule_days=list(cfg.podcast_schedule_days),
            schedule_time=cfg.podcast_schedule_time,
            max_new_per_show=int(cfg.podcast_max_new_per_show),
            auto_sync_to_device=bool(cfg.podcast_auto_sync_to_device),
            status_line=status,
        )
        if result is None:
            return
        self._config.podcast_auto_enabled = bool(result.auto_enabled)
        self._config.podcast_schedule_days = tuple(result.schedule_days)
        self._config.podcast_schedule_time = result.schedule_time
        self._config.podcast_max_new_per_show = int(result.max_new_per_show)
        self._config.podcast_auto_sync_to_device = bool(result.auto_sync_to_device)
        try:
            save_app_config(self._config)
        except Exception as e:
            messagebox.showerror(
                "Podcast Settings", f"Could not save settings:\n{e}"
            )
            return
        logger.info(
            "Podcast settings saved enabled=%s days=%s time=%s max=%s",
            self._config.podcast_auto_enabled,
            self._config.podcast_schedule_days,
            self._config.podcast_schedule_time,
            self._config.podcast_max_new_per_show,
        )
        if result.run_full_sync_now:
            self._start_full_podcast_sync(
                podcast_ids=None, label="Full Podcast Sync"
            )
        else:
            self.win.root.after(200, self._podcast_schedule_tick)

    def _podcast_schedule_status_line(self) -> str:
        from datetime import datetime

        cfg = self._config
        if not cfg.podcast_auto_enabled:
            last = (cfg.podcast_last_full_sync_at or "").strip()
            last_part = f"Last full sync: {last}" if last else "Last full sync: never"
            return f"Scheduled full sync is off. {last_part}."
        last = (cfg.podcast_last_full_sync_at or "").strip()
        last_part = f"Last full sync: {last}" if last else "Last full sync: never"
        now = datetime.now().astimezone()
        nxt = next_run_after(
            now_local=now,
            days=cfg.podcast_schedule_days,
            time_hhmm=cfg.podcast_schedule_time,
            last_run_local_date=cfg.podcast_last_full_sync_local_date or "",
        )
        if nxt is not None:
            next_part = f"Next: {nxt.strftime('%a %Y-%m-%d %I:%M %p')}"
        else:
            next_part = "Next: —"
        summary = format_schedule_summary(
            days=cfg.podcast_schedule_days,
            time_hhmm=cfg.podcast_schedule_time,
        )
        return f"{summary}. {last_part}. {next_part}."

    def _podcast_schedule_tick(self) -> None:
        """Timer: catch-up / due full sync; reschedule next tick."""
        try:
            self._podcast_schedule_after_id = self.win.root.after(
                _PODCAST_SCHEDULE_POLL_MS, self._podcast_schedule_tick
            )
        except Exception:
            self._podcast_schedule_after_id = None
        self._podcast_schedule_run_due(quiet=True)

    def _podcast_schedule_tick_once(self) -> None:
        """One-shot catch-up after library restore (does not retarget the timer)."""
        self._podcast_schedule_run_due(quiet=True)

    def _podcast_schedule_run_due(self, *, quiet: bool = True) -> None:
        """If automatic podcasts are due, start a full-sync host pass."""
        if not bool(self._config.podcast_auto_enabled):
            return
        if self._podcast_auto_host_inflight:
            return
        # Quiet while library index is still restoring/scanning — next poll
        # (or post-restore kick) will run without Busy dialogs.
        if self._library_busy:
            logger.debug(
                "Podcast schedule deferred: library still loading/scanning"
            )
            return
        if self._transfer_busy:
            return
        from datetime import datetime

        now = datetime.now().astimezone()
        # Global "already ran today" stamp short-circuits when every show
        # would be marked; still honor per-show due for partial failures.
        due = due_podcasts_for_schedule(
            now_local=now,
            global_days=self._config.podcast_schedule_days,
            global_time=self._config.podcast_schedule_time,
        )
        if not due:
            self._maybe_auto_sync_pending_podcasts()
            return
        self._start_full_podcast_sync(
            podcast_ids=[p.id for p in due],
            label="Scheduled Podcast Sync",
            quiet=quiet,
        )

    def _start_full_podcast_sync(
        self,
        *,
        podcast_ids: list[int] | None,
        label: str,
        quiet: bool = False,
    ) -> None:
        """Host pass: refresh feeds; download ≤N episodes published since last full sync.

        *podcast_ids*: ``None`` = every subscribed show (manual Full Sync Now);
        otherwise only those ids (scheduled due shows). Cap N never backfills
        older catalog items outside the publish window.

        *quiet*: scheduled/auto path — no dialogs if already busy or library
        is still loading (caller retries later).
        """
        if self._library_busy:
            if quiet:
                logger.info(
                    "Full podcast sync deferred (%s): library still loading",
                    label,
                )
                return
            messagebox.showinfo(
                "Podcast",
                "Library is still loading or scanning. Try again in a moment.",
            )
            return
        if self._podcast_auto_host_inflight:
            if quiet:
                return
            messagebox.showinfo(
                "Podcast",
                "A full podcast sync is already running.",
            )
            return
        self._podcast_auto_host_inflight = True
        stems = self._device_guid_stems_for_skip() or set()
        max_n = int(self._config.podcast_max_new_per_show or 1)
        fmt = self._target_format()
        # Cap N applies only inside the “published since last full sync” window.
        # Empty stamp → host pass floors to today (no catalog backfill).
        since = (self._config.podcast_last_full_sync_local_date or "").strip()
        ids = podcast_ids
        try:
            self.win.lbl_podcast_status.configure(text=f"{label}: updating…")
            self.win.set_progress_status(
                f"{label}: refreshing feeds and downloading…"
            )
        except Exception:
            pass
        # On-device day playlist: durable plan; GUIDs filled after successful send.
        self._full_sync_day_playlist_when = None
        try:
            from datetime import datetime as _dt

            when = _dt.now().astimezone()
            self._full_sync_day_playlist_when = when
            plan = ensure_day_playlist_plan(when=when)
            self._pending_day_podcast_playlist = {
                "name": plan.get("name") or podcast_day_playlist_name(when),
                "guids": list(plan.get("guids") or []),
            }
        except Exception:
            self._pending_day_podcast_playlist = {
                "name": podcast_day_playlist_name(),
                "guids": [],
            }

        def work():
            from datetime import datetime

            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def on_episode_ready(ep, track) -> None:
                # Marshal to main thread via progress poll (Tk-safe).
                report("episode_ready", ep, track)

            return run_full_sync_host_pass(
                podcast_ids=ids,
                max_new_per_show=max_n,
                device_guids=stems,
                target_audio_format=fmt,
                now_local=datetime.now().astimezone(),
                since_last_full_sync=since,
                on_episode_ready=on_episode_ready,
            )

        def on_progress(*args) -> None:
            if not args:
                return
            if args[0] == "episode_ready" and len(args) >= 3:
                self._on_full_sync_episode_ready(args[1], args[2], label=label)

        def on_done(result) -> None:
            self._podcast_auto_host_inflight = False
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            downloaded = list(getattr(result, "downloaded", None) or [])
            n = len(downloaded)
            errs = list(getattr(result, "errors", None) or [])
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            when_local = datetime.now().astimezone()
            local_day = when_local.date().isoformat()
            self._config.podcast_last_full_sync_at = now
            self._config.podcast_last_full_sync_local_date = local_day
            try:
                save_app_config(self._config)
            except Exception:
                logger.debug("save last full podcast sync failed", exc_info=True)

            day_pl = self._pending_day_podcast_playlist or {}
            day_name = str(day_pl.get("name") or "").strip()
            day_pl_note = f" · device playlist “{day_name}”" if day_name else ""

            msg = f"{label}: {n} episode(s) ready{day_pl_note}"
            if errs:
                msg += f" · {len(errs)} error(s)"
            try:
                self.win.lbl_podcast_status.configure(text=msg)
            except Exception:
                pass
            logger.info(
                "Podcast full sync host pass done downloaded=%s errors=%s",
                n,
                len(errs),
            )
            self._refresh_podcast_tab()
            if self._selected_podcast_id is not None:
                self._load_podcast_episodes(self._selected_podcast_id)
            # Drain leftovers / publish after quiet if transfer is idle.
            if self._transfer_busy or self._podcast_auto_device_inflight:
                # Live stream still owns the pipeline; finish path will drain.
                pass
            else:
                self._schedule_podcast_leftover_drain()

        def on_error(exc: BaseException) -> None:
            self._podcast_auto_host_inflight = False
            try:
                self.win.set_progress_status("")
                self.win.lbl_podcast_status.configure(
                    text=f"{label} failed (see log)"
                )
            except Exception:
                pass
            logger.exception("Podcast full sync host pass failed: %s", exc)
            messagebox.showerror("Podcast", f"{label} failed:\n{exc}")
            # Still try to sync whatever landed on disk (after quiet).
            if not self._transfer_busy:
                self._schedule_podcast_leftover_drain()

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
            name="podcast-full-sync",
        )

    def _on_full_sync_episode_ready(self, episode, track, *, label: str) -> None:
        """Main-thread: day playlist + optional immediate device enqueue."""
        ep_id = int(getattr(episode, "id", 0) or 0)
        title = (getattr(episode, "title", None) or "").strip() or f"#{ep_id}"
        try:
            self.win.lbl_podcast_status.configure(
                text=f"{label}: ready “{title}”"
            )
            self.win.set_progress_status(
                f"{label}: downloaded — queueing for device…"
            )
        except Exception:
            pass

        # Day playlist membership is recorded only after a successful device
        # send (see _record_day_podcast_playlist_guid) — cache files may be
        # discarded and are not a host playlist.

        if not bool(self._config.podcast_auto_sync_to_device):
            return
        if not self.device.is_connected():
            return
        if track is None or not (getattr(track, "path", None) or ""):
            return
        self._stream_podcast_track_to_device(
            track, episode_id=ep_id, label=label
        )

    def _stream_podcast_track_to_device(
        self,
        track: Track,
        *,
        episode_id: int = 0,
        label: str = "Auto Podcast Sync",
    ) -> None:
        """Start or extend an auto podcast transfer with one ready track.

        On failure, leave ``pending_device_sync`` set so the post-host drain
        can pick the episode up. Uses quiet job begin so concurrent host
        download never spams Busy dialogs.
        """
        if episode_id > 0:
            pending = self._pending_auto_podcast
            if pending is None:
                pending = {"episode_ids": []}
            ids = list(pending.get("episode_ids") or [])
            if episode_id not in ids:
                ids.append(episode_id)
            pending["episode_ids"] = ids
            self._pending_auto_podcast = pending

        job = self._active_sync_job
        if (
            self._transfer_busy
            and self._transfer_queue is not None
            and job is not None
            and getattr(job, "kind", "") == "podcast"
        ):
            self._podcast_auto_device_inflight = True
            n = self._enqueue_tracks(
                [track], kind="podcast", label=label or "Auto Podcast Sync"
            )
            if n:
                logger.info(
                    "Streamed podcast episode_id=%s onto live transfer queue",
                    episode_id,
                )
            return

        if self._transfer_busy:
            # Another kind of transfer owns USB; leave DB pending for later.
            logger.info(
                "Podcast episode_id=%s ready; deferring (transfer busy kind=%s)",
                episode_id,
                getattr(job, "kind", None) if job else None,
            )
            return

        self._podcast_auto_device_inflight = True
        before_busy = bool(self._transfer_busy)
        started = self._transfer_many(
            [track],
            kind="podcast",
            label=label or "Auto Podcast Sync",
            quiet=True,
        )
        if started:
            logger.info(
                "Starting podcast transfer with streamed episode_id=%s",
                episode_id,
            )
        elif not before_busy and not self._transfer_busy:
            # Could not start (library busy / USB holder); stay pending.
            logger.info(
                "Podcast episode_id=%s ready; transfer did not start "
                "(will retry after host pass)",
                episode_id,
            )
            if (
                self._pending_auto_podcast is not None
                and not self._podcast_auto_host_inflight
            ):
                self._finish_auto_podcast_device_batch(ok=False)
            else:
                self._podcast_auto_device_inflight = False

    def _maybe_auto_sync_pending_podcasts(self) -> None:
        """Device phase: sync pending full-sync episodes if ready.

        Used for leftovers after a full-sync host pass (or when streaming
        could not start because USB was busy). During an active host pass,
        episodes are streamed via :meth:`_on_full_sync_episode_ready`.
        """
        if not bool(self._config.podcast_auto_sync_to_device):
            return
        if self._podcast_auto_device_inflight or self._podcast_auto_host_inflight:
            return
        if self._library_busy or self._transfer_busy:
            return
        if not self.device.is_connected():
            return
        stems = self._device_guid_stems_for_skip() or set()
        pending = pending_episodes_for_device_sync(device_guids=stems)
        if not pending:
            return
        self._podcast_auto_device_inflight = True
        episode_ids = [ep.id for ep in pending]
        self._pending_auto_podcast = {"episode_ids": episode_ids}
        try:
            self.win.lbl_podcast_status.configure(
                text=f"Auto-syncing {len(pending)} podcast episode(s)…"
            )
        except Exception:
            pass
        logger.info("Auto podcast device sync starting n=%s", len(pending))
        self._sync_podcast_episodes_auto(pending)

    def _sync_podcast_episodes_auto(self, episodes: list) -> None:
        """Prepare + transfer pending auto episodes (audio-only; quiet status)."""
        try:
            self.win.set_progress_status("Auto podcast: preparing…")
        except Exception:
            pass

        def work():
            return prepare_episodes_for_sync(
                episodes,
                allow_video=False,
                audio_as_video=False,
                target_audio_format=self._target_format(),
            )

        def on_done(prep) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            audio = list(getattr(prep, "audio_tracks", None) or [])
            if not audio:
                logger.warning("Auto podcast prepare produced nothing")
                self._finish_auto_podcast_device_batch(ok=False)
                return
            before_busy = bool(self._transfer_busy)
            # Mid-job podcast queue: append without restarting.
            if (
                before_busy
                and self._active_sync_job is not None
                and getattr(self._active_sync_job, "kind", "") == "podcast"
            ):
                self._enqueue_tracks(
                    audio, kind="podcast", label="Auto Podcast Sync"
                )
                return
            self._transfer_many(
                audio,
                kind="podcast",
                label="Auto Podcast Sync",
                quiet=True,
            )
            if not before_busy and not self._transfer_busy:
                if self._pending_auto_podcast is not None:
                    self._finish_auto_podcast_device_batch(ok=False)

        def on_error(exc: BaseException) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.exception("Auto podcast prepare failed")
            self._finish_auto_podcast_device_batch(ok=False)

        self._bg.submit(
            work, on_done=on_done, on_error=on_error, name="podcast-auto-prepare"
        )

    def _finish_auto_podcast_device_batch(self, *, ok: bool) -> None:
        pending = self._pending_auto_podcast
        self._pending_auto_podcast = None
        self._podcast_auto_device_inflight = False
        ids = list((pending or {}).get("episode_ids") or [])
        if ok and ids:
            # Only clear pending for episodes that actually landed (or vanished).
            # Streaming may attach episode_ids that never made the queue.
            try:
                stems = self._device_guid_stems_for_skip() or set()
                done_ids: list[int] = []
                for eid in ids:
                    ep = get_episode(int(eid))
                    if ep is None:
                        done_ids.append(int(eid))
                        continue
                    g = (ep.guid or "").strip().lower()
                    if g and g in stems:
                        done_ids.append(int(eid))
                if done_ids:
                    mark_episodes_device_synced(done_ids)
            except Exception:
                logger.debug("mark_episodes_device_synced failed", exc_info=True)
        try:
            self.win.lbl_podcast_status.configure(
                text=(
                    "Auto podcast sync finished"
                    if ok
                    else "Auto podcast sync incomplete"
                )
            )
        except Exception:
            pass
        # Host may still be downloading — wait to publish until host is done.
        if self._podcast_auto_host_inflight:
            return
        if not ok:
            # ZEN session is often poisoned after PTP 02ff / LIBMTP panic.
            # Do **not** auto-start another batch; leave pending for Resume /
            # next schedule after the user reconnects.
            try:
                self._device_io.mark_quiet()
            except Exception:
                pass
            logger.warning(
                "Auto podcast batch incomplete — not auto-retrying "
                "(reconnect / Resume Sync if more episodes remain)"
            )
            return
        # Publish day playlist ASAP after a successful podcast batch (uses
        # send-cache object ids; no list_files). Then quiet-drain leftovers.
        if ok:
            self._try_publish_day_podcast_playlist()
        self._schedule_podcast_leftover_drain()

    def _schedule_podcast_leftover_drain(self) -> None:
        """After a quiet pause, sync any still-pending episodes + retry day PL."""
        try:
            self._device_io.mark_quiet()
        except Exception:
            pass
        remaining_ms = 0
        try:
            remaining_ms = int(self._device_io.quiet_remaining_s() * 1000)
        except Exception:
            remaining_ms = 0
        # At least a few seconds after a multi-track podcast flood.
        delay_ms = max(remaining_ms, 4_000)

        def _run() -> None:
            if self._podcast_auto_host_inflight or self._transfer_busy:
                return
            self._maybe_auto_sync_pending_podcasts()
            if (
                not self._podcast_auto_device_inflight
                and not self._transfer_busy
            ):
                # Retry if first publish raced USB quiet / missing handles.
                self._try_publish_day_podcast_playlist()

        try:
            self.win.lbl_podcast_status.configure(
                text=(
                    f"Podcast: waiting {delay_ms // 1000}s for device "
                    "before remaining episodes…"
                    if delay_ms >= 1000
                    else "Podcast: checking for remaining episodes…"
                )
            )
        except Exception:
            pass
        logger.info(
            "Scheduling podcast leftover drain in %dms", delay_ms
        )
        self.win.root.after(delay_ms, _run)

    def _record_day_podcast_playlist_guid(self, guid: str) -> None:
        """Append a successfully sent episode GUID to today's device playlist."""
        g = (guid or "").strip().lower()
        if not is_track_guid(g):
            return
        # Durable plan so restart / leftover auto-sync can still publish today.
        plan = append_day_playlist_guid(g)
        if plan is None:
            return
        self._pending_day_podcast_playlist = {
            "name": plan.get("name") or podcast_day_playlist_name(),
            "guids": list(plan.get("guids") or []),
        }
        logger.debug(
            "Day podcast playlist +1 guid=%s… total=%d",
            g[:8],
            len(self._pending_day_podcast_playlist["guids"]),
        )

    def _try_publish_day_podcast_playlist(self) -> None:
        """Create/update today's on-device playlist for just-sent episodes.

        Membership is GUID-only (episodes already on the player). Object ids
        come from the incremental device index (``record_send`` after each
        transfer). We deliberately **avoid** a full ``list_files`` refresh
        after a long podcast flood — that walk is a common LIBMTP panic
        trigger on ZEN and is unnecessary when send returned real item ids.

        Requires Experimental mode + connected device.
        """
        pending = self._pending_day_podcast_playlist or load_day_playlist_plan()
        if not pending:
            return
        self._pending_day_podcast_playlist = pending
        name = str(pending.get("name") or "").strip()
        guids = [
            g
            for g in (pending.get("guids") or [])
            if is_track_guid(str(g))
        ]
        if not name:
            self._pending_day_podcast_playlist = None
            return
        if not guids:
            if not self._podcast_auto_host_inflight:
                logger.info(
                    "Day podcast playlist %r skipped: no successfully sent "
                    "episode GUIDs",
                    name,
                )
                clear_day_playlist_plan()
                self._pending_day_podcast_playlist = None
            return
        if not self.device.is_connected():
            logger.info(
                "Day podcast playlist %r deferred: device not connected "
                "(%d GUID(s) saved for today)",
                name,
                len(guids),
            )
            return
        if self.win.active_mode() != "experimental":
            logger.info(
                "Day podcast playlist %r needs Experimental mode (PyMTP); "
                "GUIDs=%d kept for later",
                name,
                len(guids),
            )
            return
        serial = self._device_serial or ""
        if not serial:
            return

        def work() -> object:
            # Resolve from send cache only — no list_files after batch.
            track_ids, missing = resolve_track_object_ids(serial, guids)
            if missing:
                logger.warning(
                    "Day podcast playlist %r: %d/%d GUID(s) lack real object "
                    "ids in the send cache (skipping full list_files to avoid "
                    "ZEN panic). Unresolved will be omitted.",
                    name,
                    len(missing),
                    len(guids),
                )
            if not track_ids:
                raise ValueError(
                    "No on-device object ids for day playlist tracks "
                    "(send cache has no real handles yet)."
                )
            # Rebuild ordered guids that actually resolve (preserve order).
            resolved_guids = [
                g
                for g in guids
                if g not in set(missing)
            ]
            parent = playlists_parent_id(self._folder_layout)
            # merge_existing: same-day re-sync must append into "Podcasts
            # <date>" rather than creating a second playlist (ZEN list is
            # incomplete; push discovers via device_index *.zpl + Get_Playlist).
            return push_playlist_to_device(
                device=self.device,
                serial=serial,
                name=name,
                guids_in_order=resolved_guids,
                parent_id=parent,
                merge_existing=True,
            )

        def on_done(result) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            clear_day_playlist_plan()
            self._pending_day_podcast_playlist = None
            if result is None:
                return
            verb = "Created" if result.created else "Updated"
            logger.info(
                "Day podcast playlist on device %s id=%s name=%r tracks=%d "
                "missing=%d",
                verb.lower(),
                result.playlist_id,
                result.name,
                result.resolved,
                result.missing_guid,
            )
            try:
                self.win.lbl_podcast_status.configure(
                    text=(
                        f"Device playlist “{result.name}” "
                        f"({result.resolved} episode(s))"
                    )
                )
            except Exception:
                pass
            try:
                self.win.var_device_playlist_choice.set(result.name or "")
            except Exception:
                pass
            self.win.root.after(
                100,
                lambda: self._refresh_device_playlists_tab(keep_selection=True),
            )

        def on_error(exc: BaseException) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            msg = str(exc).lower()
            if "no on-device object" in msg or "object id" in msg:
                logger.info(
                    "Day podcast playlist %r not ready yet: %s",
                    name,
                    exc,
                )
                return
            # Keep durable plan for retry after reconnect.
            logger.warning(
                "Day podcast playlist publish failed name=%r: %s",
                name,
                exc,
                exc_info=True,
            )

        try:
            self.win.set_progress_status(
                f"Publishing device playlist “{name}”…"
            )
        except Exception:
            pass
        # Hold USB quiet so auto-connect does not probe mid-playlist create.
        try:
            self._device_io.mark_quiet()
        except Exception:
            pass
        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="day-podcast-playlist",
        )

    def on_podcast_sync_latest_all(self) -> None:
        ids = list(self._podcast_ids)
        self._sync_latest_for_podcasts(ids)

    def on_podcast_sync_latest_selected(self) -> None:
        ids = self._selected_podcast_ids()
        if not ids and self._selected_podcast_id is not None:
            ids = [self._selected_podcast_id]
        self._sync_latest_for_podcasts(ids)

    def _sync_latest_for_podcasts(self, podcast_ids: list[int]) -> None:
        if not podcast_ids:
            messagebox.showinfo("Podcast", "No podcasts to sync.")
            return
        if not self._require_sync_ready():
            return
        stems = self._device_guid_stems_for_skip() or set()
        episodes = []
        for pid in podcast_ids:
            ep = pick_latest_not_on_device(pid, stems)
            if ep is not None:
                episodes.append(ep)
        if not episodes:
            messagebox.showinfo(
                "Podcast",
                "Every selected show already has its latest indexed episode "
                "on the device (or has no episodes yet).",
            )
            return
        self._sync_podcast_episodes(episodes, label="Podcast Sync Latest")

    def on_podcast_sync_episodes_selected(self) -> None:
        eids = self._selected_episode_ids()
        if not eids:
            messagebox.showinfo("Podcast", "Select one or more episodes.")
            return
        if not self._require_sync_ready():
            return
        episodes = []
        for eid in eids:
            ep = get_episode(eid)
            if ep is not None:
                episodes.append(ep)
        if not episodes:
            return
        n = len(episodes)
        noun = "Episode" if n == 1 else "Episodes"
        self._update_podcast_episode_menu_labels()
        self._sync_podcast_episodes(
            episodes, label=f"Podcast {n} {noun.lower()}"
        )

    def on_podcast_play_episodes_selected(self) -> None:
        """Download selected episode enclosures if needed, then play locally."""
        eids = self._selected_episode_ids()
        if not eids:
            messagebox.showinfo("Playback", "Select one or more episodes to play.")
            return
        episodes = []
        for eid in eids:
            ep = get_episode(eid)
            if ep is not None:
                episodes.append(ep)
        if not episodes:
            return
        self._update_podcast_episode_menu_labels()
        self.win.lbl_podcast_status.configure(text="Preparing playback…")
        self.win.set_progress_status("Downloading podcast media for playback…")

        def work():
            # Playback always prefers audio (extract from video-only if needed).
            return prepare_episodes_for_sync(
                episodes,
                allow_video=False,
                target_audio_format=self._target_format(),
            )

        def on_done(prep) -> None:
            self.win.set_progress_status("")
            try:
                self.win.lbl_podcast_status.configure(text="")
            except Exception:
                pass
            tracks = list(getattr(prep, "audio_tracks", None) or [])
            if not tracks:
                messagebox.showwarning(
                    "Playback",
                    "No episodes could be prepared for playback "
                    "(download failed or missing enclosures).",
                )
                return
            # Refresh episode statuses (Downloaded) after fetch.
            if self._selected_podcast_id is not None:
                self._load_podcast_episodes(self._selected_podcast_id)
            self._start_playback_queue(tracks)

        def on_error(exc: BaseException) -> None:
            self.win.set_progress_status("")
            try:
                self.win.lbl_podcast_status.configure(text="")
            except Exception:
                pass
            logger.exception("prepare podcast episodes for playback failed")
            messagebox.showerror(
                "Playback", f"Could not prepare episodes:\n{exc}"
            )

        self._bg.submit(
            work, on_done=on_done, on_error=on_error, name="podcast-play"
        )

    def _sync_podcast_episodes(self, episodes: list, *, label: str) -> None:
        """Download enclosures on a worker, then transfer under ZENcast."""
        # Video podcast paths are experimental: only honor when the tools gate
        # is on so a stuck config.json flag cannot keep failing normal syncs.
        exp = bool(self._config.enable_experimental_tools)
        allow_video = exp and bool(self._config.allow_video_podcasts_to_sync)
        audio_as_video = exp and bool(self._config.sync_audio_podcasts_as_video)
        self.win.lbl_podcast_status.configure(text="Preparing episodes…")
        self.win.set_progress_status("Downloading podcast media…")

        def work():
            return prepare_episodes_for_sync(
                episodes,
                allow_video=allow_video,
                audio_as_video=audio_as_video,
                target_audio_format=self._target_format(),
            )

        def on_done(prep) -> None:
            self.win.set_progress_status("")
            audio = list(getattr(prep, "audio_tracks", None) or [])
            video_jobs = list(getattr(prep, "video_jobs", None) or [])
            if not audio and not video_jobs:
                messagebox.showwarning(
                    "Podcast",
                    "No episodes could be prepared for transfer "
                    "(download failed or missing enclosures).",
                )
                return
            if self._selected_podcast_id is not None:
                self._load_podcast_episodes(self._selected_podcast_id)
            # Prefer video first when both: encode+send is a short exclusive
            # device job; audio batch can follow after (or alone).
            if video_jobs and not audio:
                self._start_podcast_video_sync(video_jobs, label=label)
                return
            if video_jobs and audio:
                # Video first (XviD → ZENcast); audio batch chains on success.
                self._pending_podcast_audio_after_video = audio
                self._pending_podcast_audio_label = f"{label} (audio)"
                self._start_podcast_video_sync(
                    video_jobs, label=f"{label} (video)"
                )
                return
            self._transfer_many(audio, kind="podcast", label=label)

        def on_error(exc: BaseException) -> None:
            self.win.set_progress_status("")
            self.win.lbl_podcast_status.configure(text="")
            logger.exception("prepare podcast episodes failed")
            messagebox.showerror("Podcast", f"Could not prepare episodes:\n{exc}")

        self._bg.submit(
            work, on_done=on_done, on_error=on_error, name="podcast-prepare"
        )

    def _podcast_video_encode_profile(self):
        """Default XviD preset for ZEN Vision:M when sending video podcasts."""
        profile = self._active_profile
        if profile is None:
            return None
        opts = getattr(profile, "video_options", None)
        if opts is None:
            return None
        preset = opts.default_preset()
        return preset

    def _start_podcast_video_sync(self, video_jobs: list, *, label: str) -> None:
        """Encode (XviD default on ZEN) and send video podcasts under ZENcast."""
        if not video_jobs:
            return
        if not self._require_sync_ready():
            return
        # Avoid stacking on an in-flight audio podcast batch.
        if self._transfer_busy or self._bg.busy:
            messagebox.showinfo(
                "Podcast",
                "Another transfer is still running.\n\n"
                "Finish or cancel it, then sync the video episode(s) again.",
            )
            return

        transport = self._transport()
        parent = self._podcast_folder_id()
        podcast_folders = bool(self._config.store_podcasts_in_show_folders)
        experimental = self.win.active_mode() == "experimental"
        encode_profile = self._podcast_video_encode_profile()
        encode_for_device = encode_profile is not None
        # Still-from-audio jobs require a device video profile (XviD on ZEN).
        if any(getattr(j, "from_audio_still", False) for j in video_jobs):
            if encode_profile is None:
                messagebox.showerror(
                    "Podcast",
                    "Sync Audio Podcasts as Video needs a device video "
                    "encode profile (Creative ZEN Vision:M).\n\n"
                    "Connect the device or pick the ZEN profile, then try again.",
                )
                return
            encode_for_device = True
        jobs = list(video_jobs)
        serial = self._device_serial or device_serial_key()
        from mtpmanager.infra.remote_naming import DEFAULT_STORAGE_ID

        def work(device):
            _ = device
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            show_cache: dict[str, int] = {}
            results = []
            total = len(jobs)
            for i, job in enumerate(jobs):
                if self._should_cancel_job():
                    raise JobCancelled("Podcast video sync cancelled")
                dest_parent = parent
                if podcast_folders and experimental:
                    from mtpmanager.app.podcast_ops import ensure_podcast_show_folder

                    dest_parent = ensure_podcast_show_folder(
                        self.device,
                        job.podcast.title or "Podcast",
                        podcast_parent_id=parent,
                        folder_cache=show_cache,
                    )

                def on_progress(kind: str, *args, _i=i, _t=total) -> None:
                    if kind == "status" and args and _t > 1:
                        report("status", f"({_i + 1}/{_t}) {args[0]}")
                        return
                    if kind == "phase" and _t > 1:
                        report(kind, *args)
                        report(
                            "status",
                            f"Podcast video {_i + 1}/{_t}: "
                            f"{jobs[_i].episode.title or jobs[_i].local_path}",
                        )
                        return
                    report(kind, *args)

                if total > 1:
                    report(
                        "status",
                        f"Podcast video {i + 1}/{total}: "
                        f"{job.episode.title or os.path.basename(job.local_path)}",
                    )
                logger.info(
                    "Podcast video sync → parent_id=%s (ZENcast/show) "
                    "title=%r guid_index=%s",
                    dest_parent,
                    job.episode.title,
                    job.episode.guid,
                )
                send_result = send_podcast_video_to_zencast(
                    transport,
                    job,
                    parent_id=int(dest_parent),
                    encode_profile=encode_profile,
                    encode_for_device=encode_for_device,
                    on_progress=on_progress,
                    keep_download=bool(self._config.keep_downloaded_podcasts),
                    still_fps=float(self._config.audio_podcast_still_fps),
                    still_width=int(self._config.audio_podcast_still_width),
                    still_height=int(self._config.audio_podcast_still_height),
                )
                if not self._config.keep_downloaded_podcasts:
                    try:
                        discard_episode_local_files(job.episode)
                    except Exception:
                        logger.debug(
                            "discard podcast download after video send failed",
                            exc_info=True,
                        )
                # ObjectFileName = episode title (not GUID); GUID only in host index.
                results.append(
                    {
                        "job": job,
                        "object_id": int(send_result.object_id or 0),
                        "parent_id": int(send_result.parent_id),
                        "remote_name": send_result.remote_basename,
                        "guid": send_result.guid or job.episode.guid,
                    }
                )
            return results

        def on_ui_event(kind: str, *rest) -> None:
            if kind == "phase":
                phase = str(rest[0]) if rest else ""
                if phase == "transcode":
                    try:
                        self.win.progress.configure(mode="determinate")
                        self.win.progress["value"] = 0
                    except Exception:
                        pass
                    self.win.set_progress_status("Encoding podcast video…")
                elif phase == "send":
                    self.win.set_progress_status("Sending podcast video…")
                return
            if kind == "progress":
                if len(rest) >= 3:
                    done, total, msg = int(rest[0]), int(rest[1]), str(rest[2])
                    try:
                        self.win.progress.configure(mode="determinate")
                        if total > 0:
                            self.win.progress["value"] = max(
                                0, min(100, int(round(100 * done / total)))
                            )
                    except Exception:
                        pass
                    if msg:
                        self.win.set_progress_status(msg)
                return
            if kind == "status":
                if rest:
                    self.win.set_progress_status(str(rest[0]))
                return
            self._on_transfer_ui_event(kind, *rest)

        def on_success(results) -> None:
            for row in results or []:
                job = row["job"]
                try:
                    record_send(
                        serial,
                        remote_name=row["remote_name"],
                        guid=row.get("guid") or job.episode.guid,
                        item_id=int(row["object_id"] or 0) or None,
                        parent_id=int(row["parent_id"]),
                        storage_id=DEFAULT_STORAGE_ID,
                    )
                    logger.info(
                        "Podcast video indexed parent=%s remote=%s guid=%s",
                        row["parent_id"],
                        row["remote_name"],
                        row.get("guid") or job.episode.guid,
                    )
                except Exception:
                    logger.debug(
                        "device_index record_send after podcast video failed",
                        exc_info=True,
                    )
            try:
                self.win.lbl_podcast_status.configure(text="")
            except Exception:
                pass
            if self._selected_podcast_id is not None:
                self._load_podcast_episodes(self._selected_podcast_id)
            n = len(results or [])
            encode_note = ""
            if encode_for_device and encode_profile is not None:
                encode_note = f"\nEncode: {encode_profile.display_name}"
            pending_audio = self._pending_podcast_audio_after_video
            pending_label = self._pending_podcast_audio_label or "Podcast audio"
            self._pending_podcast_audio_after_video = None
            self._pending_podcast_audio_label = ""
            if pending_audio:
                messagebox.showinfo(
                    "Podcast",
                    f"Sent {n} video episode(s) to ZENcast."
                    f"{encode_note}\n\n"
                    f"Starting {len(pending_audio)} audio episode(s)…",
                )
                self._transfer_many(
                    pending_audio, kind="podcast", label=pending_label
                )
            else:
                messagebox.showinfo(
                    "Podcast",
                    f"Sent {n} video episode(s) to ZENcast."
                    f"{encode_note}",
                )

        self._run_device_bg(
            title="Podcast video",
            name="podcast-video-sync",
            work=work,
            on_success=on_success,
            busy_message=label or "encoding/sending podcast video…",
            on_progress=on_ui_event,
            progress_mode="determinate",
        )

    def _clear_artist_album_folder_prefs(self, *, reason: str) -> None:
        """Turn off artist/album folder options and update the Config menu."""
        self._config.store_tracks_in_artist_folder = False
        self._config.store_tracks_in_album_folder = False
        self.win.var_artist_folders.set(False)
        self.win.var_album_folders.set(False)
        self.win.set_album_folders_menu_enabled(False)
        logger.info("Disabled artist/album folder prefs (%s)", reason)

    def _podcast_folder_id(self) -> int:
        """ZENcast / Podcasts parent (live layout or legacy default)."""
        from mtpmanager.domain.device_folders import FolderRole
        from mtpmanager.infra.remote_naming import ZEN_VISION_M_FOLDER_NAMES

        try:
            rid = self._folder_layout.id_for(FolderRole.PODCAST)
            if rid is not None and int(rid) > 0:
                return int(rid)
        except Exception:
            pass
        return int(ZEN_VISION_M_FOLDER_NAMES.get("zencast", 128))

    def _parent_folder_resolver(self):
        """Return a resolve_parent_folder callback for music and/or podcasts."""
        experimental = self.win.active_mode() == "experimental"
        connected = self.device.is_connected()
        artist_on = bool(self._config.store_tracks_in_artist_folder)
        podcast_folders = bool(self._config.store_podcasts_in_show_folders)

        if not experimental or not connected:
            # Still send podcasts under ZENcast root when we know the id.
            zencast = self._podcast_folder_id()

            def flat_podcast_or_default(meta) -> int | None:
                genre = (getattr(meta, "genre", "") or "").strip().casefold()
                if genre == "podcast":
                    return zencast
                return None

            return flat_podcast_or_default

        cache: dict[str, int] = {}
        device = self.device
        use_album = bool(self._config.store_tracks_in_album_folder)
        zencast = self._podcast_folder_id()
        show_cache: dict[str, int] = {}

        def resolve(meta) -> int | None:
            genre = (getattr(meta, "genre", "") or "").strip().casefold()
            if genre == "podcast":
                if podcast_folders:
                    from mtpmanager.app.podcast_ops import ensure_podcast_show_folder

                    show = (
                        (getattr(meta, "album", None) or "")
                        or (getattr(meta, "albumartist", None) or "")
                        or "Podcast"
                    )
                    return ensure_podcast_show_folder(
                        device,
                        str(show),
                        podcast_parent_id=zencast,
                        folder_cache=show_cache,
                    )
                return zencast
            if not artist_on:
                return None
            if use_album:
                return ensure_album_folder(
                    device,
                    meta,
                    music_parent_id=self._music_folder_id(),
                    cache=cache,
                )
            return ensure_artist_folder(
                device,
                meta,
                music_parent_id=self._music_folder_id(),
                cache=cache,
            )

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
        """True when at least one configured library root exists on disk."""
        roots = self.library.root_paths
        return any(os.path.isdir(r) for r in roots)

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

    def _require_usable_library(self, *, allow_enqueue: bool = False) -> bool:
        """True when library media can be transferred; shows a dialog otherwise.

        *allow_enqueue*: when True, an active batch transfer queue is OK
        (caller will append tracks instead of starting a new job).
        """
        if self._library_busy:
            messagebox.showinfo(
                "Library",
                "Library is still loading or scanning. Try again in a moment.",
            )
            return False
        if self._transfer_busy:
            if allow_enqueue and self._transfer_queue is not None:
                pass  # mid-job append is allowed
            else:
                messagebox.showinfo(
                    "Transfer",
                    "A transfer is already in progress. Wait for it to finish,\n"
                    "or add tracks via Sync Album / Artist / Selected while a "
                    "batch transfer queue is running.",
                )
                return False
        if not self.library.root_paths:
            messagebox.showinfo(
                "Library",
                "Add a library root first (Library → Manage Library…).",
            )
            return False
        if not self._library_root_reachable():
            messagebox.showinfo(
                "Library",
                "Library root is not reachable.\n"
                "Reconnect the volume or add a root "
                "(Library → Manage Library…).",
            )
            return False
        return True

    def _require_sync_ready(self, *, allow_enqueue: bool = True) -> bool:
        """Library usable + PyMTP connection gate when not in Stable Mode.

        *allow_enqueue* defaults True so Sync Album/Artist/Selected can append
        to an active batch queue.
        """
        if not self._require_usable_library(allow_enqueue=allow_enqueue):
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
        tree = self.win.active_library_tree()

        def add_from_iid(iid: str) -> None:
            track = self._track_by_iid.get(iid)
            if track is not None:
                if track.path not in seen:
                    tracks.append(track)
                    seen.add(track.path)
                return
            for child in tree.get_children(iid):
                add_from_iid(child)

        for iid in iids:
            add_from_iid(iid)
        tracks.sort(key=lambda t: t.path)
        return tracks

    def _on_tree_selection_changed(self, _event=None) -> None:
        """Refresh Sync Selected enablement + left-panel selection detail."""
        if self._library_busy or not self.win._tracks_interactive:
            self.win.set_sync_selected_enabled(False)
            return
        tracks = self._tracks_from_selected_iids(quiet=True)
        self.win.set_sync_selected_enabled(bool(tracks), count=len(tracks))
        self._refresh_selection_detail(tracks)

    def _on_device_tree_selection_changed(self, event=None) -> None:
        """Update left-panel detail for a single device track selection."""
        tree = event.widget if event is not None else self.win.active_device_tree()
        try:
            selection = list(tree.selection())
        except Exception:
            selection = []
        if len(selection) != 1:
            return
        iid = selection[0]
        tags = set(tree.item(iid, "tags") or ())
        if "track" not in tags:
            return

        by_iid = self._device_track_map_for_tree(tree)
        track = by_iid.get(iid)
        if track is not None:
            try:
                self.win.set_context_detail(
                    track_selection_detail(track),
                    path=track.path,
                )
            except Exception:
                pass

    def _refresh_selection_detail(self, tracks: list[Track] | None = None) -> None:
        """Update left-panel label from the current tree selection.

        Keeps the first-run experimental hint until the user selects a row.
        After that the same label shows track / album / artist context.
        """
        iids = self.win.selected_tree_iids()
        if not iids:
            if self.win.is_startup_hint_active():
                return
            self.win.set_context_detail("")
            return

        if tracks is None:
            tracks = self._tracks_from_selected_iids(quiet=True)

        # Multi-select (or multi-row expansion): compact count.
        if len(iids) > 1:
            self.win.set_context_detail(multi_selection_detail(len(tracks)))
            return

        iid = iids[0]
        tree = self.win.active_library_tree()
        tags = set(tree.item(iid, "tags"))
        seed = self._group_seed_by_iid.get(iid)

        if "group_artist" in tags and seed is not None:
            self.win.set_context_detail(
                artist_selection_detail(primary_artist(seed), len(tracks))
            )
            return
        if "group_directory" in tags and seed is not None:
            folder = os.path.basename(
                (os.path.dirname(seed.path) or seed.path).rstrip(os.sep + "/")
            ) or (os.path.dirname(seed.path) or seed.path)
            folder_path = os.path.dirname(seed.path) or seed.path
            self.win.set_context_detail(
                album_selection_detail(
                    folder,
                    artist="",
                    track_count=len(tracks),
                    year="",
                ),
                path=folder_path,
            )
            return
        if "group_album" in tags and seed is not None:
            year = year_from_date(seed.meta.date or "") or ""
            self.win.set_context_detail(
                album_selection_detail(
                    seed.meta.album or "Unknown Album",
                    artist=primary_artist(seed),
                    track_count=len(tracks),
                    year=year,
                ),
                path=os.path.dirname(seed.path) or seed.path,
            )
            return

        track = self._track_by_iid.get(iid)
        if track is not None:
            self.win.set_context_detail(
                track_selection_detail(track),
                path=track.path,
            )
            return

        # Year group or unknown row: fall back to expanded track count.
        if tracks:
            self.win.set_context_detail(multi_selection_detail(len(tracks)))
        elif not self.win.is_startup_hint_active():
            self.win.set_context_detail("")

    def _prepare_context_menu(self, row_iid: str, tags) -> None:
        """Update group/multi-select menu labels before popup."""
        tagset = set(tags)
        seed = self._group_seed_by_iid.get(row_iid)
        self._context_group_seed = seed

        # Multi-select bulk action (track rows and expanded groups).
        selected_tracks = self._tracks_from_selected_iids(quiet=True)
        audio_selected = self._audio_tracks_only(selected_tracks)
        n = len(selected_tracks)
        n_audio = len(audio_selected)
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

        # Play This Track / Play These Tracks (audio only).
        # Index 6: after sync block + separator (label changes; do not key by label).
        try:
            from mtpmanager.ui.window import (
                CTX_ADD_TO_PLAYLIST,
                CTX_ADD_TRACKS_TO_PLAYLIST,
                CTX_PLAY_TRACK,
                CTX_PLAY_TRACKS,
            )

            play_label = CTX_PLAY_TRACKS if n_audio > 1 else CTX_PLAY_TRACK
            self.win.menu_track_ctx.entryconfig(
                6,
                label=play_label,
                state=NORMAL if n_audio >= 1 else DISABLED,
            )
            add_label = (
                CTX_ADD_TRACKS_TO_PLAYLIST
                if n_audio > 1
                else CTX_ADD_TO_PLAYLIST
            )
            self.win.menu_track_ctx.entryconfig(
                7,
                label=add_label,
                state=NORMAL if n_audio >= 1 else DISABLED,
            )
        except Exception:
            pass

        # Add-to-playlist labels for group menus: multi / mixed selection uses
        # the expanded track count (same resolver as the action).
        multi_sel = len(self.win.selected_tree_iids()) > 1
        if n_audio > 1:
            bulk_add_label = f"Add {n_audio} tracks to Playlist…"
        elif n_audio == 1:
            bulk_add_label = "Add This Track to Playlist…"
        else:
            bulk_add_label = "Add to Playlist…"
        add_state = NORMAL if n_audio >= 1 else DISABLED

        if seed is None:
            return
        if "group_artist" in tagset:
            artist = primary_artist(seed)
            self.win.menu_artist_ctx.entryconfig(
                0, label=f"Sync all from {artist}"
            )
            try:
                # Index 2: Play All from Artist (label changes with artist name).
                self.win.menu_artist_ctx.entryconfig(
                    2, label=f"Play All from {artist}"
                )
                artist_add = (
                    bulk_add_label
                    if multi_sel or n_audio > 1
                    else f"Add All from {artist} to Playlist…"
                )
                self.win.menu_artist_ctx.entryconfig(
                    3, label=artist_add, state=add_state
                )
            except Exception:
                pass
        elif "group_directory" in tagset:
            folder = os.path.basename(
                (os.path.dirname(seed.path) or seed.path).rstrip(os.sep + "/")
            ) or "folder"
            self.win.menu_album_ctx.entryconfig(
                0, label=f"Sync folder {folder}"
            )
            try:
                # Index 2: Play Album / folder (label changes).
                self.win.menu_album_ctx.entryconfig(
                    2, label=f"Play folder {folder}"
                )
                folder_add = (
                    bulk_add_label
                    if multi_sel or n_audio > 1
                    else f"Add folder {folder} to Playlist…"
                )
                self.win.menu_album_ctx.entryconfig(
                    3, label=folder_add, state=add_state
                )
            except Exception:
                pass
        elif "group_album" in tagset:
            album = seed.meta.album or "Unknown Album"
            self.win.menu_album_ctx.entryconfig(
                0, label=f"Sync album {album}"
            )
            try:
                self.win.menu_album_ctx.entryconfig(
                    2, label=f"Play Album {album}"
                )
                album_add = (
                    bulk_add_label
                    if multi_sel or n_audio > 1
                    else f"Add Album {album} to Playlist…"
                )
                self.win.menu_album_ctx.entryconfig(
                    3, label=album_add, state=add_state
                )
            except Exception:
                pass

    def _prepare_device_context_menu(self, tree, row_iid: str, tags) -> None:
        """Update device group delete labels before popup."""
        self._device_context_tree = tree
        self._device_context_row = row_iid
        tagset = set(tags)
        try:
            values = tree.item(row_iid, "values") or ()
            label = str(values[0] if values else "").strip() or "group"
        except Exception:
            label = "group"
        try:
            # Group menus: index 0 = Add to playlist, 2 = Delete.
            if "group_artist" in tagset:
                self.win.menu_device_artist_ctx.entryconfig(
                    2, label=f"Delete all from {label}…"
                )
            elif "group_album" in tagset:
                self.win.menu_device_album_ctx.entryconfig(
                    2, label=f"Delete album {label}…"
                )
            elif "group_folder" in tagset:
                self.win.menu_device_folder_ctx.entryconfig(
                    2, label=f"Delete all in {label}…"
                )
        except Exception:
            pass

        tracks = self._device_tracks_from_tree_selection(tree)
        n = len(tracks)
        try:
            if n >= 1:
                noun = "item" if n == 1 else "items"
                self.win.menu_device_track_ctx.entryconfig(
                    0,
                    label=f"Pull {n} {noun} to library…",
                )
                self.win.menu_device_track_ctx.entryconfig(
                    1,
                    label=f"Pull {n} {noun} to folder…",
                )
                self.win.menu_device_track_ctx.entryconfig(
                    2,
                    label=(
                        f"Fetch tags for {n} {noun}…"
                        if n > 1
                        else "Fetch track tags…"
                    ),
                )
                # Index 4: Add to Device Playlist (after first separator).
                self.win.menu_device_track_ctx.entryconfig(
                    4,
                    label=(
                        f"Add {n} {noun} to Device Playlist…"
                        if n > 1
                        else "Add to Device Playlist…"
                    ),
                )
                # Index 6: Delete (after second separator).
                self.win.menu_device_track_ctx.entryconfig(
                    6,
                    label=f"Delete {n} {noun} from device…",
                )
            else:
                self.win.menu_device_track_ctx.entryconfig(
                    0, label="Pull to library…"
                )
                self.win.menu_device_track_ctx.entryconfig(
                    1, label="Pull to folder…"
                )
                self.win.menu_device_track_ctx.entryconfig(
                    2, label="Fetch track tags…"
                )
                self.win.menu_device_track_ctx.entryconfig(
                    4, label="Add to Device Playlist…"
                )
                self.win.menu_device_track_ctx.entryconfig(
                    6, label="Delete from device…"
                )
        except Exception:
            pass
        # Group menus: keep Add label count-aware when selection expands.
        try:
            if "group_artist" in tagset and n >= 1:
                self.win.menu_device_artist_ctx.entryconfig(
                    0,
                    label=(
                        f"Add {n} item{'s' if n != 1 else ''} to Device Playlist…"
                    ),
                )
            elif "group_album" in tagset and n >= 1:
                self.win.menu_device_album_ctx.entryconfig(
                    0,
                    label=(
                        f"Add {n} item{'s' if n != 1 else ''} to Device Playlist…"
                    ),
                )
            elif "group_folder" in tagset and n >= 1:
                self.win.menu_device_folder_ctx.entryconfig(
                    0,
                    label=(
                        f"Add {n} item{'s' if n != 1 else ''} to Device Playlist…"
                    ),
                )
        except Exception:
            pass

    @staticmethod
    def _audio_tracks_only(tracks: list[Track]) -> list[Track]:
        """Drop video files from an audio transfer batch."""
        return [t for t in tracks if not is_video_track(t)]

    # ------------------------------------------------------------------
    # Host library playback (ffplay)
    # ------------------------------------------------------------------

    def on_app_close(self) -> None:
        """Main window close: stop MTP session + host playback, then destroy UI.

        Leaving a long-lived PyMTP session open after quit keeps the device
        claimed on USB and blocks the next Connect / Stable ``mtp-sendtr``.
        """
        try:
            self.shutdown_device_session()
        except Exception:
            logger.debug("device session shutdown on close failed", exc_info=True)
        try:
            self.shutdown_playback()
        except Exception:
            logger.debug("shutdown_playback on close failed", exc_info=True)
        try:
            self.win.root.destroy()
        except Exception:
            logger.debug("root.destroy on close failed", exc_info=True)

    def shutdown_device_session(self) -> None:
        """Stop auto-connect poll and release any open PyMTP session.

        Safe to call more than once (close handler + post-mainloop cleanup).
        """
        self._device_auto_reconnect = False
        self._stop_device_poll()
        # Steal even if a transfer/seed holds the gate — quit must free USB.
        self._device_io.steal("app-close")
        try:
            if self.device.is_connected():
                try:
                    device_ops.disconnect(self.device)
                    logger.info("Disconnected MTP device on application close")
                except Exception:
                    logger.exception("Disconnect on application close failed")
            else:
                # Force-clear a stale non-NULL pointer if is_connected lied.
                try:
                    device_ops.disconnect(self.device)
                except Exception:
                    pass
        finally:
            self._device_io.release(reason="app-close")
            try:
                if getattr(self, "_device_session_lock_held", False):
                    self._device_session_lock.release("gui")
                    self._device_session_lock_held = False
            except Exception:
                logger.debug("device session lock release on close failed", exc_info=True)
        try:
            self._clear_device_session()
        except Exception:
            logger.debug("clear device session on close failed", exc_info=True)

    def shutdown_playback(self) -> None:
        """Stop ffplay, cancel poll/marquee timers, and clear the play queue."""
        self._cancel_playback_poll()
        try:
            self._audio_player.stop()
        except Exception:
            logger.debug("audio_player.stop during shutdown failed", exc_info=True)
        self._playback_queue = []
        self._playback_index = -1
        try:
            # Clears title + cancels the marquee after() timer.
            self.win.set_playback_title("")
            self.win.set_playing_row(None)
            self.win.set_playback_active(False)
        except Exception:
            pass

    def on_always_show_playback_toggle(self) -> None:
        """View → Always show playback controls."""
        always = bool(self.win.var_always_show_playback.get())
        self._config.always_show_playback_controls = always
        try:
            save_app_config(self._config)
        except Exception:
            logger.exception("save_app_config for playback controls failed")
        self.win.set_playback_always_show(always)
        self._refresh_playback_ui()

    def action_play_selected_tracks(self) -> None:
        """Context: Play This Track / Play These Tracks."""
        tracks = self._audio_tracks_only(
            self._tracks_from_selected_iids_tree_order()
        )
        self._start_playback_queue(tracks)

    def action_play_artist_group(self) -> None:
        """Context: Play All from Artist (group header)."""
        seed = self._context_group_seed
        if seed is None:
            iid = self.win.selected_tree_iid()
            if iid:
                seed = self._group_seed_by_iid.get(iid)
        if seed is None:
            messagebox.showinfo("Playback", "Select an artist group first.")
            return
        tracks = self._audio_tracks_only(
            self._tracks_from_iids_tree_order(
                [self.win.selected_tree_iid() or ""]
            )
        )
        if not tracks:
            # Fallback to library filter if selection iid missing.
            tracks = self._audio_tracks_only(
                self.library.filter_by_artist(seed)
            )
        self._start_playback_queue(tracks)

    def action_play_album_group(self) -> None:
        """Context: Play Album / Play folder (group header)."""
        seed = self._context_group_seed
        iid = self.win.selected_tree_iid() or ""
        if seed is None and iid:
            seed = self._group_seed_by_iid.get(iid)
        tracks = self._audio_tracks_only(
            self._tracks_from_iids_tree_order([iid] if iid else [])
        )
        if not tracks and seed is not None:
            # Directory groups: same parent folder; album groups: album filter.
            tags = set()
            try:
                tree = self.win.active_library_tree()
                tags = set(tree.item(iid, "tags") or ())
            except Exception:
                pass
            if "group_directory" in tags:
                tracks = self._audio_tracks_only(
                    self.library.filter_by_directory(seed)
                )
            else:
                tracks = self._audio_tracks_only(
                    self.library.filter_by_album(seed)
                )
        self._start_playback_queue(tracks)

    def _tracks_from_selected_iids_tree_order(self) -> list[Track]:
        """Like selection resolve, but preserve tree order (no path sort)."""
        return self._tracks_from_iids_tree_order(self.win.selected_tree_iids())

    def _tracks_from_iids_tree_order(self, iids: list[str]) -> list[Track]:
        """Map row iids to tracks; group headers expand in tree order."""
        tracks: list[Track] = []
        seen: set[str] = set()
        tree = self.win.active_library_tree()

        def add_from_iid(iid: str) -> None:
            if not iid:
                return
            track = self._track_by_iid.get(iid)
            if track is not None:
                if track.path not in seen:
                    tracks.append(track)
                    seen.add(track.path)
                return
            try:
                children = tree.get_children(iid)
            except Exception:
                children = ()
            for child in children:
                add_from_iid(child)

        for iid in iids:
            add_from_iid(iid)
        return tracks

    def _start_playback_queue(self, tracks: list[Track]) -> None:
        """Replace the play queue and start from the first track."""
        playable = [
            t
            for t in tracks
            if t and t.path and os.path.isfile(t.path) and not is_video_track(t)
        ]
        if not playable:
            messagebox.showinfo(
                "Playback",
                "No playable audio files in the selection.",
            )
            return
        if ffplay_bin() is None:
            messagebox.showerror(
                "Playback",
                "ffplay was not found on PATH.\n\n"
                "Install ffmpeg (Homebrew: brew install ffmpeg) to play "
                "library tracks from the app.",
            )
            return
        self._playback_queue = list(playable)
        self._playback_index = 0
        self._play_queue_index(0)

    def _play_queue_index(self, index: int) -> None:
        if not self._playback_queue:
            self._stop_playback(hide=True)
            return
        if index < 0 or index >= len(self._playback_queue):
            self._stop_playback(hide=True)
            return
        self._playback_index = index
        track = self._playback_queue[index]
        duration = float(track.meta.length_sec or 0.0) if track.meta else 0.0
        try:
            self._audio_player.play(track.path, duration_sec=duration)
        except FileNotFoundError:
            messagebox.showwarning(
                "Playback",
                f"File is missing:\n{track.path}",
            )
            self._advance_after_missing()
            return
        except RuntimeError as e:
            messagebox.showerror("Playback", str(e))
            self._stop_playback(hide=True)
            return
        self.win.set_playback_active(True)
        self._refresh_playing_highlight()
        self._refresh_playback_ui()
        self._schedule_playback_poll()

    def _advance_after_missing(self) -> None:
        """Skip a missing file; stop if nothing left after current."""
        nxt = self._playback_index + 1
        if nxt < len(self._playback_queue):
            self._play_queue_index(nxt)
        else:
            self._stop_playback(hide=True)

    def on_playback_play_pause(self) -> None:
        if not self._audio_player.is_active:
            if self._playback_queue and 0 <= self._playback_index < len(
                self._playback_queue
            ):
                self._play_queue_index(self._playback_index)
            return
        try:
            self._audio_player.toggle_pause()
        except RuntimeError as e:
            messagebox.showerror("Playback", str(e))
            self._stop_playback(hide=True)
            return
        self._refresh_playback_ui()

    def on_playback_prev(self) -> None:
        n = len(self._playback_queue)
        if n <= 1:
            return
        idx = self._playback_index - 1
        if idx < 0:
            idx = n - 1
        self._play_queue_index(idx)

    def on_playback_next(self) -> None:
        n = len(self._playback_queue)
        if n <= 1:
            return
        idx = self._playback_index + 1
        if idx >= n:
            idx = 0
        self._play_queue_index(idx)

    def on_playback_close(self) -> None:
        self._stop_playback(hide=True)

    def on_playback_seek(self, position_sec: float) -> None:
        if not self._audio_player.is_active:
            return
        try:
            self._audio_player.seek(float(position_sec))
        except RuntimeError as e:
            messagebox.showerror("Playback", str(e))
            self._stop_playback(hide=True)
            return
        self._refresh_playback_ui()

    def _stop_playback(self, *, hide: bool) -> None:
        self._cancel_playback_poll()
        try:
            self._audio_player.stop()
        except Exception:
            logger.debug("audio_player.stop failed", exc_info=True)
        if hide and not self._config.always_show_playback_controls:
            self.win.set_playback_active(False)
        else:
            # Session ended but bar may stay (always-show).
            self.win.set_playback_active(
                bool(self._config.always_show_playback_controls)
            )
        self.win.set_playing_row(None)
        self._refresh_playback_ui()

    def _cancel_playback_poll(self) -> None:
        aid = self._playback_poll_after_id
        self._playback_poll_after_id = None
        if aid is not None:
            try:
                self.win.root.after_cancel(aid)
            except Exception:
                pass

    def _schedule_playback_poll(self) -> None:
        self._cancel_playback_poll()
        self._playback_poll_after_id = self.win.root.after(
            200, self._on_playback_poll
        )

    def _on_playback_poll(self) -> None:
        self._playback_poll_after_id = None
        state = self._audio_player.poll()
        if state == "ended":
            # Auto-advance within list; stop after last track (no wrap).
            nxt = self._playback_index + 1
            if 0 <= nxt < len(self._playback_queue):
                self._play_queue_index(nxt)
                return
            self._stop_playback(hide=True)
            return
        if state in ("playing", "paused"):
            self._refresh_playback_ui()
            self._schedule_playback_poll()
            return
        # idle — keep bar in always-show mode only
        self._refresh_playback_ui()

    def _playback_title_for(self, track: Track | None) -> str:
        if track is None:
            return ""
        title = (track.meta.title if track.meta else "") or "Unknown Title"
        artist = primary_artist(track) if track else ""
        if artist and artist != "Unknown Artist":
            return f"{artist} — {title}"
        return title

    def _refresh_playback_ui(self) -> None:
        active = self._audio_player.is_active
        playing = self._audio_player.is_playing
        paused = self._audio_player.is_paused
        track: Track | None = None
        if self._playback_queue and 0 <= self._playback_index < len(
            self._playback_queue
        ):
            track = self._playback_queue[self._playback_index]
        show_nav = len(self._playback_queue) > 1 and (
            active or bool(self._config.always_show_playback_controls)
        )
        # When always-show and idle, keep nav if a queue is loaded.
        if not active and self._playback_queue and len(self._playback_queue) > 1:
            show_nav = True
        enabled = active or (
            bool(self._config.always_show_playback_controls)
            and bool(self._playback_queue)
        )
        duration = (
            self._audio_player.duration_sec
            if active
            else float((track.meta.length_sec if track and track.meta else 0) or 0)
        )
        position = self._audio_player.position_sec() if active else 0.0
        title = self._playback_title_for(track) if (active or track) else ""
        self.win.update_playback_state(
            title=title,
            position_sec=position,
            duration_sec=duration,
            playing=playing,
            paused=paused or (not active and bool(self._playback_queue)),
            show_nav=show_nav and bool(self._playback_queue),
            enabled=enabled if (active or self._playback_queue) else False,
        )
        if active:
            self.win.set_playback_active(True)
        elif not self._config.always_show_playback_controls:
            self.win.set_playback_active(False)

    def _refresh_playing_highlight(self) -> None:
        """Re-apply lavender highlight for the current queue track."""
        path = ""
        if self._audio_player.is_active and self._audio_player.path:
            path = self._audio_player.path
        elif (
            self._playback_queue
            and 0 <= self._playback_index < len(self._playback_queue)
        ):
            path = self._playback_queue[self._playback_index].path
        iid = self._iid_by_path.get(path) if path else None
        self.win.set_playing_row(iid if self._audio_player.is_active else None)

    # ------------------------------------------------------------------
    # Host playlists (M3U in library index)
    # ------------------------------------------------------------------

    def on_manage_playlists(self) -> None:
        """Library → Manage Playlists… focuses the Playlists tab."""
        self.win.show_playlists_tab()
        self._refresh_playlist_tab()

    def _refresh_playlist_tab(self, *, keep_selection: bool = True) -> None:
        """Reload playlist dropdown and current tree from the index DB."""
        prev_name = ""
        if keep_selection:
            prev_name = (self.win.var_playlist_choice.get() or "").strip()
        infos = list_playlists()
        self._playlist_ids_by_name = {p.name: p.id for p in infos}
        names = [p.name for p in infos]
        selected = prev_name if prev_name in names else (names[0] if names else "")
        self.win.set_playlist_combo_values(names, selected=selected)
        if selected:
            self._load_playlist_by_name(selected)
        else:
            self._current_playlist_id = None
            self._playlist_track_by_iid.clear()
            self.win.clear_playlist_tree()
            try:
                self.win.lbl_playlist_status.configure(text="No playlists")
            except Exception:
                pass

    def on_playlist_combo_selected(self) -> None:
        name = (self.win.var_playlist_choice.get() or "").strip()
        if name:
            self._load_playlist_by_name(name)

    def _load_playlist_by_name(self, name: str) -> None:
        pid = self._playlist_ids_by_name.get(name)
        if pid is None:
            return
        pl = get_playlist(pid)
        if pl is None:
            self._refresh_playlist_tab(keep_selection=False)
            return
        self._current_playlist_id = pl.id
        tracks = resolve_playlist_tracks(pl)
        self._populate_playlist_tree(tracks)
        n = len(tracks)
        try:
            self.win.lbl_playlist_status.configure(
                text=f"{n} track{'s' if n != 1 else ''}"
            )
        except Exception:
            pass

    def _populate_playlist_tree(
        self,
        tracks: list[Track],
        *,
        select_paths: list[str] | None = None,
    ) -> None:
        self.win.clear_playlist_tree()
        self._playlist_track_by_iid.clear()
        tree = self.win.playlist_tree
        want = {os.path.normpath(p) for p in (select_paths or []) if p}
        select_iids: list[str] = []
        for i, track in enumerate(tracks, start=1):
            num, title, artist, album, year = iter_track_cells(track)
            iid = f"pl:{i}:{track.path}"
            tags = ["track"]
            if not track.path or not os.path.isfile(track.path):
                tags.append("dead")
            tree.insert(
                "",
                "end",
                iid=iid,
                text=str(i),
                values=(title, artist, album, year),
                tags=tuple(tags),
            )
            self._playlist_track_by_iid[iid] = track
            if track.path and os.path.normpath(track.path) in want:
                select_iids.append(iid)
        if select_iids:
            try:
                tree.selection_set(select_iids)
                tree.focus(select_iids[0])
                tree.see(select_iids[0])
            except Exception:
                pass

    def on_playlist_new(self) -> None:
        from mtpmanager.ui.dialogs import ask_text

        name = ask_text(
            self.win.root,
            title="New Playlist",
            prompt="Playlist name:",
        )
        if not name:
            return
        try:
            pl = create_playlist(name)
        except ValueError as e:
            messagebox.showerror("Playlist", str(e))
            return
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not create playlist:\n{e}")
            return
        self._refresh_playlist_tab(keep_selection=False)
        self.win.var_playlist_choice.set(pl.name)
        self._load_playlist_by_name(pl.name)

    def on_playlist_delete(self) -> None:
        name = (self.win.var_playlist_choice.get() or "").strip()
        pid = self._playlist_ids_by_name.get(name)
        if pid is None:
            return
        if not messagebox.askyesno(
            "Delete Playlist",
            f"Delete playlist “{name}”?\n\nThis cannot be undone.",
        ):
            return
        delete_playlist(pid)
        self._current_playlist_id = None
        self._refresh_playlist_tab(keep_selection=False)

    def on_playlist_rename(self) -> None:
        from mtpmanager.ui.dialogs import ask_text

        name = (self.win.var_playlist_choice.get() or "").strip()
        pid = self._playlist_ids_by_name.get(name)
        if pid is None:
            return
        new_name = ask_text(
            self.win.root,
            title="Rename Playlist",
            prompt="New name:",
            initialvalue=name,
        )
        if not new_name or new_name == name:
            return
        try:
            pl = rename_playlist(pid, new_name)
        except ValueError as e:
            messagebox.showerror("Playlist", str(e))
            return
        self._refresh_playlist_tab(keep_selection=False)
        self.win.var_playlist_choice.set(pl.name)
        self._load_playlist_by_name(pl.name)

    def action_playlist_remove_selected(self) -> None:
        pid = self._current_playlist_id
        if pid is None:
            return
        try:
            sel = list(self.win.playlist_tree.selection())
        except Exception:
            sel = []
        paths = []
        for iid in sel:
            t = self._playlist_track_by_iid.get(iid)
            if t and t.path:
                paths.append(t.path)
        if not paths:
            messagebox.showinfo("Playlist", "Select track(s) to remove.")
            return
        try:
            remove_paths_from_playlist(pid, paths)
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not remove tracks:\n{e}")
            return
        name = (self.win.var_playlist_choice.get() or "").strip()
        if name:
            self._load_playlist_by_name(name)

    def action_playlist_move_selected(self, delta: int) -> None:
        """Reorder selected host-playlist tracks (local M3U); device on next sync."""
        pid = self._current_playlist_id
        if pid is None:
            return
        try:
            sel = list(self.win.playlist_tree.selection())
        except Exception:
            sel = []
        paths: list[str] = []
        for iid in sel:
            t = self._playlist_track_by_iid.get(iid)
            if t and t.path:
                paths.append(t.path)
        if not paths:
            messagebox.showinfo(
                "Playlist",
                "Select track(s) to reorder.\n\n"
                "Host order is saved immediately; use Sync playlist to device "
                "to overwrite the on-device playlist.",
            )
            return
        try:
            pl = move_paths_in_playlist(pid, paths, delta=int(delta))
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not reorder tracks:\n{e}")
            return
        tracks = resolve_playlist_tracks(pl)
        self._populate_playlist_tree(tracks, select_paths=paths)
        n = len(tracks)
        try:
            self.win.lbl_playlist_status.configure(
                text=f"{n} track{'s' if n != 1 else ''} · order saved (sync device to apply)"
            )
        except Exception:
            pass

    def _playlist_seed_track(self) -> Track | None:
        """Track for shuffle RNG seed: tree focus, else first selection."""
        tree = self.win.playlist_tree
        try:
            focus = tree.focus()
        except Exception:
            focus = ""
        if focus:
            t = self._playlist_track_by_iid.get(focus)
            if t is not None:
                return t
        try:
            sel = list(tree.selection())
        except Exception:
            sel = []
        for iid in sel:
            t = self._playlist_track_by_iid.get(iid)
            if t is not None:
                return t
        return None

    def action_playlist_shuffle(self, algorithm: str) -> None:
        """Reorder whole host playlist via merge or Spotify-style shuffle.

        The focused/selected row seeds the RNG so re-running with the same seed
        track is reproducible. Saves local M3U only; device updates on Sync.
        """
        pid = self._current_playlist_id
        if pid is None:
            messagebox.showinfo("Playlist", "Select a playlist first.")
            return
        pl = get_playlist(pid)
        if pl is None:
            return
        tracks = resolve_playlist_tracks(pl)
        if len(tracks) < 2:
            messagebox.showinfo("Playlist", "Need at least two tracks to shuffle.")
            return
        seed = self._playlist_seed_track()
        if seed is None:
            seed = tracks[0]
        algo = (algorithm or "").strip().lower()
        rng = rng_from_seed_track(seed, extra=algo)
        if algo in ("artist", "merge", "merge_shuffle", "merge_artist"):
            shuffled = merge_shuffle(tracks, rng=rng)
            label = "artist"
        elif algo in ("spotify", "spotify_shuffle", "dither"):
            shuffled = spotify_shuffle(tracks, rng=rng)
            label = "spotify"
        else:
            messagebox.showerror("Playlist", f"Unknown shuffle algorithm: {algorithm!r}")
            return
        try:
            pl = replace_playlist_tracks(pid, shuffled)
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not shuffle playlist:\n{e}")
            return
        keep = [seed.path] if seed.path else None
        resolved = resolve_playlist_tracks(pl)
        self._populate_playlist_tree(resolved, select_paths=keep)
        n = len(resolved)
        try:
            self.win.lbl_playlist_status.configure(
                text=(
                    f"{n} tracks · {label} shuffle (seed track kept selected; "
                    "sync device to apply)"
                )
            )
        except Exception:
            pass
        logger.info(
            "Playlist shuffle algorithm=%s id=%s tracks=%d seed=%s",
            label,
            pid,
            n,
            (seed.path or "")[:80],
        )

    def action_playlist_play_selected(self) -> None:
        try:
            sel = list(self.win.playlist_tree.selection())
        except Exception:
            sel = []
        tracks: list[Track] = []
        for iid in sel:
            t = self._playlist_track_by_iid.get(iid)
            if t is not None:
                tracks.append(t)
        if not tracks and self._current_playlist_id is not None:
            # No selection: play whole playlist.
            pl = get_playlist(self._current_playlist_id)
            if pl is not None:
                tracks = resolve_playlist_tracks(pl)
        self._start_playback_queue(self._audio_tracks_only(tracks))

    def action_sync_current_playlist(self) -> None:
        """Sync playlist tracks, then recreate the playlist on-device (PyMTP)."""
        pid = self._current_playlist_id
        name = (self.win.var_playlist_choice.get() or "").strip() or "playlist"
        if pid is None:
            messagebox.showinfo("Playlist", "Select a playlist first.")
            return
        if not self._require_sync_ready():
            return
        pl = get_playlist(pid)
        if pl is None:
            messagebox.showwarning("Playlist", "Playlist not found.")
            self._refresh_playlist_tab(keep_selection=False)
            return
        tracks = self._audio_tracks_only(resolve_playlist_tracks(pl))
        existing = [t for t in tracks if t.path and os.path.isfile(t.path)]
        missing = len(tracks) - len(existing)
        if missing:
            logger.info(
                "Playlist sync: skipping %d missing file(s) in %r",
                missing,
                name,
            )
        if not existing:
            messagebox.showinfo(
                "Playlist",
                "No playable audio files found in this playlist.",
            )
            return
        guids = ordered_guids_from_tracks(existing)
        if not guids:
            messagebox.showwarning(
                "Playlist",
                "Playlist tracks have no host GUIDs yet.\n\n"
                "Rescan the library so tracks are indexed, then try again.",
            )
            return
        experimental = self.win.active_mode() == "experimental"
        if not experimental:
            messagebox.showinfo(
                "Playlist",
                "Tracks will transfer using Stable Mode.\n\n"
                "On-device playlist objects require Experimental (PyMTP).\n"
                "Uncheck Config → Stable Mode, Connect, then Sync playlist "
                "again to create/update the playlist on the player.",
            )
        self._pending_device_playlist = {
            "name": name,
            "guids": list(guids),
            "host_id": pid,
            "publish": experimental,
        }
        self._transfer_many(
            existing,
            kind="playlist",
            label=f"Playlist {name}",
        )

    def _clear_pending_device_playlist(self) -> None:
        self._pending_device_playlist = None

    # ------------------------------------------------------------------
    # Device → Playlists (on-device MTP playlist objects)
    # ------------------------------------------------------------------

    def _device_playlists_ready(self) -> tuple[bool, str]:
        """Return (ok, user_message) for Device → Playlists operations."""
        if self.win.active_mode() != "experimental":
            return (
                False,
                "On-device playlists require Experimental mode (PyMTP).\n\n"
                "Uncheck Config → Stable Mode, Connect, then try again.",
            )
        if not self.device.is_connected():
            return (
                False,
                "Connect the device first (Device → Connect).",
            )
        if not callable(getattr(self.device, "list_playlists", None)):
            return (
                False,
                "This transport cannot list on-device playlists.",
            )
        return True, ""

    def _clear_device_playlists_ui(self, *, status: str = "") -> None:
        self._device_playlists = []
        self._device_playlist_by_name = {}
        self._device_playlist_label_by_id = {}
        self._current_device_playlist = None
        self._device_playlist_track_by_iid.clear()
        self._device_playlist_item_ids = []
        try:
            self.win.clear_device_playlist_tree()
            self.win.set_device_playlist_combo_values(
                [], selected="", interactive=False
            )
            self.win.lbl_device_playlist_status.configure(text=status or "")
        except Exception:
            pass

    def action_refresh_device_playlists(self) -> None:
        """Reload on-device playlists from the player (PyMTP get_playlists)."""
        ok, msg = self._device_playlists_ready()
        if not ok:
            messagebox.showinfo("Device Playlists", msg)
            self._clear_device_playlists_ui(status="Not available")
            return
        self._refresh_device_playlists_tab(keep_selection=True)

    def _refresh_device_playlists_tab(self, *, keep_selection: bool = True) -> None:
        """Background list_playlists → combo + current tree."""
        ok, msg = self._device_playlists_ready()
        if not ok:
            self._clear_device_playlists_ui(
                status=msg.split("\n", 1)[0] if msg else "Not available"
            )
            return
        if self._device_playlist_load_inflight or self._device_playlist_mutate_inflight:
            return
        if self._transfer_busy:
            try:
                self.win.lbl_device_playlist_status.configure(
                    text="Wait for transfer to finish…"
                )
            except Exception:
                pass
            return

        prev_name = ""
        if keep_selection:
            prev_name = (
                self.win.var_device_playlist_choice.get() or ""
            ).strip()

        if not self._device_io.try_acquire("device-playlists-list"):
            try:
                self.win.lbl_device_playlist_status.configure(
                    text="Device busy — try Refresh shortly"
                )
            except Exception:
                pass
            return

        self._device_playlist_load_inflight = True
        try:
            self.win.lbl_device_playlist_status.configure(
                text="Loading playlists…"
            )
            self.win.set_progress_status("Listing on-device playlists…")
        except Exception:
            pass

        serial = self._device_serial or ""
        parent = playlists_parent_id(self._folder_layout)

        def work():
            # ZEN stores playlists as *.zpl under My Playlists. Discover those
            # from the device_index (filled by list_files seed) and hydrate via
            # Get_Playlist — Get_Playlist_List alone often returns only one.
            candidates = []
            names: dict[int, str] = {}
            if serial:
                try:
                    files = list_cached_files(serial)
                    cands = playlist_candidates_from_files(
                        files,
                        playlist_parent_ids={int(parent)} if parent else None,
                    )
                    for e in cands:
                        oid = int(e.item_id or 0)
                        if oid > 0:
                            candidates.append(oid)
                            names[oid] = str(e.name or "")
                except Exception:
                    logger.debug(
                        "playlist candidates from device_index failed",
                        exc_info=True,
                    )
            lister = getattr(self.device, "list_playlists_complete", None)
            if callable(lister):
                return list(
                    lister(
                        candidate_ids=candidates,
                        candidate_names=names,
                    )
                    or []
                )
            base = list(self.device.list_playlists() or [])
            extras: list[DevicePlaylist] = []
            known = {int(p.playlist_id) for p in base}
            getter = getattr(self.device, "get_playlist", None)
            if callable(getter):
                for oid in candidates:
                    if oid in known:
                        continue
                    try:
                        extras.append(getter(oid))
                    except Exception:
                        extras.append(
                            DevicePlaylist(
                                playlist_id=oid,
                                name=names.get(oid) or f"Playlist {oid}",
                                track_ids=(),
                            )
                        )
            return merge_device_playlists(base, extras)

        def on_done(result) -> None:
            self._device_playlist_load_inflight = False
            self._device_io.release(
                reason="device-playlists-list", quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            playlists: list[DevicePlaylist] = []
            if isinstance(result, list):
                playlists = [p for p in result if isinstance(p, DevicePlaylist)]
            self._apply_device_playlist_list(
                playlists, prefer_name=prev_name
            )

        def on_error(exc: BaseException) -> None:
            self._device_playlist_load_inflight = False
            self._device_io.release(
                reason="device-playlists-list", quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.warning("list_playlists failed: %s", exc, exc_info=True)
            self._clear_device_playlists_ui(status="List failed")
            messagebox.showerror(
                "Device Playlists",
                f"Could not list on-device playlists:\n\n{exc}",
            )

        self._bg.submit(
            work, on_done=on_done, on_error=on_error, name="device-playlists-list"
        )

    def _device_playlist_labels(
        self, playlists: list[DevicePlaylist]
    ) -> tuple[list[str], dict[str, DevicePlaylist], dict[int, str]]:
        """Build unique combobox labels (disambiguate duplicate names by id)."""
        base_counts: dict[str, int] = {}
        bases: list[tuple[DevicePlaylist, str]] = []
        for pl in playlists:
            base = playlist_display_name(pl.name or "", int(pl.playlist_id or 0))
            bases.append((pl, base))
            base_counts[base.casefold()] = base_counts.get(base.casefold(), 0) + 1
        by_name: dict[str, DevicePlaylist] = {}
        by_id: dict[int, str] = {}
        names: list[str] = []
        for pl, base in bases:
            if base_counts.get(base.casefold(), 0) > 1:
                label = f"{base} [{pl.playlist_id}]"
            else:
                label = base
            # Ensure absolute uniqueness even if ids collide in display.
            if label in by_name:
                label = f"{base} [{pl.playlist_id}]"
            by_name[label] = pl
            by_id[int(pl.playlist_id)] = label
            names.append(label)
        return names, by_name, by_id

    def _apply_device_playlist_list(
        self,
        playlists: list[DevicePlaylist],
        *,
        prefer_name: str = "",
    ) -> None:
        ordered = sorted(
            playlists,
            key=lambda p: (
                playlist_display_name(p.name or "", int(p.playlist_id or 0)).casefold(),
                int(p.playlist_id or 0),
            ),
        )
        self._device_playlists = ordered
        names, by_name, by_id = self._device_playlist_labels(ordered)
        self._device_playlist_by_name = by_name
        self._device_playlist_label_by_id = by_id
        selected = prefer_name if prefer_name in by_name else ""
        if not selected and prefer_name:
            # Prefer by display name match (before disambiguator) or raw name.
            want = prefer_name.casefold()
            for label, pl in by_name.items():
                if label.casefold() == want:
                    selected = label
                    break
                if playlist_display_name(pl.name or "", pl.playlist_id).casefold() == want:
                    selected = label
                    break
        if not selected:
            selected = names[0] if names else ""
        interactive = self.device.is_connected() and (
            self.win.active_mode() == "experimental"
        )
        self.win.set_device_playlist_combo_values(
            names, selected=selected, interactive=interactive
        )
        if selected:
            self._load_device_playlist_by_name(selected)
        else:
            self._current_device_playlist = None
            self._device_playlist_item_ids = []
            self._device_playlist_track_by_iid.clear()
            self.win.clear_device_playlist_tree()
            try:
                self.win.lbl_device_playlist_status.configure(
                    text="No playlists on device"
                )
            except Exception:
                pass

    def on_device_playlist_combo_selected(self) -> None:
        name = (self.win.var_device_playlist_choice.get() or "").strip()
        if name:
            self._load_device_playlist_by_name(name)

    def _load_device_playlist_by_name(self, name: str) -> None:
        pl = self._device_playlist_by_name.get(name)
        if pl is None:
            return
        self._current_device_playlist = pl
        self._device_playlist_item_ids = [
            int(x) for x in (pl.track_ids or ()) if int(x) > 0
        ]
        tracks = self._tracks_for_device_playlist_ids(
            self._device_playlist_item_ids
        )
        self._populate_device_playlist_tree(tracks)
        n = len(self._device_playlist_item_ids)
        dead = sum(
            1
            for t in tracks
            if t.meta and (t.meta.title or "").startswith("Missing object ")
        )
        try:
            extra = f" · {dead} missing" if dead else ""
            self.win.lbl_device_playlist_status.configure(
                text=f"{n} track{'s' if n != 1 else ''}{extra}"
            )
        except Exception:
            pass

    def _refs_by_item_id_for_playlists(self) -> dict[int, DeviceTrackRef]:
        """item_id → ref from live device trees + full device_index cache."""
        by_id = self._device_refs_by_item_id()
        serial = self._device_serial
        if not serial:
            return by_id
        try:
            files = list_cached_files(serial)
            for ref in track_refs_from_files(files):
                oid = int(ref.item_id or 0)
                if oid > 0 and oid not in by_id:
                    by_id[oid] = ref
        except Exception:
            logger.debug(
                "device playlist: list_cached_files failed", exc_info=True
            )
        return by_id

    def _tracks_for_device_playlist_ids(
        self, item_ids: list[int]
    ) -> list[Track]:
        """Ordered Tracks for playlist membership (host tags when possible)."""
        by_id = self._refs_by_item_id_for_playlists()
        refs: list[DeviceTrackRef] = []
        for oid in item_ids:
            ref = by_id.get(int(oid))
            if ref is None:
                refs.append(
                    DeviceTrackRef(
                        item_id=int(oid),
                        name=f"id={oid}",
                        title=f"Missing object {oid}",
                        artist="Unknown Artist",
                        album="Unknown Album",
                    )
                )
            else:
                refs.append(ref)
        by_guid = self._host_tracks_by_guid_for_refs(refs)
        refs = enrich_refs_from_host(refs, by_guid)
        # Preserve playlist order (resolve_device_tracks_for_display keeps input order).
        return resolve_device_tracks_for_display(refs, by_guid)

    def _populate_device_playlist_tree(
        self,
        tracks: list[Track],
        *,
        select_item_ids: list[int] | None = None,
    ) -> None:
        self.win.clear_device_playlist_tree()
        self._device_playlist_track_by_iid.clear()
        tree = self.win.device_playlist_tree
        want = {int(x) for x in (select_item_ids or []) if int(x) > 0}
        select_iids: list[str] = []
        for i, track in enumerate(tracks, start=1):
            num, title, artist, album, year = iter_track_cells(track)
            oid = self._item_id_from_device_track(track) or 0
            iid = f"dpl:{i}:{oid}"
            tags = ["track"]
            host = self._host_path_for_device_track(track)
            if not host and (
                (title or "").startswith("Missing object ")
                or not oid
            ):
                tags.append("dead")
            tree.insert(
                "",
                "end",
                iid=iid,
                text=str(i),
                values=(title, artist, album, year),
                tags=tuple(tags),
            )
            self._device_playlist_track_by_iid[iid] = track
            if oid and oid in want:
                select_iids.append(iid)
        if select_iids:
            try:
                tree.selection_set(select_iids)
                tree.focus(select_iids[0])
                tree.see(select_iids[0])
            except Exception:
                pass

    def _selected_device_playlist_indices(self) -> list[int]:
        """0-based indices of selected rows in the current device playlist."""
        try:
            sel = list(self.win.device_playlist_tree.selection())
        except Exception:
            sel = []
        indices: list[int] = []
        for iid in sel:
            # iid = dpl:<1-based index>:<item_id>
            try:
                parts = str(iid).split(":")
                if len(parts) >= 2:
                    indices.append(int(parts[1]) - 1)
            except (TypeError, ValueError):
                continue
        return indices

    def _run_device_playlist_mutation(
        self,
        *,
        name: str,
        work: Callable[[], object],
        on_success: Callable[[object], None] | None = None,
        status: str = "Updating playlist…",
        error_title: str = "Device Playlists",
    ) -> None:
        ok, msg = self._device_playlists_ready()
        if not ok:
            messagebox.showinfo(error_title, msg)
            return
        if self._device_playlist_mutate_inflight or self._device_playlist_load_inflight:
            messagebox.showinfo(
                error_title, "Another playlist operation is already running."
            )
            return
        if self._transfer_busy:
            messagebox.showinfo(
                error_title, "Wait for the current transfer to finish."
            )
            return
        if not self._device_io.try_acquire(name):
            messagebox.showinfo(
                error_title,
                f"Device is busy ({self._device_io.holder or 'unknown'}).",
            )
            return

        self._device_playlist_mutate_inflight = True
        try:
            self.win.lbl_device_playlist_status.configure(text=status)
            self.win.set_progress_status(status)
        except Exception:
            pass

        def on_done(result) -> None:
            self._device_playlist_mutate_inflight = False
            self._device_io.release(reason=name, quiet_s=_DEVICE_USB_COOLDOWN_S)
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            if on_success is not None:
                try:
                    on_success(result)
                except Exception:
                    logger.exception("device playlist mutation success handler")

        def on_error(exc: BaseException) -> None:
            self._device_playlist_mutate_inflight = False
            self._device_io.release(reason=name, quiet_s=_DEVICE_USB_COOLDOWN_S)
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.warning("device playlist %s failed: %s", name, exc, exc_info=True)
            messagebox.showerror(error_title, f"Playlist operation failed:\n\n{exc}")
            # Reload from device to resync UI.
            self._refresh_device_playlists_tab(keep_selection=True)

        self._bg.submit(work, on_done=on_done, on_error=on_error, name=name)

    def _write_current_device_playlist_tracks(
        self,
        new_ids: list[int],
        *,
        new_name: str | None = None,
        select_item_ids: list[int] | None = None,
        status_suffix: str = "",
    ) -> None:
        """Push updated track list (and optional rename) for the current playlist."""
        pl = self._current_device_playlist
        if pl is None or int(pl.playlist_id or 0) <= 0:
            messagebox.showinfo("Device Playlists", "Select a playlist first.")
            return
        clean_name = (new_name if new_name is not None else pl.name or "").strip()
        if not clean_name:
            messagebox.showinfo("Device Playlists", "Playlist name is required.")
            return
        parent = int(pl.parent_id or 0) or playlists_parent_id(self._folder_layout)
        storage = int(pl.storage_id or 0) or DEFAULT_STORAGE_ID
        pid = int(pl.playlist_id)
        ids = [int(x) for x in new_ids if int(x) > 0]

        def work():
            return self.device.update_playlist(
                pid,
                clean_name,
                ids,
                parent_id=parent,
                storage_id=storage,
            )

        def on_success(_result) -> None:
            updated = DevicePlaylist(
                playlist_id=pid,
                name=clean_name,
                parent_id=parent,
                storage_id=storage,
                track_ids=tuple(ids),
            )
            for i, p in enumerate(self._device_playlists):
                if int(p.playlist_id) == pid:
                    self._device_playlists[i] = updated
                    break
            else:
                self._device_playlists.append(updated)
            label = playlist_display_name(clean_name, pid)
            self._apply_device_playlist_list(
                self._device_playlists, prefer_name=label
            )
            # Re-select tracks after tree rebuild from _apply.
            if select_item_ids:
                tracks = self._tracks_for_device_playlist_ids(ids)
                self._populate_device_playlist_tree(
                    tracks, select_item_ids=select_item_ids
                )
            n = len(ids)
            suffix = f" · {status_suffix}" if status_suffix else ""
            try:
                self.win.lbl_device_playlist_status.configure(
                    text=f"{n} track{'s' if n != 1 else ''}{suffix}"
                )
            except Exception:
                pass

        self._run_device_playlist_mutation(
            name="device-playlist-update",
            work=work,
            on_success=on_success,
            status=f"Updating “{clean_name}”…",
        )

    def on_device_playlist_new(self) -> None:
        ok, msg = self._device_playlists_ready()
        if not ok:
            messagebox.showinfo("Device Playlists", msg)
            return
        name = ask_text(
            self.win.root,
            title="New Device Playlist",
            prompt="Playlist name:",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        if clean.casefold() in {
            n.casefold() for n in self._device_playlist_by_name
        }:
            messagebox.showerror(
                "Device Playlists",
                f"A playlist named “{clean}” already exists on the device.",
            )
            return
        parent = playlists_parent_id(self._folder_layout)
        storage = DEFAULT_STORAGE_ID

        def work():
            return int(
                self.device.create_playlist(
                    clean,
                    [],
                    parent_id=parent,
                    storage_id=storage,
                )
            )

        def on_success(result) -> None:
            new_id = int(result or 0)
            pl = DevicePlaylist(
                playlist_id=new_id,
                name=clean,
                parent_id=parent,
                storage_id=storage,
                track_ids=(),
            )
            self._device_playlists.append(pl)
            self._apply_device_playlist_list(
                self._device_playlists, prefer_name=clean
            )

        self._run_device_playlist_mutation(
            name="device-playlist-create",
            work=work,
            on_success=on_success,
            status=f"Creating “{clean}”…",
        )

    def on_device_playlist_delete(self) -> None:
        pl = self._current_device_playlist
        if pl is None:
            messagebox.showinfo("Device Playlists", "Select a playlist first.")
            return
        name = (pl.name or "").strip() or f"id={pl.playlist_id}"
        if not messagebox.askyesno(
            "Delete Device Playlist",
            f"Delete on-device playlist “{name}”?\n\n"
            "This removes the playlist object only — tracks stay on the player.",
        ):
            return
        pid = int(pl.playlist_id)

        def work():
            self.device.delete_object(pid)
            return pid

        def on_success(_result) -> None:
            self._device_playlists = [
                p
                for p in self._device_playlists
                if int(p.playlist_id) != pid
            ]
            self._apply_device_playlist_list(
                self._device_playlists, prefer_name=""
            )

        self._run_device_playlist_mutation(
            name="device-playlist-delete",
            work=work,
            on_success=on_success,
            status=f"Deleting “{name}”…",
        )

    def on_device_playlist_rename(self) -> None:
        pl = self._current_device_playlist
        if pl is None:
            return
        name = (pl.name or "").strip()
        new_name = ask_text(
            self.win.root,
            title="Rename Device Playlist",
            prompt="New name:",
            initialvalue=name,
        )
        if not new_name or new_name.strip() == name:
            return
        clean = new_name.strip()
        if clean.casefold() in {
            n.casefold()
            for n in self._device_playlist_by_name
            if n.casefold() != name.casefold()
        }:
            messagebox.showerror(
                "Device Playlists",
                f"A playlist named “{clean}” already exists on the device.",
            )
            return
        self._write_current_device_playlist_tracks(
            list(self._device_playlist_item_ids),
            new_name=clean,
            status_suffix="renamed",
        )

    def action_device_playlist_remove_selected(self) -> None:
        pl = self._current_device_playlist
        if pl is None:
            return
        indices = self._selected_device_playlist_indices()
        if not indices:
            messagebox.showinfo(
                "Device Playlists", "Select track(s) to remove."
            )
            return
        new_ids = remove_ids_at_indices(self._device_playlist_item_ids, indices)
        self._write_current_device_playlist_tracks(
            new_ids, status_suffix="tracks removed"
        )

    def action_device_playlist_move_selected(self, delta: int) -> None:
        pl = self._current_device_playlist
        if pl is None:
            return
        indices = self._selected_device_playlist_indices()
        if not indices:
            messagebox.showinfo(
                "Device Playlists",
                "Select track(s) to reorder.",
            )
            return
        old_ids = list(self._device_playlist_item_ids)
        new_ids = move_ids_by_indices(old_ids, indices, delta=int(delta))
        if new_ids == old_ids:
            return
        # Keep selection on the moved item ids (by value at former indices).
        moved = [old_ids[i] for i in indices if 0 <= i < len(old_ids)]
        self._write_current_device_playlist_tracks(
            new_ids,
            select_item_ids=moved,
            status_suffix="order saved on device",
        )

    def action_device_playlist_shuffle(self, algorithm: str) -> None:
        pl = self._current_device_playlist
        if pl is None:
            messagebox.showinfo("Device Playlists", "Select a playlist first.")
            return
        ids = list(self._device_playlist_item_ids)
        if len(ids) < 2:
            messagebox.showinfo(
                "Device Playlists", "Need at least two tracks to shuffle."
            )
            return
        tracks = self._tracks_for_device_playlist_ids(ids)
        if len(tracks) < 2:
            return
        # Seed from focused/selected row when possible.
        seed: Track | None = None
        tree = self.win.device_playlist_tree
        try:
            focus = tree.focus()
        except Exception:
            focus = ""
        if focus:
            seed = self._device_playlist_track_by_iid.get(focus)
        if seed is None:
            try:
                sel = list(tree.selection())
            except Exception:
                sel = []
            for iid in sel:
                seed = self._device_playlist_track_by_iid.get(iid)
                if seed is not None:
                    break
        if seed is None:
            seed = tracks[0]
        algo = (algorithm or "").strip().lower()
        rng = rng_from_seed_track(seed, extra=algo)
        if algo in ("artist", "merge", "merge_shuffle", "merge_artist"):
            shuffled = merge_shuffle(tracks, rng=rng)
            label = "artist"
        elif algo in ("spotify", "spotify_shuffle", "dither"):
            shuffled = spotify_shuffle(tracks, rng=rng)
            label = "spotify"
        else:
            messagebox.showerror(
                "Device Playlists", f"Unknown shuffle algorithm: {algorithm!r}"
            )
            return
        new_ids: list[int] = []
        for t in shuffled:
            oid = self._item_id_from_device_track(t)
            if oid:
                new_ids.append(oid)
        if not new_ids:
            return
        seed_oid = self._item_id_from_device_track(seed)
        self._write_current_device_playlist_tracks(
            new_ids,
            select_item_ids=[seed_oid] if seed_oid else None,
            status_suffix=f"{label} shuffle saved on device",
        )

    def action_device_add_selected_to_playlist(self) -> None:
        """Context: add selected on-device tracks to an on-device playlist."""
        ok, msg = self._device_playlists_ready()
        if not ok:
            messagebox.showinfo("Device Playlists", msg)
            return
        if self._transfer_busy or self._device_playlist_mutate_inflight:
            messagebox.showinfo(
                "Device Playlists",
                "Wait for the current device operation to finish.",
            )
            return

        tree = getattr(self, "_device_context_tree", None)
        tracks = self._device_tracks_from_tree_selection(tree)
        item_ids: list[int] = []
        seen: set[int] = set()
        for t in tracks:
            oid = self._item_id_from_device_track(t)
            if oid and oid not in seen:
                seen.add(oid)
                item_ids.append(oid)
        if not item_ids:
            messagebox.showinfo(
                "Device Playlists",
                "No on-device object ids in the selection.\n\n"
                "Select track rows on Device → Music / Video / "
                "Audiobooks / Podcasts (not empty groups).",
            )
            return

        if not self._ensure_device_playlists_for_dialog():
            return

        from types import SimpleNamespace

        def list_for_dialog():
            out = []
            for pl in self._device_playlists:
                out.append(
                    SimpleNamespace(
                        id=int(pl.playlist_id),
                        name=playlist_display_name(
                            pl.name or "", int(pl.playlist_id)
                        ),
                        track_count=len(pl.track_ids or ()),
                    )
                )
            out.sort(key=lambda x: (str(x.name or "").casefold(), int(x.id)))
            return out

        def create_for_dialog(name: str):
            clean = (name or "").strip()
            if not clean:
                raise ValueError("Playlist name is required")
            # Case-insensitive collision with existing device playlists.
            if clean.casefold() in {
                playlist_display_name(p.name or "", p.playlist_id).casefold()
                for p in self._device_playlists
            }:
                raise ValueError(
                    f"A playlist named “{clean}” already exists on the device."
                )
            parent = playlists_parent_id(self._folder_layout)
            storage = DEFAULT_STORAGE_ID
            if not self._device_io.try_acquire("device-playlist-create"):
                raise RuntimeError(
                    f"Device is busy ({self._device_io.holder or 'unknown'})."
                )
            try:
                new_id = int(
                    self.device.create_playlist(
                        clean,
                        [],
                        parent_id=parent,
                        storage_id=storage,
                    )
                )
            finally:
                self._device_io.release(
                    reason="device-playlist-create",
                    quiet_s=_DEVICE_USB_COOLDOWN_S,
                )
            if new_id <= 0:
                raise RuntimeError("Device returned no playlist id")
            pl = DevicePlaylist(
                playlist_id=new_id,
                name=clean,
                parent_id=parent,
                storage_id=storage,
                track_ids=(),
            )
            self._device_playlists.append(pl)
            return SimpleNamespace(id=new_id, name=clean, track_count=0)

        def delete_for_dialog(pid: int) -> bool:
            oid = int(pid or 0)
            if oid <= 0:
                return False
            if not self._device_io.try_acquire("device-playlist-delete"):
                raise RuntimeError(
                    f"Device is busy ({self._device_io.holder or 'unknown'})."
                )
            try:
                self.device.delete_object(oid)
            finally:
                self._device_io.release(
                    reason="device-playlist-delete",
                    quiet_s=_DEVICE_USB_COOLDOWN_S,
                )
            self._device_playlists = [
                p for p in self._device_playlists if int(p.playlist_id) != oid
            ]
            return True

        result = ask_add_to_playlist(
            self.win.root,
            candidate_tracks=tracks,
            list_playlists=list_for_dialog,
            create_playlist=create_for_dialog,
            delete_playlist=delete_for_dialog,
        )
        if result is None:
            return
        if result.playlists_changed:
            # Keep Device → Playlists tab combo in sync after create/delete.
            self._apply_device_playlist_list(
                self._device_playlists, prefer_name=""
            )
        if result.playlist_id < 0:
            return

        pl = next(
            (
                p
                for p in self._device_playlists
                if int(p.playlist_id) == int(result.playlist_id)
            ),
            None,
        )
        if pl is None:
            messagebox.showwarning(
                "Device Playlists",
                "Selected playlist is no longer available. Refresh and try again.",
            )
            return

        existing_ids = [int(x) for x in (pl.track_ids or ()) if int(x) > 0]
        merged, added, skipped = append_ids_to_order(
            existing_ids,
            item_ids,
            skip_existing=bool(result.skip_existing),
        )
        if added == 0:
            messagebox.showinfo(
                "Device Playlists",
                f"All selected tracks are already in “{result.playlist_name}”.",
            )
            return

        write_name = (pl.name or "").strip() or playlist_display_name(
            pl.name or "", int(pl.playlist_id)
        )
        parent = int(pl.parent_id or 0) or playlists_parent_id(
            self._folder_layout
        )
        storage = int(pl.storage_id or 0) or DEFAULT_STORAGE_ID
        pid = int(pl.playlist_id)
        pl_label = result.playlist_name or write_name

        def work():
            return self.device.update_playlist(
                pid,
                write_name,
                merged,
                parent_id=parent,
                storage_id=storage,
            )

        def on_success(_result) -> None:
            updated = DevicePlaylist(
                playlist_id=pid,
                name=write_name,
                parent_id=parent,
                storage_id=storage,
                track_ids=tuple(merged),
            )
            for i, p in enumerate(self._device_playlists):
                if int(p.playlist_id) == pid:
                    self._device_playlists[i] = updated
                    break
            else:
                self._device_playlists.append(updated)
            prefer = self._device_playlist_label_by_id.get(
                pid
            ) or playlist_display_name(write_name, pid)
            self._apply_device_playlist_list(
                self._device_playlists, prefer_name=prefer
            )
            status = (
                f"Added {added} track(s) to device playlist “{pl_label}”"
                + (f" ({skipped} already present)" if skipped else "")
            )
            try:
                self.win.set_progress_status(status)
                self.win.lbl_device_playlist_status.configure(text=status)
            except Exception:
                pass
            logger.info(
                "Device playlist add name=%r id=%s added=%d skipped=%d "
                "candidates=%d total=%d",
                pl_label,
                pid,
                added,
                skipped,
                len(item_ids),
                len(merged),
            )

        self._run_device_playlist_mutation(
            name="device-playlist-add-tracks",
            work=work,
            on_success=on_success,
            status=f"Adding {added} track(s) to “{pl_label}”…",
            error_title="Device Playlists",
        )

    def _ensure_device_playlists_for_dialog(self) -> bool:
        """Load on-device playlists into memory if the tab cache is empty.

        Blocks briefly on the UI thread under the device I/O gate so the
        Add dialog has a list to show. Returns False if not ready / failed.
        """
        if self._device_playlists:
            return True
        ok, msg = self._device_playlists_ready()
        if not ok:
            messagebox.showinfo("Device Playlists", msg)
            return False
        if self._device_playlist_load_inflight:
            messagebox.showinfo(
                "Device Playlists",
                "Still loading playlists — try again in a moment.",
            )
            return False
        if not self._device_io.try_acquire("device-playlists-list"):
            messagebox.showinfo(
                "Device Playlists",
                f"Device is busy ({self._device_io.holder or 'unknown'}).",
            )
            return False
        serial = self._device_serial or ""
        parent = playlists_parent_id(self._folder_layout)
        try:
            self.win.set_progress_status("Listing on-device playlists…")
            candidates: list[int] = []
            names: dict[int, str] = {}
            if serial:
                try:
                    files = list_cached_files(serial)
                    for e in playlist_candidates_from_files(
                        files,
                        playlist_parent_ids={int(parent)} if parent else None,
                    ):
                        oid = int(e.item_id or 0)
                        if oid > 0:
                            candidates.append(oid)
                            names[oid] = str(e.name or "")
                except Exception:
                    logger.debug(
                        "dialog playlist candidates failed", exc_info=True
                    )
            lister = getattr(self.device, "list_playlists_complete", None)
            if callable(lister):
                playlists = list(
                    lister(
                        candidate_ids=candidates,
                        candidate_names=names,
                    )
                    or []
                )
            else:
                playlists = list(self.device.list_playlists() or [])
            self._apply_device_playlist_list(playlists, prefer_name="")
            return True
        except Exception as e:
            logger.warning("ensure device playlists failed: %s", e, exc_info=True)
            messagebox.showerror(
                "Device Playlists",
                f"Could not list on-device playlists:\n\n{e}",
            )
            return False
        finally:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            self._device_io.release(
                reason="device-playlists-list", quiet_s=_DEVICE_USB_COOLDOWN_S
            )

    def action_recreate_device_playlist_locally(self) -> None:
        """Build a host M3U from the selected on-device playlist membership.

        Resolution: object id → device_index name/GUID → library tracks or
        podcast_episodes. Unresolved rows are kept as synthetic paths (greyed
        out in the host Playlists tab). Optional Get_Trackmetadata for
        unresolved members (auto if ≤5, else confirm — bulk metadata can
        poison the ZEN session).
        """
        pl = self._current_device_playlist
        if pl is None or int(pl.playlist_id or 0) <= 0:
            messagebox.showinfo(
                "Recreate playlist",
                "Select an on-device playlist first.",
            )
            return
        serial = self._device_serial or ""
        if not serial:
            messagebox.showinfo(
                "Recreate playlist",
                "Connect the device so the device index is available.",
            )
            return
        item_ids = [
            int(x)
            for x in (
                self._device_playlist_item_ids
                or list(pl.track_ids or ())
            )
            if int(x) > 0
        ]
        if not item_ids:
            messagebox.showinfo(
                "Recreate playlist",
                "This on-device playlist has no track members.",
            )
            return

        name = playlist_display_name(pl.name or "", int(pl.playlist_id))
        resolved = resolve_device_playlist_to_host_tracks(serial, item_ids)
        unresolved = list(resolved.unresolved_item_ids)
        n_unres = len(unresolved)

        def finish(final_resolved, *, metadata_fetched: int = 0) -> None:
            tracks = list(final_resolved.tracks)
            if not tracks:
                messagebox.showinfo(
                    "Recreate playlist",
                    "No tracks to write into a host playlist.",
                )
                return
            existing = None
            try:
                existing = get_playlist_by_name(name)
            except Exception:
                existing = None
            if existing is not None:
                if not messagebox.askyesno(
                    "Recreate playlist",
                    f"A local playlist named “{name}” already exists.\n\n"
                    f"Replace its membership with the {len(tracks)} track(s) "
                    "from the device playlist?\n\n"
                    f"Resolved: {final_resolved.resolved} · "
                    f"unresolved (kept, greyed out): "
                    f"{len(final_resolved.unresolved_item_ids)}",
                ):
                    return
            try:
                result = save_resolved_tracks_as_host_playlist(
                    name,
                    tracks,
                    replace_existing=True,
                )
            except Exception as e:
                messagebox.showerror(
                    "Recreate playlist",
                    f"Could not write the host playlist:\n\n{e}",
                )
                return
            verb = "Created" if result.created else "Replaced"
            status = (
                f"{verb} local playlist “{result.name}” "
                f"({result.resolved} resolved, {result.unresolved} unresolved)"
            )
            if metadata_fetched:
                status += f" · metadata fetched for {metadata_fetched}"
            try:
                self.win.set_progress_status(status)
                self.win.lbl_device_playlist_status.configure(text=status)
            except Exception:
                pass
            logger.info(
                "Recreate device playlist locally name=%r id=%s resolved=%d "
                "unresolved=%d meta=%d created=%s",
                result.name,
                result.playlist_id,
                result.resolved,
                result.unresolved,
                metadata_fetched,
                result.created,
            )
            self._refresh_playlist_tab(keep_selection=False)
            try:
                self.win.var_playlist_choice.set(result.name)
                self._load_playlist_by_name(result.name)
                self.win.show_playlists_tab()
            except Exception:
                pass
            try:
                self.win.lbl_playlist_status.configure(text=status)
            except Exception:
                pass

        if n_unres == 0:
            finish(resolved, metadata_fetched=0)
            return

        # Metadata for unresolved members (tags / possible GUID in ObjectFileName).
        fetch = False
        if n_unres <= RECREATE_METADATA_AUTO_MAX:
            fetch = True
            logger.info(
                "Recreate local: auto Get_Trackmetadata for %d unresolved "
                "(threshold=%d)",
                n_unres,
                RECREATE_METADATA_AUTO_MAX,
            )
        else:
            fetch = messagebox.askyesno(
                "Recreate playlist",
                f"{n_unres} of {len(item_ids)} track(s) could not be matched "
                "to the library or podcast index by GUID.\n\n"
                f"Fetch on-device tags (Get_Trackmetadata) for those "
                f"{n_unres} items?\n\n"
                "Many back-to-back metadata requests can crash or poison the "
                "USB sync session on Creative ZEN. Prefer small batches, or "
                "skip to keep unresolved rows greyed out in the local "
                "playlist.\n\n"
                f"(Auto-fetch without asking when ≤{RECREATE_METADATA_AUTO_MAX} "
                "unresolved.)",
                default=messagebox.NO,
            )

        if not fetch:
            finish(resolved, metadata_fetched=0)
            return

        if not self.device.is_connected():
            messagebox.showinfo(
                "Recreate playlist",
                "Device is not connected — saving without metadata fetch.\n"
                "Unresolved tracks will appear greyed out.",
            )
            finish(resolved, metadata_fetched=0)
            return
        if self._transfer_busy or self._device_tag_enrich_inflight:
            messagebox.showinfo(
                "Recreate playlist",
                "Device is busy — saving without metadata fetch.",
            )
            finish(resolved, metadata_fetched=0)
            return
        if not self._device_io.try_acquire("playlist-recreate-meta"):
            messagebox.showinfo(
                "Recreate playlist",
                f"Device is busy ({self._device_io.holder or 'unknown'}) — "
                "saving without metadata fetch.",
            )
            finish(resolved, metadata_fetched=0)
            return

        refs = [
            DeviceTrackRef(
                item_id=oid,
                name=str(resolved.names_by_item_id.get(oid) or f"id={oid}"),
            )
            for oid in unresolved
        ]
        gate_reason = "playlist-recreate-meta"
        try:
            self.win.set_progress_status(
                f"Fetching tags for {n_unres} unresolved track(s)…"
            )
            self.win.lbl_device_playlist_status.configure(
                text=f"Get_Trackmetadata × {n_unres}…"
            )
        except Exception:
            pass

        def work():
            return device_ops.enrich_track_refs(
                self.device, refs, stop_on_fatal=True
            )

        def on_done(result) -> None:
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            infos: dict[int, object] = {}
            # enrich returns refs with tags applied; rebuild DeviceTrackInfo-like
            # mapping from refs for apply_metadata_infos.
            from mtpmanager.domain.models import DeviceTrackInfo

            for ref in result.refs or []:
                oid = int(getattr(ref, "item_id", 0) or 0)
                if oid <= 0:
                    continue
                infos[oid] = DeviceTrackInfo(
                    item_id=oid,
                    name=str(getattr(ref, "name", "") or ""),
                    title=str(getattr(ref, "title", "") or ""),
                    artist=str(getattr(ref, "artist", "") or ""),
                    album=str(getattr(ref, "album", "") or ""),
                    date=str(getattr(ref, "date", "") or ""),
                    genre=str(getattr(ref, "genre", "") or ""),
                )
            improved = apply_metadata_infos_to_resolved_tracks(
                resolved, infos, serial=serial
            )
            finish(improved, metadata_fetched=int(result.updated or 0))

        def on_error(exc: BaseException) -> None:
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.warning(
                "Recreate local metadata fetch failed: %s", exc, exc_info=True
            )
            if messagebox.askyesno(
                "Recreate playlist",
                "Fetching on-device tags failed or was interrupted:\n\n"
                f"{exc}\n\n"
                "Save the local playlist anyway with unresolved tracks "
                "greyed out?",
                default=messagebox.YES,
            ):
                finish(resolved, metadata_fetched=0)

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="playlist-recreate-meta",
        )

    def action_device_playlist_play_selected(self) -> None:
        """Play host copies of selected device-playlist rows when available."""
        try:
            sel = list(self.win.device_playlist_tree.selection())
        except Exception:
            sel = []
        tracks: list[Track] = []
        for iid in sel:
            t = self._device_playlist_track_by_iid.get(iid)
            if t is not None:
                tracks.append(t)
        if not tracks and self._current_device_playlist is not None:
            tracks = self._tracks_for_device_playlist_ids(
                list(self._device_playlist_item_ids)
            )
        playable: list[Track] = []
        for t in tracks:
            host = self._host_path_for_device_track(t)
            if host and os.path.isfile(host):
                playable.append(
                    Track(path=host, meta=t.meta, guid=t.guid or "")
                )
        if not playable:
            messagebox.showinfo(
                "Playback",
                "No host library copies found for the selected tracks.\n\n"
                "Playback uses files on this computer (matched by GUID). "
                "Pull tracks from the device or keep the library path scanned.",
            )
            return
        self._start_playback_queue(self._audio_tracks_only(playable))

    def _should_push_album_art(self) -> bool:
        """True when Experimental + config wants album art after music sync."""
        if not bool(getattr(self._config, "sync_album_art", True)):
            return False
        if self.win.active_mode() != "experimental":
            return False
        return True

    def _publish_album_art_after_sync(self, paths: list[str]) -> None:
        """After successful music transfer: create/update albums + JPEG samples."""
        if not paths:
            return
        if not self._should_push_album_art():
            return
        if not self.device.is_connected():
            logger.info(
                "Album art: skip (device not connected after transfer)"
            )
            return
        tracks = self._tracks_for_paths(list(paths))
        if not tracks:
            return
        serial = self._device_serial or device_serial_key()

        def work():
            if not self._device_io.try_acquire("album-art-push"):
                raise RuntimeError(
                    f"Device is busy ({self._device_io.holder or 'unknown'})."
                )
            try:
                return push_album_art_for_tracks(
                    device=self.device,
                    serial=serial,
                    tracks=tracks,
                )
            finally:
                self._device_io.release(
                    reason="album-art-push", quiet_s=_DEVICE_USB_COOLDOWN_S
                )

        def on_done(result) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            if result is None:
                return
            sent = int(getattr(result, "art_sent_count", 0) or 0)
            ok_n = int(getattr(result, "ok_count", 0) or 0)
            err_n = int(getattr(result, "error_count", 0) or 0)
            logger.info(
                "Album art push done sent=%s ok=%s errors=%s",
                sent,
                ok_n,
                err_n,
            )
            if sent > 0:
                try:
                    self.win.set_progress_status(
                        f"Album art: {sent} cover(s) on device"
                    )
                except Exception:
                    pass

        def on_error(exc: BaseException) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            # Non-fatal for the transfer that already succeeded.
            logger.warning("Album art push failed: %s", exc, exc_info=True)

        try:
            self.win.set_progress_status("Syncing album art to device…")
        except Exception:
            pass
        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="device-album-art",
        )

    def _publish_pending_device_playlist(self) -> None:
        """After a successful playlist track sync, create/update MTP playlist."""
        pending = self._pending_device_playlist
        self._pending_device_playlist = None
        if not pending or not pending.get("publish"):
            return
        if self.win.active_mode() != "experimental":
            return
        if not self.device.is_connected():
            messagebox.showwarning(
                "Playlist",
                "Tracks transferred, but the device is not connected for "
                "on-device playlist creation.\n\n"
                "Connect in Experimental mode and Sync playlist again.",
            )
            return
        name = str(pending.get("name") or "playlist")
        guids = list(pending.get("guids") or [])
        serial = self._device_serial or device_serial_key()

        def work() -> object:
            # Prefer real object ids; refresh listing if any GUID is unresolved.
            _ids, missing = resolve_track_object_ids(serial, guids)
            if missing:
                logger.info(
                    "Playlist publish: %d GUID(s) lack real item_id — "
                    "refreshing device file list",
                    len(missing),
                )
                try:
                    self.win.root.after(
                        0,
                        lambda: self.win.set_progress_status(
                            "Refreshing device index for playlist…"
                        ),
                    )
                except Exception:
                    pass
                try:
                    files = self.device.list_files()
                    replace_device_listing(serial, files, source="list")
                except Exception:
                    logger.warning(
                        "Playlist publish: list_files refresh failed",
                        exc_info=True,
                    )
            parent = playlists_parent_id(self._folder_layout)
            return push_playlist_to_device(
                device=self.device,
                serial=serial,
                name=name,
                guids_in_order=guids,
                parent_id=parent,
            )

        def on_done(result) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            if result is None:
                return
            verb = "Created" if result.created else "Updated"
            extra = ""
            if result.missing_guid:
                extra = (
                    f"\n\n({result.missing_guid} track(s) omitted — "
                    "no on-device object id yet.)"
                )
            messagebox.showinfo(
                "Playlist on device",
                f"{verb} on-device playlist “{result.name}” "
                f"with {result.resolved} track(s).{extra}",
            )
            logger.info(
                "Device playlist publish done id=%s created=%s tracks=%d",
                result.playlist_id,
                result.created,
                result.resolved,
            )
            # Refresh Device → Playlists so the new/updated object appears.
            prefer = str(getattr(result, "name", "") or "")
            if prefer:
                try:
                    self.win.var_device_playlist_choice.set(prefer)
                except Exception:
                    pass
            self.win.root.after(
                100,
                lambda: self._refresh_device_playlists_tab(keep_selection=True),
            )

        def on_error(exc: BaseException) -> None:
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.exception("Device playlist publish failed")
            messagebox.showerror(
                "Playlist on device",
                "Tracks may have transferred, but creating/updating the "
                f"on-device playlist failed:\n\n{exc}",
            )

        try:
            self.win.set_progress_status(
                f"Creating on-device playlist “{name}”…"
            )
        except Exception:
            pass
        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="device-playlist-publish",
        )

    def action_add_selected_to_playlist(self) -> None:
        """Add expanded multi-selection (tracks + artist/album headers) to a playlist."""
        self._open_add_to_playlist(self._tracks_for_playlist_add())

    def action_add_artist_to_playlist(self) -> None:
        """Group menu entry — same mixed-selection expansion as track menu."""
        self.action_add_selected_to_playlist()

    def action_add_album_to_playlist(self) -> None:
        """Group menu entry — same mixed-selection expansion as track menu."""
        self.action_add_selected_to_playlist()

    def _tracks_for_playlist_add(self) -> list[Track]:
        """Resolve selected library rows to audio tracks for playlist add.

        Expands artist/album/folder headers via tree children (tree order),
        dedupes by path, and falls back to library filters when a group has
        no children yet. Mixed selections (tracks + groups) are supported.
        """
        iids = list(self.win.selected_tree_iids() or [])
        if not iids:
            focus = self.win.selected_tree_iid() or ""
            if focus:
                iids = [focus]
        tracks = self._audio_tracks_only(
            self._tracks_from_iids_tree_order(iids)
        )
        if tracks:
            return tracks

        # Fallback: expand group seeds when tree children are unavailable.
        seen: set[str] = set()
        out: list[Track] = []
        tree = self.win.active_library_tree()
        for iid in iids:
            seed = self._group_seed_by_iid.get(iid)
            if seed is None:
                continue
            tags: set[str] = set()
            try:
                tags = set(tree.item(iid, "tags") or ())
            except Exception:
                pass
            if "group_artist" in tags:
                batch = self.library.filter_by_artist(seed)
            elif "group_directory" in tags:
                batch = self.library.filter_by_directory(seed)
            elif "group_album" in tags:
                batch = self.library.filter_by_album(seed)
            else:
                continue
            for t in self._audio_tracks_only(batch):
                if t.path and t.path not in seen:
                    seen.add(t.path)
                    out.append(t)
        return out

    def _open_add_to_playlist(self, tracks: list[Track]) -> None:
        if not tracks:
            messagebox.showinfo(
                "Playlist",
                "No playable audio files in the selection.",
            )
            return
        result = ask_add_to_playlist(
            self.win.root,
            candidate_tracks=tracks,
            list_playlists=list_playlists,
            create_playlist=create_playlist,
            delete_playlist=delete_playlist,
        )
        if result is None:
            return
        if result.playlists_changed:
            self._refresh_playlist_tab()
        if result.playlist_id < 0:
            return
        try:
            before = get_playlist(result.playlist_id)
            before_n = len(before.entries()) if before else 0
            pl = append_tracks_to_playlist(
                result.playlist_id,
                tracks,
                skip_existing=result.skip_existing,
            )
            added_n = max(0, len(pl.entries()) - before_n)
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not add tracks:\n{e}")
            return
        # No success dialog — status bar / playlist tab only (faster build flow).
        skipped = max(0, len(tracks) - added_n)
        status = (
            f"Added {added_n} track(s) to “{result.playlist_name}”"
            + (f" ({skipped} already present)" if skipped else "")
        )
        try:
            self.win.set_progress_status(status)
        except Exception:
            pass
        try:
            self.win.lbl_playlist_status.configure(text=status)
        except Exception:
            pass
        logger.info(
            "Playlist add name=%r added=%d skipped=%d candidates=%d",
            result.playlist_name,
            added_n,
            skipped,
            len(tracks),
        )
        # Refresh tab if that playlist is open (or always keep dropdown current).
        self._refresh_playlist_tab()
        if result.playlist_name:
            try:
                self.win.var_playlist_choice.set(result.playlist_name)
                self._load_playlist_by_name(result.playlist_name)
            except Exception:
                pass

    def _sync_from_seed(self, seed: Track | None, *, kind: str) -> None:
        """Run filter_by_artist / filter_by_album / directory from a seed track."""
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
            label = f"Artist: {primary_artist(seed)}"
            job_kind = "artist"
        elif kind == "directory":
            matches = self.library.filter_by_directory(seed)
            matches.sort(key=lambda t: t.path)
            folder = os.path.basename(
                (os.path.dirname(seed.path) or seed.path).rstrip(os.sep + "/")
            ) or "folder"
            logger.info("Directory %s: %d tracks", folder, len(matches))
            label = f"Folder: {folder}"
            job_kind = "album"
        else:
            matches = self.library.filter_by_album(seed)
            matches.sort(key=lambda t: t.path)
            logger.info(
                "Album %s: %d tracks",
                seed.meta.album,
                len(matches),
            )
            label = f"Album: {seed.meta.album or 'Unknown Album'}"
            job_kind = "album"
        videos = [t for t in matches if is_video_track(t)]
        audio = self._audio_tracks_only(matches)
        if videos and not audio:
            self._start_send_video([t.path for t in videos])
            return
        if not audio:
            messagebox.showinfo("Sync", "No matching tracks found.")
            return
        if videos:
            logger.info(
                "Sync group: %d video(s) skipped (use Video tab / Send Video)",
                len(videos),
            )
        self._transfer_many(
            audio,
            self._target_format(),
            kind=job_kind,
            label=label,
        )

    def on_sort_heading(self, col: str) -> None:
        """Column heading click: set primary sort (toggle reverse if same).

        - **Default:** ``{artist} - {album}`` (Artist-column option 3)
        - **Artist** cycles four modes (see :func:`next_artist_column_sort`):
          1. Artist→Album→Track A–Z
          2. Same, Z–A
          3. ``{artist} - {album}``→Track (VA algorithm; **startup default**)
          4. Same as 3, reverse
        - Album: ``{album} - {artist}`` → Track
        - Title / #0: flat title order
        - Year: year groups
        """
        if col == "artist":
            self._sort_primary, self._sort_reverse = next_artist_column_sort(
                self._sort_primary,
                self._sort_reverse,
            )
        else:
            mapping = {
                "#0": SortPrimary.TITLE,  # track # column → title-like flat order
                "title": SortPrimary.TITLE,
                "album": SortPrimary.ALBUM,
                "year": SortPrimary.YEAR,
            }
            primary = mapping.get(col, SortPrimary.ARTIST_ALBUM_COMBO)
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

    def _cancel_audiobooks_populate(self) -> None:
        if self._audiobooks_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._audiobooks_populate_after_id)
            except Exception:
                pass
            self._audiobooks_populate_after_id = None

    def _cancel_videos_populate(self) -> None:
        if self._videos_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._videos_populate_after_id)
            except Exception:
                pass
            self._videos_populate_after_id = None

    def _cancel_device_populate(self) -> None:
        if self._device_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_populate_after_id)
            except Exception:
                pass
            self._device_populate_after_id = None

    def _cancel_device_video_populate(self) -> None:
        if self._device_video_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_video_populate_after_id)
            except Exception:
                pass
            self._device_video_populate_after_id = None

    def _cancel_device_audiobook_populate(self) -> None:
        if self._device_audiobook_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_audiobook_populate_after_id)
            except Exception:
                pass
            self._device_audiobook_populate_after_id = None

    def _cancel_device_podcast_populate(self) -> None:
        if self._device_podcast_populate_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_podcast_populate_after_id)
            except Exception:
                pass
            self._device_podcast_populate_after_id = None

    def _track_iid(self, track: Track) -> str:
        # Paths are unique; avoid characters Treeview rejects in iids.
        return "t:" + track.path.replace("\\", "/")

    def _device_track_iid(self, track: Track) -> str:
        return "d:" + track.path.replace("\\", "/")

    def _device_video_track_iid(self, track: Track) -> str:
        return "dv:" + track.path.replace("\\", "/")

    def _device_audiobook_track_iid(self, track: Track) -> str:
        return "dab:" + track.path.replace("\\", "/")

    def _device_podcast_track_iid(self, track: Track) -> str:
        return "dp:" + track.path.replace("\\", "/")

    def _podcast_parent_ids(self) -> frozenset[int]:
        """ZENcast root + experimental show-folder descendants."""
        layout = self._folder_layout_or_legacy()
        return expand_podcast_parent_ids(
            layout.podcast_id,
            self._folder_parent_by_id or None,
        )

    @staticmethod
    def _host_path_for_device_track(track: Track) -> str:
        """Extract a real host filesystem path from a synthetic device tree path.

        GUID-resolved rows use ``device:<item_id>:<host_path>``. Basename-only
        rows (filename fallback) return empty so album-art is skipped.
        """
        path = track.path or ""
        if not path.startswith("device:"):
            return path if path and not path.startswith("device:") else ""
        parts = path.split(":", 2)
        if len(parts) < 3:
            return ""
        candidate = parts[2]
        # Unix absolute or Windows drive path.
        if candidate.startswith("/"):
            return candidate
        if len(candidate) >= 3 and candidate[1] == ":" and candidate[0].isalpha():
            return candidate
        return ""

    def _search_score_tree_text(self, track: Track, default: str = "") -> str:
        """#0 cell text: fuzzy score in debug mode when a search is active."""
        from mtpmanager.infra.logging_setup import debug_ui_enabled

        if not debug_ui_enabled() or not self._active_search_scores:
            return default
        s = self._active_search_scores.get(track.path or "")
        if s is None:
            return default
        return f"{float(s):.2f}"

    def _set_library_tree_score_headers(self, *, show_scores: bool) -> None:
        """Toggle #0 heading between track # and search score (debug)."""
        label = "score" if show_scores else "#"
        for tree in (
            self.win.tree,
            self.win.audiobooks_tree,
            self.win.videos_tree,
        ):
            try:
                tree.heading("#0", text=label if tree is not self.win.videos_tree else (
                    "score" if show_scores else ""
                ))
            except Exception:
                pass

    def _insert_track_row(
        self,
        parent: str,
        track: Track,
        *,
        tree=None,
        extra_tags: tuple[str, ...] = (),
    ) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._track_iid(track)
        target = tree if tree is not None else self.win.tree
        # Avoid duplicate iids if path appears twice
        if target.exists(iid):
            iid = f"{iid}#{id(track)}"
        tags = ("track",) + tuple(extra_tags)
        target.insert(
            parent,
            "end",
            iid=iid,
            text=self._search_score_tree_text(track, num),
            values=(title, artist, album, year),
            tags=tags,
            open=False,
        )
        self._track_by_iid[iid] = track
        self._iid_by_path[track.path] = iid

    def _insert_video_row(self, parent: str, track: Track) -> None:
        """Library Video tab: single Title column showing the filename."""
        iid = self._track_iid(track)
        tree = self.win.videos_tree
        if tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        tree.insert(
            parent,
            "end",
            iid=iid,
            text=self._search_score_tree_text(track, ""),
            values=(video_display_title(track),),
            tags=("track", "video"),
            open=False,
        )
        self._track_by_iid[iid] = track
        self._iid_by_path[track.path] = iid

    def _insert_device_track_row(self, parent: str, track: Track) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._device_track_iid(track)
        if self.win.device_tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        self.win.device_tree.insert(
            parent,
            "end",
            iid=iid,
            text=num,
            values=(title, artist, album, year),
            tags=("track",),
            open=False,
        )
        self._device_track_by_iid[iid] = track

    def _insert_device_video_row(self, parent: str, track: Track) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._device_video_track_iid(track)
        if self.win.device_video_tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        self.win.device_video_tree.insert(
            parent,
            "end",
            iid=iid,
            text=num,
            values=(title, artist, album, year),
            tags=("track", "video"),
            open=False,
        )
        self._device_video_track_by_iid[iid] = track

    def _insert_device_podcast_row(self, parent: str, track: Track) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._device_podcast_track_iid(track)
        if self.win.device_podcasts_tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        tags: tuple[str, ...] = ("track", "podcast")
        # Video containers under ZENcast get the same teal hint as host tab.
        from mtpmanager.domain.library import is_video_file

        obj_name = ""
        oid = self._item_id_from_device_track(track)
        if oid is not None:
            ref = self._device_refs_by_item_id().get(oid)
            if ref is not None:
                obj_name = (ref.name or "").strip()
        if not obj_name:
            parts = (track.path or "").split(":", 2)
            if len(parts) >= 3:
                obj_name = os.path.basename(parts[2])
        if is_video_file(obj_name):
            tags = tags + ("video_episode",)
        self.win.device_podcasts_tree.insert(
            parent,
            "end",
            iid=iid,
            text=num,
            values=(title, artist, album, year),
            tags=tags,
            open=False,
        )
        self._device_podcast_track_by_iid[iid] = track

    def _insert_device_audiobook_row(self, parent: str, track: Track) -> None:
        num, title, artist, album, year = iter_track_cells(track)
        iid = self._device_audiobook_track_iid(track)
        if self.win.device_audiobooks_tree.exists(iid):
            iid = f"{iid}#{id(track)}"
        self.win.device_audiobooks_tree.insert(
            parent,
            "end",
            iid=iid,
            text=num,
            values=(title, artist, album, year),
            tags=("track", "audiobook"),
            open=False,
        )
        self._device_audiobook_track_by_iid[iid] = track

    def on_library_search_changed(self) -> None:
        """Toolbar search typed — debounce rebuild (avoid per-keystroke thrash)."""
        q = self.win.library_search_query()
        self.win.set_library_search_clear_enabled(bool(q.strip()))
        if self._library_search_after_id is not None:
            try:
                self.win.root.after_cancel(self._library_search_after_id)
            except Exception:
                pass
            self._library_search_after_id = None
        self._library_search_after_id = self.win.root.after(
            200, self._apply_library_search
        )

    def on_library_search_clear(self) -> None:
        """Clear toolbar search (button or Escape in the entry)."""
        if self._library_search_after_id is not None:
            try:
                self.win.root.after_cancel(self._library_search_after_id)
            except Exception:
                pass
            self._library_search_after_id = None
        if not self.win.library_search_query() and not self._library_search_query:
            self.win.set_library_search_clear_enabled(False)
            return
        self.win.set_library_search_query("")
        self.win.set_library_search_clear_enabled(False)
        self._library_search_query = ""
        if not self._library_busy:
            self._rebuild_track_tree()
            self._sync_library_chrome()

    def _apply_library_search(self) -> None:
        self._library_search_after_id = None
        from mtpmanager.domain.library_search import normalize_search_text

        q = self.win.library_search_query()
        norm = normalize_search_text(q)
        prev = normalize_search_text(self._library_search_query)
        if norm == prev:
            self._library_search_query = q
            return
        self._library_search_query = q
        if self._library_busy or self._index_stream_active:
            return
        self._rebuild_track_tree()
        self._sync_library_chrome()

    def _rebuild_track_tree(self) -> None:
        """Rebuild Music + Video + Audiobooks trees from library."""
        self._cancel_populate()
        self._cancel_videos_populate()
        self._cancel_audiobooks_populate()
        self.win.clear_track_tree()
        self.win.clear_videos_tree()
        self.win.clear_audiobooks_tree()
        self._track_by_iid.clear()
        self._iid_by_path.clear()
        self._group_seed_by_iid.clear()
        self._context_group_seed = None
        self._pending_album_art = []
        # Tree selection is gone; keep startup hint, else clear context label.
        self._refresh_selection_detail([])
        all_tracks = list(self.library.tracks)
        query = (self._library_search_query or self.win.library_search_query() or "")
        self._library_search_query = query
        search_scores: dict[str, float] = {}
        filter_active = bool(query.strip())
        if filter_active:
            from mtpmanager.domain.library_search import filter_library_tracks_scored

            all_tracks, search_scores = filter_library_tracks_scored(
                all_tracks, query
            )
            self._library_filter_shown_count = len(all_tracks)
            self._active_search_scores = dict(search_scores)
        else:
            self._library_filter_shown_count = None
            self._active_search_scores = {}
        from mtpmanager.infra.logging_setup import debug_ui_enabled

        show_score_col = filter_active and debug_ui_enabled() and bool(
            self._active_search_scores
        )
        self._set_library_tree_score_headers(show_scores=show_score_col)
        if not all_tracks:
            self.win.set_tracks_usable(self._library_root_reachable())
            self._sync_library_chrome()
            return

        music_tracks, video_tracks, audiobook_tracks = partition_library_media(
            all_tracks
        )
        # Fixed hierarchies for secondary tabs (independent of Music sort).
        # Search mode: flat relevance lists (no headers) on all media tabs.
        self._rebuild_videos_tree(
            video_tracks, search_scores=search_scores, flat_search=filter_active
        )
        self._rebuild_audiobooks_tree(
            audiobook_tracks,
            search_scores=search_scores,
            flat_search=filter_active,
        )

        tracks = music_tracks
        if not tracks:
            self.win.set_tracks_usable(self._library_root_reachable())
            self._refresh_playing_highlight()
            return

        primary = self._sort_primary
        reverse = self._sort_reverse

        # Build insert plan as list of ops for chunked UI work.
        # group op: ("group", parent, iid, label, tags, seed_track|None)
        # track op: ("track", parent, track)
        ops: list = []

        if filter_active:
            # Flat list, strongest matches first (filter_library_tracks_scored order).
            for t in tracks:
                ops.append(("track", "", t))
        elif primary == SortPrimary.DIRECTORY:
            groups = group_by_directory(tracks)
            if reverse:
                groups = list(reversed(groups))
            for g in groups:
                seed = g.tracks[0] if g.tracks else None
                # group_album tag enables album-art thumbs; directory identity
                # is separate for context/selection copy.
                ops.append(
                    (
                        "group",
                        "",
                        g.key,
                        g.label,
                        ("group", "group_directory", "group_album"),
                        seed,
                    )
                )
                gtracks = list(g.tracks)
                if reverse:
                    gtracks = list(reversed(gtracks))
                for t in gtracks:
                    ops.append(("track", g.key, t))
        elif primary == SortPrimary.ARTIST:
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
        elif primary == SortPrimary.ARTIST_ALBUM_COMBO:
            # "{artist} - {album}" → tracks (multi-artist dirs → Various Artists)
            groups = group_by_artist_dash_album(tracks)
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
        elif primary == SortPrimary.ALBUM:
            # "{album} - {artist}" → tracks
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

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            self.win.set_tracks_usable(self._library_root_reachable())
            return

        def run_chunk(chunk_i: int) -> None:
            self._populate_after_id = None
            start, end = chunks[chunk_i]
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
                            open=False,
                        )
                        if seed is not None:
                            self._group_seed_by_iid[iid] = seed
                else:
                    _, parent, track = op
                    self._insert_track_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )
            else:
                self.win.set_tracks_usable(self._library_root_reachable())
                self._start_background_album_art()
                self._refresh_playing_highlight()

        run_chunk(0)

    def _rebuild_videos_tree(
        self,
        tracks: list[Track],
        *,
        search_scores: dict[str, float] | None = None,
        flat_search: bool = False,
    ) -> None:
        """Rebuild Library → Video (TV series by show title; else folder)."""
        self._cancel_videos_populate()
        self.win.clear_videos_tree()
        if not tracks:
            return

        ops: list = []
        if flat_search:
            # Search: flat relevance order (already sorted by filter).
            for t in tracks:
                ops.append(("track", "", t))
        else:
            groups = group_videos_for_library(tracks)
            for g in groups:
                folder_iid = f"vl:{g.key}"
                seed = g.tracks[0] if g.tracks else None
                # TV series and plain folders both use group_directory so exclude
                # / sync-folder actions keep working on the parent row.
                tags = ("group", "group_directory")
                if g.key.startswith("tv:"):
                    tags = ("group", "group_directory", "group_tv_series")
                ops.append(
                    (
                        "group",
                        "",
                        folder_iid,
                        g.label,
                        tags,
                        seed,
                    )
                )
                for t in g.tracks:
                    ops.append(("track", folder_iid, t))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._videos_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.videos_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags, seed = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            values=(label,),
                            tags=tags,
                            open=False,
                        )
                        if seed is not None:
                            self._group_seed_by_iid[iid] = seed
                else:
                    _, parent, track = op
                    self._insert_video_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._videos_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )

        run_chunk(0)

    def _rebuild_audiobooks_tree(
        self,
        tracks: list[Track],
        *,
        search_scores: dict[str, float] | None = None,
        flat_search: bool = False,
    ) -> None:
        """Rebuild Library → Audiobooks (genre Audiobook; Author → Album - Year)."""
        self._cancel_audiobooks_populate()
        self.win.clear_audiobooks_tree()
        if not tracks:
            return

        ops: list = []
        if flat_search:
            for t in tracks:
                ops.append(("track", "", t))
        else:
            groups = group_by_artist_album_year(tracks)
            for ag in groups:
                # Prefix group iids so they never collide with Music tree maps.
                artist_iid = f"ab:{ag.key}"
                artist_seed = None
                for release in ag.children:
                    if release.tracks:
                        artist_seed = release.tracks[0]
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
                for release in ag.children:
                    release_iid = f"ab:{release.key}"
                    release_seed = release.tracks[0] if release.tracks else None
                    ops.append(
                        (
                            "group",
                            artist_iid,
                            release_iid,
                            release.label,
                            ("group", "group_album"),
                            release_seed,
                        )
                    )
                    for t in release.tracks:
                        ops.append(("track", release_iid, t))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._audiobooks_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.audiobooks_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags, seed = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            values=(label, "", "", ""),
                            tags=tags,
                            open=False,
                        )
                        if seed is not None:
                            self._group_seed_by_iid[iid] = seed
                else:
                    _, parent, track = op
                    self._insert_track_row(parent, track, tree=tree)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._audiobooks_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )
            else:
                self._refresh_playing_highlight()

        run_chunk(0)

    def _clear_device_media_trees(self) -> None:
        """Drop Device → Music/Video/Audiobooks/Podcasts trees and maps."""
        self._cancel_device_populate()
        self._cancel_device_video_populate()
        self._cancel_device_audiobook_populate()
        self._cancel_device_podcast_populate()
        if self._device_tree_refresh_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_tree_refresh_after_id)
            except Exception:
                pass
            self._device_tree_refresh_after_id = None
        self._device_album_art_job_gen += 1
        self.win.clear_device_track_tree()
        self.win.clear_device_video_tree()
        self.win.clear_device_audiobooks_tree()
        self.win.clear_device_podcasts_tree()
        self._device_track_by_iid.clear()
        self._device_video_track_by_iid.clear()
        self._device_audiobook_track_by_iid.clear()
        self._device_podcast_track_by_iid.clear()
        self._device_music_refs = []
        self._device_video_refs = []
        self._device_audiobook_refs = []
        self._device_podcast_refs = []
        self._device_pending_album_art = []
        self._clear_device_playlists_ui()

    # Back-compat alias used by older call sites / mental model.
    def _clear_device_music_tree(self) -> None:
        self._clear_device_media_trees()

    def _host_tracks_by_guid_for_refs(
        self, refs: list[DeviceTrackRef]
    ) -> dict[str, Track]:
        """GUID → host Track for device trees: library music, then podcast index.

        Music library rows live in ``tracks``. Podcast episode ObjectFileNames use
        the same 32-hex GUID scheme but are stored in ``podcast_episodes``
        (internet-sourced; not expected on a library drive).
        """
        guids: list[str] = []
        for r in refs:
            g = guid_from_remote_name(getattr(r, "name", None))
            if g:
                guids.append(g)
        if not guids:
            return {}
        by: dict[str, Track] = {}
        try:
            by.update(get_tracks_by_guids(guids))
        except Exception:
            logger.debug(
                "get_tracks_by_guids for device tree failed", exc_info=True
            )
        missing = [g for g in guids if g not in by]
        if missing:
            try:
                by.update(get_tracks_by_podcast_guids(missing))
            except Exception:
                logger.debug(
                    "get_tracks_by_podcast_guids for device tree failed",
                    exc_info=True,
                )
        return by

    def _schedule_device_music_tree_refresh(
        self, *, enrich_missing_tags: bool = False, delay_ms: int = 250
    ) -> None:
        """Debounce Device media tree rebuild (music + video)."""
        if self._device_tree_refresh_after_id is not None:
            try:
                self.win.root.after_cancel(self._device_tree_refresh_after_id)
            except Exception:
                pass
            self._device_tree_refresh_after_id = None

        def _fire() -> None:
            self._device_tree_refresh_after_id = None
            self._refresh_device_media_trees(enrich_missing_tags=enrich_missing_tags)

        self._device_tree_refresh_after_id = self.win.root.after(delay_ms, _fire)

    def _refresh_device_music_tree(self, *, enrich_missing_tags: bool = False) -> None:
        """Compatibility wrapper — refresh music and video device trees."""
        self._refresh_device_media_trees(enrich_missing_tags=enrich_missing_tags)

    def _split_device_music_and_audiobook_refs(
        self,
        refs: list[DeviceTrackRef],
        by_guid: dict[str, Track],
    ) -> tuple[list[DeviceTrackRef], list[DeviceTrackRef]]:
        """Partition audio refs into music vs audiobook by resolved genre."""
        music: list[DeviceTrackRef] = []
        audiobooks: list[DeviceTrackRef] = []
        display = resolve_device_tracks_for_display(refs, by_guid)
        for ref, track in zip(refs, display):
            if is_audiobook_track(track):
                audiobooks.append(ref)
            else:
                music.append(ref)
        return music, audiobooks

    def _refresh_device_media_trees(self, *, enrich_missing_tags: bool = False) -> None:
        """Rebuild Device → Music / Video / Audiobooks / Podcasts from index.

        Music/Audiobooks: GUID basename → host library tags, then device tags,
        then filename. Audiobooks are audio objects whose genre is Audiobook
        (host or device tags). Video: Video/TV parents only (not ZENcast).
        Podcasts: ZENcast (+ show subfolders).

        Automatic ``get_track_metadata`` is **off** by default: bulk calls
        panic/poison ZEN sessions. Use device context → **Fetch track tags…**
        (optional *enrich_missing_tags* remains for diagnostics only).
        """
        serial = self._device_serial
        if not serial:
            self._clear_device_media_trees()
            return

        podcast_parents = self._podcast_parent_ids()

        # --- Audio listing (Music folder + elsewhere) ---
        try:
            audio_refs = list_cached_music_refs(serial)
        except Exception:
            logger.warning("list_cached_music_refs failed", exc_info=True)
            audio_refs = []

        # --- Podcasts by ZENcast parent (video-as-podcast, show folders) ---
        try:
            zencast_podcast_refs = list_cached_podcast_refs(
                serial, podcast_parents=podcast_parents
            )
        except Exception:
            logger.warning("list_cached_podcast_refs failed", exc_info=True)
            zencast_podcast_refs = []

        # Audio podcasts often land under Music (parent 100) on ZEN Vision:M.
        # Reclassify any Music-folder object whose ObjectFileName GUID is a known
        # podcast episode so Device → Podcasts gets the tags from
        # podcast_episodes (not the music library ``tracks`` table).
        candidate_guids: list[str] = []
        for r in list(audio_refs) + list(zencast_podcast_refs):
            g = guid_from_remote_name(getattr(r, "name", None))
            if g:
                candidate_guids.append(g)
        try:
            pod_guid_set = known_podcast_guids(candidate_guids)
        except Exception:
            logger.debug("known_podcast_guids failed", exc_info=True)
            pod_guid_set = set()

        music_audio: list[DeviceTrackRef] = []
        podcast_from_audio: list[DeviceTrackRef] = []
        for ref in audio_refs:
            g = guid_from_remote_name(ref.name)
            if g and g in pod_guid_set:
                podcast_from_audio.append(ref)
            else:
                music_audio.append(ref)

        # Merge ZENcast + GUID-classified podcast objects (dedupe by item_id).
        podcast_by_id: dict[int, DeviceTrackRef] = {}
        for ref in list(zencast_podcast_refs) + list(podcast_from_audio):
            oid = int(ref.item_id or 0)
            if oid > 0:
                podcast_by_id[oid] = ref
        podcast_refs = list(podcast_by_id.values())

        audio_by_guid = self._host_tracks_by_guid_for_refs(music_audio)
        music_audio = enrich_refs_from_host(music_audio, audio_by_guid)
        music_refs, audiobook_refs = self._split_device_music_and_audiobook_refs(
            music_audio, audio_by_guid
        )
        self._device_music_refs = list(music_refs)
        self._device_audiobook_refs = list(audiobook_refs)
        self._rebuild_device_music_tree(music_refs, audio_by_guid)
        self._rebuild_device_audiobooks_tree(audiobook_refs, audio_by_guid)

        # --- Video (exclude ZENcast / podcast parents) ---
        try:
            video_refs = list_cached_video_refs(
                serial, podcast_parents=podcast_parents
            )
        except Exception:
            logger.warning("list_cached_video_refs failed", exc_info=True)
            video_refs = []
        # Drop video objects that are known podcast episode GUIDs (still video
        # under ZENcast already handled by list_cached_podcast_refs).
        if pod_guid_set:
            video_refs = [
                r
                for r in video_refs
                if (guid_from_remote_name(r.name) or "") not in pod_guid_set
            ]
        video_by_guid = self._host_tracks_by_guid_for_refs(video_refs)
        video_refs = enrich_refs_from_host(video_refs, video_by_guid)
        self._device_video_refs = list(video_refs)
        self._rebuild_device_video_tree(video_refs, video_by_guid)

        # --- Podcasts: ZENcast + Music-folder episodes keyed by podcast GUID ---
        podcast_by_guid = self._host_tracks_by_guid_for_refs(podcast_refs)
        podcast_refs = enrich_refs_from_host(podcast_refs, podcast_by_guid)
        self._device_podcast_refs = list(podcast_refs)
        self._rebuild_device_podcasts_tree(podcast_refs, podcast_by_guid)

        if enrich_missing_tags:
            need_music = refs_needing_device_tags(music_refs, audio_by_guid)
            need_ab = refs_needing_device_tags(audiobook_refs, audio_by_guid)
            need_video = refs_needing_device_tags(video_refs, video_by_guid)
            need_pod = refs_needing_device_tags(podcast_refs, podcast_by_guid)
            # Deduplicate by item_id (same object should not appear twice).
            seen: set[int] = set()
            need: list[DeviceTrackRef] = []
            for ref in (
                list(need_music)
                + list(need_ab)
                + list(need_video)
                + list(need_pod)
            ):
                oid = int(ref.item_id or 0)
                if oid <= 0 or oid in seen:
                    continue
                seen.add(oid)
                need.append(ref)
            if need and self.device.is_connected() and not self._device_tag_enrich_inflight:
                remaining_ms = int(self._device_io.quiet_remaining_s() * 1000)
                delay_ms = max(remaining_ms, 500)

                def _later() -> None:
                    still_music = refs_needing_device_tags(
                        self._device_music_refs,
                        self._host_tracks_by_guid_for_refs(self._device_music_refs),
                    )
                    still_ab = refs_needing_device_tags(
                        self._device_audiobook_refs,
                        self._host_tracks_by_guid_for_refs(
                            self._device_audiobook_refs
                        ),
                    )
                    still_video = refs_needing_device_tags(
                        self._device_video_refs,
                        self._host_tracks_by_guid_for_refs(self._device_video_refs),
                    )
                    still_pod = refs_needing_device_tags(
                        self._device_podcast_refs,
                        self._host_tracks_by_guid_for_refs(
                            self._device_podcast_refs
                        ),
                    )
                    still_seen: set[int] = set()
                    still: list[DeviceTrackRef] = []
                    for ref in (
                        list(still_music)
                        + list(still_ab)
                        + list(still_video)
                        + list(still_pod)
                    ):
                        oid = int(ref.item_id or 0)
                        if oid <= 0 or oid in still_seen:
                            continue
                        still_seen.add(oid)
                        still.append(ref)
                    if still:
                        self._start_device_tag_enrich(still)

                self.win.root.after(delay_ms, _later)

    def _rebuild_device_music_tree(
        self,
        refs: list[DeviceTrackRef],
        by_guid: dict[str, Track] | None = None,
    ) -> None:
        """Chunked Treeview insert for Device → Music (artist → album → track)."""
        self._cancel_device_populate()
        self.win.clear_device_track_tree()
        self._device_track_by_iid.clear()
        self._device_pending_album_art = []

        if by_guid is None:
            by_guid = self._host_tracks_by_guid_for_refs(refs)
        tracks = resolve_device_tracks_for_display(refs, by_guid)
        if not tracks:
            return

        # Same default grouping as the library tree (artist → album).
        ops: list = []
        groups = group_by_artist_album(tracks)
        for ag in groups:
            artist_iid = f"d:{ag.key}"
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
            for album in ag.children:
                album_iid = f"d:{album.key}"
                album_seed = album.tracks[0] if album.tracks else None
                ops.append(
                    (
                        "group",
                        artist_iid,
                        album_iid,
                        album.label,
                        ("group", "group_album"),
                        album_seed,
                    )
                )
                for t in album.tracks:
                    ops.append(("track", album_iid, t))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._device_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.device_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags, seed = op
                    if not tree.exists(iid):
                        image = ""
                        if seed is not None and "group_album" in tags:
                            art_path = self._host_path_for_device_track(seed)
                            if art_path:
                                photo = self.win.album_art_photo_from_disk(
                                    art_path,
                                    cache_key=iid,
                                    size=DEFAULT_THUMB_SIZE,
                                )
                                if photo is not None:
                                    image = photo
                                else:
                                    self._device_pending_album_art.append(
                                        (iid, art_path)
                                    )
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            image=image,
                            values=(label, "", "", ""),
                            tags=tags,
                            open=False,
                        )
                else:
                    _, parent, track = op
                    self._insert_device_track_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._device_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )
            else:
                self._start_device_background_album_art()

        run_chunk(0)

    def _rebuild_device_audiobooks_tree(
        self,
        refs: list[DeviceTrackRef],
        by_guid: dict[str, Track] | None = None,
    ) -> None:
        """Chunked Treeview insert for Device → Audiobooks (Author → Year)."""
        self._cancel_device_audiobook_populate()
        self.win.clear_device_audiobooks_tree()
        self._device_audiobook_track_by_iid.clear()

        if by_guid is None:
            by_guid = self._host_tracks_by_guid_for_refs(refs)
        tracks = resolve_device_tracks_for_display(refs, by_guid)
        if not tracks:
            return

        ops: list = []
        groups = group_by_artist_album_year(tracks)
        for ag in groups:
            artist_iid = f"dab:{ag.key}"
            artist_seed = None
            for release in ag.children:
                if release.tracks:
                    artist_seed = release.tracks[0]
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
            for release in ag.children:
                release_iid = f"dab:{release.key}"
                ops.append(
                    (
                        "group",
                        artist_iid,
                        release_iid,
                        release.label,
                        ("group", "group_album"),
                        release.tracks[0] if release.tracks else None,
                    )
                )
                for t in release.tracks:
                    ops.append(("track", release_iid, t))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._device_audiobook_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.device_audiobooks_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags, _seed = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            values=(label, "", "", ""),
                            tags=tags,
                            open=False,
                        )
                else:
                    _, parent, track = op
                    self._insert_device_audiobook_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._device_audiobook_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )

        run_chunk(0)

    def _rebuild_device_video_tree(
        self,
        refs: list[DeviceTrackRef],
        by_guid: dict[str, Track] | None = None,
    ) -> None:
        """Chunked Treeview insert for Device → Video (folder → items)."""
        self._cancel_device_video_populate()
        self.win.clear_device_video_tree()
        self._device_video_track_by_iid.clear()

        if by_guid is None:
            by_guid = self._host_tracks_by_guid_for_refs(refs)
        if not refs:
            return

        # Group by ZEN parent folder (Video 120 / TV 124 / Other).
        by_folder: dict[int, list[DeviceTrackRef]] = {}
        for ref in refs:
            pid = int(ref.parent_id or 0)
            by_folder.setdefault(pid, []).append(ref)

        layout = self._folder_layout_or_legacy()
        video_id = layout.video_id
        tv_id = layout.tv_id

        # Prefer known Video/TV folders first, then others by id.
        def folder_sort_key(pid: int) -> tuple:
            if pid == video_id:
                return (0, pid)
            if pid == tv_id:
                return (1, pid)
            return (2, pid)

        ops: list = []
        for pid in sorted(by_folder.keys(), key=folder_sort_key):
            folder_refs = by_folder[pid]
            folder_iid = f"dv:folder:{pid}"
            label = video_folder_label(pid, layout=layout)
            ops.append(
                (
                    "group",
                    "",
                    folder_iid,
                    label,
                    ("group", "group_folder"),
                )
            )
            # Resolve tags per folder; sort by display title.
            display = resolve_device_tracks_for_display(folder_refs, by_guid)
            # Pair with original refs for stable order: title then name then id.
            paired = list(zip(folder_refs, display))
            paired.sort(
                key=lambda pair: (
                    (pair[1].meta.title or "").casefold(),
                    (pair[0].name or "").casefold(),
                    int(pair[0].item_id or 0),
                )
            )
            for _ref, track in paired:
                ops.append(("track", folder_iid, track))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._device_video_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.device_video_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            values=(label, "", "", ""),
                            tags=tags,
                            open=False,
                        )
                else:
                    _, parent, track = op
                    self._insert_device_video_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._device_video_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )

        run_chunk(0)

    def _rebuild_device_podcasts_tree(
        self,
        refs: list[DeviceTrackRef],
        by_guid: dict[str, Track] | None = None,
    ) -> None:
        """Chunked Treeview insert for Device → Podcasts.

        Hierarchy: network → podcast (``{show} - {network}``) → episodes.
        Episodes are reverse-chronological (pub date / year desc). Device
        folder parents (Music / ZENcast) are not shown.
        """
        self._cancel_device_podcast_populate()
        self.win.clear_device_podcasts_tree()
        self._device_podcast_track_by_iid.clear()

        if by_guid is None:
            by_guid = self._host_tracks_by_guid_for_refs(refs)
        if not refs:
            return

        display = resolve_device_tracks_for_display(refs, by_guid)
        # network_key → podcast_key → list of (ref, track)
        by_network: dict[str, dict[str, list[tuple[DeviceTrackRef, Track]]]] = {}
        # Stable display labels for keys
        network_label: dict[str, str] = {}
        podcast_label: dict[tuple[str, str], str] = {}

        for ref, track in zip(refs, display):
            meta = track.meta
            show = (meta.album or "").strip() or "Unknown podcast"
            author = (meta.artist or "").strip()
            # episode_display_track falls back artist→show when author empty;
            # avoid "Show - Show" and treat that as no network.
            if (
                author
                and author.casefold() != show.casefold()
                and author.casefold() not in ("unknown artist", "podcast")
            ):
                net = author
            else:
                net = "Other"
            net_key = net.casefold()
            show_key = show.casefold()
            network_label[net_key] = net
            row_label = f"{show} - {net}" if net != "Other" else show
            podcast_label[(net_key, show_key)] = row_label
            by_network.setdefault(net_key, {}).setdefault(show_key, []).append(
                (ref, track)
            )

        def episode_sort_key(pair: tuple[DeviceTrackRef, Track]) -> tuple:
            _ref, track = pair
            # ISO-ish dates sort lexicographically with reverse=True → newest first.
            date = (track.meta.date or "").strip()
            return (
                date,
                (track.meta.title or "").casefold(),
                int(_ref.item_id or 0),
            )

        ops: list = []
        # Networks A–Z; "Other" last.
        def net_sort_key(nk: str) -> tuple:
            lab = network_label.get(nk, nk)
            if lab == "Other":
                return (1, lab.casefold())
            return (0, lab.casefold())

        for nk in sorted(by_network.keys(), key=net_sort_key):
            net_iid = f"dp:net:{nk}"
            ops.append(
                (
                    "group",
                    "",
                    net_iid,
                    network_label[nk],
                    ("group", "group_network"),
                )
            )
            shows = by_network[nk]
            for sk in sorted(
                shows.keys(), key=lambda k: podcast_label[(nk, k)].casefold()
            ):
                show_iid = f"dp:show:{nk}:{sk}"
                ops.append(
                    (
                        "group",
                        net_iid,
                        show_iid,
                        podcast_label[(nk, sk)],
                        ("group", "group_podcast"),
                    )
                )
                paired = list(shows[sk])
                # Newest first; undated (empty date) last.
                paired.sort(key=episode_sort_key, reverse=True)
                dated = [
                    p for p in paired if (p[1].meta.date or "").strip()
                ]
                undated = [
                    p for p in paired if not (p[1].meta.date or "").strip()
                ]
                for _ref, track in dated + undated:
                    ops.append(("track", show_iid, track))

        chunks = fibonacci_chunk_bounds(len(ops))
        if not chunks:
            return

        def run_chunk(chunk_i: int) -> None:
            self._device_podcast_populate_after_id = None
            start, end = chunks[chunk_i]
            tree = self.win.device_podcasts_tree
            for i in range(start, end):
                op = ops[i]
                if op[0] == "group":
                    _, parent, iid, label, tags = op
                    if not tree.exists(iid):
                        tree.insert(
                            parent,
                            "end",
                            iid=iid,
                            text="",
                            values=(label, "", "", ""),
                            tags=tags,
                            open=True,
                        )
                else:
                    _, parent, track = op
                    self._insert_device_podcast_row(parent, track)
            nxt = chunk_i + 1
            if nxt < len(chunks):
                self._device_podcast_populate_after_id = self.win.root.after(
                    1, lambda i=nxt: run_chunk(i)
                )

        run_chunk(0)

    def _start_device_background_album_art(self) -> None:
        pending = list(self._device_pending_album_art)
        if not pending:
            return
        self._device_album_art_job_gen += 1
        gen = self._device_album_art_job_gen
        size = DEFAULT_THUMB_SIZE

        def work() -> list[tuple[str, str]]:
            ready: list[tuple[str, str]] = []
            for iid, path in pending:
                if ensure_cached_thumb(path, size=size) is not None:
                    ready.append((iid, path))
            return ready

        def on_done(ready: list[tuple[str, str]]) -> None:
            if gen != self._device_album_art_job_gen:
                return
            for iid, path in ready:
                if not self.win.device_tree.exists(iid):
                    continue
                photo = self.win.album_art_photo_from_disk(
                    path, cache_key=iid, size=size
                )
                if photo is None:
                    continue
                try:
                    self.win.device_tree.item(iid, image=photo)
                except Exception:
                    pass

        def on_error(exc: BaseException) -> None:
            logger.debug("Device album art job failed: %s", exc)

        def runner() -> None:
            try:
                result = work()
            except BaseException as exc:
                self.win.root.after(0, lambda: on_error(exc))
                return
            self.win.root.after(0, lambda: on_done(result))

        threading.Thread(target=runner, name="device-album-art", daemon=True).start()

    def action_device_fetch_tags_selected(self) -> None:
        """Context menu: Get_Trackmetadata (+ download/mutagen fallback) for selection."""
        if not self._require_device_ready():
            return
        if self._transfer_busy or self._device_tag_enrich_inflight:
            messagebox.showinfo(
                "Fetch track tags",
                "A transfer or device job is already in progress.",
            )
            return
        tree = self._device_context_tree or self.win.active_device_tree()
        tracks = self._device_tracks_from_tree_selection(tree)
        refs = self._device_refs_for_tracks(tracks)
        if not refs:
            messagebox.showinfo("Fetch track tags", "No tracks selected.")
            return
        # Deduplicate by item_id (multi-select may repeat under groups).
        seen: set[int] = set()
        batch: list[DeviceTrackRef] = []
        for ref in refs:
            oid = int(ref.item_id or 0)
            if oid <= 0 or oid in seen:
                continue
            seen.add(oid)
            batch.append(ref)
        if not batch:
            messagebox.showinfo("Fetch track tags", "No tracks selected.")
            return
        n = len(batch)
        if n > 1 and not messagebox.askyesno(
            "Fetch track tags",
            f"Fetch tags for {n} selected items?\n\n"
            "Each object uses Get_Trackmetadata; if tags are empty or "
            "placeholder, the file is downloaded temporarily and read with "
            "mutagen.\n\n"
            "Large batches can stress the USB session — keep selections "
            "small when possible.",
            default=messagebox.YES,
        ):
            return
        self._start_device_tag_enrich(batch, interactive=True)

    def _start_device_tag_enrich(
        self,
        need: list[DeviceTrackRef],
        *,
        interactive: bool = False,
    ) -> None:
        """Background tag fetch for selected Device rows (explicit only).

        Uses Get_Trackmetadata, then download+mutagen when tags are still
        empty/placeholder. Never auto-started after inventory refresh.
        """
        if not need or self._device_tag_enrich_inflight:
            return
        if not self.device.is_connected():
            return
        if self._transfer_busy or not self._device_io.try_acquire("tag-enrich"):
            if interactive:
                messagebox.showinfo(
                    "Fetch track tags",
                    "The device is busy with another USB operation. Try again "
                    "in a moment.",
                )
            else:
                logger.info(
                    "Device tag enrich deferred (busy holder=%s) count=%s",
                    self._device_io.holder,
                    len(need),
                )
            return

        self._device_tag_enrich_inflight = True
        batch = list(need)
        device = self.device
        serial = self._device_serial
        gate_reason = "tag-enrich"
        show_ui = bool(interactive)

        def work():
            return device_ops.enrich_track_refs_with_embedded_fallback(
                device, batch, stop_on_fatal=True
            )

        def on_done(result) -> None:
            self._device_tag_enrich_inflight = False
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            if serial != self._device_serial:
                return
            updated_by_id = {
                int(r.item_id or 0): r
                for r in (result.refs or [])
                if r is not None and int(r.item_id or 0) > 0
            }
            # Always rebuild when any ref may have been updated.
            if result.updated > 0 and updated_by_id:

                def _merge(refs: list[DeviceTrackRef]) -> list[DeviceTrackRef]:
                    return [
                        updated_by_id.get(int(ref.item_id or 0), ref)
                        for ref in refs
                    ]

                music_merged = _merge(self._device_music_refs)
                ab_merged = _merge(self._device_audiobook_refs)
                video_merged = _merge(self._device_video_refs)
                podcast_merged = _merge(self._device_podcast_refs)
                audio_merged = list(music_merged) + list(ab_merged)
                audio_by_guid = self._host_tracks_by_guid_for_refs(audio_merged)
                music_merged, ab_merged = self._split_device_music_and_audiobook_refs(
                    audio_merged, audio_by_guid
                )
                self._device_music_refs = music_merged
                self._device_audiobook_refs = ab_merged
                self._device_video_refs = video_merged
                self._device_podcast_refs = podcast_merged
                video_by_guid = self._host_tracks_by_guid_for_refs(video_merged)
                podcast_by_guid = self._host_tracks_by_guid_for_refs(
                    podcast_merged
                )
                self._rebuild_device_music_tree(music_merged, audio_by_guid)
                self._rebuild_device_audiobooks_tree(ab_merged, audio_by_guid)
                self._rebuild_device_video_tree(video_merged, video_by_guid)
                self._rebuild_device_podcasts_tree(
                    podcast_merged, podcast_by_guid
                )
            logger.info(
                "Device media tags fetched updated=%s failed=%s aborted=%s "
                "device=%s embedded=%s",
                result.updated,
                result.failed,
                result.aborted,
                getattr(result, "from_device", 0),
                getattr(result, "from_embedded", 0),
            )
            if show_ui:
                if result.aborted:
                    messagebox.showerror(
                        "Fetch track tags aborted",
                        "A fatal MTP error stopped the batch "
                        f"(object id {result.failed_id}).\n\n"
                        f"Updated {result.updated} before abort; "
                        f"{result.failed} failed.\n"
                        "Reconnect if the session is poisoned.",
                    )
                elif result.updated == 0:
                    messagebox.showinfo(
                        "Fetch track tags",
                        "Could not recover usable tags for the selection.\n"
                        "Device listing is unchanged.",
                    )
                else:
                    messagebox.showinfo(
                        "Fetch track tags",
                        f"Updated {result.updated} of {len(batch)} item(s).\n"
                        f"From device metadata: {result.from_device}\n"
                        f"From file tags (download): {result.from_embedded}\n"
                        f"Unchanged/failed: {result.failed}",
                    )

        def on_error(exc: BaseException) -> None:
            self._device_tag_enrich_inflight = False
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            try:
                self.win.set_progress_status("")
            except Exception:
                pass
            logger.warning("Device tag enrich failed: %s", exc)
            if show_ui:
                messagebox.showerror("Fetch track tags", str(exc))

        logger.info(
            "Device media tag fetch start count=%s interactive=%s",
            len(batch),
            interactive,
        )
        if show_ui:
            try:
                self.win.set_progress_status(
                    f"Fetching tags for {len(batch)} item(s)…"
                )
            except Exception:
                pass
        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="device-media-tag-enrich",
        )

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

        Probes only when the exclusive device I/O gate is free and outside the
        post-job quiet window — never while a sync, listing, seed, or enrich
        holds USB. When a session looks open, ``session_alive`` detects unplug;
        after soft-fail strikes we disconnect and retry connect next interval
        (unless the user disabled auto-reconnect via Device → Disconnect).
        """
        if gen != self._device_poll_gen:
            return
        if self.win.active_mode() != "experimental":
            return
        if not self._device_auto_reconnect:
            return

        # Single gate: skip while any USB owner is active or bus is cooling down.
        if not self._device_io.can_auto_probe():
            self._schedule_device_poll(gen)
            return
        if not self._device_io.try_acquire("auto-connect"):
            self._schedule_device_poll(gen)
            return

        local_gen = gen
        need_identity = self._active_profile is None
        gate_reason = "auto-connect"

        def work() -> tuple[str, DeviceInfo | None]:
            """Return (status, info). status: ok | soft_fail | absent.

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
            quiet_s: float | None = None
            status: str = "absent"
            info: DeviceInfo | None = None
            try:
                stale = (
                    local_gen != self._device_poll_gen
                    or self.win.active_mode() != "experimental"
                )
                if stale:
                    if (
                        self.win.active_mode() != "experimental"
                        and self.device.is_connected()
                    ):
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
                elif status == "soft_fail":
                    self._device_probe_fails += 1
                    if self._device_probe_fails < _DEVICE_PROBE_FAIL_LIMIT:
                        logger.info(
                            "Experimental auto-connect: session probe soft-fail "
                            "%s/%s (keeping session; common after long listings)",
                            self._device_probe_fails,
                            _DEVICE_PROBE_FAIL_LIMIT,
                        )
                        quiet_s = _DEVICE_USB_COOLDOWN_S
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
                        self._clear_device_session()
                else:
                    self._device_probe_fails = 0
                    self._note_no_device()
                    self._clear_device_session()
            finally:
                self._device_io.release(reason=gate_reason, quiet_s=quiet_s)

            # Start seed / profile only after releasing the gate so seed can
            # acquire exclusive USB ownership for list_files.
            if (
                local_gen == self._device_poll_gen
                and self.win.active_mode() == "experimental"
                and self._device_auto_reconnect
                and status == "ok"
            ):
                if info is not None and self._active_profile is None:
                    self._apply_device_profile(info)
                    self._note_device_session(info)
                elif not self._device_index_seeded and self.device.is_connected():
                    self._note_device_session(info)

            if local_gen == self._device_poll_gen:
                self._schedule_device_poll(local_gen)

        def on_error(_exc: BaseException) -> None:
            try:
                if (
                    local_gen != self._device_poll_gen
                    or self.win.active_mode() != "experimental"
                    or not self._device_auto_reconnect
                ):
                    return
                self._note_no_device()
                self._clear_device_session()
            finally:
                self._device_io.release(reason=gate_reason)
            if local_gen == self._device_poll_gen:
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
            "Device profile %s (%s) manufacturer=%r model=%r serial=%r",
            profile.id,
            profile.display_name,
            info.manufacturer,
            info.model,
            info.serial,
        )

    def _clear_device_profile(self) -> None:
        self._active_profile = None
        self.win.set_device_graphic(None)

    def _clear_device_session(self) -> None:
        """Clear profile + in-memory serial seed flags (DB inventory kept)."""
        self._clear_device_profile()
        self._device_serial = None
        self._device_index_seeded = False
        self._device_index_seed_inflight = False
        self._device_tag_enrich_inflight = False
        self._folder_layout = legacy_zen_vision_m_layout()
        try:
            self.device.music_folder_id = DEFAULT_MUSIC_FOLDER_ID
        except Exception:
            pass
        self._clear_device_music_tree()

    def _note_device_session(self, info: DeviceInfo | None) -> None:
        """Remember device key and seed file index once per physical device."""
        serial = device_serial_key(info)
        prev = self._device_serial
        if prev and prev != serial:
            # Different player plugged in — do not reuse the other device's cache.
            logger.info(
                "Device key changed %s → %s; re-seeding device index",
                prev,
                serial,
            )
            self._device_index_seeded = False
            self._device_index_seed_inflight = False
        self._device_serial = serial
        if info is not None:
            try:
                upsert_device(
                    serial,
                    name=info.name or "",
                    manufacturer=info.manufacturer or "",
                    model=info.model or "",
                )
            except Exception:
                logger.debug("upsert_device failed", exc_info=True)
            logger.info(
                "Device session key=%s serial=%r name=%r model=%r",
                serial,
                info.serial,
                info.name,
                info.model,
            )
        if not self._device_index_seeded and not self._device_index_seed_inflight:
            self._start_device_index_seed(serial)

    def _start_device_index_seed(self, serial: str, *, force: bool = False) -> bool:
        """Background list_files once → replace SQLite device_files for *serial*.

        Returns True when a seed job was started (USB gate acquired).
        """
        if self._device_index_seed_inflight:
            return False
        if self._device_index_seeded and not force:
            return False
        if not self.device.is_connected():
            return False
        # Exclusive USB: never race auto-connect or an active transfer.
        if self._transfer_busy and not force:
            logger.info(
                "Device index seed deferred (transfer busy) serial=%s", serial
            )
            return False
        if not self._device_io.try_acquire("index-seed"):
            logger.info(
                "Device index seed deferred (USB held by %r) serial=%s",
                self._device_io.holder,
                serial,
            )
            return False

        self._device_index_seed_inflight = True
        device = self.device
        gate_reason = "index-seed"

        def work():
            # Folders first (cheap): name → Music/Video/TV/ZENcast object ids
            # for this firmware, parent map for podcast show folders, then
            # full file listing for the durable inventory.
            folders = device_ops.list_folders(device)
            from mtpmanager.domain.device_folders import resolve_device_folder_layout

            layout = resolve_device_folder_layout(folders)
            parent_map = {
                int(f.folder_id): int(f.parent_id or 0)
                for f in (folders or [])
                if int(getattr(f, "folder_id", 0) or 0) > 0
            }
            files = device_ops.list_files(device)
            n = replace_device_listing(serial, files, source="list")
            return {"layout": layout, "folder_parents": parent_map, "files": n}

        def on_done(result) -> None:
            self._device_index_seed_inflight = False
            self._device_index_seeded = True
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )
            # Clear seed label so it does not linger after indexing.
            self.win.set_progress_status("")
            layout = None
            n = 0
            if isinstance(result, dict):
                layout = result.get("layout")
                n = int(result.get("files") or 0)
                parents = result.get("folder_parents") or {}
                if isinstance(parents, dict):
                    self._folder_parent_by_id = {
                        int(k): int(v) for k, v in parents.items()
                    }
            if layout is not None:
                self._apply_folder_layout(layout)
            logger.info(
                "Device index seeded serial=%s files=%s music_folder=%s "
                "podcast_folder=%s",
                serial,
                n,
                self._music_folder_id(),
                self._podcast_folder_id(),
            )
            # Populate Device media trees (GUID join only; tags on demand).
            self._refresh_device_music_tree(enrich_missing_tags=False)
            # On-device playlist objects (PyMTP list) after index is warm.
            self.win.root.after(200, self._refresh_device_playlists_tab)

        def on_error(exc: BaseException) -> None:
            self._device_index_seed_inflight = False
            # Leave seeded=False so a later Refresh / reconnect can retry.
            self.win.set_progress_status("")
            logger.warning(
                "Device index seed failed serial=%s: %s", serial, exc
            )
            self._device_io.release(
                reason=gate_reason, quiet_s=_DEVICE_USB_COOLDOWN_S
            )

        self.win.set_progress_status("Indexing device folders + files…")
        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            name="device-index-seed",
        )
        return True

    def _disconnect_for_stable(self) -> None:
        """Drop PyMTP session so Stable mtp-sendtr can claim the device."""
        self._clear_device_session()
        if not self.device.is_connected():
            return
        self._device_io.steal("stable-disconnect")
        try:
            device_ops.disconnect(self.device)
            logger.info("Disconnected PyMTP session for Stable Mode")
        except Exception:
            logger.exception("Disconnect for Stable Mode failed")
        finally:
            self._device_io.release(reason="stable-disconnect")

    def on_connect(self) -> None:
        """Manual connect; re-enables auto-reconnect polling on Experimental.

        Opens the session and loads **identity only** (name / manufacturer /
        model) for the left-panel profile. Battery and storage are not queried
        here — use Device → Device Info for full diagnostics.

        Also starts a one-shot device file index seed (list_files → SQLite).
        """
        self._device_auto_reconnect = True
        if not self._device_io.try_acquire("manual-connect"):
            messagebox.showinfo(
                "Connect",
                "The device is busy with another USB operation. Wait for it "
                "to finish, then try Connect again.",
            )
            return
        gate_reason = "manual-connect"
        try:
            device_ops.connect(self.device)
            self._logged_no_device = False
            try:
                info = device_ops.get_device_identity(self.device)
                self._apply_device_profile(info)
                # Release before seed so index-seed can take the gate.
                self._device_io.release(reason=gate_reason)
                gate_reason = ""
                self._note_device_session(info)
            except Exception:
                # Session is up; missing identity must not undo connect.
                logger.exception(
                    "Connected but could not load device identity "
                    "(name/manufacturer/model)"
                )
                self._device_io.release(reason=gate_reason)
                gate_reason = ""
                self._note_device_session(None)
        except Exception as e:
            logger.exception("Connect failed")
            messagebox.showerror("Connect", str(e))
        finally:
            if gate_reason:
                self._device_io.release(reason=gate_reason)
        # Resume monitoring (liveness + reconnect after unplug).
        if self.win.active_mode() == "experimental":
            self._start_device_poll()

    def on_disconnect(self) -> None:
        """Manual disconnect; stop auto-reconnect until Device → Connect."""
        self._device_auto_reconnect = False
        self._stop_device_poll()
        self._device_io.steal("manual-disconnect")
        try:
            device_ops.disconnect(self.device)
        finally:
            self._device_io.release(reason="manual-disconnect")
        self._clear_device_session()
        self._logged_no_device = False
        logger.info("Device → Disconnect: auto-reconnect paused")


    def on_device_info(self) -> None:
        """Device → Device Info: full diagnostics (battery, storage, …)."""
        if not self._require_device_ready():
            return
        if not self._device_io.try_acquire("device-info"):
            messagebox.showinfo(
                "Device Info",
                "The device is busy with another USB operation. Try again "
                "in a moment.",
            )
            return
        try:
            # Full probe is intentional here; fields soft-fail individually.
            info = device_ops.get_device_info(self.device)
        except Exception as e:
            logger.exception("Device info failed")
            messagebox.showerror("Device Info", str(e))
            return
        finally:
            self._device_io.release(
                reason="device-info", quiet_s=_DEVICE_USB_COOLDOWN_S
            )

        def apply_name(new_name: str) -> None:
            if not self._device_io.try_acquire("set-device-name"):
                raise RuntimeError(
                    "Device is busy; could not apply name change right now."
                )
            try:
                device_ops.set_device_name(self.device, new_name)
                logger.info("Device renamed to %r", new_name)
            finally:
                self._device_io.release(reason="set-device-name")

        show_device_info_dialog(
            self.win.root,
            info,
            apply_name=apply_name,
        )


    def _set_library_busy(self, busy: bool, *, message: str | None = None) -> None:
        self._library_busy = busy
        if busy:
            self.win.set_library_menu_state(manage_enabled=False)
            self.win.set_library_status(
                track_count=len(self.library),
                root_paths=list(self.library.root_paths),
                root_reachable=self._library_root_reachable()
                if self.library.root_paths
                else True,
                busy_message=message or "Working…",
            )
            self._refresh_manage_library_dialog()
        else:
            self._sync_library_chrome()

    def _sync_library_chrome(self) -> None:
        """Update toolbar status, menu enablement, and dead/live list appearance."""
        if self._library_busy:
            self._refresh_manage_library_dialog()
            return
        reachable = self._library_root_reachable()
        total = len(self.library)
        q = (self._library_search_query or self.win.library_search_query() or "").strip()
        filter_on = bool(q)
        shown = self._library_filter_shown_count
        if filter_on and shown is None:
            shown = 0
        self.win.set_library_status(
            track_count=total,
            root_paths=list(self.library.root_paths),
            root_reachable=reachable if self.library.root_paths else True,
            shown_count=shown if filter_on else None,
            filter_active=filter_on,
        )
        self.win.set_library_search_clear_enabled(filter_on)
        # Manage Library stays available so the user can add a first root
        # even when none are reachable yet.
        self.win.set_library_menu_state(manage_enabled=True)
        self.win.set_tracks_usable(reachable)
        self._refresh_manage_library_dialog()

    def _refresh_manage_library_dialog(self) -> None:
        dlg = self._manage_library_dlg
        if dlg is None:
            return
        if not dlg.is_open():
            self._manage_library_dlg = None
            return
        dlg.refresh()
        ex = self._exclusions_dlg
        if ex is not None:
            if not ex.is_open():
                self._exclusions_dlg = None
            else:
                try:
                    ex.refresh()
                except Exception:
                    pass

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

    def _start_deferred_art_warm(self, library: Library) -> None:
        """Warm album thumbs off the UI path so index→tree is not blocked."""
        if not library.tracks:
            return

        def work() -> None:
            try:
                AppController._warm_art_for_library(library)
            except Exception:
                logger.debug("Deferred album art warm failed", exc_info=True)

        threading.Thread(
            target=work, name="mtpmanager-art-warm", daemon=True
        ).start()

    @staticmethod
    def _load_index_worker_with_report(
        report: Callable[..., None] | None,
    ) -> Library | None:
        """Load durable index; *report* streams Fibonacci batches to the UI.

        Does **not** warm album art (that blocked the tree for seconds).
        """
        return load_library_index(
            drop_missing_files=True,
            keep_missing_if_roots_unreachable=True,
            on_progress=report,
            progress_batch_first=_TREE_CHUNK_FIB_FIRST,
            progress_batch_second=_TREE_CHUNK_FIB_SECOND,
            progress_batch_cap=_TREE_CHUNK_CAP,
        )

    @staticmethod
    def _scan_and_save_worker(
        roots: list[str],
        report: Callable[..., None] | None = None,
        *,
        merge_from: Library | None = None,
        final_roots: list[str] | None = None,
    ) -> tuple[Library, str | None]:
        """Worker: scan *roots* + persist index (no Tk).

        When *merge_from* is set, only *roots* are scanned and merged into the
        existing library (other roots' tracks kept). *final_roots* is the
        post-scan root list (defaults to scanned *roots*).

        Returns (library, save_error_message). Scan failures raise; save failures
        are returned so the UI can still show the scanned library.

        *report* receives ``("dir", dir_path)`` for each folder whose media
        files are being tag-read (toolbar Scanning… indicator).
        """
        def on_dir(dir_path: str) -> None:
            if report is not None:
                report("dir", dir_path)

        exclusions = load_exclusion_paths()
        scanned = scan_library_roots(
            roots, on_dir_progress=on_dir, exclusions=exclusions
        )
        if merge_from is not None:
            library = merge_scanned_roots(
                merge_from,
                scanned,
                scanned_roots=roots,
                final_roots=final_roots if final_roots is not None else roots,
            )
        elif final_roots is not None:
            library = Library(
                tracks=list(scanned.tracks),
                root_paths=final_roots,
            )
        else:
            library = scanned
        try:
            save_library_index(library)
        except OSError as e:
            logger.exception("Failed to save library index")
            return library, str(e)
        return library, None

    def _on_scan_progress(self, kind: str, *args) -> None:
        """Main-thread: show bottom-level directory while scanning."""
        if kind != "dir" or not self._library_busy:
            return
        dir_path = str(args[0] or "") if args else ""
        if not dir_path:
            return
        bottom = os.path.basename(dir_path.rstrip(os.sep + "/")) or dir_path
        # Prefer full library root list (merge scans only scan a subset).
        roots = list(
            getattr(self, "_scan_display_roots", None)
            or getattr(self, "_scan_roots", None)
            or self.library.root_paths
        )
        self.win.set_library_status(
            track_count=len(self.library),
            root_paths=roots,
            root_reachable=True,
            busy_message=f"Scanning… /{bottom}",
        )

    def _on_index_restore_progress(self, kind: str, *args) -> None:
        """Main-thread: paint tree rows while the worker still reads SQLite."""
        if kind == "meta":
            roots = list(args[0] or [])
            total = int(args[1] or 0) if len(args) > 1 else 0
            self._index_stream_total = total
            self._cancel_populate()
            self._cancel_videos_populate()
            self._cancel_audiobooks_populate()
            self.win.clear_track_tree()
            self.win.clear_videos_tree()
            self.win.clear_audiobooks_tree()
            self._track_by_iid.clear()
            self._iid_by_path.clear()
            self._group_seed_by_iid.clear()
            self._context_group_seed = None
            self._pending_album_art = []
            self.library = Library(tracks=[], root_paths=roots)
            self._index_stream_active = True
            msg = (
                f"Loading index… 0/{total}"
                if total > 0
                else "Loading index…"
            )
            self.win.set_library_status(
                track_count=0,
                root_paths=roots,
                root_reachable=any(os.path.isdir(r) for r in roots) if roots else True,
                busy_message=msg,
            )
            return

        if kind != "batch" or not self._index_stream_active:
            return

        batch = list(args[0] or [])
        kept = int(args[1] or 0) if len(args) > 1 else len(self.library.tracks) + len(batch)
        total = int(args[2] or 0) if len(args) > 2 else self._index_stream_total
        if not batch:
            return

        # Path-order flat rows while loading; final sort rebuild happens on done.
        self.library.tracks.extend(batch)
        for track in batch:
            self._insert_track_row("", track)

        msg = (
            f"Loading index… {kept}/{total}"
            if total > 0
            else f"Loading index… {kept}"
        )
        self.win.set_library_status(
            track_count=kept,
            root_paths=list(self.library.root_paths),
            root_reachable=self._library_root_reachable()
            if self.library.root_paths
            else True,
            busy_message=msg,
        )

    def _on_library_job_done(self, library: Library | None, *, kind: str) -> None:
        self._library_busy = False
        self._index_stream_active = False
        if library is None:
            self.library = Library()
            self._cancel_populate()
            self._cancel_videos_populate()
            self._cancel_audiobooks_populate()
            self.win.clear_track_tree()
            self.win.clear_videos_tree()
            self.win.clear_audiobooks_tree()
            self._track_by_iid.clear()
            self._iid_by_path.clear()
            self._group_seed_by_iid.clear()
            self._context_group_seed = None
            self._sync_library_chrome()
            logger.info("No library index to restore")
            return

        self.library = library
        # Do not block tree paint on album-art cache; warm in parallel.
        self._start_deferred_art_warm(library)
        # Rebuild with the active sort (Artist hierarchy, etc.). Progressive
        # restore used a flat path-order preview; this applies Fibonacci chunks.
        self._populate_listbox(self.library)
        self._sync_library_chrome()
        # Host GUID index may now resolve Device → Music labels.
        if self._device_serial and self._device_index_seeded:
            self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
        # Scheduled podcast catch-up may have been deferred while the index
        # was loading — run one quiet pass now that the tree is building.
        if bool(self._config.podcast_auto_enabled):
            try:
                self.win.root.after(500, self._podcast_schedule_tick_once)
            except Exception:
                pass
        logger.info(
            "%s %d tracks (roots=%s, reachable=%s)",
            kind,
            len(self.library),
            self.library.root_paths,
            self._library_root_reachable(),
        )

    def _on_scan_done(self, result: tuple[Library, str | None]) -> None:
        library, save_err = result
        self._scan_roots = []
        self._scan_display_roots = []
        self._on_library_job_done(library, kind="Scanned")
        if save_err:
            messagebox.showwarning(
                "Library Index",
                f"Library loaded but could not save index:\n{save_err}",
            )

    def _on_library_job_error(self, exc: BaseException, *, title: str) -> None:
        self._library_busy = False
        self._index_stream_active = False
        self._scan_roots = []
        self._scan_display_roots = []
        self._sync_library_chrome()
        logger.exception("%s", title)
        messagebox.showerror(title, str(exc))

    def _start_index_restore(self) -> None:
        """Background load of durable index; stream rows into the tree."""
        self._set_library_busy(True, message="Loading index…")
        self._index_stream_active = False
        self._index_stream_total = 0

        def work() -> Library | None:
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            return AppController._load_index_worker_with_report(report)

        self._bg.submit(
            work,
            on_done=lambda lib: self._on_library_job_done(lib, kind="Restored"),
            on_error=lambda e: self._on_library_job_error(
                e, title="Library index failed"
            ),
            on_progress=self._on_index_restore_progress,
            name="library-restore",
        )

    def _start_library_scan(
        self,
        roots: list[str],
        *,
        merge_existing: bool = False,
        final_roots: list[str] | None = None,
    ) -> None:
        """Background scan of *roots*; previous library kept until done.

        *merge_existing*: scan only *roots* and merge into the current library
        (used when adding a root). Full Update / remove still replace the set.
        *final_roots*: root list after the job (defaults to *roots*).
        """
        # Do not replace self.library until the worker succeeds (stale roots safe).
        roots = normalize_library_roots(roots)
        display_roots = normalize_library_roots(
            final_roots if final_roots is not None else roots
        )
        self._scan_roots = list(roots)
        self._scan_display_roots = list(display_roots)
        self._library_busy = True
        self.win.set_library_menu_state(manage_enabled=False)
        self.win.set_library_status(
            track_count=len(self.library),
            root_paths=list(display_roots),
            root_reachable=True,
            busy_message="Scanning…",
        )
        self._refresh_manage_library_dialog()

        # Snapshot for the worker (main-thread library must not be mutated).
        merge_from: Library | None = None
        if merge_existing:
            merge_from = Library(
                tracks=list(self.library.tracks),
                root_paths=list(self.library.root_paths),
            )
        snap_final = list(display_roots)

        def work() -> tuple[Library, str | None]:
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            return AppController._scan_and_save_worker(
                roots,
                report,
                merge_from=merge_from,
                final_roots=snap_final,
            )

        self._bg.submit(
            work,
            on_done=self._on_scan_done,
            on_error=lambda e: self._on_library_job_error(
                e, title="Library scan failed"
            ),
            on_progress=self._on_scan_progress,
            name="library-scan",
        )

    def _pick_library_directory(self) -> str | None:
        root = self.library.root_path
        initial = root if root else "~/Music/"
        path = filedialog.askdirectory(
            parent=self._manage_dialog_parent(),
            initialdir=initial,
            title="Select Music Library Directory",
        )
        return path or None

    def on_manage_library(self) -> None:
        """Open Library → Manage Library… (add/remove roots, update scan)."""
        existing = self._manage_library_dlg
        if existing is not None and existing.is_open():
            existing.focus()
            existing.refresh()
            return

        def _clear_ref() -> None:
            if self._manage_library_dlg is dlg:
                self._manage_library_dlg = None

        dlg = open_manage_library_dialog(
            self.win.root,
            get_roots=lambda: list(self.library.root_paths),
            on_add=self.on_add_library_root,
            on_remove=self.on_remove_library_roots,
            on_update=self.on_update_library,
            is_busy=lambda: self._library_busy or self._transfer_busy,
            can_update=self._library_root_reachable,
            on_exclusions=self.on_exclusions_manager,
            on_close=_clear_ref,
        )
        self._manage_library_dlg = dlg

    def on_exclusions_manager(self) -> None:
        """Open Exclusions Manager (from Manage Library)."""
        existing = self._exclusions_dlg
        if existing is not None and existing.is_open():
            existing.focus()
            existing.refresh()
            return

        def _clear_ref() -> None:
            if self._exclusions_dlg is dlg:
                self._exclusions_dlg = None

        dlg = ExclusionsManagerDialog(
            self._manage_dialog_parent(),
            get_exclusions=list_library_exclusions,
            on_remove=self.on_remove_exclusions,
            is_busy=lambda: self._library_busy or self._transfer_busy,
            on_close=_clear_ref,
        )
        self._exclusions_dlg = dlg

    def on_remove_exclusions(self, paths: list[str]) -> None:
        """Drop exclusion rules and re-scan affected folders (merge)."""
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Exclusions",
                "A background job is already running. Wait for it to finish.",
            )
            return
        cleaned = [os.path.normpath(p) for p in paths if (p or "").strip()]
        if not cleaned:
            return
        remove_library_exclusions(cleaned)
        # Re-scan only the affected areas so media can reappear without a
        # full library update.
        scan_targets: list[str] = []
        for p in cleaned:
            if os.path.isdir(p):
                scan_targets.append(p)
            elif os.path.isfile(p):
                scan_targets.append(os.path.dirname(p) or p)
            else:
                # Path may be gone; still try parent if under a root.
                parent = os.path.dirname(p)
                if parent:
                    scan_targets.append(parent)
        scan_targets = normalize_library_roots(scan_targets)
        # Only scan under active roots (ignore orphans).
        active = list(self.library.root_paths)
        under_roots = [
            t
            for t in scan_targets
            if any(
                t == r or t.startswith(r + os.sep)
                for r in active
            )
        ]
        if self._exclusions_dlg is not None and self._exclusions_dlg.is_open():
            self._exclusions_dlg.refresh()
        if under_roots and active:
            logger.info(
                "De-exclude → merge scan %s (roots=%s)", under_roots, active
            )
            self._start_library_scan(
                under_roots,
                merge_existing=True,
                final_roots=active,
            )
        else:
            self._sync_library_chrome()

    def action_exclude_file(self) -> None:
        """Context menu: exclude the selected track file(s)."""
        tracks = self._tracks_from_selected_iids(quiet=True)
        if not tracks:
            track = self._selected_track()
            if track is None:
                return
            tracks = [track]
        # Prefer single focused track when multi-select expands a group.
        iid = self.win.selected_tree_iid()
        if iid and iid in self._track_by_iid:
            tracks = [self._track_by_iid[iid]]
        paths = sorted({t.path for t in tracks if t.path})
        if not paths:
            return
        if len(paths) == 1:
            msg = f"Exclude this file from the library?\n\n{paths[0]}"
        else:
            msg = f"Exclude {len(paths)} files from the library?"
        if not messagebox.askyesno("Exclude File", msg):
            return
        self._apply_exclusions([(p, "file") for p in paths])

    def action_exclude_folder(self) -> None:
        """Context menu (track row): exclude the parent folder of the track."""
        track = None
        iid = self.win.selected_tree_iid()
        if iid and iid in self._track_by_iid:
            track = self._track_by_iid[iid]
        if track is None:
            tracks = self._tracks_from_selected_iids(quiet=True)
            track = tracks[0] if tracks else None
        if track is None:
            messagebox.showinfo("Exclude Folder", "Select a media file first.")
            return
        folder = os.path.dirname(track.path) or track.path
        if not messagebox.askyesno(
            "Exclude Folder",
            f"Exclude this folder and everything under it?\n\n{folder}",
        ):
            return
        self._apply_exclusions([(folder, "folder")])

    def action_exclude_group_folder(self) -> None:
        """Context menu on album/directory header: exclude that folder."""
        seed = self._context_group_seed
        iid = self.win.selected_tree_iid()
        if seed is None and iid:
            seed = self._group_seed_by_iid.get(iid)
        if seed is None:
            messagebox.showinfo(
                "Exclude Folder",
                "Select a folder or album group first.",
            )
            return
        folder = os.path.dirname(seed.path) or seed.path
        if not messagebox.askyesno(
            "Exclude Folder",
            f"Exclude this folder and everything under it?\n\n{folder}",
        ):
            return
        self._apply_exclusions([(folder, "folder")])

    def _apply_exclusions(self, entries: list[tuple[str, str]]) -> None:
        """Persist exclusions, untrack matching media, refresh trees."""
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Exclude",
                "A background job is already running. Wait for it to finish.",
            )
            return
        if not entries:
            return

        def work() -> Library:
            return exclude_library_paths(entries)

        self._library_busy = True
        self.win.set_library_menu_state(manage_enabled=False)
        self.win.set_library_status(
            track_count=len(self.library),
            root_paths=list(self.library.root_paths),
            root_reachable=self._library_root_reachable(),
            busy_message="Updating exclusions…",
        )

        def on_done(lib: Library) -> None:
            self._on_library_job_done(lib, kind="Updated")
            if self._exclusions_dlg is not None and self._exclusions_dlg.is_open():
                self._exclusions_dlg.refresh()
            if self._manage_library_dlg is not None and self._manage_library_dlg.is_open():
                self._manage_library_dlg.refresh()

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=lambda e: self._on_library_job_error(
                e, title="Exclude failed"
            ),
            name="library-exclude",
        )

    def on_add_library_root(self) -> None:
        """Folder picker → add root (if new) → scan only that root and merge."""
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Library",
                "A background job is already running. Wait for it to finish.",
            )
            return
        path = self._pick_library_directory()
        if not path:
            return
        prior = normalize_library_roots(self.library.root_paths)
        roots = normalize_library_roots([*prior, path])
        if roots == prior:
            messagebox.showinfo(
                "Manage Library",
                "That folder is already a library root.",
                parent=self._manage_dialog_parent(),
            )
            return
        # Only scan roots that were not already present (normally one folder).
        new_roots = [r for r in roots if r not in set(prior)]
        if not new_roots:
            new_roots = [roots[-1]]
        logger.info(
            "Add Library Root → scan=%s final_roots=%s (merge existing)",
            new_roots,
            roots,
        )
        self._start_library_scan(
            new_roots,
            merge_existing=True,
            final_roots=roots,
        )

    def on_remove_library_roots(self, paths: list[str]) -> None:
        """Drop roots without rescanning: mark their files untracked in the index.

        GUIDs and tags stay in SQLite (tracked=0) so device inventory can still
        resolve them and a later re-add of the same path reuses the GUID.
        """
        if self._library_busy or self._transfer_busy:
            messagebox.showinfo(
                "Library",
                "A background job is already running. Wait for it to finish.",
            )
            return
        drop = normalize_library_roots(paths)
        if not drop:
            return
        drop_set = set(drop)
        remaining = [r for r in self.library.root_paths if r not in drop_set]
        logger.info(
            "Remove Library Root(s) → untrack under %s; remaining roots=%s",
            drop,
            remaining,
        )
        self._library_busy = True
        self.win.set_library_menu_state(manage_enabled=False)
        self.win.set_library_status(
            track_count=len(self.library),
            root_paths=list(remaining),
            root_reachable=bool(remaining)
            and any(os.path.isdir(r) for r in remaining),
            busy_message="Updating library…",
        )
        self._refresh_manage_library_dialog()

        def work() -> Library:
            return untrack_library_roots(drop, final_roots=remaining)

        self._bg.submit(
            work,
            on_done=lambda lib: self._on_library_job_done(lib, kind="Updated"),
            on_error=lambda e: self._on_library_job_error(
                e, title="Remove library root failed"
            ),
            name="library-untrack",
        )

    def on_update_library(self) -> None:
        """Rescan every stored root and rewrite the index."""
        if self._library_busy or self._transfer_busy:
            return
        if not self._library_root_reachable():
            messagebox.showinfo(
                "Library",
                "Cannot update: no library root is selected or reachable.\n"
                "Use Library → Manage Library… to add a folder.",
                parent=self._manage_dialog_parent(),
            )
            return
        roots = list(self.library.root_paths)
        logger.info("Update Library → full rescan %s", roots)
        self._start_library_scan(roots, merge_existing=False)

    def _manage_dialog_parent(self):
        dlg = self._manage_library_dlg
        if dlg is not None and dlg.is_open():
            return dlg.window
        return self.win.root

    # Back-compat aliases for older call sites / mental models.
    def on_select_library_root(self) -> None:
        self.on_manage_library()

    def on_change_library(self) -> None:
        self.on_manage_library()

    def on_select_library(self, event=None) -> None:
        self.on_manage_library()


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


    def _begin_transfer_job(self, *, quiet: bool = False) -> bool:
        """Return False if a library scan or transfer already owns the pipeline.

        Concurrent non-transfer background work is allowed (e.g. podcast full-
        sync host download streaming tracks into a new transfer job). Only
        ``_transfer_busy`` / ``_library_busy`` and exclusive USB gate matter —
        do **not** treat ``_bg.busy`` as a hard block or every streamed episode
        during full-sync spams Busy dialogs while the host pass is still
        downloading.

        *quiet*: log instead of messageboxes (auto/streamed podcast path).
        """
        if self._library_busy:
            if quiet:
                logger.info("Transfer deferred: library busy")
            else:
                messagebox.showinfo(
                    "Library",
                    "Library is still loading or scanning. Try again in a moment.",
                )
            return False
        if self._transfer_busy:
            if quiet:
                logger.info("Transfer deferred: transfer already running")
            else:
                messagebox.showinfo(
                    "Busy",
                    "A transfer is already running.\n\n"
                    "Wait for it to finish, or click Cancel to stop after the "
                    "current item.",
                )
            return False
        # Exclusive USB for the whole job so auto-connect cannot probe mid-sync.
        if not self._device_io.try_acquire("transfer"):
            if quiet:
                logger.info(
                    "Transfer deferred: USB busy holder=%s",
                    self._device_io.holder or "unknown",
                )
            else:
                messagebox.showinfo(
                    "Busy",
                    "The device is busy with another USB operation "
                    f"({self._device_io.holder or 'unknown'}).\n\n"
                    "Wait for it to finish, then try again.",
                )
            return False
        self._transfer_busy = True
        self._job_cancel.clear()
        self.win.set_cancel_job_enabled(True)
        self._clear_transfer_highlights()
        try:
            self.win.progress["value"] = 0
        except Exception:
            pass
        self.win.set_progress_status("")
        return True

    def _end_transfer_job(self) -> None:
        self._transfer_busy = False
        self._job_cancel.clear()
        self._transfer_queue = None
        self.win.set_cancel_job_enabled(False)
        try:
            self.win.btn_cancel_job.configure(text="Cancel")
        except Exception:
            pass
        self._clear_transfer_highlights()
        self._stop_busy_progress()
        self._batch_track_by_path = {}
        self.win.set_progress_status("")
        # Listing/transfer just finished — release USB + pause probes.
        self._device_probe_fails = 0
        self._device_io.release(
            reason="transfer", quiet_s=_DEVICE_USB_COOLDOWN_S
        )

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
        if is_video_track(track):
            # Library Video tab / video files use the Send Video pipeline.
            self._start_send_video([track.path])
            return
        # Prefer appending to an active batch queue over a separate one-shot job.
        if self._transfer_queue is not None and self._transfer_busy:
            self._enqueue_tracks([track], kind="track", label="Selected track")
            return
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
                stems = self._device_guid_stems_for_skip()
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
                    device_guid_stems=stems,
                    on_after_send=self._on_after_send,
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
        if status in ("done", "skipped"):
            if job.mark_path_done(path):
                self._persist_sync_job()
        elif status == "failed":
            job.mark_path_failed(path)
            self._persist_sync_job()
            self._refresh_resume_menu()

    def _device_guid_stems_for_skip(self) -> set[str] | None:
        """GUID stems from durable SQLite device index (no live list_files).

        Uses ``self._device_serial`` when set (Experimental session). Stable
        Mode without a prior serial may still skip if we know a serial from
        config/session; otherwise returns empty set (send everything).
        """
        serial = self._device_serial
        if not serial:
            # No known device key — do not skip (safe default).
            logger.info("Skip-if-present: no device serial (cache unused)")
            return set()
        try:
            stems = guid_stems_on_device(serial)
            logger.info(
                "Skip-if-present: cache serial=%s guid_stems=%d complete=%s",
                serial,
                len(stems),
                device_list_is_complete(serial),
            )
            return stems
        except Exception:
            logger.warning(
                "Skip-if-present: cache read failed; sending without skip",
                exc_info=True,
            )
            return set()

    def _on_after_send(
        self, guid: str, send_path: str, object_id: int | None
    ) -> None:
        """Record device_files row after a successful send (incremental cache)."""
        if not is_track_guid(guid):
            return
        serial = self._device_serial or device_serial_key()
        _, ext = os.path.splitext(send_path)
        # Podcast audio uses ZENcast parent; music uses Music folder.
        job = self._active_sync_job
        is_podcast = bool(job and getattr(job, "kind", "") == "podcast")
        parent = self._podcast_folder_id() if is_podcast else self._music_folder_id()
        remote = build_remote_path(
            TrackMetadata(),
            ext or ".mp3",
            music_folder_id=parent,
            guid=guid,
        )
        _, basename = split_remote_path(remote)
        try:
            record_send(
                serial,
                remote_name=basename,
                guid=guid,
                item_id=object_id,
                parent_id=parent,
                storage_id=DEFAULT_STORAGE_ID,
            )
            # Debounced rebuild from cache (many sends in a batch).
            self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
        except Exception:
            logger.debug("device_index record_send failed", exc_info=True)
        # Clear pending-device flag per send so streaming batches stay accurate.
        if is_podcast:
            self._record_day_podcast_playlist_guid(guid)
            try:
                from mtpmanager.infra.podcast_index import (
                    get_episode_by_guid,
                    set_episode_pending_device_sync,
                )

                ep = get_episode_by_guid(guid)
                if ep is not None:
                    set_episode_pending_device_sync(ep.id, False)
                    if not self._config.keep_downloaded_podcasts:
                        discard_episode_local_files(ep)
            except Exception:
                logger.debug(
                    "post-send podcast episode update failed",
                    exc_info=True,
                )

    @staticmethod
    def _enrich_device_tracks_from_index(refs: list):
        """Join List Tracks basenames to host SQLite tags (GUID ObjectFileNames)."""
        guids = []
        for r in refs:
            g = guid_from_remote_name(getattr(r, "name", None))
            if g:
                guids.append(g)
        if not guids:
            return refs
        by_guid = get_tracks_by_guids(guids)
        if not by_guid:
            return refs
        return enrich_refs_from_host(refs, by_guid)

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
        """Map source paths to Track objects (library, podcast index, or tags).

        Podcast cache files often have empty embedded tags; resume must pull
        show/episode metadata (and GUID) from ``podcast_episodes`` so the
        device does not get ``Unknown Artist``.
        """
        by_path = {t.path: t for t in self.library.tracks}
        out: list[Track] = []
        for p in paths:
            if p in by_path:
                out.append(by_path[p])
                continue
            pod = self._podcast_track_for_path(p)
            if pod is not None:
                out.append(pod)
                continue
            if os.path.isfile(p):
                try:
                    meta = read_metadata(p)
                except Exception:
                    logger.warning("Could not read tags for resume path %s", p)
                    meta = TrackMetadata()
                # Prefer GUID from basename when present (device ObjectFileName).
                stem = os.path.splitext(os.path.basename(p))[0]
                guid = stem.lower() if is_track_guid(stem) else ""
                out.append(Track(path=p, meta=meta, guid=guid))
            else:
                logger.warning("Resume: skipping missing path %s", p)
        return out

    def _podcast_track_for_path(self, path: str) -> Track | None:
        """Build a transfer Track from podcast cache path or episode GUID stem."""
        p = (path or "").strip()
        if not p:
            return None
        try:
            from dataclasses import replace

            from mtpmanager.app.podcast_ops import episode_as_track
            from mtpmanager.infra.podcast_index import get_episode_by_guid

            stem = os.path.splitext(os.path.basename(p))[0]
            # Cache layout: …/podcasts/{show_id}/{guid}.mp3
            guid = ""
            if is_track_guid(stem):
                guid = stem.lower()
            else:
                g = guid_from_remote_name(os.path.basename(p))
                if g:
                    guid = g
            if not guid:
                return None
            ep = get_episode_by_guid(guid)
            if ep is None:
                return None
            show = get_podcast(int(ep.podcast_id))
            if show is None:
                return None
            # Prefer the on-disk path we were given for the send.
            if p and os.path.isfile(p) and (ep.local_path or "") != p:
                ep = replace(ep, local_path=p)
            return episode_as_track(ep, show)
        except FileNotFoundError:
            return None
        except Exception:
            logger.debug(
                "podcast track resolve failed path=%s", path, exc_info=True
            )
            return None

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

    def _enqueue_tracks(
        self,
        tracks: list[Track],
        *,
        kind: str = "batch",
        label: str = "",
    ) -> int:
        """Append tracks to the active batch queue. Returns count newly queued."""
        q = self._transfer_queue
        job = self._active_sync_job
        if q is None or job is None or not self._transfer_busy:
            return 0
        added = q.extend(tracks)
        if not added:
            logger.info(
                "Queue: no new tracks (all already queued) kind=%s", kind
            )
            return 0
        job.append_paths([t.path for t in added])
        if label and job.label and label not in job.label:
            # Keep a short combined label for the status line.
            job.label = f"{job.label} + {label}"
        elif label and not job.label:
            job.label = label
        self._persist_sync_job()
        for t in added:
            self._batch_track_by_path[t.path] = t
            self._apply_track_status(t.path, "queued")
        logger.info(
            "Queue: added %d track(s) kind=%s → %s",
            len(added),
            kind,
            job.summary_line(),
        )
        try:
            self.win.set_progress_status(
                f"Queued +{len(added)} → {job.succeeded}/{job.total} "
                f"({job.remaining} left)"
            )
        except Exception:
            pass
        return len(added)

    def _transfer_many(
        self,
        tracks: list[Track],
        fmt: str = "mp3",
        *,
        kind: str = "batch",
        label: str = "",
        resume_job: SyncJobState | None = None,
        quiet: bool = False,
    ) -> bool:
        """Start or extend a batch transfer. Returns True if work was accepted."""
        if resume_job is None:
            videos = [t for t in tracks if is_video_track(t)]
            audio = self._audio_tracks_only(list(tracks))
            # Pure video batch (folder sync, multi-select on Video tab, etc.).
            if videos and not audio:
                self._start_send_video([t.path for t in videos])
                return True
            if videos:
                logger.info(
                    "Excluding %d video file(s) from audio transfer batch "
                    "(use Sync on Video tab or Send Video)",
                    len(videos),
                )
            tracks = audio
        if not tracks:
            if not quiet:
                messagebox.showinfo(
                    "Transfer",
                    "No audio tracks to transfer.",
                )
            return False

        # Mid-job: append to the live queue instead of refusing.
        if (
            resume_job is None
            and self._transfer_busy
            and self._transfer_queue is not None
            and self._active_sync_job is not None
        ):
            n = self._enqueue_tracks(tracks, kind=kind, label=label or kind)
            if n == 0 and not quiet:
                messagebox.showinfo(
                    "Transfer queue",
                    "Those tracks are already in the transfer queue.",
                )
            return n > 0

        if not self._begin_transfer_job(quiet=quiet):
            return False

        transport = self._transport()
        transcoder = self.transcoder
        device_formats = self._device_audio_formats()
        mode = self.win.active_mode()

        if resume_job is not None:
            job = resume_job
            job.mark_running()
            # Remaining batch must align with job.paths[job.next_index:].
            self._active_sync_job = job
            batch = list(tracks)
        else:
            job = new_sync_job(
                paths=[t.path for t in tracks],
                kind=kind,
                label=label or kind,
                target_format=fmt,
                mode=mode,
            )
            self._active_sync_job = job
            batch = list(tracks)

        track_queue = BatchTransferQueue(batch)
        self._transfer_queue = track_queue
        self._persist_sync_job()
        self.win.set_resume_sync_enabled(False)
        self._mark_batch_queued(batch)
        logger.info(
            "Sync job start: %s (queue=%d)",
            job.summary_line(),
            track_queue.total(),
        )

        def work() -> int:
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def on_progress(done: int, total: int, path: str) -> None:
                report("progress", done, total, path)

            def on_track_status(src: str, status: str) -> None:
                report("track_status", src, status)

            stems = self._device_guid_stems_for_skip()
            return transfer_tracks(
                track_queue,
                target_format=fmt,
                transport=transport,
                transcoder=transcoder,
                on_progress=on_progress,
                on_track_status=on_track_status,
                resolve_parent_folder=self._parent_folder_resolver(),
                device_formats=device_formats,
                should_cancel=self._should_cancel_job,
                device_guid_stems=stems,
                on_after_send=self._on_after_send,
            )

        def on_done(succeeded: int) -> None:
            self._finish_sync_job_success()
            self._end_transfer_job()
            logger.info("Background batch finished: succeeded=%s", succeeded)
            # Phase 2 for playlist sync: MTP playlist object (Experimental).
            if kind == "playlist" or (
                self._pending_device_playlist is not None
            ):
                self._publish_pending_device_playlist()
            # Phase 2b: abstract album + cover art (ZEN: not on track objects).
            # Music and podcasts: podcast show RSS art is used when episode
            # files lack embedded covers.
            if self._should_push_album_art():
                paths = list(job.paths) if job is not None else []
                self._publish_album_art_after_sync(paths)
            if self._pending_auto_podcast is not None:
                self._finish_auto_podcast_device_batch(ok=True)

        def on_error(exc: BaseException) -> None:
            self._clear_pending_device_playlist()
            if self._pending_auto_podcast is not None:
                self._finish_auto_podcast_device_batch(ok=False)
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
        return True


    def action_sync_this_track(self) -> None:
        # If multiple tracks are selected, treat as bulk selection sync.
        selected = self._tracks_from_selected_iids(quiet=True)
        if len(selected) > 1:
            self.action_sync_selected()
            return
        track = self._selected_track()
        if track is None:
            return
        if is_video_track(track):
            self._start_send_video([track.path])
            return
        self._transfer_one(track, self._target_format())

    def action_sync_selected(self) -> None:
        """Sync multi-selected tracks (Shift/Ctrl/Cmd selection) as one job."""
        tracks = self._tracks_from_selected_iids(quiet=True)
        if not tracks:
            if not self._require_sync_ready():
                return
            messagebox.showinfo(
                "Sync Selected",
                "Select one or more tracks first.\n\n"
                "• Click a track to select it\n"
                "• Shift+click for a range\n"
                "• Ctrl+click (Windows/Linux) or ⌘+click (macOS) to toggle\n"
                "• Group headers include all tracks under that group",
            )
            return
        videos = [t for t in tracks if is_video_track(t)]
        audio = self._audio_tracks_only(tracks)
        # Video selection → Send Video (same dialog as Device → Send Video…).
        if videos and not audio:
            self._start_send_video([t.path for t in videos])
            return
        if not self._require_sync_ready():
            return
        if len(audio) == 1 and not videos:
            self._transfer_one(audio[0], self._target_format())
            return
        if not audio:
            return
        n = len(audio)
        fmt = self._target_format().upper()
        mode = (
            "Stable (mtp-sendtr)"
            if self.win.active_mode() == "stable"
            else "PyMTP"
        )
        note = ""
        if videos:
            note = (
                f"\n\n({len(videos)} video file(s) skipped — "
                "select them alone to use Send Video.)"
            )
        if not messagebox.askyesno(
            "Sync Selected Tracks",
            f"Send {n} selected track(s) as {fmt} using {mode}?\n\n"
            "Progress is saved; use Transfer → Resume Sync after a failure."
            f"{note}",
        ):
            return
        self._transfer_many(
            audio,
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
        """Context menu on an album or directory header row."""
        seed = self._context_group_seed
        iid = self.win.selected_tree_iid()
        if seed is None:
            seed = self._group_seed_by_iid.get(iid or "")
        kind = "album"
        if iid:
            try:
                tags = set(self.win.active_library_tree().item(iid, "tags"))
            except Exception:
                tags = set()
            if "group_directory" in tags:
                kind = "directory"
        self._sync_from_seed(seed, kind=kind)

    def action_entire_library(self) -> None:
        if not self._require_sync_ready():
            return
        if not self.library.tracks:
            messagebox.showinfo("Library", "Load a library first.")
            return
        tracks = self._audio_tracks_only(list(self.library.tracks))
        tracks.sort(key=lambda t: t.path)
        n = len(tracks)
        if n == 0:
            messagebox.showinfo(
                "Library",
                "No audio tracks to sync.\n\n"
                "Video files use Device → Send Video….",
            )
            return
        fmt = self._target_format().upper()
        if not messagebox.askyesno(
            "Sync Entire Library",
            f"Send all {n} audio track(s) as {fmt} using "
            f"{'Stable (mtp-sendtr)' if self.win.active_mode() == 'stable' else 'PyMTP'}?\n\n"
            "Video files are not included (use Device → Send Video…).\n"
            "This may take a long time.\n"
            "Progress is saved; use Transfer → Resume Sync after a failure.",
        ):
            return
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

    def action_send_video(self) -> None:
        """Device → Send Video… — pick file, then shared send pipeline."""
        if not self._require_device_ready():
            return
        if self._transfer_busy:
            messagebox.showinfo(
                "Send Video",
                "A transfer or device job is already in progress.",
            )
            return
        path = filedialog.askopenfilename(
            title="Select video file to send",
            initialdir=self.library.root_path or os.path.expanduser("~"),
            filetypes=[
                (
                    "Video files",
                    "*.wmv *.avi *.mpg *.mpeg *.asf *.mp4 *.mov *.m4v *.qt",
                ),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._start_send_video([path])

    def _start_send_video(self, paths: list[str]) -> None:
        """Send Video dialog + encode/send for one or more host video paths.

        Used by Device → Send Video… and by library Video tab sync actions.
        """
        if not self._require_device_ready():
            return
        if self._transfer_busy:
            messagebox.showinfo(
                "Send Video",
                "A transfer or device job is already in progress.",
            )
            return
        files = [
            os.path.normpath(p)
            for p in paths
            if p and os.path.isfile(p)
        ]
        # Dedupe while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for p in files:
            if p in seen:
                continue
            seen.add(p)
            unique.append(p)
        files = unique
        if not files:
            messagebox.showerror(
                "Send Video",
                "No video files found to send.",
            )
            return

        # Library tracks keep GUIDs in the host index (and device_files after
        # send) for skip-if-present / future joins. ObjectFileName is always
        # title/basename style — never the GUID wire name used for music.
        by_path = {
            os.path.normpath(t.path): t for t in self.library.tracks if t.path
        }
        guid_by_path: dict[str, str] = {}
        title_by_path: dict[str, str] = {}
        basename_by_path: dict[str, str] = {}
        for p in files:
            track = by_path.get(p)
            base = os.path.basename(p)
            stem = os.path.splitext(base)[0] or "video"
            if track is not None:
                g = track.guid if is_track_guid(track.guid) else ""
                if not g:
                    g = new_track_guid()
                    # Keep in-memory library identity stable for this session.
                    idx = next(
                        (
                            i
                            for i, t in enumerate(self.library.tracks)
                            if t.path == track.path
                        ),
                        None,
                    )
                    if idx is not None:
                        old = self.library.tracks[idx]
                        self.library.tracks[idx] = Track(
                            path=old.path, meta=old.meta, guid=g
                        )
                guid_by_path[p] = g
                display = video_display_title(track)
                title_by_path[p] = os.path.splitext(display)[0] or stem
                # Prefer filename stem for ObjectFileName (tags often empty).
                basename_by_path[p] = base
            else:
                title_by_path[p] = stem
                basename_by_path[p] = base

        # Skip library videos already recorded on this device (GUID index).
        stems = self._device_guid_stems_for_skip() or set()
        if stems and guid_by_path:
            kept: list[str] = []
            skipped_n = 0
            for p in files:
                g = guid_by_path.get(p)
                if g and g in stems:
                    skipped_n += 1
                    continue
                kept.append(p)
            if skipped_n:
                logger.info(
                    "Send Video: skip %d already on device (GUID index)",
                    skipped_n,
                )
            files = kept
            if not files:
                messagebox.showinfo(
                    "Send Video",
                    f"All {skipped_n} selected video(s) are already on the "
                    "device (matched by library GUID in the device index).",
                )
                return

        video_options = None
        if self._active_profile is not None:
            video_options = self._active_profile.video_options

        if len(files) == 1:
            dlg_name = os.path.basename(files[0])
        else:
            dlg_name = f"{len(files)} video files"

        layout = self._folder_layout_or_legacy()
        opts = ask_video_destination(
            self.win.root,
            filename=dlg_name,
            video_options=video_options,
            encode_default=True,
            include_broken_presets=bool(
                self._config.show_broken_video_presets
            ),
            video_folder_id=layout.video_id,
            tv_folder_id=layout.tv_id,
            video_folder_name=layout.name_for(layout.video_id) or "Video",
            tv_folder_name=layout.name_for(layout.tv_id) or "TV",
        )
        if opts is None:
            return
        parent = int(opts.parent_id)
        encode = bool(opts.encode_for_device) and video_options is not None
        preset = None
        if encode and video_options is not None:
            preset = video_options.preset_by_id(opts.preset_id)
            if preset is None:
                preset = video_options.default_preset()
        ignore_max_fps = bool(opts.ignore_max_fps) and encode and preset is not None
        folder_label = (
            layout.video_folder_label(parent)
            if parent
            else layout.name_for(parent) or str(parent)
        )
        if encode and preset is not None:
            encode_note = f"Encode: {preset.display_name}\n"
            if ignore_max_fps:
                encode_note += (
                    "Max fps cap: ignored (experimental — may not play)\n"
                )
            elif float(preset.max_fps or 0) > 0:
                encode_note += f"Max fps cap: {preset.max_fps:g}\n"
        else:
            encode_note = "Encode: off (send as-is)\n"

        name_note = (
            "Object name: sanitized title / host basename "
            "(library GUID kept in the host index only)."
        )

        if len(files) == 1:
            confirm = (
                f"Send this file to the device {folder_label} folder?\n\n"
                f"{files[0]}\n\n"
                f"Parent folder id: {parent}\n"
                f"{encode_note}"
                f"{name_note}"
            )
        else:
            listing = "\n".join(os.path.basename(p) for p in files[:12])
            if len(files) > 12:
                listing += f"\n… and {len(files) - 12} more"
            confirm = (
                f"Send {len(files)} files to the device {folder_label} folder?\n\n"
                f"{listing}\n\n"
                f"Parent folder id: {parent}\n"
                f"{encode_note}"
                f"{name_note}"
            )
        if not messagebox.askyesno("Send Video", confirm):
            return

        transport = self._transport()
        serial = self._device_serial or device_serial_key()
        batch = list(files)
        guid_map = dict(guid_by_path)
        title_map = dict(title_by_path)
        basename_map = dict(basename_by_path)
        do_encode = encode and preset is not None

        def work(device):
            _ = device
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            results = []
            total = len(batch)
            for i, path in enumerate(batch):
                if self._should_cancel_job():
                    from mtpmanager.app.cancellation import JobCancelled

                    raise JobCancelled("Send Video cancelled")

                def on_progress(kind: str, *args, _i=i, _t=total) -> None:
                    # Prefix multi-file index on status lines.
                    if kind == "status" and args and _t > 1:
                        report(
                            "status",
                            f"({_i + 1}/{_t}) {args[0]}",
                        )
                        return
                    if kind == "phase" and _t > 1:
                        report(kind, *args)
                        report(
                            "status",
                            f"Video {_i + 1}/{_t}: {os.path.basename(batch[_i])}",
                        )
                        return
                    report(kind, *args)

                if total > 1:
                    report(
                        "status",
                        f"Video {i + 1}/{total}: {os.path.basename(path)}",
                    )
                results.append(
                    device_ops.prepare_and_send_video(
                        transport,
                        path,
                        parent_id=parent,
                        encode_profile=preset,
                        encode_for_device=do_encode,
                        ignore_max_fps=ignore_max_fps,
                        on_progress=on_progress,
                        title=title_map.get(path),
                        preferred_basename=basename_map.get(path),
                        guid=guid_map.get(path),
                        allowed_parents=layout.video_parent_ids(),
                    )
                )
            return results

        def on_ui_event(kind: str, *rest) -> None:
            if kind == "phase":
                phase = str(rest[0]) if rest else ""
                if phase == "transcode":
                    try:
                        self.win.progress.configure(mode="determinate")
                        self.win.progress["value"] = 0
                    except Exception:
                        pass
                    self.win.set_progress_status("Encoding for device…")
                elif phase == "send":
                    self.win.set_progress_status("Sending to device…")
                return
            if kind == "progress":
                if len(rest) >= 3:
                    done, total, label = int(rest[0]), int(rest[1]), str(rest[2])
                    try:
                        self.win.progress.configure(mode="determinate")
                        if total > 0:
                            self.win.progress["value"] = max(
                                0, min(100, int(round(100 * done / total)))
                            )
                    except Exception:
                        pass
                    if label:
                        self.win.set_progress_status(label)
                return
            if kind == "status":
                if rest:
                    self.win.set_progress_status(str(rest[0]))
                return
            self._on_transfer_ui_event(kind, *rest)

        def on_success(results) -> None:
            if not isinstance(results, list):
                results = [results]
            # Persist any GUIDs assigned for library videos (index only; not
            # ObjectFileName). Enables skip-if-present on later syncs.
            if guid_map:
                try:
                    save_library_index(self.library)
                except Exception:
                    logger.debug(
                        "save_library_index after video send failed",
                        exc_info=True,
                    )
            for result in results:
                src = os.path.normpath(result.source_path or result.path or "")
                g = guid_map.get(src)
                try:
                    record_send(
                        serial,
                        remote_name=result.remote_basename,
                        guid=g,
                        item_id=result.object_id,
                        parent_id=result.parent_id,
                        storage_id=DEFAULT_STORAGE_ID,
                    )
                except Exception:
                    logger.debug(
                        "device_index record_send after video failed",
                        exc_info=True,
                    )
                logger.info(
                    "Send Video ok path=%s parent=%s object_id=%s remote=%s "
                    "guid=%s encoded=%s skipped_ok=%s",
                    result.path,
                    result.parent_id,
                    result.object_id,
                    result.remote_basename,
                    g or "",
                    result.encoded,
                    result.encode_skipped_compatible,
                )
            try:
                self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
            except Exception:
                pass

            if len(results) == 1:
                result = results[0]
                oid_s = (
                    f" object id={result.object_id}" if result.object_id else ""
                )
                if result.encoded:
                    how = "encoded for device, then sent"
                elif result.encode_skipped_compatible:
                    how = "already device-compatible (encode skipped)"
                else:
                    how = "sent as-is"
                messagebox.showinfo(
                    "Send Video",
                    f"Sent to {folder_label} (folder {result.parent_id})."
                    f"{oid_s}\n\n{result.remote_basename}\n\n({how})",
                )
            else:
                messagebox.showinfo(
                    "Send Video",
                    f"Sent {len(results)} file(s) to {folder_label} "
                    f"(folder {parent}).",
                )

        self._run_device_bg(
            title="Send Video",
            name="send-video",
            work=work,
            on_success=on_success,
            busy_message=(
                f"preparing/sending video to {folder_label}…"
                if do_encode
                else f"sending video to {folder_label}…"
            ),
            on_progress=on_ui_event,
            progress_mode="determinate",
        )

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
        """Experimental Device → List Files (live get_filelisting)."""

        def on_success(files) -> None:
            logger.info("List Files (live): %d object(s)", len(files))
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
            # Best-effort: refresh durable skip index from this live snapshot.
            serial = self._device_serial or device_serial_key()
            try:
                replace_device_listing(serial, files, source="list")
                self._device_index_seeded = True
                logger.info(
                    "Device index updated from live List Files serial=%s n=%d",
                    serial,
                    len(files),
                )
            except Exception:
                logger.debug(
                    "device index update after List Files failed",
                    exc_info=True,
                )

        self._run_device_bg(
            title="Files",
            name="list-files",
            work=lambda device: device_ops.list_files(device),
            on_success=on_success,
            busy_message="listing device files (live get_filelisting)…",
        )

    def action_package_retail_demos(self) -> None:
        """Zip Creative-looking export tracks + reduced restore_map.json.

        Offline — no device required. Source is a Get Tracks export folder
        (device_media_map.json + media files).
        """
        if self._transfer_busy:
            messagebox.showinfo(
                "Transfer",
                "A transfer or device job is already in progress.",
            )
            return
        export_dir = filedialog.askdirectory(
            title="Select Get Tracks export folder (with device_media_map.json)",
            initialdir=self.library.root_path or os.path.expanduser("~/Music"),
        )
        if not export_dir:
            return
        map_path = os.path.join(export_dir, "device_media_map.json")
        if not os.path.isfile(map_path):
            messagebox.showerror(
                "Package Retail Demos",
                f"No device_media_map.json in:\n{export_dir}\n\n"
                "Run Device → Get Tracks from Device… first, then select that folder.",
            )
            return
        zip_path = filedialog.asksaveasfilename(
            title="Save retail demo package as",
            defaultextension=".zip",
            initialfile="creative_retail_demos.zip",
            initialdir=export_dir,
            filetypes=[("ZIP archive", "*.zip"), ("All files", "*.*")],
        )
        if not zip_path:
            return
        if not messagebox.askyesno(
            "Package Retail Demos",
            "This will copy only tracks flagged as Creative retail/demo\n"
            f"(looks_like_retail_demo) from:\n\n{export_dir}\n\n"
            f"into:\n{zip_path}\n\n"
            "Includes a reduced restore_map.json for later restore. Continue?",
        ):
            return

        if not self._begin_transfer_job():
            return

        def work():
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def progress(done: int, total: int, label: str) -> None:
                report(
                    "status",
                    f"packaging {done + 1}/{total}  {label}"
                    if total and done < total
                    else f"packaged {done}/{total}",
                )
                if total and total > 0:
                    report("progress", done, total, label)

            return retail_ops.package_retail_from_export(
                export_dir, zip_path, on_progress=progress
            )

        def on_done(result) -> None:
            self._end_transfer_job()
            try:
                self.win.progress["value"] = 100
            except Exception:
                pass
            mb = result.total_bytes / (1024 * 1024) if result.total_bytes else 0
            messagebox.showinfo(
                "Package Retail Demos",
                f"Packaged {result.entry_count} retail/demo file(s) "
                f"({mb:.1f} MiB).\n\n"
                f"Zip:\n{result.zip_path}\n\n"
                "Map inside zip: restore_map.json\n"
                "Edit desired_tags / include_in_restore before restore if needed.",
            )
            logger.info(
                "Retail package done entries=%s zip=%s",
                result.entry_count,
                result.zip_path,
            )

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            logger.exception("Package Retail Demos failed")
            messagebox.showerror("Package Retail Demos", str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="package-retail-demos",
        )

    def action_restore_retail_package(self) -> None:
        """Send a retail package zip onto the connected player (no GUID names)."""
        if not self._require_experimental_connected():
            return
        if self._transfer_busy:
            messagebox.showinfo(
                "Transfer",
                "A transfer or device job is already in progress.",
            )
            return
        package = filedialog.askopenfilename(
            title="Select retail demo package (.zip)",
            initialdir=self.library.root_path or os.path.expanduser("~/Music"),
            filetypes=[
                ("Retail package ZIP", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not package:
            return
        # Quick map peek for confirmation count
        from mtpmanager.infra.retail_package import (
            entries_for_restore,
            load_package_map,
        )

        peek = load_package_map(package)
        if peek is None:
            messagebox.showerror(
                "Restore Retail Package",
                "Could not read restore_map.json from the selected file.\n"
                "Use Transfer → Package Retail Demos… to create a package.",
            )
            return
        n = len(entries_for_restore(peek))
        if n == 0:
            messagebox.showinfo(
                "Restore Retail Package",
                "Package has no entries with include_in_restore=true.",
            )
            return
        if not messagebox.askyesno(
            "Restore Retail Package",
            f"Send {n} retail/demo file(s) to the player?\n\n"
            f"Package:\n{package}\n\n"
            "Uses original short ObjectFileNames (not GUIDs) and tags from\n"
            "restore_map.json desired_tags. Aborts remaining files on fatal\n"
            "transport error. Continue?",
        ):
            return

        if not self._begin_transfer_job():
            return
        transport = self._transport()

        def work():
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def progress(done: int, total: int, label: str) -> None:
                report(
                    "status",
                    f"restoring {done + 1}/{total}  {label}"
                    if total and done < total
                    else f"restored {done}/{total}",
                )
                if total and total > 0:
                    report("progress", done, total, label)

            return retail_ops.restore_retail_package(
                transport,
                package,
                on_progress=progress,
                should_cancel=self._should_cancel_job,
                stop_on_fatal=True,
            )

        def on_done(result) -> None:
            self._end_transfer_job()
            try:
                self.win.progress["value"] = 100
            except Exception:
                pass
            if result.cancelled:
                messagebox.showinfo(
                    "Restore cancelled",
                    f"Stopped after {result.succeeded} of {result.total} file(s).",
                )
                return
            if result.aborted:
                messagebox.showerror(
                    "Restore aborted",
                    f"Sent {result.succeeded} of {result.total}.\n"
                    f"Stopped at: {result.failed_label or '—'}\n\n"
                    + (
                        "\n".join(result.errors[:3])
                        if result.errors
                        else "Fatal transport error."
                    )
                    + "\n\nSession may be poisoned — disconnect/replug if needed.",
                )
                return
            messagebox.showinfo(
                "Restore Retail Package",
                f"Sent {result.succeeded} of {result.total} file(s)"
                f"{f' ({result.failed} failed)' if result.failed else ''}.",
            )
            logger.info(
                "Retail restore done succeeded=%s failed=%s total=%s",
                result.succeeded,
                result.failed,
                result.total,
            )

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, JobCancelled):
                self._handle_job_cancelled(exc, title="Restore cancelled")
                return
            if isinstance(exc, TransportError):
                self._log_transport_error("Retail restore failed", exc)
                messagebox.showerror(
                    "Restore Retail Package",
                    f"{exc}\n\n{self._transfer_recovery_hint()}",
                )
                return
            logger.exception("Restore Retail Package failed")
            messagebox.showerror("Restore Retail Package", str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="restore-retail-package",
        )

    def action_get_tracks_from_device(self) -> None:
        """Experimental: download all media tracks (+ tags) to a host folder.

        Uses mtp-tracks-style listing, then ``get_file_to_file`` per object
        (audio and video). Best-effort mutagen write when device tags exist.
        """
        if not self._require_device_ready():
            return
        dest = filedialog.askdirectory(
            title="Save retrieved tracks to folder",
            initialdir=self.library.root_path or os.path.expanduser("~/Music"),
        )
        if not dest:
            return
        if not messagebox.askyesno(
            "Get Tracks from Device",
            "This will list media on the device (with tags where available),\n"
            "then download each file to:\n\n"
            f"{dest}\n\n"
            "Includes audio and video when listed as tracks. Continue?",
        ):
            return

        if not self._begin_transfer_job():
            return
        device = self.device

        def work():
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def list_progress(done: int, total: int, message: str) -> None:
                report("status", message or "listing…")
                if total and total > 0:
                    report("progress", done, total, message)

            report("status", "listing device tracks…")
            refs = device_ops.list_tracks(device, on_progress=list_progress)
            if not refs:
                return device_ops.RetrieveTracksResult(
                    total=0, succeeded=0, failed=0, paths=[]
                )

            def dl_progress(done: int, total: int, current) -> None:
                label = ""
                if current is not None:
                    label = (
                        (current.title or current.name or "").strip()
                        or f"id={current.item_id}"
                    )
                report(
                    "status",
                    f"downloading {done + 1}/{total}  {label}"
                    if done < total
                    else f"downloaded {done}/{total}",
                )
                report("progress", done, total, label)

            try:
                identity = device_ops.get_device_identity(device)
            except Exception:
                identity = None

            return device_ops.retrieve_tracks(
                device,
                refs,
                dest,
                on_progress=dl_progress,
                should_cancel=self._should_cancel_job,
                write_tags=True,
                device_info=identity,
                write_map=True,
            )

        def on_done(result) -> None:
            self._end_transfer_job()
            try:
                self.win.progress["value"] = 100
            except Exception:
                pass
            map_line = ""
            if getattr(result, "map_json_path", ""):
                map_line = (
                    f"\n\nEditable map:\n{result.map_json_path}"
                    f"\n(Readable study copy: {result.map_md_path or '—'})"
                )
            if result.cancelled:
                messagebox.showinfo(
                    "Get Tracks cancelled",
                    f"Stopped after {result.succeeded} of {result.total} "
                    f"file(s).\nSaved under:\n{dest}{map_line}",
                )
                return
            if result.aborted:
                messagebox.showerror(
                    "Get Tracks aborted",
                    f"Downloaded {result.succeeded} of {result.total}.\n"
                    f"Stopped at object id={result.failed_id}.\n\n"
                    f"Folder:\n{dest}{map_line}\n\n"
                    "Session may be poisoned — disconnect/replug if needed.",
                )
                return
            if result.total == 0:
                messagebox.showinfo(
                    "Get Tracks from Device",
                    "No media tracks found on the device.",
                )
                return
            messagebox.showinfo(
                "Get Tracks from Device",
                f"Downloaded {result.succeeded} of {result.total} file(s)"
                f"{f' ({result.failed} failed)' if result.failed else ''}.\n\n"
                f"Saved under:\n{dest}{map_line}",
            )
            logger.info(
                "Get Tracks done succeeded=%s failed=%s dest=%s map=%s",
                result.succeeded,
                result.failed,
                dest,
                getattr(result, "map_json_path", ""),
            )

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, JobCancelled):
                self._handle_job_cancelled(
                    exc, title="Get Tracks cancelled"
                )
                return
            logger.exception("Get Tracks from Device failed")
            messagebox.showerror("Get Tracks from Device", str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="get-tracks-from-device",
        )

    def action_read_track_list(self) -> None:
        """Experimental Device → List Tracks (filelisting + Get_Trackmetadata)."""

        def on_success(tracks) -> None:
            logger.info(
                "List Tracks (mtp-tracks style): %d track(s)",
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

            # Soft-fill empty titles from host GUID library when present.
            tracks = self._enrich_device_tracks_from_index(tracks)
            show_track_list_dialog(
                self.win.root,
                tracks,
                on_load_tags=on_load_tags,
            )

        def work(device):
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def on_progress(done: int, total: int, message: str) -> None:
                report("status", message or "listing tracks…")
                if total and total > 0:
                    report("progress", done, total, message)

            return device_ops.list_tracks(device, on_progress=on_progress)

        self._run_device_bg(
            title="Tracks",
            name="list-tracks",
            work=work,
            on_success=on_success,
            busy_message=(
                "listing files + loading track tags "
                "(mtp-tracks style; scales with track count)…"
            ),
            progress_mode="determinate",
        )

    @staticmethod
    def _item_id_from_device_track(track: Track) -> int | None:
        """Parse MTP object id from synthetic ``device:<id>:…`` path."""
        path = track.path or ""
        if not path.startswith("device:"):
            return None
        parts = path.split(":", 2)
        if len(parts) < 2:
            return None
        try:
            oid = int(parts[1])
        except (TypeError, ValueError):
            return None
        return oid if oid > 0 else None

    def _device_track_map_for_tree(self, tree) -> dict[str, Track]:
        if tree is self.win.device_video_tree:
            return self._device_video_track_by_iid
        if tree is self.win.device_audiobooks_tree:
            return self._device_audiobook_track_by_iid
        if tree is self.win.device_podcasts_tree:
            return self._device_podcast_track_by_iid
        return self._device_track_by_iid

    def _device_refs_by_item_id(self) -> dict[int, DeviceTrackRef]:
        by_id: dict[int, DeviceTrackRef] = {}
        for refs in (
            self._device_music_refs,
            self._device_video_refs,
            self._device_audiobook_refs,
            self._device_podcast_refs,
        ):
            for ref in refs or ():
                oid = int(getattr(ref, "item_id", 0) or 0)
                if oid > 0:
                    by_id[oid] = ref
        return by_id

    def _device_tracks_under_iid(self, tree, iid: str) -> list[Track]:
        """Collect track rows under a group (or the row itself if a track)."""
        by_iid = self._device_track_map_for_tree(tree)
        out: list[Track] = []
        seen: set[str] = set()

        def walk(node: str) -> None:
            track = by_iid.get(node)
            if track is not None:
                key = track.path
                if key not in seen:
                    seen.add(key)
                    out.append(track)
                return
            try:
                children = tree.get_children(node)
            except Exception:
                return
            for child in children:
                walk(child)

        walk(iid)
        return out

    def _device_tracks_from_tree_selection(self, tree=None) -> list[Track]:
        tree = tree if tree is not None else self.win.active_device_tree()
        by_iid = self._device_track_map_for_tree(tree)
        out: list[Track] = []
        seen: set[str] = set()
        try:
            selection = list(tree.selection())
        except Exception:
            selection = []
        # Prefer the right-clicked row when selection is empty.
        if not selection and self._device_context_row:
            selection = [self._device_context_row]
        for iid in selection:
            for track in self._device_tracks_under_iid(tree, iid):
                if track.path in seen:
                    continue
                seen.add(track.path)
                out.append(track)
            # Direct track map lookup if under_iid missed.
            direct = by_iid.get(iid)
            if direct is not None and direct.path not in seen:
                seen.add(direct.path)
                out.append(direct)
        return out

    def _device_refs_for_tracks(
        self, tracks: list[Track]
    ) -> list[DeviceTrackRef]:
        by_id = self._device_refs_by_item_id()
        refs: list[DeviceTrackRef] = []
        seen: set[int] = set()
        for track in tracks:
            oid = self._item_id_from_device_track(track)
            if oid is None or oid in seen:
                continue
            seen.add(oid)
            ref = by_id.get(oid)
            if ref is not None:
                refs.append(ref)
            else:
                # Minimal ref from synthetic path so delete/pull still works.
                name = ""
                parts = (track.path or "").split(":", 2)
                if len(parts) >= 3:
                    name = os.path.basename(parts[2]) or parts[2]
                refs.append(
                    DeviceTrackRef(
                        item_id=oid,
                        name=name or f"id={oid}",
                        title=(track.meta.title or "").strip(),
                        artist=(track.meta.artist or "").strip(),
                        album=(track.meta.album or "").strip(),
                        date=(track.meta.date or "").strip(),
                        tracknumber=(track.meta.tracknumber or "").strip(),
                        genre=(track.meta.genre or "").strip(),
                    )
                )
        return refs

    def _delete_device_objects(
        self,
        refs: list[DeviceTrackRef],
        *,
        title: str,
        confirm: str,
    ) -> None:
        """Confirm and batch-delete *refs* from the connected device."""
        if not self._require_device_ready():
            return
        if self._transfer_busy:
            messagebox.showinfo(
                title,
                "A transfer or device job is already in progress.",
            )
            return
        if not refs:
            messagebox.showinfo(title, "No objects selected.")
            return
        n = len(refs)
        if not messagebox.askyesno(
            title,
            confirm,
            icon=messagebox.WARNING,
            default=messagebox.NO,
        ):
            return
        if n >= 10 and not messagebox.askyesno(
            f"{title} — confirm",
            f"Really permanently delete {n} object(s) from the device?",
            icon=messagebox.WARNING,
            default=messagebox.NO,
        ):
            return

        if not self._begin_transfer_job():
            return
        device = self.device
        batch = list(refs)
        serial = self._device_serial or device_serial_key()

        def work():
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            deleted = 0
            deleted_ids: list[int] = []
            failed_id = None
            aborted = False
            total = len(batch)
            for i, ref in enumerate(batch):
                if self._should_cancel_job():
                    raise JobCancelled(f"{title} cancelled")
                oid = int(ref.item_id or 0)
                label = (ref.name or ref.title or f"id={oid}").strip()
                report("progress", i, total, label)
                try:
                    device_ops.delete_object(device, oid)
                except Exception as exc:
                    from mtpmanager.ports.transport import TransportError

                    logger.exception("device delete failed id=%s", oid)
                    if isinstance(exc, TransportError) and exc.fatal:
                        failed_id = oid
                        aborted = True
                        break
                    failed_id = oid
                    aborted = True
                    break
                deleted += 1
                deleted_ids.append(oid)
            report("progress", total, total, "done")
            return {
                "deleted": deleted,
                "total": total,
                "deleted_ids": deleted_ids,
                "failed_id": failed_id,
                "aborted": aborted,
            }

        def on_done(result) -> None:
            self._end_transfer_job()
            for oid in result.get("deleted_ids") or ():
                try:
                    remove_by_item_id(serial, int(oid))
                except Exception:
                    logger.debug(
                        "device_index remove after delete failed id=%s",
                        oid,
                        exc_info=True,
                    )
            self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
            if result.get("aborted"):
                messagebox.showerror(
                    f"{title} aborted",
                    f"Deleted {result['deleted']} of {result['total']} "
                    f"object(s).\nStopped at object id={result.get('failed_id')}.",
                )
                return
            messagebox.showinfo(
                title,
                f"Deleted {result['deleted']} of {result['total']} object(s).",
            )

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, JobCancelled):
                self._handle_job_cancelled(exc, title=f"{title} cancelled")
                return
            logger.exception("%s failed", title)
            messagebox.showerror(title, str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name="device-delete-selection",
        )

    def action_device_delete_selected(self) -> None:
        """Context menu: delete selected on-device track/video object(s)."""
        tree = self._device_context_tree or self.win.active_device_tree()
        tracks = self._device_tracks_from_tree_selection(tree)
        refs = self._device_refs_for_tracks(tracks)
        n = len(refs)
        if n == 1:
            r = refs[0]
            name = (r.name or r.title or f"id={r.item_id}").strip()
            confirm = (
                f"Delete this object from the device?\n\n"
                f"{name}\n(id={r.item_id})\n\n"
                "This cannot be undone from the app."
            )
        else:
            confirm = (
                f"Delete {n} selected object(s) from the device?\n\n"
                "This cannot be undone from the app."
            )
        self._delete_device_objects(
            refs, title="Delete from device", confirm=confirm
        )

    def action_device_delete_artist_group(self) -> None:
        """Context menu: delete all tracks under a device artist group."""
        tree = self._device_context_tree or self.win.device_tree
        iid = self._device_context_row
        if not iid:
            messagebox.showinfo("Delete artist", "No artist group selected.")
            return
        tracks = self._device_tracks_under_iid(tree, iid)
        refs = self._device_refs_for_tracks(tracks)
        try:
            values = tree.item(iid, "values") or ()
            label = str(values[0] if values else "Artist").strip() or "Artist"
        except Exception:
            label = "Artist"
        self._delete_device_objects(
            refs,
            title="Delete artist",
            confirm=(
                f"Delete all {len(refs)} track(s) from artist “{label}” "
                f"on the device?\n\nThis cannot be undone from the app."
            ),
        )

    def action_device_delete_album_group(self) -> None:
        """Context menu: delete all tracks under a device album group."""
        tree = self._device_context_tree or self.win.device_tree
        iid = self._device_context_row
        if not iid:
            messagebox.showinfo("Delete album", "No album group selected.")
            return
        tracks = self._device_tracks_under_iid(tree, iid)
        refs = self._device_refs_for_tracks(tracks)
        try:
            values = tree.item(iid, "values") or ()
            label = str(values[0] if values else "Album").strip() or "Album"
        except Exception:
            label = "Album"
        self._delete_device_objects(
            refs,
            title="Delete album",
            confirm=(
                f"Delete all {len(refs)} track(s) from album “{label}” "
                f"on the device?\n\nThis cannot be undone from the app."
            ),
        )

    def action_device_delete_folder_group(self) -> None:
        """Context menu: delete all videos under a device Video/TV folder."""
        tree = self._device_context_tree or self.win.device_video_tree
        iid = self._device_context_row
        if not iid:
            messagebox.showinfo("Delete folder", "No folder selected.")
            return
        tracks = self._device_tracks_under_iid(tree, iid)
        refs = self._device_refs_for_tracks(tracks)
        try:
            values = tree.item(iid, "values") or ()
            label = str(values[0] if values else "folder").strip() or "folder"
        except Exception:
            label = "folder"
        self._delete_device_objects(
            refs,
            title="Delete folder",
            confirm=(
                f"Delete all {len(refs)} item(s) under “{label}” on the "
                f"device?\n\nThis cannot be undone from the app."
            ),
        )

    def action_device_pull_selected(self) -> None:
        """Context menu: download selected device objects into the library."""
        self._start_device_pull(to_library=True)

    def action_device_pull_to_folder(self) -> None:
        """Context menu: download selected device objects to a chosen folder.

        Same download / tag-recovery path as Pull to library, but does **not**
        add files to the library index. Destination is chosen via folder dialog.
        """
        self._start_device_pull(to_library=False)

    def _start_device_pull(self, *, to_library: bool) -> None:
        """Download selected device objects under *dest_root*.

        *to_library*: write into a library root and index the tracks.
        Otherwise prompt for a folder and leave the library unchanged.
        """
        title = "Pull to library" if to_library else "Pull to folder"
        if not self._require_device_ready():
            return
        if self._transfer_busy:
            messagebox.showinfo(
                title,
                "A transfer or device job is already in progress.",
            )
            return

        tree = self._device_context_tree or self.win.active_device_tree()
        tracks = self._device_tracks_from_tree_selection(tree)
        refs = self._device_refs_for_tracks(tracks)
        if not refs:
            messagebox.showinfo(title, "No objects selected.")
            return

        if to_library:
            roots = normalize_library_roots(self.library.root_paths)
            if not roots and self.library.root_path:
                roots = normalize_library_roots([self.library.root_path])
            dest_root = device_ops.pick_library_root(roots)
            if not dest_root:
                messagebox.showerror(
                    title,
                    "No library root is configured.\n\n"
                    "Use Library → Manage Library… to add a root first.",
                )
                return
        else:
            initial = ""
            roots = normalize_library_roots(self.library.root_paths)
            if not roots and self.library.root_path:
                roots = normalize_library_roots([self.library.root_path])
            if roots:
                initial = roots[0]
            if not initial:
                initial = os.path.expanduser("~")
            dest_root = filedialog.askdirectory(
                title="Choose folder for pulled files",
                initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
                mustexist=True,
                parent=self.win.root,
            )
            if not dest_root:
                return
            dest_root = os.path.abspath(dest_root)
            if not os.path.isdir(dest_root):
                messagebox.showerror(
                    title, f"Folder does not exist:\n{dest_root}"
                )
                return

        n = len(refs)
        layout_note = (
            "Paths use Artist → Album → Title from device tags when available "
            "(embedded file tags recovered if the device only has placeholders)."
        )
        if to_library:
            confirm = (
                f"Download {n} object(s) into the library?\n\n"
                f"Root:\n{dest_root}\n\n"
                f"{layout_note}\n\n"
                "Files will be added to the library index."
            )
        else:
            confirm = (
                f"Download {n} object(s) to this folder?\n\n"
                f"{dest_root}\n\n"
                f"{layout_note}\n\n"
                "Files will not be added to the library."
            )
        if not messagebox.askyesno(title, confirm):
            return

        if not self._begin_transfer_job():
            return
        device = self.device
        batch = list(refs)
        root = dest_root
        # GUID → existing host track only when indexing into the library.
        host_by_guid = (
            {
                t.guid: t
                for t in self.library.tracks
                if is_track_guid(t.guid)
            }
            if to_library
            else {}
        )
        index_into_library = to_library

        def work():
            import shutil

            gen = self._bg.generation
            report = self._bg.progress_callback(gen)
            pulled: list[dict] = []
            failed = 0
            total = len(batch)
            for i, ref in enumerate(batch):
                if self._should_cancel_job():
                    raise JobCancelled(f"{title} cancelled")
                oid = int(ref.item_id or 0)
                label = (ref.name or ref.title or f"id={oid}").strip()
                report("progress", i, total, f"pulling {label}")
                try:
                    remote_guid = guid_from_remote_name(ref.name)
                    existing = (
                        host_by_guid.get(remote_guid) if remote_guid else None
                    )
                    if (
                        index_into_library
                        and existing is not None
                        and existing.path
                        and os.path.isfile(existing.path)
                    ):
                        # Already in library on disk — skip download.
                        pulled.append(
                            {
                                "path": existing.path,
                                "guid": existing.guid,
                                "meta": existing.meta,
                                "skipped_existing": True,
                            }
                        )
                        continue

                    # Device tags, then embedded-file recovery when placeholders
                    # (mass-storage-style dump on an MTP-only player).
                    info, file_meta, temp_path = (
                        device_ops.resolve_tags_with_embedded_fallback(
                            device,
                            ref,
                            prefer_embedded_when_placeholder=True,
                            keep_download=True,
                        )
                    )
                    rel = device_ops.suggested_library_relpath(
                        ref, info=info, file_meta=file_meta
                    )
                    dest = os.path.join(root, rel)
                    dest = os.path.abspath(dest)
                    os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
                    if os.path.exists(dest):
                        dest = device_ops.unique_dest_path(
                            os.path.dirname(dest), os.path.basename(dest)
                        )

                    if temp_path and os.path.isfile(temp_path):
                        # Reuse the probe download; avoid a second USB transfer.
                        try:
                            shutil.move(temp_path, dest)
                        except OSError:
                            shutil.copy2(temp_path, dest)
                            try:
                                os.remove(temp_path)
                            except OSError:
                                pass
                        item_path = dest
                        if file_meta is not None and track_meta_is_usable(
                            file_meta
                        ):
                            try:
                                from mtpmanager.infra.mutagen_tags import (
                                    write_metadata,
                                )

                                write_metadata(dest, file_meta)
                            except Exception:
                                logger.debug(
                                    "pull: write recovered tags failed",
                                    exc_info=True,
                                )
                    else:
                        item = device_ops.retrieve_track(
                            device,
                            ref,
                            root,
                            info=info,
                            write_tags=True,
                            dest_path=dest,
                        )
                        if item.status != "ok" or not item.path:
                            failed += 1
                            continue
                        item_path = item.path
                        if file_meta is None:
                            try:
                                from mtpmanager.infra.mutagen_tags import (
                                    read_metadata,
                                )
                                from mtpmanager.domain.device_media import (
                                    track_meta_looks_placeholder,
                                )

                                local = read_metadata(item_path)
                                if (
                                    track_meta_is_usable(local)
                                    and not track_meta_looks_placeholder(local)
                                ):
                                    file_meta = local
                            except Exception:
                                pass

                    if file_meta is not None and track_meta_is_usable(file_meta):
                        meta = file_meta
                    elif info is not None:
                        meta = device_ops.track_info_to_metadata(info)
                    else:
                        meta = TrackMetadata(
                            title=(ref.title or "").strip()
                            or os.path.splitext(os.path.basename(item_path))[0],
                            artist=(ref.artist or "").strip()
                            or "Unknown Artist",
                            album=(ref.album or "").strip() or "Unknown Album",
                            genre=(ref.genre or "").strip() or "Unknown Genre",
                            date=(ref.date or "").strip(),
                            tracknumber=(ref.tracknumber or "").strip() or "01",
                        )
                    guid = (
                        remote_guid
                        if is_track_guid(remote_guid)
                        else new_track_guid()
                    )
                    pulled.append(
                        {
                            "path": item_path,
                            "guid": guid,
                            "meta": meta,
                            "skipped_existing": False,
                        }
                    )
                except Exception:
                    logger.exception("pull failed id=%s", oid)
                    failed += 1
            report("progress", total, total, "done")
            return {"pulled": pulled, "failed": failed, "total": total}

        def on_done(result) -> None:
            self._end_transfer_job()
            pulled = result.get("pulled") or []
            failed = int(result.get("failed") or 0)
            added = 0
            if index_into_library and pulled:
                by_path = {
                    os.path.normpath(t.path): i
                    for i, t in enumerate(self.library.tracks)
                    if t.path
                }
                for row in pulled:
                    path = os.path.normpath(row["path"])
                    guid = row["guid"]
                    meta = row["meta"]
                    track = Track(path=path, meta=meta, guid=guid)
                    idx = by_path.get(path)
                    if idx is not None:
                        self.library.tracks[idx] = track
                    else:
                        self.library.tracks.append(track)
                        by_path[path] = len(self.library.tracks) - 1
                        if not row.get("skipped_existing"):
                            added += 1
                try:
                    save_library_index(self.library)
                except Exception:
                    logger.exception("save_library_index after pull failed")
                try:
                    self._rebuild_track_tree()
                    lib_roots = normalize_library_roots(self.library.root_paths)
                    self.win.set_library_status(
                        self.library.root_path or "",
                        len(self.library.tracks),
                        root_paths=lib_roots or None,
                    )
                except Exception:
                    logger.debug(
                        "library UI refresh after pull failed", exc_info=True
                    )
                messagebox.showinfo(
                    title,
                    f"Downloaded {len(pulled)} of {result.get('total', 0)} "
                    f"object(s) ({added} new).\n"
                    f"Failed: {failed}\n\nLibrary root:\n{root}",
                )
            else:
                listing = "\n".join(
                    os.path.basename(str(r.get("path") or ""))
                    for r in pulled[:12]
                )
                if len(pulled) > 12:
                    listing += f"\n… and {len(pulled) - 12} more"
                detail = f"\n\n{listing}" if listing else ""
                messagebox.showinfo(
                    title,
                    f"Downloaded {len(pulled)} of {result.get('total', 0)} "
                    f"object(s).\n"
                    f"Failed: {failed}\n\nFolder:\n{root}{detail}",
                )

        def on_error(exc: BaseException) -> None:
            self._end_transfer_job()
            if isinstance(exc, JobCancelled):
                self._handle_job_cancelled(exc, title=f"{title} cancelled")
                return
            logger.exception("%s failed", title)
            messagebox.showerror(title, str(exc))

        self._bg.submit(
            work,
            on_done=on_done,
            on_error=on_error,
            on_progress=self._on_transfer_ui_event,
            name=(
                "device-pull-library"
                if index_into_library
                else "device-pull-folder"
            ),
        )

    def action_delete_track(self) -> None:
        """Experimental Device → Delete Track: live file listing picker."""

        def on_listed(files) -> None:
            if not files:
                messagebox.showinfo(
                    "Delete Track",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Delete Track (live): %d object(s) listed", len(files)
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
            serial = self._device_serial or device_serial_key()
            try:
                remove_by_item_id(serial, entry.item_id)
            except Exception:
                logger.debug(
                    "device_index remove after delete failed", exc_info=True
                )
            self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
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
        """Experimental Device → Delete All: live tracklisting, then batch delete."""

        def on_listed(tracks) -> None:
            if not tracks:
                messagebox.showinfo(
                    "Delete All Tracks",
                    "No tracks found on the device.",
                )
                return

            n = len(tracks)
            logger.info(
                "Delete All Tracks (live): %d track(s) listed", n
            )
            if not messagebox.askyesno(
                "Delete All Tracks",
                f"Delete all {n} track(s) from the device?\n\n"
                "This deletes objects from the device track listing "
                "(folders and photos are not deleted).\n\n"
                "This cannot be undone from the app.",
                icon=messagebox.WARNING,
                default=messagebox.NO,
            ):
                return
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
            serial = self._device_serial or device_serial_key()

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
                for oid in getattr(result, "deleted_ids", ()) or ():
                    try:
                        remove_by_item_id(serial, int(oid))
                    except Exception:
                        logger.debug(
                            "device_index remove after delete-all failed id=%s",
                            oid,
                            exc_info=True,
                        )
                self._schedule_device_music_tree_refresh(enrich_missing_tags=False)
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

        def list_work(device):
            gen = self._bg.generation
            report = self._bg.progress_callback(gen)

            def on_progress(done: int, total: int, message: str) -> None:
                report("status", message or "listing tracks…")
                if total and total > 0:
                    report("progress", done, total, message)

            return device_ops.list_tracks(device, on_progress=on_progress)

        self._run_device_bg(
            title="Delete All Tracks",
            name="delete-all-list",
            work=list_work,
            on_success=on_listed,
            busy_message=(
                "listing tracks before delete "
                "(filelisting + tags; may take a while)…"
            ),
            progress_mode="determinate",
        )

    def action_refresh_device_index(self) -> None:
        """Device → Refresh Device Index: one live list_files → SQLite replace."""
        if not self._require_device_ready():
            return
        serial = self._device_serial or device_serial_key()
        if self._device_index_seed_inflight:
            messagebox.showinfo(
                "Refresh Device Index",
                "A device index job is already running.",
            )
            return
        if self._transfer_busy or self._device_io.is_held():
            messagebox.showinfo(
                "Refresh Device Index",
                "The device is busy with another USB operation. Wait for it "
                "to finish, then refresh again.",
            )
            return
        self._device_index_seeded = False
        if not self._start_device_index_seed(serial, force=True):
            messagebox.showinfo(
                "Refresh Device Index",
                "Could not start device index refresh (device busy or "
                "disconnected).",
            )
            return
        messagebox.showinfo(
            "Refresh Device Index",
            "Refreshing durable device file index in the background "
            f"(serial={serial}).\n\n"
            "Used for sync skip-if-present. Experimental List Files/Tracks "
            "use live USB listing separately.",
        )

    def action_get_file_info(self) -> None:
        """Experimental Device → Get File Info: live list picker + Get_Filemetadata."""

        def on_listed(files) -> None:
            if not files:
                messagebox.showinfo(
                    "File Info",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Get File Info (live): %d object(s) listed", len(files)
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
            # Prefer live Get_Filemetadata; on ZEN proplist fail use listing row.
            meta = entry
            source = "listing"
            try:
                meta = device_ops.get_file_metadata(self.device, entry.item_id)
                source = "live"
            except TransportError as e:
                if e.fatal:
                    logger.exception(
                        "Get file info failed id=%s", entry.item_id
                    )
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
                    "Source: live file listing snapshot "
                    "(Get_Filemetadata failed for this id — common on ZEN "
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
        """Experimental Device → Get Track Info: live list picker + tags."""

        def on_listed(files) -> None:
            candidates = [e for e in files if looks_like_track(e)]
            pool = candidates if candidates else list(files or [])
            if not pool:
                messagebox.showinfo(
                    "Track Info",
                    "No objects found on the device.",
                )
                return
            logger.info(
                "Get Track Info (live): %d candidate(s) of %d listed",
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
                info = device_ops.get_track_metadata(
                    self.device, entry.item_id
                )
            except TransportError as e:
                logger.exception(
                    "Get track info failed id=%s", entry.item_id
                )
                messagebox.showerror("Track Info", str(e))
                return
            except Exception as e:
                logger.exception(
                    "Get track info failed id=%s", entry.item_id
                )
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
