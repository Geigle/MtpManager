"""Device profile model and matching (stdlib only, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from mtpmanager.domain.models import DeviceInfo


@dataclass(frozen=True)
class DeviceProfile:
    """Describes a known player family for UI and future device-specific behavior.

    Needle groups use casefold substring match. An empty needle tuple means
    that field is ignored. Non-empty groups must all match (AND).
    """

    id: str
    display_name: str
    manufacturer_needles: tuple[str, ...]
    model_needles: tuple[str, ...]
    name_needles: tuple[str, ...] = ()
    graphic_filename: str = "generic_player.png"


def _field_matches(value: str, needles: tuple[str, ...]) -> bool:
    if not needles:
        return True
    hay = (value or "").casefold()
    return any(n.casefold() in hay for n in needles if n)


def profile_matches(info: DeviceInfo, profile: DeviceProfile) -> bool:
    """True if *info* satisfies all non-empty needle groups on *profile*."""
    return (
        _field_matches(info.manufacturer, profile.manufacturer_needles)
        and _field_matches(info.model, profile.model_needles)
        and _field_matches(info.name, profile.name_needles)
    )


def match_device_profile(
    info: DeviceInfo,
    profiles: Sequence[DeviceProfile],
    *,
    fallback: DeviceProfile | None = None,
) -> DeviceProfile:
    """Return the first matching profile, or *fallback* / last generic."""
    for profile in profiles:
        if profile.id == "generic":
            continue
        if profile_matches(info, profile):
            return profile
    if fallback is not None:
        return fallback
    for profile in profiles:
        if profile.id == "generic":
            return profile
    if profiles:
        return profiles[-1]
    raise ValueError("no device profiles registered")
