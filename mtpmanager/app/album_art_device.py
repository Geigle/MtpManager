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
)
from mtpmanager.infra.device_index import (
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


def _prepare_jpeg_for_group(
    tracks: Sequence[Track],
    *,
    max_edge: int,
    max_bytes: int,
) -> tuple[bytes, int, int, str] | None:
    """First track with cover art → JPEG sample; return None if none."""
    for t in tracks:
        path = str(getattr(t, "path", "") or "")
        if not path:
            continue
        out = prepare_device_cover_jpeg(
            path, max_edge=max_edge, max_bytes=max_bytes
        )
        if out:
            data, w, h = out
            return data, w, h, path
    return None


def _expand_group_with_on_device_siblings(
    serial: str,
    key: str,
    group: list[Track],
    *,
    index_path: Path | None,
) -> list[Track]:
    """Add library tracks of the same album already present on the device.

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

    # Library index is the same DB path as device_index by default.
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
    # Preserve original group order first, then extras sorted by track number.
    ordered = list(group)
    seen = {
        str(t.guid).strip().lower()
        for t in ordered
        if t and is_track_guid(getattr(t, "guid", None))
    }
    extras = [
        t
        for g, t in by_guid.items()
        if g not in seen
    ]
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
            group, max_edge=max_edge, max_bytes=max_bytes
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
                # Merge membership: keep prior order, append new ids.
                merged = list(prior_ids)
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
