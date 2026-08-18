"""Push album art to the device via abstract MTP album objects.

Creative ZEN Vision:M (and similar) do **not** accept RepresentativeSampleData
on track objects. They do on ``LIBMTP_FILETYPE_ALBUM`` abstract albums:

  create/update album with track object ids → Send_Representative_Sample (JPEG)

Does **not** call ``Get_Album_List`` (CMD hang class after bad finalize). Host
``device_albums`` rows cache album_id + art hash for update/skip.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mtpmanager.domain.library import primary_artist
from mtpmanager.domain.models import Track
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.album_art import (
    DEFAULT_DEVICE_ART_MAX_BYTES,
    DEFAULT_DEVICE_ART_MAX_EDGE,
    prepare_device_cover_jpeg,
    prepare_device_cover_jpeg_from_image_file,
)
from mtpmanager.infra.device_index import (
    clear_device_album,
    clear_device_album_by_id,
    files_by_item_ids,
    get_device_album,
    item_ids_for_guids,
    record_device_album,
)
from mtpmanager.infra.remote_naming import DEFAULT_STORAGE_ID
from mtpmanager.ports.transport import TransportError

logger = logging.getLogger(__name__)

# ZEN Vision:M probe (2026-08): ALBUM samples = JPEG 80×80, max 24576 bytes.
ZEN_ALBUM_ART_MAX_EDGE = 80
ZEN_ALBUM_ART_MAX_BYTES = 24 * 1024


@dataclass(frozen=True)
class AlbumArtPushResult:
    """Outcome for one host album key."""

    album_key: str
    name: str
    artist: str
    album_id: int
    created: bool
    art_sent: bool
    art_skipped: bool
    track_ids: tuple[int, ...]
    unresolved_guids: tuple[str, ...] = ()
    error: str = ""
    jpeg_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and self.album_id > 0


@dataclass
class AlbumArtBatchResult:
    albums: list[AlbumArtPushResult] = field(default_factory=list)

    @property
    def art_sent_count(self) -> int:
        return sum(1 for a in self.albums if a.art_sent)

    @property
    def ok_count(self) -> int:
        return sum(1 for a in self.albums if a.ok)

    @property
    def error_count(self) -> int:
        return sum(1 for a in self.albums if a.error)


def album_grouping_key(track: Track) -> str | None:
    """Stable key for device album grouping (albumartist-ish + album title).

    Returns None when the album title is empty (cannot form a useful album).
    """
    album = (track.meta.album or "").strip()
    if not album:
        return None
    artist = primary_artist(track).strip() or "Unknown Artist"
    return f"{artist.casefold()}\0{album.casefold()}"


def album_display_fields(track: Track) -> tuple[str, str]:
    """Return (album_name, album_artist) for MTP album object strings."""
    album = (track.meta.album or "").strip() or "Album"
    artist = primary_artist(track).strip() or (track.meta.artist or "").strip()
    return album, artist


def group_tracks_by_album(tracks: Sequence[Track]) -> OrderedDict[str, list[Track]]:
    """Group tracks by :func:`album_grouping_key` (insertion order of first hit)."""
    groups: OrderedDict[str, list[Track]] = OrderedDict()
    for t in tracks:
        if t is None:
            continue
        key = album_grouping_key(t)
        if key is None:
            continue
        groups.setdefault(key, []).append(t)
    return groups


def _art_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _probe_album_sample_caps(device) -> tuple[int, int, int]:
    """Return (max_edge, max_bytes, sample_filetype_id) for ALBUM objects.

    Falls back to ZEN-known defaults when probe fails or returns empty.
    """
    import mtpmanager.infra.pymtp_wrapper as pymtp

    max_edge = ZEN_ALBUM_ART_MAX_EDGE
    max_bytes = ZEN_ALBUM_ART_MAX_BYTES
    sample_ft = int(pymtp.LIBMTP_Filetype.get("JPEG", 14))
    probe = getattr(device, "get_representative_sample_format", None)
    if not callable(probe):
        return max_edge, max_bytes, sample_ft
    try:
        ft_album = int(pymtp.LIBMTP_Filetype["ALBUM"])
        info = probe(ft_album)
    except Exception:
        logger.debug("ALBUM sample format probe failed", exc_info=True)
        return max_edge, max_bytes, sample_ft
    if not info:
        # Device may still accept samples (ZEN probe returns supported=true).
        return max_edge, max_bytes, sample_ft
    w = int(info.get("width") or 0)
    h = int(info.get("height") or 0)
    if w > 0 and h > 0:
        max_edge = min(w, h)
    elif w > 0:
        max_edge = w
    elif h > 0:
        max_edge = h
    sz = int(info.get("size") or 0)
    if sz > 0:
        max_bytes = sz
    if info.get("filetype") is not None:
        sample_ft = int(info["filetype"])
    return max_edge, max_bytes, sample_ft


def _podcast_show_for_track(track: Track, *, index_path: Path | None = None):
    """Return Podcast row when *track* is a podcast episode (by GUID), else None."""
    g = str(getattr(track, "guid", "") or "").strip().lower()
    if not is_track_guid(g):
        return None
    try:
        from mtpmanager.infra.podcast_index import get_episode_by_guid, get_podcast
    except Exception:
        return None
    try:
        ep = get_episode_by_guid(g, path=index_path)
        if ep is None:
            return None
        return get_podcast(int(ep.podcast_id), path=index_path)
    except Exception:
        logger.debug("podcast show lookup failed for %s", g, exc_info=True)
        return None


def _prepare_jpeg_for_group(
    tracks: Sequence[Track],
    *,
    max_edge: int,
    max_bytes: int,
    index_path: Path | None = None,
) -> tuple[bytes, int, int, str] | None:
    """First usable cover → JPEG sample (embedded, sidecar, or podcast show art)."""
    # 1) Embedded / sidecar on episode or music file.
    for t in tracks:
        path = str(getattr(t, "path", "") or "")
        if not path or path.startswith("podcast:"):
            continue
        out = prepare_device_cover_jpeg(
            path, max_edge=max_edge, max_bytes=max_bytes
        )
        if out:
            data, w, h = out
            return data, w, h, path

    # 2) Podcast show artwork (RSS image_url, cached under data/podcasts/).
    seen_show: set[int] = set()
    for t in tracks:
        show = _podcast_show_for_track(t, index_path=index_path)
        if show is None:
            continue
        sid = int(getattr(show, "id", 0) or 0)
        if sid <= 0 or sid in seen_show:
            continue
        seen_show.add(sid)
        try:
            from mtpmanager.app.podcast_ops import ensure_podcast_artwork

            art_path = ensure_podcast_artwork(show)
        except Exception:
            logger.debug(
                "ensure_podcast_artwork failed show_id=%s", sid, exc_info=True
            )
            continue
        if not art_path:
            continue
        out = prepare_device_cover_jpeg_from_image_file(
            art_path, max_edge=max_edge, max_bytes=max_bytes
        )
        if out:
            data, w, h = out
            return data, w, h, art_path
    return None


def _expand_group_with_on_device_siblings(
    serial: str,
    key: str,
    group: list[Track],
    *,
    index_path: Path | None,
) -> list[Track]:
    """Add library/podcast tracks of the same album already present on device.

    Single-track syncs still produce album objects that include sibling tracks
    already sent (object ids from ``device_files``).
    """
    try:
        from mtpmanager.infra.device_index import guid_stems_on_device
        from mtpmanager.infra.library_index import get_tracks_by_guids
    except Exception:
        return list(group)

    try:
        stems = set(guid_stems_on_device(serial, path=index_path) or [])
    except Exception:
        stems = set()
    if not stems:
        return list(group)

    # Library music siblings (same album key).
    try:
        on_dev = get_tracks_by_guids(list(stems), path=index_path)
    except Exception:
        on_dev = {}

    by_guid = {
        str(t.guid).strip().lower(): t
        for t in group
        if t and is_track_guid(getattr(t, "guid", None))
    }
    for g, t in (on_dev or {}).items():
        if not t:
            continue
        gg = str(g).strip().lower()
        if gg in by_guid:
            continue
        if album_grouping_key(t) == key:
            by_guid[gg] = t

    # Podcast siblings: episodes of the same show already on the device.
    try:
        from mtpmanager.domain.models import TrackMetadata
        from mtpmanager.infra.podcast_index import (
            get_episode_by_guid,
            get_podcast,
            list_episodes,
        )

        seed_guid = next(iter(by_guid), None)
        if seed_guid:
            seed_ep = get_episode_by_guid(seed_guid, path=index_path)
            if seed_ep is not None:
                show = get_podcast(int(seed_ep.podcast_id), path=index_path)
                show_title = (
                    (show.title if show else "") or "Podcast"
                ).strip() or "Podcast"
                show_author = (
                    (show.author if show else "") or show_title
                ).strip() or show_title
                for other in list_episodes(
                    int(seed_ep.podcast_id), path=index_path
                ):
                    og = str(other.guid or "").strip().lower()
                    if not is_track_guid(og) or og not in stems or og in by_guid:
                        continue
                    by_guid[og] = Track(
                        path=str(other.local_path or ""),
                        meta=TrackMetadata(
                            artist=show_author,
                            albumartist=show_title,
                            album=show_title,
                            title=(other.title or "Episode").strip()
                            or "Episode",
                            genre="Podcast",
                            date=(other.pub_date or "")[:10],
                            tracknumber=str(other.episode_index or ""),
                        ),
                        guid=og,
                    )
    except Exception:
        logger.debug("podcast sibling expand failed", exc_info=True)

    # Preserve original group order first, then extras sorted by track number.
    ordered = list(group)
    seen = {
        str(t.guid).strip().lower()
        for t in ordered
        if t and is_track_guid(getattr(t, "guid", None))
    }
    extras = [t for g, t in by_guid.items() if g not in seen]
    extras.sort(key=lambda t: (t.meta.tracknumber or "", t.path or ""))
    ordered.extend(extras)
    return ordered


def push_album_art_for_tracks(
    *,
    device,
    serial: str,
    tracks: Sequence[Track],
    storage_id: int = DEFAULT_STORAGE_ID,
    index_path: Path | None = None,
    force_art: bool = False,
) -> AlbumArtBatchResult:
    """Create/update device albums and attach JPEG art for *tracks*.

    Non-fatal per album: failures are recorded on :class:`AlbumArtPushResult`
    and do not raise (unless the device disconnects mid-batch — then we stop).

    *device* must expose create_album, update_album, send_representative_sample,
    and optionally get_representative_sample_format.
    """
    result = AlbumArtBatchResult()
    serial = str(serial or "").strip()
    if not serial:
        logger.warning("push_album_art: no serial; skipping")
        return result

    groups = group_tracks_by_album(tracks)
    if not groups:
        logger.info("push_album_art: no album-tagged tracks")
        return result

    max_edge, max_bytes, sample_ft = _probe_album_sample_caps(device)
    logger.info(
        "push_album_art probe edge=%s max_bytes=%s sample_ft=%s groups=%d",
        max_edge,
        max_bytes,
        sample_ft,
        len(groups),
    )

    for key, group in groups.items():
        group = _expand_group_with_on_device_siblings(
            serial, key, list(group), index_path=index_path
        )
        name, artist = album_display_fields(group[0])
        guids = [
            str(t.guid).strip().lower()
            for t in group
            if t and is_track_guid(getattr(t, "guid", None))
        ]
        # Preserve order, unique
        seen: set[str] = set()
        ordered_guids: list[str] = []
        for g in guids:
            if g not in seen:
                seen.add(g)
                ordered_guids.append(g)

        id_map = item_ids_for_guids(serial, ordered_guids, path=index_path)
        track_ids: list[int] = []
        unresolved: list[str] = []
        for g in ordered_guids:
            oid = id_map.get(g)
            if oid is not None and int(oid) > 0:
                track_ids.append(int(oid))
            else:
                unresolved.append(g)

        if not track_ids:
            result.albums.append(
                AlbumArtPushResult(
                    album_key=key,
                    name=name,
                    artist=artist,
                    album_id=0,
                    created=False,
                    art_sent=False,
                    art_skipped=True,
                    track_ids=(),
                    unresolved_guids=tuple(unresolved),
                    error="No on-device object ids for album tracks",
                )
            )
            continue

        jpeg = _prepare_jpeg_for_group(
            group,
            max_edge=max_edge,
            max_bytes=max_bytes,
            index_path=index_path,
        )
        if not jpeg:
            result.albums.append(
                AlbumArtPushResult(
                    album_key=key,
                    name=name,
                    artist=artist,
                    album_id=0,
                    created=False,
                    art_sent=False,
                    art_skipped=True,
                    track_ids=tuple(track_ids),
                    unresolved_guids=tuple(unresolved),
                    error="No cover art on host (tags/sidecar)",
                )
            )
            continue

        jpeg_bytes, jw, jh, _src = jpeg
        art_hash = _art_sha256(jpeg_bytes)
        cached = get_device_album(serial, key, path=index_path)
        album_id = int(cached["album_id"]) if cached else 0
        prior_ids = list(cached["track_ids"]) if cached else []
        prior_hash = str(cached["art_sha256"] or "") if cached else ""
        created = False
        art_sent = False
        art_skipped = False
        err = ""

        try:
            if album_id > 0:
                # Keep only prior track ids that still exist on-device. After a
                # wipe/resync, cached priors are usually dead object ids; merging
                # them produced “successful” art on an album the player ignores.
                live_prior: list[int] = []
                if prior_ids:
                    still = files_by_item_ids(
                        serial, prior_ids, path=index_path
                    )
                    live_prior = [
                        tid for tid in prior_ids if tid in still
                    ]
                merged = list(live_prior)
                have = set(merged)
                for tid in track_ids:
                    if tid not in have:
                        merged.append(tid)
                        have.add(tid)
                need_update = (
                    merged != prior_ids
                    or (cached and (cached.get("name") != name or cached.get("artist") != artist))
                )
                if need_update:
                    device.update_album(
                        album_id,
                        name,
                        merged,
                        artist=artist,
                    )
                track_ids = merged
                if force_art or art_hash != prior_hash:
                    device.send_representative_sample(
                        album_id,
                        jpeg_bytes,
                        width=jw,
                        height=jh,
                        filetype=sample_ft,
                    )
                    art_sent = True
                else:
                    art_skipped = True
            else:
                album_id = int(
                    device.create_album(
                        name,
                        track_ids,
                        artist=artist,
                    )
                )
                created = True
                device.send_representative_sample(
                    album_id,
                    jpeg_bytes,
                    width=jw,
                    height=jh,
                    filetype=sample_ft,
                )
                art_sent = True

            record_device_album(
                serial,
                album_key=key,
                album_id=album_id,
                name=name,
                artist=artist,
                art_sha256=art_hash if (art_sent or prior_hash == art_hash) else prior_hash,
                track_ids=track_ids,
                path=index_path,
            )
            if art_sent:
                # Ensure hash recorded as current after send.
                record_device_album(
                    serial,
                    album_key=key,
                    album_id=album_id,
                    name=name,
                    artist=artist,
                    art_sha256=art_hash,
                    track_ids=track_ids,
                    path=index_path,
                )
        except TransportError as e:
            err = str(e)
            logger.warning(
                "push_album_art failed album=%r artist=%r: %s",
                name,
                artist,
                e,
            )
            if e.fatal:
                result.albums.append(
                    AlbumArtPushResult(
                        album_key=key,
                        name=name,
                        artist=artist,
                        album_id=album_id,
                        created=created,
                        art_sent=art_sent,
                        art_skipped=art_skipped,
                        track_ids=tuple(track_ids),
                        unresolved_guids=tuple(unresolved),
                        error=err,
                        jpeg_bytes=len(jpeg_bytes),
                    )
                )
                break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.exception("push_album_art unexpected for %r", name)

        result.albums.append(
            AlbumArtPushResult(
                album_key=key,
                name=name,
                artist=artist,
                album_id=album_id,
                created=created,
                art_sent=art_sent,
                art_skipped=art_skipped,
                track_ids=tuple(track_ids),
                unresolved_guids=tuple(unresolved),
                error=err,
                jpeg_bytes=len(jpeg_bytes),
            )
        )

    logger.info(
        "push_album_art done ok=%d art_sent=%d errors=%d",
        result.ok_count,
        result.art_sent_count,
        result.error_count,
    )
    return result


@dataclass(frozen=True)
class RemoveAlbumArtResult:
    """Outcome of deleting one on-device abstract album + clearing host cache."""

    album_key: str
    name: str
    artist: str
    album_id: int
    deleted_object: bool
    cleared_cache: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        # Cache clear alone is success when there was nothing on-device to delete.
        return not self.error and (self.deleted_object or self.cleared_cache or self.album_id <= 0)


def remove_device_album_art(
    *,
    device,
    serial: str,
    album_key: str,
    name: str = "",
    artist: str = "",
    index_path: Path | None = None,
) -> RemoveAlbumArtResult:
    """Delete the cached MTP album object (cover container) and drop host cache.

    Does **not** delete track files. After a wipe/resync the host may still
    point at a stale ``album_id``; removing it forces the next art push to
    ``create_album`` instead of ``update_album`` / re-sample a dead object.
    """
    serial = str(serial or "").strip()
    key = str(album_key or "").strip()
    if not serial or not key:
        return RemoveAlbumArtResult(
            album_key=key,
            name=name,
            artist=artist,
            album_id=0,
            deleted_object=False,
            cleared_cache=False,
            error="Missing serial or album key",
        )

    cached = get_device_album(serial, key, path=index_path)
    album_id = int(cached["album_id"]) if cached else 0
    disp_name = name or (str(cached["name"]) if cached else "") or "Album"
    disp_artist = artist or (str(cached["artist"]) if cached else "") or ""

    deleted = False
    err = ""
    if album_id > 0:
        try:
            # Prefer device_ops-style delete when present; fall back to port method.
            if callable(getattr(device, "delete_object", None)):
                device.delete_object(int(album_id))
            else:
                raise TransportError(
                    f"Device cannot delete album object id={album_id}"
                )
            deleted = True
            logger.info(
                "remove_album_art deleted album_id=%s key=%r name=%r",
                album_id,
                key,
                disp_name,
            )
        except TransportError as e:
            # Object may already be gone after a wipe — still clear cache.
            err = str(e)
            logger.warning(
                "remove_album_art delete failed id=%s name=%r: %s",
                album_id,
                disp_name,
                e,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.warning(
                "remove_album_art delete failed id=%s name=%r: %s",
                album_id,
                disp_name,
                e,
            )

    cleared = clear_device_album(serial, key, path=index_path)
    if album_id > 0:
        # Also drop any other keys that still point at the same MTP id.
        extra = clear_device_album_by_id(serial, album_id, path=index_path)
        if extra:
            cleared = True

    # If delete failed because the object is gone, treat cache clear as recovery.
    if err and cleared and not deleted:
        logger.info(
            "remove_album_art cleared stale cache after delete error "
            "id=%s key=%r",
            album_id,
            key,
        )
        err = ""

    return RemoveAlbumArtResult(
        album_key=key,
        name=disp_name,
        artist=disp_artist,
        album_id=album_id,
        deleted_object=deleted,
        cleared_cache=cleared,
        error=err,
    )
