# Basename length policy — evidence review

**Status:** Investigation note (2026-07)  
**Code constant:** `MAX_REMOTE_BASENAME = 56` in `mtpmanager/infra/remote_naming.py`  
**Contract:** [device-contract.md](./device-contract.md)  
**Related incident:** [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md)

This note separates **external** evidence (libmtp/PTP/mailing lists) from **project-empirical** evidence (ZEN Vision:M send failures and on-device observations). It does **not** authorize loosening `MAX_REMOTE_BASENAME` without a controlled send experiment.

---

## Summary

| Claim | Support |
|-------|---------|
| Send as `100/<short>.ext` with storage `0x00010001` | **Strong external + on-device** |
| Sanitize FS-hostile chars (`/\:*?"<>|&`, controls) | **External (Creative ZEN filename bugs) + local incident** |
| Require extension on object name | **Local incident** (Doom basename had no `.mp3`) |
| Tags may be long/full; filename stays short | **Correct split** (metadata props vs ObjectFileName) |
| Device hard-max object name is ~64 chars | **Not established externally** |
| `MAX_REMOTE_BASENAME = 56` | **Empirical send hygiene** under a suspected ~64 boundary from one stacked failure |

**Existence of a long name on the device ≠ proof that long names are a safe send recipe for our transports.**

---

## On-device observation (Get File Info)

Object listed on a real Creative ZEN Vision:M after Get File Info worked:

| Field | Value |
|-------|--------|
| Object id | 1320 |
| Parent | **100** (Music) |
| Storage | **0x00010001** |
| Filetype | MP3 |
| Name | `Apocalyptica - Live Or Die (feat. Joakim Brodén) - 1 Live or Die (feat. Joakim Brodén) .MP3` |
| Length | **91** Unicode characters / **93** UTF-8 bytes |
| Shape | Creative-style `Artist - Album - N Title .MP3` (space before extension; non-ASCII `é`) |

Contract-shaped send for similar tags would be approximately:

```text
100/1 Live or Die (feat. Joakim Brodén).mp3
```

Basename length ≈ **39** (≤ 56). Parent and storage match the long existing object; only the **object filename policy** differs.

Interpretation: another tool (e.g. Creative Media Source) can store long `ObjectFileName` values the player still indexes. That does **not** mean our CMD/PyMTP send path should emit long archive-style basenames.

---

## External evidence

### PTP / libmtp string ceiling (not 64)

- `PTP_MAXSTRLEN` is **255** in libmtp’s PTP headers (includes terminating null → effective pack limit ~254 UCS-2 code units).
- Object File Name (`PTP_OPC_ObjectFileName` / `dc07`) is a normal STRING property.
- libmtp does **not** publish a universal 64-character basename constant in public headers or device-flags.
- `ptp_pack_string` rejects strings above the PTP max — far above 56/64.

So a 91-character name already on the device is **compatible with the protocol**. A “device max = 64” reading is **not** justified from PTP/libmtp alone.

### Path shape and storage (strong)

libmtp `examples/sendtr.c` / `mtp-sendtr`:

- `dirname(remote_path)` → parent folder id (via path parse against **existing** folders)
- `basename(remote_path)` → `trackmeta->filename`
- `-s` sets `storage_id`
- Nested `Music/Artist/Album/...` is **not** auto-created like a filesystem

Historical libmtp-discuss traffic includes *“mtp-sendtr to Creative Zen - folders got mixed up”*: wrong destination handling rewrote filenames (including null names) and scrambled folder views. That reinforces numeric parent + basename, not invented hierarchy.

### Creative ZEN filename character bugs (strong for sanitization)

libmtp-discuss **[Zen 4GB filenames]** (2007-12, Gregor J. / replies):

- Transfers sometimes failed depending on filename.
- Characters such as `. , : ;` were reported to cause failures on a Creative Zen 4GB.
- Question raised: allowed characters? maximum filename length?
- Maintainer reaction acknowledged more Creative firmware bugs; MTP conceptually accepts Unicode filenames, while stripping FS-hostile characters is an application/library concern.

This **externally** supports sanitizing awkward punctuation. It does **not** establish a published 64-char maximum.

### ZEN Vision:M device flags (no length flag)

From libmtp `music-players.h` / `device-flags.h` (as of review):

- Creative ZEN Vision:M (`041e` / `413e`): primarily `DEVICE_FLAG_BROKEN_MTPGETOBJPROPLIST_ALL` (and related variants on sibling PIDs).
- Filename-related flags elsewhere in the tree include `DEVICE_FLAG_ONLY_7BIT_FILENAMES` and `DEVICE_FLAG_UNIQUE_FILENAMES` — **charset / uniqueness**, not a 64-char length cap.
- No Creative “max basename length = 64” flag found.

---

## Project-empirical evidence (origin of 56 / ~64)

From [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md), the old CMD remote basename for Mick Gordon — *Doom* track 08:

```text
Mick Gordon - Doom (Original Game Soundtrack) - 08 Flesh & Metal
```

| Property | Value |
|----------|--------|
| Length | **exactly 64** characters |
| Extension | **none** |
| Special character | **`&`** |
| Parent context | nested `Music/Artist/Album/...` (bad) |
| Storage | often **0** (bad) |

Failure class: bulk transfer to ~99%, then finalize errors (`get_suggested_storage_id`, metadata refresh), hang amplified by post-failure album association.

`MAX_REMOTE_BASENAME = 56` is a **margin under that 64-character smoking-gun basename**, leaving room for `.mp3`, after stripping unsafe characters. It is **send hardening**, not a measured hard device limit from a controlled length sweep.

Stacked factors matter: long awkward name **plus** bad parent/storage. The long name alone was never isolated as the sole root cause in that incident.

---

## What the contract still correctly requires

Keep without loosening unless re-proven on hardware:

1. **Parent 100** (Music object id), never string path `Music/...`
2. **Storage `0x00010001`**, never leave 0
3. **No nested Artist/Album remote paths**
4. **Sanitize** `/ \ : * ? " < > | &` and controls on the **object filename**
5. **Extension required** on the object name
6. **Tags full / filename short** split
7. **≤56 basename budget** as default send hygiene

Do **not** raise `MAX_REMOTE_BASENAME` solely because Get File Info showed a longer existing name.

---

## Optional follow-ups (not done here)

- Controlled experiment: send identical audio with basenames of length 40 / 56 / 64 / 80 / 91 under parent 100 + storage `0x00010001`, measure finalize success (Stable and Experimental separately).
- If long sends prove reliable on this unit, document the measured ceiling and reconsider the constant with tests.
- Soften any remaining “device limit = 64” wording elsewhere to “empirical margin / suspected boundary.”

---

## Sources checklist

| Source | What it supports |
|--------|------------------|
| libmtp PTP headers (`PTP_MAXSTRLEN`) | Protocol string ceiling ~255, not 64 |
| libmtp `sendtr.c` | dirname=parent, basename=filename, `-s` storage |
| libmtp `music-players.h` / `device-flags.h` | ZEN quirks; no 64-char flag |
| libmtp-discuss “Zen 4GB filenames” | Character-sensitive Creative filenames |
| libmtp-discuss sendtr/Zen folder mix-ups | Fragile parent/destination handling |
| On-device Get File Info (object 1320) | Long names can **exist**; parent/storage still match contract |
| [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md) | 64-char `&` no-ext basename in stacked finalize failure |
| `remote_naming.py` / tests | Current policy implementation |
