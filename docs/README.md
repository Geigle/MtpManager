# MtpManager documentation

MtpManager loads music onto picky MTP players (Creative ZEN Vision:M–oriented): FLAC and friends → MP3/WMA, dual transport (Stable `mtp-sendtr` vs Experimental PyMTP), and a strict remote send contract so devices stop failing at finalize.

This folder is the **design and incident** map. For run instructions, logs, and license, see the [root README](../README.md). For OS setup traps, see [PLATFORMS.md](../PLATFORMS.md). AI agents should start at root [AGENTS.md](../AGENTS.md).

---

## Reading order

### New humans (developers)

1. [architecture.md](./architecture.md) — layers, packages, composition, dual mode  
2. [transfer-and-modes.md](./transfer-and-modes.md) — pipeline, Stable vs Experimental  
3. [device-contract.md](./device-contract.md) — **required** before changing send paths  
4. [decisions.md](./decisions.md) — why dual mode, no silent fallback, fatal abort, …  
5. Debriefs only when debugging a class of failure you have not seen yet  

### AI coding agents

1. [AGENTS.md](../AGENTS.md) (hard invariants + change surfaces)  
2. This index  
3. [architecture.md](./architecture.md) → [device-contract.md](./device-contract.md) → [decisions.md](./decisions.md)  
4. [transfer-and-modes.md](./transfer-and-modes.md) when editing transfer/UI  
5. Relevant debrief when changing transport, naming, or hang handling  
6. [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) when touching PyMTP / wrapper / Device menu  
7. [libmtp-api-coverage.md](./libmtp-api-coverage.md) for “does libmtp/pymtp even expose this?”  

**When debugging transfers, start with [device-contract.md](./device-contract.md) plus the debrief for your transport** (CMD finalize vs PyMTP layers).  
**When opening any new stock pymtp API**, use [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) (failure classes + predicted breaks).  
**When planning Device/admin features**, use [libmtp-api-coverage.md](./libmtp-api-coverage.md) (implemented vs pymtp-only vs libmtp-only).

---

## Document index

| Doc | Purpose |
|-----|---------|
| [architecture.md](./architecture.md) | System design: layers, package map, composition root, dual-mode wiring, logging, known gaps |
| [decisions.md](./decisions.md) | ADR-lite: dual mode, no silent fallback, shared naming, fatal abort, pymtp patches, license, … |
| [device-contract.md](./device-contract.md) | MTP/ZEN remote path, storage, basename, tags vs filename — **do not rebreak** |
| [basename-limit-evidence.md](./basename-limit-evidence.md) | Why `MAX_REMOTE_BASENAME=56`: external vs empirical evidence; long on-device names |
| [transfer-and-modes.md](./transfer-and-modes.md) | Scan → transcode → send; Stable vs Experimental; batch abort; tests |
| [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) | Living catalog: PyMTP breakage patterns, confirmed fixes, predicted next failures |
| [libmtp-api-coverage.md](./libmtp-api-coverage.md) | libmtp vs stock pymtp vs MtpManager: what is implemented, stubbed, or unbound |
| [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md) | Incident: CMD 99% finalize, hang, batch abort (forensic detail) |
| [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) | Incident: layered PyMTP binding failures; no silent CMD fallback |

---

## Quick answers

| Question | Answer lives in |
|----------|-----------------|
| Why 56-char basenames if the device has longer names? | [basename-limit-evidence.md](./basename-limit-evidence.md) |
| Where do I change remote filenames? | `mtpmanager/infra/remote_naming.py` — [device-contract.md](./device-contract.md) |
| Why doesn’t Experimental fall back to mtp-sendtr? | [decisions.md](./decisions.md) D3 |
| Why abort the whole album on one failure? | [decisions.md](./decisions.md) D5; CMD debrief |
| Why did Create Folder only store one letter? | [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) class D |
| What else in stock pymtp will break? | [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) |
| Which libmtp ops are we missing? | [libmtp-api-coverage.md](./libmtp-api-coverage.md) |
| How is transport chosen? | [architecture.md](./architecture.md); `AppController._transport` |
| How do I run tests / the app? | [README.md](../README.md), [PLATFORMS.md](../PLATFORMS.md) |
