# Debrief: Creative ZEN Vision:M track send failure at 99%

**Status:** Resolved  
**Device:** Creative ZEN Vision:M (`VID=041e`, `PID=413e`), firmware `1.61.01_0.00.23`  
**Transport:** Stable mode via libmtp `mtp-sendtr` (1.1.23)  
**Symptom track:** Mick Gordon — *Doom (Original Game Soundtrack)* track 08, *Flesh & Metal*  
**Date context:** July 2026

---

## Summary

Transfers via `mtp-sendtr` could hang or die near the end of a send (often after most of the album had already succeeded). One track would freeze until the cable was unplugged; subsequent sends then failed with cascading PTP I/O errors because the app never stopped the batch.

The underlying device failure was more specific than “USB is flaky.” The bulk file transfer usually completed to **~99%**, then libmtp failed while **finalizing the object** (storage/parent metadata and object cache). `mtp-sendtr` often **did not exit** after that failure—it continued into album association and hung—so the UI looked frozen until unplug or timeout.

The durable fix was:

1. Send into the real **Music folder** with a **short, sanitized object name** and an explicit **storage ID**.
2. Treat failed/hung `mtp-sendtr` as a real error and **abort the batch** instead of grinding through the rest of the album.

After that change, the previously failing single-track case succeeded.

---

## What users saw

### Phase A — freeze mid-album

- Album transfer progressed until roughly track 8.
- `mtp-sendtr` appeared stuck (no clean completion).
- Unplugging the player eventually released the process.
- After reconnect, remaining tracks were still attempted and all failed.

### Phase B — single-track reproduction (the smoking gun)

Sending only track 8 produced a clean, repeatable pattern:

```text
Progress: 14137868 of 14138336 (99%)
Error 2: PTP Layer error 2002: get_suggested_storage_id(): could not get storage id from parent id.
Error 2: PTP Layer error 2002: add_object_to_cache(): couldn't add object to cache
Error 1: LIBMTP_Send_File_From_File_Descriptor(): Could not retrieve updated metadata.
Error 1: LIBMTP_Send_Track_From_File_Descriptor(): subcall to LIBMTP_Send_File_From_File_Descriptor failed.
Progress: 14138336 of 14138336 (100%)
… then hang until timeout …
```

Key observations:

- Failure is **not** mid-stream corruption of the MP3 payload; it is **post-transfer finalize**.
- Free space on the device was large (~105 GB free) — “storage full” messages in later cascade logs were **secondary symptoms**, not the root cause.
- Console also showed `Storage ID: 0` before the send, which turned out to matter.

---

## Root causes (stacked)

Several independent issues combined. Fixing only the app’s hang handling would have improved UX; fixing only path/storage is what made the track land.

### 1. App treated every send as success

`CmdTransport` used something equivalent to:

```python
subprocess.run(cmd, check=False)  # no timeout, no stderr analysis
```

Consequences:

- A hung `mtp-sendtr` blocked the UI forever (or until unplug).
- Non-zero exits and libmtp error spam were ignored.
- Batch transfers continued after the session was already dead.

### 2. Nested remote paths were not real folders

Stable mode built destinations like:

```text
Music/Mick Gordon/Doom (Original Game Soundtrack)/Mick Gordon - Doom (Original Game Soundtrack) - 08 Flesh & Metal
```

`mtp-sendtr` (libmtp examples) does **not** create that hierarchy. It:

- Takes `dirname(remote)` as the **parent**
- Takes `basename(remote)` as the **object filename**
- Resolves the parent with `parse_path()` against the device’s existing folder/file listing

For a non-absolute path such as `Music/Artist/Album`, `parse_path` does not walk/create nested folders the way a filesystem would. In practice the send landed with a bad/zero parent context instead of the device’s real **Music** folder (id **100** on this ZEN).

On this player, `mtp-folders` shows a fixed layout (`Music`, `My Playlists`, `Pictures`, …). Music belongs under folder **100**, not under a host-invented path string.

### 3. Storage ID was left at 0

`mtp-sendtr` supports `-s <storage_id>`. Without it, storage defaults to **0**.

Device reality (`mtp-detect`):

| Field | Value |
|-------|--------|
| StorageID | `0x00010001` |
| Description | Storage Media |
| FreeSpaceInBytes | large (not full) |

With parent/storage unresolved, finalize hits:

```text
get_suggested_storage_id(): could not get storage id from parent id
Could not retrieve updated metadata
```

That matches the 99% failure window exactly.

### 4. Object name was long, awkward, and exactly 64 characters

Old basename:

```text
Mick Gordon - Doom (Original Game Soundtrack) - 08 Flesh & Metal
```

- Length: **64 characters**
- No `.mp3` extension on the remote object name
- Contained `&` (`Flesh & Metal`)

Creative-era MTP firmwares are picky about object names. Hitting a 64-character boundary with punctuation is a classic intermittent fail class, even when tags themselves are fine.

Tags (`-t`, `-a`, `-l`, …) still carry full title/album metadata for the player UI. The remote **filename** does not need to be a verbose archive-style path.

### 5. `mtp-sendtr` hangs after a failed send if an album is set

From libmtp’s `sendtr.c` flow (simplified):

1. `LIBMTP_Send_Track_From_File(...)` — may fail at finalize  
2. If `-l` / album was provided, still call `add_track_to_album(...)`  
3. That path calls `LIBMTP_Get_Album_List` / property probes  

After a broken finalize, album association often emits long streams of:

```text
LIBMTP_Get_Album_List(): Could not get object references
get_album_metadata(): ptp_mtp_getobjectpropssupported() failed
Error 02ff: PTP I/O Error
```

…and may never return. That is why the log could show 99% errors, then 100%, then a long stall until unplug or our timeout.

We cannot change stock `mtp-sendtr` behavior from Python, but we can:

- Avoid the bad parent/storage/name conditions that cause finalize to fail  
- Detect fatal stderr patterns and **kill** a post-failure hang quickly  
- Abort remaining batch items so reconnect is intentional

---

## Fix

Implemented primarily in `mtpmanager/infra/cmd_transport.py`, with batch abort in `mtpmanager/app/transfer.py` and UI messaging in `mtpmanager/ui/controllers.py`.

### Transfer target

| Before | After |
|--------|--------|
| Nested `Music/Artist/Album/long name` | `100/<short name>.mp3` (Music folder id) |
| No `-s` (storage 0) | `-s 65537` (`0x00010001`) |
| Basename up to 64+ chars, `&`, no extension | Sanitized, ≤56-char body + `.mp3` |
| Duration `422.04`, year `2016-09-28` | Duration `422`, year `2016` |

Example successful shape:

```text
mtp-sendtr -q ... -y 2016 -d 422 -s 65537 /tmp/TRANSCODE.mp3 100/08 Flesh Metal.mp3
```

Metadata flags still pass the real title/artist/album (including characters like `&` in tags). Only the **object filename** is sanitized.

### Process robustness

- Size-based timeout so a true USB stall cannot freeze the app indefinitely  
- Live stdout/stderr tee (diagnostics still visible)  
- Detect fatal libmtp phrases (`Could not retrieve updated metadata`, `get_suggested_storage_id`, PTP `02ff`, session/USB death, etc.)  
- If those appear and the process keeps running, kill after a short grace period (~8s) instead of waiting out the full timeout  
- Raise `TransportError(fatal=True)` and **stop the batch**  
- Cleanup of transcoded temp files remains in `transfer_track`’s `finally`

---

## Diagnosis checklist (for future regressions)

When a send fails near the end:

1. **Capture progress lines** — mid-file vs 99%/finalize.  
2. **Note Storage ID printed by sendtr** — `0` is a red flag.  
3. **Check `mtp-detect` storage block** — free space vs `0x00010001`.  
4. **Check `mtp-folders`** — confirm Music folder id (100 on this ZEN).  
5. **Measure remote basename length** — stay well under 64; avoid `& \ / : * ? " < > |`.  
6. **Distinguish cascade errors** after unplug — I/O spam and “storage full or corrupt” often mean *session dead*, not necessarily a full disk.  
7. **Confirm app abort** — one failure should stop the batch; if not, transport error handling regressed.

Useful commands:

```bash
mtp-detect | tail -n 80
mtp-folders
```

---

## Why this felt “periodic” before

The failure depended on object naming and finalize context, not only on one cursed track:

- Long album titles inflate the old `Artist - Album - NN Title` basename  
- Special characters in titles (`&`, `;`, etc.) appear only on some tracks  
- Nested path + storage 0 was always wrong, but some sends still “got lucky”  
- After one bad finalize, the session could be poisoned for the rest of the batch, which looked like “everything after track N is broken”

Documenting a single-track 99% log was what made the finalize path obvious.

---

## Follow-ups (optional)

- Auto-discover storage id / Music folder id instead of ZEN defaults (multi-device support).  
- Preflight free-space check before large albums (now that we know “full” can also be a lie after I/O death).  
- ~~Apply the same short-name sanitization to the PyMTP experimental transport for consistency.~~ **Done:** `PymtpDevice.send_track` now shares `remote_naming` (Music folder 100, storage `0x00010001`, short sanitized basename) and wraps failures as `TransportError`.  
- Consider a wrapper that skips album association after a failed send (would require not using stock `mtp-sendtr` as-is, or a custom sender).  
- Move transfers off the Tk main thread so a slow/hung libmtp call does not freeze the UI.

---

## Outcome

- **Cause:** finalize failure from bad parent/storage context and a borderline remote object name; hang amplified by post-failure album association and ignored process status.  
- **Fix:** correct Music folder + storage id + short safe filename; fail fast and abort batches.  
- **Validation:** previously failing track 08 single-track send succeeded after the change.

This is the difference between “MTP is haunted” and “we were asking the device to file a 64-character object under a parent/storage it could not resolve, then ignoring the fire.”
