# System architecture

MtpManager loads music onto picky MTP players (especially Creative ZEN Vision:M). MTP is an unreliable device protocol: Windows Media Player, Gnomad, and stock tooling fail for this use case. The app is a **small hexagonal layout** with dual transfer modes and a hard-won device send contract—not a general media library suite.

**Related:** [device-contract.md](./device-contract.md) · [transfer-and-modes.md](./transfer-and-modes.md) · [decisions.md](./decisions.md)

---

## Problem framing

- Old players speak MTP poorly; host software often assumes modern devices or WMP-centric workflows.
- Need: FLAC (etc.) → device formats (MP3/WMA/WAV on ZEN), reliable track send, honest failure handling when the session dies. Native formats are passed through without re-encode.
- Hard lessons: nested remote paths, storage id 0, long object names, ignored subprocess status, and broken PyMTP bindings all look like “USB is haunted” until diagnosed. See debriefs under `docs/`.

---

## Layer diagram

```text
  ui  →  app  →  domain
           ↓         ↑
         ports  ←  infra
```

**Invariant:** dependency direction is `ui → app → domain/ports ← infra`.

- `domain` and `ports` have no MTP CLI construction, no Tk, no ffmpeg subprocess details.
- `ui` must not embed `mtp-sendtr` argv or libmtp ctypes; it picks a transport via the controller and calls app use cases.
- `infra` implements ports; adapters are composed at the edges (`AppController`, `__main__`).

---

## Package map

| Package | Responsibility | Key modules |
|---------|----------------|-------------|
| `domain/` | Pure models + library selection logic | `models.py` (`Track`, `TrackMetadata`, `DeviceInfo`), `library.py`, `device_profile.py` / `device_profiles.py` (player matching) |
| `ports/` | Protocols + shared error type | `transport.py` (`Transport`, `TransportError`), `device.py` (`DevicePort`), `tags.py`, `transcoder.py` |
| `app/` | Use cases (orchestration only) | `transfer.py`, `scan_library.py`, `device_ops.py` |
| `infra/` | libmtp / ffmpeg / mutagen / logging / library index | `cmd_transport.py`, `pymtp_device.py`, `pymtp_wrapper.py`, `remote_naming.py`, `ffmpeg_transcode.py`, `mutagen_tags.py`, `logging_setup.py`, `app_paths.py`, `library_index.py` |
| `ui/` | Tk layout + event wiring | `window.py` (menus, track context, format, status toolbar), `controllers.py`, `dialogs.py`, `formatting.py`, `bg.py` |

---

## Composition root

| Entry | Role |
|-------|------|
| `./MtpManager.sh` | Ensures `.venv` (Homebrew Python 3.13 on macOS), runs `mm.py` |
| `mm.py` | Thin launcher → `mtpmanager.__main__.main` |
| `python -m mtpmanager` | Same: configure logging, build UI + device, mainloop |

`mtpmanager/__main__.py` wires:

1. `configure_logging()` / `prune_old_logs()`
2. `MainWindow()` + `PymtpDevice()` + `AppController(window, device)` (index restore scheduled on a background thread; mainloop is not blocked)
3. `window.mainloop()`

`PymtpDevice` is always constructed (for Experimental Connect / device admin). Stable transfers use a **separate** `CmdTransport()` instance and do not require an open PyMTP session.

---

## Dual-mode composition

`AppController._transport()` (`ui/controllers.py`):

| UI tab | Mode id | Transport |
|--------|---------|-----------|
| **PyMTP (default)** | `"experimental"` | `self.device` (`PymtpDevice`) — also implements device admin |
| **Stable Mode** | `"stable"` | `CmdTransport()` — one `mtp-sendtr` process per track; Config menu toggle |

UI action surfaces (`ui/window.py`):

- **Track context menu** (both modes): Sync this track / Album / Artist.
- **Transfer** menubar: entire library / folder sync.
- **Device** menubar (Connect / Disconnect / Device Info + admin tools; enabled when Stable Mode is off) + left-panel device graphic (`domain/device_profile` + `assets/devices/`).

PyMTP is the default (aspirational) path. Stable Mode is an opt-in Config toggle for the proven `mtp-sendtr` subprocess path. PyMTP **does not** silently fall back to CMD on failure (see [decisions.md](./decisions.md) D3).

---

## Data flow (high level)

```text
[index load | Library menu Select/Update] → scan_library → Library[Track]
     → user action → transfer_track(s)
     → (optional) FFmpegTranscoder → Transport.send_track
```

Chrome: **Library** menubar (Select root / Update); full-width **status toolbar** (path + count); left panel is PyMTP device session (or Stable Mode help when that toggle is on). Details: [transfer-and-modes.md](./transfer-and-modes.md). Durable library index and `config.json` live under the app data dir (`infra/app_paths.py` + `infra/library_index.py` + `infra/app_config.py`).

Remote object naming for **both** transports is centralized in `infra/remote_naming.py` ([device-contract.md](./device-contract.md)).

---

## Logging architecture

Configured in `infra/logging_setup.py`; paths documented in root [README.md](../README.md).

| File | Role |
|------|------|
| `mtpmanager.log` | Full app detail (DEBUG+), size-rotated |
| `errors.log` | ERROR+ only |
| `transfer-YYYYMMDD-HHMMSS.log` | One file per transfer batch / single-track session |

Platform defaults: macOS `~/Library/Logs/MtpManager`; Linux `~/.local/share/mtpmanager/logs` (or XDG). Override with `MTP_MANAGER_LOG_DIR`. Console defaults to INFO; set `MTP_MANAGER_DEBUG=1` for DEBUG on console.

---

## What is intentionally not abstracted yet

| Gap | Where it lives today |
|-----|----------------------|
| Hardcoded ZEN Music folder / storage defaults | `remote_naming.DEFAULT_*`; constructors on both transports |
| Multi-device discovery | Not implemented; user must match device layout |
| Transfer send still blocking in worker | Convert/send pipeline + UI job are off the Tk thread; each `send_track` still blocks the transfer worker until the device finishes |
| Single-object metadata after picker | Listing is backgrounded; live `get_file_metadata` / one-shot `get_track_metadata` (Get Track Info) / one-shot `delete_object` still run on the main thread after the user picks (usually short; can still hitch). List Tracks → **Load tags for selection** batches `get_track_metadata` on a worker |
| Upstream-maintained libmtp Python binding | Stock pymtp patched in-process via `pymtp_wrapper.py`; hazards [pymtp-binding-hazards.md](./pymtp-binding-hazards.md); coverage [libmtp-api-coverage.md](./libmtp-api-coverage.md) |

**Device admin (experimental, implemented):** **List Files** / **List Tracks** (fast `get_filelisting` + media filter; ids/filenames; optional **Load tags for selection** via per-id `get_track_metadata` — not full-library `get_tracklisting`); single **Delete Track** (file listing picker + `delete_object`); **Delete All Tracks** (same fast list path + confirmed batch `delete_object`, fatal abort); **Get File Info** / **Get Track Info** (picker + `get_file_metadata` / `get_track_metadata`). Full-device listings use `_run_device_bg` so USB walks do not freeze Tk.

These are known limitations, not accidental omissions in the docs. Product follow-ups stay out of this architecture description except as honest gaps.
