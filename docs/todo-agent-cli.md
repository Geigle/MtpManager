# Agent / CLI backlog (historical)

**Active plan:** [plan-agent-interface-phases.md](./plan-agent-interface-phases.md)  
**Operator docs:** [agent-interface.md](./agent-interface.md)

This file keeps shipped-item notes. Do not add new open TODOs here — extend the phased plan instead.

---

## TODO: `sync --playlist NAME`

**Status:** done (2026-08-06)  
**Priority:** high  
**Created:** 2026-08-06

### Goal

```bash
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --mode experimental --dry-run
.venv/bin/python -m mtpmanager.cli sync --playlist Rock --mode experimental --confirm
# optional:
#   --push-playlist     after sends, create/update on-device playlist
#   --batch-size 15     USB-friendly batches + quiet + reconnect on fatal
```

### Why

CLI had no playlist-scoped sync. Agents cannot pass 700 paths on argv; each CLI process disconnects on exit; a single `transfer_tracks` aborts remaining work on ZEN session poison without resume. Interim operator path was `scripts/sync_rock_experimental.py`.

### Implementation (shipped)

- Resolve host playlist M3U → library tracks (`HeadlessService._resolve_playlist_tracks`; soft missing paths → `unresolved_paths`).
- `HeadlessService.sync_tracks(..., playlist=, push_playlist=, batch_size=)` + CLI/MCP.
- `on_after_send` → `record_send` for all headless syncs (skip-if-present across batches).
- Playlist default `batch_size=15` + Experimental quiet reconnect on fatal (rock-script pattern).
- Docs: [agent-interface.md](./agent-interface.md). Tests: `tests/test_headless_cli.py`.

### Acceptance

- [x] `--playlist` dry-run JSON (would-send / would-skip for all resolved paths)
- [x] `--confirm` transfers missing tracks under device session lock
- [x] Optional `--push-playlist` after send
- [x] Unit tests without live device; docs updated

### Crash notes (2026-08-06 Rock Experimental run)

Full forensics: [debrief-zen-experimental-bulk-session-poison.md](./debrief-zen-experimental-bulk-session-poison.md).

Summary: five session deaths, **identical** PTP 2002 + 0x02ff + `CommandFailed` stack; **variable** sends-before-death (~40–384); software reconnect insufficient; skip-if-present + restart completed all 715 + playlist push.
