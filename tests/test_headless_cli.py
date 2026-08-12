"""Headless service + CLI tests (no live device)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mtpmanager.cli.main import main
from mtpmanager.domain.library import Library
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_m3u import (
    PlaylistEntry,
    entry_from_track,
    serialize_m3u,
)
from mtpmanager.domain.track_id import new_track_guid
from mtpmanager.headless.dto import ExitCode
from mtpmanager.headless.service import (
    DEFAULT_PLAYLIST_BATCH_SIZE,
    HeadlessService,
    normalize_transfer_mode,
)
from mtpmanager.infra.device_index import record_send
from mtpmanager.infra.library_index import save_library_index
from mtpmanager.infra.playlists import create_playlist, set_playlist_m3u


def _track(path: str, **meta_kw) -> Track:
    defaults = dict(
        artist="Nightwish",
        album="Once",
        title="Nemo",
        tracknumber="04",
        length_sec=260.0,
    )
    defaults.update(meta_kw)
    guid = defaults.pop("guid", "") or new_track_guid()
    return Track(path=path, meta=TrackMetadata(**defaults), guid=guid)


class HeadlessServiceTests(unittest.TestCase):
    def test_normalize_transfer_mode_aliases(self) -> None:
        self.assertIsNone(normalize_transfer_mode(None))
        self.assertIsNone(normalize_transfer_mode(""))
        self.assertEqual(normalize_transfer_mode("default"), "experimental")
        self.assertEqual(normalize_transfer_mode("pymtp"), "experimental")
        self.assertEqual(normalize_transfer_mode("experimental"), "experimental")
        self.assertEqual(normalize_transfer_mode("stable"), "stable")
        self.assertEqual(normalize_transfer_mode("cmd"), "stable")
        self.assertEqual(normalize_transfer_mode("mtp-sendtr"), "stable")
        self.assertEqual(normalize_transfer_mode("nope"), "")

    def test_sync_defaults_to_pymtp_like_gui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "a.mp3"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g)],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")
            svc = HeadlessService(data_dir=data)
            plan = svc.sync_tracks(guids=[g], dry_run=True)
            self.assertTrue(plan.ok)
            # Wire value stays "experimental" (GUI active_mode) when Stable is off.
            self.assertEqual(plan.data["mode"], "experimental")
            plan_alias = svc.sync_tracks(guids=[g], dry_run=True, mode="default")
            self.assertTrue(plan_alias.ok)
            self.assertEqual(plan_alias.data["mode"], "experimental")
            plan_stable = svc.sync_tracks(guids=[g], dry_run=True, mode="cmd")
            self.assertTrue(plan_stable.ok)
            self.assertEqual(plan_stable.data["mode"], "stable")

    def test_doctor_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "nemo.flac"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g, title="Nemo", artist="Nightwish")],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")

            svc = HeadlessService(data_dir=data)
            doc = svc.agent_doctor()
            self.assertTrue(doc.ok)
            self.assertEqual(doc.data["track_count"], 1)
            self.assertEqual(doc.data["library_roots"], [str(root)])

            roots = svc.library_list_roots()
            self.assertTrue(roots.ok)
            self.assertEqual(roots.data["roots"], [str(root)])

            search = svc.library_search("artist:nightwish")
            self.assertTrue(search.ok)
            self.assertGreaterEqual(search.data["total_matched"], 1)
            self.assertEqual(search.data["tracks"][0]["guid"], g)

            track = svc.library_track(guid=g)
            self.assertTrue(track.ok)
            self.assertEqual(track.data["track"]["title"], "Nemo")

            tools = svc.agent_tools()
            self.assertTrue(tools.ok)
            self.assertGreater(tools.data["count"], 5)

            cfg = svc.config_get()
            self.assertTrue(cfg.ok)
            self.assertIn("config", cfg.data)

            status = svc.device_status()
            self.assertTrue(status.ok)
            self.assertFalse(status.data["connected"])

    def test_sync_dry_run_and_confirm_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "a.mp3"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g)],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")
            svc = HeadlessService(data_dir=data)

            plan = svc.sync_tracks(guids=[g], dry_run=True)
            self.assertTrue(plan.ok)
            self.assertEqual(plan.data["track_count"], 1)
            self.assertEqual(plan.data["tracks"][0]["action"], "send")

            gated = svc.sync_tracks(guids=[g], confirm=False, dry_run=False)
            self.assertFalse(gated.ok)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))

    def test_sync_playlist_dry_run_and_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f2 = root / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            g1 = new_track_guid()
            g2 = new_track_guid()
            t1 = _track(str(f1), guid=g1, title="A")
            t2 = _track(str(f2), guid=g2, title="B")
            lib = Library(tracks=[t1, t2], root_paths=[str(root)])
            index = data / "library_index.db"
            save_library_index(lib, path=index)

            missing = str(root / "gone.mp3")
            pl = create_playlist("Rock", path=index)
            body = serialize_m3u(
                [
                    entry_from_track(t1),
                    entry_from_track(t2),
                    PlaylistEntry(path=missing),  # not in library
                ]
            )
            set_playlist_m3u(pl.id, body, path=index)

            # Mark g1 already on device so dry-run skips it.
            record_send(
                "TESTSERIAL",
                remote_name=f"{g1}.mp3",
                guid=g1,
                item_id=1001,
                path=index,
            )

            svc = HeadlessService(data_dir=data)
            plan = svc.sync_tracks(playlist="Rock", dry_run=True)
            self.assertTrue(plan.ok)
            self.assertEqual(plan.data["playlist"], "Rock")
            self.assertEqual(plan.data["track_count"], 2)
            self.assertEqual(plan.data["would_skip"], 1)
            self.assertEqual(plan.data["would_send"], 1)
            self.assertEqual(plan.data["batch_size"], DEFAULT_PLAYLIST_BATCH_SIZE)
            self.assertEqual(plan.data["unresolved_count"], 1)
            self.assertIn(missing, plan.data["unresolved_paths"])
            actions = {p["guid"]: p["action"] for p in plan.data["tracks"]}
            self.assertEqual(actions[g1], "skip")
            self.assertEqual(actions[g2], "send")

            gated = svc.sync_tracks(playlist="Rock", confirm=False)
            self.assertFalse(gated.ok)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))

            missing_pl = svc.sync_tracks(playlist="NoSuchList", dry_run=True)
            self.assertFalse(missing_pl.ok)
            self.assertEqual(missing_pl.exit_code, int(ExitCode.NOT_FOUND))

    def test_sync_playlist_confirm_wires_record_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "a.mp3"
            f.write_bytes(b"x")
            g = new_track_guid()
            t = _track(str(f), guid=g, title="A")
            index = data / "library_index.db"
            save_library_index(
                Library(tracks=[t], root_paths=[str(root)]), path=index
            )
            pl = create_playlist("Tiny", path=index)
            set_playlist_m3u(pl.id, serialize_m3u([entry_from_track(t)]), path=index)

            svc = HeadlessService(data_dir=data)
            # Avoid real USB: mock lock, connect, and transfer.
            self.assertTrue(svc._session_lock.try_acquire("cli-sync"))
            svc._connected = True
            svc._device = MagicMock()
            svc._device_serial = "TESTSERIAL"

            def fake_transfer(tracks, **kwargs):
                on_after = kwargs.get("on_after_send")
                on_status = kwargs.get("on_track_status")
                for tr in tracks:
                    if on_status:
                        on_status(tr.path, "sending")
                    if on_after:
                        on_after(tr.guid, tr.path, 4242)
                    if on_status:
                        on_status(tr.path, "done")
                return len(list(tracks))

            with patch(
                "mtpmanager.headless.service.transfer_tracks",
                side_effect=fake_transfer,
            ):
                result = svc.sync_tracks(
                    playlist="Tiny",
                    confirm=True,
                    mode="experimental",
                    batch_size=0,
                    push_playlist=False,
                )
            self.assertTrue(result.ok, result.message)
            self.assertEqual(result.data["succeeded"], 1)
            self.assertEqual(result.data["to_send"], 1)

            # record_send should have written the GUID into the device index.
            from mtpmanager.infra.device_index import guid_stems_on_device

            stems = set(guid_stems_on_device("TESTSERIAL", path=index) or [])
            self.assertIn(g, stems)

            # Second dry-run should skip the track now on device.
            plan2 = svc.sync_tracks(playlist="Tiny", dry_run=True)
            self.assertTrue(plan2.ok)
            self.assertEqual(plan2.data["would_skip"], 1)
            self.assertEqual(plan2.data["would_send"], 0)

    def test_device_busy_on_connect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            holder = HeadlessService(data_dir=data)
            # Simulate GUI holding lock without real USB.
            self.assertTrue(holder._session_lock.try_acquire("gui"))
            other = HeadlessService(data_dir=data)
            # Avoid real pymtp connect: mock after lock check by pre-failing lock.
            r = other.device_connect()
            self.assertFalse(r.ok)
            self.assertEqual(r.code, "DEVICE_BUSY")
            self.assertEqual(r.exit_code, int(ExitCode.DEVICE_BUSY))
            holder._session_lock.release("gui")


class PlaylistMutationTests(unittest.TestCase):
    def test_create_add_replace_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f1 = root / "a.mp3"
            f2 = root / "b.mp3"
            f1.write_bytes(b"x")
            f2.write_bytes(b"y")
            g1 = new_track_guid()
            g2 = new_track_guid()
            t1 = _track(str(f1), guid=g1, title="A")
            t2 = _track(str(f2), guid=g2, title="B")
            save_library_index(
                Library(tracks=[t1, t2], root_paths=[str(root)]),
                path=data / "library_index.db",
            )
            svc = HeadlessService(data_dir=data)

            created = svc.playlist_create("Hansi")
            self.assertTrue(created.ok)
            self.assertEqual(created.data["name"], "Hansi")
            self.assertEqual(created.data["track_count"], 0)

            conflict = svc.playlist_create("hansi")
            self.assertFalse(conflict.ok)
            self.assertEqual(conflict.code, "CONFLICT")

            missing = svc.playlist_add("Hansi", guids=["deadbeefdeadbeefdeadbeefdeadbeef"])
            self.assertFalse(missing.ok)
            self.assertEqual(missing.exit_code, int(ExitCode.NOT_FOUND))

            empty_add = svc.playlist_add("Hansi")
            self.assertFalse(empty_add.ok)
            self.assertEqual(empty_add.exit_code, int(ExitCode.USAGE))

            add1 = svc.playlist_add("Hansi", guids=[g1])
            self.assertTrue(add1.ok)
            self.assertEqual(add1.data["added"], 1)
            self.assertEqual(add1.data["track_count"], 1)
            self.assertEqual(add1.data["paths"], [str(f1)])

            add_again = svc.playlist_add("Hansi", guids=[g1, g2])
            self.assertTrue(add_again.ok)
            self.assertEqual(add_again.data["added"], 1)
            self.assertEqual(add_again.data["skipped_existing"], 1)
            self.assertEqual(add_again.data["track_count"], 2)

            shown = svc.playlist_show("Hansi")
            self.assertTrue(shown.ok)
            self.assertEqual(shown.data["track_count"], 2)

            gated = svc.playlist_replace("Hansi", guids=[g2], confirm=False)
            self.assertFalse(gated.ok)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))
            self.assertEqual(gated.code, "CONFIRM_REQUIRED")

            replaced = svc.playlist_replace("Hansi", guids=[g2], confirm=True)
            self.assertTrue(replaced.ok)
            self.assertEqual(replaced.data["track_count"], 1)
            self.assertEqual(replaced.data["paths"], [str(f2)])
            self.assertEqual(replaced.data["replaced_with"], 1)

            cleared = svc.playlist_replace("Hansi", confirm=True)
            self.assertTrue(cleared.ok)
            self.assertEqual(cleared.data["track_count"], 0)

            not_found = svc.playlist_add("Nope", guids=[g1])
            self.assertFalse(not_found.ok)
            self.assertEqual(not_found.exit_code, int(ExitCode.NOT_FOUND))

    def test_cli_playlist_create_and_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "x.flac"
            f.write_bytes(b"x")
            g = new_track_guid()
            save_library_index(
                Library(
                    tracks=[_track(str(f), guid=g, title="Song")],
                    root_paths=[str(root)],
                ),
                path=data / "library_index.db",
            )
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    ["--data-dir", str(data), "playlist", "create", "Road Mix"]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["name"], "Road Mix")

            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                code2 = main(
                    [
                        "--data-dir",
                        str(data),
                        "playlist",
                        "add",
                        "Road Mix",
                        "--guid",
                        g,
                    ]
                )
            self.assertEqual(code2, 0)
            payload2 = json.loads(buf2.getvalue())
            self.assertTrue(payload2["ok"])
            self.assertEqual(payload2["data"]["added"], 1)
            self.assertEqual(payload2["data"]["track_count"], 1)

            buf_gate = io.StringIO()
            with redirect_stdout(buf_gate):
                code_gate = main(
                    [
                        "--data-dir",
                        str(data),
                        "playlist",
                        "replace",
                        "Road Mix",
                        "--guids",
                        g,
                    ]
                )
            self.assertEqual(code_gate, int(ExitCode.CONFIRM_REQUIRED))
            payload_gate = json.loads(buf_gate.getvalue())
            self.assertFalse(payload_gate["ok"])
            self.assertEqual(payload_gate["code"], "CONFIRM_REQUIRED")

            buf3 = io.StringIO()
            with redirect_stdout(buf3):
                code3 = main(
                    [
                        "--data-dir",
                        str(data),
                        "playlist",
                        "replace",
                        "Road Mix",
                        "--guids",
                        g,
                        "--confirm",
                    ]
                )
            self.assertEqual(code3, 0)
            payload3 = json.loads(buf3.getvalue())
            self.assertTrue(payload3["ok"])
            self.assertEqual(payload3["data"]["track_count"], 1)


class AgentSurfaceParityTests(unittest.TestCase):
    """Catalog must match MCP dispatch; dev experiments stay off agent surfaces."""

    def test_catalog_matches_mcp_handlers(self) -> None:
        from mtpmanager.headless.tools import (
            DEV_ONLY_TOOL_NAMES,
            catalog_tool_names,
        )
        from mtpmanager.mcp_server import implemented_mcp_tool_names

        catalog = catalog_tool_names()
        mcp = implemented_mcp_tool_names()
        self.assertEqual(
            catalog,
            mcp,
            msg=(
                f"catalog-only={sorted(catalog - mcp)!r} "
                f"mcp-only={sorted(mcp - catalog)!r}"
            ),
        )
        self.assertTrue(catalog.isdisjoint(DEV_ONLY_TOOL_NAMES))

    def test_catalog_cli_paths_are_registered(self) -> None:
        from mtpmanager.cli.main import build_parser
        from mtpmanager.headless.tools import TOOL_CATALOG

        parser = build_parser()
        for tool in TOOL_CATALOG:
            cli = list(tool.get("cli") or [])
            self.assertGreaterEqual(
                len(cli),
                1,
                msg=f"{tool['name']} missing cli path",
            )
            # sync is a top-level group with no sub-action.
            if cli == ["sync"]:
                args = parser.parse_args(["sync", "--dry-run"])
                self.assertEqual(args.group, "sync")
                continue
            if len(cli) == 1:
                args = parser.parse_args(cli)
                self.assertEqual(args.group, cli[0])
                continue
            group, action = cli[0], cli[1]
            argv = [group, action]
            # Satisfy required positionals / flags so parse succeeds.
            if group == "library" and action == "search":
                argv.append("q")
            elif group == "library" and action == "add-root":
                argv.append("/tmp")
            elif group == "library" and action == "remove-root":
                argv.extend(["/tmp", "--confirm"])
            elif group == "library" and action == "set-roots":
                argv.append("--confirm")
            elif group == "playlist" and action in (
                "show",
                "create",
                "add",
                "replace",
                "push",
                "delete",
                "remove",
                "move",
                "shuffle",
            ):
                argv.append("Name")
            elif group == "playlist" and action == "rename":
                argv.extend(["Old", "New"])
            elif group == "config" and action == "patch":
                argv.append("{}")
            elif group == "device" and action == "delete":
                argv.extend(["1", "--confirm"])
            if group == "playlist" and action in (
                "replace",
                "push",
                "delete",
                "shuffle",
            ):
                argv.append("--confirm")
            args = parser.parse_args(argv)
            self.assertEqual(args.group, group)
            self.assertEqual(getattr(args, "action", None), action)

    def test_art_experiment_not_on_cli(self) -> None:
        from mtpmanager.cli.main import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["device", "art-probe"])
        self.assertNotEqual(ctx.exception.code, 0)
        with self.assertRaises(SystemExit) as ctx2:
            parser.parse_args(
                ["device", "art-experiment", "--path", "/tmp/x", "--confirm"]
            )
        self.assertNotEqual(ctx2.exception.code, 0)

    def test_agent_tools_omits_dev_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = HeadlessService(data_dir=Path(tmp))
            tools = svc.agent_tools()
            self.assertTrue(tools.ok)
            names = {t["name"] for t in tools.data["tools"]}
            self.assertNotIn("device_art_probe", names)
            self.assertNotIn("device_art_experiment", names)


class CliMainTests(unittest.TestCase):
    def test_cli_doctor_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--data-dir", str(data), "agent", "doctor"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["data_dir"], str(data))

    def test_cli_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "x.flac"
            f.write_bytes(b"x")
            g = new_track_guid()
            lib = Library(
                tracks=[_track(str(f), guid=g, title="Countdown")],
                root_paths=[str(root)],
            )
            save_library_index(lib, path=data / "library_index.db")
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    ["--data-dir", str(data), "library", "search", "countdown"]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertGreaterEqual(payload["data"]["total_matched"], 1)

    def test_cli_sync_playlist_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "x.mp3"
            f.write_bytes(b"x")
            g = new_track_guid()
            t = _track(str(f), guid=g, title="Song")
            index = data / "library_index.db"
            save_library_index(
                Library(tracks=[t], root_paths=[str(root)]), path=index
            )
            pl = create_playlist("Road", path=index)
            set_playlist_m3u(pl.id, serialize_m3u([entry_from_track(t)]), path=index)

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--data-dir",
                        str(data),
                        "sync",
                        "--playlist",
                        "Road",
                        "--dry-run",
                        "--mode",
                        "experimental",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["playlist"], "Road")
            self.assertEqual(payload["data"]["would_send"], 1)
            self.assertEqual(
                payload["data"]["batch_size"], DEFAULT_PLAYLIST_BATCH_SIZE
            )


class MilestoneBLibraryConfigTests(unittest.TestCase):
    def test_scan_add_root_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            f = root / "song.mp3"
            f.write_bytes(b"ID3")
            svc = HeadlessService(data_dir=data)
            added = svc.library_add_root(str(root), rescan=True)
            self.assertTrue(added.ok, msg=added.message)
            self.assertGreaterEqual(added.data["track_count"], 1)
            search = svc.library_search("song")
            self.assertTrue(search.ok)
            self.assertGreaterEqual(search.data["total_matched"], 1)
            roots = svc.library_list_roots()
            self.assertEqual(roots.data["roots"], [str(root)])

    def test_remove_last_root_requires_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            (root / "a.mp3").write_bytes(b"x")
            svc = HeadlessService(data_dir=data)
            self.assertTrue(svc.library_add_root(str(root)).ok)
            gated = svc.library_remove_root(str(root), confirm=False)
            self.assertFalse(gated.ok)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))
            cleared = svc.library_remove_root(str(root), confirm=True, rescan=False)
            self.assertTrue(cleared.ok)
            self.assertEqual(cleared.data["roots"], [])

    def test_config_patch_allowlist_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            svc = HeadlessService(data_dir=data)
            bad = svc.config_patch({"not_a_key": True})
            self.assertFalse(bad.ok)
            self.assertEqual(bad.exit_code, int(ExitCode.USAGE))
            patched = svc.config_patch(
                {"stable_mode": True, "sync_album_art": False, "send_format": "mp3"}
            )
            self.assertTrue(patched.ok, msg=patched.message)
            self.assertEqual(patched.data["mode"], "stable")
            self.assertIn("stable_mode", patched.data["changed"])
            got = svc.config_get("stable_mode")
            self.assertTrue(got.ok)
            self.assertTrue(got.data["value"])
            # Round-trip off
            off = svc.config_patch({"stable_mode": False})
            self.assertTrue(off.ok)
            self.assertEqual(off.data["mode"], "experimental")


class MilestoneBInventoryPlaylistSyncTests(unittest.TestCase):
    def test_inventory_pagination_and_filters(self) -> None:
        from mtpmanager.domain.models import FileEntry
        from mtpmanager.infra.device_index import replace_device_listing

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            index = data / "library_index.db"
            serial = "TESTSERIAL001"
            g1 = new_track_guid()
            g2 = new_track_guid()
            files = [
                FileEntry(
                    item_id=1,
                    name=f"{g1}.mp3",
                    parent_id=100,
                    storage_id=1,
                    filesize=10,
                    filetype=1,
                ),
                FileEntry(
                    item_id=2,
                    name=f"{g2}.mp3",
                    parent_id=100,
                    storage_id=1,
                    filesize=20,
                    filetype=1,
                ),
                FileEntry(
                    item_id=3,
                    name="folder",
                    parent_id=0,
                    storage_id=1,
                    filesize=0,
                    filetype=2,
                ),
            ]
            replace_device_listing(serial, files, path=index, source="list")
            svc = HeadlessService(data_dir=data)
            page = svc.device_inventory(limit=1, offset=0, serial=serial)
            self.assertTrue(page.ok)
            self.assertEqual(page.data["total"], 3)
            self.assertEqual(page.data["returned"], 1)
            page2 = svc.device_inventory(limit=1, offset=1, serial=serial)
            self.assertEqual(page2.data["returned"], 1)
            self.assertNotEqual(
                page.data["files"][0]["item_id"], page2.data["files"][0]["item_id"]
            )
            by_parent = svc.device_inventory(parent_id=100, serial=serial)
            self.assertEqual(by_parent.data["total"], 2)
            by_guid = svc.device_inventory(guid=g1, serial=serial)
            self.assertEqual(by_guid.data["total"], 1)
            self.assertEqual(by_guid.data["files"][0]["name"], f"{g1}.mp3")
            known = svc.device_list_known()
            self.assertTrue(known.ok)
            self.assertGreaterEqual(known.data["count"], 1)

    def test_device_refresh_index_mocked(self) -> None:
        from mtpmanager.domain.models import FileEntry

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            svc = HeadlessService(data_dir=data)
            not_conn = svc.device_refresh_index()
            self.assertFalse(not_conn.ok)
            self.assertEqual(not_conn.code, "NOT_CONNECTED")

            mock_dev = MagicMock()
            listed = [
                FileEntry(
                    item_id=9,
                    name=f"{new_track_guid()}.mp3",
                    parent_id=100,
                    storage_id=1,
                    filesize=1,
                    filetype=1,
                )
            ]
            svc._device = mock_dev
            svc._connected = True
            svc._device_serial = "MOCKSERIAL"
            svc._session_lock.try_acquire("cli-test")
            with patch(
                "mtpmanager.headless.service.device_list_files",
                return_value=listed,
            ):
                result = svc.device_refresh_index()
            self.assertTrue(result.ok, msg=result.message)
            self.assertEqual(result.data["written"], 1)
            inv = svc.device_inventory(serial="MOCKSERIAL")
            self.assertEqual(inv.data["total"], 1)
            svc._session_lock.release()

    def test_playlist_lifecycle_delete_rename_remove_move_shuffle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            root.mkdir()
            paths = []
            guids = []
            tracks = []
            for i, title in enumerate(["A", "B", "C"], start=1):
                f = root / f"{title}.mp3"
                f.write_bytes(b"x")
                g = new_track_guid()
                t = _track(str(f), guid=g, title=title, artist=f"Art{i}")
                paths.append(str(f))
                guids.append(g)
                tracks.append(t)
            save_library_index(
                Library(tracks=tracks, root_paths=[str(root)]),
                path=data / "library_index.db",
            )
            svc = HeadlessService(data_dir=data)
            self.assertTrue(svc.playlist_create("Mix").ok)
            self.assertTrue(
                svc.playlist_add("Mix", guids=guids).ok
            )
            gated = svc.playlist_delete("Mix", confirm=False)
            self.assertEqual(gated.exit_code, int(ExitCode.CONFIRM_REQUIRED))

            ren = svc.playlist_rename("Mix", "Road")
            self.assertTrue(ren.ok)
            self.assertEqual(ren.data["name"], "Road")

            rem = svc.playlist_remove("Road", guids=[guids[0]])
            self.assertTrue(rem.ok)
            self.assertEqual(rem.data["track_count"], 2)

            shown = svc.playlist_show("Road")
            order_before = list(shown.data["paths"])
            moved = svc.playlist_move("Road", paths=[order_before[-1]], delta=-1)
            self.assertTrue(moved.ok)
            after_move = svc.playlist_show("Road").data["paths"]
            self.assertEqual(len(after_move), 2)

            shuf_gate = svc.playlist_shuffle("Road", confirm=False)
            self.assertEqual(shuf_gate.exit_code, int(ExitCode.CONFIRM_REQUIRED))
            shuf = svc.playlist_shuffle(
                "Road", algorithm="artist", confirm=True, seed_guid=guids[1]
            )
            self.assertTrue(shuf.ok)
            self.assertEqual(shuf.data["algorithm"], "artist")

            deleted = svc.playlist_delete("Road", confirm=True)
            self.assertTrue(deleted.ok)
            miss = svc.playlist_show("Road")
            self.assertFalse(miss.ok)

    def test_sync_entire_library_and_path_prefix_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = data / "Music"
            sub = root / "Band"
            sub.mkdir(parents=True)
            f1 = sub / "a.mp3"
            f2 = root / "other.mp3"
            f1.write_bytes(b"a")
            f2.write_bytes(b"b")
            g1 = new_track_guid()
            g2 = new_track_guid()
            save_library_index(
                Library(
                    tracks=[
                        _track(str(f1), guid=g1, title="A"),
                        _track(str(f2), guid=g2, title="B"),
                    ],
                    root_paths=[str(root)],
                ),
                path=data / "library_index.db",
            )
            svc = HeadlessService(data_dir=data)
            entire = svc.sync_tracks(entire_library=True, dry_run=True)
            self.assertTrue(entire.ok)
            self.assertEqual(entire.data["track_count"], 2)
            self.assertEqual(
                entire.data["batch_size"], DEFAULT_PLAYLIST_BATCH_SIZE
            )
            prefix = svc.sync_tracks(path_prefix=str(sub), dry_run=True)
            self.assertTrue(prefix.ok)
            self.assertEqual(prefix.data["track_count"], 1)
            self.assertEqual(prefix.data["tracks"][0]["guid"], g1)


if __name__ == "__main__":
    unittest.main()
