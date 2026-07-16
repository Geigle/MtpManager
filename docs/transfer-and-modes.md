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
| Restore index | `ui/controllers._restore_library_from_index` | On startup: load durable JSON if present |
| Library menu | `ui/window` menubar **Library** | Select root / Update (see below) |
| Library toolbar | `ui/window` (full-width under title) | Status only: path + track count |
| Select root | `ui/controllers.on_select_library_root` | Folder picker → full scan → save index |
| Update | `ui/controllers.on_update_library` | Re-scan stored root; disabled if root missing/unreachable |
| Scan | `app/scan_library.scan_library` | Recursive music files → tags via mutagen |
| Persist index | `infra/library_index` | Write after Select/Update under app data dir |
| Index (in-memory) | `domain/library.Library` | Ordered list for listbox indices |
| Transfer strip | Left panel | Combobox + Execute Action (mode-specific options) |
| Action | `ui/controllers` | Single / artist / album / library / convert album |
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
| **Select Library Root…** | Folder picker → full scan → save index (always available) |
| **Update Library** | Re-scan stored root → rewrite index; **disabled** when no root is set or the root directory is not reachable |

- **Startup:** load `{data_dir}/library_index.json` when present. If `root_path` is still a directory, drop missing files and show a live listbox. If the root is **unreachable**, still populate the listbox from the index, mark path as `(unreachable) …`, grey out and disable list entries, and leave **Update Library** disabled.
- Transfers that need the library refuse to run while the root is unreachable (user is pointed at Select Library Root…).
- Left panel is **Transfer** (combo + Execute) plus Experimental **Device** (Connect / Disconnect / Device Info).
- Data dir: macOS `~/Library/Application Support/MtpManager/`; Linux `$XDG_DATA_HOME/mtpmanager` or `~/.local/share/mtpmanager/`; override with `MTP_MANAGER_DATA_DIR`.

---

## Format targets and transcoding

- User-facing targets: **MP3** and **WMA** (single-track actions; batch paths currently use MP3).
- `domain/library.is_format(path, fmt)` — extension-based; if already target format, skip convert.
- Otherwise `FFmpegTranscoder.convert` (`infra/ffmpeg_transcode.py`) writes a temp file; `transfer_track` always `cleanup`s it in `finally`.
- After convert, tags are re-read and merged (prefer original tags; take stream length/bitrate from converted file when useful).

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

## Open limitation: Tk main thread

Transfers run on the **Tk main thread** (`on_action` → `_transfer_one` / `_transfer_many`). A slow or hung libmtp/`mtp-sendtr` call freezes the window. Progress bar updates via `update_idletasks` only between tracks in batch. Worker-thread transfers are a documented follow-up, not implemented.

---

## Tests that lock the contract

```bash
.venv/bin/python -m unittest tests.test_remote_naming tests.test_pymtp_filetypes -v
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
