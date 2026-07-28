"""Fetch and parse podcast RSS/Atom feeds (stdlib only).

# TODO(follow-up): richer Atom / podcasting 2.0 namespace support
"""

from __future__ import annotations

import email.utils
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element

logger = logging.getLogger(__name__)

_USER_AGENT = "MtpManager/1.0 (+https://github.com/local/mtpmanager; podcast)"
_TIMEOUT_S = 45

# Common namespaces seen on podcast feeds.
_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

_AUDIO_TYPES = (
    "audio/",
    "mpeg",
    "mp3",
    "m4a",
    "aac",
    "ogg",
    "opus",
    "x-m4a",
)


@dataclass
class FeedEpisode:
    feed_guid: str
    title: str = ""
    description: str = ""
    pub_date: str = ""  # ISO UTC when parseable
    duration_sec: float = 0.0
    enclosure_url: str = ""
    enclosure_type: str = ""
    enclosure_bytes: int = 0
    episode_index: int = 0
    season: int = 0


@dataclass
class FeedChannel:
    title: str = ""
    author: str = ""
    description: str = ""
    image_url: str = ""
    site_url: str = ""
    episodes: list[FeedEpisode] = field(default_factory=list)


def fetch_feed_bytes(url: str, *, timeout: float = _TIMEOUT_S) -> bytes:
    """HTTP GET feed body; raises OSError/URLError on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_feed_xml(data: bytes | str) -> FeedChannel:
    """Parse RSS 2.0 (preferred) or simple Atom into a FeedChannel."""
    if isinstance(data, str):
        text = data
    else:
        text = data.decode("utf-8", errors="replace")
    # Strip BOM / leading junk before <?xml
    text = text.lstrip("\ufeff").strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"Invalid feed XML: {e}") from e

    tag = _local(root.tag).lower()
    if tag == "rss" or tag == "rdf":
        return _parse_rss(root)
    if tag == "feed":
        return _parse_atom(root)
    # Sometimes channel is root
    if tag == "channel":
        return _parse_rss_channel(root)
    raise ValueError(f"Unsupported feed root element: {root.tag}")


def fetch_and_parse(url: str, *, timeout: float = _TIMEOUT_S) -> FeedChannel:
    raw = fetch_feed_bytes(url, timeout=timeout)
    return parse_feed_xml(raw)


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":", 1)[-1]
    return tag


def _child_text(parent: Element, names: tuple[str, ...]) -> str:
    for child in parent:
        if _local(child.tag).lower() in names:
            return (child.text or "").strip()
    return ""


def _child_attr(parent: Element, names: tuple[str, ...], attr: str) -> str:
    for child in parent:
        if _local(child.tag).lower() in names:
            return (child.get(attr) or "").strip()
    return ""


def _find_text_ns(parent: Element, *paths: str) -> str:
    """Try several {ns}local or bare local names."""
    for path in paths:
        # bare local scan
        want = path.split("}")[-1].lower() if "}" in path else path.lower()
        for child in parent.iter():
            if child is parent:
                continue
            if _local(child.tag).lower() == want and (child.text or "").strip():
                # Prefer direct-ish children for channel fields
                if child in list(parent) or True:
                    return (child.text or "").strip()
    return ""


def _parse_rss(root: Element) -> FeedChannel:
    channel = None
    for child in root:
        if _local(child.tag).lower() == "channel":
            channel = child
            break
    if channel is None:
        raise ValueError("RSS feed missing <channel>")
    return _parse_rss_channel(channel)


def _parse_rss_channel(channel: Element) -> FeedChannel:
    title = _child_text(channel, ("title",))
    description = _child_text(channel, ("description", "summary"))
    site_url = _child_text(channel, ("link",))
    author = ""
    image_url = ""
    for child in channel:
        loc = _local(child.tag).lower()
        if loc in ("author", "creator") and (child.text or "").strip():
            author = (child.text or "").strip()
        if loc == "owner":
            # itunes:owner/itunes:name
            for sub in child:
                if _local(sub.tag).lower() == "name" and (sub.text or "").strip():
                    author = author or (sub.text or "").strip()
        if loc == "image":
            href = child.get("href") or ""
            if href:
                image_url = href
            else:
                for sub in child:
                    if _local(sub.tag).lower() == "url" and (sub.text or "").strip():
                        image_url = (sub.text or "").strip()
        if loc == "author" and not author:
            author = (child.text or "").strip()

    # itunes:image href on channel children
    if not image_url:
        image_url = _child_attr(channel, ("image",), "href")
    if not author:
        for child in channel:
            if _local(child.tag).lower() == "author":
                author = (child.text or "").strip()

    episodes: list[FeedEpisode] = []
    for child in channel:
        if _local(child.tag).lower() == "item":
            ep = _parse_rss_item(child)
            if ep is not None:
                episodes.append(ep)

    episodes.sort(key=lambda e: (e.pub_date or "", e.feed_guid), reverse=True)
    return FeedChannel(
        title=title,
        author=author,
        description=description,
        image_url=image_url,
        site_url=site_url,
        episodes=episodes,
    )


def _parse_rss_item(item: Element) -> FeedEpisode | None:
    title = _child_text(item, ("title",))
    description = _child_text(item, ("description", "summary", "encoded"))
    pub_raw = _child_text(item, ("pubdate", "date", "published"))
    pub_date = _normalize_pub_date(pub_raw)
    feed_guid = _child_text(item, ("guid", "id"))
    enclosure_url = ""
    enclosure_type = ""
    enclosure_bytes = 0
    duration_sec = 0.0
    episode_index = 0
    season = 0

    for child in item:
        loc = _local(child.tag).lower()
        if loc == "enclosure":
            url = (child.get("url") or "").strip()
            typ = (child.get("type") or "").strip()
            length = child.get("length") or "0"
            if url and _looks_audio(url, typ):
                enclosure_url = url
                enclosure_type = typ
                try:
                    enclosure_bytes = int(float(length))
                except (TypeError, ValueError):
                    enclosure_bytes = 0
        if loc == "duration":
            duration_sec = _parse_duration(child.text or "")
        if loc == "episode":
            try:
                episode_index = int(float((child.text or "0").strip()))
            except (TypeError, ValueError):
                episode_index = 0
        if loc == "season":
            try:
                season = int(float((child.text or "0").strip()))
            except (TypeError, ValueError):
                season = 0
        if loc == "content" and not description:
            description = (child.text or "").strip()

    if not enclosure_url:
        # media:content
        for child in item:
            if _local(child.tag).lower() in ("content", "group"):
                url = (child.get("url") or "").strip()
                typ = (child.get("type") or "").strip()
                if url and _looks_audio(url, typ):
                    enclosure_url = url
                    enclosure_type = typ
                    break

    if not enclosure_url:
        return None
    if not feed_guid:
        feed_guid = enclosure_url
    return FeedEpisode(
        feed_guid=feed_guid,
        title=title or "Untitled episode",
        description=_strip_html(description),
        pub_date=pub_date,
        duration_sec=duration_sec,
        enclosure_url=enclosure_url,
        enclosure_type=enclosure_type,
        enclosure_bytes=enclosure_bytes,
        episode_index=episode_index,
        season=season,
    )


def _parse_atom(root: Element) -> FeedChannel:
    title = _child_text(root, ("title",))
    description = _child_text(root, ("subtitle", "summary"))
    author = ""
    for child in root:
        if _local(child.tag).lower() == "author":
            for sub in child:
                if _local(sub.tag).lower() == "name":
                    author = (sub.text or "").strip()
    site_url = ""
    for child in root:
        if _local(child.tag).lower() == "link":
            rel = (child.get("rel") or "alternate").lower()
            href = (child.get("href") or "").strip()
            if rel in ("alternate", "") and href:
                site_url = href
                break
    episodes: list[FeedEpisode] = []
    for child in root:
        if _local(child.tag).lower() == "entry":
            ep = _parse_atom_entry(child)
            if ep is not None:
                episodes.append(ep)
    episodes.sort(key=lambda e: (e.pub_date or "", e.feed_guid), reverse=True)
    if not episodes:
        raise ValueError(
            "Atom feed has no audio enclosures "
            "(only basic Atom enclosure links are supported)"
        )
    return FeedChannel(
        title=title,
        author=author,
        description=description,
        site_url=site_url,
        episodes=episodes,
    )


def _parse_atom_entry(entry: Element) -> FeedEpisode | None:
    title = _child_text(entry, ("title",))
    description = _child_text(entry, ("summary", "content"))
    pub_raw = _child_text(entry, ("published", "updated"))
    pub_date = _normalize_pub_date(pub_raw)
    feed_guid = _child_text(entry, ("id",))
    enclosure_url = ""
    enclosure_type = ""
    enclosure_bytes = 0
    for child in entry:
        if _local(child.tag).lower() != "link":
            continue
        rel = (child.get("rel") or "").lower()
        href = (child.get("href") or "").strip()
        typ = (child.get("type") or "").strip()
        length = child.get("length") or "0"
        if rel == "enclosure" and href and _looks_audio(href, typ):
            enclosure_url = href
            enclosure_type = typ
            try:
                enclosure_bytes = int(float(length))
            except (TypeError, ValueError):
                enclosure_bytes = 0
            break
    if not enclosure_url:
        return None
    if not feed_guid:
        feed_guid = enclosure_url
    return FeedEpisode(
        feed_guid=feed_guid,
        title=title or "Untitled episode",
        description=_strip_html(description),
        pub_date=pub_date,
        enclosure_url=enclosure_url,
        enclosure_type=enclosure_type,
        enclosure_bytes=enclosure_bytes,
    )


def _looks_audio(url: str, mime: str) -> bool:
    m = (mime or "").lower()
    u = (url or "").lower().split("?", 1)[0]
    if any(t in m for t in _AUDIO_TYPES):
        return True
    if m.startswith("audio/"):
        return True
    if u.endswith((".mp3", ".m4a", ".aac", ".ogg", ".opus", ".mp4")):
        # .mp4 might be video — allow if no mime or audio-ish
        if u.endswith(".mp4") and m and not m.startswith("audio/"):
            return False
        return True
    return False


def _parse_duration(raw: str) -> float:
    text = (raw or "").strip()
    if not text:
        return 0.0
    if text.isdigit():
        return float(text)
    # HH:MM:SS or MM:SS
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return 0.0


def _normalize_pub_date(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # RFC 2822 (RSS)
    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    # ISO-ish
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return text


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", text)
    return " ".join(plain.split())


def episode_to_row_dict(ep: FeedEpisode) -> dict[str, Any]:
    """Shape for podcast_index.upsert_episodes."""
    return {
        "feed_guid": ep.feed_guid,
        "title": ep.title,
        "description": ep.description,
        "pub_date": ep.pub_date,
        "duration_sec": ep.duration_sec,
        "enclosure_url": ep.enclosure_url,
        "enclosure_type": ep.enclosure_type,
        "enclosure_bytes": ep.enclosure_bytes,
        "episode_index": ep.episode_index,
        "season": ep.season,
    }
