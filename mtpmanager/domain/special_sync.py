"""Special Sync options: experimental per-batch send overrides.

Normal Sync ignores this module. Special Sync (experimental tools) builds a
:class:`SpecialSyncOptions` from the dialog and threads it into
``app.transfer.transfer_tracks``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Literal, Mapping

from mtpmanager.domain.audio_encode import AudioEncodeSettings
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.remote_naming import (
    MAX_REMOTE_BASENAME,
    sanitize_component,
)

FolderMode = Literal["none", "artist", "artist_album", "custom"]
BasenameMode = Literal["source_stem", "title", "pattern"]

# Tag fields the Special Sync dialog may override (host library untouched).
META_PATCH_FIELDS: tuple[str, ...] = (
    "title",
    "artist",
    "albumartist",
    "album",
    "genre",
    "tracknumber",
    "date",
    "composer",
)

_PATTERN_TOKEN = re.compile(
    r"\{(title|artist|album|albumartist|genre|tracknumber|date|composer)\}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpecialSyncOptions:
    """Per-batch overrides for an experimental Special Sync job.

    Defaults match normal Sync (GUID ObjectFileName, no meta patch, normal
    parent resolution) so empty options are a no-op.
    """

    encode: AudioEncodeSettings | None = None
    force_transcode: bool = False
    # Keys in META_PATCH_FIELDS → non-empty string overrides.
    meta_patch: Mapping[str, str] = field(default_factory=dict)
    # When True, every non-empty meta_patch value is forced onto every track
    # even if the dialog field was only meant as a partial patch (UI sets this
    # from "Apply these metadata values to every track").
    apply_meta_to_all: bool = False
    # Fixed MTP parent folder id; when set, wins over GUID flat-Music rule.
    parent_id: int | None = None
    folder_mode: FolderMode = "none"
    custom_folder_name: str = ""
    use_guid: bool = True
    basename_mode: BasenameMode = "source_stem"
    basename_pattern: str = "{tracknumber} - {title}"
    # Single-track optional override when use_guid is False.
    custom_basename: str = ""
    # Skip when host GUID already on device (only meaningful with use_guid).
    skip_if_present: bool = True

    def effective_skip_if_present(self) -> bool:
        return bool(self.use_guid and self.skip_if_present)

    def needs_folder_create(self) -> bool:
        return self.folder_mode in ("artist", "artist_album", "custom")


def common_meta_seed(tracks: list[Track]) -> dict[str, str]:
    """Pre-fill dialog fields: shared values only; empty string if varies."""
    if not tracks:
        return {k: "" for k in META_PATCH_FIELDS}
    if len(tracks) == 1:
        m = tracks[0].meta
        return {k: str(getattr(m, k, "") or "") for k in META_PATCH_FIELDS}
    out: dict[str, str] = {}
    for key in META_PATCH_FIELDS:
        values = {str(getattr(t.meta, key, "") or "") for t in tracks}
        out[key] = next(iter(values)) if len(values) == 1 else ""
    return out


def apply_meta_patch(
    meta: TrackMetadata,
    patch: Mapping[str, str] | None,
    *,
    apply_all: bool = False,
) -> TrackMetadata:
    """Return *meta* with non-empty *patch* string fields applied.

    Empty / missing patch values leave the original field. Technical stream
    fields (length, bitrate, …) are never patched from the dialog.
    """
    if not patch:
        return meta
    kwargs: dict[str, str] = {}
    for key in META_PATCH_FIELDS:
        if key not in patch:
            continue
        raw = patch.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text and not apply_all:
            continue
        # apply_all with empty still skips empty (never wipe tags to blank
        # unless user typed something; blank means "no override").
        if not text:
            continue
        kwargs[key] = text
    if not kwargs:
        return meta
    return replace(meta, **kwargs)


def _format_pattern(pattern: str, meta: TrackMetadata) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        return str(getattr(meta, key, "") or "")

    return _PATTERN_TOKEN.sub(repl, pattern or "")


def basename_for_special_sync(
    track: Track,
    meta: TrackMetadata,
    file_extension: str,
    *,
    options: SpecialSyncOptions,
    max_basename: int = MAX_REMOTE_BASENAME,
) -> str:
    """ObjectFileName when GUID naming is off (no path separators).

    Returns basename **with** extension.
    """
    ext = (file_extension or "").lower().lstrip(".")
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    elif not ext:
        # Fall back to source extension
        _, src_ext = os.path.splitext(track.path or "")
        ext = src_ext.lower() if src_ext else ".mp3"

    custom = (options.custom_basename or "").strip()
    if custom:
        stem, c_ext = os.path.splitext(custom)
        if c_ext:
            # User included extension — sanitize stem, keep their ext if sane
            safe_stem = sanitize_component(stem, max_basename - len(c_ext))
            return f"{safe_stem}{c_ext.lower()}"
        safe_stem = sanitize_component(custom, max_basename - len(ext))
        return f"{safe_stem}{ext}"

    mode = options.basename_mode or "source_stem"
    if mode == "title":
        raw = (meta.title or "").strip() or "unknown"
    elif mode == "pattern":
        raw = _format_pattern(options.basename_pattern, meta).strip() or (
            (meta.title or "").strip() or "unknown"
        )
    else:
        # source_stem (default)
        base = os.path.basename(track.path or "")
        stem, _ = os.path.splitext(base)
        raw = stem.strip() or (meta.title or "").strip() or "unknown"

    # Leave room for extension
    budget = max(1, max_basename - len(ext))
    safe = sanitize_component(raw, budget)
    return f"{safe}{ext}"


def meta_patch_from_dialog_fields(
    fields: Mapping[str, str],
    *,
    seed: Mapping[str, str] | None = None,
    apply_all: bool = False,
) -> dict[str, str]:
    """Build a patch dict from dialog Entry values.

    When *apply_all* is False, only include keys whose value differs from
    *seed* (or any non-empty value when seed is missing). When *apply_all*
    is True, include every non-empty field.
    """
    patch: dict[str, str] = {}
    for key in META_PATCH_FIELDS:
        if key not in fields:
            continue
        text = str(fields.get(key) or "").strip()
        if not text:
            continue
        if apply_all:
            patch[key] = text
            continue
        if seed is not None:
            seed_val = str(seed.get(key) or "").strip()
            if text == seed_val:
                # Unchanged common seed — do not force onto diverging tracks
                # when multi-select left the shared value alone.
                # Exception: single-track seed equals fields → still want
                # overrides only when user edited. Same comparison works:
                # unchanged → omit; edited → include.
                continue
        patch[key] = text
    return patch
