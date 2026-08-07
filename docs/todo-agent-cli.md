# Agent / CLI backlog

## TODO: `sync --playlist NAME`

**Status:** open  
**Priority:** high (unblocked Rock playlist work only via ad-hoc script)  
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

CLI today has no playlist-scoped sync. Agents cannot pass 700 paths on argv; each CLI process disconnects on exit; a single `transfer_tracks` aborts remaining work on ZEN session poison without resume. Interim operator path: `scripts/sync_rock_experimental.py`.

### Implementation sketch

- Resolve host playlist M3U → library tracks (`infra/playlists`, `domain/playlist_m3u`, index).
- Reuse `HeadlessService.sync_tracks` / `app.transfer`; skip-if-present via `device_index.guid_stems_on_device`.
- Wire `on_after_send` → `record_send` (CLI path currently omits this).
- Long runs: batch + fatal recovery (quiet, reconnect, resume unfinished).
- MCP: `sync_tracks` + `playlist` param; tool catalog + `docs/agent-interface.md`.
- Hard invariants: GUID ObjectFileNames under Music 100; no silent Experimental→Stable fallback.

### Acceptance

- [ ] `--playlist` dry-run JSON (would-send / would-skip for all resolved paths)
- [ ] `--confirm` transfers missing tracks under device session lock
- [ ] Optional `--push-playlist` after send
- [ ] Unit tests without live device; docs updated

### Crash notes (2026-08-06 Rock Experimental run)

Full forensics: [debrief-zen-experimental-bulk-session-poison.md](./debrief-zen-experimental-bulk-session-poison.md).

Summary: five session deaths, **identical** PTP 2002 + 0x02ff + `CommandFailed` stack; **variable** sends-before-death (~40–384); software reconnect insufficient; skip-if-present + restart completed all 715 + playlist push.
