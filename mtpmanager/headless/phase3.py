"""Milestone D / Phase 3 headless ops (experimental / power tools).

Mixed into :class:`~mtpmanager.headless.service.HeadlessService`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Sequence

from mtpmanager.app.device_ops import (
    create_folder as device_create_folder_op,
    delete_all_tracks,
    delete_object as device_delete_object,
    list_tracks,
)
from mtpmanager.app.playlist_device import (
    find_device_playlist_by_name,
    load_device_playlists_for_lookup,
    move_ids_by_indices,
    playlist_display_name,
    remove_ids_at_indices,
    resolve_device_playlist_to_host_tracks,
    save_resolved_tracks_as_host_playlist,
)
from mtpmanager.app.retail_ops import package_retail_from_export, restore_retail_package
from mtpmanager.app.shrink import (
    collect_shrink_items,
    resolve_shrink_encode_settings,
    shrink_items,
    suggest_shrink_preset,
)
from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import DeviceTrackRef, Track
from mtpmanager.domain.playlist_shuffle import merge_shuffle, rng_from_seed_track, spotify_shuffle
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.headless.dto import AgentResult, ExitCode, fail, ok
from mtpmanager.infra.device_index import (
    item_ids_for_guids,
    list_known_devices,
    remove_by_item_id,
)
from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)

DELETE_ALL_PHRASE = "DELETE ALL TRACKS"


class Phase3Mixin:
    """Experimental / niche agent tools for HeadlessService."""

    def _require_experimental(self) -> AgentResult | None:
        cfg = self._config()
        if bool(getattr(cfg, "enable_experimental_tools", False)):
            return None
        return fail(
            "USAGE",
            "Requires enable_experimental_tools=true "
            "(config patch {\"enable_experimental_tools\": true})",
            exit_code=ExitCode.USAGE,
        )

    def _device_serial_or_known(self) -> str:
        serial = self._device_serial or ""
        if serial:
            return serial
        known = list_known_devices(path=self._index_path)
        if known:
            return str(known[0].get("serial") or "")
        return ""

    # --- retail ------------------------------------------------------------

    def retail_package(
        self,
        export_path: str,
        zip_path: str,
        *,
        confirm: bool = False,
    ) -> AgentResult:
        """Package a retail export folder into a zip (host only). Experimental."""
        exp = self._require_experimental()
        if exp is not None:
            return exp
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to write retail package zip",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        src = Path(export_path)
        dest = Path(zip_path)
        if not src.is_dir():
            return fail(
                "NOT_FOUND",
                f"export_path is not a directory: {export_path}",
                exit_code=ExitCode.NOT_FOUND,
            )
        try:
            result = package_retail_from_export(src, dest)
        except Exception as e:
            logger.exception("retail_package failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                "export_path": str(src),
                "zip_path": str(dest),
                "result": str(result) if result is not None else None,
            },
            message=f"Packaged retail export → {dest}",
        )

    def retail_restore(
        self,
        package_path: str,
        *,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> AgentResult:
        """Restore a retail package zip/dir to the device. Risks R1/R3/R5."""
        exp = self._require_experimental()
        if exp is not None:
            return exp
        pkg = Path(package_path)
        if not pkg.exists():
            return fail(
                "NOT_FOUND",
                f"package not found: {package_path}",
                exit_code=ExitCode.NOT_FOUND,
            )
        plan = {
            "package_path": str(pkg),
            "risks": ["R1", "R3", "R5"],
            "note": "Sends demo basenames (no GUID ObjectFileNames).",
        }
        if dry_run or not confirm:
            if not dry_run:
                return fail(
                    "CONFIRM_REQUIRED",
                    "Pass confirm=true to restore retail package (or dry_run)",
                    exit_code=ExitCode.CONFIRM_REQUIRED,
                    data=plan,
                )
            return ok(plan, message="Retail restore dry-run (no USB)")

        busy = self._require_session_lock("cli-retail-restore")
        if busy is not None:
            return busy
        if not self._connected or self._device is None:
            conn = self.device_connect()
            if not conn.ok:
                return conn
        try:
            result = restore_retail_package(self._device, pkg)
        except TransportError as e:
            return fail(
                "TRANSPORT_FATAL" if getattr(e, "fatal", False) else "DEVICE_ERROR",
                str(e),
                exit_code=(
                    ExitCode.TRANSPORT_FATAL
                    if getattr(e, "fatal", False)
                    else ExitCode.ERROR
                ),
            )
        except Exception as e:
            logger.exception("retail_restore failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                **plan,
                "total": result.total,
                "succeeded": result.succeeded,
                "failed": result.failed,
                "skipped": result.skipped,
                "aborted": result.aborted,
                "errors": list(result.errors or [])[:20],
            },
            message=(
                f"Retail restore succeeded={result.succeeded}/{result.total} "
                f"failed={result.failed}"
            ),
        )

    # --- shrink ------------------------------------------------------------

    def device_shrink(
        self,
        *,
        guids: Sequence[str] | None = None,
        artist: str | None = None,
        album: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> AgentResult:
        """Re-encode on-device tracks more aggressively (quality loss). R1/R3/R5."""
        tracks = self._load_tracks()
        selected: list[Track] = []
        gset = {(g or "").strip().lower() for g in (guids or []) if (g or "").strip()}
        art = (artist or "").strip()
        alb = (album or "").strip()
        if gset:
            for t in tracks:
                if (t.guid or "").lower() in gset:
                    selected.append(t)
        if art or alb:
            art_cf = art.casefold()
            alb_cf = alb.casefold()
            for t in tracks:
                pa = primary_artist(t).casefold()
                album_ok = (not alb) or (t.meta.album or "").casefold() == alb_cf
                artist_ok = (not art) or pa == art_cf or (t.meta.artist or "").casefold() == art_cf
                if artist_ok and album_ok and t not in selected:
                    selected.append(t)
        if not selected:
            return fail(
                "USAGE",
                "No tracks resolved; pass guids and/or artist/album",
                exit_code=ExitCode.USAGE,
            )

        serial = self._device_serial_or_known()
        if not serial:
            return fail(
                "USAGE",
                "No device serial; connect and refresh-index first",
                exit_code=ExitCode.USAGE,
            )
        items, missing = collect_shrink_items(selected, serial=serial)
        plan_rows = [
            {
                "guid": it.guid,
                "item_id": it.item_id,
                "title": it.track.meta.title if it.track.meta else "",
                "artist": it.track.meta.artist if it.track.meta else "",
            }
            for it in items
        ]
        plan = {
            "serial": serial,
            "shrinkable": len(items),
            "missing_on_device": len(missing),
            "items": plan_rows[:100],
            "risks": ["R1", "R3", "R5"],
            "note": (
                "Deletes and re-sends each object at lower bitrate; "
                "rewrites device playlists that referenced old ids. Quality loss."
            ),
        }
        if dry_run or not confirm:
            if not dry_run:
                return fail(
                    "CONFIRM_REQUIRED",
                    "Pass confirm=true to shrink on-device tracks (or dry_run)",
                    exit_code=ExitCode.CONFIRM_REQUIRED,
                    data=plan,
                )
            return ok(plan, message=f"Shrink dry-run: {len(items)} item(s)")

        if not items:
            return ok(plan, message="Nothing on device to shrink")

        busy = self._require_session_lock("cli-shrink")
        if busy is not None:
            return busy
        if not self._connected or self._device is None:
            conn = self.device_connect()
            if not conn.ok:
                return conn

        cfg = self._config()

        def encode_for_item(item):
            base = cfg.resolved_audio_encode_for_track(item.track)
            suggested = suggest_shrink_preset(item.track, configured=base)
            if suggested is not None:
                return resolve_shrink_encode_settings(
                    item.track, configured=base, user_override=suggested.settings
                )
            return resolve_shrink_encode_settings(item.track, configured=base)

        try:
            result = shrink_items(
                items,
                serial=serial,
                device=self._device,
                transport=self._device,
                transcoder=FFmpegTranscoder(),
                encode_for_item=encode_for_item,
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
            logger.exception("device_shrink failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR, data=plan)

        return ok(
            {
                **plan,
                "attempted": result.attempted,
                "deleted": result.deleted,
                "resent": result.resent,
                "playlists_updated": result.playlists_updated,
                "errors": list(result.errors or [])[:20],
            },
            message=(
                f"Shrink resent={result.resent}/{result.attempted} "
                f"playlist_updates={result.playlists_updated}"
            ),
        )

    # --- delete all / bulk / folder ----------------------------------------

    def device_delete_all_tracks(
        self,
        *,
        confirm: bool = False,
        confirm_phrase: str = "",
    ) -> AgentResult:
        """Delete all music/video track objects. Extreme. Experimental. R1/R3/R5."""
        exp = self._require_experimental()
        if exp is not None:
            return exp
        if not confirm or (confirm_phrase or "").strip() != DELETE_ALL_PHRASE:
            return fail(
                "CONFIRM_REQUIRED",
                f'Pass confirm=true and confirm_phrase="{DELETE_ALL_PHRASE}"',
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"required_phrase": DELETE_ALL_PHRASE, "risks": ["R1", "R3", "R5"]},
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-delete-all")
        if busy is not None:
            return busy
        try:
            result = delete_all_tracks(self._device, stop_on_fatal=True)
        except Exception as e:
            logger.exception("device_delete_all_tracks failed")
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        # Best-effort cache cleanup
        serial = self._device_serial_or_known()
        if serial and result.deleted_ids:
            for oid in result.deleted_ids:
                try:
                    remove_by_item_id(serial, int(oid), path=self._index_path)
                except Exception:
                    pass
        return ok(
            {
                "total": result.total,
                "deleted": result.deleted,
                "aborted": result.aborted,
                "cancelled": result.cancelled,
                "failed_id": result.failed_id,
                "risks": ["R1", "R3", "R5"],
            },
            message=f"Deleted {result.deleted}/{result.total} track(s)",
        )

    def device_create_folder(
        self,
        name: str,
        *,
        parent_id: int = 100,
        confirm: bool = False,
    ) -> AgentResult:
        """Create MTP folder. String/ctypes hazards (pymtp binding). R3."""
        clean = (name or "").strip()
        if not clean:
            return fail("USAGE", "name is required", exit_code=ExitCode.USAGE)
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to create a folder on the device",
                exit_code=ExitCode.CONFIRM_REQUIRED,
                data={"name": clean, "parent_id": int(parent_id)},
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-mkdir")
        if busy is not None:
            return busy
        try:
            folder_id = device_create_folder_op(
                self._device, clean, parent=int(parent_id)
            )
        except Exception as e:
            logger.exception("device_create_folder failed")
            return fail(
                "DEVICE_ERROR",
                str(e),
                exit_code=ExitCode.ERROR,
                data={
                    "note": "See pymtp-binding-hazards.md (string/ctypes class D)",
                },
            )
        return ok(
            {
                "folder_id": int(folder_id),
                "name": clean,
                "parent_id": int(parent_id),
                "risks": ["R3"],
            },
            message=f"Created folder id={folder_id} name={clean!r}",
        )

    def device_delete_bulk(
        self,
        *,
        artist: str | None = None,
        album: str | None = None,
        object_ids: Sequence[int] | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> AgentResult:
        """Delete by artist/album (host GUID join) or explicit object ids. R1/R3/R5."""
        ids: list[int] = []
        labels: list[dict[str, Any]] = []
        serial = self._device_serial_or_known()

        if object_ids:
            ids = [int(x) for x in object_ids if int(x) > 0]
            labels = [{"item_id": i} for i in ids]
        else:
            art = (artist or "").strip()
            alb = (album or "").strip()
            if not art and not alb:
                return fail(
                    "USAGE",
                    "Pass artist/album and/or object_ids",
                    exit_code=ExitCode.USAGE,
                )
            if not serial:
                return fail(
                    "USAGE",
                    "No device serial for GUID join; refresh-index first",
                    exit_code=ExitCode.USAGE,
                )
            tracks = self._load_tracks()
            art_cf = art.casefold()
            alb_cf = alb.casefold()
            matched: list[Track] = []
            for t in tracks:
                if not t.guid:
                    continue
                pa = primary_artist(t).casefold()
                album_ok = (not alb) or (t.meta.album or "").casefold() == alb_cf
                artist_ok = (
                    (not art)
                    or pa == art_cf
                    or (t.meta.artist or "").casefold() == art_cf
                )
                if artist_ok and album_ok:
                    matched.append(t)
            mapping = item_ids_for_guids(
                serial, [t.guid for t in matched if t.guid], path=self._index_path
            )
            for t in matched:
                oid = mapping.get(t.guid)
                if oid and int(oid) > 0:
                    ids.append(int(oid))
                    labels.append(
                        {
                            "item_id": int(oid),
                            "guid": t.guid,
                            "title": t.meta.title,
                            "artist": t.meta.artist,
                            "album": t.meta.album,
                        }
                    )

        # unique preserve order
        seen: set[int] = set()
        uniq: list[int] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        ids = uniq

        plan = {
            "count": len(ids),
            "items": labels[:200],
            "artist": artist or "",
            "album": album or "",
            "risks": ["R1", "R3", "R5"],
        }
        if dry_run or not confirm:
            if not dry_run:
                return fail(
                    "CONFIRM_REQUIRED",
                    "Pass confirm=true to delete listed objects (or dry_run)",
                    exit_code=ExitCode.CONFIRM_REQUIRED,
                    data=plan,
                )
            return ok(plan, message=f"Bulk delete dry-run: {len(ids)} object(s)")

        if not ids:
            return ok(plan, message="Nothing to delete")
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-bulk-delete")
        if busy is not None:
            return busy

        deleted = 0
        errors: list[str] = []
        for oid in ids:
            try:
                device_delete_object(self._device, oid)
                deleted += 1
                if serial:
                    try:
                        remove_by_item_id(serial, oid, path=self._index_path)
                    except Exception:
                        pass
            except TransportError as e:
                errors.append(f"id={oid}: {e}")
                if getattr(e, "fatal", False):
                    return fail(
                        "TRANSPORT_FATAL",
                        f"Aborted after object {oid}: {e}",
                        exit_code=ExitCode.TRANSPORT_FATAL,
                        data={**plan, "deleted": deleted, "errors": errors},
                    )
            except Exception as e:
                errors.append(f"id={oid}: {e}")

        return ok(
            {**plan, "deleted": deleted, "failed": len(ids) - deleted, "errors": errors[:20]},
            message=f"Bulk deleted {deleted}/{len(ids)}",
        )

    # --- device playlists --------------------------------------------------

    def _load_device_playlists(self) -> list:
        serial = self._device_serial_or_known()
        if not self._connected or self._device is None:
            return []
        return load_device_playlists_for_lookup(
            self._device, serial=serial or ""
        )

    def device_playlist_list(self) -> AgentResult:
        """List on-device playlists (requires connect). R3."""
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-dpl-list")
        if busy is not None:
            return busy
        try:
            pls = self._load_device_playlists()
        except Exception as e:
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)
        rows = [
            {
                "playlist_id": int(p.playlist_id),
                "name": playlist_display_name(p.name or "", int(p.playlist_id)),
                "wire_name": p.name or "",
                "track_count": len(p.track_ids or ()),
                "parent_id": int(p.parent_id or 0),
            }
            for p in pls
        ]
        return ok({"playlists": rows, "count": len(rows), "risks": ["R3"]})

    def device_playlist_show(self, name: str) -> AgentResult:
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-dpl-show")
        if busy is not None:
            return busy
        pls = self._load_device_playlists()
        pl = find_device_playlist_by_name(pls, name)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Device playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        return ok(
            {
                "playlist_id": int(pl.playlist_id),
                "name": playlist_display_name(pl.name or "", int(pl.playlist_id)),
                "wire_name": pl.name or "",
                "track_ids": list(pl.track_ids or ()),
                "track_count": len(pl.track_ids or ()),
            }
        )

    def device_playlist_update(
        self,
        name: str,
        *,
        track_ids: Sequence[int] | None = None,
        remove_indices: Sequence[int] | None = None,
        move_indices: Sequence[int] | None = None,
        delta: int = -1,
        confirm: bool = False,
    ) -> AgentResult:
        """Update device playlist membership/order. R1/R3."""
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to rewrite device playlist",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-dpl-update")
        if busy is not None:
            return busy
        pls = self._load_device_playlists()
        pl = find_device_playlist_by_name(pls, name)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Device playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        ids = list(pl.track_ids or ())
        if track_ids is not None:
            ids = [int(x) for x in track_ids if int(x) > 0]
        else:
            if remove_indices:
                ids = remove_ids_at_indices(ids, remove_indices)
            if move_indices:
                ids = move_ids_by_indices(ids, move_indices, delta=int(delta))
        display = playlist_display_name(pl.name or "", int(pl.playlist_id))
        try:
            self._device.update_playlist(
                int(pl.playlist_id),
                display,
                ids,
                parent_id=int(pl.parent_id or 0) or None,
            )
        except Exception as e:
            logger.exception("device_playlist_update failed")
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                "playlist_id": int(pl.playlist_id),
                "name": display,
                "track_ids": ids,
                "track_count": len(ids),
                "risks": ["R1", "R3"],
            },
            message=f"Updated device playlist {display!r} ({len(ids)} tracks)",
        )

    def device_playlist_shuffle(
        self,
        name: str,
        *,
        algorithm: str = "artist",
        confirm: bool = False,
        seed_index: int | None = None,
    ) -> AgentResult:
        """Shuffle on-device playlist via host GUID resolve. R1/R3.

        *seed_index*: 0-based index into current playlist order for RNG seed
        (negative counts from end; default 0 = first track). Host GUI often
        seeds from the context-menu track; pass -1 for the last track.
        """
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to shuffle device playlist",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-dpl-shuffle")
        if busy is not None:
            return busy
        serial = self._device_serial_or_known()
        pls = self._load_device_playlists()
        pl = find_device_playlist_by_name(pls, name)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Device playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        ids = list(pl.track_ids or ())
        if len(ids) <= 1:
            return ok(
                {"playlist_id": pl.playlist_id, "track_count": len(ids)},
                message="Nothing to shuffle",
            )
        resolved = resolve_device_playlist_to_host_tracks(
            serial, ids, path=self._index_path
        )
        tracks = list(resolved.tracks)
        # Keep pairing id with track for reorder
        paired = list(zip(ids, tracks))
        algo = (algorithm or "artist").strip().lower()
        n = len(tracks)
        si = 0 if seed_index is None else int(seed_index)
        if si < 0:
            si = n + si
        if n and not (0 <= si < n):
            return fail(
                "USAGE",
                f"seed_index out of range for {n} track(s)",
                exit_code=ExitCode.USAGE,
            )
        seed_track = tracks[si] if tracks else None
        rng = rng_from_seed_track(seed_track, extra=algo)
        only_tracks = [t for _, t in paired]
        if algo in ("artist", "merge", "merge_shuffle"):
            shuffled_tracks = merge_shuffle(only_tracks, rng=rng)
            algo_wire = "artist"
        elif algo in ("spotify", "spotify_shuffle", "dither"):
            shuffled_tracks = spotify_shuffle(only_tracks, rng=rng)
            algo_wire = "spotify"
        else:
            return fail(
                "USAGE",
                "algorithm must be artist|spotify",
                exit_code=ExitCode.USAGE,
            )
        # Map track identity back to item id (path/guid)
        by_key: dict[str, int] = {}
        for oid, t in paired:
            key = (t.guid or t.path or str(oid))
            by_key[key] = int(oid)
        new_ids: list[int] = []
        used: set[int] = set()
        for t in shuffled_tracks:
            key = t.guid or t.path or ""
            oid = by_key.get(key)
            if oid is None:
                continue
            if oid in used:
                continue
            used.add(oid)
            new_ids.append(oid)
        # append any missing
        for oid in ids:
            if oid not in used:
                new_ids.append(oid)
        display = playlist_display_name(pl.name or "", int(pl.playlist_id))
        try:
            self._device.update_playlist(
                int(pl.playlist_id),
                display,
                new_ids,
                parent_id=int(pl.parent_id or 0) or None,
            )
        except Exception as e:
            return fail("DEVICE_ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                "playlist_id": int(pl.playlist_id),
                "name": display,
                "algorithm": algo_wire,
                "seed_index": si,
                "seed_guid": (seed_track.guid if seed_track else "") or "",
                "seed_title": (
                    seed_track.meta.title if seed_track and seed_track.meta else ""
                ),
                "track_count": len(new_ids),
                "track_ids": new_ids,
            },
            message=f"Shuffled device playlist {display!r} ({algo_wire})",
        )

    def device_playlist_recreate_host(
        self,
        name: str,
        *,
        host_name: str | None = None,
        confirm: bool = False,
    ) -> AgentResult:
        """Copy device playlist membership into a host M3U (GUID join)."""
        if not confirm:
            return fail(
                "CONFIRM_REQUIRED",
                "Pass confirm=true to write/replace host playlist",
                exit_code=ExitCode.CONFIRM_REQUIRED,
            )
        if not self._connected or self._device is None:
            return fail(
                "NOT_CONNECTED",
                "Not connected; run device connect first",
                exit_code=ExitCode.ERROR,
            )
        busy = self._require_session_lock("cli-dpl-recreate")
        if busy is not None:
            return busy
        serial = self._device_serial_or_known()
        if not serial:
            return fail(
                "USAGE",
                "No device serial; refresh-index first",
                exit_code=ExitCode.USAGE,
            )
        pls = self._load_device_playlists()
        pl = find_device_playlist_by_name(pls, name)
        if pl is None:
            return fail(
                "NOT_FOUND",
                f"Device playlist not found: {name!r}",
                exit_code=ExitCode.NOT_FOUND,
            )
        resolved = resolve_device_playlist_to_host_tracks(
            serial, list(pl.track_ids or ()), path=self._index_path
        )
        hname = (host_name or playlist_display_name(pl.name or "", int(pl.playlist_id))).strip()
        try:
            result = save_resolved_tracks_as_host_playlist(
                hname,
                list(resolved.tracks),
                path=self._index_path,
                replace_existing=True,
            )
        except Exception as e:
            return fail("ERROR", str(e), exit_code=ExitCode.ERROR)
        return ok(
            {
                "host_name": result.name,
                "playlist_id": result.playlist_id,
                "created": result.created,
                "track_count": result.track_count,
                "resolved": result.resolved,
                "unresolved": result.unresolved,
            },
            message=(
                f"Host playlist {result.name!r}: "
                f"{result.resolved} resolved, {result.unresolved} placeholder(s)"
            ),
        )
