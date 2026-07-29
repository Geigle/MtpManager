"""Subscribe, refresh, download, and prepare podcast episodes for transfer.

# TODO(follow-up): OPML import/export
# TODO(follow-up): auto-refresh on a timer
# TODO(follow-up): Device → Podcasts inventory browser
# TODO(follow-up): video podcasts
# TODO(follow-up): per-show auto-download rules beyond “latest”
"""

from __future__ import annotations

import logging
import os
import urllib.request
from collections.abc import Collection, Sequence
from pathlib import Path
from urllib.parse import urlparse

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
    create_or_update_podcast,
    episode_cache_dir,
    get_episode,
    get_podcast,
    known_feed_guids,
    latest_episode,
    list_episodes,
    list_podcasts,
    normalize_feed_url,
    set_episode_local_path,
    upsert_episodes,
)
logger = logging.getLogger(__name__)

INITIAL_EPISODE_LIMIT = 5
MORE_EPISODE_STEP = 10


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


def download_episode(
    episode: PodcastEpisode,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
) -> PodcastEpisode:
    """Ensure enclosure is on disk; update local_path. Returns refreshed episode."""
    if episode.local_path and os.path.isfile(episode.local_path):
        return episode
    url = (episode.enclosure_url or "").strip()
    if not url:
        raise ValueError("Episode has no enclosure URL")
    cache = episode_cache_dir(episode.podcast_id, data_dir=data_dir)
    ext = _guess_ext(url, episode.enclosure_type)
    dest = cache / f"{episode.guid}{ext}"
    logger.info(
        "Downloading podcast episode id=%s → %s",
        episode.id,
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


def prepare_episodes_for_sync(
    episodes: Sequence[PodcastEpisode],
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
) -> list[Track]:
    """Download missing enclosures and return Track list for transfer."""
    out: list[Track] = []
    podcast_cache: dict[int, Podcast] = {}
    for ep in episodes:
        show = podcast_cache.get(ep.podcast_id)
        if show is None:
            show = get_podcast(ep.podcast_id, path=path)
            if show is None:
                logger.warning("Podcast %s missing for episode %s", ep.podcast_id, ep.id)
                continue
            podcast_cache[ep.podcast_id] = show
        try:
            ready = download_episode(ep, path=path, data_dir=data_dir)
            out.append(episode_as_track(ready, show))
        except Exception:
            logger.exception(
                "Failed to prepare episode id=%s title=%r",
                ep.id,
                ep.title,
            )
    return out


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


def _guess_ext(url: str, mime: str) -> str:
    m = (mime or "").lower()
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "m4a" in m or "mp4" in m or "aac" in m:
        return ".m4a"
    if "ogg" in m or "opus" in m:
        return ".ogg"
    path = urlparse(url).path.lower()
    for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus"):
        if path.endswith(ext):
            return ext
    return ".mp3"
