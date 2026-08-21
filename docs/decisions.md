# Key decisions (ADR-lite)

Durable product/engineering choices earned from code and debriefs. Each entry: **Context → Decision → Rationale → Consequences → Source**.

Debriefs remain the forensic narrative; this file is what we keep doing.

---

## D1 — Hexagonal ports over a single god-module

**Context:** Early prototypes mixed Tk, pymtp, and CLI in large scripts. MTP failure modes and UI wiring are hard to reason about when everything shares one module.

**Decision:** Split into `domain` / `ports` / `app` / `infra` / `ui`. Protocols: `Transport`, `DevicePort`, tags, transcoder. Domain stays pure data + library logic.

**Rationale:** Swappable send backends (CMD vs PyMTP), testable naming without a device, UI that only maps events to use cases.

**Consequences:** New features should land in the right layer (e.g. remote naming in infra, selection heuristics in domain). Do not reintroduce MTP CLI construction inside `ui/`.

**Source:** Package layout; root README; [architecture.md](./architecture.md).

---

## D2 — Dual modes: PyMTP (default) vs Stable (`mtp-sendtr`)

**Context:** libmtp’s `mtp-sendtr` is battle-tested for one-shot sends. In-process PyMTP enables device admin (folders, name, listing) and is the aspirational path, but stock bindings are fragile.

**Decision:** PyMTP is the default UI (left-panel device session, Device menu, auto-connect). Stable Mode is a **Config → Stable Mode** checkbutton that switches transfers to `CmdTransport` (`mtp-sendtr` per track) and disables Device admin. Preference is persisted as `stable_mode` in `config.json`. Composition: `AppController._transport()`.

**Rationale:** Present the aspirational in-process path front-and-center; keep the proven subprocess path one menu toggle away with clear left-panel copy. Users choose deliberately.

**Consequences:** Two code paths must share the remote contract (D4). PyMTP requires Connect/auto-connect before send (UI gates sync/admin). Track sync is mode-agnostic (context menu); Device admin is PyMTP-only. Enabling Stable Mode disconnects any open PyMTP session so `mtp-sendtr` can claim the device.

**Source:** `ui/window.py`, `ui/controllers.py`, `infra/app_config.py`; [transfer-and-modes.md](./transfer-and-modes.md).

---

## D3 — No silent CMD fallback from Experimental on send failure

**Context:** After layered PyMTP bugs, it was tempting to auto-retry via `mtp-sendtr` so “tracks still land.”

**Decision:** PyMTP `send_track` is pure libmtp/PyMTP only. On failure, raise `TransportError` and show recovery steps pointing the user to **Config → Stable Mode**. Never call CMD from PyMTP send without an explicit user mode switch.

**Rationale:** Silent fallback hides regressions, mixes transports, and makes debugging impossible. Honest UX preserves PyMTP as a real binding under test.

**Consequences:** Users must Disconnect (and often replug) then enable Stable Mode. Logs are the source of truth for pure PyMTP failures.

**Source:** [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md); `pymtp_device.py` docstring; `controllers._transfer_recovery_hint`.

---

## D4 — Shared remote naming/storage contract for both transports

**Context:** CMD learned Music folder 100, storage `0x00010001`, short names the hard way. Experimental initially left parent/storage at 0 and used long basenames—same failure class.

**Decision:** Single module `infra/remote_naming.py` used by `CmdTransport` and `PymtpDevice`. Constants: `DEFAULT_MUSIC_FOLDER_ID`, `DEFAULT_STORAGE_ID`, `MAX_REMOTE_BASENAME`, `build_remote_path` / `split_remote_path`.

**Rationale:** One device, one contract; parity prevents “works on Stable only because of different paths.”

**Consequences:** Nested `Music/Artist/Album/...` paths are forbidden. Defaults are ZEN-centric until multi-device discovery exists.

**Source:** [device-contract.md](./device-contract.md); both debriefs; `tests/test_remote_naming.py`.

---

## D5 — Fatal `TransportError` aborts the batch

**Context:** After one bad finalize, the MTP/USB session is often dead. Continuing the album produced cascading PTP errors and looked like “everything after track N is broken.”

**Decision:** `TransportError.fatal` (default True for both transports). `transfer_tracks` aborts remaining items when `stop_on_fatal` (default True) and re-raises for the UI.

**Rationale:** Fail fast; force intentional reconnect; avoid writing into a poisoned session.

**Consequences:** Partial albums need resume from the failed track after unplug/replug. Non-fatal continue is API-possible but not used by production send paths.

**Source:** [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md); `ports/transport.py`, `app/transfer.py`.

---

## D6 — Size-based timeout + post-fatal grace kill for hung `mtp-sendtr`

**Context:** After a failed finalize, stock `mtp-sendtr` still runs album association (`add_track_to_album` / `LIBMTP_Get_Album_List`) and may hang forever. Ignoring process status froze the UI until unplug.

**Decision:** Stream stdout/stderr; match fatal patterns; apply size-based overall timeout; if fatal diagnostics appear and the process stays alive, kill after ~8s grace (`_POST_FATAL_GRACE_SEC`). Raise fatal `TransportError`.

**Rationale:** We cannot change stock `mtp-sendtr`; we can bound hangs and surface real errors.

**Consequences:** Kill may leave a messy device session—batch abort (D5) and user replug still required. Timeout scaling lives in `cmd_transport._timeout_for`.

**Source:** CMD debrief; `infra/cmd_transport.py`.

---

## D7 — Tags carry full metadata; remote filename is short/sanitized

**Context:** Verbose archive-style object names (exactly 64 chars, `&`, no extension in the Doom incident) stacked with bad parent/storage and failed at finalize. Player UI still needs real titles. Longer names can already exist on-device from other tools; that does not make long names a safe default for our send path.

**Decision:** Metadata flags/fields keep full title/artist/album (including `&`). Object basename is sanitized, length-bounded (`MAX_REMOTE_BASENAME = 56` as empirical send hygiene), extension required (`08 Flesh Metal.mp3`).

**Rationale:** Tags and object names are different channels on MTP; only the name is device-fragile on Creative-era firmware. 56 is a margin under a suspected ~64 boundary from local incident data, not a proven PTP/libmtp hard max.

**Consequences:** On-device browser may show short names; library views that use tags stay correct. Do not “fix” send by stuffing full `Artist - Album - Title` into the remote path. Do not raise the basename budget solely because Get File Info shows a longer existing object.

**Source:** [device-contract.md](./device-contract.md); [basename-limit-evidence.md](./basename-limit-evidence.md); CMD debrief.

---

## D8 — Patch stock pymtp in-process rather than forking PyPI package

**Context:** Stock pymtp is effectively unmaintained vs libmtp 1.1.x / Python 3 / arm64: missing `FOLDER=0` in filetype enum (MP3 labeled as WAV), missing ctypes `argtypes`, `Dump_Errorstack` without device pointer, Python 2 `has_key`, untyped `str`→`char*` (first character only on device), macOS `find_library` failure.

**Decision:** Load via `infra/pymtp_wrapper.py`: macOS lib path patch, mutate `LIBMTP_Filetype` in place, fix send/errorstack/folder/create/name bindings as we hit them. Unit-test filetype table and critical patches. Catalog patterns and predictions in [pymtp-binding-hazards.md](./pymtp-binding-hazards.md).

**Rationale:** Small project surface; avoids maintaining a full fork until an upstream binding is viable. Failures arrive **layered** (contract → enum → ctypes → strings → session); a living hazard list stops rediscovering the same classes.

**Consequences:** Always import pymtp through the wrapper. Opening a new stock method requires a hazard checklist pass (encode strings, set argtypes, no `has_key`). Upgrading pymtp may require re-checking patches. Experimental send still device/session-dependent after binding fixes.

**Source:** [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md); [pymtp-binding-hazards.md](./pymtp-binding-hazards.md); `tests/test_pymtp_filetypes.py`.

---

## D9 — macOS: Homebrew Python 3.13 + wrapper for libmtp / Tkinter

**Context:** System/CLT Python breaks Tkinter on newer macOS; `ctypes.util.find_library("mtp")` returns None so pymtp import crashes.

**Decision:** Document and script Homebrew Python 3.13 + `python-tk@3.13` + `libmtp`. `MtpManager.sh` prefers `/opt/homebrew/bin/python3.13` when creating the venv. `pymtp_wrapper` patches find_library on Darwin.

**Rationale:** Platform-specific; Linux needs no find_library patch. Capture in `PLATFORMS.md` so setup is not rediscovered.

**Consequences:** Wrong Python = abort or missing libmtp symbols. Always use project `.venv` via `./MtpManager.sh`.

**Source:** [PLATFORMS.md](../PLATFORMS.md); `MtpManager.sh`; `pymtp_wrapper.py`.

---

## D10 — GPL-3.0-or-later alignment with PyMTP / libmtp stack

**Context:** PyMTP is GPL-3; libmtp is LGPL. Distributing a combined app that embeds PyMTP-style use requires GPL-compatible licensing.

**Decision:** Project licensed **GNU GPL v3 or later** (`LICENSE`, README).

**Rationale:** Legal alignment with dependency licenses; redistribution must stay GPL-compliant.

**Consequences:** Downstream forks/redistribution must honor GPL-3.0-or-later. Do not relicense to a more restrictive proprietary model without replacing GPL dependencies.

**Source:** Root README License section; `LICENSE`; PyMTP / libmtp licensing.

---

## D11 — SQLite library index with stable per-track GUID

**Context:** The JSON index (`library_index.json`) was a path+tags cache only. Device inventory could not be joined to the host library without relying on fragile bulk MTP track tags or inventing nested folders (which poisoned ZEN sessions).

**Decision:** Persist the library as SQLite `{data_dir}/library_index.db` (stdlib `sqlite3`): `library_meta` (`root_path` first root, `root_paths` JSON list of all roots, `scanned_at`), `tracks` (guid PK, path unique, full tag columns), optional `device_objects` (last-seen basename / item id). Assign a **32-char hex GUID** (UUID4 without hyphens) per track; preserve across rescans by absolute path. One-shot migrate from legacy JSON when the DB is missing; pre-v3 DBs gain `root_paths` seeded from `root_path`. API remains `save_library_index` / `load_library_index` in `infra/library_index.py`. **Library → Manage Library…** adds/removes roots and **Update Library** rescans all roots.

**Rationale:** Durable identity independent of MTP object ids; room for host↔device mapping without a general media-library product.

**Consequences:** Host rename of a file gets a new GUID (possible duplicate on device until cleaned). Index is app-private; on-device GUID names are vendor-locked to this app for inventory. No new pip deps.

**Source:** Experiment on GUID flat naming; `tests/test_library_index.py`.

---

## D12 — GUID ObjectFileName under Music 100; host DB for inventory

**Context:** Nested artist/album folders and bulk device tag listing failed on Creative ZEN Vision:M. Title-based basenames cannot be reliably matched after send without device metadata.

**Decision:** Send ObjectFileName as `{guid}{ext}` under parent **100** (flat Music). Full title/artist/album still written as MTP tags (D7 tag channel). Ignore experimental artist/album folder parents when a GUID is present. Device **List Tracks** uses `list_files` + media filter, then joins basename stems to SQLite for display. Multi-track sync **skips** tracks whose GUID stem is already on the device when Experimental listing is available.

**Rationale:** Minimizes folder object churn; inventory works with the fast filelisting path; tags still help players that index them.

**Consequences:** On-device file browser shows GUIDs. Skip-if-present requires a connected PyMTP session for `list_files` (Stable Mode without session does not skip). Foreign (non-GUID) files remain visible by raw name.

**Source:** [device-contract.md](./device-contract.md); `domain/track_id.py`, `remote_naming.build_remote_path(..., guid=)`, `device_media.enrich_refs_from_host`.

---

## D13 — Durable device file index; list_files only on connect/refresh

**Context:** Skip-if-present called `list_files` at the start of every sync job. Full `get_filelisting` walks are USB-heavy and appear to poison ZEN sessions when repeated. Admin List Files/Tracks did the same walk every menu click.

**Decision:** Persist device inventory in SQLite (`devices` + `device_files` in `library_index.db`), keyed by **MTP serial alone** when present. If serial is missing/placeholder, key by a fingerprint of **manufacturer + model only** (never friendly name). **Seed once** after Experimental connect (background `list_files` → replace rows). **Skip-if-present** and **List Files / List Tracks / delete pickers** read the cache only. **Update incrementally** on successful send (`record_send`) and successful delete (`remove_by_item_id`). **Device → Refresh Device Index…** forces one live listing. No per-sync `list_files`.

**Rationale:** One listing per connect is enough for app-driven sync/delete; external device changes need explicit refresh. Friendly name is user-editable (Device Info) and must not re-key or orphan the inventory. Serial is the stable hardware identity; mfr+model is the best no-serial fallback (two identical models without serials share one bucket — preferred over rename-fragile keys).

**Consequences:** Cache can go stale if another tool writes the player; user Refresh or reconnect. Stable Mode without a serial may not skip. MTP `item_id` remains best-effort (volatile across rebuilds); skip keys on GUID stem / ObjectFileName. Same-model players without serials share one index.

**Source:** `infra/device_index.py`; `ui/controllers.py` connect seed + skip path; `tests/test_device_index.py`.

---

## D15 — Album art via abstract MTP albums (not track samples)

**Context:** Host UI already showed album thumbs. Device cover art was missing. Live probe on Creative ZEN Vision:M showed `RepresentativeSampleData` on **ALBUM** objects only (JPEG ≤80×80, ≤24KB), not on MP3/WMA tracks. CMD `mtp-sendtr` album association via `Get_Album_List` is hang-class after bad finalize.

**Decision:** After successful Experimental (PyMTP) music **or podcast** Sync, group transferred items by albumartist+album (podcasts: show title), resolve object ids from `device_files`, **create or update** abstract albums (`Create_New_Album` / `Update_Album`), and `Send_Representative_Sample` JPEG. Music uses embedded/sidecar art; podcasts use show RSS artwork via `ensure_podcast_artwork` when episode files have no cover. Cache album id + art SHA-256 in `device_albums` (never `Get_Album_List`). Config → **Sync album art (PyMTP)** (default on). No-op in Stable Mode. Art failures are non-fatal to the transfer.

**Rationale:** Matches ZEN firmware; reuses host cover pipeline + existing podcast artwork cache; avoids hang-prone album list; skip re-send when art hash unchanged.

**Consequences:** Partial album/show syncs may create albums with incomplete membership until later items update the object. Without list, orphaned on-device albums from other tools are not merged. Stable Mode users get no art.

**Source:** `app/album_art_device.py`; `infra/album_art.prepare_device_cover_jpeg`; `pymtp_wrapper` sample/album bindings; live ZEN probe 2026-08.

---

## D14 — Retail package zip for Creative demos (iFlash restore)

**Context:** iFlash-upgraded ZENs lack stock Creative demo media. Full device exports mix user content with demos; restore needs a portable subset plus editable metadata.

**Decision:** **Transfer → Package Retail Demos…** filters a Get Tracks `device_media_map.json` to `flags.looks_like_retail_demo` host files, writes a zip (`restore_map.json` + `media/`). **Transfer → Restore Retail Package…** extracts and sends with **no GUID** ObjectFileName (`preferred_basename` / original short name), tags from `desired_tags`, and fatal batch abort. Reduced map is re-editable (`include_in_restore`, tags, notes).

**Rationale:** Separate study map (verbose full export) from transfer payload (small, retail-only). Keep retail ObjectFileNames for a stock-like on-device browser; library GUID mode stays for user music.

**Consequences:** Heuristic demos can false-positive; user edits full map flags before package or reduced map before restore. Video filetypes prefer ZEN Video folder parent when original parent is unknown.

**Source:** `infra/retail_package.py`, `app/retail_ops.py`; `tests/test_retail_package.py`.

---

## D16 — Transcode temps are audio-only (never remux cover art)

**Context:** ffmpeg’s default stream map copies FLAC/MP4 **attached pictures** into convert outputs as a second stream. Low-bitrate presets then sound compressed but stay multi‑MB; MTP bitrate tags look absurd; ZEN Vision:M can take on the order of **a minute** before playback starts on fat objects. Device cover art is already handled by abstract MTP albums (D15), not track-embedded images.

**Decision:** Every `FFmpegTranscoder.convert` / `extract_audio` recipe forces **audio-only** mapping: `-map 0:a:0`, `-vn`/`-sn`/`-dn`, and strip global metadata/chapters (`-map_metadata -1`, `-map_chapters -1`). Codec bitrate/VBR options apply only to that audio stream. Do not rely on extension-only outputs without an explicit map.

**Rationale:** Temp files exist solely to send playable audio under the remote contract. Host library art and D15 album samples are separate; stuffing pixels into the track file wastes device storage and hurts DAP open latency.

**Consequences:** Objects sent before the fix keep bloat until deleted and re-synced. Convert temps will not carry ID3 APIC from the source (MTP tags still come from host `TrackMetadata`). Changing ffmpeg option maps without preserving audio-only is a regression class — see the debrief.

**Source:** [debrief-ffmpeg-cover-art-bloat.md](./debrief-ffmpeg-cover-art-bloat.md); `infra/ffmpeg_transcode.py` (`_audio_only_map_options`); `tests/test_audio_encode.py`.

---

## D17 — Main-window chrome: flat frames + ttk interactive stack

**Context:** The main window mixed classic Motif-like sunken `Frame` wells (root, toolbar, left/right, bottom) with selective `ttk` (Notebook, Treeview, Combobox, Progressbar, Scale) and classic `Button`/`Entry`/`Scrollbar` in the same strips — high visual complexity and a poor base for macOS / KDE / GNOME polish.

**Decision (UI visual pass phase 1):**

1. **Frame language:** Main layout regions are **flat** (`borderwidth=0`, no sunken relief). Hairline `ttk.Separator` marks toolbar↔body, sidebar↔content, and body↔bottom.
2. **Control stack:** Main-window **interactive** controls prefer **ttk** (`Button`, `Entry`, `Scrollbar`, plus existing Notebook/Treeview/Combobox/Progressbar/Scale). Shared setup lives in `mtpmanager/ui/chrome.py` (`apply_chrome_baseline`, `flat_frame`, separators). Platform theme still defaults (Aqua on macOS); Linux desktop themes are phase 4b/4c.
3. **Documented classic-tk exceptions:** `Menu`; `Text` / `Listbox`; `Label` (including `PhotoImage` device graphic); custom `_HoverTip`; **dialogs** in `dialogs.py` (not rewritten in phase 1).

**Rationale:** One chrome language so later OS blend is palette/theme, not undoing nested MDI wells. ttk on interactive strips removes the dual-skin look without a Qt/GTK rewrite.

**Consequences:** Controllers keep using `configure(state=DISABLED|NORMAL)` (works on ttk). Dialogs still look classic until a later phase. Glyph vs text button grammar is phase 3 (`docs/ui-visual-pass.md`).

**Source:** [ui-visual-pass.md](./ui-visual-pass.md); `mtpmanager/ui/chrome.py`, `mtpmanager/ui/window.py`.

---

## D18 — Podcasts master–detail + Device category strip (not nested tabs)

**Context:** Host Podcasts used a classic `Listbox` + vertical glyph column + episode table (O2), while Device nested a second `ttk.Notebook` of Music/Video/… under the outer media notebook (O3). Global Treeview rowheight for album art made playlist/episode rows needlessly tall (O11).

**Decision (UI visual pass phase 2):**

1. **Podcasts:** One **P4-like** horizontal toolbar; subscriptions and episodes are both **`ttk.Treeview`** (compact style). Layout stays **master–detail** (show list above episode table) as the **only** intentional exception to pure hierarchical media trees (P3).
2. **Device:** Replace the nested notebook with an **“On device:”** combobox + single content frame. Keep a Notebook-compatible shim (`device_notebook.select`) for existing call sites.
3. **Tree density:** `Thumb.Treeview` vs `Compact.Treeview` named styles in `ui/chrome.py`.

**Rationale:** Shallower Device navigation and one list widget language on Podcasts without forcing podcasts into artist/album hierarchy they do not have. Dense lists where art is absent.

**Consequences:** Controller podcast selection uses Treeview iids `ps:{id}` (not Listbox indices). Device UI tests should use `show_device_subview` / combobox, not nested tab geometry.

**Source:** [ui-visual-pass.md](./ui-visual-pass.md); `ui/window.py`, `ui/controllers.py`, `ui/chrome.py`.

---

## D19 — Control grammar: ASCII compact glyphs, combobox time, ttk.Scale only

**Context:** Toolbars mixed Unicode `×` `−` `↻` `↑` `↓` `▲` `▼` with English transport labels (O5). Podcast Settings used a large custom hour/min/AM·PM spinner grid (O6). Encode/shrink dialogs used classic `tk.Scale` while playback scrubber used `ttk.Scale` (O7).

**Decision (UI visual pass phase 3):**

1. **Compact actions** use ASCII `+` / `-` / `x`; reorder uses short English `Up` / `Dn`; refresh uses the word **Refresh** (not a circular arrow). Constants live in `mtpmanager/ui/chrome.py`.
2. **Tool / primary actions** stay short English on `Tool.TButton` (Play, Cancel, Sync Latest, …).
3. **Time of day** is hour + minute + AM/PM **readonly comboboxes** (`time_of_day_row`); max-episodes uses **`ttk.Spinbox`** (Entry fallback).
4. **Continuous values** use **`ttk.Scale` only**, via `make_ttk_scale` (resolution snap). No classic `tk.Scale` in app UI.

**Rationale:** One grammar, portable fonts on Linux, less custom chrome. Matches phase 1 ttk interactive stack.

**Consequences:** Context-menu shortcut strings may still say ⌥↑ / Alt+↑ (keyboard), while toolbar buttons say Up/Dn. Dialog primary buttons may remain classic `Button` until a later dialog chrome pass.

**Source:** [ui-visual-pass.md](./ui-visual-pass.md); `ui/chrome.py`, `ui/window.py`, `ui/dialogs.py`.

---

## D20 — macOS blend: system secondary text, platform reveal, Stable Mode About

**Context:** Dialog helper prose used light-only hex grays (`#333`–`#666`) that fail in dark Aqua (O12). Podcast reveal menus said “Finder” on every OS (O13). Stable Mode filled the Device panel with a multi-paragraph help wall (O9).

**Decision (UI visual pass phase 4a):**

1. **Secondary dialog labels** use `secondary_label_kwargs()` — on Darwin `fg=systemSecondaryLabelColor`; elsewhere omit *fg* (theme default).
2. **Reveal actions** use `reveal_in_file_manager_label()` (Finder / Explorer / File Manager by platform). `CTX_PODCAST_REVEAL_DOWNLOAD` is set at import from that helper.
3. **Stable Mode panel** shows a short caption (`STABLE_MODE_CAPTION`) plus **About Stable Mode…** (`messagebox` with full `STABLE_MODE_HELP`); device art remains the experimental-mode identity.

**Rationale:** Track macOS appearance without inventing a theme engine; keep Linux wording honest until 4b/4c.

**Consequences:** Transfer/playing row colors (O8) unchanged. Hover tips still use explicit light-panel colors (required for help-window chrome). KDE/GNOME theme picking remains phase 4b/4c.

**Source:** [ui-visual-pass.md](./ui-visual-pass.md); `ui/chrome.py`, `ui/window.py`, `ui/dialogs.py`.

---

## D21 — Video encode: orthogonal resolution + audio ladder

**Context:** ZEN Vision:M Send Video recipes hardcoded **640×480** (retail / A/V Out) and fixed 128 kbps stereo audio. The device panel is **320×240**; smaller frames (e.g. 160×120) are useful for storage. Multiplying recipe tabs per size would explode the notebook. Music/podcasts already share `AudioEncodeSettings`.

**Decision:** Keep recipe tabs as **container + video codec** only. Add orthogonal axes on `DeviceVideoOptions`:

1. **Allowed resolutions** from `domain/video_encode` catalog (ZEN: QQVGA / QVGA / VGA; default **QVGA**).
2. **Audio** via the same preset ladder as music/podcasts, clamped to what the recipe can mux (AVI→MP3, WMV→WMA).
3. **Video quality** for mpeg4/XviD: ``qscale:v`` (lower = higher quality; default 5) plus optional **slow encode** (`mbd=rd`, `trellis=2`, `+mv4+aic`, better cmp) to spend more CPU — especially useful at QQVGA/QVGA.

Effective encode = recipe ⊕ resolution ⊕ quality ⊕ `AudioEncodeSettings`. Last Send Video choices persist in `config.json`.

**Rationale:** Matches how still-video already overrides geometry separately; reuses proven audio UI; avoids tab combinatorial explosion; low-res encodes benefit from slower, higher-quality mpeg4 passes without bumping frame size.

**Consequences:** Callers must `apply_resolution` / `apply_audio_settings` / `apply_video_quality` (or `effective_video_preset`) before match-skip / encode. Podcast full-motion video uses `PodcastVideoEncodeSettings` (Config → Podcast Settings + per-show Encode Settings), with precedence per-show → podcast default → device defaults — separate from library Send Video last-used keys. Still-from-audio ladder (`audio_podcast_still_*`) stays independent. Bitrate recipes (WMV) ignore qscale; slow flags are mpeg4-only.

**Source:** `domain/video_encode.py`, `device_profiles.py`, `dialogs.ask_video_destination`, `infra/ffmpeg_video.py`, `infra/podcast_index.py`.
