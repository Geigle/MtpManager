"""Machine-readable tool catalog for ``agent tools`` and MCP registration."""

from __future__ import annotations

from typing import Any

# Each tool: name, description, host_only, destructive, parameters (JSON-schema-ish).
TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "agent_doctor",
        "cli": ["agent", "doctor"],
        "description": "Environment check: data dir, logs, lock, ffmpeg, library index.",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "agent_tools",
        "cli": ["agent", "tools"],
        "description": "List available agent tools with schemas (this catalog).",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_list_roots",
        "cli": ["library", "list-roots"],
        "description": "List configured library root paths from the SQLite index.",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "library_search",
        "cli": ["library", "search"],
        "description": (
            "Fuzzy search the host library. Supports field:term boosts "
            "(artist:, album:, title:, genre:, path:). Returns GUID + tags."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                    "default": 50,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_track",
        "cli": ["library", "track"],
        "description": "Look up one host track by GUID or filesystem path.",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "guid": {"type": "string"},
                "path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_list",
        "cli": ["playlist", "list"],
        "description": "List host playlists (M3U in library index DB).",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "playlist_show",
        "cli": ["playlist", "show"],
        "description": "Show one host playlist by name (paths + count).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "config_get",
        "cli": ["config", "get"],
        "description": "Read app config (send format, stable_mode, etc.).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Optional single key; omit for full config",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_status",
        "cli": ["device", "status"],
        "description": "Cross-process lock state and whether this process holds a session.",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_connect",
        "cli": ["device", "connect"],
        "description": "Open Experimental (PyMTP) session. Acquires device session lock.",
        "host_only": False,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_disconnect",
        "cli": ["device", "disconnect"],
        "description": "Close device session and release lock if held by this process.",
        "host_only": False,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_info",
        "cli": ["device", "info"],
        "description": "Device identity / diagnostics (requires connect).",
        "host_only": False,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_inventory",
        "cli": ["device", "inventory"],
        "description": (
            "List cached device inventory from SQLite (no full USB walk). "
            "Prefer this over live listings."
        ),
        "host_only": False,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_delete",
        "cli": ["device", "delete"],
        "description": (
            "Delete one object by MTP item id. Requires confirm=true. "
            "No bulk delete via agent API."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["object_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_tracks",
        "cli": ["sync"],
        "description": (
            "Sync track(s) by GUID, path, artist/album, or host playlist name. "
            "Remote ObjectFileName is always the track GUID + extension under "
            "Music folder 100 — never pass nested paths. Use dry_run to plan; "
            "confirm=true to send. mode: experimental (PyMTP) or stable "
            "(mtp-sendtr). Playlist sync defaults to batch_size=15 with "
            "reconnect-on-fatal for ZEN session poison; optional "
            "push_playlist creates/updates the on-device playlist after send."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "guids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Track GUIDs (32-hex)",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "playlist": {
                    "type": "string",
                    "description": (
                        "Host playlist name (M3U in library index). "
                        "Missing paths are soft-skipped and listed as unresolved_paths."
                    ),
                },
                "push_playlist": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "After sends, push host playlist to device "
                        "(requires playlist)."
                    ),
                },
                "batch_size": {
                    "type": "integer",
                    "description": (
                        "USB batch size; reconnect after fatal (Experimental). "
                        "Default 15 when playlist is set, else 0 (all at once)."
                    ),
                },
                "album": {
                    "type": "string",
                    "description": "If set with artist, expand to album tracks",
                },
                "artist": {
                    "type": "string",
                    "description": "Expand to all tracks by artist (or with album)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["experimental", "stable"],
                },
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_push",
        "cli": ["playlist", "push"],
        "description": (
            "Push a host playlist to the device (GUID→item_id). Requires confirm=true."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


def tools_as_dict() -> dict[str, Any]:
    return {"tools": list(TOOL_CATALOG), "count": len(TOOL_CATALOG)}
