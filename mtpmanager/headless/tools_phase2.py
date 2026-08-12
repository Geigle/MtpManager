"""Phase 2 (Milestone C) tool catalog entries."""

from __future__ import annotations

from typing import Any

PHASE2_TOOLS: list[dict[str, Any]] = [
    {
        "name": "podcast_list",
        "cli": ["podcast", "list"],
        "description": "List subscribed podcasts (host index; no USB).",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "podcast_show",
        "cli": ["podcast", "show"],
        "description": "Show one podcast by id or exact title.",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "podcast_id": {"type": "integer"},
                "title": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_episodes",
        "cli": ["podcast", "episodes"],
        "description": "List episodes for a podcast (newest first).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "podcast_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["podcast_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_subscribe",
        "cli": ["podcast", "subscribe"],
        "description": "Subscribe to an RSS feed URL (host index).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "feed_url": {"type": "string"},
                "initial_limit": {"type": "integer", "default": 20},
            },
            "required": ["feed_url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_unsubscribe",
        "cli": ["podcast", "unsubscribe"],
        "description": "Delete a podcast subscription. Requires confirm=true.",
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "podcast_id": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["podcast_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_refresh",
        "cli": ["podcast", "refresh"],
        "description": "Re-fetch RSS and insert new episodes (host only).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {"podcast_id": {"type": "integer"}},
            "required": ["podcast_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_download_episode",
        "cli": ["podcast", "download"],
        "description": "Download one episode enclosure to the podcasts cache (R4).",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "integer"},
                "prefer_video": {"type": "boolean", "default": False},
            },
            "required": ["episode_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_full_sync_host",
        "cli": ["podcast", "full-sync-host"],
        "description": (
            "Scheduled-style host pass: refresh feeds, download up to N new "
            "episodes per show, mark pending. No USB / no day playlist auto-push."
        ),
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "podcast_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "max_new_per_show": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_day_playlist_show",
        "cli": ["podcast", "day-show"],
        "description": "Show today's Podcasts {Mon} {D}, {YYYY} host playlist.",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "podcast_day_add",
        "cli": ["podcast", "day-add"],
        "description": "Add episode to today's host day playlist by episode_id or guid.",
        "host_only": True,
        "destructive": False,
        "parameters": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "integer"},
                "guid": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_day_remove",
        "cli": ["podcast", "day-remove"],
        "description": "Remove episode GUID from today's host day playlist.",
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {"guid": {"type": "string"}},
            "required": ["guid"],
            "additionalProperties": False,
        },
    },
    {
        "name": "podcast_sync_pending",
        "cli": ["podcast", "sync-pending"],
        "description": (
            "Transfer pending podcast episodes to device (PyMTP default). "
            "Risks R1/R3/R4. Optional push_day_playlist = Finish Sync semantics "
            "(never auto after host flood alone)."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
                "mode": {"type": "string"},
                "batch_size": {"type": "integer"},
                "push_day_playlist": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_pull",
        "cli": ["device", "pull"],
        "description": (
            "Download object id(s) to library root or dest. Requires confirm. "
            "Risks R3 USB exclusive, R4 large download. Prefer host library."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "dest": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["object_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_enrich_tags",
        "cli": ["device", "enrich-tags"],
        "description": (
            "HAZARDOUS: Get_Trackmetadata (+ download/mutagen fallback) for object ids. "
            "R1 session poison, R2 hang — NOT for inventory. Max 25 ids/call. "
            "Requires confirm. Prefer host tags + GUID ObjectFileName. "
            "On poison: disconnect, quiet, reconnect, refresh-index."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["object_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "device_send_video",
        "cli": ["device", "send-video"],
        "description": (
            "Send host video to Video (120) or TV (124). Optional encode for ZEN. "
            "Risks R1/R3/R4 (encode time). dry_run or confirm required."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "parent_id": {
                    "type": "integer",
                    "default": 120,
                    "description": "120=Video, 124=TV",
                },
                "encode": {"type": "boolean", "default": True},
                "preset_id": {"type": "string", "default": "zen_avi_xvid_mp3"},
                "title": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_job_status",
        "cli": ["sync-job", "status"],
        "description": "Show durable multi-track sync job (resume state).",
        "host_only": True,
        "destructive": False,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "sync_job_clear",
        "cli": ["sync-job", "clear"],
        "description": "Clear durable sync job file. Requires confirm.",
        "host_only": True,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "default": False}},
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_resume",
        "cli": ["sync-job", "resume"],
        "description": (
            "Resume failed/cancelled sync job remaining paths. Risk R1. "
            "Prefer dry_run first. Fatal abort still applies within a batch."
        ),
        "host_only": False,
        "destructive": True,
        "parameters": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
                "batch_size": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
]
