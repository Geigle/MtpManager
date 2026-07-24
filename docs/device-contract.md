# MTP / ZEN send contract

**Do not rebreak this.** Wrong parent, storage, or object names cause 99% finalize failures, silent rejects, or poisoned sessions. Both transports must honor the same rules.

**Code:** `mtpmanager/infra/remote_naming.py`  
**Consumers:** `cmd_transport.py`, `pymtp_device.py`  
**Tests:** `tests/test_remote_naming.py`  
**How we learned:** [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md), [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md)  
**Basename length evidence:** [basename-limit-evidence.md](./basename-limit-evidence.md)

---

## Target device assumptions

Defaults are **Creative ZEN Vision:M–centric** (VID `041e`, PID `413e`). They work for this player; other devices may differ.

| Constant | Value | Source |
|----------|--------|--------|
| `DEFAULT_MUSIC_FOLDER_ID` | **100** | List Folders / `mtp-folders`: folder 100 == `"Music"` |
| `DEFAULT_STORAGE_ID` | **`0x00010001`** (65537) | `mtp-detect`: Storage Media |
| `MAX_REMOTE_BASENAME` | **56** | Empirical send hygiene (margin under a suspected ~64 boundary from a ZEN finalize incident) — **not** a proven hard device max; see [basename-limit-evidence.md](./basename-limit-evidence.md) |

These are hardcoded in `remote_naming` and constructor defaults on `CmdTransport` / `PymtpDevice`. **Auto-discovery of folder/storage IDs is future work**, not present today.

### ZEN Vision:M top-level folder IDs (`ZEN_VISION_M_FOLDER_IDS`)

Captured via **Device → List Folders** on a real Creative ZEN Vision:M (same layout as `mtp-folders`). Code: `mtpmanager/infra/remote_naming.py`.

| ID | Name | Notes |
|----|------|--------|
| **100** | Music | **Track send parent** (`DEFAULT_MUSIC_FOLDER_ID`) |
| 104 | My Playlists | |
| 108 | My Recordings | |
| 112 | My Organizer | |
| 116 | Pictures | |
| **120** | Video | **Device → Send Video…** when user picks Video (`DEFAULT_VIDEO_FOLDER_ID`) |
| **124** | TV | **Device → Send Video…** when user picks TV show (`DEFAULT_TV_FOLDER_ID`) |
| 128 | ZENcast | Podcasts |
| 132 | My Slideshows | |

Use **numeric IDs**, never string paths like `Music/...`.

---

## Remote path shape

### Correct

```text
100/<32-hex-guid>.mp3
```

Examples:

```text
100/a1b2c3d4e5f6789012345678abcdef01.mp3
100/9f86d081884c7d659a2feaa0c55ad015.wma
```

- **Parent** is a **numeric folder id** (Music = 100), not the string `Music`.
- **Basename** is the host track GUID (32 hex) plus the real extension (`.mp3`, `.wma`, …).
- Full title/artist/album go in **tags**, not the ObjectFileName.

### Incorrect (do not invent)

```text
Music/Artist/Album/Mick Gordon - Doom ... - 08 Flesh & Metal
```

Why nested paths fail:

- `mtp-sendtr` treats `dirname(remote)` as the **parent** and `basename` as the object name.
- Parent resolution uses libmtp `parse_path()` against **existing** folders only.
- Nested `Music/Artist/Album` is **not** created like a filesystem hierarchy.
- Result: bad/zero parent context → finalize fails (`get_suggested_storage_id`, cache errors) even when bulk transfer reaches ~99%.

PyMTP does not use a path string for parent; it sets `LIBMTP_Track.parent_id` and a basename-only `filename`. The **logical** shape is the same: folder 100 + short name.

---

## Storage ID

| Value | Meaning |
|-------|---------|
| **0** | Default if `-s` omitted / field left zero — **broken** on this ZEN |
| **`0x00010001`** | Storage Media — required |

With unresolved parent/storage, finalize hits:

```text
get_suggested_storage_id(): could not get storage id from parent id
Could not retrieve updated metadata
```

CMD always passes `-s <storage_id>`. PyMTP sets `mt.storage_id` and refreshes storage via `LIBMTP_Get_Storage` before send when possible.

---

## Basename rules

Implemented by `sanitize_component` / `build_remote_path` (and `domain/track_id` for GUIDs).

### Production (GUID mode)

When a host track GUID is available (normal send path):

1. **Shape:** `100/{32-hex-guid}{ext}` — e.g. `100/a1b2c3d4e5f6789012345678abcdef01.mp3`.
2. **Parent:** always Music folder **100** (flat; ignore artist/album folder prefs).
3. **GUID:** lowercase UUID4 hex without hyphens (32 chars). Safe charset; length well under `MAX_REMOTE_BASENAME` (56).
4. **Extension required** on the object name (e.g. `.mp3`).
5. **Inventory key:** any file under Music whose basename **stem** is that GUID counts as “already on device” (extension may differ after transcode).

Host SQLite maps GUID → path + tags. Device List Tracks uses `list_files` + join; bulk device track tags are not required for display.

### Legacy fallback (no GUID)

If `guid` is omitted (tests / rare paths), the older short title form applies:

1. **Max body ~56** (`MAX_REMOTE_BASENAME`), including extension budget. Details: [basename-limit-evidence.md](./basename-limit-evidence.md).
2. **Strip unsafe characters** (replaced with space, then collapsed whitespace):

   ```text
   / \ : * ? " < > | &  and control chars (\x00-\x1f)
   ```

3. Prefer compact form: `{trackno} {title}{ext}` (e.g. `08 Flesh Metal.mp3`).
4. If the candidate is still tiny (&lt; 4 chars after sanitize), fall back to `{trackno} {artist} {title}`.
5. Empty components become `"unknown"`.

**Tags** may still contain `&`, long titles, full album names, etc. Only the **on-wire object filename** is a GUID (or sanitized short title).

Do **not** invent nested string paths like `Music/Artist/Album/...`.

---

## Tags vs filename split

| Channel | Content |
|---------|---------|
| Remote **filename** | GUID + extension (production), short/safe under length limit |
| **Tags** / metadata flags | Full title, artist, album, genre, track number, year |

CMD (`mtp-sendtr`): `-t`/`-a`/`-l`/… carry full metadata; remote path is only parent + basename.

PyMTP: `LIBMTP_Track` title/artist/album fields keep full tags; `filename` is basename only.

---

## Duration and year formatting

| Field | Rule | Code |
|-------|------|------|
| Duration (CMD `-d`) | Integer seconds (`int(round(length_sec))`) | `cmd_transport._duration_arg` |
| Duration (PyMTP) | Milliseconds (`round(length_sec * 1000)`) | `pymtp_device.send_track` |
| Year (CMD `-y`) | 4-digit year when present | `remote_naming.year_arg` |
| Date (PyMTP) | `YYYY0101T0000.0` when year known | `pymtp_device._year_date_field` |

Do not pass floating durations like `422.04` or full ISO dates like `2016-09-28` as the CMD year flag.

---

## Shared API

```python
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,  # 100
    DEFAULT_STORAGE_ID,       # 0x00010001
    MAX_REMOTE_BASENAME,      # 56
    build_remote_path,        # meta + ext [+ guid] → "100/<guid>.mp3"
    split_remote_path,        # "100/<name>.mp3" → (100, basename)
    sanitize_component,
    year_arg,
)
from mtpmanager.domain.track_id import new_track_guid, guid_from_remote_name
```

- **CMD:** `build_remote_path(..., guid=)` → last argv to `mtp-sendtr`; storage via `-s`.
- **PyMTP:** `build_remote_path` + `split_remote_path` → `parent_id` + basename; storage on `LIBMTP_Track`.

Any new transport **must** use this module (or equivalent constants) rather than inventing `Music/Artist/Album/...` paths.

---

## Diagnosis checklist

When a send fails near the end or rejects immediately:

1. **Progress** — mid-file vs **99%/finalize**.
2. **Storage ID** in logs/CMD output — `0` is a red flag; expect `65537` / `0x00010001`.
3. **`mtp-detect`** — confirm Storage Media id and free space (post-death “full” is often a lie).
4. **`mtp-folders`** — confirm Music folder id (100 on this ZEN).
5. **Remote basename** — within `MAX_REMOTE_BASENAME` (56) send budget; no `& \ / : * ? " < > |`; extension present. Longer names may already exist on-device from other tools; that does not relax our send policy ([basename-limit-evidence.md](./basename-limit-evidence.md)).
6. **Cascade after unplug** — PTP `02ff`, “storage full or corrupt” often means **session dead**, not root cause.
7. **App abort** — one fatal `TransportError` should stop the batch; if not, transfer error handling regressed.
8. **Experimental only** — `filetype=2` for MP3 (not 1); libmtp errorstack in logs, not bare `CommandFailed`.

```bash
mtp-detect | tail -n 80
mtp-folders
```

---

## Explicit non-goals (today)

- **Defaults are ZEN-centric.** Multi-device folder/storage discovery is not implemented.
- Nested artist/album folders on the device are **not** created by this app’s send path.
- Preflight free-space checks before large albums are optional follow-up, not required for correctness of parent/storage/name.
