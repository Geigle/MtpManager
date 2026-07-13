"""Single transfer pipeline: optional transcode → transport.send."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mtpmanager.domain.library import is_format
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport

ProgressCallback = Callable[[int, int, str], None]


def transfer_track(
    track: Track,
    *,
    target_format: str,
    transport: Transport,
    transcoder: Transcoder,
    reread_tags_after_convert: bool = True,
) -> None:
    """
    Ensure track is in target_format (transcode if needed), then send via transport.
    Temp files from the transcoder are always cleaned up.
    """
    target_format = target_format.lower().lstrip(".")
    src = track.path
    cleanup_path: str | None = None
    meta = track.meta

    try:
        if not is_format(src, target_format):
            src = transcoder.convert(src, target_format)
            cleanup_path = src
            if reread_tags_after_convert:
                # Prefer original tags; merge length/stream info from output if useful
                converted = read_metadata(src)
                meta = TrackMetadata(
                    artist=meta.artist or converted.artist,
                    albumartist=meta.albumartist or converted.albumartist,
                    composer=meta.composer or converted.composer,
                    album=meta.album or converted.album,
                    title=meta.title or converted.title,
                    genre=meta.genre or converted.genre,
                    tracknumber=meta.tracknumber or converted.tracknumber,
                    date=meta.date or converted.date,
                    length_sec=converted.length_sec or meta.length_sec,
                    sample_rate=converted.sample_rate or meta.sample_rate,
                    channels=converted.channels or meta.channels,
                    bitrate=converted.bitrate or meta.bitrate,
                    bitrate_mode=converted.bitrate_mode or meta.bitrate_mode,
                )
        transport.send_track(src, meta)
    finally:
        if cleanup_path is not None:
            transcoder.cleanup(cleanup_path)


def transfer_tracks(
    tracks: Sequence[Track],
    *,
    target_format: str,
    transport: Transport,
    transcoder: Transcoder,
    on_progress: ProgressCallback | None = None,
) -> None:
    total = len(tracks)
    for i, track in enumerate(tracks):
        if on_progress:
            on_progress(i, total, track.path)
        print(f"{i + 1}/{total} - {track.path}")
        transfer_track(
            track,
            target_format=target_format,
            transport=transport,
            transcoder=transcoder,
        )
    if on_progress and total:
        on_progress(total, total, "")
