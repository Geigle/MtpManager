"""Special Sync options helpers and transfer overrides."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mtpmanager.app.transfer import (
    _resolve_parent,
    prepare_track,
    transfer_tracks,
)
from mtpmanager.domain.audio_encode import AudioEncodeSettings
from mtpmanager.domain.models import Track, TrackMetadata
from mtpmanager.domain.special_sync import (
    SpecialSyncOptions,
    apply_meta_patch,
    basename_for_special_sync,
    common_meta_seed,
    meta_patch_from_dialog_fields,
)


class MetaPatchTests(unittest.TestCase):
    def test_apply_nonempty_only(self) -> None:
        meta = TrackMetadata(title="T", artist="A", album="Al")
        out = apply_meta_patch(meta, {"title": "New", "artist": ""})
        self.assertEqual(out.title, "New")
        self.assertEqual(out.artist, "A")
        self.assertEqual(out.album, "Al")

    def test_common_meta_seed_varies(self) -> None:
        tracks = [
            Track(
                path="a.mp3",
                meta=TrackMetadata(title="One", artist="Band"),
                guid="a" * 32,
            ),
            Track(
                path="b.mp3",
                meta=TrackMetadata(title="Two", artist="Band"),
                guid="b" * 32,
            ),
        ]
        seed = common_meta_seed(tracks)
        self.assertEqual(seed["artist"], "Band")
        self.assertEqual(seed["title"], "")

    def test_dialog_patch_omits_unchanged_seed(self) -> None:
        seed = {"title": "T", "artist": "A"}
        fields = {"title": "T", "artist": "B"}
        patch = meta_patch_from_dialog_fields(fields, seed=seed)
        self.assertEqual(patch, {"artist": "B"})

    def test_dialog_patch_apply_all(self) -> None:
        seed = {"title": "T", "artist": "A"}
        fields = {"title": "T", "artist": "A"}
        patch = meta_patch_from_dialog_fields(
            fields, seed=seed, apply_all=True
        )
        self.assertEqual(patch, {"title": "T", "artist": "A"})


class BasenameTests(unittest.TestCase):
    def test_source_stem_default(self) -> None:
        track = Track(
            path="/lib/My Song&.flac",
            meta=TrackMetadata(title="Ignored"),
        )
        opts = SpecialSyncOptions(use_guid=False, basename_mode="source_stem")
        name = basename_for_special_sync(track, track.meta, "mp3", options=opts)
        self.assertTrue(name.endswith(".mp3"))
        self.assertNotIn("&", name)
        self.assertIn("My Song", name)

    def test_title_mode(self) -> None:
        track = Track(
            path="/lib/file.flac",
            meta=TrackMetadata(title="Hello World"),
        )
        opts = SpecialSyncOptions(use_guid=False, basename_mode="title")
        name = basename_for_special_sync(track, track.meta, "mp3", options=opts)
        self.assertEqual(name, "Hello World.mp3")

    def test_custom_basename(self) -> None:
        track = Track(path="/lib/file.flac", meta=TrackMetadata(title="T"))
        opts = SpecialSyncOptions(
            use_guid=False, custom_basename="Retail Demo"
        )
        name = basename_for_special_sync(track, track.meta, "wma", options=opts)
        self.assertEqual(name, "Retail Demo.wma")


class ResolveParentSpecialTests(unittest.TestCase):
    def test_fixed_parent_wins_with_guid(self) -> None:
        meta = TrackMetadata(genre="Rock")
        parent = _resolve_parent(
            lambda m: 999,
            meta,
            guid="c" * 32,
            fixed_parent_id=88,
        )
        self.assertEqual(parent, 88)

    def test_fixed_none_falls_back(self) -> None:
        meta = TrackMetadata(genre="Rock")
        parent = _resolve_parent(
            lambda m: 999,
            meta,
            guid="c" * 32,
            fixed_parent_id=None,
        )
        self.assertIsNone(parent)


class _FakeTranscoder:
    def __init__(self, temp_dir: str) -> None:
        self.temp_dir = temp_dir
        self.calls: list[tuple[str, str, int]] = []

    def convert(
        self,
        src_path: str,
        target_format: str,
        *,
        slot: int = 0,
        settings=None,
        force: bool = False,
    ) -> str:
        target_format = target_format.lower().lstrip(".")
        out = os.path.join(self.temp_dir, f"TRANSCODE_{slot}.{target_format}")
        self.calls.append((src_path, target_format, slot))
        Path(out).write_text(f"from:{src_path}", encoding="utf-8")
        return out

    def cleanup(self, path: str | None) -> None:
        if path and os.path.isfile(path):
            os.remove(path)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_track(
        self,
        path: str,
        meta: TrackMetadata,
        *,
        parent_id: int | None = None,
        guid: str | None = None,
        preferred_basename: str | None = None,
    ) -> int | None:
        self.sent.append(
            {
                "path": path,
                "title": meta.title,
                "artist": meta.artist,
                "parent_id": parent_id,
                "guid": guid,
                "preferred_basename": preferred_basename,
            }
        )
        return 42


class SpecialTransferTests(unittest.TestCase):
    def test_prepare_meta_patch_and_fixed_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "clip.flac")
            Path(src).write_bytes(b"x")
            tr = _FakeTranscoder(tmp)
            special = SpecialSyncOptions(
                meta_patch={"title": "Override"},
                parent_id=77,
                use_guid=True,
            )
            prep = prepare_track(
                Track(
                    path=src,
                    meta=TrackMetadata(title="Orig", artist="A"),
                    guid="d" * 32,
                ),
                target_format="mp3",
                transcoder=tr,
                reread_tags_after_convert=False,
                special=special,
            )
            self.assertEqual(prep.meta.title, "Override")
            self.assertEqual(prep.fixed_parent_id, 77)
            self.assertTrue(prep.use_guid)
            self.assertEqual(prep.guid, "d" * 32)
            tr.cleanup(prep.cleanup_path)

    def test_no_guid_uses_preferred_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "MyFile.flac")
            Path(src).write_bytes(b"x")
            tr = _FakeTranscoder(tmp)
            transport = _RecordingTransport()
            special = SpecialSyncOptions(
                use_guid=False,
                basename_mode="source_stem",
                parent_id=100,
                skip_if_present=False,
            )
            n = transfer_tracks(
                [
                    Track(
                        path=src,
                        meta=TrackMetadata(title="Song"),
                        guid="e" * 32,
                    )
                ],
                target_format="mp3",
                transport=transport,
                transcoder=tr,
                session_log=False,
                special=special,
            )
            self.assertEqual(n, 1)
            self.assertEqual(len(transport.sent), 1)
            call = transport.sent[0]
            self.assertIsNone(call["guid"])
            self.assertEqual(call["parent_id"], 100)
            self.assertIsNotNone(call["preferred_basename"])
            self.assertTrue(
                str(call["preferred_basename"]).startswith("MyFile")
            )

    def test_skip_if_present_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.flac")
            Path(src).write_bytes(b"x")
            tr = _FakeTranscoder(tmp)
            transport = _RecordingTransport()
            guid = "f" * 32
            special = SpecialSyncOptions(
                use_guid=True,
                skip_if_present=False,
            )
            n = transfer_tracks(
                [Track(path=src, meta=TrackMetadata(title="T"), guid=guid)],
                target_format="mp3",
                transport=transport,
                transcoder=tr,
                session_log=False,
                device_guid_stems={guid},
                special=special,
            )
            self.assertEqual(n, 1)
            self.assertEqual(len(transport.sent), 1)

    def test_skip_if_present_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.flac")
            Path(src).write_bytes(b"x")
            tr = _FakeTranscoder(tmp)
            transport = _RecordingTransport()
            guid = "1" * 32
            n = transfer_tracks(
                [Track(path=src, meta=TrackMetadata(title="T"), guid=guid)],
                target_format="mp3",
                transport=transport,
                transcoder=tr,
                session_log=False,
                device_guid_stems={guid},
                special=SpecialSyncOptions(use_guid=True, skip_if_present=True),
            )
            self.assertEqual(n, 1)
            self.assertEqual(transport.sent, [])


if __name__ == "__main__":
    unittest.main()
