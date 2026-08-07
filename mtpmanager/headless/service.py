"""Headless operations for CLI and MCP (no Tk, no dialogs)."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from mtpmanager.app.cancellation import JobCancelled
from mtpmanager.app.device_ops import connect as device_connect
from mtpmanager.app.device_ops import delete_object as device_delete_object
from mtpmanager.app.device_ops import disconnect as device_disconnect
from mtpmanager.app.device_ops import get_device_info, get_device_identity
from mtpmanager.app.playlist_device import push_playlist_to_device
from mtpmanager.app.transfer import transfer_tracks
from mtpmanager.domain.device_profile import match_device_profile
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES, GENERIC, ZEN_VISION_M
from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.library_search import filter_library_tracks_scored
from mtpmanager.domain.models import Track
from mtpmanager.domain.playlist_m3u import parse_m3u
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.headless.dto import AgentResult, ExitCode, fail, ok, to_jsonable
from mtpmanager.headless.tools import tools_as_dict
from mtpmanager.infra.app_config import load_app_config
from mtpmanager.infra.app_paths import default_data_dir
from mtpmanager.infra.device_index import (
    guid_stems_on_device,
    list_cached_files,
    list_known_devices,
)
from mtpmanager.infra.device_session_lock import DeviceSessionBusy, DeviceSessionLock
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import (
    get_tracks_by_guids,
    index_path,
    load_library_index,
)
from mtpmanager.infra.logging_setup import default_log_dir
from mtpmanager.infra.playlists import get_playlist_by_name, list_playlists
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)


def _track_dict(track: Track, *, score: float | None = None) -> dict[str, Any]:
    m = track.meta
    d: dict[str, Any] = {
        "guid": track.guid or "",
        "path": track.path or "",
        "artist": m.artist,
        "albumartist": m.albumartist,
        "album": m.album,
        "title": m.title,
        "genre": m.genre,
        "tracknumber": m.tracknumber,
        "date": m.date,
        "length_sec": m.length_sec,
    }
    if score is not None:
        d["score"] = round(float(score), 4)
    return d


class HeadlessService:
    """Composition root for agent-facing operations."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._index_path = index_path(data_dir=self.data_dir)
        self._session_lock = DeviceSessionLock(data_dir=self.data_dir)
        self._device = None  # lazy PymtpDevice
        self._connected = False
        self._device_serial: str = ""
        self._library_cache: list[Track] | None = None

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            if self._connected and self._device is not None:
                try:
                    device_disconnect(self._device)
                except Exception:
                    logger.debug("disconnect on close failed", exc_info=True)
        finally:
            self._connected = False
            self._session_lock.release()

    def _config(self):
        return load_app_config(path=self.data_dir / "config.json")

    def _load_tracks(self, *, force: bool = False) -> list[Track]:
        if self._library_cache is not None and not force:
            return self._library_cache
        lib = load_library_index(
            path=self._index_path,
            drop_missing_files=True,
            keep_missing_if_roots_unreachable=True,
        )
        tracks = list(lib.tracks) if lib is not None else []
        self._library_cache = tracks
        return tracks

    def _ensure_device(self):
        if self._device is None:
            from mtpmanager.infra.pymtp_device import PymtpDevice

            self._device = PymtpDevice()
        return self._device

    def _require_session_lock(self, holder: str) -> AgentResult | None:
        if self._session_lock.owned:
            return None
        if self._session_lock.try_acquire(holder):
            return None
        info = self._session_lock.status()
        return fail(
            "DEVICE_BUSY",
            f"Device session busy (held by {info.holder!r} pid={info.pid})",
            exit_code=ExitCode.DEVICE_BUSY,
            data=info.as_dict(),
        )

    # --- agent / host ------------------------------------------------------

    def agent_tools(self) -> AgentResult:
        return ok(tools_as_dict())

    def agent_doctor(self) -> AgentResult:
        cfg = self._config()
        lock = self._session_lock.status()
        index = self._index_path
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        mtp_sendtr = shutil.which("mtp-sendtr")
        lib = None
        track_count = 0
        roots: list[str] = []
        try:
            lib = load_library_index(
                path=index,
                drop_missing_files=False,
                keep_missing_if_roots_unreachable=True,
            )
            if lib is not None:
                track_count = len(lib.tracks)
                roots = list(getattr(lib, "root_paths", None) or [])
                if not roots and getattr(lib, "root_path", None):
                    roots = [str(lib.root_path)]
        except Exception as e:
            logger.debug("doctor library load: %s", e)

        data = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "data_dir": str(self.data_dir),
            "log_dir": str(default_log_dir()),
            "config_path": str(self.data_dir / "config.json"),
            "library_index": str(index),
            "library_index_exists": index.is_file(),
            "track_count": track_count,
            "library_roots": roots,
            "mode": cfg.active_mode(),
            "send_format": cfg.normalized_send_format(),
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "mtp_sendtr": mtp_sendtr,
            "device_lock": lock.as_dict(),
            "session_owned_by_us": self._session_lock.owned,
            "device_connected": self._connected,
        }
        return ok(data)

    def library_list_roots(self) -> AgentResult:
        lib = load_library_index(
            path=self._index_path,
            drop_missing_files=False,
            keep_missing_if_roots_unreachable=True,
        )
        if lib is None:
            return ok({"roots": [], "track_count": 0})
        roots = list(getattr(lib, "root_paths", None) or [])
        if not roots:
            rp = getattr(lib, "root_path", None)
            if rp:
                roots = [str(rp)]
        return ok({"roots": roots, "track_count": len(lib.tracks)})

    def library_search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> AgentResult:
        q = (query or "").strip()
        if not q:
            return fail("USAGE", "query is required", exit_code=ExitCode.USAGE)
        lim = max(1, min(int(limit or 50), 500))
        tracks = self._load_tracks()
        matched, scores = filter_library_tracks_scored(tracks, q)
        rows = []
        for t in matched[:lim]:
            sc = scores.get(t.path or "", 0.0)
            rows.append(_track_dict(t, score=sc))
        return ok(
            {
                "query": q,
                "total_matched": len(matched),
                "returned": len(rows),
                "tracks": rows,
            }
        )

    def library_track(
        self,
        *,
        guid: str | None = None,
        path: str | None = None,
    ) -> AgentResult:
        g = (guid or "").strip()
        p = (path or "").strip()
        if not g and not p:
            return fail(
                "USAGE",
                "guid or path is required",
                exit_code=ExitCode.USAGE,
            )
        if g and is_track_guid(g):
            found = get_tracks_by_guids([g], path=self._index_path)
            t = found.get(g)
            if t is not None:
                return ok({"track": _track_dict(t)})
        tracks = self._load_tracks()
        if p:
            norm = os.path.normpath(p)
            for t in tracks:
                if os.path.normpath(t.path or "") == norm:
                    return ok({"track": _track_dict(t)})
        if g:
            for t in tracks:
                if (t.guid or "").lower() == g.lower():
                    return ok({"track": _track_dict(t)})
        return fail(
            "NOT_FOUND",
            "Track not found in library index",
            exit_code=ExitCode.NOT_FOUND,
        )

    def playlist_list(self) -> AgentResult:
        items = list_playlists(path=self._index_path)
        return ok(
            {
                "playlists": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "track_count": p.track_count,
                        "updated_at": p.updated_at,
                    }
                    for p in items
                ]
            }
        )

    def playlist_show(self, name: str) -> AgentResult:
        pl = get_playlist_by_name(name, path=self._index_path)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        entries = parse_m3u(pl.m3u_text or "")
        return ok(
            {
                "id": pl.id,
                "name": pl.name,
                "updated_at": pl.updated_at,
                "track_count": len(entries),
                "paths": [e.path for e in entries],
            }
        )

    def config_get(self, key: str | None = None) -> AgentResult:
        cfg = self._config()
        raw = asdict(cfg)
        if key:
            k = key.strip()
            if k not in raw:
                return fail(
                    "NOT_FOUND",
                    f"Unknown config key: {k!r}",
                    exit_code=ExitCode.NOT_FOUND,
                    data={"keys": sorted(raw.keys())},
                )
            return ok({"key": k, "value": to_jsonable(raw[k])})
        return ok({"config": to_jsonable(raw), "mode": cfg.active_mode()})

    # --- device ------------------------------------------------------------

    def device_status(self) -> AgentResult:
        info = self._session_lock.status()
        return ok(
            {
                "lock": info.as_dict(),
                "session_owned_by_us": self._session_lock.owned,
                "connected": self._connected,
                "serial": self._device_serial or "",
            }
        )

    def device_connect(self) -> AgentResult:
        busy = self._require_session_lock("cli-device")
        if busy is not None:
            return busy
        try:
            dev = self._ensure_device()
            name = device_connect(dev)
            self._connected = True
            try:
                identity = get_device_identity(dev)
                self._device_serial = str(identity.serial or "")
            except Exception:
                logger.debug("identity after connect failed", exc_info=True)
                self._device_serial = ""
            return ok(
                {
                    "name": name,
                    "serial": self._device_serial,
                    "connected": True,
                }
            )
        except DeviceSessionBusy as e:
            return fail(
                "DEVICE_BUSY",
                str(e),
                exit_code=ExitCode.DEVICE_BUSY,
                data={"holder": e.holder, "pid": e.pid},
            )
        except Exception as e:
            logger.exception("device_connect failed")
            msg = str(e).strip() or type(e).__name__
            return fail(
                "DEVICE_ERROR",
                msg,
                exit_code=ExitCode.ERROR,
                data={"error_type": type(e).__name__},
            )

    def device_disconnect(self) -> AgentResult:
        try:
            if self._device is not None and self._connected:
                device_disconnect(self._device)
        except Exception as e:
            logger.warning("disconnect: %s", e)
        finally:
            self._connected = False
            self._device_serial = ""
            self._session_lock.release()
        return ok({"connected": False})

    def device_info(self) -> AgentResult:
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        try:
            info = get_device_info(self._device)
            try:
                profile = match_device_profile(info, BUILTIN_PROFILES)
                profile_id = profile.id if profile else ""
            except Exception:
                profile_id = ""
            return ok(
                {
                    "info": to_jsonable(info.as_legacy_dict()),
                    "profile_id": profile_id,
                }
            )
        except Exception as e:
            logger.exception("device_info failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)

    def device_inventory(self, *, limit: int = 200) -> AgentResult:
        """Cache-only inventory (no USB walk)."""
        lim = max(1, min(int(limit or 200), 5000))
        serial = self._device_serial
        devices = list_known_devices(path=self._index_path)
        if not serial and devices:
            # Prefer last known device when not connected.
            serial = str(devices[0].get("serial") or "")
        if not serial:
            return ok(
                {
                    "serial": "",
                    "files": [],
                    "known_devices": devices,
                    "note": "No serial; connect once or seed device index from GUI",
                }
            )
        files = list_cached_files(serial, path=self._index_path)
        rows = [
            {
                "item_id": f.item_id,
                "name": f.name,
                "parent_id": f.parent_id,
                "filesize": f.filesize,
                "filetype": f.filetype,
            }
            for f in files[:lim]
        ]
        return ok(
            {
                "serial": serial,
                "total": len(files),
                "returned": len(rows),
                "files": rows,
                "known_devices": devices,
            }
        )

    def device_delete(self, object_id: int, *, confirm: bool = False) -> AgentResult:
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to delete an object",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"object_id": int(object_id)},
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-delete")
        if busy is not None:
            return busy
        try:
            device_delete_object(self._device, int(object_id))
            return ok({"deleted_object_id": int(object_id)})
        except Exception as e:
            logger.exception("device_delete failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)

    # --- sync --------------------------------------------------------------

    def _resolve_sync_tracks(
        self,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> tuple[list[Track], AgentResult | None]:
        tracks = self._load_tracks()
        by_guid = {t.guid: t for t in tracks if t.guid}
        by_path = {os.path.normpath(t.path or ""): t for t in tracks}
        selected: list[Track] = []
        seen: set[str] = set()

        def _add(t: Track) -> None:
            key = t.guid or t.path
            if key in seen:
                return
            seen.add(key)
            selected.append(t)

        for g in guids or []:
            g = (g or "").strip()
            if not g:
                continue
            t = by_guid.get(g)
            if t is None and is_track_guid(g):
                found = get_tracks_by_guids([g], path=self._index_path)
                t = found.get(g)
            if t is None:
                return [], fail(
                    "NOT_FOUND",
                    f"Unknown GUID: {g}",
                    exit_code=ExitCode.NOT_FOUND,
                )
            _add(t)

        for p in paths or []:
            p = os.path.normpath((p or "").strip())
            if not p:
                continue
            t = by_path.get(p)
            if t is None:
                return [], fail(
                    "NOT_FOUND",
                    f"Unknown path: {p}",
                    exit_code=ExitCode.NOT_FOUND,
                )
            _add(t)

        art = (artist or "").strip()
        alb = (album or "").strip()
        if art or alb:
            art_cf = art.casefold()
            alb_cf = alb.casefold()
            for t in tracks:
                pa = primary_artist(t).casefold()
                ta = (t.meta.artist or "").casefold()
                album_ok = (not alb) or (t.meta.album or "").casefold() == alb_cf
                artist_ok = (not art) or pa == art_cf or ta == art_cf
                if artist_ok and album_ok:
                    _add(t)

        if not selected:
            return [], fail(
                "USAGE",
                "No tracks resolved; pass guids, paths, and/or artist/album",
                exit_code=ExitCode.USAGE,
            )
        return selected, None

    def sync_tracks(
        self,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        artist: str | None = None,
        album: str | None = None,
        mode: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> AgentResult:
        selected, err = self._resolve_sync_tracks(
            guids=guids, paths=paths, artist=artist, album=album
        )
        if err is not None:
            return err

        cfg = self._config()
        mode_s = (mode or cfg.active_mode()).strip().lower()
        if mode_s not in ("stable", "experimental"):
            return fail(
                "USAGE",
                "mode must be 'stable' or 'experimental'",
                exit_code=ExitCode.USAGE,
            )

        target_format = cfg.normalized_send_format()
        device_formats = set(ZEN_VISION_M.supported_audio_formats) | set(
            GENERIC.supported_audio_formats
        )

        serial = self._device_serial
        if not serial:
            known = list_known_devices(path=self._index_path)
            if known:
                serial = str(known[0].get("serial") or "")
        stems: set[str] = set()
        if serial:
            try:
                stems = set(guid_stems_on_device(serial, path=self._index_path) or [])
            except Exception:
                logger.debug("guid_stems_on_device failed", exc_info=True)

        plan = []
        for t in selected:
            g = t.guid or ""
            already = bool(g and g in stems)
            plan.append(
                {
                    "guid": g,
                    "path": t.path,
                    "title": t.meta.title,
                    "artist": t.meta.artist,
                    "album": t.meta.album,
                    "action": "skip" if already else "send",
                    "target_format": target_format,
                }
            )

        if dry_run or not confirm:
            code = "ok" if dry_run else "CONFIRM_REQUIRED"
            exit_c = ExitCode.OK if dry_run else ExitCode.CONFIRM_REQUIRED
            msg = (
                "Dry-run plan only"
                if dry_run
                else "Pass confirm=true to execute (or dry_run=true to plan only)"
            )
            result = AgentResult(
                ok=dry_run,
                code=code if dry_run else "CONFIRM_REQUIRED",
                message=msg,
                data={
                    "mode": mode_s,
                    "target_format": target_format,
                    "track_count": len(plan),
                    "would_send": sum(1 for p in plan if p["action"] == "send"),
                    "would_skip": sum(1 for p in plan if p["action"] == "skip"),
                    "tracks": plan,
                },
                exit_code=int(exit_c),
            )
            if not dry_run:
                return result
            return ok(result.data, message=msg)

        busy = self._require_session_lock("cli-sync")
        if busy is not None:
            return busy

        try:
            if mode_s == "stable":
                from mtpmanager.infra.cmd_transport import CmdTransport

                transport = CmdTransport()
            else:
                if not self._connected or self._device is None:
                    # Auto-connect for experimental sync.
                    conn = self.device_connect()
                    if not conn.ok:
                        return conn
                transport = self._ensure_device()

            transcoder = FFmpegTranscoder()
            statuses: list[dict[str, str]] = []

            def on_status(path: str, status: str) -> None:
                statuses.append({"path": path, "status": status})

            succeeded = transfer_tracks(
                selected,
                target_format=target_format,
                transport=transport,
                transcoder=transcoder,
                device_formats=device_formats,
                device_guid_stems=stems,
                on_track_status=on_status,
                stop_on_fatal=True,
            )
            return ok(
                {
                    "mode": mode_s,
                    "target_format": target_format,
                    "requested": len(selected),
                    "succeeded": succeeded,
                    "statuses": statuses,
                    "note": (
                        "Remote names are track GUIDs under Music 100; "
                        "see docs/device-contract.md"
                    ),
                }
            )
        except JobCancelled as e:
            return fail(
                "CANCELLED",
                str(e) or "Job cancelled",
                exit_code=ExitCode.CANCELLED,
            )
        except TransportError as e:
            return fail(
                "TRANSPORT_FATAL" if e.fatal else "TRANSPORT_ERROR",
                str(e),
                exit_code=ExitCode.TRANSPORT_FATAL if e.fatal else ExitCode.ERROR,
                data={
                    "fatal": e.fatal,
                    "path": e.path,
                    "stderr": (e.stderr or "")[:2000],
                    "returncode": e.returncode,
                },
            )
        except Exception as e:
            logger.exception("sync_tracks failed")
            return fail("SYNC_ERROR", str(e), exit_code=ExitCode.ERROR)

    def playlist_push(self, name: str, *, confirm: bool = False) -> AgentResult:
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to push playlist to device",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        pl = get_playlist_by_name(name, path=self._index_path)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        if not self._connected or self._device is None:
            conn = self.device_connect()
            if not conn.ok:
                return conn
        entries = parse_m3u(pl.m3u_text or "")
        paths = [e.path for e in entries]
        tracks = self._load_tracks()
        by_path = {os.path.normpath(t.path or ""): t for t in tracks}
        guids: list[str] = []
        for p in paths:
            t = by_path.get(os.path.normpath(p))
            if t and is_track_guid(t.guid):
                guids.append(t.guid)
        serial = self._device_serial
        if not serial:
            return fail(
                "DEVICE_ERROR",
                "No device serial after connect",
                exit_code=ExitCode.ERROR,
            )
        try:
            result = push_playlist_to_device(
                device=self._ensure_device(),
                serial=serial,
                name=pl.name,
                guids_in_order=guids,
            )
            return ok(
                {
                    "playlist_id": result.playlist_id,
                    "name": result.name,
                    "created": result.created,
                    "resolved": result.resolved,
                    "missing_guid": result.missing_guid,
                    "unresolved_guids": list(result.unresolved_guids),
                }
            )
        except Exception as e:
            logger.exception("playlist_push failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)
