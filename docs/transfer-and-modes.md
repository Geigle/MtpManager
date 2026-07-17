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
| Index (in-memory) | `domain/library.Library` | Ordered list for listbox indices |
| Format preference | Left panel **Send as** MP3/WMA | Global; all Sync actions use `target_format()` |
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
- Left panel: mode tabs, **Send as** format, Experimental **Connect / Disconnect** + auto device graphic. Track sync is via **context menu**. **Device Info** is under the **Device** menu.
- **Experimental auto-connect:** while the Experimental tab is active, a ~3s poll quietly tries PyMTP connect. Success profiles the device and shows art (ZEN Vision:M vs generic). Absence is logged once per unplug streak (no dialogs). Switching to **Stable disconnects** PyMTP so `mtp-sendtr` is not blocked by an open session.
- **Experimental sync** requires `PymtpDevice.is_connected()`; otherwise a warning points the user to Connect or Stable Mode.
- Data dir: macOS `~/Library/Application Support/MtpManager/`; Linux `$XDG_DATA_HOME/mtpmanager` or `~/.local/share/mtpmanager/`; override with `MTP_MANAGER_DATA_DIR`.

### Track context menu and other operations

| Entry point | Actions |
|-------------|---------|
| Right-click track | **Sync this track**, **Sync Album**, **Sync all from Artist** (global format + active mode transport) |
| **Transfer** menu | **Sync Entire Library** (confirm); **Sync Folder…** (picker + scan + batch) |
| **Device** menu | Device Info (only place to edit device name — applied on close if changed), Create Folder…, List Folders, Get File Info…, Delete All Tracks… stub — Experimental only; require Connect |

Device admin prompts use dialogs (`ui/dialogs.py`); there is no main-window path/name entry.

---

## Format targets and transcoding

- User-facing targets: **MP3** and **WMA** (single-track actions; batch paths currently use MP3).
- `domain/library.is_format(path, fmt)` — extension-based; if already target format, skip convert.
- Otherwise `FFmpegTranscoder.convert` writes into a **dual-buffer slot**: `TRANSCODE_0.<ext>` / `TRANSCODE_1.<ext>` (`slot` 0 or 1). Batch `transfer_tracks` prepares track *i+1* on a helper thread into the alternate slot while track *i* is sent, so ffmpeg cannot clobber a file still in flight (CMD and PyMTP share this pipeline).
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
