"""Phase 3 (Milestone D) tool catalog — experimental / power tools."""

from __future__ import annotations

from typing import Any

PHASE3_TOOLS: list[dict[str, Any]] = [
    {
        "name": "retail_package",
        "cli": ["retail", "package"],
        "description": (
            "Package a retail export directory into a zip (host). "
            "Requires enable_experimental_tools + confirm."
        ),
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "export_path": {"type": "string"},
                "zip_path": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["export_path", "zip_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retail_restore",
        "cli": ["retail", "restore"],
        "description": (
            "Restore retail package zip/dir to device (demo basenames, no GUID). "
            "Experimental. Risks R1/R3/R5. confirm or dry_run."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "package_path": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["package_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_shrink",
        "cli": ["device", "shrink"],
        "description": (
            "Delete+re-encode on-device tracks at lower bitrate (quality loss). "
            "By guids or artist/album. Risks R1/R3/R5. dry_run or confirm."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "guids": {"type": "array", "items": {"type": "string"}},
                "artist": {"type": "string"},
                "album": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_delete_all_tracks",
        "cli": ["device", "delete-all"],
        "description": (
            "Delete ALL music/video tracks on device. Experimental. "
            'Requires confirm=true AND confirm_phrase="DELETE ALL TRACKS". R1/R3/R5.'
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "default": False},
                "confirm_phrase": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_create_folder",
        "cli": ["device", "create-folder"],
        "description": (
            "Create MTP folder under parent_id (default Music 100). "
            "ctypes/string hazards — see pymtp-binding-hazards. R3. confirm required."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": "integer", "default": 100},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_delete_bulk",
        "cli": ["device", "delete-bulk"],
        "description": (
            "Bulk delete by artist/album (GUID join) or object_ids. "
            "Prefer dry_run first. Risks R1/R3/R5."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "artist": {"type": "string"},
                "album": {"type": "string"},
                "object_ids": {"type": "array", "items": {"type": "integer"}},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_playlist_list",
        "cli": ["device-playlist", "list"],
        "description": "List on-device playlists (requires connect). R3.",
        "host_only": False,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_playlist_show",
        "cli": ["device-playlist", "show"],
        "description": "Show device playlist track_ids by name.",
        "host_only": False,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_playlist_update",
        "cli": ["device-playlist", "update"],
        "description": (
            "Rewrite device playlist: full track_ids replace, or remove_indices / "
            "move_indices+delta. Requires confirm. R1/R3."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "track_ids": {"type": "array", "items": {"type": "integer"}},
                "remove_indices": {"type": "array", "items": {"type": "integer"}},
                "move_indices": {"type": "array", "items": {"type": "integer"}},
                "delta": {"type": "integer", "default": -1},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_playlist_shuffle",
        "cli": ["device-playlist", "shuffle"],
        "description": (
            "Shuffle device playlist (artist/merge or spotify). "
            "seed_index: 0-based RNG seed track (-1=last). confirm. R1/R3."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "algorithm": {
                    "type": "string",
                    "enum": ["artist", "merge", "spotify"],
                    "default": "artist",
                },
                "seed_index": {
                    "type": "integer",
                    "description": "Seed track index; -1 = last track",
                },
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_playlist_recreate_host",
        "cli": ["device-playlist", "recreate-host"],
        "description": (
            "Create/replace host M3U from device playlist (GUID join; placeholders "
            "for unresolved). confirm."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "host_name": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]
