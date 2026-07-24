# PyMTP / stock libmtp binding hazards

**Purpose:** Catalog of **patterns** of breakage we have already hit when using stock **pymtp** against modern **libmtp** (1.1.x) on **Python 3 / arm64 / macOS**, plus **predicted** failures for APIs we have not fully hardened yet.

**Code that applies fixes:** `mtpmanager/infra/pymtp_wrapper.py` (always import pymtp through this).  
**Device adapter:** `mtpmanager/infra/pymtp_device.py`.  
**Incident narratives:** [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) (send path), [device-contract.md](./device-contract.md) (remote shape).  
**Decision:** [decisions.md](./decisions.md) D8.

This is **not** a replacement for those debriefs. It is the living “what keeps breaking and what will break next” checklist.

---

## The pattern (why this keeps happening)

Stock **pymtp** is effectively a thin, stale ctypes binding written for **Python 2-era** libmtp. On our stack it fails in **layers**:

1. **Wrong domain contract** (our code): parent/storage 0, long names, inventing `Music/…` paths.  
2. **Stale C tables / structs** (stock pymtp): filetype enum off-by-one, incomplete device layout.  
3. **Python 2 leftovers**: `dict.has_key`, `os.path.exists(x) == None`, `== None` comparisons.  
4. **Untyped ctypes multi-arg calls** (especially arm64): missing `argtypes` / `restype` → wrong registers / stack.  
5. **String marshaling**: Python `str` passed where C wants `char *` without `c_char_p` → often **only the first character** on the device.  
6. **Opaque failures**: stock `debug_stack` dumps with a **NULL device** (PANIC) and drops real PTP text.  
7. **Session model**: long-lived PyMTP session after a bad send is often **poisoned** (`02ff`, cannot close session).

CMD / Stable Mode (`mtp-sendtr`) avoids most of 3–6 because the C CLI is maintained with libmtp. PyMTP is the aspirational path; **expect every new stock method we touch to need a wrapper patch** until proven otherwise.

---

## Failure taxonomy

| Class | Symptom | Root shape | First fix instinct |
|-------|---------|------------|--------------------|
| **A. Domain / ZEN contract** | Finalize ~99%, `get_suggested_storage_id`, empty parent | `parent_id`/`storage_id` 0; nested path strings | Use `remote_naming` (Music **100**, storage **`0x00010001`**, short basename) |
| **B. Filetype enum skew** | Immediate reject; log `filetype=1` for MP3 | Stock table omitted `FOLDER=0` → all values −1 vs libmtp 1.1 | Wrapper mutates `LIBMTP_Filetype` in place; `MP3 == 2` |
| **C. Missing ctypes argtypes** | Random `CommandFailed`, bus errors, wrong progress args | Multi-arg libmtp calls with default ctypes convention | Set `argtypes`/`restype` in wrapper before call |
| **D. char\* / first character only** | Folder/name becomes `"B"` for `"Blargh"` | `str` passed untyped; wide/char mix or bad pointer | UTF-8 `bytes` + `c_char_p` + stable buffer |
| **E. Python 2 API** | `AttributeError: has_key` | `dict.has_key` | Use `key in dict`; patch method in wrapper |
| **F. Bad error path** | `LIBMTP PANIC: … NULL device!`; empty `CommandFailed` | `Dump_Errorstack()` with no device; no errorstack log | Dump/Get with device pointer; log `error_text` |
| **G. Poisoned session** | Next PyMTP op fails immediately after a bad send | Long-lived session after PTP death | Disconnect/replug; Config → Stable Mode; no silent CMD fallback |
| **H. Platform load** | Import fails / no libmtp | `find_library("mtp")` is `None` on macOS | Wrapper patches Homebrew lib paths |
| **I. Exists-check bugs** | Wrong IOError / always/never fail | `os.path.exists(source) == None` (stock send_track) | Use `os.path.isfile` (already in patched send) |
| **J. Get_Filemetadata / proplist** | Listed, playable object → `ObjectNotFound` on Get File Info | libmtp wants ObjectInfo **+** MTP proplist; ZEN `BROKEN_MTPGETOBJPROPLIST_ALL` + single-object proplist fail → NULL | Fall back to **listing snapshot** fields; dump errorstack; do not claim missing handle |

---

## Confirmed findings (we hit these)

### 1. macOS library discovery — **patched** (H)

`ctypes.util.find_library("mtp")` often returns `None` on Darwin → pymtp cannot load libmtp.

**Fix:** Patch `find_library` before importing pymtp (`/opt/homebrew/lib/libmtp.dylib`, `/usr/local/lib/libmtp.dylib`).

---

### 2. Filetype enum off-by-one — **patched** (B)

Stock `LIBMTP_Filetype` omitted **`FOLDER = 0`**. `find_filetype(".mp3")` returned **1 (WAV)** instead of **2 (MP3)**. Device rejected tracks almost immediately.

**Fix:** Replace table with libmtp 1.1.23-aligned values in wrapper. Tests: `tests/test_pymtp_filetypes.py`.

---

### 3. Track send ctypes + error dump — **patched** (C, F, I)

Stock `send_track_from_file`:

- No `argtypes` on `LIBMTP_Send_Track_From_File` (fragile on arm64).  
- `Dump_Errorstack()` without device → NULL-device PANIC.  
- Broken exists check: `os.path.exists(source) == None`.

**Fix:** Wrapper replacement for `send_track_from_file` + `debug_stack`; argtypes for send/errorstack/storage.

**Adapter still owns domain contract:** `PymtpDevice.send_track` sets parent 100, storage `0x00010001`, short basename, tags, duration ms, year date field — see [device-contract.md](./device-contract.md).

---

### 4. List Folders / `get_folder_list` — **patched** (E)

```text
AttributeError: 'dict' object has no attribute 'has_key'
```

Stock walks folders with `ret.has_key(id)` (Python 2).

**Fix:** Python 3 `get_folder_list` / `get_parent_folders` in wrapper; NULL-safe walk; argtypes for Get/Find folder.

**Payoff:** Confirmed ZEN Vision:M top-level map (stored as `ZEN_VISION_M_FOLDER_IDS` in `remote_naming.py`).

---

### 5. Create Folder — first character only — **patched** (D, C)

Create `"Blargh"` → device shows `"B"` (object id high / new). Classic **untyped `str` → `char *`**.

Stock:

```python
LIBMTP_Create_Folder(self.device, name, parent, storage)  # name is str, no argtypes
```

**Contrast with working `set_device_name`:** the adapter already passed **`name.encode("utf-8")`** (bytes). That was enough for ctypes to often treat the buffer as a real C string **without** argtypes. Create Folder never encoded.

**Minimal fix that would have matched the working name path:**

```python
self._mtp.create_folder(name.encode("utf-8"), parent=…, storage=…)
```

**What we implemented:** wrapper `create_folder` + `set_devicename` both UTF-8 + `create_string_buffer` + `c_char_p` argtypes; create also passes ZEN `storage_id`. That **unifies** string-out paths so every caller does not relearn `.encode("utf-8")`. Encoding at the call site alone remains a valid pattern if a method is only half-patched.

---

### 6. No silent CMD fallback — **policy** (G + product)

On pure PyMTP send failure: log errorstack, raise `TransportError`, UI points at **Config → Stable Mode**. Never auto-call `mtp-sendtr` from experimental send ([decisions.md](./decisions.md) D3).

---

### 7. Get File Info / `Get_Filemetadata` intermittent NULL — **mitigated** (J)

**Symptom:** Device → Get File Info lists the object (e.g. id `398401`, `66617`); live refresh raises `ObjectNotFound`. Same session can succeed for other ids (e.g. `262464`). Object remains playable via the device library. Logs may show concurrent `LIBMTP panic: unable to read in zero packet` (USB noise; not proof the handle is gone).

**Root shape (libmtp, not ctypes skew):**

- `LIBMTP_Get_Filemetadata` → `ptp_object_want(..., OBJECTINFO | MTPPROPLIST)`.
- If proplist flags are not fully loaded, `ptp_object_want` returns `PTP_RC_GeneralError` → libmtp returns **NULL** → pymtp `ObjectNotFound`.
- ZEN Vision:M is flagged `DEVICE_FLAG_BROKEN_MTPGETOBJPROPLIST_ALL` (bulk proplist). Listing uses classic ObjectInfo path and already has id/name/parent/storage/size/type/mtime.
- `LIBMTP_File` ctypes layout matches C on this platform (`sizeof` 56) — not a struct-offset bug for `item_id`.

**Fix / policy:**

- Wrapper: typed `Get_Filemetadata`; dump errorstack on NULL before `ObjectNotFound`.
- UI: on non-fatal live failure, show **listing snapshot** with an explicit note (not “object missing”).
- Do **not** treat this as silent Experimental→CMD fallback; it is the same session’s listing data already fetched for the picker.
- File Info stays listing-based for shell fields. On-device **tags** are a separate path: Device → **Get Track Info** (`Get_Trackmetadata`).

---

### 8. Get Track Info / `Get_Trackmetadata` — **patched** (C + product)

**Surface:** Device → Get Track Info (experimental). Picker prefers audio/video-ish listing entries; calls patched `get_track_metadata`.

**libmtp behavior:**

- `ptp_object_want(..., OBJECTINFO only)` then fills tags from proplist cache **or** many `GetObjectPropValue` calls (USB-heavy; do not loop).
- Non-tracks return NULL → `ObjectNotFound` (not a missing handle necessarily).
- Does **not** hard-require MTPPROPLIST flags the way `Get_Filemetadata` does — better chance on ZEN for ids that fail File Info live refresh, but property GETs can still be empty/slow/noisy.

**Fix:**

- Wrapper: argtypes; snapshot fields into Python; `LIBMTP_destroy_track_t`; dump stack on NULL.
- Domain: `DeviceTrackInfo`; adapter maps snapshot → model; UI dialog shows tags + stream fields.
- No silent CMD fallback; no merge into Get File Info.

---

## Predicted breakages (not all patched yet)

Assume stock pymtp methods are **guilty until proven** on device under Python 3.13 + arm64 + libmtp 1.1.x.

### High likelihood (same classes as above)

| Surface | Stock risk | Predicted symptom | Class |
|---------|------------|-------------------|--------|
| **`send_file_from_file`** | Path/target as `str`; no/partial argtypes; uses `find_filetype` (OK if table patched); stock `debug_stack` | First-char remote name, wrong filetype if table regressed, arm64 send fail | C, D, F |
| **`get_file_to_file` / `get_track_to_file`** | Host path as `str` without `c_char_p` argtypes | Truncated path / failed download / first-char path | C, D |
| **Playlist name / create / update** | `LIBMTP_Playlist.name` as `c_char_p`; create/update with untyped pointer + name | Playlist titled `"B"`; create fails; track id array layout wrong | D, C, struct |
| **Any new “set string on device”** (album art description, custom props if added) | Same as Create Folder | First character only | D |
| **Progress callbacks** | Stock `Progressfunc` signature historically wrong / incomplete | Hang, crash, or ignored progress | C |
| **Linked-list walks** (files, tracks, playlists, errors) | NULL `next`; no guard; Py2-era loops | Segfault or hang on empty list | E-adjacent |
| **`get_errorstack` stock path** | Treats pointer as int (`if ret != 0`) | Wrong “failure” or never raises | C, F |
| **Delete object / batch admin** | ~~untyped device ptr~~ **patched**; batch uses track listing + fatal abort | Residual: intermittent fail after poisoned session | G residual |

### Medium likelihood (struct / libmtp skew)

| Surface | Risk | Symptom |
|---------|------|---------|
| **`LIBMTP_MTPDevice` layout** | ctypes struct smaller/stale vs real device | Rare if we only pass **device pointer**; breaks if code dereferences fields we define |
| **`LIBMTP_Track` / `File` extra fields** | Missing fields on newer libmtp | Wrong offset for later fields if C grows struct; currently send path relies on early fields + pointer |
| **Storage enumeration** | Free/total space walks storage list | Wrong free space UI; create/send with storage 0 if we stop hardcoding ZEN id |
| **Multi-storage devices** | Hardcoded `0x00010001` | Fine on Vision:M; wrong volume elsewhere |
| **Folder create under non-Music** | Parent/storage mismatch | Folder appears on wrong storage or fails |

### Lower likelihood but expensive

| Surface | Risk |
|---------|------|
| **Album association after send** | libmtp “could not add to album” noise even when object landed (seen historically with sendtr) |
| **Concurrent USB use** | Auto-connect poll + transfer + folder list on same session without locking | Race / poison — mitigated when listings/transfers set `_transfer_busy` so poll skips |
| **Disconnect while worker runs** | Main-thread vs bg poll | Stale session, double free class bugs |
| **Upstream pymtp upgrade** | Different wheel / fork | Silently drop our monkey-patches if import path changes |
| **Long track/file listing on UI thread** | ~~was~~ Device → List Tracks/Files (and picker listings) blocked Tk for 60s+ while libmtp walked USB; stderr `LIBMTP panic: unable to read in zero packet` looked like a hard hang | **Fixed:** `_run_device_bg` (indeterminate for files/folders/tracks list); panic can still print to terminal and still be non-fatal |
| **Post-listing auto-connect kill** | ~~was~~ list_tracks ok, then poll `session_alive`/`get_device_info` on a recovering bus → disconnect → `Could not close session!` / endpoint errors | **Fixed:** USB quiet cooldown after jobs + soft-fail probes (2 strikes) + connect/auto-connect use identity-only (no battery/storage); full `get_info` is Device → Device Info only with per-field soft-fail |
| **Connect freezes after send death** | Connect ok then `get_batterylevel` `CommandFailed` / hang | Battery/storage walks on half-dead session | Connect uses `get_identity` only; `get_info` soft-fails battery/space |
| **Get_Tracklisting incomplete on ZEN** | Bulk `LIBMTP_Get_Tracklisting*` may return **1** demo track (or a tiny set) while filelisting sees full library; CLI **mtp-tracks** does **not** use this API | **Product path:** List Tracks = filelisting + per-id `Get_Trackmetadata` (mtp-tracks algorithm). Keep patched `get_tracklisting` for diagnostics only; harden linked-list `next` capture before `destroy_track_t` |
| **File-only List Tracks empty tags** | Pure file-filter list has empty artist/title on 1000+ rows | **Mitigated:** `track_line` falls back title→filename; dialog **Load tags for selection** (small batches; warn >25); not full-library tracklisting |
| **LIBMTP panic zero-packet noise** | `LIBMTP panic: unable to read in zero packet response` / `0xfffffff8` (LIBUSB_ERROR_OVERFLOW on optional ZLP) during long walks | **Non-fatal** stderr from C (LIBMTP_INFO); do not treat as hard failure or abort the session solely for this message |

---

## Heuristic: how to open a new stock pymtp API

Before wiring a new Device menu item or send path:

1. **Read stock source** in site-packages `pymtp.py` for that method.  
2. **Classify** each argument: device ptr, `char *`, uint32, struct pointer, callback.  
3. **If any `char *`:** never pass raw `str` without either:
   - encoding to UTF-8 **bytes** (pattern that made `set_device_name` work), **or**
   - wrapper patch with `c_char_p` + stable buffer (preferred for shared use).  
4. **If multi-arg C call:** set `argtypes`/`restype` in the wrapper (arm64).  
5. **If dict walk / `has_key` / `== None` for existence:** rewrite in wrapper.  
6. **On failure:** log libmtp errorstack with **device pointer**; surface `TransportError`; no silent CMD.  
7. **Add a unit test** that locks the patch (source contains no `.has_key(`, filetype table, encode helper, etc.) even if device is unavailable.  
8. **Manual on-device check** with a distinctive multi-byte name (`Blargh`, `café`) and List Folders / re-read.

---

## What is intentionally *not* the binding’s fault

These are **app contract** bugs that looked like “PyMTP is broken”:

| Issue | Fix location |
|-------|----------------|
| Nested `Music/Artist/Album` remote paths | `remote_naming` — numeric parent only |
| Storage id 0 | `DEFAULT_STORAGE_ID` |
| Long / `&` basenames | sanitize + length cap |
| Continuing batch after fatal send | transfer pipeline abort |
| Tag reader gaps (OGG/WMA keys) | `mutagen_tags` — orthogonal to pymtp |

Do not “fix” those by patching ctypes.

---

## Patch inventory (wrapper today)

Keep this table in sync when adding monkey-patches:

| Stock surface | Patch status | Classes |
|---------------|--------------|---------|
| `find_library("mtp")` (Darwin) | Patched | H |
| `LIBMTP_Filetype` | Mutated in place | B |
| `send_track_from_file` | Replaced | C, F, I |
| `debug_stack` | Replaced | F |
| Send / errorstack / storage argtypes | Configured | C |
| `get_folder_list` / `get_parent_folders` | Replaced | E |
| Folder Get/Find argtypes | Configured | C |
| `get_filelisting` | Replaced (NULL-safe; progress=NULL) | E-adjacent, C |
| Filelisting callback argtypes | Configured | C |
| `get_tracklisting` | Replaced (NULL-safe; snapshot + destroy_track_t per node; optional progress callback) | E-adjacent, C |
| Tracklisting callback argtypes | Configured | C |
| `create_folder` | Replaced | D, C |
| `set_devicename` | Replaced (was already OK via bytes at adapter) | D, C |
| `send_file_from_file` | **Not fully replaced** (argtypes partial via Send_File) | C, D residual |
| Playlist APIs | **Untouched** | D, C predicted |
| Download to file | **Untouched** | D, C predicted |
| `delete_object` | Replaced (argtypes + device ptr); batch Delete All aborts on fatal | C, G residual |
| `get_file_metadata` | Replaced (argtypes + device ptr; NULL → dump + ObjectNotFound); UI listing fallback | C, J |
| `get_track_metadata` | Replaced (argtypes + snapshot + destroy_track_t; NULL → dump + ObjectNotFound) | C |

---

## Related docs

| Doc | Role |
|-----|------|
| [libmtp-api-coverage.md](./libmtp-api-coverage.md) | What libmtp/pymtp/MtpManager implement vs leave unbound |
| [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) | Forensic send-path layers |
| [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md) | CMD finalize / hang (same device pickiness) |
| [device-contract.md](./device-contract.md) | Parent 100, storage, basenames, folder id map |
| [decisions.md](./decisions.md) D3, D8 | No silent fallback; wrapper strategy |
| [transfer-and-modes.md](./transfer-and-modes.md) | UI mode + recovery |

---

## Outcome rule of thumb

If a PyMTP feature:

- **crashes in pure Python** (`has_key`, type errors) → class **E**; patch the method.  
- **succeeds but corrupts strings on device** → class **D**; encode / `c_char_p`.  
- **fails in ~1s with empty `CommandFailed`** → class **B/C/F**; filetype + argtypes + errorstack.  
- **fails after a prior bad send** → class **G**; session poison, not a new enum.  
- **looks like CMD’s old 99% finalize** → class **A**; re-check parent/storage/name contract first.  
- **listed object, Get_Filemetadata NULL** → class **J**; listing snapshot, not “missing id”.

Stock pymtp is a **compatibility tax**, not a stable platform. Prefer **small, tested wrapper patches** over expanding call surface without a checklist pass.
