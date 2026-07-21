# Transfer pipeline and modes

End-to-end send path, Stable vs Experimental behavior, and where to change things.

**Related:** [architecture.md](./architecture.md) · [device-contract.md](./device-contract.md) · [decisions.md](./decisions.md)

---

## End-to-end flow

```text
[index load | Select/Scan Library] → Library / Track
     → user action → transfer_track(s)
     → (optional) FFmpegTranscoder → Transport.send_track
```

| Step | Module | Notes |
|------|--------|--------|
| Restore index | `ui/controllers._start_index_restore` | Startup: background load of durable JSON |
| Library menu | `ui/window` menubar **Library** | Select root / Update (see below) |
| Library toolbar | `ui/window` (full-width under title) | Status only: path + track count (shows Scanning… / Loading index… when busy) |
| Select root | `ui/controllers.on_select_library_root` | Folder picker → **background** full scan → save index |
| Update | `ui/controllers.on_update_library` | **Background** re-scan of stored root; disabled if root missing/unreachable or busy |
| Scan | `app/scan_library.scan_library` | Recursive music files → tags via mutagen (worker thread) |
| Background jobs | `ui/bg.TkBackgroundRunner` | Thread + queue + `root.after` poll; never touch Tk from workers |
| Persist index | `infra/library_index` | Saved in scan worker; UI updated on main thread |
| Index (in-memory) | `domain/library.Library` | Source of truth; Treeview is a sorted view |
| Library tree | `ttk.Treeview` + `domain/library_sort` | Columns Title/Artist/Album/Year; heading click sorts; Artist/Year hierarchy. Group rows put the full header text in Title. **Album** headers show a thumb in `#0` (rowheight sized so art is not cropped). Thumbs are **disk-cached** PNGs under `{data_dir}/album_art_cache/` and built **off the UI thread** (with index load/scan + after tree rebuild). |
| Format preference | **Config → Config…** → `{data_dir}/config.json` | Durable `send_format` (`mp3`/`wma`); all Sync actions use it |
| Track context menu | Right-click listbox row | Sync this track / Sync Album / Sync all from Artist |
| Transfer menu | Menubar | Sync Entire Library; Sync Folder… |
| Device menu | Menubar (Experimental) | Admin / test tools; disabled in Stable |
| Pipeline | `app/transfer.transfer_track` | Transcode if needed, then send |
| Batch | `app/transfer.transfer_tracks` | Progress callback; abort on fatal `TransportError` |
| Transport | `CmdTransport` or `PymtpDevice` | Chosen by mode tab |

### Library menu, status toolbar, and durable index

| Chrome | Role |
|--------|------|
| **Library** menu | Commands: **Select Library Root…**, **Update Library** |
| Status toolbar | Path + track count only (not action buttons) |

| Menu command | Behavior |
|--------------|----------|
| **Select Library Root…** | Folder picker → background full scan → save index |
| **Update Library** | Background re-scan of stored root → rewrite index; **disabled** when no root, root unreachable, or a library job is running |

- **Startup:** schedule index restore after the UI is up (`after(0, …)`). Worker loads `{data_dir}/library_index.json`; main thread fills the listbox. If `root_path` is still a directory, missing files are dropped. If the root is **unreachable**, still show index entries greyed/disabled and leave **Update Library** disabled.
- **Non-blocking:** scan and index restore run on a daemon thread (`TkBackgroundRunner`). The previous library stays until the job finishes; a newer job discards stale results. Listbox population is chunked so large libraries do not freeze the event loop.
- While busy, Library menu actions are disabled and the toolbar count shows `Loading index…` / `Scanning…`.
- Transfers that need the library refuse to run while busy or while the root is unreachable.
- Left panel: **PyMTP device session** front-and-center (graphic + caption). Track sync is via **context menu**. **Connect / Disconnect / Device Info** live under the **Device** menu (enabled when Stable Mode is off). Output format is **Config → Config…**; transfer engine is **Config → Stable Mode**; experimental **Config → Store tracks in artist folder** creates `Music/<Artist>` (numeric folder id) before send and uses that id as parent (PyMTP only; disabled under Stable Mode). Optional **Config → Store tracks in album folder** (enabled only when artist folders are on) nests `Music/<Artist>/<Album>` the same way — still `{folder_id}/{basename}` on the wire, not string paths. Preferences live in app data `config.json`.
- **Default is PyMTP** (Stable Mode unchecked). Auto-connect: while Stable Mode is off (and auto-reconnect is enabled), a ~3s poll quietly maintains the PyMTP session: connect when absent, **probe liveness** when a session looks open (stale pointers after unplug), disconnect + clear art + retry when the device is gone. Absence is logged once per unplug streak (no dialogs). **Device → Disconnect** stops auto-reconnect until **Device → Connect** (or turning Stable Mode off again). **Enabling Stable Mode** disconnects PyMTP so `mtp-sendtr` is not blocked by an open session; the left panel shows Stable Mode help text instead of the device graphic.
- **PyMTP sync** requires `PymtpDevice.is_connected()`; otherwise a warning points the user to Connect or Config → Stable Mode.
- Data dir: macOS `~/Library/Application Support/MtpManager/`; Linux `$XDG_DATA_HOME/mtpmanager` or `~/.local/share/mtpmanager/`; override with `MTP_MANAGER_DATA_DIR`.

### Track context menu and other operations

| Entry point | Actions |
|-------------|---------|
| Track list multi-select | **extended** selectmode: **Shift+click** range, **Ctrl+click** (Windows/Linux) or **⌘+click** (macOS) toggle. Group headers expand to their tracks. |
| Right-click track | **Sync N selected tracks** (when multi-select), **Sync this track**, **Sync Album**, **Sync all from Artist** (global format + active mode transport). Right-click inside a multi-selection keeps the selection. |
| **Transfer** menu | **Sync Entire Library**; **Sync Folder…**; **Sync Selected Tracks** (multi-select batch); **Resume Sync**; **Cancel Current Job** |
| **Device** menu | Connect, Disconnect, Device Info (only place to edit device name — applied on close if changed), Create Folder…, List Folders, List Files (experimental), List Tracks (experimental; fast `get_filelisting` + media filter; optional **Load tags for selection** via `get_track_metadata`), Delete Track (experimental; pick from file listing → `delete_object`), Get File Info (experimental; pick → `get_file_metadata`, listing fallback on ZEN), Get Track Info (experimental; pick audio-ish → `get_track_metadata` tags), Delete All Tracks… (experimental; same fast list path + confirm + batch `delete_object`, fatal abort) — Experimental only |

Device admin prompts use dialogs (`ui/dialogs.py`); there is no main-window path/name entry.

**USB listings never run on the Tk thread.** List Folders / Files / Tracks and the listing phase of Delete Track, Delete All Tracks, Get File Info, and Get Track Info go through `AppController._run_device_bg` → `TkBackgroundRunner` (same busy flag as transfers, so auto-connect poll does not race the session). List paths use an indeterminate bar. **Do not** use full-library `get_tracklisting` as the default List Tracks path on ZEN (multi-hour USB; no partial results until C returns). Tags are on-demand only. Long USB walks may still print `LIBMTP panic: unable to read in zero packet` to **stderr** (C library, not Python logging); that noise is often non-fatal.

After a heavy USB job the controller keeps a short **USB quiet window** (`_DEVICE_USB_COOLDOWN_S`) and treats a single failed liveness probe as a **soft-fail** (keep session; only disconnect after consecutive failures).

**Connect vs diagnostics:** Device → Connect and auto-connect only open the MTP session and read **identity** (name / manufacturer / model) for profile matching. They never call battery or storage APIs. Full `get_info` (battery, free/total/used space, serial, version) is **Device → Device Info** only; each optional field soft-fails so one bad property (historically `get_batterylevel` on recovering ZENs) does not abort the dialog or undo connect.

---

## Format targets and transcoding

- User-facing **fallback** targets (Config → Config…): **MP3**, **WMA**, **WAV**. Used when the source is *not* already playable on the matched device (or when no device profile is active).
- **Device-native passthrough:** each `DeviceProfile` lists `supported_audio_formats`. For Creative ZEN Vision:M that is `mp3`, `wma`, `wav`. After USB detect + profile match, sources already in a native format are sent **as-is** (no ffmpeg), even if they differ from the configured target — avoids lossy→lossy re-encodes. Logic: `domain/device_profile.needs_transcode`; profiles in `domain/device_profiles.py`. Profile is applied only when a device is detected (`AppController._apply_device_profile`); no profile → convert only if extension ≠ target (no ZVM assumption).
- Otherwise `FFmpegTranscoder.convert` writes into a **dual-buffer slot**: `TRANSCODE_0.<ext>` / `TRANSCODE_1.<ext>` (`slot` 0 or 1). Batch `transfer_tracks` prepares track *i+1* on a helper thread into the alternate slot while track *i* is sent, so ffmpeg cannot clobber a file still in flight (CMD and PyMTP share this pipeline). WAV target uses `pcm_s16le`; WMA uses `wmav2`.
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
| Remote | `build_remote_path` → `100/<short>.mp3`; `-s` storage id |
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

### Resume Sync

Multi-track syncs (Entire Library, Folder, Album, Artist, **Selected tracks**) write a durable plan to `{data_dir}/sync_job.json` (`infra/sync_job.py`): ordered source paths, `next_index` (first not-yet-successful path), status, target format, and last error.

- After each successful send, `next_index` advances and the file is updated.
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
