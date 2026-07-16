"""Single transfer pipeline: optional transcode → transport.send.

Batch transfers pipeline convert of track N+1 into the alternate temp slot
while track N is being sent, so ffmpeg cannot clobber a file in flight.
Works for any Transport (CMD mtp-sendtr or Experimental PyMTP).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mtpmanager.domain.library import is_format
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.infra.logging_setup import start_transfer_log, stop_transfer_log
from mtpmanager.infra.mutagen_tags import read_metadata
from mtpmanager.ports.transcoder import Transcoder
from mtpmanager.ports.transport import Transport, TransportError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class PreparedTrack:
    """Local path + metadata ready for transport.send_track."""

    send_path: str
    meta: TrackMetadata
    cleanup_path: str | None
    source_path: str


def _merge_meta_after_convert(
    original: TrackMetadata, converted: TrackMetadata
) -> TrackMetadata:
    """Prefer original tags; take stream length/bitrate from converted when useful."""
    return TrackMetadata(
        artist=original.artist or converted.artist,
        albumartist=original.albumartist or converted.albumartist,
        composer=original.composer or converted.composer,
        album=original.album or converted.album,
        title=original.title or converted.title,
        genre=original.genre or converted.genre,
        tracknumber=original.tracknumber or converted.tracknumber,
        date=original.date or converted.date,
        length_sec=converted.length_sec or original.length_sec,
        sample_rate=converted.sample_rate or original.sample_rate,
        channels=converted.channels or original.channels,
        bitrate=converted.bitrate or original.bitrate,
        bitrate_mode=converted.bitrate_mode or original.bitrate_mode,
    )


def prepare_track(
    track: Track,
    *,
    target_format: str,
    transcoder: Transcoder,
    slot: int = 0,
    reread_tags_after_convert: bool = True,
) -> PreparedTrack:
    """Transcode into *slot* if needed; return path/meta for send (no send yet)."""
    target_format = target_format.lower().lstrip(".")
    src = track.path
    meta = track.meta
    cleanup_path: str | None = None

    if not is_format(src, target_format):
        src = transcoder.convert(src, target_format, slot=slot)
        cleanup_path = src
        if reread_tags_after_convert:
            converted = read_metadata(src)
            meta = _merge_meta_after_convert(meta, converted)

    return PreparedTrack(
        send_path=src,
        meta=meta,
        cleanup_path=cleanup_path,
        source_path=track.path,
    )


def transfer_track(
    track: Track,
    *,
    target_format: str,
    transport: Transport,
    transcoder: Transcoder,
    reread_tags_after_convert: bool = True,
    slot: int = 0,
) -> None:
    """
    Ensure track is in target_format (transcode if needed), then send via transport.
    Temp files from the transcoder are always cleaned up.
    """
    prepared = prepare_track(
        track,
        target_format=target_format,
        transcoder=transcoder,
        slot=slot,
        reread_tags_after_convert=reread_tags_after_convert,
    )
    try:
        transport.send_track(prepared.send_path, prepared.meta)
    finally:
        if prepared.cleanup_path is not None:
            transcoder.cleanup(prepared.cleanup_path)


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
    """Transfer many tracks with dual-slot convert/send pipeline.

    While track *i* is sent (blocking transport), track *i+1* is prepared on a
    helper thread into the alternate temp slot (``i % 2`` vs ``(i+1) % 2``).
    Returns number of successful sends.

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
        "Batch transfer start: %d track(s) target_format=%s (dual-slot pipeline)",
        total,
        target_format,
    )

    prepared: PreparedTrack | None = None
    next_future: Future[PreparedTrack] | None = None

    def _cleanup(prep: PreparedTrack | None) -> None:
        if prep is not None and prep.cleanup_path is not None:
            transcoder.cleanup(prep.cleanup_path)

    def _cancel_next() -> None:
        nonlocal next_future
        if next_future is None:
            return
        next_future.cancel()
        try:
            if next_future.done() and not next_future.cancelled():
                _cleanup(next_future.result())
        except Exception:
            pass
        next_future = None

    try:
        if total == 0:
            return 0

        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mtpmanager-prep"
        ) as pool:
            # Prepare first track on the batch thread (no prior send to overlap).
            prepared = prepare_track(
                tracks[0],
                target_format=target_format,
                transcoder=transcoder,
                slot=0,
            )

            for i, track in enumerate(tracks):
                if on_progress:
                    on_progress(i, total, track.path)
                logger.info("%d/%d - %s", i + 1, total, track.path)

                # Kick off prepare for the next track into the other slot while we send.
                if i + 1 < total:
                    next_slot = (i + 1) % 2
                    next_track = tracks[i + 1]
                    next_future = pool.submit(
                        prepare_track,
                        next_track,
                        target_format=target_format,
                        transcoder=transcoder,
                        slot=next_slot,
                    )

                assert prepared is not None
                try:
                    transport.send_track(prepared.send_path, prepared.meta)
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
                    _cleanup(prepared)
                    prepared = None
                    if exc.fatal and stop_on_fatal:
                        _cancel_next()
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
                    # Fall through to fetch next prepared track if any.
                else:
                    _cleanup(prepared)
                    prepared = None

                if next_future is not None:
                    try:
                        prepared = next_future.result()
                    except Exception:
                        next_future = None
                        raise
                    next_future = None
                else:
                    prepared = None

        if on_progress and total:
            on_progress(total, total, "")
        logger.info(
            "Batch transfer finished: succeeded=%d/%d",
            succeeded,
            total,
        )
        return succeeded
    finally:
        _cleanup(prepared)
        _cancel_next()
        stop_transfer_log(session_handler)
