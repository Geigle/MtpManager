# Debrief: Experimental bulk session poison (Rock playlist science run)

> **See also:** [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) (binding/contract fixes), [device-contract.md](./device-contract.md), [transfer-and-modes.md](./transfer-and-modes.md).  
> **Operator path:** `scripts/sync_rock_experimental.py` · logs `/tmp/mtpmanager_rock_sync.log`, `/tmp/mtpmanager_rock_sync_state.json`

**Status:** Observed / partially mitigated by skip-if-present + manual restart (not fixed in libmtp)  
**Device:** Creative ZEN Vision:M (`041e:413e`), serial `00023C0296C9A62D209630E0F14FE57A`  
**Transport:** **Experimental only** (PyMTP → libmtp 1.1.23)  
**Workload:** Host playlist **Rock** (715 tracks), FLAC→MP3 + dual-slot pipeline, batch size 15  
**Date:** 2026-08-06  

---

## Outcome

Playlist transfer completed across many restarts:

| Metric | Final |
|--------|--------|
| Playlist tracks | 715 |
| Device playlist push | OK (`playlist_id=1635701`, resolved 715, missing 0) |
| Fatal session deaths this night | **5** distinct poison events (same error stack) |

Skip-if-present (`device_index` GUID stems + `record_send`) made restarts safe: each run only sent what was still missing.

---

## What every failure had in common

Across **all five** session deaths, the failure signature was identical:

```text
PTP Layer error 2002: add_object_to_cache(): couldn't add object to cache
Error 2002: PTP General Error
PTP Layer error 02ff: send_file_object_info():Could not send object property list.
Error 02ff: PTP I/O Error
LIBMTP_Send_Track_From_File_Descriptor(): subcall to LIBMTP_Send_File_From_File_Descriptor failed.
→ pymtp.CommandFailed → TransportError(fatal=True)
```

Then the recovery path always degraded the same way:

1. Batch abort (`stop_on_fatal=True`) — remaining tracks in the batch not attempted.  
2. Dual-slot pipeline marks **two** tracks `failed` (the send in flight and the next prepared track) — **not** two independent bad files.  
3. Disconnect often prints `Could not close session!` / USB endpoint errors.  
4. Software reconnect after quiet (~15s) **fails**: `LIBMTP PANIC` / USB reset / `NoDeviceConnected`.  
5. Host-side recovery needs **unplug/replug** (or long wait) before the next run.

**Send context at fail was always on-contract:**

- `parent=100` (Music)  
- `storage=0x00010001`  
- `filetype=2` (MP3)  
- Remote name = 32-hex GUID + `.mp3`  

So this is **not** the old nested-path / storage-0 / basename-title class of bug.

---

## What was *not* shared (variable session life)

| Dimension | Observation |
|-----------|-------------|
| **Successful sends before poison** | ~40–60, ~48, ~135, ~384, or a short early death (~minutes) — **no fixed N** |
| **Wall-clock session life** | Roughly **2–18 minutes** of continuous PyMTP send (order-of-magnitude; not a tight threshold) |
| **Position inside 15-track batch** | 1/15, 4/15, 10/15, 12/15 — **no fixed slot in the batch** |
| **Transcode temp slot** | Both `TRANSCODE_0` and `TRANSCODE_1` seen; slot 1 more often simply because dual-slot alternates |
| **Victim GUID / title** | **Different remote each time** (5 unique remotes for 5 events). Tracks that “failed” once often **sent fine on a later resume** |
| **Artist / genre** | No single artist or format exclusive to failures (Disturbed/Flyleaf appear in dual-fail pairs as pipeline neighbors, not as a content blacklist) |

**Conclusion:** Session lifetime looks **stochastic from the host’s point of view** — same stack every time, unpredictable *when*. Consistent with ZEN/libmtp session or device object-cache exhaustion under sustained `SendTrack` / property-list traffic, not with one bad file or one bad batch index.

---

## Timeline of poison events (this run)

| Fail local time | Approx. successful sends this session | Batch position | Remote GUID (basename) | Temp slot |
|-----------------|----------------------------------------|----------------|--------------------------|-----------|
| 20:38:47 | short early run (~4 batch oks; multi-process race earlier) | mid-batch | `72b01ae0…` | 1 |
| 21:09:33 | ~384 | 10/15 | `30c4e44e…` | 1 |
| 21:36:03 | ~135 | 1/15 | `4cf382dd…` | 0 |
| 22:13:57 | ~48 | 4/15 | `910f7cd2…` | 1 |
| 22:26:36 | ~41 | 12/15 | `772c6c9e…` | 1 |

Final leg: already 688 on device, 27 remaining → completed + **playlist push OK**.

---

## Pipeline artifact (easy to misread)

When the session dies, logs show **two** `track …: failed` lines close together. That is the dual-slot convert/send pipeline (track *i* send fails fatally while *i+1* was already prepared). Treat the pair as **one poison event**, not two root causes.

Log lines may also appear **triplicated** (root logger + `rock_sync` FileHandler + StreamHandler) — count unique timestamps / `rock_sync` lines when analyzing.

---

## Mitigations that worked for science

1. **`record_send` + GUID skip** after each success → restarts only transfer remaining tracks.  
2. **Batch size 15** + fatal abort (honest; does not continue on a dead bus).  
3. **Manual unplug/replug + restart** after poison (software reconnect alone was insufficient).  
4. **Do not** silent-fallback Experimental → Stable mid-job (product invariant).

### Mitigations worth trying later (not proven here)

- Smaller batches + **proactive** disconnect/reconnect every *N* tracks (before poison).  
- Longer quiet after fatal (30–60s) + more reconnect attempts (still may need physical cycle).  
- Optional Stable (`mtp-sendtr`) for bulk residual only when user accepts leaving Experimental.  
- First-class CLI `sync --playlist` with the same resume semantics ([todo-agent-cli.md](./todo-agent-cli.md)).

---

## Implications for agents / CLI

- Long Experimental bulk jobs **will** hit this class of failure; design for **idempotent resume**, not one-shot perfection.  
- Surface `fatal=True` + PTP 2002/0x02ff as **session poison → unplug/replug**, not “retry same process immediately”.  
- Dual-fail tracks in one log burst ≠ two bad songs.

---

## Log locations (operator machine)

| Path | Contents |
|------|----------|
| `/tmp/mtpmanager_rock_sync.log` | Full operator + app stream for the science runs |
| `/tmp/mtpmanager_rock_sync_state.json` | Last JSON progress (final: `done=true`, playlist push OK) |
| `~/Library/Logs/MtpManager/transfer-*.log` | Per-batch transfer session logs |
| `~/Library/Logs/MtpManager/mtpmanager.log` | App DEBUG+ |
