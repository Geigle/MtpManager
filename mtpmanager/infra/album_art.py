"""Extract and cache album cover thumbnails (embedded tags or folder sidecars).

Disk cache under the app data dir stores small PNGs so the UI thread only
loads ``PhotoImage(file=…)`` (no mutagen/Pillow on the main thread).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path

from mtpmanager.infra.app_paths import default_data_dir

logger = logging.getLogger(__name__)

# Display size; rowheight should be slightly larger so thumbs are not cropped.
DEFAULT_THUMB_SIZE = 48
_CACHE_VERSION = 2

_SIDECAR_NAMES = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "AlbumArt.jpg",
    "album.jpg",
    "front.jpg",
    "Front.jpg",
)


def art_cache_dir(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / "album_art_cache"


def thumb_cache_key(track_path: str, *, size: int = DEFAULT_THUMB_SIZE) -> str:
    """Stable key for a track path + mtime/size + thumb size."""
    try:
        st = os.stat(track_path)
        stamp = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        stamp = "missing"
    raw = f"v{_CACHE_VERSION}|{size}|{track_path}|{stamp}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:40]


def cached_thumb_path(
    track_path: str,
    *,
    size: int = DEFAULT_THUMB_SIZE,
    data_dir: Path | None = None,
) -> Path:
    return art_cache_dir(data_dir=data_dir) / f"{thumb_cache_key(track_path, size=size)}.png"


def cached_thumb_exists(
    track_path: str,
    *,
    size: int = DEFAULT_THUMB_SIZE,
    data_dir: Path | None = None,
) -> Path | None:
    """Return path to on-disk PNG thumb if present and non-empty."""
    dest = cached_thumb_path(track_path, size=size, data_dir=data_dir)
    try:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
    except OSError:
        return None
    return None


def _bytes_from_sidecar(track_path: str) -> bytes | None:
    directory = os.path.dirname(track_path) or "."
    try:
        names = {n.casefold(): n for n in os.listdir(directory)}
    except OSError:
        return None
    for wanted in _SIDECAR_NAMES:
        actual = names.get(wanted.casefold())
        if not actual:
            continue
        full = os.path.join(directory, actual)
        if not os.path.isfile(full):
            continue
        try:
            return Path(full).read_bytes()
        except OSError as e:
            logger.debug("Cannot read cover sidecar %s: %s", full, e)
    return None


def _bytes_from_mutagen(track_path: str) -> bytes | None:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return None
    try:
        audio = MutagenFile(track_path)
    except Exception as e:
        logger.debug("mutagen open failed for art %s: %s", track_path, e)
        return None
    if audio is None:
        return None

    pictures = getattr(audio, "pictures", None)
    if pictures:
        ordered = sorted(
            pictures,
            key=lambda p: (0 if getattr(p, "type", None) == 3 else 1),
        )
        data = getattr(ordered[0], "data", None)
        if data:
            return bytes(data)

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    try:
        apics = []
        for key in tags.keys():
            key_s = str(key)
            if key_s.startswith("APIC") or key_s == "APIC":
                frame = tags[key]
                if isinstance(frame, list):
                    apics.extend(frame)
                else:
                    apics.append(frame)
        if apics:
            ordered = sorted(
                apics,
                key=lambda f: (0 if getattr(f, "type", None) == 3 else 1),
            )
            data = getattr(ordered[0], "data", None)
            if data:
                return bytes(data)
    except Exception:
        logger.debug("APIC scan failed for %s", track_path, exc_info=True)

    try:
        if "WM/Picture" in tags:
            pic = tags["WM/Picture"]
            if isinstance(pic, list):
                pic = pic[0]
            value = getattr(pic, "value", pic)
            if isinstance(value, (bytes, bytearray)) and len(value) > 16:
                raw = bytes(value)
                for magic in (b"\xff\xd8\xff", b"\x89PNG"):
                    idx = raw.find(magic)
                    if idx >= 0:
                        return raw[idx:]
                return raw
    except Exception:
        logger.debug("WM/Picture scan failed for %s", track_path, exc_info=True)

    return None


def load_cover_bytes(track_path: str) -> bytes | None:
    """Return raw image bytes for a track's album art, or None."""
    if not track_path or not os.path.isfile(track_path):
        return None
    data = _bytes_from_mutagen(track_path)
    if data:
        return data
    return _bytes_from_sidecar(track_path)


def ensure_cached_thumb(
    track_path: str,
    *,
    size: int = DEFAULT_THUMB_SIZE,
    data_dir: Path | None = None,
) -> Path | None:
    """Extract/resize cover to a cached PNG if needed; return path or None.

    Safe to call from a worker thread (no Tk).
    """
    existing = cached_thumb_exists(track_path, size=size, data_dir=data_dir)
    if existing is not None:
        return existing

    data = load_cover_bytes(track_path)
    if not data:
        return None

    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; album art cache disabled")
        return None

    dest = cached_thumb_path(track_path, size=size, data_dir=data_dir)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGBA")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        # Canvas exact size so Treeview row layout is predictable.
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        x = (size - im.width) // 2
        y = (size - im.height) // 2
        canvas.paste(im, (x, y), im)
        tmp = dest.with_suffix(".tmp.png")
        canvas.save(tmp, format="PNG", optimize=True)
        os.replace(tmp, dest)
        return dest
    except Exception as e:
        logger.debug("ensure_cached_thumb failed for %s: %s", track_path, e)
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def warm_album_thumbs(
    track_paths: list[str],
    *,
    size: int = DEFAULT_THUMB_SIZE,
    data_dir: Path | None = None,
) -> int:
    """Ensure thumbs for unique paths; return count written or already cached."""
    done = 0
    seen: set[str] = set()
    for path in track_paths:
        if not path or path in seen:
            continue
        seen.add(path)
        if ensure_cached_thumb(path, size=size, data_dir=data_dir) is not None:
            done += 1
    return done


def photoimage_from_cache_file(path: Path | str, *, master=None):
    """Load a cached PNG as Tk PhotoImage (main thread). No Pillow required."""
    from tkinter import PhotoImage

    p = Path(path)
    if not p.is_file():
        return None
    try:
        if master is not None:
            return PhotoImage(file=str(p), master=master)
        return PhotoImage(file=str(p))
    except Exception as e:
        logger.debug("PhotoImage load failed %s: %s", p, e)
        return None
