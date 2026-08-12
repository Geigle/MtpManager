"""Milestone C / Phase 2 headless ops (podcasts, pull, enrich, video, sync job).

Mixed into :class:`~mtpmanager.headless.service.HeadlessService`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Sequence

from mtpmanager.app.device_ops import (
    enrich_track_refs_with_embedded_fallback,
    pick_library_root,
    prepare_and_send_video,
    retrieve_track,
)
from mtpmanager.app.podcast_ops import (
    add_episode_to_day_host_playlist,
    download_episode,
    episode_as_track,
    mark_episodes_device_synced,
    pending_episodes_for_device_sync,
    refresh_podcast,
    remove_episode_from_day_host_playlist,
    run_full_sync_host_pass,
    subscribe_feed,
)
from mtpmanager.app.podcast_schedule import podcast_day_playlist_name as day_name_fn
from mtpmanager.domain.models import DeviceTrackRef, Track
from mtpmanager.domain.track_id import is_track_guid, new_track_guid
from mtpmanager.headless.dto import AgentResult, ExitCode, fail, ok
from mtpmanager.infra.device_index import guid_stems_on_device, list_known_devices
from mtpmanager.infra.playlists import get_playlist_by_name
from mtpmanager.infra.podcast_index import (
    delete_podcast,
    get_episode,
    get_episode_by_guid,
    get_podcast,
    list_episodes,
    list_podcasts,
)
from mtpmanager.infra.sync_job import (
    clear_sync_job,
    load_sync_job,
    new_sync_job,
    save_sync_job,
    sync_job_path,
)
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)


def _podcast_dict(p: Any) -> dict[str, Any]:
    return {
        "id": int(p.id),
        "title": p.title or "",
        "author": p.author or "",
        "feed_url": p.feed_url or "",
        "episode_count": int(getattr(p, "episode_count", 0) or 0),
        "auto_update": bool(getattr(p, "auto_update", True)),
        "image_url": getattr(p, "image_url", "") or "",
        "last_fetched_at": getattr(p, "last_fetched_at", "") or "",
        "playback_speed": float(getattr(p, "playback_speed", 1.0) or 1.0),
    }


def _episode_dict(e: Any) -> dict[str, Any]:
    return {
        "id": int(e.id),
        "podcast_id": int(e.podcast_id),
        "guid": e.guid or "",
        "title": e.title or "",
        "pub_date": e.pub_date or "",
        "duration_sec": float(e.duration_sec or 0),
        "local_path": e.local_path or "",
        "downloaded": bool(e.local_path and os.path.isfile(e.local_path or "")),
        "pending_device_sync": bool(getattr(e, "pending_device_sync", False)),
        "is_video": bool(getattr(e, "is_video", False)),
        "enclosure_bytes": int(getattr(e, "enclosure_bytes", 0) or 0),
    }


class Phase2Mixin:
    """Podcast / device media / sync-job methods for HeadlessService."""

    # --- podcasts host -----------------------------------------------------

    def podcast_list(self) -> AgentResult:
        shows = list_podcasts(path=self._index_path)
        return ok(
            {
                "podcasts": [_podcast_dict(p) for p in shows],
                "count": len(shows),
            }
        )

    def podcast_show(self, podcast_id: int | None = None, *, title: str | None = None) -> AgentResult:
        show = None
        if podcast_id is not None and int(podcast_id) > 0:
            show = get_podcast(int(podcast_id), path=self._index_path)
        elif (title or "").strip():
            t = title.strip().casefold()
            for p in list_podcasts(path=self._index_path):
                if (p.title or "").casefold() == t:
                    show = p
                    break
        else:
            return fail(
                "USAGE",
                "podcast_id or title is required",
                exit_code=ExitCode.USAGE,
            )
        if show is None:
            return fail(
                "NOT_FOUND",
                "Podcast not found",
                exit_code=ExitCode.NOT_FOUND,
            )
        return ok({"podcast": _podcast_dict(show)})

    def podcast_episodes(
        self,
        podcast_id: int,
        *,
        limit: int = 50,
    ) -> AgentResult:
        show = get_podcast(int(podcast_id), path=self._index_path)
        if show is None:
            return fail(
                "NOT_FOUND",
                f"Podcast id {podcast_id} not found",
                exit_code=ExitCode.NOT_FOUND,
            )
        lim = max(1, min(int(limit or 50), 5000))
        eps = list_episodes(int(podcast_id), limit=lim, path=self._index_path)
        return ok(
            {
                "podcast_id": int(podcast_id),
                "title": show.title or "",
                "episodes": [_episode_dict(e) for e in eps],
                "returned": len(eps),
            }
        )

    def podcast_subscribe(self, feed_url: str, *, initial_limit: int = 20) -> AgentResult:
        url = (feed_url or "").strip()
        if not url:
            return fail("USAGE", "feed_url is required", exit_code=ExitCode.USAGE)
        try:
            podcast, n = subscribe_feed(
                url,
                initial_limit=max(0, int(initial_limit or 20)),
                path=self._index_path,
            )
        except Exception as e:
            logger.exception("podcast_subscribe failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                "podcast": _podcast_dict(podcast),
                "new_episodes": int(n),
            },
            message=f"Subscribed {podcast.title!r} (+{n} episodes)",
        )

    def podcast_unsubscribe(self, podcast_id: int, *, confirm: bool = False) -> AgentResult:
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to unsubscribe (deletes show + episode rows)",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"podcast_id": int(podcast_id)},
            )
        show = get_podcast(int(podcast_id), path=self._index_path)
        if show is None:
            return fail(
                "NOT_FOUND",
                f"Podcast id {podcast_id} not found",
                exit_code=ExitCode.NOT_FOUND,
            )
        ok_del = delete_podcast(int(podcast_id), path=self._index_path)
        if not ok_del:
            return fail("ERROR", "delete_podcast failed", exit_code=ExitCode.ERROR)
        return ok(
            {"podcast_id": int(podcast_id), "title": show.title or "", "deleted": True},
            message=f"Unsubscribed {show.title!r}",
        )

    def podcast_refresh(self, podcast_id: int) -> AgentResult:
        show = get_podcast(int(podcast_id), path=self._index_path)
        if show is None:
            return fail(
                "NOT_FOUND",
                f"Podcast id {podcast_id} not found",
                exit_code=ExitCode.NOT_FOUND,
            )
        try:
            podcast, n = refresh_podcast(int(podcast_id), path=self._index_path)
        except Exception as e:
            logger.exception("podcast_refresh failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {"podcast": _podcast_dict(podcast), "new_episodes": int(n)},
            message=f"Refreshed {podcast.title!r} (+{n} new)",
        )

    def podcast_download_episode(
        self,
        episode_id: int,
        *,
        prefer_video: bool = False,
    ) -> AgentResult:
        ep = get_episode(int(episode_id), path=self._index_path)
        if ep is None:
            return fail(
                "NOT_FOUND",
                f"Episode id {episode_id} not found",
                exit_code=ExitCode.NOT_FOUND,
            )
        cfg = self._config()
        if prefer_video and not bool(getattr(cfg, "enable_experimental_tools", False)):
            return fail(
                "USAGE",
                "prefer_video requires enable_experimental_tools (config patch)",
                exit_code=ExitCode.USAGE,
            )
        try:
            ready = download_episode(
                ep,
                path=self._index_path,
                data_dir=self.data_dir,
                prefer_video=bool(prefer_video),
            )
        except Exception as e:
            logger.exception("podcast_download_episode failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {"episode": _episode_dict(ready)},
            message=f"Downloaded episode {ready.title!r}",
        )

    def podcast_full_sync_host(
        self,
        *,
        podcast_ids: Sequence[int] | None = None,
        max_new_per_show: int | None = None,
    ) -> AgentResult:
        """Host-only: refresh feeds, download N new episodes, mark pending (no USB)."""
        cfg = self._config()
        limit = (
            int(max_new_per_show)
            if max_new_per_show is not None
            else int(cfg.podcast_max_new_per_show or 1)
        )
        try:
            result = run_full_sync_host_pass(
                podcast_ids=list(podcast_ids) if podcast_ids else None,
                max_new_per_show=limit,
                path=self._index_path,
                data_dir=self.data_dir,
                target_audio_format=cfg.normalized_send_format(),
                tracknumber_as_date=bool(cfg.podcast_tracknumber_as_date),
                title_date_prefix=bool(cfg.podcast_title_date_prefix),
                since_last_full_sync=str(cfg.podcast_last_full_sync_local_date or ""),
            )
        except Exception as e:
            logger.exception("podcast_full_sync_host failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        data = {
            "shows_processed": result.shows_processed,
            "refreshed_ids": list(result.refreshed_ids),
            "downloaded": [_episode_dict(e) for e in result.downloaded],
            "downloaded_count": len(result.downloaded),
            "errors": list(result.errors),
            "max_new_per_show": limit,
            "note": (
                "Host only — no device transfer. Use podcast_sync_pending "
                "or Library → Finish Sync for on-device push."
            ),
        }
        return ok(
            data,
            message=(
                f"Host pass: {len(result.downloaded)} downloaded, "
                f"{len(result.errors)} error(s)"
            ),
        )

    def podcast_day_playlist_show(self) -> AgentResult:
        name = day_name_fn()
        pl = get_playlist_by_name(name, path=self._index_path)
        if pl is None:
            return ok(
                {
                    "name": name,
                    "exists": False,
                    "track_count": 0,
                    "paths": [],
                },
                message=f"No day playlist yet: {name}",
            )
        from mtpmanager.domain.playlist_m3u import parse_m3u

        entries = parse_m3u(pl.m3u_text or "")
        return ok(
            {
                "name": pl.name,
                "exists": True,
                "id": pl.id,
                "track_count": len(entries),
                "paths": [e.path for e in entries],
            }
        )

    def podcast_day_add(self, *, episode_id: int | None = None, guid: str | None = None) -> AgentResult:
        ep = None
        if episode_id is not None:
            ep = get_episode(int(episode_id), path=self._index_path)
        elif guid and is_track_guid(guid.strip()):
            ep = get_episode_by_guid(guid.strip(), path=self._index_path)
        if ep is None:
            return fail(
                "NOT_FOUND",
                "Episode not found (pass episode_id or guid)",
                exit_code=ExitCode.NOT_FOUND,
            )
        result = add_episode_to_day_host_playlist(ep, path=self._index_path)
        if result is None:
            return fail(
                "ERROR",
                "Could not add episode to day playlist (missing GUID?)",
                exit_code=ExitCode.ERROR,
            )
        return ok(
            {
                "name": result.name,
                "added": result.added,
                "guids": list(result.guids),
                "track_count": len(result.guids),
            },
            message=f"Day playlist {result.name!r} (+{result.added})",
        )

    def podcast_day_remove(self, guid: str) -> AgentResult:
        g = (guid or "").strip().lower()
        if not is_track_guid(g):
            return fail("USAGE", "guid must be 32-hex track GUID", exit_code=ExitCode.USAGE)
        ok_rm = remove_episode_from_day_host_playlist(g, path=self._index_path)
        return ok(
            {"guid": g, "removed": bool(ok_rm), "name": day_name_fn()},
            message="Removed from day playlist" if ok_rm else "Day playlist not found",
        )

    def podcast_sync_pending(
        self,
        *,
        dry_run: bool = False,
        confirm: bool = False,
        mode: str | None = None,
        batch_size: int | None = None,
        push_day_playlist: bool = False,
    ) -> AgentResult:
        """Transfer pending podcast episodes (host files) to the device.

        Does **not** auto-push day playlist unless *push_day_playlist* (Finish Sync).
        """
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
                logger.debug("guid_stems for podcast sync failed", exc_info=True)

        pending = pending_episodes_for_device_sync(
            device_guids=stems, path=self._index_path
        )
        tracks: list[Track] = []
        skipped: list[dict[str, Any]] = []
        cfg = self._config()
        for ep in pending:
            show = get_podcast(int(ep.podcast_id), path=self._index_path)
            if show is None:
                skipped.append({"episode_id": ep.id, "reason": "missing_show"})
                continue
            try:
                tracks.append(
                    episode_as_track(
                        ep,
                        show,
                        tracknumber_as_date=bool(cfg.podcast_tracknumber_as_date),
                        title_date_prefix=bool(cfg.podcast_title_date_prefix),
                    )
                )
            except Exception as e:
                skipped.append(
                    {"episode_id": ep.id, "reason": str(e), "title": ep.title or ""}
                )

        if not tracks:
            return ok(
                {
                    "pending": 0,
                    "pending_ready": 0,
                    "tracks": 0,
                    "skipped": skipped,
                    "would_send": 0,
                    "would_skip": 0,
                    "note": "No pending podcast episodes ready for device sync",
                }
            )

        result = self.sync_tracks(
            tracks=tracks,
            mode=mode,
            dry_run=dry_run,
            confirm=confirm,
            batch_size=batch_size if batch_size is not None else 15,
        )
        if result.data is not None:
            result.data["podcast_skipped"] = skipped
            result.data["pending_ready"] = len(tracks)

        if (
            result.ok
            and confirm
            and not dry_run
            and tracks
            and int(result.data.get("succeeded") or 0) > 0
        ):
            # Mark only successfully implied batch — if all succeeded mark all.
            succ = int(result.data.get("succeeded") or 0)
            if succ >= len(tracks):
                mark_episodes_device_synced(
                    [ep.id for ep in pending if ep.id],
                    path=self._index_path,
                )
            if push_day_playlist:
                day = day_name_fn()
                push = self.playlist_push(day, confirm=True)
                result.data["day_playlist_push"] = push.to_dict()
                result.data["note_day"] = (
                    "Day playlist push is explicit (Finish Sync semantics); "
                    "not run after full-sync host flood alone."
                )

        return result

    # --- device pull / enrich / video --------------------------------------

    def device_pull(
        self,
        object_ids: Sequence[int],
        *,
        dest: str | None = None,
        confirm: bool = False,
    ) -> AgentResult:
        """Download object id(s) to a library root or dest folder.

        Risks: R3 USB exclusive, R4 large download. Prefer host library over pull.
        """
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to pull from device (USB download; R3/R4)",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"object_ids": list(object_ids)},
            )
        ids = [int(x) for x in object_ids if int(x) > 0]
        if not ids:
            return fail(
                "USAGE",
                "object_ids required (positive integers)",
                exit_code=ExitCode.USAGE,
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-pull")
        if busy is not None:
            return busy

        dest_dir = (dest or "").strip()
        if not dest_dir:
            roots = self._current_library_roots()
            dest_dir = pick_library_root(roots) or ""
        if not dest_dir:
            return fail(
                "USAGE",
                "No dest and no library root; pass dest= or configure roots",
                exit_code=ExitCode.USAGE,
            )
        os.makedirs(dest_dir, exist_ok=True)

        results: list[dict[str, Any]] = []
        for oid in ids:
            ref = DeviceTrackRef(item_id=oid, name="")
            try:
                item = retrieve_track(self._device, ref, dest_dir)
                results.append(
                    {
                        "object_id": oid,
                        "ok": True,
                        "path": item.path,
                        "tags_written": bool(item.tags_written),
                    }
                )
            except TransportError as e:
                results.append(
                    {
                        "object_id": oid,
                        "ok": False,
                        "error": str(e),
                        "fatal": bool(getattr(e, "fatal", False)),
                    }
                )
                if getattr(e, "fatal", False):
                    return fail(
                        "TRANSPORT_FATAL",
                        f"Pull aborted after object {oid}: {e}",
                        exit_code=ExitCode.TRANSPORT_FATAL,
                        data={"results": results},
                    )
            except Exception as e:
                logger.exception("device_pull id=%s", oid)
                results.append({"object_id": oid, "ok": False, "error": str(e)})

        ok_n = sum(1 for r in results if r.get("ok"))
        return ok(
            {
                "dest": dest_dir,
                "succeeded": ok_n,
                "failed": len(results) - ok_n,
                "results": results,
                "risks": ["R3", "R4"],
            },
            message=f"Pulled {ok_n}/{len(results)} object(s)",
        )

    def device_enrich_tags(
        self,
        object_ids: Sequence[int],
        *,
        confirm: bool = False,
    ) -> AgentResult:
        """Explicit Get_Trackmetadata (+ download fallback). Hazardous (R1/R2).

        Not used for normal inventory. Small batches only; fatal aborts rest.
        """
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true for tag enrich (USB metadata/download; "
                "R1 session poison, R2 hang — not for bulk inventory)",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"object_ids": list(object_ids)},
            )
        ids = [int(x) for x in object_ids if int(x) > 0]
        if not ids:
            return fail("USAGE", "object_ids required", exit_code=ExitCode.USAGE)
        if len(ids) > 25:
            return fail(
                "USAGE",
                "Refuse >25 ids per call (hang/poison risk); split into small batches",
                exit_code=ExitCode.USAGE,
                data={"count": len(ids)},
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-enrich")
        if busy is not None:
            return busy

        refs = [DeviceTrackRef(item_id=oid, name="") for oid in ids]
        try:
            result = enrich_track_refs_with_embedded_fallback(
                self._device, refs, stop_on_fatal=True
            )
        except TransportError as e:
            return fail(
                "TRANSPORT_FATAL",
                str(e),
                exit_code=ExitCode.TRANSPORT_FATAL,
                data={"risks": ["R1", "R2"]},
            )
        except Exception as e:
            logger.exception("device_enrich_tags failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)

        rows = []
        for ref in result.refs:
            rows.append(
                {
                    "item_id": ref.item_id,
                    "name": ref.name,
                    "title": ref.title,
                    "artist": ref.artist,
                    "album": ref.album,
                    "genre": ref.genre,
                    "tracknumber": ref.tracknumber,
                }
            )
        return ok(
            {
                "updated": int(result.updated),
                "failed": int(result.failed),
                "from_device": int(getattr(result, "from_device", 0) or 0),
                "from_embedded": int(getattr(result, "from_embedded", 0) or 0),
                "tracks": rows,
                "risks": ["R1", "R2", "R3"],
                "note": (
                    "After poison: disconnect, quiet wait, reconnect, "
                    "device_refresh_index — do not continue enrich."
                ),
            },
            message=f"Enriched updated={result.updated} failed={result.failed}",
        )

    def device_send_video(
        self,
        path: str,
        *,
        parent_id: int = 120,
        encode: bool = True,
        preset_id: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
        title: str | None = None,
    ) -> AgentResult:
        """Send a host video to Video (120) or TV (124). Risks R1/R3/R4 (encode time)."""
        src = (path or "").strip()
        if not src or not os.path.isfile(src):
            return fail(
                "NOT_FOUND",
                f"Video file not found: {src}",
                exit_code=ExitCode.NOT_FOUND,
            )
        parent = int(parent_id)
        if parent not in (120, 124):
            return fail(
                "USAGE",
                "parent_id must be 120 (Video) or 124 (TV) for agent API",
                exit_code=ExitCode.USAGE,
            )
        size = os.path.getsize(src)
        plan = {
            "path": src,
            "parent_id": parent,
            "parent_label": "Video" if parent == 120 else "TV",
            "encode": bool(encode),
            "preset_id": preset_id or "zen_avi_xvid_mp3",
            "filesize": size,
            "title": (title or Path(src).stem),
            "risks": ["R1", "R3", "R4"],
            "note": (
                "Encode may take a long time. ObjectFileName is title-style "
                "(not library GUID). Experimental tools not required for AVI·XviD."
            ),
        }
        if dry_run or not confirm:
            if not dry_run:
                return fail(
                    "CONFIRM_REQUIRED",
                    "Pass confirm=true to send video (or dry_run=true to plan)",
                    exit_code=ExitCode.CONFIRM_REQUIRED,
                    data=plan,
                )
            return ok(plan, message="Video send dry-run")

        busy = self._require_session_lock("cli-video")
        if busy is not None:
            return busy
        if not self._connected or self._device is None:
            conn = self.device_connect()
            if not conn.ok:
                return conn

        from mtpmanager.domain.device_profiles import ZEN_AVI_XVID_MP3, ZEN_VISION_M

        profile = ZEN_AVI_XVID_MP3
        if preset_id:
            opts = getattr(ZEN_VISION_M, "video_options", None)
            if opts is not None:
                found = opts.preset_by_id(preset_id)
                if found is not None:
                    profile = found
        try:
            # PymtpDevice implements Transport.send_track (GUI parity).
            send_result = prepare_and_send_video(
                self._device,
                src,
                parent_id=parent,
                encode_profile=profile if encode else None,
                encode_for_device=bool(encode),
                title=title,
                guid=new_track_guid(),
                allowed_parents=frozenset({120, 124}),
            )
        except TransportError as e:
            return fail(
                "TRANSPORT_FATAL" if getattr(e, "fatal", False) else "DEVICE_ERROR",
                str(e),
                exit_code=(
                    ExitCode.TRANSPORT_FATAL
                    if getattr(e, "fatal", False)
                    else ExitCode.ERROR
                ),
                data=plan,
            )
        except Exception as e:
            logger.exception("device_send_video failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR, data=plan)

        return ok(
            {
                **plan,
                "object_id": send_result.object_id,
                "remote_basename": send_result.remote_basename,
                "encoded": bool(send_result.encoded),
            },
            message=f"Sent video → parent {parent} id={send_result.object_id}",
        )

    # --- sync job ----------------------------------------------------------

    def sync_job_status(self) -> AgentResult:
        job = load_sync_job(path=sync_job_path(data_dir=self.data_dir))
        if job is None:
            return ok(
                {"exists": False, "resumable": False},
                message="No sync job on disk",
            )
        return ok(
            {
                "exists": True,
                "resumable": job.is_resumable(),
                "kind": job.kind,
                "label": job.label,
                "status": job.status,
                "mode": job.mode,
                "target_format": job.target_format,
                "total": job.total,
                "succeeded": job.succeeded,
                "remaining": job.remaining,
                "next_index": job.next_index,
                "last_error": job.last_error,
                "last_failed_path": job.last_failed_path,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "summary": job.summary_line(),
            }
        )

    def sync_job_clear(self, *, confirm: bool = False) -> AgentResult:
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to clear durable sync job",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        clear_sync_job(path=sync_job_path(data_dir=self.data_dir))
        return ok({"cleared": True})

    def sync_resume(
        self,
        *,
        dry_run: bool = False,
        confirm: bool = False,
        batch_size: int | None = None,
    ) -> AgentResult:
        """Resume a failed/cancelled durable sync job (remaining paths). Risk R1."""
        job = load_sync_job(path=sync_job_path(data_dir=self.data_dir))
        if job is None:
            return fail(
                "NOT_FOUND",
                "No sync job to resume",
                exit_code=ExitCode.NOT_FOUND,
            )
        if not job.is_resumable():
            return fail(
                "USAGE",
                f"Job not resumable (status={job.status}, remaining={job.remaining})",
                exit_code=ExitCode.USAGE,
                data=self.sync_job_status().data,
            )
        paths = job.remaining_paths()
        result = self.sync_tracks(
            paths=paths,
            mode=job.mode,
            dry_run=dry_run,
            confirm=confirm,
            batch_size=batch_size if batch_size is not None else 15,
            persist_job=False,
        )
        if result.ok and confirm and not dry_run:
            succ = int(result.data.get("succeeded") or 0)
            # Advance job for successful prefix of remaining paths.
            # Headless statuses list path/status pairs; mark done for succeeded sends.
            # Simple approach: if all remaining succeeded, complete; else mark failed head.
            if succ >= len(paths) and int(result.data.get("fatal_events") or 0) == 0:
                job.mark_completed()
                save_sync_job(job, path=sync_job_path(data_dir=self.data_dir))
            elif succ > 0:
                for p in paths[:succ]:
                    job.mark_path_done(p)
                if int(result.data.get("fatal_events") or 0) > 0:
                    fail_path = paths[succ] if succ < len(paths) else paths[-1]
                    job.mark_path_failed(fail_path, result.message or "fatal")
                save_sync_job(job, path=sync_job_path(data_dir=self.data_dir))
            result.data["sync_job"] = self.sync_job_status().data
        return result
