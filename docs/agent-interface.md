# Agent interface (CLI + MCP)

Headless, machine-readable API for coding agents (Grok Build, Copilot CLI, Claude Code, MCP clients). Does **not** drive the Tk GUI.

**Composition:** CLI and MCP call the same `mtpmanager.headless.HeadlessService` facade → existing `app/*` use cases. Dependency rule unchanged: no `mtp-sendtr` argv in the agent layer; remote names stay GUID + extension under Music `100` ([device-contract.md](./device-contract.md)).

## Quick start

```bash
# From project root, with the app venv active
.venv/bin/python -m mtpmanager.cli agent doctor
.venv/bin/python -m mtpmanager.cli library search 'artist:nightwish' --limit 20
.venv/bin/python -m mtpmanager.cli library list-roots
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
| `library search QUERY` | Fuzzy search (`artist:`, `album:`, …) |
| `library track --guid G` / `--path P` | One track |
| `playlist list` / `playlist show NAME` | Host M3U playlists |
| `config get [key]` | Read `config.json` |
| `device status` | Lock + connection (no open session required) |

### Device (lock + USB)

| Command | Purpose |
|---------|---------|
| `device connect` / `disconnect` | Experimental PyMTP session |
| `device info` | Diagnostics |
| `device inventory [--limit N]` | **Cached** inventory only (no full USB walk) |
| `device delete OBJECT_ID --confirm` | Single object delete |
| `sync --guid … --dry-run` | Plan send/skip |
| `sync --guid … --confirm [--mode stable\|experimental]` | Transfer |
| `playlist push NAME --confirm` | On-device playlist from host M3U |

### Sync guards

- **`--dry-run`**: resolve tracks, report would-send / would-skip (GUID already on device index). No USB write.
- **`--confirm`**: required to send. Without it, exit 6.
- **Mode**: defaults to `config.json` (`stable_mode`). Override with `--mode stable|experimental`.
- **Never** pass nested remote paths; the app always uses track GUID ObjectFileNames.
- Experimental failures **do not** fall back to Stable/`mtp-sendtr`.

Example agent flow:

```bash
.venv/bin/python -m mtpmanager.cli library search 'album:once'
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --dry-run
# quit GUI if open
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --confirm --mode stable
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

Tools mirror `agent tools` names (`library_search`, `sync_tracks`, …). Destructive tools require `confirm: true`.

**Note:** This is a minimal MCP subset (initialize, tools/list, tools/call). Upgrade to the official Python MCP SDK later if a client needs full protocol features.

## What this is not

- Not an LLM inside MtpManager.
- Not a network/remote API.
- Not a replacement for [AGENTS.md](../AGENTS.md) (that doc is for **changing code**; this is for **operating** the app).

## TODO / backlog

| Item | Notes |
|------|--------|
| **`sync --playlist NAME`** | First-class CLI/MCP: resolve host M3U → tracks, dry-run / confirm, optional `--push-playlist`, batch + reconnect on fatal (so agents need no ad-hoc scripts). Wire `record_send` on after-send. Full write-up: [todo-agent-cli.md](./todo-agent-cli.md). Interim: `scripts/sync_rock_experimental.py`. |

## Related

- [architecture.md](./architecture.md) — layers / composition
- [device-contract.md](./device-contract.md) — send rules
- [transfer-and-modes.md](./transfer-and-modes.md) — Stable vs Experimental
