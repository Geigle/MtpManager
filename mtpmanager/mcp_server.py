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
from typing import Any, Callable

from mtpmanager.headless.dto import AgentResult
from mtpmanager.headless.service import HeadlessService
from mtpmanager.headless.tools import TOOL_CATALOG
from mtpmanager.infra.logging_setup import configure_logging

logger = logging.getLogger("mtpmanager.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mtpmanager"
SERVER_VERSION = "0.1.0"

# name -> (svc, arguments) -> AgentResult
ToolHandler = Callable[[HeadlessService, dict[str, Any]], AgentResult]


def _handlers() -> dict[str, ToolHandler]:
    """Single registry for MCP tool dispatch (parity-tested vs TOOL_CATALOG)."""

    def agent_doctor(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.agent_doctor()

    def agent_tools(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.agent_tools()

    def library_list_roots(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.library_list_roots()

    def library_set_roots(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        rescan_raw = args.get("rescan")
        rescan = True if rescan_raw is None else bool(rescan_raw)
        return svc.library_set_roots(
            list(args.get("roots") or []),
            rescan=rescan,
            confirm=bool(args.get("confirm")),
        )

    def library_add_root(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        rescan_raw = args.get("rescan")
        rescan = True if rescan_raw is None else bool(rescan_raw)
        return svc.library_add_root(str(args.get("root") or ""), rescan=rescan)

    def library_remove_root(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        rescan_raw = args.get("rescan")
        rescan = True if rescan_raw is None else bool(rescan_raw)
        return svc.library_remove_root(
            str(args.get("root") or ""),
            rescan=rescan,
            confirm=bool(args.get("confirm")),
        )

    def library_scan(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        roots = args.get("roots")
        return svc.library_scan(roots=list(roots) if roots else None)

    def library_search(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.library_search(
            str(args.get("query") or ""),
            limit=int(args.get("limit") or 50),
        )

    def library_track(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.library_track(
            guid=args.get("guid"),
            path=args.get("path"),
        )

    def playlist_list(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.playlist_list()

    def playlist_show(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_show(str(args.get("name") or ""))

    def playlist_create(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_create(str(args.get("name") or ""))

    def playlist_add(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        skip_raw = args.get("skip_existing")
        skip_existing = True if skip_raw is None else bool(skip_raw)
        return svc.playlist_add(
            str(args.get("name") or ""),
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
            skip_existing=skip_existing,
        )

    def playlist_replace(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_replace(
            str(args.get("name") or ""),
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
            confirm=bool(args.get("confirm")),
        )

    def playlist_delete(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_delete(
            str(args.get("name") or ""),
            confirm=bool(args.get("confirm")),
        )

    def playlist_rename(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_rename(
            str(args.get("name") or ""),
            str(args.get("new_name") or ""),
        )

    def playlist_remove(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_remove(
            str(args.get("name") or ""),
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
        )

    def playlist_move(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        delta_raw = args.get("delta")
        delta = -1 if delta_raw is None else int(delta_raw)
        return svc.playlist_move(
            str(args.get("name") or ""),
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
            delta=delta,
        )

    def playlist_shuffle(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_shuffle(
            str(args.get("name") or ""),
            algorithm=str(args.get("algorithm") or "artist"),
            confirm=bool(args.get("confirm")),
            seed_guid=args.get("seed_guid"),
        )

    def playlist_push(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.playlist_push(
            str(args.get("name") or ""),
            confirm=bool(args.get("confirm")),
        )

    def config_get(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.config_get(args.get("key"))

    def config_patch(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        updates = args.get("updates")
        if not isinstance(updates, dict):
            updates = {}
        return svc.config_patch(updates)

    def device_status(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_status()

    def device_list_known(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_list_known()

    def device_connect(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_connect()

    def device_disconnect(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_disconnect()

    def device_info(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_info()

    def device_refresh_index(svc: HeadlessService, _args: dict[str, Any]) -> AgentResult:
        return svc.device_refresh_index()

    def device_inventory(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        parent_raw = args.get("parent_id")
        parent_id = int(parent_raw) if parent_raw is not None else None
        return svc.device_inventory(
            limit=int(args.get("limit") or 200),
            offset=int(args.get("offset") or 0),
            parent_id=parent_id,
            name_contains=args.get("name_contains"),
            guid=args.get("guid"),
            serial=args.get("serial"),
        )

    def device_delete(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        return svc.device_delete(
            int(args.get("object_id") or 0),
            confirm=bool(args.get("confirm")),
        )

    def sync_tracks(svc: HeadlessService, args: dict[str, Any]) -> AgentResult:
        batch_raw = args.get("batch_size")
        batch_size = int(batch_raw) if batch_raw is not None else None
        return svc.sync_tracks(
            guids=list(args.get("guids") or []),
            paths=list(args.get("paths") or []),
            artist=args.get("artist"),
            album=args.get("album"),
            playlist=args.get("playlist"),
            entire_library=bool(args.get("entire_library")),
            path_prefix=args.get("path_prefix"),
            mode=args.get("mode"),
            dry_run=bool(args.get("dry_run")),
            confirm=bool(args.get("confirm")),
            push_playlist=bool(args.get("push_playlist")),
            batch_size=batch_size,
        )

    return {
        "agent_doctor": agent_doctor,
        "agent_tools": agent_tools,
        "library_list_roots": library_list_roots,
        "library_set_roots": library_set_roots,
        "library_add_root": library_add_root,
        "library_remove_root": library_remove_root,
        "library_scan": library_scan,
        "library_search": library_search,
        "library_track": library_track,
        "playlist_list": playlist_list,
        "playlist_show": playlist_show,
        "playlist_create": playlist_create,
        "playlist_add": playlist_add,
        "playlist_replace": playlist_replace,
        "playlist_delete": playlist_delete,
        "playlist_rename": playlist_rename,
        "playlist_remove": playlist_remove,
        "playlist_move": playlist_move,
        "playlist_shuffle": playlist_shuffle,
        "playlist_push": playlist_push,
        "config_get": config_get,
        "config_patch": config_patch,
        "device_status": device_status,
        "device_list_known": device_list_known,
        "device_connect": device_connect,
        "device_disconnect": device_disconnect,
        "device_info": device_info,
        "device_refresh_index": device_refresh_index,
        "device_inventory": device_inventory,
        "device_delete": device_delete,
        "sync_tracks": sync_tracks,
    }


def implemented_mcp_tool_names() -> frozenset[str]:
    """Tool names MCP can invoke (must match :data:`TOOL_CATALOG`)."""
    return frozenset(_handlers().keys())


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
    handler = _handlers().get(name)
    if handler is None:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }
    result = handler(svc, args)
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
