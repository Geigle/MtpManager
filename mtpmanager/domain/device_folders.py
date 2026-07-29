"""Resolve MTP top-level folder roles from device folder listings.

Object IDs for Music / Video / TV / … are **not** portable across Creative
firmware builds (or other players). A Vision:M on one firmware may use Music
``100``; another uses ``88``. Always prefer matching **folder names** from
``list_folders`` / ``mtp-folders``, then fall back to documented legacy IDs
only when discovery fails.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from mtpmanager.domain.models import FolderEntry
from mtpmanager.infra.remote_naming import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    ZEN_VISION_M_FOLDER_IDS,
)


class FolderRole(str, Enum):
    """Logical use of a top-level device folder (not a wire object id)."""

    MUSIC = "music"
    VIDEO = "video"
    TV = "tv"
    PICTURES = "pictures"
    PLAYLISTS = "playlists"
    RECORDINGS = "recordings"
    ORGANIZER = "organizer"
    PODCAST = "podcast"
    SLIDESHOWS = "slideshows"


# Casefold names that map to a role. Prefer exact primary names first when
# scoring (see :func:`resolve_device_folder_layout`).
_ROLE_NAME_ALIASES: dict[FolderRole, tuple[str, ...]] = {
    FolderRole.MUSIC: (
        "music",
        "my music",
        "audio",
        "songs",
    ),
    FolderRole.VIDEO: (
        "video",
        "videos",
        "my videos",
        "movies",
    ),
    FolderRole.TV: (
        "tv",
        "tv shows",
        "television",
        "tv series",
    ),
    FolderRole.PICTURES: (
        "pictures",
        "photos",
        "images",
        "my pictures",
    ),
    FolderRole.PLAYLISTS: (
        "my playlists",
        "playlists",
        "playlist",
    ),
    FolderRole.RECORDINGS: (
        "my recordings",
        "recordings",
        "voice recordings",
    ),
    FolderRole.ORGANIZER: (
        "my organizer",
        "organizer",
        "calendar",
    ),
    FolderRole.PODCAST: (
        "zencast",
        "zen cast",
        "podcasts",
        "podcast",
    ),
    FolderRole.SLIDESHOWS: (
        "my slideshows",
        "slideshows",
        "slideshow",
    ),
}

# Primary (canonical) display names for each role.
_ROLE_PRIMARY_NAME: dict[FolderRole, str] = {
    FolderRole.MUSIC: "Music",
    FolderRole.VIDEO: "Video",
    FolderRole.TV: "TV",
    FolderRole.PICTURES: "Pictures",
    FolderRole.PLAYLISTS: "My Playlists",
    FolderRole.RECORDINGS: "My Recordings",
    FolderRole.ORGANIZER: "My Organizer",
    FolderRole.PODCAST: "ZENcast",
    FolderRole.SLIDESHOWS: "My Slideshows",
}


def _normalize_folder_name(name: str | None) -> str:
    text = (name or "").strip().casefold()
    # Collapse internal whitespace.
    return " ".join(text.split())


def role_for_folder_name(name: str | None) -> FolderRole | None:
    """Map a folder display name to a :class:`FolderRole`, or None."""
    key = _normalize_folder_name(name)
    if not key:
        return None
    for role, aliases in _ROLE_NAME_ALIASES.items():
        if key in aliases:
            return role
    return None


@dataclass(frozen=True)
class DeviceFolderLayout:
    """Resolved folder object ids for a connected (or fallback) device."""

    # role → object id
    roles: Mapping[FolderRole, int] = field(default_factory=dict)
    # object id → display name as listed on the device
    names_by_id: Mapping[int, str] = field(default_factory=dict)
    # How this layout was produced.
    source: str = "fallback"  # "listed" | "fallback" | "merged"

    @property
    def music_id(self) -> int:
        return int(self.roles.get(FolderRole.MUSIC, DEFAULT_MUSIC_FOLDER_ID))

    @property
    def video_id(self) -> int:
        return int(self.roles.get(FolderRole.VIDEO, DEFAULT_VIDEO_FOLDER_ID))

    @property
    def tv_id(self) -> int:
        return int(self.roles.get(FolderRole.TV, DEFAULT_TV_FOLDER_ID))

    def id_for(self, role: FolderRole) -> int | None:
        """Return object id for *role*, or None if unknown."""
        val = self.roles.get(role)
        return int(val) if val is not None else None

    def name_for(self, folder_id: int) -> str:
        """Device display name for *folder_id*, or empty."""
        return (self.names_by_id.get(int(folder_id)) or "").strip()

    def role_for_id(self, folder_id: int) -> FolderRole | None:
        fid = int(folder_id or 0)
        for role, rid in self.roles.items():
            if int(rid) == fid:
                return role
        return None

    def video_parent_ids(self) -> frozenset[int]:
        """Object ids treated as Video / TV destinations."""
        out: set[int] = set()
        for role in (FolderRole.VIDEO, FolderRole.TV):
            rid = self.roles.get(role)
            if rid is not None:
                out.add(int(rid))
        if not out:
            out.update({DEFAULT_VIDEO_FOLDER_ID, DEFAULT_TV_FOLDER_ID})
        return frozenset(out)

    @property
    def podcast_id(self) -> int:
        """ZENcast / Podcasts folder id (legacy 128 when unknown)."""
        from mtpmanager.infra.remote_naming import ZEN_VISION_M_FOLDER_NAMES

        rid = self.roles.get(FolderRole.PODCAST)
        if rid is not None and int(rid) > 0:
            return int(rid)
        return int(ZEN_VISION_M_FOLDER_NAMES.get("zencast", 128))

    def non_music_parent_ids(self) -> frozenset[int]:
        """Parents that should not appear on Device → Music."""
        music = self.music_id
        out: set[int] = set()
        for role, rid in self.roles.items():
            if role is FolderRole.MUSIC:
                continue
            out.add(int(rid))
        # Always exclude known non-music roles even if music id equals a
        # legacy value that once meant something else.
        if music in out:
            out.discard(music)
        return frozenset(out)

    def video_folder_label(self, parent_id: int) -> str:
        """Human label for a video tree group (Video / TV / named / Other)."""
        role = self.role_for_id(parent_id)
        if role is FolderRole.VIDEO:
            return "Video"
        if role is FolderRole.TV:
            return "TV"
        name = self.name_for(parent_id)
        if name:
            return name
        # Legacy hard-coded fallbacks when layout is fallback-only.
        pid = int(parent_id or 0)
        if pid == DEFAULT_VIDEO_FOLDER_ID:
            return "Video"
        if pid == DEFAULT_TV_FOLDER_ID:
            return "TV"
        return "Other"

    def as_id_name_map(self) -> dict[int, str]:
        """id → name for UI labels (includes resolved roles)."""
        out = dict(self.names_by_id)
        for role, rid in self.roles.items():
            out.setdefault(int(rid), _ROLE_PRIMARY_NAME.get(role, role.value))
        return out


def legacy_zen_vision_m_layout() -> DeviceFolderLayout:
    """Documented Vision:M defaults (Music 100 / Video 120 / TV 124).

    Used only when the device cannot be listed or names cannot be matched.
    """
    roles: dict[FolderRole, int] = {
        FolderRole.MUSIC: DEFAULT_MUSIC_FOLDER_ID,
        FolderRole.VIDEO: DEFAULT_VIDEO_FOLDER_ID,
        FolderRole.TV: DEFAULT_TV_FOLDER_ID,
    }
    # Map legacy id table through name → role when possible.
    for fid, name in ZEN_VISION_M_FOLDER_IDS.items():
        role = role_for_folder_name(name)
        if role is not None and role not in roles:
            roles[role] = int(fid)
    return DeviceFolderLayout(
        roles=roles,
        names_by_id=dict(ZEN_VISION_M_FOLDER_IDS),
        source="fallback",
    )


def _folder_depth_score(entry: FolderEntry, by_id: Mapping[int, FolderEntry]) -> int:
    """0 = root-ish (parent 0 or missing parent), higher = nested."""
    depth = 0
    pid = int(entry.parent_id or 0)
    seen: set[int] = set()
    while pid > 0 and pid not in seen and depth < 8:
        seen.add(pid)
        parent = by_id.get(pid)
        if parent is None:
            break
        depth += 1
        pid = int(parent.parent_id or 0)
    return depth


def resolve_device_folder_layout(
    folders: Sequence[FolderEntry] | Iterable[FolderEntry],
    *,
    fallback: DeviceFolderLayout | None = None,
) -> DeviceFolderLayout:
    """Build a :class:`DeviceFolderLayout` from a live folder listing.

    Matching rules:
    1. Normalize folder names (casefold, strip).
    2. Map to :class:`FolderRole` via known aliases (``Music``, ``Video``, …).
    3. Prefer **shallower** folders (top-level over nested ``Music/…``).
    4. Prefer primary alias (e.g. exact ``music``) over secondary.
    5. Unmatched roles filled from *fallback* (default: legacy Vision:M map).
    """
    base = fallback if fallback is not None else legacy_zen_vision_m_layout()
    entries = [e for e in folders if e is not None]
    if not entries:
        return DeviceFolderLayout(
            roles=dict(base.roles),
            names_by_id=dict(base.names_by_id),
            source="fallback",
        )

    by_id: dict[int, FolderEntry] = {}
    names_by_id: dict[int, str] = {}
    for e in entries:
        fid = int(e.folder_id or 0)
        if fid <= 0:
            continue
        by_id[fid] = e
        name = (e.name or "").strip()
        if name:
            names_by_id[fid] = name

    # role → list of (score, folder_id) lower score wins
    candidates: dict[FolderRole, list[tuple[int, int]]] = {}
    for e in entries:
        fid = int(e.folder_id or 0)
        if fid <= 0:
            continue
        role = role_for_folder_name(e.name)
        if role is None:
            continue
        key = _normalize_folder_name(e.name)
        aliases = _ROLE_NAME_ALIASES[role]
        # Primary alias = first entry in the tuple (e.g. "music").
        primary = aliases[0] if aliases else key
        alias_rank = 0 if key == primary else 1
        depth = _folder_depth_score(e, by_id)
        # Prefer shallow, then primary name match, then smaller id (stable).
        score = depth * 100 + alias_rank * 10
        candidates.setdefault(role, []).append((score, fid))

    roles: dict[FolderRole, int] = {}
    for role, scored in candidates.items():
        scored.sort(key=lambda t: (t[0], t[1]))
        roles[role] = scored[0][1]

    # Fill missing critical roles from fallback so sends still have a parent.
    for role in (
        FolderRole.MUSIC,
        FolderRole.VIDEO,
        FolderRole.TV,
        FolderRole.PODCAST,
    ):
        if role not in roles:
            fb = base.roles.get(role)
            if fb is not None:
                roles[role] = int(fb)
                names_by_id.setdefault(
                    int(fb),
                    _ROLE_PRIMARY_NAME.get(role, role.value),
                )

    # Secondary roles from fallback only if not discovered.
    for role, fid in base.roles.items():
        roles.setdefault(role, int(fid))

    source = "listed" if candidates else "fallback"
    if candidates and any(
        r not in candidates
        for r in (FolderRole.MUSIC, FolderRole.VIDEO, FolderRole.TV)
    ):
        source = "merged"

    return DeviceFolderLayout(
        roles=roles,
        names_by_id=names_by_id,
        source=source,
    )
