# Platform setup: macOS vs Linux

MtpManager runs on both platforms, but macOS needs extra care around Python, Tkinter, and libmtp. This document captures the gotchas so you do not have to rediscover them.

## Quick start

| | Linux | macOS |
|---|---|---|
| Python | 3.9+ from your distro | **3.13 via Homebrew** (not CLT Python 3.9) |
| Tkinter | `python3-tk` package | `python-tk@3.13` Homebrew formula |
| libmtp | `libmtp` / `libmtp-dev` package | `brew install libmtp` |
| Create venv | `python3 -m venv .venv` | `/opt/homebrew/bin/python3.13 -m venv .venv` |
| Run | `./MtpManager.sh` | `./MtpManager.sh` |

---

## Linux

This is the reference platform the project was originally developed on. Setup is straightforward.

### Requirements

```bash
# Debian / Ubuntu
sudo apt install python3 python3-venv python3-tk libmtp-dev libmtp-runtime ffmpeg

# Fedora
sudo dnf install python3 python3-tkinter libmtp ffmpeg
```

### Virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./MtpManager.sh
```

### Notes

- **libmtp**: pymtp loads `libmtp.so` via `ctypes.util.find_library("mtp")`, which works on Linux through `ldconfig`. No wrapper changes are needed.
- **Tkinter**: Install the distro `python3-tk` (or equivalent) package. Without it, `import tkinter` fails immediately with a clear error.
- **`pymtp_wrapper.py`**: Imports pymtp normally on Linux. The macOS-only patch is skipped when `sys.platform != "darwin"`.

---

## macOS

macOS has two separate compatibility issues that do **not** appear on Linux.

### 1. libmtp is not found by pymtp (import crash)

**Symptom:**

```
AttributeError: dlsym(RTLD_DEFAULT, LIBMTP_Detect_Raw_Devices): symbol not found
```

**Cause:** `ctypes.util.find_library("mtp")` returns `None` on macOS. pymtp then fails while binding libmtp symbols at import time. This is not device detection running early — it is library discovery failing.

**Fix:** The project includes `pymtp_wrapper.py`, which patches `find_library` on macOS to check Homebrew paths before pymtp loads. Always import pymtp through the wrapper:

```python
import pymtp_wrapper as pymtp
```

**Also required:**

```bash
brew install libmtp
```

### 2. Tkinter abort on macOS 26+ (Tahoe)

**Symptom:**

```
macOS 26 (2603) or later required, have instead 16 (1603) !
zsh: abort      ./MtpManager.sh
```

**Cause:** Apple's Command Line Tools Python 3.9 ships an old `_tkinter` extension (built with a pre-26 SDK). On macOS 26+, system Tcl/Tk requires the real OS version, but old binaries see a compatibility version of **16.x** instead of **26.x**. The crash happens at `root = Tk()` in `mm.py`, not during pymtp import.

**Fix:** Use Homebrew Python 3.13 with its tkinter support, and build the venv from that interpreter:

```bash
brew install python@3.13 python-tk@3.13 libmtp
rm -rf .venv
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
./MtpManager.sh
```

**Do not use** `/usr/bin/python3` or CLT `python3` for this project on macOS 26+. They share the same broken tkinter.

### Verifying a macOS setup

```bash
# Should print 3.13.x and a path under /opt/homebrew/
.venv/bin/python --version
.venv/bin/python -c "import sys; print(sys.executable)"

# Should print "tk ok" with no abort
.venv/bin/python -c "from tkinter import Tk; root=Tk(); print('tk ok'); root.destroy()"

# Should print "pymtp ok" with no AttributeError
.venv/bin/python -c "import pymtp_wrapper as pymtp; print('pymtp ok')"
```

### Recreating `.venv` on macOS

If you delete `.venv`, recreate it with **Homebrew Python 3.13**, not whatever `python3` happens to be on your `PATH`:

```bash
rm -rf .venv
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`MtpManager.sh` will prefer Homebrew `python3.13` when creating a new venv on macOS, but an existing `.venv` is never replaced automatically — delete it manually if you need to rebuild.

---

## Troubleshooting

| Error | Platform | Likely cause | Fix |
|---|---|---|---|
| `LIBMTP_Detect_Raw_Devices: symbol not found` | macOS | libmtp not installed, or pymtp imported directly | `brew install libmtp`; use `pymtp_wrapper` |
| `macOS 26 ... have instead 16` | macOS 26+ | CLT / old Python tkinter | Recreate venv with Homebrew `python3.13` + `python-tk@3.13` |
| `No module named '_tkinter'` | either | tkinter not installed for that Python | Linux: `python3-tk`; macOS: `brew install python-tk@3.13` |
| `import pymtp` works but no devices | either | USB permissions / cable / device mode | Check `mtp-detect`; Linux may need udev rules |

---

## Project files involved

| File | Role |
|---|---|
| `mtpmanager/infra/pymtp_wrapper.py` | macOS libmtp path fix; transparent pass-through on Linux |
| `pymtp_wrapper.py` | Compatibility re-export of the above |
| `MtpManager.sh` | Runs `.venv/bin/python mm.py`; creates venv if missing |
| `mm.py` | Thin launcher → `mtpmanager` package |
| `mtpmanager/` | App package (`domain`, `app`, `infra`, `ui`, `ports`) |