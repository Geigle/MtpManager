"""Minimal stdio MCP server (no third-party SDK).

Implements a subset of the MCP JSON-RPC protocol sufficient for tool listing
and tool calls. Prefer the official ``mcp`` package later if you want full
protocol coverage; this keeps MtpManager zero-extra-deps for agents.

Run::

    python -m mtpmanager.mcp_server

Configure clients with command + args pointing at the project venv Python.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from mtpmanager.headless.service import HeadlessService
from mtpmanager.headless.tools import TOOL_CATALOG
from mtpmanager.infra.logging_setup import configure_logging

logger = logging.getLogger("mtpmanager.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mtpmanager"
SERVER_VERSION = "0.1.0"


def _tool_mcp_list() -> list[dict[str, Any]]:
    out = []
    for t in TOOL_CATALOG:
        out.append(
            {
                "name": t["name"],
                "description": t.get("description") or t["name"],
                "inputSchema": t.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return out


def _call_tool(svc: HeadlessService, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    args = arguments or {}
    if name == "agent_doctor":
        result = svc.agent_doctor()
    elif name == "agent_tools":
        result = svc.agent_tools()
    elif name == "library_list_roots":
        result = svc.library_list_roots()
    elif name == "library_search":
        result = svc.library_search(
            str(args.get("query") or ""),
            limit=int(args.get("limit") or 50),
        )
    elif name == "library_track":
        result = svc.library_track(
            guid=args.get("guid"),
            path=args.get("path"),
        )
    elif name == "playlist_list":
        result = svc.playlist_list()
    elif name == "playlist_show":
        result = svc.playlist_show(str(args.get("name") or ""))
    elif name == "config_get":
        result = svc.config_get(args.get("key"))
    elif name == "device_status":
        result = svc.device_status()
    elif name == "device_connect":
        result = svc.device_connect()
    elif name == "device_disconnect":
        result = svc.device_disconnect()
    elif name == "device_info":
        result = svc.device_info()
    elif name == "device_inventory":
        result = svc.device_inventory(limit=int(args.get("limit") or 200))
    elif name == "device_delete":
        result = svc.device_delete(
            int(args.get("object_id") or 0),
            confirm=bool(args.get("confirm")),
        )
    elif name == "sync_tracks":
        batch_raw = args.get("batch_size")
        batch_size = int(batch_raw) if batch_raw is not None else None
        result = svc.sync_tracks(
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
            artist=args.get("artist"),
            album=args.get("album"),
            playlist=args.get("playlist"),
            mode=args.get("mode"),
            dry_run=bool(args.get("dry_run")),
            confirm=bool(args.get("confirm")),
            push_playlist=bool(args.get("push_playlist")),
            batch_size=batch_size,
        )
    elif name == "playlist_push":
        result = svc.playlist_push(
            str(args.get("name") or ""),
            confirm=bool(args.get("confirm")),
        )
    else:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }

    text = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": not result.ok,
    }


def _handle(
    svc: HeadlessService,
    msg: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a JSON-RPC response dict, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id", None)
    params = msg.get("params") or {}

    # Notifications have no id
    is_notification = "id" not in msg

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _tool_mcp_list()},
        }

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            tool_result = _call_tool(svc, name, arguments)
        except Exception as e:
            logger.exception("tools/call failed")
            tool_result = {
                "content": [{"type": "text", "text": str(e)}],
                "isError": True,
            }
        return {"jsonrpc": "2.0", "id": msg_id, "result": tool_result}

    if is_notification:
        return None

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main(argv: list[str] | None = None) -> int:
    try:
        configure_logging()
    except Exception:
        logging.basicConfig(level=logging.INFO)

    svc = HeadlessService()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                err = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
                continue
            if not isinstance(msg, dict):
                continue
            resp = _handle(svc, msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            svc.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
