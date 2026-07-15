"""Single transfer pipeline: optional transcode → transport.send."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from mtpmanager.domain.library import is_format
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

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
    stop_on_fatal: bool = True,
    session_log: bool = True,
) -> int:
    """Transfer many tracks. Returns number of successful sends.

    On a fatal TransportError (dead USB/MTP session, timeout, storage unusable),
    aborts the rest of the batch when *stop_on_fatal* is True (default) and
    re-raises so the UI can report it.

    When *session_log* is True, attaches a per-batch ``transfer-*.log`` handler
    for the duration of the batch.
    """
    total = len(tracks)
    succeeded = 0
    session_handler = None
    if session_log:
        try:
            session_handler = start_transfer_log()
        except OSError as exc:
            logger.warning("Could not open transfer session log: %s", exc)

    logger.info(
        "Batch transfer start: %d track(s) target_format=%s",
        total,
        target_format,
    )
    try:
        for i, track in enumerate(tracks):
            if on_progress:
                on_progress(i, total, track.path)
            logger.info("%d/%d - %s", i + 1, total, track.path)
            try:
                transfer_track(
                    track,
                    target_format=target_format,
                    transport=transport,
                    transcoder=transcoder,
                )
                succeeded += 1
            except TransportError as exc:
                remaining = total - i - 1
                logger.error(
                    "FAILED (%d/%d): %s fatal=%s path=%s rc=%s",
                    i + 1,
                    total,
                    exc,
                    exc.fatal,
                    exc.path or track.path,
                    exc.returncode,
                )
                if exc.stderr:
                    logger.error("Transport stderr:\n%s", exc.stderr)
                if exc.fatal and stop_on_fatal:
                    logger.error(
                        "Aborting batch: device/session looks unusable. "
                        "%d track(s) not attempted. Succeeded: %d/%d.",
                        remaining,
                        succeeded,
                        total,
                    )
                    if on_progress and total:
                        on_progress(i + 1, total, track.path)
                    raise
                logger.warning(
                    "Continuing after non-fatal failure (%d left).",
                    remaining,
                )
        if on_progress and total:
            on_progress(total, total, "")
        logger.info(
            "Batch transfer finished: succeeded=%d/%d",
            succeeded,
            total,
        )
        return succeeded
    finally:
        stop_transfer_log(session_handler)
