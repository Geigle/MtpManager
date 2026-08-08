# Transfer pipeline and modes

End-to-end send path, Stable vs Experimental behavior, and where to change things.

**Related:** [architecture.md](./architecture.md) · [device-contract.md](./device-contract.md) · [decisions.md](./decisions.md)

---

## End-to-end flow

```text
[index load | Manage Library / scan] → Library / Track
     → user action → transfer_track(s)
     → (optional) FFmpegTranscoder → Transport.send_track
```

| Step | Module | Notes |
|------|--------|--------|
| Restore index | `ui/controllers._start_index_restore` | Startup: background load of durable JSON |
| Library menu | `ui/window` menubar **Library** | Manage Library… (see below) |
| Library toolbar | `ui/window` (full-width under title) | Status only: path + track count (shows Scanning… / Loading index… when busy) |
| Manage library | `ui/controllers.on_manage_library` + `dialogs.ManageLibraryDialog` | **Library → Manage Library…**: list roots; **Add Root…** / **Remove Selected** / **Update Library** |
| Add root | `ui/controllers.on_add_library_root` | Folder picker → **add** root (if new) → **background** full scan of **all** roots → save index |
| Remove root(s) | `ui/controllers.on_remove_library_roots` | Drop selected roots → **background** re-scan of remaining (or clear library if none left) |
| Update | `ui/controllers.on_update_library` | **Background** re-scan of **all** stored roots; disabled in dialog when no root reachable or busy |
| Scan | `app/scan_library.scan_library` / `scan_library_roots` | Recursive music files → tags via mutagen (worker thread); multi-root merge + path dedupe |
| Background jobs | `ui/bg.TkBackgroundRunner` | Concurrent jobs (per-gen callbacks); queue + main-thread `root.after` poll; never touch Tk from workers |
| Persist index | `infra/library_index` | SQLite `{data_dir}/library_index.db`; GUID per track; saved in scan worker |
| Index (in-memory) | `domain/library.Library` | Source of truth; Treeview is a sorted view |
| Library tree | `ttk.Treeview` + `domain/library_sort` | Columns Title/Artist/Album/Year; heading click changes grouping. **Default (Artist option 3):** `{artist} - {album}`→Track (VA only when `tracks_should_group_as_various_artists` — compilations/path/true multi-core-artist; soundtrack/OST genre only if multi-artist; **feat./ft./with/vs. do not** force VA). **Artist** *cycles* four modes: (1) Artist→Album→Track A–Z, (2) same Z–A, (3) default combo, (4) combo reverse. **Album:** `{album} - {artist}`→Track. **Year / Title** as before. Group headers in Title; album thumbs in `#0` when cached. Fibonacci-chunked inserts. |
| Format / encode presets | **Config → Config…** → `{data_dir}/config.json` | Durable `send_format` + full `audio_encode` recipe (bitrate, VBR, channels, sample rate, …). Named ladders per format (MP3/WMA/WAV/FLAC/AAC/OGG/Opus); Advanced view for granular controls. Device profiles restrict formats (ZEN Vision:M: MP3/WMA/WAV; generic unrestricted). Sync uses this recipe for Music (and other non-audiobook) via `FFmpegTranscoder.convert(..., settings=…)` |
| Audiobook encode override | **Library → Podcast Settings…** → same `config.json` | Optional `audiobook_audio_encode` recipe. When set, **only** tracks under the Audiobooks tab (genre Audiobook) use it instead of the global Config encode. |
| Podcast encode override | **Library → Podcast Settings…** (default) + **Podcasts tab → show context → Encode Settings…** (per-show) | Default `podcast_audio_encode` in `config.json`. Per-show `audio_encode_json` on the subscription (talk shows low bitrate; RPG/music shows higher). Precedence: per-show → podcast default → Config `audio_encode`. |
| Track context menu | Right-click listbox row | Sync this track / Sync Album / Sync all from Artist |
| Transfer menu | Menubar | Sync Entire Library; Sync Folder… |
| Device menu | Menubar (Experimental) | Admin / test tools; disabled in Stable |
| Pipeline | `app/transfer.transfer_track` | Transcode if needed, then send |
| Batch | `app/transfer.transfer_tracks` | Progress callback; abort on fatal `TransportError` |
| Transport | `CmdTransport` or `PymtpDevice` | Chosen by mode tab |

### Library menu, status toolbar, and durable index

| Chrome | Role |
|--------|------|
| **Library** menu | Command: **Manage Library…** (add/remove roots + update scan) |
| Status toolbar | Path (or **Multiple Library Roots**) + track count only (not action buttons). Hover the path label for the full path, or the full list of roots when multi-root. |

| Menu / dialog action | Behavior |
|----------------------|----------|
| **Library → Manage Library…** | Modeless window listing roots; **Add Root…**, **Remove Selected**, **Update Library**, **Close** |
| **Add Root…** | Folder picker → add folder if new → background full scan of **all** roots → save index |
| **Remove Selected** | Confirm → drop root(s) → background re-scan of remaining (empty library if none left) |
| **Update Library** | Background re-scan of **all** stored roots → rewrite index; **disabled** when no root is reachable or a library job is running |

- **Startup:** schedule index restore after the UI is up (`after(0, …)`). Worker loads `{data_dir}/library_index.db` (migrates legacy `library_index.json` once if needed); main thread fills the listbox. If **any** root is still a directory, missing files are dropped. If **all** roots are **unreachable**, still show index entries greyed/disabled; Manage Library remains available so roots can be fixed.
- **Multiple roots:** the durable index stores `root_paths` (JSON list) plus legacy `root_path` (first root). One mixed tree or several media locations can share a single library view.
- **Send names:** ObjectFileName is `{guid}{ext}` under Music folder 100; full tags still go on the wire. Multi-track sync **skips** tracks whose GUID stem is in the durable device index (SQLite) — **not** a live `list_files` per job.
- **Device index (skip only):** one `list_files` seed after Experimental connect (or **Refresh Device Index…**); successful send/delete update the cache. Used for **skip-if-present**, not as the sole browse UI.
- **Enable Experimental Tools:** Config checkbutton (`enable_experimental_tools` in `config.json`, default **false**). When off, experimental Device commands (List Folders/Files/Tracks, Get Tracks, Delete/Get Info, Delete All), Transfer retail package commands, and experimental Config toggles (artist/album/podcast folders, **allow video podcasts to sync**, audio-podcasts-as-video) are **removed from the menus** (not merely disabled). Podcast video encode paths are also **ignored at runtime** when the gate is off. **Send Video…** is a standard Device tool and is always listed under PyMTP mode.
- **Experimental List Folders / Files / pickers** (tools gate on): **live** folder list / `get_filelisting` (may also refresh the durable index).
- **Experimental List Tracks / Delete All list** (tools gate on): **live** filelisting + per-id `Get_Trackmetadata` (same algorithm as CLI `mtp-tracks`; complete on ZEN). Soft-fills empty titles from host GUID library when known. Bulk `Get_Tracklisting*` is diagnostic-only (`list_tracks_via_tracklisting`) — often returns only a few tracks on this device.
- **Get Tracks from Device…** (tools gate on): list media (with tags), then download each via `get_file_to_file` to a chosen host folder; best-effort mutagen tag write when device metadata exists (audio containers; video often keeps embedded tags only). Writes an editable **`device_media_map.json`** (+ readable **`device_media_map.md`**) in the export folder: device identity, full MTP object fields, tags, host paths, retail-demo heuristics, and blank `editor_notes` / `desired_tags` for fixing missing tags before a later restore.
- **Package Retail Demos… / Restore Retail Package… (Transfer menu):** From a Get Tracks export, zip **only** entries with `flags.looks_like_retail_demo` plus a reduced **`restore_map.json`** (`media/` + map). Restore sends that package with **no GUID** ObjectFileNames (`preferred_basename` from the map) and MTP tags from `desired_tags`; respects `include_in_restore`; fatal abort on transport error.
- **Non-blocking:** scan and index restore run on a daemon thread (`TkBackgroundRunner`). **Index restore streams** Fibonacci-sized track batches to the main thread (`on_progress`) so the library Treeview paints **while SQLite is still loading** (flat path-order preview); when load finishes the tree rebuilds with the active sort (Artist hierarchy, etc.), also Fibonacci-chunked. Album-art cache warm runs **after** paint starts (deferred thread), not before. A short yield between progress batches lets Tk poll paint between SSD-fast reads. Device index seed / tag enrich may run **concurrently** with restore — each job has its own callbacks (a newer job must not discard the library restore result, or the UI stays stuck on “Loading index…”).
- While busy, Library menu actions are disabled and the toolbar count shows `Loading index…` / `Scanning…`.
- Transfers that need the library refuse to run while busy or while the root is unreachable.
- Left panel: **PyMTP device session** front-and-center (graphic + caption). Track sync is via **context menu**. **Connect / Disconnect / Device Info** live under the **Device** menu (enabled when Stable Mode is off). Output format is **Config → Config…**; transfer engine is **Config → Stable Mode**; experimental **Config → Store tracks in artist folder** creates `Music/<Artist>` (numeric folder id) before send and uses that id as parent (PyMTP only; disabled under Stable Mode). Optional **Config → Store tracks in album folder** (enabled only when artist folders are on) nests `Music/<Artist>/<Album>` the same way — still `{folder_id}/{basename}` on the wire, not string paths. Preferences live in app data `config.json`.
- **Default is PyMTP** (Stable Mode unchecked). Auto-connect: while Stable Mode is off (and auto-reconnect is enabled), a ~3s poll quietly maintains the PyMTP session: connect when absent, **probe liveness** when a session looks open (stale pointers after unplug via `LIBMTP_Get_Storage` — **not** cached `get_modelname`), soft-fail a couple of times then disconnect + clear art + retry when the device is gone. Absence is logged once per unplug streak (no dialogs). **All MTP/USB work shares one exclusive `DeviceIoGate`** (`app/device_io_gate.py`): transfers, listings, index seed, tag enrich, embedded meta probes, manual Connect/Disconnect, and the poll itself. The poll only runs when the gate is free and outside a short post-job quiet window — it never pings the device mid-sync. **Device → Disconnect** stops auto-reconnect until **Device → Connect** (or turning Stable Mode off again). **Enabling Stable Mode** disconnects PyMTP so `mtp-sendtr` is not blocked by an open session; the left panel shows Stable Mode help text instead of the device graphic. The fixed-width left column has two subframes: **Selection** (first-run PyMTP hint, then track/album/artist context for the tree selection) and **Device** (profile graphic under PyMTP, or Stable Mode help text when that mode is on).
- **PyMTP sync** requires `PymtpDevice.is_connected()`; otherwise a warning points the user to Connect or Config → Stable Mode.
- Data dir: macOS `~/Library/Application Support/MtpManager/`; Linux `$XDG_DATA_HOME/mtpmanager` or `~/.local/share/mtpmanager/`; override with `MTP_MANAGER_DATA_DIR`.

### Track context menu and other operations

| Entry point | Actions |
|-------------|---------|
| Track list multi-select | **extended** selectmode: **Shift+click** range, **Ctrl+click** (Windows/Linux) or **⌘+click** (macOS) toggle. Group headers expand to their tracks. |
| Right-click track | **Sync N selected tracks** (when multi-select), **Sync this track**, **Sync Album**, **Sync all from Artist** (global format + active mode transport). Right-click inside a multi-selection keeps the selection. |
| **Transfer** menu | **Sync Entire Library**; **Sync Folder…**; **Sync Selected Tracks** (multi-select batch); **Resume Sync**; **Package Retail Demos…** / **Restore Retail Package…** (export → zip Creative demos; zip → player); **Cancel Current Job** |
| **Device** menu | **Standard** (PyMTP mode): Connect, Disconnect, Device Info (only place to edit device name — applied on close if changed), Create Folder…, **Send Video…** (standard tool: pick host video; choose **Video** 120 or **TV** 124; optional **Encode for device** from `DeviceProfile.video_options` (ZEN Vision:M only — hidden on generic): notebook of mutually exclusive recipes (default **AVI · XviD · MP3**, plus **AVI · DivX · MP3**; **WMV · WMA** is marked broken and hidden unless Config → **Show broken video encode presets**); container/video/audio details per tab; ZEN caps at 30 fps unless **Ignore max frame rate (experimental)**; background job with determinate progress; skip encode when source already matches; sanitized basename, no library GUID), Refresh Device Index…. **Experimental tools** (only when **Config → Enable Experimental Tools** is on): List Folders, List Files, List Tracks (fast `get_filelisting` + media filter; optional **Load tags for selection** via `get_track_metadata`), Get Tracks from Device…, Delete Track, Get File Info, Get Track Info, Delete All Tracks… (batch `delete_object`, fatal abort). Device menu is PyMTP-only (disabled under Stable Mode). |
| **Config** menu | **Stable Mode**; **Enable Experimental Tools** (default off: hides experimental Device/Transfer items and experimental Config toggles such as artist/album folders, **Allow video podcasts to Sync**, **Sync Audio Podcasts as Video**); podcast keep/reveal/clear; **Config…** (audio format + quality presets / advanced encode, broken video presets). Video-podcast encode paths only run when Experimental Tools is on *and* the matching toggle is checked. |

Device admin prompts use dialogs (`ui/dialogs.py`); there is no main-window path/name entry.

**USB listings never run on the Tk thread.** List Folders / Files / Tracks and the listing phase of Delete Track, Delete All Tracks, Get File Info, and Get Track Info go through `AppController._run_device_bg` → `TkBackgroundRunner`. Those jobs take the same `DeviceIoGate` as transfers (`reason="transfer"`), so the auto-connect poll cannot race the session. List paths use an indeterminate bar. **Do not** use full-library `get_tracklisting` as the default List Tracks path on ZEN (multi-hour USB; no partial results until C returns). Tags are on-demand only. Long USB walks may still print `LIBMTP panic: unable to read in zero packet` to **stderr** (C library, not Python logging); that noise is often non-fatal.

After a heavy USB job the gate is released with a short **USB quiet window** (`DEFAULT_USB_QUIET_S` / `_DEVICE_USB_COOLDOWN_S`) so a recovering ZEN is not torn down by an immediate `Get_Storage` probe. A single failed liveness probe is a **soft-fail** (keep session; only disconnect after consecutive failures). The quiet window blocks auto-connect only — user-initiated jobs may still acquire the gate.

**Connect vs diagnostics:** Device → Connect and auto-connect only open the MTP session and read **identity** (name / manufacturer / model) for profile matching. They never call battery or storage APIs. Full `get_info` (battery, free/total/used space, serial, version) is **Device → Device Info** only; each optional field soft-fails so one bad property (historically `get_batterylevel` on recovering ZENs) does not abort the dialog or undo connect.

**Quit:** Closing the main window stops the auto-connect poll and calls `disconnect` on any open PyMTP session (`AppController.shutdown_device_session`) so the device is not left claimed on USB for the next run or for Stable `mtp-sendtr`.

---

## Format targets and transcoding

- User-facing encode recipe (Config → Config…): format + quality presets / advanced (`audio_encode` in `config.json`). Used when the source is *not* already playable on the matched device (or when no device profile is active). Device profiles may restrict formats (ZEN: MP3/WMA/WAV).
- **Device-native passthrough:** each `DeviceProfile` lists `supported_audio_formats`. For Creative ZEN Vision:M that is `mp3`, `wma`, `wav`. After USB detect + profile match, sources already in a native format are sent **as-is** (no ffmpeg), even if they differ from the configured target — avoids lossy→lossy re-encodes. Logic: `domain/device_profile.needs_transcode`; profiles in `domain/device_profiles.py`. Profile is applied only when a device is detected (`AppController._apply_device_profile`); no profile → convert only if extension ≠ target (no ZVM assumption).
- Otherwise `FFmpegTranscoder.convert` writes into a **dual-buffer slot**: `TRANSCODE_0.<ext>` / `TRANSCODE_1.<ext>` (`slot` 0 or 1). Batch `transfer_tracks` prepares track *i+1* on a helper thread into the alternate slot while track *i* is sent, so ffmpeg cannot clobber a file still in flight (CMD and PyMTP share this pipeline). Encode options come from the Config recipe (libmp3lame VBR/CBR, wmav2, PCM, …).
- **Audio-only convert (invariant):** every convert/extract forces `-map 0:a:0` and strips video/subs/data/metadata so **FLAC/MP4 cover art is never remuxed** into the temp. Default ffmpeg mapping used to copy attached pictures into “MP3” files → multi‑MB objects at “low bitrate,” bogus device bitrates, and **slow playback start** on ZEN (~1 min observed on one fat track). Device cover art is abstract albums (D15), not track-embedded images. Details: [debrief-ffmpeg-cover-art-bloat.md](./debrief-ffmpeg-cover-art-bloat.md), [decisions.md](./decisions.md) D16.
- After convert, tags are re-read and merged (prefer original tags; take stream length/bitrate from converted file when useful).
- Temps are cleaned up after each successful send (or on abort).

Supported library extensions for scan: `aac`, `alac`, `flac`, `mp3`, `ogg`, `vorbis`, `wav`, `wma` (`MUSIC_EXTENSIONS` in `library.py`).

---

## Album / artist selection (high level)

Logic lives in `domain/library.py`—enough for agents to find the module without re-deriving every edge case.

### `filter_by_artist(seed)`

Include a track if any of:

- same `meta.artist` as seed
- same `meta.albumartist` as seed’s artist (when artist is meaningful)
- path has a folder component equal to the artist name (casefold)

Logs “questionable” matches when artist tags differ but path/albumartist matched.

### `filter_by_album(seed)`

Requires **same album title** plus at least one strong signal:

- same artist, or
- same meaningful albumartist, or
- same parent directory, or
- same year **and** path layout hint (`_album_path_hint`: shared grandparent, or album-named parent folders with multi-level common prefix)

Batch actions sort matches by `path` before `transfer_tracks`.

---

## Stable mode (`CmdTransport`)

| Property | Behavior |
|----------|----------|
| Process model | **One `mtp-sendtr` process per track** — connect → send → exit |
| Session | No long-lived libmtp session in the app |
| Remote | `build_remote_path(..., guid=)` → `100/<32hex>.mp3`; `-s` storage id |
| Tags | Full metadata on CLI flags; filename sanitized |
| Timeout | Size-based (min 90s, max 900s, ~256 KiB/s + overhead) |
| Hang handling | Stream stdout/stderr; match fatal patterns; after ~8s post-fatal grace, **kill** process (album-association hang after failed finalize) |
| Errors | Always `TransportError(fatal=True)` on failure / timeout / kill |

Code: `mtpmanager/infra/cmd_transport.py`.

Recommended for normal music loading. Does not require Experimental Connect.

---

## Experimental mode (`PymtpDevice`)

| Property | Behavior |
|----------|----------|
| Process model | **Long-lived** libmtp session from Connect until Disconnect |
| Implements | `DevicePort` + `Transport` |
| Remote | Same `remote_naming` contract; parent/storage on `LIBMTP_Track` |
| Filetype | Via patched `find_filetype` — MP3 must be **2** (see wrapper) |
| Failures | Pure PyMTP only; wrap as `TransportError(fatal=True)` with errorstack when available |
| **No silent fallback** | Does **not** invoke `mtp-sendtr` on failure |
| UX | Dialog + recovery: Disconnect → Stable Mode → retry |

Code: `mtpmanager/infra/pymtp_device.py`, `pymtp_wrapper.py`.  
Story: [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md).

Device admin (set name, folders, test file, etc.) is Experimental-only in the UI.

---

## Batch abort and session poisoning

`transfer_tracks` (`app/transfer.py`):

- On `TransportError` with `fatal=True` (default for both transports): **abort remaining tracks**, re-raise.
- UI shows “Transfer aborted” and mode-aware recovery text (`controllers._transfer_recovery_hint`).

After PTP **`02ff`**, “Could not close session”, or unplug:

- Further Experimental sends often fail immediately on the same session.
- Stable starts a fresh process each track, but a **dead USB device** still fails until unplug/replug.
- **Recovery:** disconnect/replug before retrying; prefer Stable after Experimental send death.

Non-fatal continues are supported by the API (`stop_on_fatal=False`) but production transports mark failures fatal.

---

## Background transfers

Single-track and batch sends run on a **worker thread** via `ui/bg.TkBackgroundRunner` (same pattern as library scan). Progress events are queued to the main thread for the progress bar. Library menu / transfers refuse to start while the other is busy.

**Cancel** is available while a transfer/device batch job runs (`_begin_transfer_job`):

- Bottom bar **Cancel** button (right of the progress bar)
- **Transfer → Cancel Current Job**
- **Escape**

Cancel is **cooperative**: the current track send / object delete is allowed to finish; remaining items are skipped and the UI reports how many completed (`JobCancelled` / `DeleteAllResult.cancelled`). In-flight ffmpeg convert of the *next* track is abandoned when the batch stops.

### Transfer queue (live append)

Batch syncs run from a **live queue** (`app/transfer_queue.BatchTransferQueue`) shared by the worker and UI:

- Starting Entire Library / Folder / Album / Artist / Selected creates the queue and durable `sync_job.json` plan.
- While that batch is running, **Sync album**, **Sync all from artist**, **Sync selected**, and single-track sync **append** new unique paths (by source path) instead of refusing with “already in progress”.
- Already-queued or finished paths are ignored on append.
- Progress totals grow as items are added; row tints mark newly queued tracks.

Device admin jobs (list/delete) still take the busy lock and do **not** expose a transfer queue.

### Resume Sync

Multi-track syncs (Entire Library, Folder, Album, Artist, **Selected tracks**) write a durable plan to `{data_dir}/sync_job.json` (`infra/sync_job.py`): ordered source paths, `next_index` (first not-yet-successful path), status, target format, and last error.

- After each successful send, `next_index` advances and the file is updated.
- Mid-job queue appends also append paths on the durable job.
- On fatal failure or cancel, status becomes `failed` / `cancelled` and **Transfer → Resume Sync** enables.
- Resume rebuilds tracks from the remaining paths (library tags, or re-read from disk) and continues from `next_index` (retries the failed track).
- A full successful run marks the job `completed` (Resume disabled). An app quit mid-job is treated as failed on next launch if paths remain.

The transfer **worker** still blocks on each `transport.send_track` (subprocess or libmtp); the dual-slot prep thread overlaps **ffmpeg convert** of the next track only.

### Listbox transfer highlighting

`on_track_status` reports per source path; the UI tints listbox rows (selection blue is unchanged):

| Status | Color |
|--------|--------|
| Queued (whole batch at start) | Desaturated green |
| Transcoding | Stronger desaturated green |
| Transferring | Desaturated red |
| Done / failed / job end | Clear |

Bulk Sync Album / Artist / Entire Library marks every matching library row queued first; each row clears when that track finishes (or the whole job ends).

---

## Tests that lock the contract

```bash
.venv/bin/python -m unittest tests.test_remote_naming tests.test_pymtp_filetypes tests.test_library_index tests.test_bg tests.test_transfer_pipeline -v
```

| Test | Guards |
|------|--------|
| `tests/test_remote_naming.py` | Music folder 100, storage `0x00010001`, short names, strip `&`, year extract |
| `tests/test_pymtp_filetypes.py` | `LIBMTP_Filetype["MP3"] == 2`, `FOLDER == 0`, `find_filetype` |

---

## Preferred change surfaces

| Task | Touch first |
|------|-------------|
| Remote filenames / folder / storage defaults | `infra/remote_naming.py` (+ tests) |
| CMD hang, timeout, fatal patterns | `infra/cmd_transport.py` |
| PyMTP send / filetype / ctypes | `infra/pymtp_wrapper.py`, `infra/pymtp_device.py` |
| Batch abort policy | `app/transfer.py` |
| Mode selection / recovery dialogs | `ui/controllers.py`, `ui/window.py` |
| Transcode formats | `infra/ffmpeg_transcode.py`, actions in controllers |
| Artist/album selection heuristics | `domain/library.py` |
