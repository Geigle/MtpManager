"""Machine-readable tool catalog for ``agent tools`` and MCP registration."""

from __future__ import annotations

from typing import Any

from mtpmanager.headless.tools_phase2 import PHASE2_TOOLS

# Each tool: name, description, host_only, destructive, parameters (JSON-schema-ish).
_BASE_TOOLS: list[dict[str, Any]] = [
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
        "name": "library_set_roots",
        "cli": ["library", "set-roots"],
        "description": (
            "Replace library root paths. Empty list clears roots (requires confirm). "
            "Default rescan=true runs a full scan after updating roots."
        ),
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute library root directories",
                },
                "rescan": {"type": "boolean", "default": True},
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Required when clearing all roots",
                },
            },
            "required": ["roots"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_add_root",
        "cli": ["library", "add-root"],
        "description": "Append one library root directory; rescans by default.",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Absolute directory path"},
                "rescan": {"type": "boolean", "default": True},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_remove_root",
        "cli": ["library", "remove-root"],
        "description": (
            "Remove one library root (untracks media under it). "
            "Removing the last root requires confirm=true."
        ),
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "rescan": {"type": "boolean", "default": True},
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Required when removing the last root",
                },
            },
            "required": ["root"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_scan",
        "cli": ["library", "scan"],
        "description": (
            "Scan library roots and rewrite the SQLite index (GUID preserve). "
            "Optional roots override; default uses configured roots. "
            "Honors durable library exclusions. Blocks until complete."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit roots for this scan only",
                },
            },
            "additionalProperties": False,
        },
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
                    "description": "Max results (default 50, max 5000)",
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
        "name": "playlist_create",
        "cli": ["playlist", "create"],
        "description": (
            "Create an empty host playlist (M3U in library index). "
            "Fails if the name already exists (case-insensitive)."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Playlist name"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_add",
        "cli": ["playlist", "add"],
        "description": (
            "Append library tracks to a host playlist by GUID and/or path. "
            "Unknown GUIDs/paths fail hard. Existing paths are skipped by default "
            "(set skip_existing=false to allow duplicate path entries)."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Existing playlist name"},
                "guids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Track GUIDs (32-hex)",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Host filesystem paths",
                },
                "skip_existing": {
                    "type": "boolean",
                    "default": True,
                    "description": "Skip paths already in the playlist (default true)",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_replace",
        "cli": ["playlist", "replace"],
        "description": (
            "Replace host playlist membership with the given tracks "
            "(order preserved). Passing neither guids nor paths clears the playlist. "
            "Requires confirm=true."
        ),
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Existing playlist name"},
                "guids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Track GUIDs (32-hex), order preserved",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Host filesystem paths, order preserved",
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Required true to replace or clear membership",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_delete",
        "cli": ["playlist", "delete"],
        "description": "Delete a host playlist. Requires confirm=true.",
        "host_only": True,
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
    {
        "name": "playlist_rename",
        "cli": ["playlist", "rename"],
        "description": "Rename a host playlist.",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Current name"},
                "new_name": {"type": "string", "description": "New unique name"},
            },
            "required": ["name", "new_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_remove",
        "cli": ["playlist", "remove"],
        "description": "Remove tracks from a host playlist by GUID and/or path.",
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "guids": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_move",
        "cli": ["playlist", "move"],
        "description": (
            "Move tracks within a host playlist. delta=-1 moves up one slot; "
            "delta=+1 moves down. Host M3U only."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "guids": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}},
                "delta": {
                    "type": "integer",
                    "default": -1,
                    "description": "Negative=up, positive=down",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "playlist_shuffle",
        "cli": ["playlist", "shuffle"],
        "description": (
            "Shuffle host playlist order in place. algorithm=artist (merge) or "
            "spotify. Requires confirm=true. Optional seed_guid for deterministic RNG."
        ),
        "host_only": True,
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
                "seed_guid": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["name"],
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
        "name": "config_patch",
        "cli": ["config", "patch"],
        "description": (
            "Patch allowlisted config keys and save config.json. "
            "Unknown keys fail. stable_mode=true selects mtp-sendtr for sync "
            "(GUI Stable Mode parity). Prefer patch over full file replace."
        ),
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "object",
                    "description": (
                        "Allowlisted keys only: stable_mode, sync_album_art, "
                        "send_format, audio_encode, enable_experimental_tools, "
                        "podcast_*, folder layout flags, …"
                    ),
                },
            },
            "required": ["updates"],
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
        "name": "device_list_known",
        "cli": ["device", "list-known"],
        "description": "List known device serials from the local device index (no USB).",
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
        "name": "device_refresh_index",
        "cli": ["device", "refresh-index"],
        "description": (
            "Full USB list_files and replace the SQLite device cache. "
            "Requires connect. Slow on large libraries; exclusive USB "
            "(quit GUI first). Prefer after quiet reconnect if a prior "
            "transfer hit session poison."
        ),
        "host_only": False,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "device_inventory",
        "cli": ["device", "inventory"],
        "description": (
            "Cached device inventory from SQLite only (no USB walk, no lock). "
            "Supports offset/limit and filters: parent_id, name_contains, guid. "
            "Seed via device refresh-index or GUI connect."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 200},
                "offset": {"type": "integer", "default": 0},
                "parent_id": {"type": "integer"},
                "name_contains": {"type": "string"},
                "guid": {"type": "string", "description": "Match ObjectFileName GUID stem"},
                "serial": {"type": "string", "description": "Device serial; default last known"},
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
    # device_art_probe / device_art_experiment: HeadlessService helpers only
    # (dev experiment). Not agent tools — do not re-add to this catalog.
    {
        "name": "sync_tracks",
        "cli": ["sync"],
        "description": (
            "Sync track(s) by GUID, path, artist/album, host playlist, "
            "entire_library, or path_prefix (host folder). "
            "Remote ObjectFileName is always the track GUID + extension under "
            "Music folder 100 — never pass nested paths. Use dry_run to plan; "
            "confirm=true to send. Default transport is PyMTP (same as GUI when "
            "Stable Mode is off). mode aliases: default|pymtp|experimental "
            "(PyMTP) or stable|cmd (mtp-sendtr recovery only — never silent "
            "fallback). Playlist / entire_library / path_prefix default "
            "batch_size=15 with reconnect-on-fatal for ZEN session poison; "
            "optional push_playlist creates/updates the on-device playlist after send."
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
                "entire_library": {
                    "type": "boolean",
                    "default": False,
                    "description": "Sync all indexed tracks (prefer dry_run first)",
                },
                "path_prefix": {
                    "type": "string",
                    "description": "Host directory; expand to indexed tracks under it",
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
                        "USB batch size; reconnect after fatal (PyMTP). "
                        "Default 15 for playlist/entire_library/path_prefix, "
                        "else 0 (all at once)."
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
                    "enum": [
                        "default",
                        "pymtp",
                        "experimental",
                        "stable",
                        "cmd",
                        "mtp-sendtr",
                    ],
                    "description": (
                        "Omit for config/GUI default (PyMTP). "
                        "stable only when PyMTP is failing."
                    ),
                },
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
]

TOOL_CATALOG: list[dict[str, Any]] = list(_BASE_TOOLS) + list(PHASE2_TOOLS)


def tools_as_dict() -> dict[str, Any]:
    return {"tools": list(TOOL_CATALOG), "count": len(TOOL_CATALOG)}


def catalog_tool_names() -> frozenset[str]:
    """Names exposed to agents via ``agent tools`` / MCP ``tools/list``."""
    return frozenset(str(t["name"]) for t in TOOL_CATALOG)


# Dev-only HeadlessService helpers — must never appear in TOOL_CATALOG.
DEV_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "device_art_probe",
        "device_art_experiment",
    }
)
