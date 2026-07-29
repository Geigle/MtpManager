"""Subscribe, refresh, download, and prepare podcast episodes for transfer.

# TODO(follow-up): OPML import/export
# TODO(follow-up): auto-refresh on a timer
# TODO(follow-up): Device → Podcasts inventory browser
# TODO(follow-up): per-show auto-download rules beyond “latest”
"""

from __future__ import annotations

import logging
import os
import urllib.request
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from mtpmanager.domain.library import is_video_file
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.track_id import is_track_guid
from mtpmanager.infra.podcast_feed import (
    FeedChannel,
    episode_to_row_dict,
    fetch_and_parse,
)
from mtpmanager.infra.podcast_index import (
    Podcast,
    PodcastEpisode,
    clear_all_episode_local_paths,
    create_or_update_podcast,
    episode_cache_dir,
    get_episode,
    get_podcast,
    known_feed_guids,
    latest_episode,
    list_episodes,
    list_podcasts,
    normalize_feed_url,
    podcasts_cache_root,
    set_episode_local_path,
    upsert_episodes,
)

logger = logging.getLogger(__name__)

INITIAL_EPISODE_LIMIT = 5
MORE_EPISODE_STEP = 10


@dataclass
class PodcastVideoJob:
    """Media ready to encode/send under ZENcast as device video.

    *local_path* is either a real video enclosure, or (when *from_audio_still*)
    a downloaded audio file that will be muxed with a still image.
    """

    episode: PodcastEpisode
    podcast: Podcast
    local_path: str
    # Experimental: audio enclosure + show artwork (or black) → still XviD.
    from_audio_still: bool = False
    image_path: str = ""


@dataclass
class PodcastSyncPrep:
    """Result of preparing episodes for sync or playback."""

    audio_tracks: list[Track] = field(default_factory=list)
    video_jobs: list[PodcastVideoJob] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.audio_tracks and not self.video_jobs


def subscribe_feed(
    feed_url: str,
    *,
    initial_limit: int = INITIAL_EPISODE_LIMIT,
    path: Path | None = None,
) -> tuple[Podcast, int]:
    """Fetch feed, upsert show, store up to *initial_limit* newest episodes.

    Returns (podcast, newly_inserted_count).
    """
    url = normalize_feed_url(feed_url)
    if not url:
        raise ValueError("Feed URL is required")
    channel = fetch_and_parse(url)
    podcast = create_or_update_podcast(
        feed_url=url,
        title=channel.title or url,
        author=channel.author,
        description=channel.description,
        image_url=channel.image_url,
        site_url=channel.site_url,
        path=path,
    )
    rows = [episode_to_row_dict(e) for e in channel.episodes[: max(0, initial_limit)]]
    n = upsert_episodes(podcast.id, rows, path=path)
    refreshed = get_podcast(podcast.id, path=path) or podcast
    return refreshed, n


def load_more_episodes(
    podcast_id: int,
    *,
    count: int = MORE_EPISODE_STEP,
    full_history: bool = False,
    path: Path | None = None,
) -> tuple[Podcast, int]:
    """Fetch feed again and insert next *count* unknown episodes (or all).

    Returns (podcast, newly_inserted_count).
    """
    podcast = get_podcast(podcast_id, path=path)
    if podcast is None:
        raise ValueError(f"Podcast id {podcast_id} not found")
    channel = fetch_and_parse(podcast.feed_url)
    # Refresh channel metadata.
    podcast = create_or_update_podcast(
        feed_url=podcast.feed_url,
        title=channel.title or podcast.title,
        author=channel.author or podcast.author,
        description=channel.description or podcast.description,
        image_url=channel.image_url or podcast.image_url,
        site_url=channel.site_url or podcast.site_url,
        path=path,
    )
    known = known_feed_guids(podcast.id, path=path)
    candidates = [
        episode_to_row_dict(e)
        for e in channel.episodes
        if e.feed_guid not in known
    ]
    if not full_history:
        candidates = candidates[: max(0, int(count))]
    n = upsert_episodes(podcast.id, candidates, path=path)
    refreshed = get_podcast(podcast.id, path=path) or podcast
    return refreshed, n


def refresh_podcast(
    podcast_id: int,
    *,
    path: Path | None = None,
) -> tuple[Podcast, int]:
    """Manual refresh: re-fetch the RSS feed, update show metadata, add new episodes.

    Processes every item in the current feed document (publisher-bounded list)
    and inserts any not already in the index. Returns (podcast, new_count).
    """
    return load_more_episodes(
        podcast_id, count=0, full_history=True, path=path
    )


def _enclosure_is_video(url: str, mime: str) -> bool:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return True
    if m.startswith("audio/"):
        return False
    path = (urlparse(url).path or "").lower()
    if path.endswith(
        (".m4v", ".mov", ".webm", ".avi", ".mkv", ".wmv", ".mpg", ".mpeg", ".mp4")
    ):
        return not path.endswith((".m4a", ".mp3"))
    return False


def _guess_ext(url: str, mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("video/") or ("mp4" in m and "audio" not in m):
        if "webm" in m:
            return ".webm"
        if "quicktime" in m or "mov" in m:
            return ".mov"
        if "avi" in m or "msvideo" in m:
            return ".avi"
        if "m4v" in m:
            return ".m4v"
        return ".mp4"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "m4a" in m or "aac" in m:
        return ".m4a"
    if "ogg" in m or "opus" in m:
        return ".ogg"
    path = urlparse(url).path.lower()
    for ext in (
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".mp4",
        ".m4v",
        ".mov",
        ".webm",
        ".avi",
        ".mkv",
        ".wmv",
    ):
        if path.endswith(ext):
            return ext
    return ".mp3"


def download_episode(
    episode: PodcastEpisode,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
    prefer_video: bool = False,
) -> PodcastEpisode:
    """Ensure enclosure is on disk; update local_path. Returns refreshed episode.

    When *prefer_video* and a video enclosure URL exists, downloads that
    instead of the primary (often audio) enclosure.
    """
    if prefer_video and (episode.video_enclosure_url or "").strip():
        url = episode.video_enclosure_url.strip()
        mime = episode.video_enclosure_type or ""
    else:
        url = (episode.enclosure_url or "").strip()
        mime = episode.enclosure_type or ""
        # If primary is already video, fine.
        if prefer_video and not _enclosure_is_video(url, mime):
            # No separate video URL; fall through to primary.
            pass

    if episode.local_path and os.path.isfile(episode.local_path):
        # Reuse cache when it matches the requested media kind.
        existing_is_video = is_video_file(episode.local_path)
        want_video = prefer_video and (
            bool(episode.video_enclosure_url)
            or _enclosure_is_video(url, mime)
            or bool(episode.is_video)
        )
        if want_video and existing_is_video:
            return episode
        if not want_video and not existing_is_video:
            return episode

    if not url:
        raise ValueError("Episode has no enclosure URL")
    cache = episode_cache_dir(episode.podcast_id, data_dir=data_dir)
    ext = _guess_ext(url, mime)
    dest = cache / f"{episode.guid}{ext}"
    # Separate video cache name when audio may already occupy {guid}.mp3.
    if prefer_video and ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus"):
        dest = cache / f"{episode.guid}_video{ext}"
    elif prefer_video:
        dest = cache / f"{episode.guid}_video{ext}"

    if dest.is_file() and dest.stat().st_size > 0:
        set_episode_local_path(episode.id, str(dest), path=path)
        refreshed = get_episode(episode.id, path=path)
        if refreshed is not None:
            return refreshed

    logger.info(
        "Downloading podcast episode id=%s prefer_video=%s → %s",
        episode.id,
        prefer_video,
        dest,
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MtpManager/1.0 (podcast download)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    set_episode_local_path(episode.id, str(dest), path=path)
    refreshed = get_episode(episode.id, path=path)
    if refreshed is None:
        raise RuntimeError("episode missing after download")
    return refreshed


def ensure_episode_audio_file(
    episode: PodcastEpisode,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
    target_format: str = "mp3",
) -> PodcastEpisode:
    """Download media and ensure a local audio file for play/sync.

    Dual feeds: primary enclosure is already audio. Video-only: download
    video then extract audio with ffmpeg into the episode cache.
    """
    fmt = (target_format or "mp3").lower().lstrip(".")
    cache = episode_cache_dir(episode.podcast_id, data_dir=data_dir)
    audio_dest = cache / f"{episode.guid}.{fmt}"

    if audio_dest.is_file() and audio_dest.stat().st_size > 0:
        if episode.local_path != str(audio_dest):
            set_episode_local_path(episode.id, str(audio_dest), path=path)
            refreshed = get_episode(episode.id, path=path)
            if refreshed is not None:
                return refreshed
        return episode

    ready = download_episode(
        episode, path=path, data_dir=data_dir, prefer_video=False
    )
    local = ready.local_path or ""
    if local and os.path.isfile(local) and not is_video_file(local):
        return ready

    # Video-only (or cached video): extract audio.
    if not local or not os.path.isfile(local):
        ready = download_episode(
            episode, path=path, data_dir=data_dir, prefer_video=True
        )
        local = ready.local_path or ""
    if not local or not os.path.isfile(local):
        raise FileNotFoundError(f"No media to extract for episode {episode.id}")

    if not is_video_file(local) and not _enclosure_is_video(
        ready.enclosure_url, ready.enclosure_type
    ):
        return ready

    from mtpmanager.infra.ffmpeg_transcode import FFmpegTranscoder

    logger.info(
        "Extracting audio from video podcast episode id=%s → %s",
        episode.id,
        audio_dest,
    )
    FFmpegTranscoder().extract_audio(local, str(audio_dest), target_format=fmt)
    set_episode_local_path(episode.id, str(audio_dest), path=path)
    refreshed = get_episode(episode.id, path=path)
    if refreshed is None:
        raise RuntimeError("episode missing after audio extract")
    return refreshed


def episode_as_track(
    episode: PodcastEpisode,
    podcast: Podcast,
) -> Track:
    """Build a Track for the transfer pipeline (requires local_path)."""
    if not episode.local_path or not os.path.isfile(episode.local_path):
        raise FileNotFoundError(
            f"Episode not downloaded: {episode.title!r}"
        )
    if not is_track_guid(episode.guid):
        raise ValueError(f"Episode missing host GUID: id={episode.id}")
    meta = TrackMetadata(
        artist=(podcast.author or podcast.title or "Podcast").strip()
        or "Podcast",
        albumartist=(podcast.title or "Podcast").strip() or "Podcast",
        album=(podcast.title or "Podcast").strip() or "Podcast",
        title=(episode.title or "Episode").strip() or "Episode",
        genre="Podcast",
        date=(episode.pub_date or "")[:10],
        length_sec=float(episode.duration_sec or 0),
        tracknumber=str(episode.episode_index or "01"),
    )
    return Track(path=episode.local_path, meta=meta, guid=episode.guid)


def pick_latest_not_on_device(
    podcast_id: int,
    device_guids: Collection[str],
    *,
    path: Path | None = None,
) -> PodcastEpisode | None:
    """Newest episode whose host GUID is not in *device_guids*."""
    stems = {g for g in device_guids if is_track_guid(g)}
    for ep in list_episodes(podcast_id, path=path):
        if ep.guid and ep.guid not in stems and ep.enclosure_url:
            return ep
    return None


def ensure_podcast_artwork(
    podcast: Podcast,
    *,
    data_dir: Path | None = None,
) -> str | None:
    """Download show artwork into the podcast cache; return local path or None.

    Used as the single video frame when syncing audio podcasts as still video.
    """
    url = (podcast.image_url or "").strip()
    if not url:
        return None
    cache = episode_cache_dir(podcast.id, data_dir=data_dir)
    for existing in sorted(cache.glob("artwork.*")):
        try:
            if existing.is_file() and existing.stat().st_size > 0:
                return str(existing)
        except OSError:
            continue
    # Guess extension from URL path; default jpeg.
    path_l = (urlparse(url).path or "").lower()
    ext = ".jpg"
    for cand in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path_l.endswith(cand):
            ext = ".jpg" if cand == ".jpeg" else cand
            break
    dest = cache / f"artwork{ext}"
    try:
        logger.info(
            "Downloading podcast artwork podcast_id=%s → %s",
            podcast.id,
            dest,
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MtpManager/1.0 (podcast artwork)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if not data:
            logger.warning("Empty podcast artwork for id=%s", podcast.id)
            return None
        dest.write_bytes(data)
        return str(dest)
    except Exception as e:
        logger.warning(
            "Could not download podcast artwork id=%s url=%s: %s",
            podcast.id,
            url,
            e,
        )
        return None


def prepare_episodes_for_sync(
    episodes: Sequence[PodcastEpisode],
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
    allow_video: bool = False,
    audio_as_video: bool = False,
    target_audio_format: str = "mp3",
) -> PodcastSyncPrep:
    """Download missing media; return audio tracks and optional video jobs.

    When *allow_video* is False (default), always produce audio (extract from
    video-only episodes). When True, video episodes become :class:`PodcastVideoJob`
    entries for ZENcast video send (caller encodes XviD for ZEN Vision:M).

    When *audio_as_video* is True (experimental), non-video episodes become
    still-image video jobs (artwork or black + audio) so they can land under
    ZENcast on devices that only list video there.
    """
    prep = PodcastSyncPrep()
    podcast_cache: dict[int, Podcast] = {}
    artwork_cache: dict[int, str | None] = {}
    for ep in episodes:
        show = podcast_cache.get(ep.podcast_id)
        if show is None:
            show = get_podcast(ep.podcast_id, path=path)
            if show is None:
                logger.warning(
                    "Podcast %s missing for episode %s", ep.podcast_id, ep.id
                )
                continue
            podcast_cache[ep.podcast_id] = show
        try:
            use_video = bool(allow_video) and bool(
                ep.is_video
                or ep.video_enclosure_url
                or _enclosure_is_video(ep.enclosure_url, ep.enclosure_type)
            )
            if use_video:
                ready = download_episode(
                    ep, path=path, data_dir=data_dir, prefer_video=True
                )
                if not ready.local_path or not os.path.isfile(ready.local_path):
                    raise FileNotFoundError("video download missing")
                prep.video_jobs.append(
                    PodcastVideoJob(
                        episode=ready, podcast=show, local_path=ready.local_path
                    )
                )
            elif audio_as_video:
                ready = ensure_episode_audio_file(
                    ep,
                    path=path,
                    data_dir=data_dir,
                    target_format=target_audio_format,
                )
                if not ready.local_path or not os.path.isfile(ready.local_path):
                    raise FileNotFoundError("audio download missing")
                if show.id not in artwork_cache:
                    artwork_cache[show.id] = ensure_podcast_artwork(
                        show, data_dir=data_dir
                    )
                art = artwork_cache.get(show.id) or ""
                prep.video_jobs.append(
                    PodcastVideoJob(
                        episode=ready,
                        podcast=show,
                        local_path=ready.local_path,
                        from_audio_still=True,
                        image_path=art,
                    )
                )
            else:
                ready = ensure_episode_audio_file(
                    ep,
                    path=path,
                    data_dir=data_dir,
                    target_format=target_audio_format,
                )
                prep.audio_tracks.append(episode_as_track(ready, show))
        except Exception:
            logger.exception(
                "Failed to prepare episode id=%s title=%r",
                ep.id,
                ep.title,
            )
    return prep


def clear_downloaded_podcasts(
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, int | str]:
    """Delete all files under the podcasts cache and clear ``local_path`` rows.

    Returns ``{files, bytes, rows_cleared, root}``.
    """
    root = podcasts_cache_root(data_dir=data_dir)
    files_removed = 0
    bytes_removed = 0
    if root.is_dir():
        for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    size = int(fp.stat().st_size)
                except OSError:
                    size = 0
                try:
                    fp.unlink()
                    files_removed += 1
                    bytes_removed += size
                except OSError as e:
                    logger.warning("Could not delete podcast cache file %s: %s", fp, e)
            # Remove empty show dirs
            try:
                Path(dirpath).rmdir()
            except OSError:
                pass
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    rows = clear_all_episode_local_paths(path=path)
    logger.info(
        "Cleared podcast downloads: files=%s bytes=%s db_rows=%s root=%s",
        files_removed,
        bytes_removed,
        rows,
        root,
    )
    return {
        "files": files_removed,
        "bytes": bytes_removed,
        "rows_cleared": rows,
        "root": str(root),
    }


def discard_episode_local_files(
    episode: PodcastEpisode,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Delete known local media for one episode and clear ``local_path``.

    Used when Config → Keep downloaded podcasts is off (after successful sync).
    """
    cache = episode_cache_dir(episode.podcast_id, data_dir=data_dir)
    candidates: list[Path] = []
    if episode.local_path:
        candidates.append(Path(episode.local_path))
    if episode.guid:
        try:
            for p in cache.glob(f"{episode.guid}*"):
                if p.is_file():
                    candidates.append(p)
        except OSError:
            pass
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_file():
                p.unlink()
                logger.info("Discarded podcast download %s", p)
        except OSError as e:
            logger.warning("Could not discard podcast file %s: %s", p, e)
    set_episode_local_path(episode.id, "", path=path)


def sanitize_show_folder_name(title: str) -> str:
    """Safe single path component for experimental show folders."""
    from mtpmanager.infra.remote_naming import MAX_REMOTE_BASENAME, sanitize_component

    cleaned = sanitize_component(title or "Podcast", MAX_REMOTE_BASENAME)
    return cleaned or "Podcast"


def ensure_podcast_show_folder(
    device,
    show_title: str,
    *,
    podcast_parent_id: int,
    folder_cache: dict[str, int] | None = None,
) -> int:
    """Create ZENcast/<show>/ when experimental show folders are enabled.

    *folder_cache* maps casefold show name → folder object id for the batch.
    """
    name = sanitize_show_folder_name(show_title)
    key = name.casefold()
    if folder_cache is not None and key in folder_cache:
        return folder_cache[key]
    # List children of podcast parent for an existing same-named folder.
    try:
        folders = device.list_folders()
        for f in folders or []:
            if int(getattr(f, "parent_id", 0) or 0) != int(podcast_parent_id):
                continue
            if (getattr(f, "name", "") or "").strip().casefold() == key:
                fid = int(getattr(f, "folder_id", 0) or 0)
                if fid > 0:
                    if folder_cache is not None:
                        folder_cache[key] = fid
                    return fid
    except Exception:
        logger.debug("list_folders for podcast show match failed", exc_info=True)
    new_id = int(device.create_folder(name, parent=int(podcast_parent_id)))
    if folder_cache is not None and new_id > 0:
        folder_cache[key] = new_id
    return new_id


@dataclass(frozen=True)
class PodcastVideoSendResult:
    """Result of sending a video podcast under ZENcast (title-style ObjectFileName)."""

    object_id: int
    parent_id: int
    remote_basename: str
    guid: str = ""  # host index only — never the wire ObjectFileName


def podcast_video_object_basename(
    episode_title: str,
    *,
    container_ext: str,
) -> str:
    """Sanitized episode title + extension for ObjectFileName (no GUID)."""
    from mtpmanager.infra.remote_naming import MAX_REMOTE_BASENAME, sanitize_component

    ext = container_ext if container_ext.startswith(".") else f".{container_ext}"
    if ext == ".":
        ext = ".avi"
    body_max = max(8, MAX_REMOTE_BASENAME - len(ext))
    stem = sanitize_component((episode_title or "Episode").strip() or "Episode", body_max)
    basename = f"{stem}{ext}"
    if len(basename) > MAX_REMOTE_BASENAME:
        stem_max = max(1, MAX_REMOTE_BASENAME - len(ext))
        stem = sanitize_component(stem, stem_max)
        basename = f"{stem}{ext}"
    return basename


def send_podcast_video_to_zencast(
    transport,
    job: PodcastVideoJob,
    *,
    parent_id: int,
    encode_profile=None,
    encode_for_device: bool = True,
    on_progress=None,
    keep_download: bool = True,
    still_fps: float | None = None,
    still_width: int | None = None,
    still_height: int | None = None,
) -> PodcastVideoSendResult:
    """Encode and send podcast video via the same path as library Send Video.

    Uses :func:`~mtpmanager.app.device_ops.prepare_and_send_video` so the
    XviD filter chain, skip-if-compatible, and ``send_video`` wire path match
    library Video tab sync. Only differences:

    - *parent_id* is ZENcast / show folder (``allowed_parents=None``).
    - ObjectFileName is the episode title (+ container).
    - Optional durable ``{guid}_device.avi`` when *keep_download* is on.
    - *from_audio_still* jobs: still image (artwork or black) + audio → XviD.

    Host GUID is returned for device-index skip-if-present only (never wire).
    """
    from mtpmanager.app.device_ops import prepare_and_send_video
    from mtpmanager.infra.ffmpeg_video import (
        cleanup_video_temp,
        convert_audio_still_to_video_for_profile,
        convert_video_for_profile,
        default_temp_video_path,
    )

    src = job.local_path
    if not src or not os.path.isfile(src):
        raise FileNotFoundError(f"Podcast video missing: {src!r}")

    parent = int(parent_id)
    if parent <= 0:
        raise ValueError(f"podcast parent_id must be positive, got {parent}")

    ep = job.episode
    title = (ep.title or "Episode").strip() or "Episode"
    host_guid = ep.guid if is_track_guid(ep.guid) else ""
    still = bool(job.from_audio_still)

    if still and encode_profile is None:
        raise ValueError(
            "Audio-as-video podcasts need a device video encode profile"
        )

    do_encode = bool(encode_for_device and encode_profile is not None)
    send_src = src
    temp_to_clean: str | None = None

    def _enc_progress(done: float, total: float, msg: str) -> None:
        if on_progress is None:
            return
        try:
            if total and total > 0:
                pct = int(min(85, max(0, (done / total) * 85)))
                on_progress("progress", pct, 100, msg)
            else:
                on_progress("status", msg)
        except Exception:
            logger.debug("podcast video progress failed", exc_info=True)

    def _start_transcode(status: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress("phase", "transcode")
            on_progress("progress", 0, 100, status)
        except Exception:
            pass

    try:
        # Still-from-audio: always pre-encode (source is not a video file).
        if still:
            cont = encode_profile.container.lstrip(".")
            if keep_download and host_guid:
                out_path = str(
                    episode_cache_dir(ep.podcast_id)
                    / f"{host_guid}_device.{cont}"
                )
            else:
                out_path = default_temp_video_path(encode_profile)
                temp_to_clean = out_path
            _start_transcode("encoding still-image podcast…")
            image = (job.image_path or "").strip() or None
            from mtpmanager.infra.app_config import (
                DEFAULT_AUDIO_PODCAST_STILL_FPS,
                DEFAULT_AUDIO_PODCAST_STILL_HEIGHT,
                DEFAULT_AUDIO_PODCAST_STILL_WIDTH,
                normalize_still_fps,
                normalize_still_frame_size,
            )

            fps = normalize_still_fps(
                still_fps
                if still_fps is not None
                else DEFAULT_AUDIO_PODCAST_STILL_FPS
            )
            fw, fh = normalize_still_frame_size(
                still_width
                if still_width is not None
                else DEFAULT_AUDIO_PODCAST_STILL_WIDTH,
                still_height
                if still_height is not None
                else DEFAULT_AUDIO_PODCAST_STILL_HEIGHT,
            )
            convert_audio_still_to_video_for_profile(
                src,
                encode_profile,
                image_path=image,
                dest_path=out_path,
                on_progress=_enc_progress,
                still_fps=fps,
                width=fw,
                height=fh,
            )
            logger.info(
                "Podcast audio-still device encode: %s (audio=%s image=%s "
                "fps=%g frame=%dx%d)",
                out_path,
                src,
                image or "(black)",
                fps,
                fw,
                fh,
            )
            send_src = out_path
            do_encode = False
        elif do_encode and keep_download and host_guid:
            # Real video: encode once into the cache for inspect/resend.
            cont = encode_profile.container.lstrip(".")
            kept = episode_cache_dir(ep.podcast_id) / f"{host_guid}_device.{cont}"
            _start_transcode("encoding podcast video…")
            convert_video_for_profile(
                src,
                encode_profile,
                dest_path=str(kept),
                on_progress=_enc_progress,
            )
            logger.info(
                "Podcast video device encode kept for inspect: %s (source=%s)",
                kept,
                src,
            )
            send_src = str(kept)
            do_encode = False  # already device AVI; send as-is

        if encode_profile is not None and (
            still or encode_for_device or send_src != src
        ):
            ext = f".{encode_profile.container.lstrip('.')}"
        else:
            _, src_ext = os.path.splitext(send_src)
            ext = src_ext or ".avi"
        preferred = podcast_video_object_basename(title, container_ext=ext)

        logger.info(
            "Podcast video → prepare_and_send_video parent=%s title=%r "
            "src=%s encode=%s still=%s guid_index=%s",
            parent,
            title,
            send_src,
            do_encode,
            still,
            host_guid or "—",
        )
        result = prepare_and_send_video(
            transport,
            send_src,
            parent_id=parent,
            encode_profile=encode_profile,
            encode_for_device=do_encode,
            on_progress=on_progress,
            title=title,
            preferred_basename=preferred,
            guid=host_guid or None,
            allowed_parents=None,  # ZENcast / show folder — not Video/TV only
        )
        return PodcastVideoSendResult(
            object_id=int(result.object_id or 0),
            parent_id=int(result.parent_id),
            remote_basename=result.remote_basename,
            guid=host_guid,
        )
    finally:
        if temp_to_clean:
            cleanup_video_temp(temp_to_clean)
