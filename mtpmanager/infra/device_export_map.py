"""Verbose, human-editable export map for device media retrieval.

Written alongside Get Tracks from Device downloads so retail/demo libraries
can be studied and later restored (e.g. iFlash-upgraded ZENs missing stock
Creative demos). Edit ``desired_tags``, ``flags``, and ``editor_notes`` freely.

Formats:
  - ``device_media_map.json`` — full structured map (primary, re-loadable)
  - ``device_media_map.md``  — readable study document
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mtpmanager.domain.models import DeviceInfo, DeviceTrackInfo, DeviceTrackRef

logger = logging.getLogger(__name__)

MAP_SCHEMA_VERSION = 1
MAP_JSON_NAME = "device_media_map.json"
MAP_MD_NAME = "device_media_map.md"

# Creative demo heuristics (editable flags on each entry after export).
_RETAIL_ARTISTS = frozenset(
    {
        "creative technology ltd",
        "creative",
        "mama earth",
        "vincent cheng",
        "dr sk chew",
        "dr. sk chew",
    }
)
_RETAIL_ALBUMS = frozenset({"creative"})
_RETAIL_NAME_HINTS = (
    "creative",
    "zen vision",
    "zen micro",
    "i-trigue",
    "xtreme fidelity",
    "color fantasia",
    "panda",
    "neeon",
    "microphoto",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unix_to_iso(ts: int) -> str:
    if not ts or int(ts) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OSError, OverflowError, ValueError):
        return ""


def _tags_missing(info: DeviceTrackInfo | None, ref: DeviceTrackRef) -> bool:
    title = (info.title if info else "") or ref.title or ""
    artist = (info.artist if info else "") or ref.artist or ""
    title = title.strip()
    artist = artist.strip()
    if not title and not artist:
        return True
    if title in ("Unknown Title",) and artist in ("Unknown Artist", "—", ""):
        return True
    return False


def _looks_like_retail_demo(
    ref: DeviceTrackRef,
    info: DeviceTrackInfo | None,
) -> bool:
    artist = ((info.artist if info else "") or ref.artist or "").strip().casefold()
    album = ((info.album if info else "") or "").strip().casefold()
    title = ((info.title if info else "") or ref.title or "").strip().casefold()
    name = (ref.name or (info.name if info else "") or "").strip().casefold()
    if artist in _RETAIL_ARTISTS:
        return True
    if album in _RETAIL_ALBUMS:
        return True
    blob = f"{title} {name} {artist} {album}"
    return any(h in blob for h in _RETAIL_NAME_HINTS)


def filetype_label(filetype: int, device: Any | None = None) -> str:
    """Best-effort human label for libmtp filetype enum."""
    ft = int(filetype or 0)
    known = {
        0: "FOLDER",
        1: "WAV",
        2: "MP3",
        3: "WMA",
        4: "OGG",
        5: "AUDIBLE",
        6: "MP4",
        7: "UNDEF_AUDIO",
        8: "WMV",
        9: "AVI",
        10: "MPEG",
        11: "ASF",
        12: "QT",
        13: "UNDEF_VIDEO",
        14: "JPEG",
        15: "JFIF",
        16: "TIFF",
        17: "BMP",
        18: "GIF",
        19: "PICT",
        20: "PNG",
        30: "AAC",
        32: "FLAC",
        33: "MP2",
        34: "M4A",
    }
    if device is not None:
        try:
            desc = device.get_filetype_description(ft)
            if desc:
                text = (
                    desc.decode("utf-8", errors="replace")
                    if isinstance(desc, bytes)
                    else str(desc)
                ).strip()
                if text:
                    return text
        except Exception:
            pass
    return known.get(ft, f"UNKNOWN({ft})")


def build_entry_dict(
    *,
    index: int,
    ref: DeviceTrackRef,
    info: DeviceTrackInfo | None,
    host_path: str | None,
    status: str,
    error: str = "",
    tags_written: bool = False,
    host_tags: dict[str, Any] | None = None,
    filetype_desc: str = "",
    export_dir: str = "",
) -> dict[str, Any]:
    """One verbose map row (JSON-serializable)."""
    ft = int(
        (info.filetype if info and info.filetype else 0)
        or (ref.filetype or 0)
    )
    parent_id = int(
        (info.parent_id if info and info.parent_id else 0)
        or (ref.parent_id or 0)
    )
    storage_id = int(
        (info.storage_id if info and info.storage_id else 0)
        or (ref.storage_id or 0)
    )
    filesize = int(info.filesize if info else 0)
    mod = int(info.modificationdate if info else 0)
    filename = (ref.name or (info.name if info else "") or "").strip()

    device_tags = {
        "title": (info.title if info else "") or ref.title or "",
        "artist": (info.artist if info else "") or ref.artist or "",
        "album": (info.album if info else "") or "",
        "genre": (info.genre if info else "") or "",
        "composer": (info.composer if info else "") or "",
        "date": (info.date if info else "") or "",
        "tracknumber": int(info.tracknumber if info else 0) or 0,
        "duration_ms": int(info.duration_ms if info else 0) or 0,
        "sample_rate": int(info.sample_rate if info else 0) or 0,
        "channels": int(info.channels if info else 0) or 0,
        "bitrate": int(info.bitrate if info else 0) or 0,
        "bitrate_type": int(info.bitrate_type if info else 0) or 0,
        "rating": int(info.rating if info else 0) or 0,
        "usecount": int(info.usecount if info else 0) or 0,
    }

    missing = _tags_missing(info, ref)
    retail = _looks_like_retail_demo(ref, info)

    host_rel = ""
    host_abs = ""
    host_base = ""
    host_size = 0
    if host_path:
        host_abs = os.path.abspath(host_path)
        host_base = os.path.basename(host_abs)
        if export_dir:
            try:
                host_rel = os.path.relpath(host_abs, os.path.abspath(export_dir))
            except ValueError:
                host_rel = host_base
        else:
            host_rel = host_base
        try:
            if os.path.isfile(host_abs):
                host_size = int(os.path.getsize(host_abs))
        except OSError:
            pass

    return {
        "index": int(index),
        "item_id": int(ref.item_id or 0),
        "status": status,
        "error": error or "",
        "device_object": {
            "filename": filename,
            "parent_id": parent_id,
            "storage_id": storage_id,
            "storage_id_hex": f"0x{storage_id:08x}" if storage_id else "0x0",
            "filesize": filesize,
            "filesize_host": host_size,
            "filetype": ft,
            "filetype_label": filetype_desc or filetype_label(ft),
            "modificationdate": mod,
            "modificationdate_iso": _unix_to_iso(mod),
        },
        "device_tags": device_tags,
        # Copy for humans to fix before a future restore job.
        "desired_tags": dict(device_tags),
        "host": {
            "path": host_abs,
            "relative_path": host_rel,
            "basename": host_base,
            "tags_written": bool(tags_written),
            "tags_after_write": host_tags or {},
        },
        "flags": {
            "tags_missing": missing,
            "looks_like_retail_demo": retail,
            "include_in_restore": bool(retail) or not missing,
            "user_content": not retail,
            "download_ok": status == "ok",
            "needs_manual_tag_edit": missing,
        },
        "editor_notes": "",
    }


def build_map_document(
    *,
    entries: list[dict[str, Any]],
    dest_dir: str,
    device_info: DeviceInfo | None = None,
    export_label: str = "",
) -> dict[str, Any]:
    """Full map root object."""
    info = device_info or DeviceInfo()
    ok = sum(1 for e in entries if e.get("status") == "ok")
    failed = sum(1 for e in entries if e.get("status") == "failed")
    missing_tags = sum(
        1 for e in entries if (e.get("flags") or {}).get("tags_missing")
    )
    retail = sum(
        1 for e in entries if (e.get("flags") or {}).get("looks_like_retail_demo")
    )
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "document_type": "mtpmanager_device_media_export_map",
        "purpose": (
            "Verbose map of media retrieved from an MTP player for study and "
            "for restoring a retail/demo experience to iFlash-upgraded devices "
            "that no longer ship Creative stock content. Edit freely."
        ),
        "how_to_edit": (
            "1) Set flags.include_in_restore true/false per entry.\n"
            "2) Fix desired_tags (title/artist/album/genre/…) when device tags "
            "were empty or wrong.\n"
            "3) Use editor_notes for free-form research (video load notes, "
            "bitrate experiments, etc.).\n"
            "4) host.relative_path is relative to export_dir; keep files next "
            "to this JSON or update paths after moving files.\n"
            "5) Re-save as valid JSON (no trailing commas)."
        ),
        "exported_at": _utc_now(),
        "export_dir": os.path.abspath(dest_dir),
        "export_label": export_label or "",
        "device": {
            "name": info.name or "",
            "serial": info.serial or "",
            "manufacturer": info.manufacturer or "",
            "model": info.model or "",
            "version": info.version or "",
        },
        "summary": {
            "entry_count": len(entries),
            "downloaded_ok": ok,
            "download_failed": failed,
            "tags_missing_count": missing_tags,
            "looks_like_retail_demo_count": retail,
        },
        "notes": (
            "Global notes for this export (iFlash restore plan, source player "
            "condition, etc.)."
        ),
        "entries": entries,
    }


def write_export_map_json(doc: dict[str, Any], dest_dir: str) -> Path:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / MAP_JSON_NAME
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    logger.info("Wrote device media map JSON → %s (%d entries)", path, len(doc.get("entries") or []))
    return path


def write_export_map_markdown(doc: dict[str, Any], dest_dir: str) -> Path:
    """Human-readable companion for study (regenerate from JSON anytime)."""
    dest = Path(dest_dir)
    path = dest / MAP_MD_NAME
    device = doc.get("device") or {}
    summary = doc.get("summary") or {}
    entries = doc.get("entries") or []
    lines: list[str] = [
        "# Device media export map",
        "",
        f"- **Exported:** {doc.get('exported_at', '')}",
        f"- **Export dir:** `{doc.get('export_dir', '')}`",
        f"- **Device:** {device.get('name', '')} / {device.get('model', '')}",
        f"- **Serial:** `{device.get('serial', '')}`",
        f"- **Manufacturer:** {device.get('manufacturer', '')}",
        f"- **Firmware:** {device.get('version', '')}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| Entries | {summary.get('entry_count', 0)} |",
        f"| Downloaded OK | {summary.get('downloaded_ok', 0)} |",
        f"| Download failed | {summary.get('download_failed', 0)} |",
        f"| Missing tags | {summary.get('tags_missing_count', 0)} |",
        f"| Looks like retail demo | {summary.get('looks_like_retail_demo_count', 0)} |",
        "",
        "## Purpose",
        "",
        str(doc.get("purpose") or ""),
        "",
        "## How to edit",
        "",
        "Edit **`device_media_map.json`** (authoritative). This Markdown is a "
        "readable snapshot; re-export or regenerate after JSON edits if needed.",
        "",
        "```",
        str(doc.get("how_to_edit") or ""),
        "```",
        "",
        f"## Global notes",
        "",
        str(doc.get("notes") or ""),
        "",
        "## Entries",
        "",
    ]
    for e in entries:
        obj = e.get("device_object") or {}
        tags = e.get("device_tags") or {}
        flags = e.get("flags") or {}
        host = e.get("host") or {}
        lines.extend(
            [
                f"### {e.get('index', 0)}. item_id={e.get('item_id')} — "
                f"{obj.get('filename') or host.get('basename') or '(unnamed)'}",
                "",
                f"- **Status:** {e.get('status')}"
                + (f" — {e.get('error')}" if e.get("error") else ""),
                f"- **Host file:** `{host.get('relative_path') or host.get('path') or '—'}`",
                f"- **Size (device / host):** {obj.get('filesize', 0)} / "
                f"{obj.get('filesize_host', 0)} bytes",
                f"- **Parent / storage:** {obj.get('parent_id')} / "
                f"{obj.get('storage_id_hex')} ({obj.get('storage_id')})",
                f"- **Filetype:** {obj.get('filetype_label')} "
                f"(enum {obj.get('filetype')})",
                f"- **Modified:** {obj.get('modificationdate_iso') or '—'}",
                f"- **Tags:** title={tags.get('title')!r} artist={tags.get('artist')!r} "
                f"album={tags.get('album')!r} genre={tags.get('genre')!r}",
                f"- **Stream:** duration_ms={tags.get('duration_ms')} "
                f"sample_rate={tags.get('sample_rate')} "
                f"channels={tags.get('channels')} bitrate={tags.get('bitrate')} "
                f"bitrate_type={tags.get('bitrate_type')}",
                f"- **Usage:** rating={tags.get('rating')} usecount={tags.get('usecount')} "
                f"tracknumber={tags.get('tracknumber')}",
                f"- **Flags:** retail_demo={flags.get('looks_like_retail_demo')} "
                f"tags_missing={flags.get('tags_missing')} "
                f"include_in_restore={flags.get('include_in_restore')} "
                f"user_content={flags.get('user_content')}",
                f"- **Editor notes:** {e.get('editor_notes') or '*(empty — fill in JSON)*'}",
                "",
            ]
        )
    lines.append(
        "---\n\n*Generated by MtpManager. Authoritative data: "
        f"`{MAP_JSON_NAME}`.*\n"
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote device media map Markdown → %s", path)
    return path


def write_export_maps(
    doc: dict[str, Any],
    dest_dir: str,
) -> tuple[Path, Path]:
    """Write JSON + Markdown maps into *dest_dir*."""
    j = write_export_map_json(doc, dest_dir)
    m = write_export_map_markdown(doc, dest_dir)
    return j, m


def load_export_map(path: str | Path) -> dict[str, Any] | None:
    """Load a previously written map JSON (for future restore tooling)."""
    src = Path(path)
    if src.is_dir():
        src = src / MAP_JSON_NAME
    if not src.is_file():
        return None
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        logger.warning("Cannot read export map %s: %s", src, e)
        return None
    if not isinstance(raw, dict):
        return None
    return raw
