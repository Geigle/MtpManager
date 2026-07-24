"""Retail/demo package: zip Creative-looking export tracks + reduced restore map.

Workflow
--------
1. **Get Tracks from Device** writes a full ``device_media_map.json``.
2. **Package Retail Demos** filters entries with
   ``flags.looks_like_retail_demo`` (and a present host file), copies only those
   files into a zip with a **reduced** ``restore_map.json`` for iFlash restore.
3. **Restore Retail Package** extracts that zip and sends each entry via
   Transport with original basenames (no GUID ObjectFileName) and
   ``desired_tags`` as MTP metadata.

Edit ``desired_tags`` / ``flags.include_in_restore`` in either the full export
map (before packaging) or the reduced map (inside the zip / after unpack).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mtpmanager.domain.models import TrackMetadata
from mtpmanager.infra.device_export_map import MAP_JSON_NAME, load_export_map
from mtpmanager.infra.remote_naming import (
    MAX_REMOTE_BASENAME,
    sanitize_component,
)

logger = logging.getLogger(__name__)

PACKAGE_SCHEMA_VERSION = 1
PACKAGE_DOCUMENT_TYPE = "mtpmanager_retail_restore_package"
RESTORE_MAP_NAME = "restore_map.json"
MEDIA_DIR_NAME = "media"
DEFAULT_ZIP_NAME = "creative_retail_demos.zip"

_UNSAFE_FILE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_package_basename(name: str, index: int, ext: str) -> str:
    """Unique media path segment under ``media/`` inside the zip."""
    base = _UNSAFE_FILE.sub("_", (name or "").strip()) or f"item_{index}"
    base = base.strip(" .") or f"item_{index}"
    # Drop existing extension from display name before re-adding *ext*.
    stem, old_ext = os.path.splitext(base)
    if old_ext and not ext:
        ext = old_ext
    if not stem:
        stem = f"item_{index}"
    if len(stem) > 80:
        stem = stem[:80].rstrip(" .")
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""
    return f"{index:03d}_{stem}{ext}"


def sanitize_remote_basename(filename: str, *, ext_fallback: str = ".mp3") -> str:
    """Short ObjectFileName for restore (ZEN basename hygiene)."""
    raw = (filename or "").strip() or "track"
    stem, ext = os.path.splitext(raw)
    if not ext:
        ext = ext_fallback if ext_fallback.startswith(".") else f".{ext_fallback}"
    body_max = max(8, MAX_REMOTE_BASENAME - len(ext))
    stem = sanitize_component(stem, body_max)
    return f"{stem}{ext}"


def is_retail_candidate(entry: dict[str, Any]) -> bool:
    """True when the full export map marks this as Creative-like retail/demo."""
    flags = entry.get("flags") or {}
    if not flags.get("looks_like_retail_demo"):
        return False
    if entry.get("status") not in (None, "", "ok"):
        # Allow status missing (hand-edited maps) but skip explicit failures.
        if entry.get("status") == "failed":
            return False
    return True


def resolve_host_file(
    entry: dict[str, Any],
    export_dir: str | Path,
) -> Path | None:
    """Locate the downloaded file for a full-export map entry."""
    host = entry.get("host") or {}
    export = Path(export_dir).resolve()
    candidates: list[Path] = []
    rel = (host.get("relative_path") or "").strip()
    if rel:
        candidates.append(export / rel)
    abs_path = (host.get("path") or "").strip()
    if abs_path:
        candidates.append(Path(abs_path))
    base = (host.get("basename") or "").strip()
    if base:
        candidates.append(export / base)
    obj = entry.get("device_object") or {}
    fname = (obj.get("filename") or "").strip()
    if fname:
        candidates.append(export / fname)

    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p
    return None


def select_retail_entries(
    export_doc: dict[str, Any],
    export_dir: str | Path,
) -> list[tuple[dict[str, Any], Path]]:
    """Return (entry, host_path) pairs for Creative-looking downloads that exist."""
    out: list[tuple[dict[str, Any], Path]] = []
    for entry in export_doc.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if not is_retail_candidate(entry):
            continue
        path = resolve_host_file(entry, export_dir)
        if path is None:
            logger.warning(
                "Retail package skip (file missing) item_id=%s host=%s",
                entry.get("item_id"),
                (entry.get("host") or {}).get("path"),
            )
            continue
        out.append((entry, path))
    return out


def _reduced_desired_tags(entry: dict[str, Any]) -> dict[str, Any]:
    tags = entry.get("desired_tags") or entry.get("device_tags") or {}
    if not isinstance(tags, dict):
        return {}
    # Keep the full tag dict (duration_ms, sample_rate, …) for restore fidelity.
    return dict(tags)


def _reduced_device_object(entry: dict[str, Any]) -> dict[str, Any]:
    obj = entry.get("device_object") or {}
    if not isinstance(obj, dict):
        return {}
    keys = (
        "filename",
        "parent_id",
        "storage_id",
        "storage_id_hex",
        "filesize",
        "filesize_host",
        "filetype",
        "filetype_label",
        "modificationdate",
        "modificationdate_iso",
    )
    return {k: obj[k] for k in keys if k in obj}


def build_reduced_entry(
    *,
    index: int,
    source_entry: dict[str, Any],
    package_path: str,
    remote_basename: str,
) -> dict[str, Any]:
    flags = source_entry.get("flags") or {}
    return {
        "index": int(index),
        "package_path": package_path,
        "remote_basename": remote_basename,
        "source_item_id": int(source_entry.get("item_id") or 0),
        "desired_tags": _reduced_desired_tags(source_entry),
        "device_object": _reduced_device_object(source_entry),
        "flags": {
            "looks_like_retail_demo": True,
            "include_in_restore": bool(
                flags.get("include_in_restore", True)
            ),
            "tags_missing": bool(flags.get("tags_missing")),
            "needs_manual_tag_edit": bool(flags.get("needs_manual_tag_edit")),
        },
        "editor_notes": source_entry.get("editor_notes") or "",
    }


def build_package_document(
    *,
    entries: list[dict[str, Any]],
    source_export: dict[str, Any] | None = None,
    total_bytes: int = 0,
) -> dict[str, Any]:
    src = source_export or {}
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "document_type": PACKAGE_DOCUMENT_TYPE,
        "purpose": (
            "Reduced retail/demo package for restoring Creative stock content "
            "to iFlash-upgraded (or wiped) players. Only files that looked like "
            "retail demos are included. Edit desired_tags and "
            "flags.include_in_restore before restore if needed."
        ),
        "how_to_edit": (
            "1) Set flags.include_in_restore false to skip an entry on restore.\n"
            "2) Fix desired_tags (title/artist/album/genre/duration_ms/…).\n"
            "3) remote_basename is the ObjectFileName used on send (short, "
            "sanitized); edit carefully — device name limits apply.\n"
            "4) package_path is the path inside the zip under media/.\n"
            "5) Re-zip after edits, or restore from an unpacked folder that "
            "still contains restore_map.json + media/."
        ),
        "created_at": _utc_now(),
        "source_export": {
            "exported_at": src.get("exported_at") or "",
            "export_dir": src.get("export_dir") or "",
            "export_label": src.get("export_label") or "",
            "device": dict(src.get("device") or {}),
        },
        "summary": {
            "entry_count": len(entries),
            "total_bytes": int(total_bytes),
            "include_in_restore_count": sum(
                1
                for e in entries
                if (e.get("flags") or {}).get("include_in_restore", True)
            ),
        },
        "notes": (
            "Global notes for this retail package (source player, iFlash target, "
            "video experiments, …)."
        ),
        "entries": entries,
    }


@dataclass(frozen=True)
class PackageRetailResult:
    zip_path: str
    map_path: str  # path written inside staging / also in zip as RESTORE_MAP_NAME
    entry_count: int
    total_bytes: int
    skipped_missing: int = 0
    document: dict[str, Any] = field(default_factory=dict)


def package_retail_export(
    export_path: str | Path,
    zip_path: str | Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> PackageRetailResult:
    """Build a zip of Creative-looking files + reduced restore map.

    *export_path* is a directory containing ``device_media_map.json``, or a path
    to that JSON file.
    """
    export_path = Path(export_path)
    if export_path.is_file():
        export_dir = export_path.parent
        doc = load_export_map(export_path)
    else:
        export_dir = export_path
        doc = load_export_map(export_dir)
    if doc is None:
        raise FileNotFoundError(
            f"No {MAP_JSON_NAME} found at {export_path}. "
            "Run Get Tracks from Device first (or point at that export folder)."
        )

    selected = select_retail_entries(doc, export_dir)
    if not selected:
        raise ValueError(
            "No Creative/retail-looking tracks with host files found in this "
            "export map. Check flags.looks_like_retail_demo and that media "
            "files are still next to the map."
        )

    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    reduced_entries: list[dict[str, Any]] = []
    total_bytes = 0
    # Stage then zip so restore_map.json is easy to inspect if needed.
    with tempfile.TemporaryDirectory(prefix="mtpmanager-retail-pkg-") as tmp:
        stage = Path(tmp)
        media_dir = stage / MEDIA_DIR_NAME
        media_dir.mkdir(parents=True, exist_ok=True)
        n = len(selected)
        for i, (entry, host) in enumerate(selected, start=1):
            if on_progress is not None:
                try:
                    on_progress(i - 1, n, host.name)
                except Exception:
                    pass
            obj = entry.get("device_object") or {}
            original_name = (
                (obj.get("filename") or "").strip()
                or host.name
                or f"track_{i}"
            )
            _, ext = os.path.splitext(host.name)
            if not ext:
                _, ext = os.path.splitext(original_name)
            pkg_name = _safe_package_basename(original_name, i, ext or ".mp3")
            package_rel = f"{MEDIA_DIR_NAME}/{pkg_name}"
            dest = media_dir / pkg_name
            shutil.copy2(host, dest)
            try:
                total_bytes += int(dest.stat().st_size)
            except OSError:
                pass
            remote = sanitize_remote_basename(
                original_name, ext_fallback=ext or ".mp3"
            )
            reduced_entries.append(
                build_reduced_entry(
                    index=i,
                    source_entry=entry,
                    package_path=package_rel.replace("\\", "/"),
                    remote_basename=remote,
                )
            )

        package_doc = build_package_document(
            entries=reduced_entries,
            source_export=doc,
            total_bytes=total_bytes,
        )
        map_file = stage / RESTORE_MAP_NAME
        map_file.write_text(
            json.dumps(package_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.write(map_file, arcname=RESTORE_MAP_NAME)
            for path in sorted(media_dir.iterdir()):
                if path.is_file():
                    zf.write(path, arcname=f"{MEDIA_DIR_NAME}/{path.name}")

        if on_progress is not None:
            try:
                on_progress(n, n, "done")
            except Exception:
                pass

        logger.info(
            "Retail package written zip=%s entries=%d bytes=%d",
            zip_path,
            len(reduced_entries),
            total_bytes,
        )
        return PackageRetailResult(
            zip_path=str(zip_path.resolve()),
            map_path=RESTORE_MAP_NAME,
            entry_count=len(reduced_entries),
            total_bytes=total_bytes,
            document=package_doc,
        )


def load_package_map(path: str | Path) -> dict[str, Any] | None:
    """Load restore_map.json from a zip file or an unpacked package directory."""
    src = Path(path)
    try:
        if src.is_dir():
            map_path = src / RESTORE_MAP_NAME
            if not map_path.is_file():
                return None
            raw = json.loads(map_path.read_text(encoding="utf-8"))
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(src, "r") as zf:
                try:
                    data = zf.read(RESTORE_MAP_NAME)
                except KeyError:
                    # Tolerate nested single-folder zip
                    names = [
                        n
                        for n in zf.namelist()
                        if n.endswith(RESTORE_MAP_NAME) and not n.endswith("/")
                    ]
                    if not names:
                        return None
                    data = zf.read(names[0])
                raw = json.loads(data.decode("utf-8"))
        elif src.is_file() and src.name == RESTORE_MAP_NAME:
            raw = json.loads(src.read_text(encoding="utf-8"))
        else:
            return None
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as e:
        logger.warning("Cannot read retail package map %s: %s", src, e)
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("document_type") not in (
        PACKAGE_DOCUMENT_TYPE,
        None,
        "",
    ):
        # Accept missing type for hand-edited maps; warn if clearly wrong.
        if raw.get("document_type") and "retail" not in str(
            raw.get("document_type")
        ):
            logger.warning(
                "Unexpected package document_type=%r in %s",
                raw.get("document_type"),
                src,
            )
    return raw


def extract_package(
    package_path: str | Path,
    dest_dir: str | Path | None = None,
) -> Path:
    """Extract a retail zip to *dest_dir* (or a new temp dir). Returns root path.

    If *package_path* is already an unpacked directory with restore_map.json,
    returns that path without copying.
    """
    src = Path(package_path)
    if src.is_dir() and (src / RESTORE_MAP_NAME).is_file():
        return src.resolve()

    if dest_dir is None:
        dest = Path(tempfile.mkdtemp(prefix="mtpmanager-retail-restore-"))
    else:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

    if not zipfile.is_zipfile(src):
        raise ValueError(f"Not a retail package zip: {src}")

    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(dest)

    # Flatten if zip contained a single top-level folder
    map_at_root = dest / RESTORE_MAP_NAME
    if map_at_root.is_file():
        return dest.resolve()
    for child in dest.iterdir():
        if child.is_dir() and (child / RESTORE_MAP_NAME).is_file():
            return child.resolve()
    raise FileNotFoundError(
        f"Zip extracted but {RESTORE_MAP_NAME} not found under {dest}"
    )


def media_path_for_entry(package_root: Path, entry: dict[str, Any]) -> Path | None:
    rel = (entry.get("package_path") or "").strip().replace("\\", "/")
    if not rel:
        return None
    # Refuse path traversal
    if rel.startswith("/") or ".." in Path(rel).parts:
        logger.warning("Rejecting unsafe package_path=%r", rel)
        return None
    path = (package_root / rel).resolve()
    try:
        path.relative_to(package_root.resolve())
    except ValueError:
        logger.warning("package_path escapes root: %s", rel)
        return None
    if path.is_file():
        return path
    return None


def desired_tags_to_metadata(tags: dict[str, Any] | None) -> TrackMetadata:
    """Map reduced-map desired_tags → TrackMetadata for transport.send_track."""
    t = tags or {}
    duration_ms = int(t.get("duration_ms") or 0)
    length_sec = 0.0
    if duration_ms > 0:
        length_sec = duration_ms / 1000.0
    elif t.get("length_sec"):
        try:
            length_sec = float(t.get("length_sec") or 0)
        except (TypeError, ValueError):
            length_sec = 0.0

    tracknumber = t.get("tracknumber")
    if tracknumber is None or tracknumber == "":
        tn = "01"
    else:
        tn = str(tracknumber)

    title = (t.get("title") or "").strip() or "Unknown Title"
    artist = (t.get("artist") or "").strip() or "Unknown Artist"
    album = (t.get("album") or "").strip() or "Unknown Album"
    genre = (t.get("genre") or "").strip() or "Unknown Genre"
    composer = (t.get("composer") or "").strip() or "Unknown Composer"
    date = (t.get("date") or "").strip()

    return TrackMetadata(
        artist=artist,
        albumartist=artist,
        composer=composer,
        album=album,
        title=title,
        genre=genre,
        tracknumber=tn,
        date=date,
        length_sec=length_sec,
        sample_rate=int(t.get("sample_rate") or 0),
        channels=int(t.get("channels") or 0),
        bitrate=int(t.get("bitrate") or 0),
        bitrate_mode=int(t.get("bitrate_type") or t.get("bitrate_mode") or 0),
    )


def entries_for_restore(package_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries with include_in_restore true (default true)."""
    out: list[dict[str, Any]] = []
    for entry in package_doc.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        flags = entry.get("flags") or {}
        if flags.get("include_in_restore", True) is False:
            continue
        out.append(entry)
    return out
