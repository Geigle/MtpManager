# AGENTS.md — AI onboarding for MtpManager

## What this project is / is not

**Is:** A small desktop app (Tk) that loads music onto picky MTP players, especially Creative ZEN Vision:M. Hexagonal layout (`domain` / `ports` / `app` / `infra` / `ui`). Dual transfer modes: **Stable** (`mtp-sendtr` subprocess) and **Experimental** (in-process PyMTP). FLAC→MP3 (etc.) via ffmpeg.

**Is not:** A general-purpose media library, a multi-device auto-discovery suite, or an aspirational redesign of libmtp. Prefer durable docs + debriefs over inventing layers the tree does not have.

---

## Mandatory reading order

1. [docs/README.md](docs/README.md) — index  
2. [docs/architecture.md](docs/architecture.md) — layers and composition  
3. [docs/device-contract.md](docs/device-contract.md) — **send rules you must not rebreak**  
4. [docs/decisions.md](docs/decisions.md) — why dual mode, fatal abort, no silent fallback  
5. [docs/transfer-and-modes.md](docs/transfer-and-modes.md) — when changing transfer/UI  

**Also read the relevant debrief when changing transport or send behavior:**

- CMD / hang / 99% finalize → [docs/debrief-zen-track-send-failure.md](docs/debrief-zen-track-send-failure.md)  
- PyMTP / filetype / ctypes / fallback policy → [docs/debrief-pymtp-transfer-failure.md](docs/debrief-pymtp-transfer-failure.md)  
- Opening **any new stock pymtp API** or Device menu path → [docs/pymtp-binding-hazards.md](docs/pymtp-binding-hazards.md) (failure classes + predicted breaks)  
- “Does libmtp/pymtp support X?” / coverage gaps → [docs/libmtp-api-coverage.md](docs/libmtp-api-coverage.md)  

Run/setup: [README.md](README.md), [PLATFORMS.md](PLATFORMS.md).

---

## Hard invariants

Do **not**:

1. **Invent nested remote paths** like `Music/Artist/Album/long name`. Remote shape is `100/<guid>.mp3` (numeric Music folder id + 32-hex track GUID + extension). See `mtpmanager/infra/remote_naming.py` and `domain/track_id.py`.
2. **Silent-fallback Experimental → CMD** on send failure. Experimental is pure PyMTP; UI guides the user to Stable Mode.
3. **Continue a batch after fatal `TransportError`**. Abort remaining tracks; session is likely poisoned.
4. **Leave storage id at 0** or omit Music parent 100 for this ZEN defaults contract.
5. **Put full titles with `&` / 64-char basenames** on the wire object name. Tags may be full; ObjectFileName is the track GUID (+ ext), not a title string.
6. **Import stock pymtp without** `mtpmanager.infra.pymtp_wrapper` (macOS lib path + filetype + ctypes fixes).
7. **Embed `mtp-sendtr` construction in `ui/`** — use ports/app + `AppController._transport()`.

---

## Preferred change surfaces

| Task | Where |
|------|--------|
| Remote filenames, folder/storage defaults | `mtpmanager/infra/remote_naming.py` + `tests/test_remote_naming.py` |
| CMD hang / timeout / fatal stderr patterns | `mtpmanager/infra/cmd_transport.py` |
| PyMTP send, filetype enum, ctypes | `mtpmanager/infra/pymtp_wrapper.py`, `pymtp_device.py` + `tests/test_pymtp_filetypes.py` |
| Transcode → send pipeline, batch abort | `mtpmanager/app/transfer.py` |
| Live batch queue (append mid-job) | `mtpmanager/app/transfer_queue.py` + controllers `_enqueue_tracks` |
| UI actions, mode, recovery dialogs | `mtpmanager/ui/controllers.py`, `window.py` |
| Artist/album selection | `mtpmanager/domain/library.py` |
| Scan / tags | `app/scan_library.py`, `infra/mutagen_tags.py` |
| Album art thumbs | `infra/album_art.py` (mutagen + Pillow; album header rows only) |
| Library index (SQLite + GUID) | `infra/library_index.py`, `domain/track_id.py`, `infra/app_paths.py` |
| Device list join / skip-if-present | `domain/device_media.py`, `app/transfer.py`, controllers list/sync |
| Durable device inventory (list_files once) | `infra/device_index.py` + connect seed / Refresh menu in controllers |
| Device profiles / graphics | `domain/device_profile.py`, `domain/device_profiles.py`, `assets/devices/` |
| App config (send format, …) | `infra/app_config.py` (`config.json` under data dir) |
| Track listing / media filter (ZEN) | `domain/device_media.py` + `pymtp_device.list_tracks` (filelisting + media filter) + on-demand tags via `device_ops.enrich_track_refs` / `get_track_metadata` + `tests/test_device_media.py` |

---

## How to run

```bash
./MtpManager.sh
# or: .venv/bin/python -m mtpmanager

.venv/bin/python -m unittest tests.test_remote_naming tests.test_pymtp_filetypes -v
```

macOS needs Homebrew Python 3.13 + Tk + libmtp — see **PLATFORMS.md** before fighting import/Tk aborts.

Logs: platform log dir (`~/Library/Logs/MtpManager` on macOS); see root README.
