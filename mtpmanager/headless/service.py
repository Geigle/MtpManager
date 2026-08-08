"""Headless operations for CLI and MCP (no Tk, no dialogs)."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence

from mtpmanager.app.cancellation import JobCancelled
from mtpmanager.app.device_ops import connect as device_connect
from mtpmanager.app.device_ops import delete_object as device_delete_object
from mtpmanager.app.device_ops import disconnect as device_disconnect
from mtpmanager.app.device_ops import get_device_info, get_device_identity
from mtpmanager.app.album_art_device import push_album_art_for_tracks
from mtpmanager.app.playlist_device import push_playlist_to_device
from mtpmanager.app.transfer import transfer_tracks
from mtpmanager.domain.device_profile import match_device_profile
from mtpmanager.domain.device_profiles import BUILTIN_PROFILES, GENERIC, ZEN_VISION_M
from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.library_search import filter_library_tracks_scored
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.playlist_m3u import parse_m3u
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.headless.dto import AgentResult, ExitCode, fail, ok, to_jsonable
from mtpmanager.headless.tools import tools_as_dict
from mtpmanager.infra.app_config import load_app_config
from mtpmanager.infra.app_paths import default_data_dir
from mtpmanager.infra.device_index import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    guid_stems_on_device,
    list_cached_files,
    list_known_devices,
    record_send,
)
from mtpmanager.infra.device_session_lock import DeviceSessionBusy, DeviceSessionLock
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.library_index import (
    get_tracks_by_guids,
    index_path,
    load_library_index,
)
from mtpmanager.infra.logging_setup import default_log_dir
from mtpmanager.infra.playlists import (
    append_tracks_to_playlist,
    create_playlist,
    get_playlist_by_name,
    list_playlists,
    replace_playlist_tracks,
)
from mtpmanager.infra.remote_naming import build_remote_path, split_remote_path
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)

# ZEN PyMTP bulk: small batches + quiet reconnect after PTP poison.
# See docs/debrief-zen-experimental-bulk-session-poison.md
DEFAULT_PLAYLIST_BATCH_SIZE = 15
DEFAULT_RECONNECT_QUIET_S = 15.0
MAX_BATCH_RETRIES = 4

# Wire values match AppConfig.active_mode() / GUI: "experimental" | "stable".
# CLI/MCP also accept friendly aliases for the default PyMTP path.
_MODE_ALIASES: dict[str, str] = {
    "experimental": "experimental",
    "default": "experimental",
    "pymtp": "experimental",
    "stable": "stable",
    "cmd": "stable",
    "mtp-sendtr": "stable",
}


def normalize_transfer_mode(mode: str | None) -> str | None:
    """Map user/CLI mode tokens to ``experimental`` or ``stable``.

    Returns None if *mode* is empty/None (caller should use config default).
    Returns ``\"\"`` if the token is non-empty but unknown.
    """
    if mode is None:
        return None
    raw = str(mode).strip().lower()
    if not raw:
        return None
    return _MODE_ALIASES.get(raw, "")


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
            "audio_encode": cfg.resolved_audio_encode().to_dict(),
            "audiobook_audio_encode": (
                cfg.audiobook_audio_encode.to_dict()
                if cfg.audiobook_audio_encode is not None
                else None
            ),
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
        # Agents often need bulk results (e.g. whole-artist playlists).
        lim = max(1, min(int(limit or 50), 5000))
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

    def _playlist_summary(self, pl: Any) -> dict[str, Any]:
        entries = parse_m3u(getattr(pl, "m3u_text", None) or "")
        return {
            "id": int(pl.id),
            "name": str(pl.name or ""),
            "updated_at": str(getattr(pl, "updated_at", "") or ""),
            "track_count": len(entries),
            "paths": [e.path for e in entries],
        }

    def _resolve_tracks_by_guid_path(
        self,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> tuple[list[Track], AgentResult | None]:
        """Resolve explicit GUID/path lists (hard-fail on unknown)."""
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
                # Case-insensitive GUID fallback (index keys are lowercase).
                t = by_guid.get(g.lower())
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

        return selected, None

    def playlist_create(self, name: str) -> AgentResult:
        """Create an empty host playlist (M3U in library index)."""
        clean = (name or "").strip()
        if not clean:
            return fail(
                "USAGE",
                "playlist name is required",
                exit_code=ExitCode.USAGE,
            )
        try:
            pl = create_playlist(clean, path=self._index_path)
        except ValueError as e:
            msg = str(e)
            if "already exists" in msg.lower():
                return fail(
                    "CONFLICT",
                    msg,
                    exit_code=ExitCode.ERROR,
                    data={"name": clean},
                )
            return fail("USAGE", msg, exit_code=ExitCode.USAGE)
        except Exception as e:
            logger.exception("playlist_create failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(self._playlist_summary(pl), message=f"Created playlist {pl.name!r}")

    def playlist_add(
        self,
        name: str,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        skip_existing: bool = True,
    ) -> AgentResult:
        """Append library tracks to a host playlist by GUID and/or path."""
        clean = (name or "").strip()
        if not clean:
            return fail(
                "USAGE",
                "playlist name is required",
                exit_code=ExitCode.USAGE,
            )
        pl = get_playlist_by_name(clean, path=self._index_path)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Playlist not found: {clean!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        selected, err = self._resolve_tracks_by_guid_path(guids=guids, paths=paths)
        if err is not None:
            return err
        if not selected:
            return fail(
                "USAGE",
                "No tracks resolved; pass --guid and/or --path",
                exit_code=ExitCode.USAGE,
            )
        before = {os.path.normpath(e.path) for e in parse_m3u(pl.m3u_text or "")}
        try:
            updated = append_tracks_to_playlist(
                pl.id,
                selected,
                skip_existing=bool(skip_existing),
                path=self._index_path,
            )
        except ValueError as e:
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        except Exception as e:
            logger.exception("playlist_add failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        after_entries = parse_m3u(updated.m3u_text or "")
        after = {os.path.normpath(e.path) for e in after_entries}
        added_n = max(0, len(after) - len(before)) if skip_existing else len(selected)
        # Prefer path-set diff when skip_existing (true new membership).
        if skip_existing:
            new_paths = after - before
            added_n = len(new_paths)
            skipped_n = len(selected) - added_n
        else:
            added_n = len(selected)
            skipped_n = 0
        summary = self._playlist_summary(updated)
        summary["added"] = added_n
        summary["skipped_existing"] = max(0, skipped_n)
        summary["requested"] = len(selected)
        return ok(
            summary,
            message=(
                f"Added {added_n} track(s) to {updated.name!r} "
                f"({summary['track_count']} total)"
            ),
        )

    def playlist_replace(
        self,
        name: str,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> AgentResult:
        """Replace host playlist membership with the given tracks (order preserved).

        Passing no guids/paths clears the playlist.
        """
        clean = (name or "").strip()
        if not clean:
            return fail(
                "USAGE",
                "playlist name is required",
                exit_code=ExitCode.USAGE,
            )
        pl = get_playlist_by_name(clean, path=self._index_path)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Playlist not found: {clean!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        has_any = any((g or "").strip() for g in (guids or [])) or any(
            (p or "").strip() for p in (paths or [])
        )
        if has_any:
            selected, err = self._resolve_tracks_by_guid_path(
                guids=guids, paths=paths
            )
            if err is not None:
                return err
        else:
            selected = []
        try:
            updated = replace_playlist_tracks(
                pl.id, selected, path=self._index_path
            )
        except ValueError as e:
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        except Exception as e:
            logger.exception("playlist_replace failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        summary = self._playlist_summary(updated)
        summary["replaced_with"] = len(selected)
        return ok(
            summary,
            message=(
                f"Replaced {updated.name!r} with {len(selected)} track(s)"
            ),
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

    def device_art_probe(self) -> AgentResult:
        """Probe RepresentativeSample support for common filetypes (Experimental)."""
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-art-probe")
        if busy is not None:
            return busy
        try:
            import mtpmanager.infra.pymtp_wrapper as pymtp

            names = ("MP3", "WMA", "ALBUM", "JPEG", "UNDEF_AUDIO", "PLAYLIST")
            rows: list[dict[str, Any]] = []
            for name in names:
                ft = int(pymtp.LIBMTP_Filetype.get(name, -1))
                if ft < 0:
                    continue
                row: dict[str, Any] = {
                    "object_filetype": name,
                    "object_filetype_id": ft,
                    "supported": False,
                }
                try:
                    info = self._device.get_representative_sample_format(ft)
                    if info:
                        row["supported"] = True
                        # Sample props use keys width/height/size/filetype —
                        # do not clobber object_filetype with sample JPEG id.
                        row["sample_width"] = info.get("width")
                        row["sample_height"] = info.get("height")
                        row["sample_max_bytes"] = info.get("size")
                        row["sample_duration"] = info.get("duration")
                        row["sample_filetype"] = info.get("filetype")
                        row["sample_filetype_name"] = info.get("filetype_name")
                except TransportError as e:
                    row["error"] = str(e)
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {e}"
                rows.append(row)
            return ok(
                {
                    "formats": rows,
                    "note": (
                        "supported=true means the device advertises "
                        "RepresentativeSample* props for that object format. "
                        "Creative often wants art on ALBUM objects, not tracks."
                    ),
                }
            )
        except Exception as e:
            logger.exception("device_art_probe failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)

    def device_art_experiment(
        self,
        path: str,
        *,
        object_id: int | None = None,
        confirm: bool = False,
        try_album: bool = True,
        max_edge: int = 320,
        max_bytes: int = 20 * 1024,
    ) -> AgentResult:
        """Minimum album-art experiment: probe → (optional send track) → sample.

        Steps:
          1. Prepare JPEG from host cover (embedded/sidecar).
          2. Probe sample formats (MP3 + ALBUM).
          3. If *object_id* omitted: send the track via PyMTP (new GUID).
          4. Send representative sample to the track object id.
          5. If track sample fails or *try_album*: create album + sample on album.
          6. Read sample back when possible.

        Does **not** call Get_Album_List (hang class after bad finalize).
        """
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass --confirm to run the album-art experiment (writes to device)",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"path": path, "object_id": object_id},
            )
        if not path or not os.path.isfile(path):
            return fail(
                "NOT_FOUND",
                f"Track path not found: {path}",
                exit_code=ExitCode.ERROR,
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-art-experiment")
        if busy is not None:
            return busy

        from mtpmanager.domain.track_id import new_track_guid
        from mtpmanager.infra.album_art import prepare_device_cover_jpeg
        from mtpmanager.infra.mutagen_tags import MutagenTagReader
        import mtpmanager.infra.pymtp_wrapper as pymtp

        result: dict[str, Any] = {
            "path": path,
            "steps": [],
        }

        def step(name: str, **payload: Any) -> None:
            row = {"step": name, **payload}
            result["steps"].append(row)
            logger.info("art-experiment %s %s", name, payload)

        try:
            jpeg = prepare_device_cover_jpeg(
                path, max_edge=int(max_edge), max_bytes=int(max_bytes)
            )
            if not jpeg:
                return fail(
                    "NO_ART",
                    "No cover art found (embedded tags or cover.jpg sidecar) "
                    "or Pillow missing",
                    exit_code=ExitCode.ERROR,
                    data=result,
                )
            jpeg_bytes, jw, jh = jpeg
            result["jpeg"] = {
                "bytes": len(jpeg_bytes),
                "width": jw,
                "height": jh,
            }
            step("prepare_jpeg", ok=True, **result["jpeg"])

            probe: dict[str, Any] = {}
            for name in ("MP3", "ALBUM"):
                ft = int(pymtp.LIBMTP_Filetype[name])
                try:
                    info = self._device.get_representative_sample_format(ft)
                    probe[name] = info or {"supported": False}
                    if info:
                        probe[name] = {"supported": True, **info}
                except Exception as e:
                    probe[name] = {"supported": False, "error": str(e)}
            result["probe"] = probe
            step("probe", **probe)

            track_id = int(object_id) if object_id is not None else None
            if track_id is None or track_id <= 0:
                meta = MutagenTagReader().read_metadata(path)
                # Prefer library GUID when path is indexed.
                guid = ""
                try:
                    for t in self._load_tracks():
                        if os.path.normpath(t.path) == os.path.normpath(path):
                            guid = str(t.guid or "")
                            break
                except Exception:
                    logger.debug("library guid lookup failed", exc_info=True)
                if not guid:
                    guid = new_track_guid()
                result["guid"] = guid
                result["meta"] = {
                    "title": meta.title,
                    "artist": meta.artist,
                    "album": meta.album,
                }
                trid = self._device.send_track(path, meta, guid=guid)
                track_id = int(trid) if trid is not None else None
                step(
                    "send_track",
                    ok=track_id is not None and track_id > 0,
                    object_id=track_id,
                    guid=guid,
                )
                if track_id is None or track_id <= 0:
                    return fail(
                        "SEND_FAILED",
                        "Track send returned no object id; cannot attach art",
                        exit_code=ExitCode.ERROR,
                        data=result,
                    )
            else:
                step("use_existing_object", object_id=track_id)

            result["track_object_id"] = track_id

            # Prefer sample filetype from probe when present.
            sample_ft = int(pymtp.LIBMTP_Filetype["JPEG"])
            for key in ("MP3", "ALBUM"):
                info = probe.get(key) or {}
                if info.get("supported") and info.get("filetype") is not None:
                    sample_ft = int(info["filetype"])
                    break

            # Clamp JPEG to probe max dimensions/size when available.
            edge_cap = max_edge
            byte_cap = max_bytes
            for key in ("MP3", "ALBUM"):
                info = probe.get(key) or {}
                if not info.get("supported"):
                    continue
                if int(info.get("width") or 0) > 0:
                    edge_cap = min(edge_cap, int(info["width"]))
                if int(info.get("height") or 0) > 0:
                    edge_cap = min(edge_cap, int(info["height"]))
                if int(info.get("size") or 0) > 0:
                    byte_cap = min(byte_cap, int(info["size"]))
            if edge_cap < max_edge or byte_cap < max_bytes:
                jpeg2 = prepare_device_cover_jpeg(
                    path, max_edge=edge_cap, max_bytes=byte_cap
                )
                if jpeg2:
                    jpeg_bytes, jw, jh = jpeg2
                    result["jpeg"] = {
                        "bytes": len(jpeg_bytes),
                        "width": jw,
                        "height": jh,
                        "clamped_to_probe": True,
                    }
                    step("prepare_jpeg_clamped", ok=True, **result["jpeg"])

            track_art: dict[str, Any] = {"object_id": track_id}
            try:
                self._device.send_representative_sample(
                    track_id,
                    jpeg_bytes,
                    width=jw,
                    height=jh,
                    filetype=sample_ft,
                )
                track_art["ok"] = True
            except TransportError as e:
                track_art["ok"] = False
                track_art["error"] = str(e)
            result["track_art"] = track_art
            step("send_sample_track", **track_art)

            if track_art.get("ok"):
                try:
                    got = self._device.get_representative_sample(track_id)
                    result["track_art_readback"] = got
                    step("readback_track", ok=bool(got and got.get("has_data")), **(got or {}))
                except Exception as e:
                    step("readback_track", ok=False, error=str(e))

            album_art: dict[str, Any] | None = None
            need_album = bool(try_album) and (
                not track_art.get("ok")
                or bool((probe.get("ALBUM") or {}).get("supported"))
            )
            if need_album:
                album_art = {}
                meta_album = (result.get("meta") or {}).get("album") or "Album"
                meta_artist = (result.get("meta") or {}).get("artist") or ""
                if "meta" not in result:
                    try:
                        m = MutagenTagReader().read_metadata(path)
                        meta_album = m.album or meta_album
                        meta_artist = m.artist or meta_artist
                    except Exception:
                        pass
                try:
                    album_id = self._device.create_album(
                        str(meta_album)[:120] or "Album",
                        [int(track_id)],
                        artist=str(meta_artist)[:120],
                    )
                    album_art["album_id"] = album_id
                    step("create_album", ok=True, album_id=album_id)
                    try:
                        self._device.send_representative_sample(
                            album_id,
                            jpeg_bytes,
                            width=jw,
                            height=jh,
                            filetype=sample_ft,
                        )
                        album_art["ok"] = True
                        step("send_sample_album", ok=True, album_id=album_id)
                    except TransportError as e:
                        album_art["ok"] = False
                        album_art["error"] = str(e)
                        step("send_sample_album", ok=False, error=str(e))
                    if album_art.get("ok"):
                        try:
                            got = self._device.get_representative_sample(album_id)
                            album_art["readback"] = got
                            step(
                                "readback_album",
                                ok=bool(got and got.get("has_data")),
                                **(got or {}),
                            )
                        except Exception as e:
                            step("readback_album", ok=False, error=str(e))
                except TransportError as e:
                    album_art["ok"] = False
                    album_art["error"] = str(e)
                    step("create_album", ok=False, error=str(e))
                result["album_art"] = album_art

            any_ok = bool(track_art.get("ok")) or bool(
                album_art and album_art.get("ok")
            )
            result["success"] = any_ok
            if any_ok:
                return ok(
                    result,
                    message=(
                        "Album art sample sent — check the ZEN UI for the cover. "
                        "Track and/or album sample details are in data."
                    ),
                )
            return fail(
                "ART_FAILED",
                "Could not attach representative sample to track or album. "
                "See probe + step errors (device may lack RepresentativeSampleData).",
                exit_code=ExitCode.ERROR,
                data=result,
            )
        except TransportError as e:
            logger.exception("art experiment transport error")
            result["error"] = str(e)
            return fail(
                "DEVICE_ERROR",
                str(e),
                exit_code=ExitCode.ERROR,
                data=result,
            )
        except Exception as e:
            logger.exception("device_art_experiment failed")
            result["error"] = str(e)
            return fail(
                "DEVICE_ERROR",
                str(e),
                exit_code=ExitCode.ERROR,
                data=result,
            )

    # --- sync --------------------------------------------------------------

    def _resolve_playlist_tracks(
        self, name: str
    ) -> tuple[list[Track], list[str], str | None, AgentResult | None]:
        """Resolve host playlist M3U → library tracks (soft missing paths).

        Returns ``(selected, unresolved_paths, playlist_name, error)``.
        """
        pl = get_playlist_by_name(name, path=self._index_path)
        if pl is None:
            return (
                [],
                [],
                None,
                fail(
                    "NOT_FOUND",
                    f"Playlist not found: {name!r}",
                    exit_code=ExitCode.NOT_FOUND,
                ),
            )
        entries = parse_m3u(pl.m3u_text or "")
        tracks = self._load_tracks()
        by_path = {os.path.normpath(t.path or ""): t for t in tracks}
        selected: list[Track] = []
        seen: set[str] = set()
        unresolved: list[str] = []
        for e in entries:
            p = os.path.normpath(e.path or "")
            if not p:
                continue
            t = by_path.get(p)
            if t is None:
                unresolved.append(p)
                continue
            key = t.guid or t.path
            if key in seen:
                continue
            seen.add(key)
            selected.append(t)
        return selected, unresolved, pl.name, None

    def _resolve_sync_tracks(
        self,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        artist: str | None = None,
        album: str | None = None,
        playlist: str | None = None,
    ) -> tuple[list[Track], list[str], str | None, AgentResult | None]:
        """Resolve selection. Returns tracks, unresolved_paths, playlist_name, err."""
        selected: list[Track] = []
        seen: set[str] = set()
        unresolved: list[str] = []
        playlist_name: str | None = None

        def _add(t: Track) -> None:
            key = t.guid or t.path
            if key in seen:
                return
            seen.add(key)
            selected.append(t)

        pl_name = (playlist or "").strip()
        if pl_name:
            pl_tracks, pl_unresolved, resolved_name, err = self._resolve_playlist_tracks(
                pl_name
            )
            if err is not None:
                return [], [], None, err
            playlist_name = resolved_name
            unresolved.extend(pl_unresolved)
            for t in pl_tracks:
                _add(t)

        tracks = self._load_tracks()
        by_guid = {t.guid: t for t in tracks if t.guid}
        by_path = {os.path.normpath(t.path or ""): t for t in tracks}

        for g in guids or []:
            g = (g or "").strip()
            if not g:
                continue
            t = by_guid.get(g)
            if t is None and is_track_guid(g):
                found = get_tracks_by_guids([g], path=self._index_path)
                t = found.get(g)
            if t is None:
                return [], [], playlist_name, fail(
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
                return [], [], playlist_name, fail(
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
            if pl_name and unresolved:
                return [], unresolved, playlist_name, fail(
                    "NOT_FOUND",
                    (
                        f"Playlist {playlist_name!r} has {len(unresolved)} path(s) "
                        "but none resolve in the library index"
                    ),
                    exit_code=ExitCode.NOT_FOUND,
                    data={"unresolved_paths": unresolved, "playlist": playlist_name},
                )
            return [], unresolved, playlist_name, fail(
                "USAGE",
                "No tracks resolved; pass --playlist, guids, paths, and/or artist/album",
                exit_code=ExitCode.USAGE,
            )
        return selected, unresolved, playlist_name, None

    def _on_after_send_record(
        self, serial: str, stems: set[str]
    ) -> Callable[[str, str, int | None], None]:
        """Wire skip-if-present cache after each successful send (GUI parity)."""
        index = self._index_path

        def on_after_send(guid: str, send_path: str, object_id: int | None) -> None:
            if not is_track_guid(guid):
                return
            _, ext = os.path.splitext(send_path)
            remote = build_remote_path(
                TrackMetadata(),
                ext or ".mp3",
                music_folder_id=DEFAULT_MUSIC_FOLDER_ID,
                guid=guid,
            )
            _, basename = split_remote_path(remote)
            try:
                record_send(
                    serial,
                    remote_name=basename,
                    guid=guid,
                    item_id=object_id,
                    parent_id=DEFAULT_MUSIC_FOLDER_ID,
                    storage_id=DEFAULT_STORAGE_ID,
                    path=index,
                )
                stems.add(guid)
            except Exception:
                logger.debug("record_send failed", exc_info=True)

        return on_after_send

    def _reconnect_device(self, *, quiet_s: float) -> AgentResult | None:
        """Quiet disconnect + reconnect for PyMTP session recovery.

        Returns None on success, or a fail AgentResult.
        """
        try:
            try:
                self.device_disconnect()
            except Exception:
                logger.debug("disconnect before reconnect failed", exc_info=True)
            if quiet_s > 0:
                time.sleep(quiet_s)
            conn = self.device_connect()
            if not conn.ok:
                return conn
            return None
        except Exception as e:
            logger.exception("reconnect failed")
            return fail(
                "DEVICE_ERROR",
                f"Reconnect failed: {e}",
                exit_code=ExitCode.ERROR,
            )

    def _transfer_batches(
        self,
        to_send: list[Track],
        *,
        mode_s: str,
        target_format: str,
        device_formats: set[str],
        stems: set[str],
        serial: str,
        batch_size: int,
        quiet_s: float,
        statuses: list[dict[str, str]],
        encode_settings=None,
        resolve_encode_settings=None,
    ) -> tuple[int, int, AgentResult | None]:
        """Send tracks, optionally batched with PyMTP reconnect-on-fatal.

        Returns ``(succeeded_count, fatal_events, early_fail_or_None)``.
        """
        transcoder = FFmpegTranscoder()
        on_after = self._on_after_send_record(serial, stems) if serial else None
        fatal_events = 0
        succeeded = 0

        def on_status(path: str, status: str) -> None:
            statuses.append({"path": path, "status": status})

        use_batches = batch_size > 0 and len(to_send) > batch_size
        if not use_batches:
            # Single shot (or batch covers entire remainder).
            if mode_s == "stable":
                from mtpmanager.infra.cmd_transport import CmdTransport

                transport = CmdTransport()
            else:
                transport = self._ensure_device()
            n = transfer_tracks(
                to_send,
                target_format=target_format,
                transport=transport,
                transcoder=transcoder,
                device_formats=device_formats,
                device_guid_stems=stems,
                on_track_status=on_status,
                on_after_send=on_after,
                stop_on_fatal=True,
                encode_settings=encode_settings,
                resolve_encode_settings=resolve_encode_settings,
            )
            return int(n), 0, None

        i = 0
        while i < len(to_send):
            batch = to_send[i : i + batch_size]
            batch_n = len(batch)
            retries = 0
            while retries < MAX_BATCH_RETRIES:
                batch_statuses: list[dict[str, str]] = []

                def _batch_status(
                    path: str, status: str, _bs=batch_statuses
                ) -> None:
                    _bs.append({"path": path, "status": status})
                    on_status(path, status)

                try:
                    if mode_s == "stable":
                        from mtpmanager.infra.cmd_transport import CmdTransport

                        transport = CmdTransport()
                    else:
                        if not self._connected or self._device is None:
                            conn = self.device_connect()
                            if not conn.ok:
                                return succeeded, fatal_events, conn
                        transport = self._ensure_device()
                    n = transfer_tracks(
                        batch,
                        target_format=target_format,
                        transport=transport,
                        transcoder=transcoder,
                        device_formats=device_formats,
                        device_guid_stems=stems,
                        on_track_status=_batch_status,
                        on_after_send=on_after,
                        stop_on_fatal=True,
                        encode_settings=encode_settings,
                        resolve_encode_settings=resolve_encode_settings,
                    )
                    succeeded += int(n)
                    i += batch_n
                    break
                except TransportError as e:
                    fatal_events += 1
                    ok_paths = {
                        s["path"]
                        for s in batch_statuses
                        if s["status"] in ("done", "skipped")
                    }
                    succeeded += len(ok_paths)
                    unfinished = [
                        t
                        for t in batch
                        if t.path not in ok_paths
                        and (not t.guid or t.guid not in stems)
                    ]
                    if mode_s != "experimental":
                        # Stable: do not soft-recover; surface fatal.
                        raise
                    rec = self._reconnect_device(quiet_s=quiet_s)
                    if rec is not None:
                        return succeeded, fatal_events, rec
                    if not unfinished:
                        i += batch_n
                        break
                    batch = unfinished
                    batch_n = len(batch)
                    retries += 1
                    logger.info(
                        "sync batch retry unfinished=%d after transport error: %s",
                        batch_n,
                        e,
                    )
                except JobCancelled:
                    raise
                except Exception as e:
                    fatal_events += 1
                    logger.exception("sync batch exception: %s", e)
                    if mode_s != "experimental":
                        raise
                    rec = self._reconnect_device(quiet_s=quiet_s)
                    if rec is not None:
                        return succeeded, fatal_events, rec
                    retries += 1
            else:
                # Permanent batch failure: limp past one track so a long
                # playlist can finish after poison (rock-script pattern).
                logger.error(
                    "sync batch failed permanently at offset=%d; advancing by 1",
                    i,
                )
                i += 1
                if mode_s == "experimental":  # default PyMTP wire value
                    rec = self._reconnect_device(quiet_s=quiet_s)
                    if rec is not None:
                        return succeeded, fatal_events, rec

        return succeeded, fatal_events, None

    def sync_tracks(
        self,
        *,
        guids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        artist: str | None = None,
        album: str | None = None,
        playlist: str | None = None,
        mode: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
        push_playlist: bool = False,
        batch_size: int | None = None,
        reconnect_quiet_s: float | None = None,
    ) -> AgentResult:
        selected, unresolved, playlist_name, err = self._resolve_sync_tracks(
            guids=guids,
            paths=paths,
            artist=artist,
            album=album,
            playlist=playlist,
        )
        if err is not None:
            return err

        cfg = self._config()
        # Same default as the GUI: PyMTP unless config Stable Mode is on.
        # Use --mode stable only when deliberately recovering via mtp-sendtr.
        mode_norm = normalize_transfer_mode(mode)
        if mode_norm is None:
            mode_s = cfg.active_mode()
        elif not mode_norm:
            return fail(
                "USAGE",
                "mode must be default|pymtp|experimental (PyMTP) or "
                "stable|cmd (mtp-sendtr); omit to use config (GUI default is PyMTP)",
                exit_code=ExitCode.USAGE,
            )
        else:
            mode_s = mode_norm

        if push_playlist and not playlist_name:
            return fail(
                "USAGE",
                "push_playlist requires --playlist NAME",
                exit_code=ExitCode.USAGE,
            )

        encode_settings = cfg.resolved_audio_encode()
        target_format = encode_settings.normalized_format()

        def resolve_encode_for_track(track: Track):
            return cfg.resolved_audio_encode_for_track(track)

        device_formats = set(ZEN_VISION_M.supported_audio_formats) | set(
            GENERIC.supported_audio_formats
        )

        # Default batching for playlist-scoped sync (ZEN poison recovery).
        if batch_size is None:
            effective_batch = (
                DEFAULT_PLAYLIST_BATCH_SIZE if playlist_name else 0
            )
        else:
            effective_batch = max(0, int(batch_size))
        quiet_s = (
            DEFAULT_RECONNECT_QUIET_S
            if reconnect_quiet_s is None
            else max(0.0, float(reconnect_quiet_s))
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

        plan_data: dict[str, Any] = {
            "mode": mode_s,
            "target_format": target_format,
            "track_count": len(plan),
            "would_send": sum(1 for p in plan if p["action"] == "send"),
            "would_skip": sum(1 for p in plan if p["action"] == "skip"),
            "tracks": plan,
            "batch_size": effective_batch,
        }
        if playlist_name:
            plan_data["playlist"] = playlist_name
            plan_data["push_playlist"] = bool(push_playlist)
        if unresolved:
            plan_data["unresolved_paths"] = unresolved
            plan_data["unresolved_count"] = len(unresolved)

        if dry_run or not confirm:
            msg = (
                "Dry-run plan only"
                if dry_run
                else "Pass confirm=true to execute (or dry_run=true to plan only)"
            )
            if not dry_run:
                return AgentResult(
                    ok=False,
                    code="CONFIRM_REQUIRED",
                    message=msg,
                    data=plan_data,
                    exit_code=int(ExitCode.CONFIRM_REQUIRED),
                )
            return ok(plan_data, message=msg)

        busy = self._require_session_lock("cli-sync")
        if busy is not None:
            return busy

        to_send = [t for t in selected if not (t.guid and t.guid in stems)]
        already = len(selected) - len(to_send)

        try:
            if mode_s == "stable":
                # CmdTransport needs no open PyMTP session.
                pass
            else:
                if not self._connected or self._device is None:
                    conn = self.device_connect()
                    if not conn.ok:
                        return conn
                if not serial:
                    serial = self._device_serial or ""
                    if serial:
                        try:
                            stems = set(
                                guid_stems_on_device(serial, path=self._index_path)
                                or []
                            )
                        except Exception:
                            logger.debug(
                                "guid_stems after connect failed", exc_info=True
                            )
                        to_send = [
                            t for t in selected if not (t.guid and t.guid in stems)
                        ]
                        already = len(selected) - len(to_send)

            statuses: list[dict[str, str]] = []
            succeeded = 0
            fatal_events = 0
            if to_send:
                succeeded, fatal_events, early = self._transfer_batches(
                    to_send,
                    mode_s=mode_s,
                    target_format=target_format,
                    device_formats=device_formats,
                    stems=stems,
                    serial=serial or self._device_serial or "",
                    batch_size=effective_batch,
                    quiet_s=quiet_s,
                    statuses=statuses,
                    encode_settings=encode_settings,
                    resolve_encode_settings=resolve_encode_for_track,
                )
                if early is not None:
                    early.data = {
                        **(early.data or {}),
                        "mode": mode_s,
                        "target_format": target_format,
                        "playlist": playlist_name,
                        "requested": len(selected),
                        "already_on_device": already,
                        "to_send": len(to_send),
                        "succeeded": succeeded,
                        "fatal_events": fatal_events,
                        "statuses": statuses,
                        "unresolved_paths": unresolved,
                    }
                    return early

            album_art_result: dict[str, Any] | None = None
            if (
                mode_s == "experimental"
                and bool(getattr(cfg, "sync_album_art", True))
                and selected
            ):
                # Phase 2: abstract albums + JPEG samples (ZEN: not on tracks).
                try:
                    if not self._connected or self._device is None:
                        conn = self.device_connect()
                        if not conn.ok:
                            logger.warning(
                                "album art: connect failed after sync: %s",
                                conn.message,
                            )
                        else:
                            serial = self._device_serial or serial
                    if self._connected and self._device is not None and serial:
                        batch = push_album_art_for_tracks(
                            device=self._device,
                            serial=serial,
                            tracks=selected,
                            index_path=self._index_path,
                        )
                        album_art_result = {
                            "art_sent": batch.art_sent_count,
                            "ok": batch.ok_count,
                            "errors": batch.error_count,
                            "albums": [
                                {
                                    "name": a.name,
                                    "artist": a.artist,
                                    "album_id": a.album_id,
                                    "created": a.created,
                                    "art_sent": a.art_sent,
                                    "art_skipped": a.art_skipped,
                                    "tracks": len(a.track_ids),
                                    "error": a.error or None,
                                }
                                for a in batch.albums
                            ],
                        }
                except Exception as e:
                    logger.warning("album art push after sync failed: %s", e)
                    album_art_result = {"error": str(e)}

            push_result: dict[str, Any] | None = None
            if push_playlist and playlist_name:
                # Push needs a live PyMTP session (default transport).
                if not self._connected or self._device is None:
                    conn = self.device_connect()
                    if not conn.ok:
                        return fail(
                            conn.code or "DEVICE_ERROR",
                            conn.message
                            or "Connect failed before playlist push",
                            exit_code=conn.exit_code or int(ExitCode.ERROR),
                            data={
                                "mode": mode_s,
                                "playlist": playlist_name,
                                "requested": len(selected),
                                "already_on_device": already,
                                "succeeded": succeeded,
                                "fatal_events": fatal_events,
                                "statuses": statuses,
                                "connect": conn.to_dict(),
                            },
                        )
                push = self.playlist_push(playlist_name, confirm=True)
                push_result = push.to_dict()
                if not push.ok:
                    return fail(
                        push.code or "PLAYLIST_PUSH_FAILED",
                        push.message or "Playlist push failed after sync",
                        exit_code=push.exit_code or int(ExitCode.ERROR),
                        data={
                            "mode": mode_s,
                            "target_format": target_format,
                            "playlist": playlist_name,
                            "requested": len(selected),
                            "already_on_device": already,
                            "to_send": len(to_send),
                            "succeeded": succeeded,
                            "fatal_events": fatal_events,
                            "statuses": statuses,
                            "playlist_push": push_result,
                            "unresolved_paths": unresolved,
                        },
                    )

            return ok(
                {
                    "mode": mode_s,
                    "target_format": target_format,
                    "playlist": playlist_name,
                    "requested": len(selected),
                    "already_on_device": already,
                    "to_send": len(to_send),
                    "succeeded": succeeded,
                    "fatal_events": fatal_events,
                    "batch_size": effective_batch,
                    "statuses": statuses,
                    "unresolved_paths": unresolved,
                    "playlist_push": push_result,
                    "album_art": album_art_result,
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
                    "playlist": playlist_name,
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
