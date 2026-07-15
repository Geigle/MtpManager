# Debrief: Experimental PyMTP transfer failure on Creative ZEN

**Status:** Resolved (layered fixes + CMD fallback)  
**Device:** Creative ZEN Vision:M (`VID=041e`, `PID=413e`)  
**Transport:** Experimental mode via PyMTP → libmtp 1.1.23 (with mtp-sendtr fallback)  
**Symptom track:** Forhill — *Outlines* track 01 (and any experimental single-track send)  
**Date context:** July 2026  
**Related:** [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md) (stable/CMD finalize bugs)

---

## Summary

Experimental mode (PyMTP) could not send tracks to the ZEN even after stable/CMD mode was working. Failures looked like a single “PyMTP is broken” bug, but were a **stack of independent defects**: wrong remote context (same class as the old CMD bug), a stale filetype enum off-by-one, fragile ctypes bindings on arm64, and opaque diagnostics that hid the real PTP errors.

Each fix unmasked the next layer. The durable outcome is:

1. **Shared ZEN remote contract** with CMD (Music folder 100, storage `0x00010001`, short sanitized names).  
2. **Correct libmtp filetype values** (`MP3 = 2`, not `1`).  
3. **Hardened ctypes send path** (argtypes, path encoding, error-stack capture, no NULL-device dump).  
4. **One-shot fallback** to `mtp-sendtr` when pure PyMTP still hits a dead PTP session (`02ff`), then reconnect.

Stable mode was never the regression target; this work brings experimental send up to the same device contract and makes failures observable.

---

## What users saw

### Timeline of attempts

| Session | What happened |
|---------|----------------|
| Pre-fix experimental | Connect OK → convert FLAC→MP3 OK → bare `pymtp.CommandFailed` ~1s later → UI freeze feel → reconnect needed |
| After parent/storage/naming | Still `CommandFailed` in ~1s; logs now showed `parent=100 storage=0x00010001 remote=01 Outlines.mp3` |
| After filetype fix | Logs showed `filetype=2`; real PTP text appeared: `02ff Could not send object` + `LIBMTP PANIC: … NULL device!` |
| After ctypes + fallback | Pure path hardened; if session still dies, automatic retry via proven `mtp-sendtr` |

### Log shapes

**Layer 1 (context only — no real libmtp reason):**

```text
send_track … (old long name, parent/storage unset)
raise CommandFailed   # empty exception, no message
Action failed: Single Track MP3
```

**Layer 2 (context fixed, still silent reason):**

```text
send_track path=…/TRANSCODE.mp3 remote=01 Outlines.mp3 parent=100 storage=0x00010001
PyMTP send_track failed … detail=CommandFailed (libmtp stack may be on stderr)
TransportError: PyMTP send failed (CommandFailed). Remote=01 Outlines.mp3 parent=100 …
```

**Layer 3 (filetype correct; real PTP text finally visible):**

```text
send_track … parent=100 storage=0x00010001 filetype=2
LIBMTP PANIC: Trying to dump the error stack of a NULL device!
PTP Layer error 02ff: LIBMTP_Send_File_From_File_Descriptor(): Could not send object.
Error 02ff: PTP I/O Error
LIBMTP_Send_Track_From_File_Descriptor(): subcall to LIBMTP_Send_File_From_File_Descriptor failed.
ERROR: Could not close session!
```

Contrast with a healthy **stable/CMD** send of the same device:

```text
type: mp3, 2
Storage ID: Storage Media (65537)
Sending track… Progress: …
```

---

## Root causes (stacked)

Several independent issues combined. Fixing only one improved logs or partial state; all of them mattered for a reliable experimental send.

### 1. Experimental path never got the CMD ZEN contract

Stable mode had already learned (see the CMD debrief):

| Field | Needed on this ZEN | Old PyMTP value |
|--------|--------------------|-----------------|
| `parent_id` | Music folder **100** | **0** |
| `storage_id` | **`0x00010001` (65537)** | **0** |
| Object filename | Short sanitized, e.g. `01 Outlines.mp3` | Long `Artist - Album - N - Title.mp3` |

`PymtpDevice.send_track` filled tags only and left `LIBMTP_Track` defaults at zero for parent/storage. That is the same finalize-context class that made CMD die at ~99% with `get_suggested_storage_id(): could not get storage id from parent id`.

**Fix:** Shared module `mtpmanager/infra/remote_naming.py` used by both transports; PyMTP sets `parent_id` / `storage_id` and uses basename-only for the libmtp filename field.

### 2. Stock pymtp filetype enum was off-by-one

After parent/storage/naming were correct, sends still died in ~1 second (not a bulk-transfer hang). Successful CMD logs showed:

```text
type: mp3, 2
```

Stock pymtp’s `LIBMTP_Filetype` table (last “checked” against ancient libmtp) **omitted `FOLDER = 0`**, so every later value was shifted:

| Name | Stock pymtp | libmtp 1.1.23 (`libmtp.h`) |
|------|-------------|----------------------------|
| FOLDER | *(missing)* | **0** |
| WAV | 0 | **1** |
| **MP3** | **1** | **2** |
| WMA | 2 | 3 |

`send_track_from_file` always does `metadata.filetype = self.find_filetype(source)`. For `.mp3` that returned **1**, which modern libmtp interprets as **WAV**. The player then rejected the object almost immediately.

**Fix:** In `mtpmanager/infra/pymtp_wrapper.py`, mutate `LIBMTP_Filetype` **in place** after import so `MTP.find_filetype` (which reads the pymtp-module global dict) sees `MP3 = 2`. Unit tests lock this in `tests/test_pymtp_filetypes.py`.

### 3. Real libmtp errors never reached the app log

On failure, stock pymtp does:

```python
self.debug_stack()   # only if __DEBUG__; dumps to stderr
raise CommandFailed  # empty exception
```

And `debug_stack` called:

```python
self.mtp.LIBMTP_Dump_Errorstack()  # missing required device pointer!
```

That produced:

```text
LIBMTP PANIC: Trying to dump the error stack of a NULL device!
```

…while the useful PTP lines either stayed on process stderr or were never captured. `_transfer_one` already special-cased `TransportError`; raw `CommandFailed` fell through less useful paths until we wrapped failures.

**Fix:**

- Walk `LIBMTP_Get_Errorstack(device)` and put text on `TransportError.stderr` + ERROR logs.  
- Patch `debug_stack` to pass a real device pointer.  
- Wrap send failures as `TransportError(fatal=True)` so batch abort / transfer UI messaging apply.

### 4. Fragile ctypes call path for `LIBMTP_Send_Track_From_File`

Even with correct metadata fields, stock pymtp:

- Set **no `argtypes`** on multi-arg send (especially risky on Apple Silicon).  
- Passed Python `str` paths without an explicit `c_char_p` contract.  
- Used a nonsense exists check (`os.path.exists(source) == None` — always false).  
- Used temporary `c_char_p` / short-lived buffers for string fields.

After the filetype fix, remaining failures showed true USB/PTP death:

```text
Error 02ff: PTP I/O Error
Could not send object
Could not close session!
```

That is a **dead session** class of error, not “wrong folder id.”

**Fix:** In `pymtp_wrapper.py`:

- Configure `argtypes` / `restype` for send, dump, get/clear errorstack, get storage.  
- Replace `MTP.send_track_from_file` with a path that encodes the filesystem path, uses `ctypes.byref(metadata)`, and only passes NULL for progress.  
- Keep tag/filename byte buffers alive for the full C call in `PymtpDevice`.  
- Align small sendtr habits: year as `YYYY0101T0000.0`, empty optional tags as NULL, `LIBMTP_Get_Storage` before applying storage id.

### 5. Long-lived experimental session vs one-shot CMD

Stable mode opens a **fresh** `mtp-sendtr` process per track (connect → send → exit). Experimental mode holds one libmtp session from **Connect** until **Disconnect**. After a bad send, that session is often unusable (`Could not close session!`); further pure-PyMTP attempts fail fast with `02ff`.

**Fix (resilience):** If pure PyMTP fails with markers like `02ff`, `PTP I/O Error`, `Could not send object`, then:

1. Disconnect / clear the poisoned session.  
2. Retry **once** via `CmdTransport` (same remote naming + storage as stable).  
3. Reconnect PyMTP so experimental device tools still work.

Logged explicitly as a fallback, not silent success.

### 6. UI freeze (partially separate)

Transfers run on the Tk main thread. Even a 1-second libmtp call freezes the window; a hung session feels like a hard lock until unplug. That is orthogonal to object finalize correctness but amplified every failed experimental send.

**Status:** Still an open follow-up (worker thread + progress). Not required for “track lands on device.”

---

## How we diagnosed it (method)

Working backwards from logs and parity with CMD:

1. **Prove convert is fine** — transfer session logs always showed ffmpeg “Done converting” before failure.  
2. **Diff remote contract vs CMD** — CMD debug lines already had `100/…` and `-s 65537`; PyMTP did not → layer 1.  
3. **After parity, still ~1s fail** — too fast for bulk finalize-at-99%; suspect metadata/type, not USB flakiness alone.  
4. **Compare filetype** — CMD `type: mp3, 2` vs pymtp table `MP3: 1` and `libmtp.h` enum order including `FOLDER` → layer 2.  
5. **Capture error stack into app logs** — exposed `02ff` / NULL-device PANIC → layers 3–5.  
6. **Measure struct sizes** — `LIBMTP_track_t` ctypes size matched C (136); device struct was stale (96 vs 112) but send only needs a correct device *pointer*; still tightened bindings.  
7. **Reuse proven sender** when session is dead — CMD fallback.

This is the same lesson as the CMD debrief: the device is picky but consistent; the app was asking for the wrong object context / type / call shape and then throwing away the evidence.

---

## Fix (where the code lives)

| Area | File(s) | What changed |
|------|---------|----------------|
| Shared remote naming | `mtpmanager/infra/remote_naming.py` | Music folder 100, storage `0x00010001`, sanitize, `build_remote_path` / `split_remote_path` / `year_arg` |
| CMD transport | `mtpmanager/infra/cmd_transport.py` | Imports shared naming (behavior unchanged) |
| PyMTP load + binding patches | `mtpmanager/infra/pymtp_wrapper.py` | Filetype table, ctypes argtypes, fixed `send_track_from_file` + `debug_stack` |
| Device/transport adapter | `mtpmanager/infra/pymtp_device.py` | Parent/storage/tags/send, errorstack → `TransportError`, CMD fallback |
| Tests | `tests/test_remote_naming.py`, `tests/test_pymtp_filetypes.py` | Naming + `MP3 == 2` / `find_filetype` |
| Prior CMD-only debrief | `docs/debrief-zen-track-send-failure.md` | Follow-ups marked done where relevant |

### Intended send shape (PyMTP)

```text
LIBMTP_Track:
  parent_id   = 100
  storage_id  = 0x00010001
  filename    = "01 Outlines.mp3"   # basename only; parent is a field
  filetype    = 2                   # MP3
  title/artist/album/…              # full tags (may contain '&' etc.)
  date        = "YYYY0101T0000.0" when year known
```

### Intended failure path

```text
send_track
  → try pure libmtp send
  → on CommandFailed: log errorstack, raise TransportError(fatal=True)
  → if stderr matches 02ff / I/O / could not send object:
        disconnect → CmdTransport.send_track → reconnect
```

---

## Verification

### Offline

```bash
.venv/bin/python -m unittest tests.test_remote_naming tests.test_pymtp_filetypes -v
```

Expect:

- `LIBMTP_Filetype["MP3"] == 2`, `["FOLDER"] == 0`  
- `find_filetype("x.mp3") == 2`  
- Remote paths like `100/08 Flesh Metal.mp3` (no `&`, length bounded)

### On device (Experimental tab)

1. Unplug/replug if a prior session was poisoned.  
2. Restart app so wrapper patches load.  
3. Connect → Single Track MP3 (e.g. Forhill/Outlines).  
4. Logs should show `filetype=2` and either:
   - pure success (`send_track object_id=…`), or  
   - explicit fallback: `retrying via mtp-sendtr` then success.  
5. Stable tab still sends the same library without regression.

### Diagnosis checklist (future PyMTP regressions)

1. Is `parent` / `storage` / `remote` logged correctly?  
2. Is `filetype` **2** for MP3 (not 1)?  
3. Does ERROR log include **libmtp error_text**, not only `CommandFailed`?  
4. Is there a NULL-device PANIC on dump? (should be gone)  
5. After `02ff`, does one CMD fallback attempt run instead of grinding the batch?  
6. Did the user Connect before send? (`NotConnected` is a separate footgun.)

---

## What we ruled out

| Suspect | Why not primary |
|---------|------------------|
| Transcode / bad MP3 payload | Convert always completed; failure inside `send_track` |
| Free space | Same device took full albums via CMD |
| “MTP is randomly haunted” | Failures tracked specific wrong fields / enum / session state |
| Only long filenames | Short `01 Outlines.mp3` still failed until later layers fixed |
| UI freeze as root cause | Freeze is main-thread blocking; send already returned `CommandFailed` in ~1s in many runs |

---

## Follow-ups (optional)

- Move transfers off the Tk main thread (progress + cancel; no freeze on hang).  
- Auto-discover Music folder id / storage id (multi-device) instead of ZEN defaults.  
- Optionally make pure PyMTP the only path once long-lived session reliability is proven without fallback.  
- Upstream or vendor a maintained libmtp binding (stock pymtp is effectively unmaintained vs libmtp 1.1.x).  
- Clearer “Connect first” UX when experimental send hits `NotConnected`.

---

## Outcome

- **Cause (layered):** (1) parent/storage 0 + long names, (2) filetype off-by-one labeling MP3 as WAV, (3) invisible libmtp errors / NULL dump, (4) fragile ctypes send + poisoned long-lived session.  
- **Fix:** Shared ZEN remote contract, correct filetype table, hardened libmtp bindings and diagnostics, `TransportError` integration, CMD one-shot fallback after PTP death.  
- **Validation:** Progressive log evidence at each layer; unit tests for naming and filetypes; on-device experimental send succeeds (pure path and/or fallback).

This is the difference between “experimental mode is cursed” and “we were sending the wrong object context, then the wrong object type, then a poorly bound C call, while throwing away the PTP stack that would have said so on day one.”
