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

**When debugging transfers, start with [device-contract.md](./device-contract.md) plus the debrief for your transport** (CMD finalize vs PyMTP layers).

---

## Document index

| Doc | Purpose |
|-----|---------|
| [architecture.md](./architecture.md) | System design: layers, package map, composition root, dual-mode wiring, logging, known gaps |
| [decisions.md](./decisions.md) | ADR-lite: dual mode, no silent fallback, shared naming, fatal abort, pymtp patches, license, … |
| [device-contract.md](./device-contract.md) | MTP/ZEN remote path, storage, basename, tags vs filename — **do not rebreak** |
| [transfer-and-modes.md](./transfer-and-modes.md) | Scan → transcode → send; Stable vs Experimental; batch abort; tests |
| [debrief-zen-track-send-failure.md](./debrief-zen-track-send-failure.md) | Incident: CMD 99% finalize, hang, batch abort (forensic detail) |
| [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) | Incident: layered PyMTP binding failures; no silent CMD fallback |

---

## Quick answers

| Question | Answer lives in |
|----------|-----------------|
| Where do I change remote filenames? | `mtpmanager/infra/remote_naming.py` — [device-contract.md](./device-contract.md) |
| Why doesn’t Experimental fall back to mtp-sendtr? | [decisions.md](./decisions.md) D3 |
| Why abort the whole album on one failure? | [decisions.md](./decisions.md) D5; CMD debrief |
| How is transport chosen? | [architecture.md](./architecture.md); `AppController._transport` |
| How do I run tests / the app? | [README.md](../README.md), [PLATFORMS.md](../PLATFORMS.md) |
