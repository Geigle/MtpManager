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

## D2 — Dual modes: Stable (`mtp-sendtr`) vs Experimental (PyMTP)

**Context:** libmtp’s `mtp-sendtr` is battle-tested for one-shot sends. In-process PyMTP enables device admin (folders, name, listing) and experiments, but stock bindings are fragile.

**Decision:** Two UI tabs. Stable uses `CmdTransport` (subprocess per track). Experimental uses `PymtpDevice` (long-lived session) for admin + experimental send. Stable is recommended for loading music.

**Rationale:** Different reliability and session models; users choose deliberately. Composition: `AppController._transport()`.

**Consequences:** Two code paths must share the remote contract (D4). Experimental requires Connect before send. Action lists differ (`STABLE_ACTIONS` vs `EXPERIMENTAL_ACTIONS`).

**Source:** `ui/window.py`, `ui/controllers.py`; [transfer-and-modes.md](./transfer-and-modes.md).

---

## D3 — No silent CMD fallback from Experimental on send failure

**Context:** After layered PyMTP bugs, it was tempting to auto-retry via `mtp-sendtr` so “tracks still land.”

**Decision:** Experimental `send_track` is pure libmtp/PyMTP only. On failure, raise `TransportError` and show recovery steps pointing the user to **Stable Mode**. Never call CMD from Experimental send without an explicit user mode switch.

**Rationale:** Silent fallback hides regressions, mixes transports, and makes debugging impossible. Honest UX preserves the Experimental surface as a real binding under test.

**Consequences:** Users must Disconnect (and often replug) then switch tabs. Logs are the source of truth for pure PyMTP failures.

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

**Context:** Verbose archive-style object names hit ~64-char limits and unsafe characters (`&`). Player UI still needs real titles.

**Decision:** Metadata flags/fields keep full title/artist/album (including `&`). Object basename is sanitized, length-bounded, extension required (`08 Flesh Metal.mp3`).

**Rationale:** Tags and object names are different channels on MTP; only the name is device-fragile on Creative-era firmware.

**Consequences:** On-device browser may show short names; library views that use tags stay correct. Do not “fix” send by stuffing full `Artist - Album - Title` into the remote path.

**Source:** [device-contract.md](./device-contract.md); CMD debrief.

---

## D8 — Patch stock pymtp in-process rather than forking PyPI package

**Context:** Stock pymtp is effectively unmaintained vs libmtp 1.1.x: missing `FOLDER=0` in filetype enum (MP3 labeled as WAV), missing ctypes `argtypes`, `Dump_Errorstack` without device pointer, macOS `find_library` failure.

**Decision:** Load via `infra/pymtp_wrapper.py`: macOS lib path patch, mutate `LIBMTP_Filetype` in place, fix send/errorstack bindings. Unit-test filetype table.

**Rationale:** Small project surface; avoids maintaining a full fork until an upstream binding is viable.

**Consequences:** Always import pymtp through the wrapper. Upgrading pymtp may require re-checking patches. Experimental send still device/session-dependent after binding fixes.

**Source:** [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md); `tests/test_pymtp_filetypes.py`.

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
