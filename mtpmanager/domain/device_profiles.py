"""Built-in device profile registry (extend here for new players)."""

from __future__ import annotations

from mtpmanager.domain.device_profile import DeviceProfile

# First match wins (excluding generic, which is always fallback).
ZEN_VISION_M = DeviceProfile(
    id="creative_zen_vision_m",
    display_name="Creative ZEN Vision:M",
    manufacturer_needles=("creative",),
    model_needles=("vision:m", "vision m", "zen vision"),
    name_needles=(),
    graphic_filename="zen_vision_m.png",
    # Official Creative formats for Vision:M: MP3, WMA, WAV (PCM).
    supported_audio_formats=frozenset({"mp3", "wma", "wav"}),
)

GENERIC = DeviceProfile(
    id="generic",
    display_name="MTP Player",
    manufacturer_needles=(),
    model_needles=(),
    name_needles=(),
    graphic_filename="generic_player.png",
    # Conservative default when the player is unknown.
    supported_audio_formats=frozenset({"mp3"}),
)

BUILTIN_PROFILES: tuple[DeviceProfile, ...] = (
    ZEN_VISION_M,
    GENERIC,
)
