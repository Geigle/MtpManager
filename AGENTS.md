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
- Long Experimental bulk / PTP 2002 session poison → [docs/debrief-zen-experimental-bulk-session-poison.md](docs/debrief-zen-experimental-bulk-session-poison.md)  
- Huge “low bitrate” convert / slow ZEN play start after sync → [docs/debrief-ffmpeg-cover-art-bloat.md](docs/debrief-ffmpeg-cover-art-bloat.md) (ffmpeg must map audio-only; never remux FLAC covers into temps)

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
8. **Drop audio-only ffmpeg mapping** on convert/extract (`-map 0:a:0`, `-vn`, strip metadata). Default ffmpeg maps remux FLAC cover art into “MP3” temps → multi‑MB low-bitrate objects and slow DAP open. See [docs/debrief-ffmpeg-cover-art-bloat.md](docs/debrief-ffmpeg-cover-art-bloat.md) / decisions D16.

---

## Preferred change surfaces

| Task | Where |
|------|--------|
| Remote filenames, folder/storage defaults | `mtpmanager/infra/remote_naming.py` + `tests/test_remote_naming.py` |
| CMD hang / timeout / fatal stderr patterns | `mtpmanager/infra/cmd_transport.py` |
| PyMTP send, filetype enum, ctypes | `mtpmanager/infra/pymtp_wrapper.py`, `pymtp_device.py` + `tests/test_pymtp_filetypes.py` |
| Transcode → send pipeline, batch abort | `mtpmanager/app/transfer.py` |
| Live batch queue (append mid-job) | `mtpmanager/app/transfer_queue.py` + controllers `_enqueue_tracks` |
| UI actions, mode, recovery dialogs | `mtpmanager/ui/controllers.py`, `window.py`, `dialogs.py` (incl. Manage Library) |
| Exclusive MTP/USB ownership (auto-connect vs sync) | `mtpmanager/app/device_io_gate.py` + poll/transfer/seed paths in `ui/controllers.py` |
| Artist/album selection | `mtpmanager/domain/library.py` |
| Library fuzzy search (toolbar; flat ranked results; `field:term` boosts) | `domain/library_search.py`, [docs/library-search.md](docs/library-search.md), toolbar in `ui/window.py`, debounce + flat rebuild in `ui/controllers.py` |
| Scan / tags | `app/scan_library.py`, `infra/mutagen_tags.py` |
| Album art thumbs (host UI) | `infra/album_art.py` (mutagen + Pillow; album header rows only) |
| Album art on device (PyMTP) | `app/album_art_device.py` after music **or podcast** Sync; podcast show RSS art via `ensure_podcast_artwork`; `device_index.device_albums`; Config `sync_album_art`; ZEN: abstract album + JPEG sample (not track) |
| Library index (SQLite + GUID; multi-root) | `infra/library_index.py`, `domain/library.py` (`root_paths`), `app/scan_library.py`, `domain/track_id.py`, `infra/app_paths.py` |
| Host playlists (M3U in index DB; local reorder/shuffle → device on next Sync) | `domain/playlist_m3u.py` (`move_paths`), `domain/playlist_shuffle.py` (artist merge + Spotify dither), `infra/playlists.py` (`move_paths_in_playlist`, `replace_playlist_tracks`), Playlists tab ↑/↓ + Shuffle context menu in `ui/window.py` / `ui/controllers.py`, dialog `ui/dialogs.py` (`ask_add_to_playlist`) |
| On-device playlist push (GUID→item_id) | `app/playlist_device.py`, `infra/device_index.item_ids_for_guids`, patched playlist APIs in `pymtp_wrapper` / `pymtp_device`; phase-2 after Sync playlist track transfer |
| Podcasts (RSS, ZENcast sync; video detect + optional video sync) | `infra/podcast_feed.py`, `infra/podcast_index.py` (`podcasts` + `podcast_episodes` by episode GUID — not music `tracks`), `app/podcast_ops.py`, Config `enable_experimental_tools` + `allow_video_podcasts_to_sync` / `sync_audio_podcasts_as_video` (both experimental; UI gated; runtime no-ops if tools off), Podcasts tab in `ui/window.py` / `ui/controllers.py`; Device tree joins episode GUID via `get_tracks_by_podcast_guids` (Music-folder audio reclassified when GUID is a podcast); parent ZENcast; default video-only → audio extract; XviD / still+XviD only when experimental tools + toggles on |
| Podcast schedule / full sync (1–N new eps since last full sync) | `app/podcast_schedule.py`, `app/podcast_ops.run_full_sync_host_pass`, Config `podcast_auto_*` / `podcast_max_new_per_show`, Library → Podcast Settings dialog in `ui/dialogs.py`, timer + device phase in `ui/controllers.py` |
| Device → Podcasts inventory | `domain/device_media.looks_like_podcast` / `podcast_refs_from_files`, `device_index.list_cached_podcast_refs`, Device Podcasts tree in `ui/window.py` / controllers; pull via existing device context menu |
| Device list join / skip-if-present | `domain/device_media.py`, `app/transfer.py`, controllers list/sync |
| Durable device inventory (list_files once) | `infra/device_index.py` + connect seed / Refresh menu in controllers |
| Device profiles / graphics | `domain/device_profile.py`, `domain/device_profiles.py`, `assets/devices/` |
| App config (send format, encode presets, …) | `infra/app_config.py` (`config.json`); encode model `domain/audio_encode.py`; ffmpeg map `infra/ffmpeg_transcode.py`; Config dialog `ui/dialogs.show_config_dialog`; audiobook/podcast encode overrides in Podcast Settings (`audiobook_audio_encode`, `podcast_audio_encode`); per-show encode in `podcast_index` (`audio_encode_json`) |
| Track listing / media filter (ZEN) | `domain/device_media.py` + `pymtp_device.list_tracks` (filelisting + media filter). Device tree uses listing + host GUID join only — **no** auto `get_track_metadata`. Explicit: Device track context **Fetch track tags…** → `enrich_track_refs_with_embedded_fallback` (metadata then download/mutagen) + `tests/test_enrich_tracks.py` |
| Headless CLI / MCP for agents | `mtpmanager/headless/` + `mtpmanager/cli/` + `mcp_server.py`; cross-process USB lock `infra/device_session_lock.py`; [docs/agent-interface.md](docs/agent-interface.md) |
| Export map / retail zip / restore | `infra/device_export_map.py`, `infra/retail_package.py`, `app/retail_ops.py` + `tests/test_device_export_map.py`, `tests/test_retail_package.py` |
| Send Video (Video 120 / TV 124) | `DeviceVideoOptions` + presets in `device_profile.py` / `device_profiles.py`; encode `infra/ffmpeg_video.py`; `app/device_ops.prepare_and_send_video`; notebook UI `dialogs.ask_video_destination`; `tests/test_send_video.py` |

---

## How to run

```bash
./MtpManager.sh
# or: .venv/bin/python -m mtpmanager

.venv/bin/python -m unittest tests.test_remote_naming tests.test_pymtp_filetypes -v
```

macOS needs Homebrew Python 3.13 + Tk + libmtp — see **PLATFORMS.md** before fighting import/Tk aborts.

Logs: platform log dir (`~/Library/Logs/MtpManager` on macOS); see root README.
