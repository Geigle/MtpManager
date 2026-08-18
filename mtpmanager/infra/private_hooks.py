"""Optional private package hooks (silent no-op when absent).

A gitignored ``private/`` package at the project root may expose
``private.library_guid_adapter``. When missing, all helpers return
neutral defaults — no UI or config should mention the hook.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_adapter: Any | None | bool = False  # False = not probed yet


def library_guid_adapter() -> Any | None:
    """Return the private GUID adapter module, or None if unavailable."""
    global _adapter
    if _adapter is False:
        try:
            import private.library_guid_adapter as mod  # type: ignore[import-not-found]

            _adapter = mod
            logger.debug("Private library GUID adapter loaded")
        except ImportError:
            _adapter = None
    return None if _adapter is False else _adapter


def enrich_path_guid_map(
    tracks: Iterable[Any],
    path_map: dict[str, str],
) -> dict[str, str]:
    """Merge optional private GUID sources into *path_map* (sidecar wins)."""
    adapter = library_guid_adapter()
    if adapter is None:
        return path_map
    try:
        enriched = adapter.enrich_path_guid_map(list(tracks), dict(path_map))
    except Exception:
        logger.debug("private enrich_path_guid_map failed", exc_info=True)
        return path_map
    if not isinstance(enriched, dict):
        return path_map
    return enriched


def after_library_saved(tracks: Iterable[Any]) -> None:
    """Notify private adapter after a successful library index save."""
    adapter = library_guid_adapter()
    if adapter is None:
        return
    try:
        adapter.after_library_saved(list(tracks))
    except Exception:
        logger.debug("private after_library_saved failed", exc_info=True)


def obsolete_guid_paths() -> dict[str, str]:
    """Return ``{obsolete_guid: host_path}`` when the adapter is loaded."""
    adapter = library_guid_adapter()
    if adapter is None:
        return {}
    try:
        raw = adapter.obsolete_guid_paths()
    except Exception:
        logger.debug("private obsolete_guid_paths failed", exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def clear_obsolete_guids(guids: Iterable[str]) -> None:
    """Drop obsolete GUID records after a successful device GUID update."""
    adapter = library_guid_adapter()
    if adapter is None:
        return
    try:
        adapter.clear_obsolete_guids(list(guids))
    except Exception:
        logger.debug("private clear_obsolete_guids failed", exc_info=True)
