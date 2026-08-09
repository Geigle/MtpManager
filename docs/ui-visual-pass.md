# UI visual pass — inventory follow-up and platform notes

**Purpose:** Capture the visual-element survey outcomes so a later implementation pass does not rediscover layout, control grammar, or Linux desktop blend work. **No send-path or MTP invariants change here** — UI chrome only (`mtpmanager/ui/`).

**Status:** Phases **1–3 done**. Phase 4 (platform blend) still backlog.

**Related code:** `mtpmanager/ui/window.py`, `dialogs.py`, light styling via Treeview tags and `_HoverTip`. No design-token module yet.

---

## Implementation order (agreed)

Do **not** start platform polish (phase 4) before the shared structural work (phases 1–3). Phases 1–3 are platform-agnostic foundations; they also reduce how much per-desktop special casing is needed later.

| Phase | Scope | Environment | Notes |
|-------|--------|-------------|--------|
| **1** | §7.1 — chrome baseline (O1 + O4) | Any (author on macOS OK) | tk/ttk hybrid + sunken “MDI” frames |
| **2** | §7.2 — IA / list consistency (O2 + O3 + P3/P4) | Any | Podcasts tab vs media trees; nested Device notebook |
| **3** | §7.3 — control grammar (O5–O7) | Any | Glyph vs text buttons; time spinner; dual Scale types |
| **4a** | §7.4 — macOS blend | **Primary test host (now)** | Aqua / menubar / tips / Finder wording / dark-safe colors |
| **4b** | §7.4 — KDE (Breeze) | **Later** (Linux machine refresh) | See [KDE recommendations](#kde-plasma--breeze-recommendations-phase-4b) |
| **4c** | §7.4 — GNOME (Adwaita) | **Later** (same Linux refresh) | See [GNOME recommendations](#gnome--adwaita-recommendations-phase-4c) |

**Rule of thumb:** After 1–3, re-test on macOS before investing in 4b/4c. KDE/GNOME sections below are **frozen recommendations**, not current work.

---

## Survey shorthand (from inventory)

### Outliers referenced by phase

| ID | Summary |
|----|---------|
| **O1** | Hybrid classic `tk` + selective `ttk` (two skins in one window) |
| **O2** | Host Podcasts tab is Listbox + headers + glyph stack + Sync Latest — not a media Treeview like Music/Video/Audiobooks |
| **O3** | Nested notebooks (media tabs + Device → inner Music/Video/…) |
| **O4** | Stacked `borderwidth=1` + `relief="sunken"` regions (root, toolbar, left/right, bottom) |
| **O5** | Mixed button grammar: `+`/`−`/`×`/`↻`/`↑`/`↓`/`▲`/`▼` vs English “Prev”/“Play”/“Sync Latest” |
| **O6** | Custom hour/min/AM·PM spinner columns in Podcast Settings |
| **O7** | Scrubber = `ttk.Scale`; encode dialogs = classic `Scale` |
| **O8** | Playing purple vs soft transfer greens/red vs video teal (semantic color pile) |
| **O9** | Stable Mode dumps multi-paragraph help into the Device panel |
| **O11** | Global Treeview `rowheight` raised for album thumbs (tall rows where no art) |
| **O12** | Hard-coded gray `#333`–`#666` dialog prose (dark-mode unsafe) |
| **O13** | “Reveal in Finder” on all platforms |

### Patterns (keep / align to)

| ID | Description |
|----|-------------|
| **P3** | Media Treeview + dual scroll + group bold + transfer/playing tags |
| **P4** | Playlist chrome: Combobox + Rename + +/− + move + status (host ≈ device twins) |
| **P6** | Dialog footer: Cancel / primary, ~width 10 |

Full classification lives in the conversation survey; this doc only tracks work order and Linux backlog.

---

## Phase 1 — §7.1 Chrome baseline (O1 + O4) — **DONE**

**Goal:** One coherent window chrome language so later OS polish is palette/theme, not fighting Motif wells + mixed skins.

**Decision (shipped as [D17](./decisions.md)):**

1. **Control stack:** Main-window interactive chrome → **ttk** (`Button`, `Entry`, `Scrollbar` + existing Notebook/Treeview/Combobox/Progressbar/Scale). Styles `Compact.TButton` / `Tool.TButton` in `ui/chrome.py`.
2. **Frame language:** Flat layout frames + `ttk.Separator` hairlines (toolbar, sidebar split, bottom). No stacked sunken wells on the main window.

**Shipped surfaces:**

- `mtpmanager/ui/chrome.py` — baseline helper
- `mtpmanager/ui/window.py` — packing + interactive widgets
- ADR: [decisions.md](./decisions.md) D17

**Documented exceptions (still classic tk):** `Menu`; `Text` / `Listbox`; `Label` (incl. device `PhotoImage`); `_HoverTip`; **all of `dialogs.py`**.

**Out of scope for phase 1 (unchanged):** Podcasts IA (phase 2), button glyphs (phase 3), platform themes (phase 4).

**Done when:** Main window no longer reads as stacked MDI boxes; interactive controls no longer visibly flip between two toolkits in the same strip (or remaining exceptions are listed). ✅

---

## Phase 2 — §7.2 Information architecture / lists (O2 + O3 + P3/P4) — **DONE**

**Goal:** One list/browse language for “media in a tab,” and shallower Device navigation.

**O2 — Host Podcasts (shipped):**

- **P4-like toolbar:** `Show:` + `+` `−` `↻` · Sync Latest · More Episodes · status (same grammar as Playlists).
- **Master–detail (documented exception):** subscriptions `Treeview` + episodes `Treeview` (not a pure artist/album hierarchy like Music). This is the **only** intentional master–detail media tab.
- Subscriptions no longer use classic `Listbox`; iid `ps:{id}` selection in the controller.

**O3 — Device category strip (shipped):**

- Nested `ttk.Notebook` under Device **removed**.
- **On device:** readonly combobox (Music / Video / Audiobooks / Podcasts / Playlists) + one packed content frame.
- `device_notebook` remains a thin shim (`_DeviceSubviewNotebook`) so `.select()` / `.select(frame)` call sites keep working.

**P3 / P4:** Host and device Playlists toolbars stay twins. No fourth list widget style on the main window.

**O11 (shipped with phase 2):**

- `Thumb.Treeview` — album-art media trees (library Music/Video/Audiobooks, device Music/Video/Audiobooks).
- `Compact.Treeview` — playlists, podcast shows/episodes, device podcasts/playlists.
- Helpers in `mtpmanager/ui/chrome.py` (`STYLE_TREE_*`, `apply_chrome_baseline`).

**Done when:** Host Podcasts and Device browsing are either consistent with P3/P4 or explicitly documented as the single exception pattern. ✅

---

## Phase 3 — §7.3 Control grammar (O5–O7) — **DONE**

**Goal:** One vocabulary for primary actions, compact CRUD, transport, and continuous values.

| Outlier | Shipped |
|---------|---------|
| **O5** | ASCII `+` / `-` / `x`; move `Up`/`Dn`; `Refresh` text; constants in `ui/chrome.py` |
| **O6** | Time = hour/minute/AM·PM **comboboxes** (`time_of_day_row`); max-episodes = `ttk.Spinbox` |
| **O7** | Continuous values via **`ttk.Scale` only** (`make_ttk_scale` + snap); scrubber already ttk |

**Control grammar (authoritative):** docstring of `mtpmanager/ui/chrome.py` + [D19](./decisions.md).

**Done when:** A short “control grammar” note in this doc (or decisions.md) matches what the UI actually uses. ✅

---

## Phase 4a — §7.4 macOS (primary test environment)

**When:** After phases 1–3.

**Already good (preserve):**

- Native menubar; Control-click / Option reorder bindings
- `_HoverTip` Mac help style + explicit dark-safe tip colors
- Menlo monospace preference; transient modals

**Prioritize on macOS:**

| Item | Note |
|------|------|
| Flat chrome after O4 | Matches modern Aqua content windows more than sunken wells |
| `ttk` + Aqua where available | Reduces classic raised-button look |
| Dark-safe dialog prose | Kill fixed `#333`–`#666` (O12); tip colors already careful |
| Finder wording only on Darwin | O13 |
| Stable Mode copy (O9) | Short caption + dialog vs text wall in Device slot |
| Playing / transfer colors (O8) | Optional later; less urgent than chrome/controls |
| Search field affordance | Clear `×` already; optional polish only |

**Verify:** Homebrew Python 3.13 + Tk ([PLATFORMS.md](../PLATFORMS.md)); light and dark appearance if tip/dialog colors change.

---

## KDE (Plasma / Breeze) recommendations (phase 4b)

**When:** After 4a is stable; when the Linux test machine is updated with post–phase-1–3 code. **Do not block macOS work on this.**

True Breeze is a Qt theme. MtpManager remains **Tk**, so “blend” means *feel closer to Plasma defaults*, not pixel-match System Settings.

### Toolkit and theme

| Recommendation | Rationale |
|----------------|-----------|
| After O1, pick a Linux `ttk` theme deliberately (`clam` / `alt` / `default` — experiment) | Classic Motif-like buttons fight Breeze’s flat gray UI |
| Avoid inventing a full custom palette that ignores Plasma light/dark | Fixed hex transfer/playing colors may need relative luminance or config later |
| Do **not** require Qt rewrite for “Breeze support” | Out of scope; document residual mismatch honestly |

### Chrome and layout

| Recommendation | Rationale |
|----------------|-----------|
| Prefer flat frames / hairlines from phase 1 over stacked sunken wells | Plasma content areas are subtle, not nested 3D boxes |
| Keep header-like toolbars sparse; use menus for power features | Matches many KDE apps; Config menu already holds experimental density |
| If Device nesting remains, ensure inner tabs are visually secondary | Plasma prefers shallow navigation |

### Icons and FreeDesktop

| Recommendation | Rationale |
|----------------|-----------|
| Replace improvised Unicode tool glyphs with a small **symbolic icon set** (or FreeDesktop-named PNGs) where phase 3 still needs compact actions | Breeze users expect symbolic icons; `↻`/`−` vary by font |
| Optional: map “Reveal” to “Open containing folder” / Dolphin via existing `_reveal_path_in_os` | Wording only; open path already uses `xdg-open`-style behavior |

### Typography and colors

| Recommendation | Rationale |
|----------------|-----------|
| Prefer system UI font; monospace via `TkFixedFont` or common Linux mono (not Menlo-first) | Menlo is macOS-centric |
| Semantic row colors (transfer / playing / video) should remain legible on Breeze dark | Revisit O8/O12 on a real Plasma session |
| Status line + Cancel already OK | Familiar job progress pattern |

### Dialogs and prefs

| Recommendation | Rationale |
|----------------|-----------|
| Long scroll forms (Podcast Settings) could later split into list + page (System Settings style) | Optional; not required for “blend” |
| Keep destructive actions clearly labeled | Plasma uses distinct destructive styling; Tk can at least use clear wording and confirm dialogs (already common) |

### Input

| Recommendation | Rationale |
|----------------|-----------|
| Keep Button-3 context menus; Alt+arrows for playlist reorder | Already aligned |
| Avoid macOS-only menu strings on Linux (Finder, ⌥ in labels if shown) | Platform strings |

### Explicit non-goals (KDE)

- Embedding as a Plasma applet / Kirigami UI  
- Full Breeze QStyle via Qt  
- Global menu widget integration beyond normal menubar  

---

## GNOME (Adwaita) recommendations (phase 4c)

**When:** Same as 4b — Linux machine with updated tree; after 4a. Independent of KDE work order (either order is fine).

libadwaita header bars and CSS are **not** available in stock Tk. Blend is HIG-ish layout and wording, not a GTK port.

### Toolkit and theme

| Recommendation | Rationale |
|----------------|-----------|
| Flat content + fewer raised classic buttons (phases 1 + 3) | Adwaita is flat; Motif relief looks alien on Fedora/Ubuntu GNOME |
| `ttk` defaults on Linux still imperfect vs Adwaita | Accept residual mismatch; document it |
| Prefer larger padding (12–18px regions) if tightening chrome after O4 | Adwaita spacing is looser than current 2–8px pads |

### Navigation

| Recommendation | Rationale |
|----------------|-----------|
| Shallow navigation after O3 | Nested notebooks fight GNOME’s few-levels preference |
| Left Selection + Device as a **sidebar** tint (not sunken well) | Closer to Adwaita sidebars |
| Primary actions in menus are acceptable | GNOME primary menu pattern; optional later header strip is not required |

### Controls and semantics

| Recommendation | Rationale |
|----------------|-----------|
| Destructive actions (Delete All, remove library root) stay confirm-heavy | Already messagebox-heavy; optional distinct button labeling |
| Suggested/default actions: one clear primary per dialog (P6) | Aligns with Adwaita suggested-action concept without CSS |
| Time/schedule controls simplified in phase 3 | Custom AM/PM spinners feel non-GNOME |

### Wording and files

| Recommendation | Rationale |
|----------------|-----------|
| “Show in Files” / “Open containing folder” instead of Finder | O13 |
| Escape/close patterns already via dialogs | Keep `transient` + grab |

### Colors and dark style

| Recommendation | Rationale |
|----------------|-----------|
| No hard-coded light-only gray prose (O12) | Adwaita dark is common |
| Playing purple is non-Adwaita accent — optional later map to a single accent | O8 |
| Tree selection should remain readable in dark | Test on real GNOME session |

### Explicit non-goals (GNOME)

- libadwaita / GTK4 rewrite  
- Client-side decorations / CSD header bar parity  
- GNOME Software packaging requirements beyond normal desktop file (if any later)  

---

## Shared Linux notes (4b + 4c)

These apply once the Linux machine is updated; they do not require choosing KDE *or* GNOME first.

1. **Phases 1–3 land first** — Linux polish without them rewrites the same chrome twice.  
2. **Platform strings** — Finder / Files / folder; Option vs Alt in user-visible shortcuts.  
3. **Monospace** — Prefer portable font fallbacks over Menlo-only.  
4. **Hard-coded light hex** — Remove or theme before claiming dark desktop support.  
5. **Tk ceiling** — Neither Breeze nor Adwaita will match natively; goal is “does not look like a foreign Motif toy,” not “indistinguishable from Dolphin/Nautilus.”  
6. **Test matrix (later):** Plasma light/dark + GNOME light/dark; one HiDPI scale; default font install.

---

## Deferred items (not phases 1–3)

Track here so they are not forgotten when 4a–4c run:

| ID | Topic | Earliest sensible phase |
|----|--------|-------------------------|
| O8 | Semantic color system / legend | 4a or later |
| O9 | Stable Mode help placement | 4a |
| O10 | Selection `Text` vs label styling | with 1 or 4a |
| O12 | Dialog secondary text colors | 4a (macOS), verify 4b/4c |
| O13 | Reveal wording by OS | 4a partial; finish 4b/4c |
| O14 | Config menu density / Experimental submenu | optional anytime |
| O15 | Unify `simpledialog` vs custom prompt | with 3 or polish |
| O16 | Stale `splash` bytecode without source | cleanup anytime |

---

## Change surfaces (when implementing)

| Task | Where |
|------|--------|
| Main layout, menus, trees, toolbars, tips | `mtpmanager/ui/window.py` |
| Modals, spinners, encode/podcast prefs chrome | `mtpmanager/ui/dialogs.py` |
| Context wiring / reveal paths | `mtpmanager/ui/controllers.py` |
| Album thumb size / rowheight coupling | `window.py` + `infra/album_art.py` (`DEFAULT_THUMB_SIZE`) |
| Device art | `assets/devices/`, `infra/device_assets.py` |

Do **not** fold visual chrome into `domain/` or transport paths. Prefer durable notes here over inventing a theming framework the tree does not have—unless phase 1 explicitly introduces a small style helper.

---

## History

| Date | Note |
|------|------|
| 2026-08-08 | Inventory captured; phases 1→2→3→4a (macOS)→4b/4c (KDE/GNOME later) agreed. KDE/GNOME recommendations documented for deferred Linux testing. |
| 2026-08-08 | **Phase 1 shipped:** flat main frames + separators; ttk main interactive stack; D17; `ui/chrome.py`. Dialogs deferred. |
| 2026-08-08 | **Phase 2 shipped:** Podcasts P4 toolbar + show Treeview master–detail; Device combobox strip (no nested notebook); O11 Thumb/Compact Treeview styles. |
| 2026-08-08 | **Phase 3 shipped:** control grammar (ASCII glyphs, Up/Dn, Refresh); time comboboxes; ttk.Scale only; D19. |
