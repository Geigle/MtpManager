# Agent interface (CLI + MCP)

Headless, machine-readable API for coding agents (Grok Build, Copilot CLI, Claude Code, MCP clients). Does **not** drive the Tk GUI.

**Composition:** CLI and MCP call the same `mtpmanager.headless.HeadlessService` facade → existing `app/*` use cases. Dependency rule unchanged: no `mtp-sendtr` argv in the agent layer; remote names stay GUID + extension under Music `100` ([device-contract.md](./device-contract.md)).

## Quick start

```bash
# From project root, with the app venv active
.venv/bin/python -m mtpmanager.cli agent doctor
.venv/bin/python -m mtpmanager.cli library search 'artist:nightwish' --limit 20
.venv/bin/python -m mtpmanager.cli library list-roots
.venv/bin/python -m mtpmanager.cli playlist create 'Nightwish picks'
.venv/bin/python -m mtpmanager.cli playlist add 'Nightwish picks' --guid <32hex>
.venv/bin/python -m mtpmanager.cli config get
.venv/bin/python -m mtpmanager.cli agent tools
```

Every command prints one JSON object on stdout:

```json
{
  "ok": true,
  "code": "ok",
  "message": "",
  "data": { },
  "exit_code": 0
}
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic error |
| 2 | Usage / bad args |
| 3 | Device session busy (another process holds USB) |
| 4 | Fatal transport error (abort remaining batch) |
| 5 | Not found |
| 6 | Confirm required (`--confirm` / `confirm=true`) |
| 7 | Cancelled |

### Data directory

Same as the GUI: platform data dir, or `MTP_MANAGER_DATA_DIR`, or CLI `--data-dir`.

## Device session lock

File: `device_session.lock` under the data dir.

- GUI acquires holder `"gui"` for the app lifetime.
- CLI/MCP acquire on device connect / sync / delete.
- If the lock is held by a **live** PID, device ops return `DEVICE_BUSY` (exit 3).
- Stale locks (dead PID) are broken automatically.

**Rule:** do not run device CLI while the GUI is open (or quit the GUI first). Host-only commands (library, config, doctor) never need the lock.

## Command groups

### Host (no USB)

| Command | Purpose |
|---------|---------|
| `agent doctor` | Paths, ffmpeg, mtp-sendtr, lock, index stats |
| `agent tools` | Tool catalog + JSON schemas |
| `library list-roots` | Library roots |
| `library search QUERY [--limit N]` | Fuzzy search (`artist:`, `album:`, …); max limit 5000 |
| `library track --guid G` / `--path P` | One track |
| `playlist list` / `playlist show NAME` | Host M3U playlists |
| `playlist create NAME` | Create empty host playlist |
| `playlist add NAME --guid … / --path …` | Append tracks (skips existing paths by default) |
| `playlist replace NAME --guid … / --path … --confirm` | Replace membership (no tracks → clear). **Requires `--confirm`** (exit 6 without it) |
| `config get [key]` | Read `config.json` |
| `device status` | Lock + connection (no open session required) |
| `device inventory [--limit N]` | **Cached** inventory only (no USB walk, **no lock**). Needs a serial from a prior connect/seed (GUI or future `device refresh-index`) |

### Device (session lock + USB)

| Command | Purpose |
|---------|---------|
| `device connect` / `disconnect` | Default PyMTP session (same transport as GUI); takes session lock |
| `device info` | Diagnostics (connected) |
| `device delete OBJECT_ID --confirm` | Single object delete |
| `sync --guid … --dry-run` | Plan send/skip (may use cache for skip-if-present; no write) |
| `sync --guid … --confirm [--mode …]` | Transfer (default transport = PyMTP) |
| `sync --playlist NAME --dry-run` | Plan entire host M3U (would-send / would-skip / unresolved) |
| `sync --playlist NAME --confirm [--push-playlist] [--batch-size N]` | Transfer missing tracks; optional on-device playlist push |
| `playlist push NAME --confirm` | On-device playlist from host M3U (no track send) |

Host-only commands never need the device lock. `device status` and `device inventory` are safe while the GUI holds the lock (inventory is SQLite cache only).

### Sync guards

- **`--dry-run`**: resolve tracks, report would-send / would-skip (GUID already on device index). No USB write.
- **`--confirm`**: required to send. Without it, exit 6.
- **Transport (mode):** same default as the GUI — **PyMTP** unless `config.json` has `stable_mode: true` (Config → Stable Mode). Omit `--mode` in normal use. Aliases: `default` / `pymtp` / `experimental` → PyMTP; `stable` / `cmd` / `mtp-sendtr` → subprocess `mtp-sendtr`. **Use Stable only when the default PyMTP path is failing** (deliberate recovery). JSON still reports wire values `experimental` | `stable` (matches `AppConfig.active_mode()`).
- **No silent fallback:** PyMTP send failures never auto-switch to Stable/`mtp-sendtr`. Agents must re-invoke with `--mode stable` after the user chooses recovery.
- **`--playlist NAME`**: resolve host M3U paths via library index. Paths missing from the index are soft-skipped and listed as `unresolved_paths` (not a hard fail if some tracks resolve).
- **`--push-playlist`**: after sends (or when everything was already on device), create/update the on-device playlist from the host M3U. Requires `--playlist`.
- **`--batch-size N`**: USB-friendly batches with quiet reconnect on PyMTP fatal (ZEN PTP session poison). Default **15** when `--playlist` is set; **0** (all at once) otherwise. Successful sends call `record_send` so skip-if-present stays accurate across batches/restarts.
- **Never** pass nested remote paths; the app always uses track GUID ObjectFileNames.

Example agent flow:

```bash
.venv/bin/python -m mtpmanager.cli library search 'album:once'
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --dry-run
# quit GUI if open — default transport is PyMTP (same as GUI)
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --confirm

# Host playlist → device (PyMTP bulk + optional on-device playlist)
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --dry-run
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --confirm --push-playlist

# Only if PyMTP is failing and the user wants mtp-sendtr recovery:
# .venv/bin/python -m mtpmanager.cli sync --guid <32hex> --confirm --mode stable
```

## MCP server

Zero extra dependencies: line-delimited JSON-RPC on stdio.

```bash
.venv/bin/python -m mtpmanager.mcp_server
```

Client config sketch (Cursor / Claude Desktop style):

```json
{
  "mcpServers": {
    "mtpmanager": {
      "command": "/absolute/path/to/MtpManager/.venv/bin/python",
      "args": ["-m", "mtpmanager.mcp_server"]
    }
  }
}
```

Tools mirror `agent tools` names (`library_search`, `sync_tracks`, …). Destructive tools require `confirm: true` (including `playlist_replace`).

**Note:** This is a minimal MCP subset (initialize, tools/list, tools/call) using **line-delimited** JSON-RPC on stdio (one JSON object per line), not Content-Length framing. Some clients expect the official framing/SDK — verify before relying on auto-connect. Upgrade to the official Python MCP SDK later if a client needs full protocol features.

**Not agent tools:** album-art probe/experiment live only on `HeadlessService` for human/script debugging. They do not appear in `agent tools`, CLI, or MCP.

## What this is not

- Not an LLM inside MtpManager.
- Not a network/remote API.
- Not a replacement for [AGENTS.md](../AGENTS.md) (that doc is for **changing code**; this is for **operating** the app).

## TODO / backlog

Phased PR plan (P0–P3): **[plan-agent-interface-phases.md](./plan-agent-interface-phases.md)**.

| Milestone | Scope |
|-----------|--------|
| **A (Phase 0)** | Hide art experiment from agents; docs parity; `playlist_replace` confirm; catalog↔MCP guard test |
| **B (Phase 1)** | Library scan/roots; config patch; device index refresh; inventory query; host playlist lifecycle |
| **C (Phase 2)** | Podcasts; pull; tag enrich (**risk docs required**); video; resume job; MCP ergonomics |
| **D (Phase 3)** | Retail, shrink, delete-all, create-folder, device playlist edit |

Historical shipped item (`sync --playlist`): [todo-agent-cli.md](./todo-agent-cli.md).

**Dev-only (not agent tools):** `HeadlessService` album-art probe/experiment helpers may exist for humans/scripts; they must not appear in `agent tools`, CLI, or MCP (see plan PR 0.1).

## Related

- [plan-agent-interface-phases.md](./plan-agent-interface-phases.md) — phased PR backlog
- [architecture.md](./architecture.md) — layers / composition
- [device-contract.md](./device-contract.md) — send rules
- [transfer-and-modes.md](./transfer-and-modes.md) — Stable vs Experimental
