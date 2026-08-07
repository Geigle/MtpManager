#!/usr/bin/env python3
"""Rock playlist → Zen via Experimental PyMTP, batched with panic recovery.

Progress: /tmp/mtpmanager_rock_sync_state.json
Log:      /tmp/mtpmanager_rock_sync.log
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

# Project root on path when run as scripts/…
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mtpmanager.app.transfer import transfer_tracks
from mtpmanager.domain.device_profiles import GENERIC, ZEN_VISION_M
from mtpmanager.domain.models import TrackMetadata
from mtpmanager.domain.playlist_m3u import parse_m3u
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.headless.service import HeadlessService
from mtpmanager.infra.app_config import load_app_config
from mtpmanager.infra.device_index import (
    DEFAULT_MUSIC_FOLDER_ID,
    DEFAULT_STORAGE_ID,
    guid_stems_on_device,
    record_send,
)
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.infra.logging_setup import configure_logging
from mtpmanager.infra.playlists import get_playlist_by_name
from mtpmanager.infra.remote_naming import build_remote_path, split_remote_path
from mtpmanager.ports.transport import TransportError

LOG_PATH = Path("/tmp/mtpmanager_rock_sync.log")
STATE_PATH = Path("/tmp/mtpmanager_rock_sync_state.json")
BATCH = 15
QUIET_S = 15.0
MAX_BATCH_RETRIES = 4


def main() -> int:
    configure_logging()
    log = logging.getLogger("rock_sync")
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    log.addHandler(logging.StreamHandler(sys.stdout))

    svc = HeadlessService()
    cfg = load_app_config(path=svc.data_dir / "config.json")
    target_format = cfg.normalized_send_format()
    device_formats = set(ZEN_VISION_M.supported_audio_formats) | set(
        GENERIC.supported_audio_formats
    )

    pl = get_playlist_by_name("Rock", path=svc._index_path)
    if not pl:
        log.error("Rock playlist not found")
        return 1
    entries = parse_m3u(pl.m3u_text or "")
    paths = [e.path for e in entries]
    log.info("Rock playlist entries=%d", len(paths))

    selected, unresolved, _pl_name, err = svc._resolve_sync_tracks(paths=paths)
    if err is not None:
        log.error("resolve failed: %s", err.message)
        return 1
    if unresolved:
        log.warning("unresolved paths=%d (first=%s)", len(unresolved), unresolved[:3])
    log.info("resolved tracks=%d", len(selected))

    conn = None
    for attempt in range(1, 13):
        conn = svc.device_connect()
        if conn.ok:
            break
        log.warning(
            "connect attempt %d/12 failed: %s %s — waiting 10s (unplug/replug if needed)",
            attempt,
            conn.code,
            conn.message or conn.data,
        )
        try:
            svc.device_disconnect()
        except Exception:
            pass
        time.sleep(10.0)
    if conn is None or not conn.ok:
        log.error("connect failed after retries: %s", conn.to_dict() if conn else None)
        return 1
    serial = svc._device_serial or str(conn.data.get("serial") or "")
    log.info("connected serial=%s name=%s", serial, conn.data.get("name"))

    stems = set(guid_stems_on_device(serial, path=svc._index_path) or [])
    to_send = [t for t in selected if not (t.guid and t.guid in stems)]
    already = len(selected) - len(to_send)
    log.info(
        "already_on_device=%d to_send=%d mode=experimental batch=%d",
        already,
        len(to_send),
        BATCH,
    )

    state: dict = {
        "playlist": "Rock",
        "mode": "experimental",
        "total_playlist": len(selected),
        "already_on_device": already,
        "to_send": len(to_send),
        "sent_ok": 0,
        "skipped": already,
        "failed": 0,
        "fatal_events": 0,
        "done": False,
        "error": None,
        "playlist_push": None,
        "offset": 0,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))

    transcoder = FFmpegTranscoder()

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
                path=svc._index_path,
            )
            stems.add(guid)
        except Exception:
            log.debug("record_send failed", exc_info=True)

    def reconnect() -> bool:
        try:
            try:
                svc.device_disconnect()
            except Exception:
                pass
            time.sleep(QUIET_S)
            r = svc.device_connect()
            if not r.ok:
                log.error("reconnect failed: %s %s", r.code, r.message)
                return False
            log.info("reconnected after quiet %.0fs", QUIET_S)
            return True
        except Exception:
            log.exception("reconnect boom")
            return False

    i = 0
    while i < len(to_send):
        batch = to_send[i : i + BATCH]
        batch_n = len(batch)
        retries = 0
        while retries < MAX_BATCH_RETRIES:
            state["offset"] = i
            STATE_PATH.write_text(json.dumps(state, indent=2))
            log.info(
                "batch start offset=%d size=%d retry=%d progress=%d/%d sent_ok=%d",
                i,
                batch_n,
                retries,
                i,
                len(to_send),
                state["sent_ok"],
            )
            statuses: list[dict[str, str]] = []

            def on_status(path: str, status: str, _s=statuses) -> None:
                _s.append({"path": path, "status": status})
                if status in ("done", "skipped", "failed"):
                    log.info("track %s: %s", Path(path).name[:70], status)

            try:
                n = transfer_tracks(
                    batch,
                    target_format=target_format,
                    transport=svc._ensure_device(),
                    transcoder=transcoder,
                    device_formats=device_formats,
                    device_guid_stems=stems,
                    on_track_status=on_status,
                    on_after_send=on_after_send,
                    stop_on_fatal=True,
                    session_log=True,
                )
                done = sum(1 for s in statuses if s["status"] in ("done", "skipped"))
                failed = sum(1 for s in statuses if s["status"] == "failed")
                state["sent_ok"] += done
                state["failed"] += failed
                STATE_PATH.write_text(json.dumps(state, indent=2))
                log.info(
                    "batch ok offset=%d succeeded_returns=%s statuses_done=%d",
                    i,
                    n,
                    done,
                )
                i += batch_n
                break
            except TransportError as e:
                state["fatal_events"] += 1
                ok_paths = {
                    s["path"] for s in statuses if s["status"] in ("done", "skipped")
                }
                failed_paths = {
                    s["path"] for s in statuses if s["status"] == "failed"
                }
                state["sent_ok"] += len(ok_paths)
                state["failed"] += len(failed_paths)
                STATE_PATH.write_text(json.dumps(state, indent=2))
                log.error(
                    "TRANSPORT %s fatal=%s path=%s stderr=%s",
                    e,
                    e.fatal,
                    e.path,
                    (e.stderr or "")[:500],
                )
                unfinished = [
                    t
                    for t in batch
                    if t.path not in ok_paths
                    and (not t.guid or t.guid not in stems)
                ]
                if not reconnect():
                    state["error"] = f"reconnect failed: {e}"
                    STATE_PATH.write_text(json.dumps(state, indent=2))
                    return 2
                if not unfinished:
                    i += batch_n
                    break
                batch = unfinished
                batch_n = len(batch)
                retries += 1
                log.info("retry unfinished=%d after transport error", batch_n)
            except Exception as e:
                state["fatal_events"] += 1
                log.error("batch exception: %s\n%s", e, traceback.format_exc())
                if not reconnect():
                    state["error"] = str(e)
                    STATE_PATH.write_text(json.dumps(state, indent=2))
                    return 2
                retries += 1
        else:
            log.error(
                "batch failed permanently at offset=%d; advancing by 1 to limp on",
                i,
            )
            state["failed"] += 1
            i += 1
            STATE_PATH.write_text(json.dumps(state, indent=2))
            if not reconnect():
                break

    log.info("pushing playlist Rock to device…")
    try:
        push = svc.playlist_push("Rock", confirm=True)
        state["playlist_push"] = push.to_dict()
        log.info("playlist_push: %s", json.dumps(push.to_dict())[:800])
    except Exception as e:
        state["playlist_push"] = {"ok": False, "error": str(e)}
        log.exception("playlist push failed")

    state["done"] = True
    state["offset"] = i
    STATE_PATH.write_text(json.dumps(state, indent=2))
    try:
        svc.device_disconnect()
    except Exception:
        svc.close()
    log.info(
        "FINISHED sent_ok=%s failed=%s fatal_events=%s",
        state["sent_ok"],
        state["failed"],
        state["fatal_events"],
    )
    print(json.dumps(state, indent=2))
    return 0 if not state.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
