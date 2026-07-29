# MtpManager

**MtpManager** is a small Tk desktop app for loading media onto picky MTP players—especially the **Creative ZEN Vision:M**—without fighting broken “media manager” stacks. It is still oriented around **durable send contracts** and dual transfer modes, not a full streaming library suite.

## Background
I want to use an old Zen Vision:M, but it's MTP-only, which is a lovecraftian horror. Windows Media Player doesn't convert from FLAC, MTP support for old players is completely broken in all common apps, and older versions of WMP fail a DRM check for music I legally purchased. Gnomad2 almost did the trick, but it crashes too much, and I didn't spend my time fixing it because by the time that thought occurred to me, I was too deep into writing this Python tool.

## What it does

| Area | Capabilities |
|------|----------------|
| **Library** | Multi-root host library (SQLite index + stable track GUIDs); Music, Video, Audiobooks tabs; progressive tree load; prefer highest-fidelity encoding when the same track exists as FLAC/MP3/etc. |
| **Transfer** | FLAC/ALAC/WAV/… → device format via **ffmpeg**; dual modes: **Stable** (`mtp-sendtr` subprocess) and **Experimental** (in-process PyMTP); skip-if-present by GUID; batch progress, cancel, resume |
| **Device** | Connect / inventory cache; Music / Video / Audiobooks views; pull to library or any folder; optional embedded-tag recovery when MTP tags are empty; Send Video; folder layout discovered by name (Music, ZENcast, …) |
| **Playlists** | Host M3U playlists in the index; Playlists tab; sync tracks and recreate playlists on-device (Experimental) |
| **Podcasts** | RSS subscribe, episode list (video episodes highlighted), download, sync to ZENcast (audio by default; optional video sync with XviD on ZEN Vision:M), local playback |
| **Playback** | Local listen via **ffplay** (library tracks, albums/artists, playlists, podcast episodes) with bottom-bar controls |
| **Selection** | Left panel shows scrollable context for the current library / device / podcast selection |

It is **not** a multi-device auto-discovery suite, a general-purpose media organizer, or a reimplementation of libmtp.

## Running

```bash
./MtpManager.sh
```

Or: `.venv/bin/python -m mtpmanager` / `.venv/bin/python mm.py`

Install deps into the venv once:

```bash
.venv/bin/pip install -r requirements.txt
```

**Playback** needs `ffplay` on `PATH` (comes with **ffmpeg**). **Stable Mode** needs `mtp-sendtr` (libmtp tools). See [PLATFORMS.md](PLATFORMS.md).

## Layout

```
mtpmanager/
  domain/     # models, library rules, folder roles, M3U helpers (no Tk)
  ports/      # Transport / Device protocols
  app/        # scan, transfer, device/podcast/playlist ops
  infra/      # pymtp wrapper, mtp-sendtr, SQLite indexes, mutagen, ffmpeg, RSS
  ui/         # Tk window + controllers only
```

Dependency direction: `ui → app → domain/ports ← infra`.

## Documentation

| Doc | Contents |
|-----|----------|
| **[docs/README.md](docs/README.md)** | Documentation map and reading order |
| [docs/architecture.md](docs/architecture.md) | Layers, packages, dual-mode composition |
| [docs/device-contract.md](docs/device-contract.md) | MTP/ZEN remote path, storage, filename rules |
| [docs/decisions.md](docs/decisions.md) | Why dual mode, no silent fallback, fatal batch abort, … |
| [docs/transfer-and-modes.md](docs/transfer-and-modes.md) | Transfer pipeline; Stable vs Experimental |
| [docs/debrief-*.md](docs/) | Incident narratives (CMD finalize, PyMTP layers) |
| [AGENTS.md](AGENTS.md) | Short invariants and change surfaces for AI agents |
| [PLATFORMS.md](PLATFORMS.md) | macOS / Linux setup traps |

## Logs

Diagnostics live under a platform log directory (not next to your music library):

| Platform | Default path |
|----------|----------------|
| macOS | `~/Library/Logs/MtpManager` |
| Linux | `~/.local/share/mtpmanager/logs` (or `$XDG_STATE_HOME/mtpmanager/logs`) |

| File | Contents |
|------|----------|
| `mtpmanager.log` | Full app detail (DEBUG+), size-rotated |
| `errors.log` | ERROR+ only (exceptions, fatal transfer aborts) |
| `transfer-YYYYMMDD-HHMMSS.log` | One file per transfer batch (progress, CMD, mtp-sendtr) |

Console defaults to **INFO**. Files stay at **DEBUG**.

| Env var | Effect |
|---------|--------|
| `MTP_MANAGER_LOG_DIR` | Override log directory |
| `MTP_MANAGER_DEBUG=1` | Console also at DEBUG |
| `MTP_MANAGER_LOG_MAX_AGE_DAYS` | Delete logs older than N days (default **14**) |
| `MTP_MANAGER_DATA_DIR` | Override app data dir (library index, config, podcast cache) |

Stale logs are pruned on every startup. App data (SQLite library index, `config.json`, podcast downloads) defaults to Application Support / XDG data, not the repo tree.

## Platform setup

macOS and Linux have different Python/Tkinter/libmtp requirements. See **[PLATFORMS.md](PLATFORMS.md)** before setting up on a new machine — especially on **macOS 26+**, where the system Python's Tkinter will crash and pymtp cannot find libmtp without the project wrapper.

## License

MtpManager is free software: you can redistribute it and/or modify it under the terms of the **[GNU General Public License](LICENSE)** as published by the Free Software Foundation, either **version 3** of the License, or (at your option) any later version.

This project depends on **[PyMTP](https://pypi.org/project/PyMTP/)** (GPL-3) for experimental in-process libmtp access, and on **libmtp** (LGPL) via system libraries and tools such as `mtp-sendtr`. Redistribution of MtpManager must comply with the GPL.
