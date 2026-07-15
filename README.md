# MtpManager
I want to use an old MP3 player but MTP is a lovecraftian horror. This work-in-progress helps me load *most* of my music. Random tracks won't transfer for reasons unknown.

## Running

```bash
./MtpManager.sh
```

Or: `.venv/bin/python -m mtpmanager` / `.venv/bin/python mm.py`

## Layout

```
mtpmanager/
  domain/     # Track, Library (no I/O frameworks)
  ports/      # Protocols (Transport, Device, tags, transcoder)
  app/        # scan_library, transfer pipeline, device_ops
  infra/      # pymtp, mtp-sendtr, mutagen, ffmpeg
  ui/         # Tk window + controllers only
```

Dependency direction: `ui → app → domain/ports ← infra`.

## Logs

Diagnostics are written under a platform log directory (not next to your music library):

| Platform | Default path |
|----------|----------------|
| macOS | `~/Library/Logs/MtpManager` |
| Linux | `~/.local/share/mtpmanager/logs` (or `$XDG_STATE_HOME/mtpmanager/logs`) |

| File | Contents |
|------|----------|
| `mtpmanager.log` | Full app detail (DEBUG+), size-rotated |
| `errors.log` | ERROR+ only (exceptions, fatal transfer aborts) |
| `transfer-YYYYMMDD-HHMMSS.log` | One file per transfer batch (progress, CMD, mtp-sendtr) |

Console defaults to **INFO** (readable). Files stay at **DEBUG**.

| Env var | Effect |
|---------|--------|
| `MTP_MANAGER_LOG_DIR` | Override log directory |
| `MTP_MANAGER_DEBUG=1` | Console also at DEBUG |
| `MTP_MANAGER_LOG_MAX_AGE_DAYS` | Delete logs older than N days (default **14**) |

Stale logs are pruned on every startup.

## Platform setup

macOS and Linux have different Python/Tkinter/libmtp requirements. See **[PLATFORMS.md](PLATFORMS.md)** before setting up on a new machine — especially on **macOS 26+**, where the system Python's Tkinter will crash and pymtp cannot find libmtp without the project wrapper.
