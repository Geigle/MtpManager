"""Device profile model and matching (stdlib only, no I/O)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from mtpmanager.domain.library import extension_of
from mtpmanager.domain.models import DeviceInfo


@dataclass(frozen=True)
class DeviceProfile:
    """Describes a known player family for UI and device-specific behavior.

    Needle groups use casefold substring match. An empty needle tuple means
    that field is ignored. Non-empty groups must all match (AND).

    *supported_audio_formats* lists extensions the player can play natively
    (lowercase, no dot). Sources already in these formats are sent as-is
    instead of re-encoding to the user's preferred target format.
    """

    id: str
    display_name: str
    manufacturer_needles: tuple[str, ...]
    model_needles: tuple[str, ...]
    name_needles: tuple[str, ...] = ()
    graphic_filename: str = "generic_player.png"
    # Native playable audio extensions (e.g. frozenset({"mp3", "wma", "wav"})).
    supported_audio_formats: frozenset[str] = frozenset({"mp3"})

    def accepts_audio_format(self, fmt: str) -> bool:
        """True if *fmt* (extension or dotted) is natively playable."""
        key = (fmt or "").lower().lstrip(".")
        return key in self.supported_audio_formats

    def accepts_source_path(self, path: str) -> bool:
        """True if the file extension is natively playable on this device."""
        return extension_of(path) in self.supported_audio_formats


def normalize_audio_formats(formats: Collection[str] | None) -> frozenset[str]:
    """Lowercase, strip dots; empty/None → empty set."""
    if not formats:
        return frozenset()
    return frozenset(f.lower().lstrip(".") for f in formats if f)


def needs_transcode(
    path: str,
    *,
    target_format: str,
    device_formats: Collection[str] | None = None,
) -> bool:
    """True when *path* must be converted before send.

    Skip convert when the source is already in a device-native format
    (avoids lossy→lossy re-encodes), or when it already matches *target_format*.
    """
    ext = extension_of(path)
    if not ext:
        return True
    allowed = normalize_audio_formats(device_formats)
    if ext in allowed:
        return False
    target = (target_format or "").lower().lstrip(".")
    return ext != target


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
