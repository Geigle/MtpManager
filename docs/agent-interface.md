# Agent interface (CLI + MCP)

Headless, machine-readable API for coding agents (Grok Build, Copilot CLI, Claude Code, MCP clients). Does **not** drive the Tk GUI.

**Composition:** CLI and MCP call the same `mtpmanager.headless.HeadlessService` facade → existing `app/*` use cases. Dependency rule unchanged: no `mtp-sendtr` argv in the agent layer; remote names stay GUID + extension under Music `100` ([device-contract.md](./device-contract.md)).

## Quick start

```bash
# From project root, with the app venv active
.venv/bin/python -m mtpmanager.cli agent doctor
.venv/bin/python -m mtpmanager.cli library add-root /path/to/Music
.venv/bin/python -m mtpmanager.cli library search 'artist:nightwish' --limit 20
.venv/bin/python -m mtpmanager.cli library list-roots
.venv/bin/python -m mtpmanager.cli playlist create 'Nightwish picks'
.venv/bin/python -m mtpmanager.cli playlist add 'Nightwish picks' --guid <32hex>
.venv/bin/python -m mtpmanager.cli config get
.venv/bin/python -m mtpmanager.cli config patch '{"sync_album_art": true}'
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
- CLI/MCP acquire on device connect / sync / delete / refresh-index.
- If the lock is held by a **live** PID, device ops return `DEVICE_BUSY` (exit 3).
- Stale locks (dead PID) are broken automatically.

**Rule:** do not run device CLI while the GUI is open (or quit the GUI first). Host-only commands (library, config, doctor, inventory) never need the lock.

## Command groups

### Host (no USB)

| Command | Purpose |
|---------|---------|
| `agent doctor` | Paths, ffmpeg, mtp-sendtr, lock, index stats |
| `agent tools` | Tool catalog + JSON schemas |
| `library list-roots` | Library roots |
| `library set-roots PATH… [--no-rescan] [--confirm]` | Replace roots (empty + `--confirm` clears) |
| `library add-root PATH [--no-rescan]` | Append root; rescans by default |
| `library remove-root PATH [--no-rescan] [--confirm]` | Remove root; last root needs `--confirm` |
| `library scan [--root PATH]` | Scan into SQLite index (honors exclusions) |
| `library search QUERY [--limit N]` | Fuzzy search (`artist:`, `album:`, …); max limit 5000 |
| `library track --guid G` / `--path P` | One track |
| `playlist list` / `playlist show NAME` | Host M3U playlists |
| `playlist create NAME` | Create empty host playlist |
| `playlist add NAME --guid … / --path …` | Append tracks (skips existing paths by default) |
| `playlist replace NAME … --confirm` | Replace membership (no tracks → clear) |
| `playlist remove NAME --guid …` | Remove membership rows |
| `playlist move NAME --guid … --delta N` | Reorder (negative=up, positive=down) |
| `playlist shuffle NAME --algorithm artist\|spotify --confirm` | Host M3U shuffle in place |
| `playlist rename OLD NEW` | Rename host playlist |
| `playlist delete NAME --confirm` | Delete host playlist |
| `config get [key]` | Read `config.json` |
| `config patch '{"key": value}'` | Allowlisted keys only (see below) |
| `device status` | Lock + connection (no open session required) |
| `device list-known` | Known device serials from local index |
| `device inventory [filters]` | **Cached** only (no USB, **no lock**). See filters below |

### Device (session lock + USB)

| Command | Purpose |
|---------|---------|
| `device connect` / `disconnect` | Default PyMTP session; takes session lock |
| `device info` | Diagnostics (connected) |
| `device refresh-index` | Full `list_files` → replace SQLite cache (**slow**; quit GUI) |
| `device delete OBJECT_ID --confirm` | Single object delete |
| `sync --guid … --dry-run` | Plan send/skip |
| `sync --guid … --confirm [--mode …]` | Transfer (default transport = PyMTP) |
| `sync --playlist NAME --dry-run` / `--confirm [--push-playlist]` | Host M3U plan / send |
| `sync --entire-library --dry-run` | All indexed tracks (prefer dry-run first) |
| `sync --path-prefix DIR --dry-run` | Indexed tracks under a host folder |
| `playlist push NAME --confirm` | On-device playlist from host M3U (no track send) |

Host-only commands never need the device lock. `device status`, `device list-known`, and `device inventory` are safe while the GUI holds the lock (cache/SQLite only).

### `device inventory` filters (cache-only)

```bash
.venv/bin/python -m mtpmanager.cli device inventory \
  --limit 100 --offset 0 \
  --parent-id 100 \
  --name-contains abc \
  --guid <32hex> \
  --serial <serial>
```

Needs a serial from a prior `device refresh-index` (or GUI connect seed).

### `config patch` allowlist

Unknown keys fail with exit 2 and list allowed keys. Includes booleans such as `stable_mode`, `sync_album_art`, `enable_experimental_tools`, folder/podcast flags; `send_format`; optional `audio_encode` object; podcast schedule fields (`podcast_schedule_time`, `podcast_schedule_days`, `podcast_max_new_per_show`, …).

**`stable_mode: true`** selects mtp-sendtr for sync (GUI Stable Mode parity). Prefer patch over hand-editing the whole JSON file.

### Sync guards

- **`--dry-run`**: resolve tracks, report would-send / would-skip (GUID already on device index). No USB write.
- **`--confirm`**: required to send. Without it, exit 6.
- **Transport (mode):** same default as the GUI — **PyMTP** unless `config.json` has `stable_mode: true` (Config → Stable Mode). Omit `--mode` in normal use. Aliases: `default` / `pymtp` / `experimental` → PyMTP; `stable` / `cmd` / `mtp-sendtr` → subprocess `mtp-sendtr`. **Use Stable only when the default PyMTP path is failing** (deliberate recovery). JSON still reports wire values `experimental` | `stable` (matches `AppConfig.active_mode()`).
- **No silent fallback:** PyMTP send failures never auto-switch to Stable/`mtp-sendtr`. Agents must re-invoke with `--mode stable` after the user chooses recovery.
- **`--playlist NAME`**: resolve host M3U paths via library index. Paths missing from the index are soft-skipped and listed as `unresolved_paths` (not a hard fail if some tracks resolve).
- **`--entire-library` / `--path-prefix`**: bulk host scopes; prefer dry-run first.
- **`--push-playlist`**: after sends (or when everything was already on device), create/update the on-device playlist from the host M3U. Requires `--playlist`.
- **`--batch-size N`**: USB-friendly batches with quiet reconnect on PyMTP fatal (ZEN PTP session poison). Default **15** for `--playlist`, `--entire-library`, or `--path-prefix`; **0** (all at once) otherwise. Successful sends call `record_send` so skip-if-present stays accurate across batches/restarts.
- **Never** pass nested remote paths; the app always uses track GUID ObjectFileNames.

### Podcasts

| Command | Purpose |
|---------|---------|
| `podcast list` / `show` / `episodes ID` | Host index |
| `podcast subscribe URL` / `unsubscribe ID --confirm` | Manage feeds |
| `podcast refresh ID` / `download EPISODE_ID` | RSS + enclosure download (host) |
| `podcast full-sync-host` | Refresh + download N new/show; mark pending (**no USB**) |
| `podcast day-show` / `day-add` / `day-remove` | Today's day playlist (host M3U) |
| `podcast sync-pending --dry-run` / `--confirm` | Transfer pending episodes; optional `--push-day-playlist` (Finish Sync) |

### Device media (Phase 2)

| Command | Purpose |
|---------|---------|
| `device pull ID… --confirm [--dest DIR]` | Download objects (R3/R4) |
| `device enrich-tags ID… --confirm` | **Hazardous** metadata fetch (R1/R2); max 25 |
| `device send-video PATH [--parent-id 120\|124] --dry-run` / `--confirm` | Video/TV send (R1/R3/R4) |
| `sync-job status` / `clear --confirm` / `resume --dry-run` / `--confirm` | Durable multi-track job |

### Hazardous device tools

Risk classes: **R1** session poison · **R2** hang/metadata · **R3** USB exclusive · **R4** large download · **R5** destructive.

| Tool | Risk | Mitigation |
|------|------|------------|
| `device refresh-index` | R3; slow list_files | Quit GUI; quiet reconnect after fatal |
| `sync` / `podcast sync-pending` / `sync-job resume` | R1, R3 | dry-run; batch_size; no silent mode switch; fatal aborts batch |
| `device pull` | R3, R4 | confirm; per-id results; prefer host library |
| `device enrich-tags` | **R1, R2** | **Not for inventory**; confirm; ≤25 ids; abort on fatal; disconnect/quiet/reconnect after poison |
| `device send-video` | R1, R3, R4 (encode) | dry-run; parent 120/124 only on agent API |
| `device shrink` | R1, R3, R5 + quality loss | dry-run; small batches |
| `device delete-all` | R1, R3, R5 extreme | experimental tools + phrase `DELETE ALL TRACKS` |
| `device create-folder` | R3; ctypes string hazards | confirm; [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) |
| `device delete-bulk` | R1, R3, R5 | dry-run list first |
| `device-playlist *` | R1, R3 | confirm on writes; list/show need connect |
| `retail restore` | R1, R3, R5 | experimental; demo basenames (no GUID) |
| See also | | [pymtp-binding-hazards.md](./pymtp-binding-hazards.md), bulk session poison debrief |

### Phase 3 power tools (experimental / niche)

| Command | Purpose |
|---------|---------|
| `retail package EXPORT ZIP --confirm` | Host zip from retail export (needs experimental tools) |
| `retail restore PKG --dry-run` / `--confirm` | Restore demos to device |
| `device shrink --artist X --dry-run` / `--confirm` | Re-encode on-device lower bitrate |
| `device delete-all --confirm --confirm-phrase 'DELETE ALL TRACKS'` | Wipe all tracks |
| `device create-folder NAME [--parent-id 100] --confirm` | Create MTP folder |
| `device delete-bulk --artist X --dry-run` / `--object-id N --confirm` | Scoped bulk delete |
| `device-playlist list` / `show NAME` | On-device playlists |
| `device-playlist update NAME … --confirm` | Membership / reorder |
| `device-playlist shuffle NAME --confirm` | Artist or Spotify-style |
| `device-playlist recreate-host NAME --confirm` | Device → host M3U |

Example agent flow:

```bash
.venv/bin/python -m mtpmanager.cli library add-root ~/Music
.venv/bin/python -m mtpmanager.cli library search 'album:once'
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --dry-run
# quit GUI if open — default transport is PyMTP (same as GUI)
.venv/bin/python -m mtpmanager.cli sync --guid <32hex> --confirm

# Host playlist → device (PyMTP bulk + optional on-device playlist)
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --dry-run
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --confirm --push-playlist

# Seed device cache then query
.venv/bin/python -m mtpmanager.cli device connect
.venv/bin/python -m mtpmanager.cli device refresh-index
.venv/bin/python -m mtpmanager.cli device inventory --parent-id 100 --limit 50
.venv/bin/python -m mtpmanager.cli device disconnect

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

```bash
.venv/bin/python -m mtpmanager.mcp_server --data-dir /path/to/data
# or: MTP_MANAGER_DATA_DIR=...
```

Long ops: no progress notifications yet — watch log files. Cancel by killing the process; stale device locks recover when the holder PID is dead.

**Not agent tools:** album-art probe/experiment live only on `HeadlessService` for human/script debugging. They do not appear in `agent tools`, CLI, or MCP.

## What this is not

- Not an LLM inside MtpManager.
- Not a network/remote API.
- Not a replacement for [AGENTS.md](../AGENTS.md) (that doc is for **changing code**; this is for **operating** the app).

## TODO / backlog

Phased PR plan (P0–P3): **[plan-agent-interface-phases.md](./plan-agent-interface-phases.md)**.

| Milestone | Scope |
|-----------|--------|
| **A (Phase 0)** | Done — art experiment hidden; docs; `playlist_replace` confirm; catalog↔MCP tests |
| **B (Phase 1)** | Done — library scan/roots; config patch; refresh-index; inventory filters; playlist lifecycle; entire/path sync |
| **C (Phase 2)** | Done — podcasts; pull; enrich (**risk docs**); video; sync-job; MCP `--data-dir` |
| **D (Phase 3)** | Done — retail; shrink; delete-all; create-folder; bulk delete; device playlists |

Historical shipped item (`sync --playlist`): [todo-agent-cli.md](./todo-agent-cli.md).

**Dev-only (not agent tools):** `HeadlessService` album-art probe/experiment helpers may exist for humans/scripts; they must not appear in `agent tools`, CLI, or MCP (see plan PR 0.1).

## Related

- [plan-agent-interface-phases.md](./plan-agent-interface-phases.md) — phased PR backlog
- [architecture.md](./architecture.md) — layers / composition
- [device-contract.md](./device-contract.md) — send rules
- [transfer-and-modes.md](./transfer-and-modes.md) — Stable vs Experimental
