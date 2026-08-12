"""argparse CLI over :class:`~mtpmanager.headless.service.HeadlessService`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from mtpmanager.headless.dto import AgentResult, ExitCode
from mtpmanager.headless.service import HeadlessService
from mtpmanager.infra.logging_setup import configure_logging


def _print_result(result: AgentResult, *, as_json: bool = True) -> int:
    payload = result.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if result.ok:
            print(result.message or "ok")
            if result.data:
                print(json.dumps(result.data, indent=2, ensure_ascii=False))
        else:
            print(f"{result.code}: {result.message}", file=sys.stderr)
            if result.data:
                print(json.dumps(result.data, indent=2, ensure_ascii=False), file=sys.stderr)
    return int(result.exit_code)


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts: list[str] = []
    for chunk in value.split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def _guid_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--guid",
        action="append",
        default=[],
        dest="guids",
        help="Track GUID (repeatable)",
    )
    parser.add_argument(
        "--guids",
        dest="guids_csv",
        default=None,
        help="Comma-separated GUIDs",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        help="Host file path (repeatable)",
    )
    parser.add_argument(
        "--paths",
        dest="paths_csv",
        default=None,
        help="Comma-separated host paths",
    )


def _collect_guids_paths(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    guids = list(args.guids or [])
    guids.extend(_csv_list(getattr(args, "guids_csv", None)))
    paths = list(args.paths or [])
    paths.extend(_csv_list(getattr(args, "paths_csv", None)))
    return guids, paths


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mtpmanager.cli",
        description=(
            "Headless MtpManager for agents (JSON stdout). "
            "GUI remains: python -m mtpmanager"
        ),
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override app data dir (default: platform data dir / MTP_MANAGER_DATA_DIR)",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG logging on console",
    )
    sub = p.add_subparsers(dest="group", required=True)

    # agent
    agent = sub.add_parser("agent", help="Agent meta commands")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    agent_sub.add_parser("doctor", help="Environment / lock / index diagnostics")
    agent_sub.add_parser("tools", help="Machine-readable tool catalog")

    # library
    lib = sub.add_parser("library", help="Host library (no USB)")
    lib_sub = lib.add_subparsers(dest="action", required=True)
    lib_sub.add_parser("list-roots", help="List library root paths")
    set_roots = lib_sub.add_parser("set-roots", help="Replace library roots")
    set_roots.add_argument(
        "roots",
        nargs="*",
        help="Root directories (empty + --confirm clears)",
    )
    set_roots.add_argument(
        "--no-rescan",
        action="store_true",
        help="Update roots without scanning",
    )
    set_roots.add_argument(
        "--confirm",
        action="store_true",
        help="Required when clearing all roots",
    )
    add_root = lib_sub.add_parser("add-root", help="Append one library root")
    add_root.add_argument("root", help="Absolute directory path")
    add_root.add_argument("--no-rescan", action="store_true")
    rem_root = lib_sub.add_parser("remove-root", help="Remove one library root")
    rem_root.add_argument("root", help="Root path to remove")
    rem_root.add_argument("--no-rescan", action="store_true")
    rem_root.add_argument(
        "--confirm",
        action="store_true",
        help="Required when removing the last root",
    )
    scan = lib_sub.add_parser("scan", help="Scan roots into SQLite index")
    scan.add_argument(
        "--root",
        action="append",
        default=[],
        dest="scan_roots",
        help="Optional root for this scan only (repeatable)",
    )
    search = lib_sub.add_parser("search", help="Fuzzy search tracks")
    search.add_argument("query", help="Search query (supports artist:term etc.)")
    search.add_argument("--limit", type=int, default=50)
    track = lib_sub.add_parser("track", help="Look up one track")
    track.add_argument("--guid", default=None)
    track.add_argument("--path", default=None)

    # playlist
    pl = sub.add_parser("playlist", help="Host playlists / device push")
    pl_sub = pl.add_subparsers(dest="action", required=True)
    pl_sub.add_parser("list", help="List host playlists")
    pl_show = pl_sub.add_parser("show", help="Show playlist paths")
    pl_show.add_argument("name")
    pl_create = pl_sub.add_parser("create", help="Create empty host playlist")
    pl_create.add_argument("name", help="Playlist name (unique, case-insensitive)")
    pl_add = pl_sub.add_parser(
        "add",
        help="Append tracks to a host playlist by GUID and/or path",
    )
    pl_add.add_argument("name", help="Existing playlist name")
    _guid_path_args(pl_add)
    pl_add.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Append even if the path is already in the playlist",
    )
    pl_replace = pl_sub.add_parser(
        "replace",
        help=(
            "Replace host playlist membership (order = arg order); "
            "no tracks clears. Requires --confirm."
        ),
    )
    pl_replace.add_argument("name", help="Existing playlist name")
    _guid_path_args(pl_replace)
    pl_replace.add_argument(
        "--confirm",
        action="store_true",
        help="Required to replace or clear playlist membership",
    )
    pl_del = pl_sub.add_parser("delete", help="Delete host playlist")
    pl_del.add_argument("name")
    pl_del.add_argument("--confirm", action="store_true")
    pl_ren = pl_sub.add_parser("rename", help="Rename host playlist")
    pl_ren.add_argument("name")
    pl_ren.add_argument("new_name")
    pl_rem = pl_sub.add_parser("remove", help="Remove tracks from host playlist")
    pl_rem.add_argument("name")
    _guid_path_args(pl_rem)
    pl_move = pl_sub.add_parser("move", help="Reorder tracks in host playlist")
    pl_move.add_argument("name")
    _guid_path_args(pl_move)
    pl_move.add_argument(
        "--delta",
        type=int,
        default=-1,
        help="Negative=up, positive=down (default -1)",
    )
    pl_shuf = pl_sub.add_parser("shuffle", help="Shuffle host playlist order")
    pl_shuf.add_argument("name")
    pl_shuf.add_argument(
        "--algorithm",
        choices=("artist", "merge", "spotify"),
        default="artist",
    )
    pl_shuf.add_argument("--seed-guid", default=None, dest="seed_guid")
    pl_shuf.add_argument("--confirm", action="store_true")
    pl_push = pl_sub.add_parser("push", help="Push playlist to device")
    pl_push.add_argument("name")
    pl_push.add_argument(
        "--confirm",
        action="store_true",
        help="Required to perform the push",
    )

    # config
    cfg = sub.add_parser("config", help="App config")
    cfg_sub = cfg.add_subparsers(dest="action", required=True)
    cfg_get = cfg_sub.add_parser("get", help="Get config (or one key)")
    cfg_get.add_argument("key", nargs="?", default=None)
    cfg_patch = cfg_sub.add_parser(
        "patch",
        help="Patch allowlisted config keys (JSON object)",
    )
    cfg_patch.add_argument(
        "updates_json",
        help='JSON object, e.g. \'{"stable_mode": true}\'',
    )

    # device
    dev = sub.add_parser("device", help="Device session / inventory")
    dev_sub = dev.add_subparsers(dest="action", required=True)
    dev_sub.add_parser("status", help="Lock + connection status")
    dev_sub.add_parser("list-known", help="Known device serials (cache)")
    dev_sub.add_parser("connect", help="Open PyMTP session (takes session lock)")
    dev_sub.add_parser("disconnect", help="Close session / release lock")
    dev_sub.add_parser("info", help="Device diagnostics (connected)")
    dev_sub.add_parser(
        "refresh-index",
        help="Full list_files → replace device cache (USB; slow)",
    )
    inv = dev_sub.add_parser("inventory", help="Cached inventory (no USB walk)")
    inv.add_argument("--limit", type=int, default=200)
    inv.add_argument("--offset", type=int, default=0)
    inv.add_argument("--parent-id", type=int, default=None, dest="parent_id")
    inv.add_argument("--name-contains", default=None, dest="name_contains")
    inv.add_argument("--guid", default=None)
    inv.add_argument("--serial", default=None)
    delete = dev_sub.add_parser("delete", help="Delete one object by id")
    delete.add_argument("object_id", type=int)
    delete.add_argument("--confirm", action="store_true")
    # Album-art probe/experiment: HeadlessService only (dev). Not CLI tools.

    # sync
    sync = sub.add_parser("sync", help="Transfer tracks to device")
    _guid_path_args(sync)
    sync.add_argument("--artist", default=None, help="Expand to artist tracks")
    sync.add_argument("--album", default=None, help="Filter/expand by album")
    sync.add_argument(
        "--playlist",
        default=None,
        help="Host playlist name (M3U in library index); soft-skips missing paths",
    )
    sync.add_argument(
        "--entire-library",
        action="store_true",
        dest="entire_library",
        help="All indexed tracks (prefer --dry-run first)",
    )
    sync.add_argument(
        "--path-prefix",
        default=None,
        dest="path_prefix",
        help="Host folder; expand to indexed tracks under it",
    )
    sync.add_argument(
        "--push-playlist",
        action="store_true",
        help="After send, create/update on-device playlist (requires --playlist)",
    )
    sync.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help=(
            "USB-friendly batch size with reconnect on fatal "
            "(default: 15 for playlist/entire-library/path-prefix, 0 otherwise)"
        ),
    )
    sync.add_argument(
        "--mode",
        choices=(
            "default",
            "pymtp",
            "experimental",
            "stable",
            "cmd",
            "mtp-sendtr",
        ),
        default=None,
        help=(
            "Transfer transport. Default (omit): same as GUI — PyMTP unless "
            "config Stable Mode is on. Prefer default/pymtp; use stable/cmd "
            "only when the PyMTP path is failing (no silent fallback)."
        ),
    )
    g = sync.add_mutually_exclusive_group()
    g.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only (would-send / would-skip)",
    )
    g.add_argument(
        "--confirm",
        action="store_true",
        help="Execute transfer",
    )

    return p


def dispatch(svc: HeadlessService, args: argparse.Namespace) -> AgentResult:
    group = args.group
    action = getattr(args, "action", None)

    if group == "agent":
        if action == "doctor":
            return svc.agent_doctor()
        if action == "tools":
            return svc.agent_tools()

    if group == "library":
        if action == "list-roots":
            return svc.library_list_roots()
        if action == "set-roots":
            return svc.library_set_roots(
                list(args.roots or []),
                rescan=not bool(getattr(args, "no_rescan", False)),
                confirm=bool(getattr(args, "confirm", False)),
            )
        if action == "add-root":
            return svc.library_add_root(
                args.root,
                rescan=not bool(getattr(args, "no_rescan", False)),
            )
        if action == "remove-root":
            return svc.library_remove_root(
                args.root,
                rescan=not bool(getattr(args, "no_rescan", False)),
                confirm=bool(getattr(args, "confirm", False)),
            )
        if action == "scan":
            roots = list(getattr(args, "scan_roots", None) or [])
            return svc.library_scan(roots=roots or None)
        if action == "search":
            return svc.library_search(args.query, limit=args.limit)
        if action == "track":
            return svc.library_track(guid=args.guid, path=args.path)

    if group == "playlist":
        if action == "list":
            return svc.playlist_list()
        if action == "show":
            return svc.playlist_show(args.name)
        if action == "create":
            return svc.playlist_create(args.name)
        if action == "add":
            guids, paths = _collect_guids_paths(args)
            return svc.playlist_add(
                args.name,
                guids=guids,
                paths=paths,
                skip_existing=not bool(getattr(args, "allow_duplicates", False)),
            )
        if action == "replace":
            guids, paths = _collect_guids_paths(args)
            return svc.playlist_replace(
                args.name,
                guids=guids,
                paths=paths,
                confirm=bool(getattr(args, "confirm", False)),
            )
        if action == "delete":
            return svc.playlist_delete(
                args.name, confirm=bool(getattr(args, "confirm", False))
            )
        if action == "rename":
            return svc.playlist_rename(args.name, args.new_name)
        if action == "remove":
            guids, paths = _collect_guids_paths(args)
            return svc.playlist_remove(args.name, guids=guids, paths=paths)
        if action == "move":
            guids, paths = _collect_guids_paths(args)
            return svc.playlist_move(
                args.name,
                guids=guids,
                paths=paths,
                delta=int(getattr(args, "delta", -1)),
            )
        if action == "shuffle":
            return svc.playlist_shuffle(
                args.name,
                algorithm=str(getattr(args, "algorithm", "artist") or "artist"),
                confirm=bool(getattr(args, "confirm", False)),
                seed_guid=getattr(args, "seed_guid", None),
            )
        if action == "push":
            return svc.playlist_push(args.name, confirm=bool(args.confirm))

    if group == "config":
        if action == "get":
            return svc.config_get(args.key)
        if action == "patch":
            raw = getattr(args, "updates_json", "") or ""
            try:
                updates = json.loads(raw)
            except json.JSONDecodeError as e:
                return AgentResult(
                    ok=False,
                    code="USAGE",
                    message=f"updates must be JSON object: {e}",
                    exit_code=int(ExitCode.USAGE),
                )
            if not isinstance(updates, dict):
                return AgentResult(
                    ok=False,
                    code="USAGE",
                    message="updates must be a JSON object",
                    exit_code=int(ExitCode.USAGE),
                )
            return svc.config_patch(updates)

    if group == "device":
        if action == "status":
            return svc.device_status()
        if action == "list-known":
            return svc.device_list_known()
        if action == "connect":
            return svc.device_connect()
        if action == "disconnect":
            return svc.device_disconnect()
        if action == "info":
            return svc.device_info()
        if action == "refresh-index":
            return svc.device_refresh_index()
        if action == "inventory":
            return svc.device_inventory(
                limit=int(getattr(args, "limit", 200) or 200),
                offset=int(getattr(args, "offset", 0) or 0),
                parent_id=getattr(args, "parent_id", None),
                name_contains=getattr(args, "name_contains", None),
                guid=getattr(args, "guid", None),
                serial=getattr(args, "serial", None),
            )
        if action == "delete":
            return svc.device_delete(args.object_id, confirm=bool(args.confirm))

    if group == "sync":
        guids, paths = _collect_guids_paths(args)
        return svc.sync_tracks(
            guids=guids,
            paths=paths,
            artist=args.artist,
            album=args.album,
            playlist=getattr(args, "playlist", None),
            entire_library=bool(getattr(args, "entire_library", False)),
            path_prefix=getattr(args, "path_prefix", None),
            mode=args.mode,
            dry_run=bool(args.dry_run),
            confirm=bool(args.confirm),
            push_playlist=bool(getattr(args, "push_playlist", False)),
            batch_size=getattr(args, "batch_size", None),
        )

    return AgentResult(
        ok=False,
        code="USAGE",
        message=f"Unknown command: {group} {action}",
        exit_code=int(ExitCode.USAGE),
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2

    if args.verbose:
        import os

        os.environ.setdefault("MTP_MANAGER_DEBUG", "1")
    try:
        configure_logging()
    except Exception:
        logging.basicConfig(level=logging.INFO)

    svc = HeadlessService(data_dir=args.data_dir)
    try:
        result = dispatch(svc, args)
        return _print_result(result, as_json=True)
    finally:
        # Keep lock if still connected (device connect without disconnect).
        # Only release when not holding an intentional session.
        if not svc._connected:
            svc.close()
        # If connected, leave lock for multi-command shell scripts that reconnect?
        # Single-shot CLI: disconnect on exit unless user called connect intentionally
        # and process ends — always release USB on process exit.
        else:
            try:
                svc.device_disconnect()
            except Exception:
                svc.close()
