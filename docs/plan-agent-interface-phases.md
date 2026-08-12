# Agent interface backlog — phased PR plan

Phased plan to close CLI/MCP gaps vs the Tk app. Implementation order is **Phase 0 → 3**; each phase is one or more small PRs that leave `main` green.

**Composition rule (unchanged):** CLI and MCP call `mtpmanager.headless.HeadlessService` only. No `mtp-sendtr` argv in agent layers; remote names stay GUID + extension under Music `100` ([device-contract.md](./device-contract.md)). No silent Experimental → Stable fallback.

**Related:** [agent-interface.md](./agent-interface.md) (operator contract), historical notes in [todo-agent-cli.md](./todo-agent-cli.md).

---

## Design principles for every PR

1. **Catalog = truth.** Anything in `TOOL_CATALOG` must be wired in CLI *and* MCP (or explicitly `agent_visible: false` and stripped from MCP `tools/list` / CLI help).
2. **Confirm gates for destructive writes.** Host or device mutators that can lose data need `confirm=true` / `--confirm` (exit 6 without it).
3. **Dev experiments stay out of AI surfaces.** Keep `HeadlessService` helpers for human debugging if useful; do **not** register them as agent tools.
4. **Risk docs for hazardous device ops.** Any tool that walks USB, downloads, or runs Get_Trackmetadata-class APIs must document hang/poison risk in tool description + [agent-interface.md](./agent-interface.md).
5. **Tests without live device** for host paths and confirm/usage gates; mock device for service branches when practical.
6. **Docs in the same PR** as the surface change (`agent-interface.md`, this plan checkboxes, `docs/README.md` index if needed).

---

## Phase map

| Phase | Theme | Ship when |
|-------|--------|-----------|
| **0** | Correctness / hygiene | Catalog/CLI/MCP aligned; docs honest; confirm policy fixed; art experiment **hidden** from agents |
| **1** | Operate without GUI (host + device seed) | Scan roots, config patch, inventory refresh, richer cache query, full host playlist CRUD/reorder |
| **2** | Product depth + safe device media | Podcasts, pull, tag enrich (**risks documented**), video send, resume sync job, better MCP/long-ops |
| **3** | Experimental / niche | Retail, shrink, delete-all, create-folder, device playlist edit |

Suggested PR sizing: **one row in “PRs” tables ≈ one PR** unless noted “can merge”.

---

## Phase 0 — Correctness / hygiene (P0)

**Goal:** Agents see only intended tools; docs match behavior; destructive host ops cannot one-shot wipe playlists.

### PR 0.1 — Hide album-art *experiment* from agent surfaces

**Intent:** `device_art_experiment` (and probe if only used for that experiment) is a **development experiment**. Preserve code for humans; do **not** expose to AI via CLI or MCP.

| Keep | Remove / hide from agents |
|------|---------------------------|
| `HeadlessService.device_art_experiment` / `device_art_probe` (or move under `scripts/` / internal module later) | `TOOL_CATALOG` entries `device_art_probe`, `device_art_experiment` |
| Optional: `scripts/art_experiment.py` or unittest-only call sites | CLI `device art-probe` / `device art-experiment` subcommands |
| | MCP dispatch (today already missing — do not add) |

**Implementation notes:**

- Prefer **delete CLI subcommands + catalog entries** over a half-documented “hidden flag.”
- If probe remains useful for docs/debriefs, keep service method; call from a script or tests, not `agent tools`.
- Production album art remains: post-sync `push_album_art_for_tracks` when `sync_album_art` is on (no separate agent tool required in Phase 0).

**Acceptance:**

- [x] `agent tools` / MCP `tools/list` do not list art probe/experiment
- [x] `python -m mtpmanager.cli device --help` has no art-probe/art-experiment
- [x] Service methods still importable for dev (`device_art_probe` / `device_art_experiment`)
- [x] Tests: catalog name set does not include those tools; CLI parse rejects old subcommands

### PR 0.2 — Docs parity + inventory/lock semantics

**Update [agent-interface.md](./agent-interface.md):**

| Topic | Truth |
|-------|--------|
| Command table | Full set after 0.1 (playlist create/add/replace; **no** art experiment) |
| `device inventory` | **Cached only**; no USB walk; **no session lock required**. Needs serial from prior GUI/agent seed/connect |
| Device vs host groups | Host-only ops never take lock; connect/sync/delete/push do |
| TODO | Point at this plan; clear “none open” lie |
| MCP | Line-delimited JSON-RPC subset; client framing caveats |

**Acceptance:**

- [x] Docs match CLI after 0.1
- [x] Inventory lock/USB wording corrected
- [x] `docs/README.md` links this plan if not already

### PR 0.3 — Confirm gate for destructive host playlist replace

- `playlist_replace` already `destructive: true` in catalog → require `confirm` / `--confirm` (same pattern as `device_delete` / `sync_tracks`).
- Empty replace (clear playlist) especially must not run without confirm.
- MCP + CLI + service + tests.

**Acceptance:**

- [x] Without confirm → exit 6 / `CONFIRM_REQUIRED`
- [x] With confirm → replace/clear works as today
- [x] Catalog parameter schema includes `confirm`

### PR 0.4 — MCP catalog parity test (guardrail)

- Unit test: every `TOOL_CATALOG` name is handled in `mcp_server._call_tool` (and inverse: no orphan dispatch names).
- Optional: every catalog `cli` path is registered in `build_parser()`.

**Acceptance:**

- [x] Test fails if someone adds a catalog tool without MCP wiring (prevents 0.1-class skew)
- [x] Catalog CLI paths parse; art subcommands rejected

### Phase 0 exit criteria

- [x] AI-facing surface is intentional and consistent.
- [x] Docs are trustworthy for operators/agents.
- [x] No agent-visible art experiment.

---

## Phase 1 — Operate without the GUI (P1)

**Goal:** An agent can index music, adjust safe config, seed device cache, query inventory usefully, and manage host playlists end-to-end — without Tk.

### PR 1.1 — Library scan + roots

| Tool (suggested name) | Behavior |
|----------------------|----------|
| `library_list_roots` | Already exists |
| `library_scan` | Scan configured roots (or explicit `roots: []`) via `app/scan_library.py`; write SQLite index; return track counts / errors |
| `library_set_roots` or `library_add_root` / `library_remove_root` | Mutate roots then optional rescan; confirm if removing last root |

**Notes:** Reuse exclusions from config/index if already stored. Long scans: Phase 2 may add progress; Phase 1 can block until done with clear JSON summary.

**Acceptance:** Fresh data dir → set root → scan → `library search` returns tracks. Tests with temp trees. **Done (Milestone B).**

### PR 1.2 — Config patch (allowlisted keys)

| Tool | Behavior |
|------|----------|
| `config_get` | Exists |
| `config_patch` | JSON object of keys; **allowlist** only (e.g. `stable_mode`, `sync_album_art`, send format / encode preset fields, podcast toggles that already exist on `AppConfig`) |

**Hard rules:**

- Unknown keys → fail USAGE with allowed key list
- No arbitrary code paths; validate enums/types
- Document that `stable_mode: true` implies CMD transport for sync (same as GUI)
- Prefer patch over full replace to avoid wiping nested encode objects

**Acceptance:** Round-trip get → patch → get; invalid key rejected; tests without device. **Done (Milestone B).**

### PR 1.3 — Device index refresh + known devices

| Tool | Behavior |
|------|----------|
| `device_refresh_index` | Requires connect + lock; `list_files` (or existing seed path used by GUI connect); write `device_index`; return counts |
| `device_list_known` | Optional thin wrapper if not enough in inventory payload |

**Risks (document on tool + agent-interface):** Full file listing can be slow; USB exclusive; do not run concurrent with GUI; may stress flaky devices — prefer after quiet reconnect if prior fatal.

**Acceptance:** Mock or documented manual path; confirm not required (read/seed) unless we treat seed as heavy — **no confirm** is OK for refresh; still needs lock. **Done (Milestone B).**

### PR 1.4 — Richer cached inventory query

Extend `device_inventory` (or add `device_inventory_query`):

- `offset` / `limit` (or cursor)
- Filters: `parent_id`, name/guid-stem substring, optional media class if cheap from cache
- Return `total` always (already partial)

Still **cache-only** by default. Live walk only via `device_refresh_index`.

**Acceptance:** Pagination tests on synthetic device_index rows. **Done (Milestone B).**

### PR 1.5 — Host playlist lifecycle

| Tool | Maps to |
|------|---------|
| `playlist_delete` | `delete_playlist` + confirm |
| `playlist_rename` | `rename_playlist` |
| `playlist_remove` | `remove_paths_from_playlist` by guid/path |
| `playlist_move` | `move_paths_in_playlist` (indices or before/after) |
| `playlist_shuffle` | domain shuffle (artist merge / spotify); **host M3U only**; confirm optional if overwrite in place |

Keep existing create/add/replace/push/sync `--playlist`.

**Acceptance:** Full CRUD tests on temp index DB; shuffle deterministic seed if API allows. **Done (Milestone B).**

### PR 1.6 — Sync conveniences (optional same phase)

| Tool / flag | Behavior |
|-------------|----------|
| `sync_tracks` + `scope: entire_library` or `library_sync` | All indexed tracks; force dry-run first habit via docs; require confirm; default batch_size like playlist |
| `path_prefix` / `folder` | Expand to tracks under host directory |

Can ship as 1.6 or early Phase 2 if 1.1–1.5 already large. **Done (Milestone B):** `entire_library` + `path_prefix`.

### Phase 1 exit criteria

- [x] Headless bootstrap: roots → scan → playlist → dry-run sync → refresh index → inventory query.
- [x] Config knobs agents need without hand-editing JSON.
- [x] Playlist management parity with common GUI playlist tab ops (except device-side edit).

---

## Phase 2 — Product depth (P2)

**Goal:** Podcasts and device media paths agents need for real ZEN workflows; long-op ergonomics; **hazard documentation** on anything that can poison sessions or thrash USB.

### Cross-cutting: risk documentation standard

For every Phase 2+ device tool that is more than cache read, the PR must update:

1. **Tool `description`** in `TOOL_CATALOG` (short risk line agents actually see)
2. **[agent-interface.md](./agent-interface.md)** section *Hazardous device tools* (table: tool, risk class, mitigations)
3. Link to debriefs where applicable ([pymtp-binding-hazards.md](./pymtp-binding-hazards.md), bulk session poison, track metadata hangs)

**Risk classes (use consistently):**

| Class | Meaning | Mitigations |
|-------|---------|-------------|
| **R1 Session poison** | PTP/libmtp error leaves session unusable | Abort batch; reconnect + quiet; no silent mode switch |
| **R2 Hang / metadata** | `get_track_metadata` / bad finalize / album list | Prefer cache; explicit opt-in; timeout where possible; never auto-enrich listings |
| **R3 USB exclusive** | Lock contention with GUI | `DEVICE_BUSY`; docs: quit GUI |
| **R4 Large download** | Pull / podcast / video disk + time | confirm; size in dry-run when possible |
| **R5 Destructive** | Delete / replace | confirm; single-id default |

### PR 2.1 — Podcast host ops (no USB)

Subscribe/list/refresh/download/settings mirrors `podcast_ops` / `podcast_index` without device:

- `podcast_list` / `podcast_show` / `podcast_episodes`
- `podcast_subscribe` / `podcast_unsubscribe` (confirm on unsub)
- `podcast_refresh` / `podcast_download_episode`
- `podcast_full_sync_host` (schedule pass: refresh + download N new) — host only
- Day playlist: add/remove episode GUIDs; `podcast_day_playlist_show`

Wire encode lookup already used inside headless sync for genre podcast.

**Acceptance:** Temp DB + mocked feed HTTP where tests already do; no device required.

### PR 2.2 — Podcast → device

- Sync pending/selected episodes through existing transfer pipeline (GUID path)
- Optional day-playlist push (align with GUI “Finish Sync” semantics — **no auto flood push** if product rule says Library → Finish Sync only)
- Document interaction with `enable_experimental_tools` / video podcast flags (no-op or explicit error when tools off)

**Risks:** R1, R3, R4; video path R1 heavier.

### PR 2.3 — Device pull

| Tool | Behavior |
|------|----------|
| `device_pull` | object_id(s) → library root or `--dest`; confirm; uses existing retrieve helpers |

**Risks:** R3, R4; partial failures reported per id. Do not invent nested remote names on re-send later (GUID contract still applies on next sync).

### PR 2.4 — Tag enrich (explicit, hazardous)

| Tool | Behavior |
|------|----------|
| `device_enrich_tags` | Opt-in only; wraps `enrich_track_refs_with_embedded_fallback` (metadata then download/mutagen) for given object ids |

**Must document (tool + agent-interface):**

- **Not used for normal inventory.** Device tree in GUI uses listing + host GUID join only; enrich is explicit because **Get_Trackmetadata / download can hang or poison** (R2, R1).
- Prefer host library tags + GUID ObjectFileName when possible.
- Require `confirm=true` even though “read-ish,” because side effects include USB traffic and possible session death.
- Recommend small batches; on failure abort remaining (same fatal philosophy).
- After poison: disconnect, quiet, reconnect, `device_refresh_index` if needed — do not continue enrich.

**Acceptance:** Tests with mocks; catalog description contains hang/poison warning; docs table row present.

### PR 2.5 — Send video

- `device_send_video` with profile presets / parent folder (Video 120 / TV 124) via `prepare_and_send_video`
- dry-run + confirm
- Document encode time (R4) and experimental-ish profile limits

### PR 2.6 — Resume sync job

- Expose `sync_job` state: `sync_job_status`, `sync_resume` / CLI `sync --resume`
- Align with GUI Resume Sync; headless batch reconnect already partial — job file makes multi-process resume possible

**Risks:** R1; document that resume still respects fatal abort within a batch.

### PR 2.7 — MCP / long-op ergonomics

Pick what clients need; can split PRs:

| Item | Notes |
|------|--------|
| `--data-dir` / `MTP_MANAGER_DATA_DIR` on MCP process | Match CLI |
| Progress notifications | Optional MCP `notifications/progress` or log lines; don’t block Phase 2.1–2.6 |
| Cancellation | Cooperative flag if transfer supports it; else document “kill process + lock stale recovery” |
| Framing | Document line-delimited requirement **or** add Content-Length if a target client requires it (spike first) |

Official MCP SDK remains optional (existing note in agent-interface).

### Phase 2 exit criteria

- [x] Podcast host + device path usable from CLI/MCP with experimental flags respected.
- [x] Pull and enrich available with **visible risk docs**.
- [x] Video send and job resume available; MCP `--data-dir` shipped; progress/cancel deferred (docs only).

---

## Phase 3 — Experimental / niche (P3)

Ship only when Phase 1–2 are stable; keep behind clear naming and confirm.

| PR | Tooling | Notes |
|----|---------|--------|
| 3.1 | `retail_package` / `retail_restore` | `app/retail_ops`; confirm; experimental flag |
| 3.2 | `device_shrink` | Per artist/album; confirm; document quality loss |
| 3.3 | `device_delete_all_tracks` | Extreme confirm phrase or double confirm; experimental tools on |
| 3.4 | `device_create_folder` | Parent id + name; ctypes/string hazards — see pymtp binding docs |
| 3.5 | Device playlist edit | list / remove / reorder / shuffle on-device; recreate host from device |
| 3.6 | Bulk delete by album/artist | confirm + dry-run listing object ids first |

Each PR: catalog + CLI + MCP + tests + hazard table rows.

---

## Dependency graph (simplified)

```text
0.1 hide art experiment ──┐
0.2 docs ─────────────────┼─► Phase 0 done
0.3 playlist_replace confirm
0.4 catalog↔MCP test ─────┘
         │
         ▼
1.1 scan/roots ──► 1.6 entire/folder sync (optional)
1.2 config_patch
1.3 refresh_index ──► 1.4 inventory query
1.5 playlist lifecycle
         │
         ▼
2.1 podcast host ──► 2.2 podcast device
2.3 pull
2.4 enrich (docs heavy)
2.5 video
2.6 resume job
2.7 MCP ergonomics (parallelizable)
         │
         ▼
3.x experimental suite (order flexible)
```

---

## Out of scope (do not put on agent surface)

| Item | Reason |
|------|--------|
| Tk playback controls | GUI-only |
| Silent Experimental→Stable fallback | Invariant D3 |
| Nested remote path send | Device contract |
| Auto tag-enrich on every inventory | Hang class |
| Auto on-device playlist after podcast flood | Product rule: Finish Sync / explicit push |
| Exposing `device_art_experiment` to CLI/MCP | Dev experiment only (Phase 0.1) |

---

## Suggested milestone checklist

### Milestone A — Safe agent baseline (Phase 0)

- [x] PR 0.1 Art experiment hidden
- [x] PR 0.2 Docs parity
- [x] PR 0.3 playlist_replace confirm
- [x] PR 0.4 Catalog/MCP parity test

### Milestone B — Headless daily driver (Phase 1)

- [x] PR 1.1 Library scan/roots
- [x] PR 1.2 Config patch
- [x] PR 1.3 Device refresh index
- [x] PR 1.4 Inventory query
- [x] PR 1.5 Playlist lifecycle
- [x] PR 1.6 Entire/folder sync

### Milestone C — Full product agent (Phase 2)

- [x] PR 2.1–2.2 Podcasts
- [x] PR 2.3 Pull
- [x] PR 2.4 Enrich + risk docs
- [x] PR 2.5 Video
- [x] PR 2.6 Resume job
- [x] PR 2.7 MCP ergonomics (`--data-dir`; progress/cancel documented, not implemented)

### Milestone D — Power tools (Phase 3)

- [ ] PR 3.1–3.6 as needed

---

## Implementation cheat sheet

| Concern | Primary files |
|---------|----------------|
| Tool catalog | `mtpmanager/headless/tools.py` |
| Service | `mtpmanager/headless/service.py` |
| CLI | `mtpmanager/cli/main.py` |
| MCP | `mtpmanager/mcp_server.py` |
| Tests | `tests/test_headless_cli.py` (+ new modules as needed) |
| Operator docs | `docs/agent-interface.md` |
| This plan | `docs/plan-agent-interface-phases.md` |
| Scan | `mtpmanager/app/scan_library.py` |
| Config | `mtpmanager/infra/app_config.py` |
| Device index | `mtpmanager/infra/device_index.py`, seed paths in `app/device_ops` / GUI connect |
| Playlists | `mtpmanager/infra/playlists.py`, `domain/playlist_*.py` |
| Podcasts | `mtpmanager/app/podcast_ops.py`, `podcast_schedule.py`, `infra/podcast_index.py` |
| Pull / enrich | `mtpmanager/app/device_ops.py`, enrich helpers + `tests/test_enrich_tracks.py` |
| Video | `mtpmanager/app/device_ops.prepare_and_send_video`, `tests/test_send_video.py` |
| Sync job | `mtpmanager/infra/sync_job.py` |

---

## History

| Date | Note |
|------|------|
| 2026-08-11 | Plan created from CLI/MCP gap review. P0.1: **hide** art experiment (do not MCP-wire). P2 enrich: mandatory risk docs. |
| 2026-08-11 | Milestone A shipped: art tools off agent surface; playlist_replace confirm; catalog↔MCP/CLI parity tests; docs. |
| 2026-08-11 | Milestone B shipped: library roots/scan; config_patch; refresh-index; inventory filters; playlist CRUD/reorder/shuffle; entire_library + path_prefix sync. |
| 2026-08-11 | Milestone C shipped: podcasts; pull; enrich (R1/R2 docs); video; sync-job; MCP --data-dir; 49 agent tools. |
