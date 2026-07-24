"""App use-cases: package retail demos from export; restore package to device."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mtpmanager.app.cancellation import CancelCheck, JobCancelled, raise_if_cancelled
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    ZEN_VISION_M_FOLDER_IDS,
)
from mtpmanager.infra.retail_package import (
    desired_tags_to_metadata,
    entries_for_restore,
    extract_package,
    load_package_map,
    media_path_for_entry,
    package_retail_export,
)
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

# libmtp filetype enums commonly used for video (see device_export_map labels).
_VIDEO_FILETYPES = frozenset({8, 9, 10, 11, 12, 13})  # WMV AVI MPEG ASF QT UNDEF_VIDEO


@dataclass
class RestorePackageResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    aborted: bool = False
    cancelled: bool = False
    failed_label: str = ""
    errors: list[str] = field(default_factory=list)


def package_retail_from_export(
    export_path: str | Path,
    zip_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
):
    """Thin wrapper — see :func:`package_retail_export`."""
    return package_retail_export(
        export_path, zip_path, on_progress=on_progress
    )


def _parent_for_entry(entry: dict) -> int:
    """Choose MTP parent folder: Video for video filetypes, else Music."""
    obj = entry.get("device_object") or {}
    ft = int(obj.get("filetype") or 0)
    parent = int(obj.get("parent_id") or 0)
    # Prefer original parent when it is a known top-level ZEN folder.
    if parent in ZEN_VISION_M_FOLDER_IDS:
        return parent
    if ft in _VIDEO_FILETYPES:
        return DEFAULT_VIDEO_FOLDER_ID
    label = str(obj.get("filetype_label") or "").casefold()
    if any(x in label for x in ("video", "wmv", "avi", "mpeg", "asf")):
        return DEFAULT_VIDEO_FOLDER_ID
    return DEFAULT_MUSIC_FOLDER_ID


def restore_retail_package(
    transport: Transport,
    package_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    stop_on_fatal: bool = True,
    cleanup_extract: bool = True,
) -> RestorePackageResult:
    """Extract retail zip (or use unpacked dir) and send each include_in_restore entry.

    Uses **no GUID** ObjectFileNames — ``preferred_basename`` from the map so
    demos keep retail-like names (Dance.mp3). Tags come from ``desired_tags``.
    Aborts the batch on fatal TransportError (same as normal transfer).
    """
    package_path = Path(package_path)
    result = RestorePackageResult()
    extract_root: Path | None = None
    owns_extract = False

    try:
        if package_path.is_dir():
            extract_root = extract_package(package_path)
            owns_extract = False
        else:
            extract_root = extract_package(package_path)
            owns_extract = True

        doc = load_package_map(extract_root)
        if doc is None:
            raise FileNotFoundError(
                f"No restore_map.json in package {package_path}"
            )

        entries = entries_for_restore(doc)
        result.total = len(entries)
        if not entries:
            logger.info("restore_retail_package: nothing to send (all skipped)")
            return result

        logger.info(
            "restore_retail_package start total=%s package=%s",
            result.total,
            package_path,
        )

        for i, entry in enumerate(entries):
            raise_if_cancelled(
                should_cancel,
                completed=result.succeeded,
                total=result.total,
            )
            label = (
                (entry.get("remote_basename") or "")
                or (entry.get("package_path") or "")
                or f"entry {i + 1}"
            )
            if on_progress is not None:
                try:
                    on_progress(i, result.total, label)
                except Exception:
                    pass

            media = media_path_for_entry(extract_root, entry)
            if media is None:
                msg = f"Missing media for {label} ({entry.get('package_path')})"
                logger.error(msg)
                result.failed += 1
                result.errors.append(msg)
                result.skipped += 1
                continue

            meta = desired_tags_to_metadata(entry.get("desired_tags") or {})
            remote_base = (entry.get("remote_basename") or media.name).strip()
            parent_id = _parent_for_entry(entry)

            try:
                transport.send_track(
                    str(media),
                    meta,
                    parent_id=parent_id,
                    guid=None,
                    preferred_basename=remote_base,
                )
                result.succeeded += 1
                logger.info(
                    "restore sent %s → parent=%s basename=%s",
                    media.name,
                    parent_id,
                    remote_base,
                )
            except TransportError as exc:
                result.failed += 1
                result.errors.append(f"{label}: {exc}")
                result.failed_label = label
                logger.error(
                    "restore_retail_package failed label=%s fatal=%s: %s",
                    label,
                    exc.fatal,
                    exc,
                )
                if stop_on_fatal and exc.fatal:
                    result.aborted = True
                    if on_progress is not None:
                        try:
                            on_progress(i, result.total, label)
                        except Exception:
                            pass
                    return result
            except JobCancelled:
                result.cancelled = True
                raise
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{label}: {exc}")
                logger.exception("restore unexpected label=%s", label)

        if on_progress is not None:
            try:
                on_progress(result.total, result.total, "done")
            except Exception:
                pass
        logger.info(
            "restore_retail_package done succeeded=%s failed=%s total=%s",
            result.succeeded,
            result.failed,
            result.total,
        )
        return result
    except JobCancelled:
        result.cancelled = True
        raise
    finally:
        if (
            cleanup_extract
            and owns_extract
            and extract_root is not None
            and extract_root.is_dir()
        ):
            # Only remove temp dirs we created (prefix from extract_package).
            name = extract_root.name
            if name.startswith("mtpmanager-retail-restore-"):
                try:
                    shutil.rmtree(extract_root, ignore_errors=True)
                except Exception:
                    logger.debug(
                        "Could not remove extract dir %s", extract_root, exc_info=True
                    )
