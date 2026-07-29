"""Unit tests for device media heuristics (no device)."""

from __future__ import annotations

import unittest

from mtpmanager.domain.device_media import (
    apply_track_info,
    enrich_refs_from_host,
    expand_podcast_parent_ids,
    guid_stems_from_files,
    is_placeholder_tag,
    looks_like_music,
    looks_like_podcast,
    looks_like_track,
    looks_like_video,
    merge_track_refs,
    music_refs_from_files,
    podcast_refs_from_files,
    ref_tags_look_placeholder,
    refs_needing_device_tags,
    resolve_device_tracks_for_display,
    tags_look_placeholder,
    track_meta_is_usable,
    track_meta_looks_placeholder,
    track_refs_from_files,
    video_folder_label,
    video_refs_from_files,
)
from mtpmanager.domain.models import (
    DeviceTrackInfo,
    DeviceTrackRef,
    FileEntry,
    Track,
    TrackMetadata,
)
from mtpmanager.domain.track_id import new_track_guid


def _file(
    oid: int,
    name: str,
    *,
    filetype: int = 0,
    parent_id: int = 100,
    storage_id: int = 0x00010001,
) -> FileEntry:
    return FileEntry(
        item_id=oid,
        name=name,
        parent_id=parent_id,
        storage_id=storage_id,
        filetype=filetype,
        filesize=1000,
    )


class LooksLikeTrackTests(unittest.TestCase):
    def test_mp3_filetype(self) -> None:
        self.assertTrue(looks_like_track(_file(1, "x.bin", filetype=2)))

    def test_extension_fallback(self) -> None:
        self.assertTrue(looks_like_track(_file(1, "Song.MP3", filetype=0)))
        self.assertTrue(looks_like_track(_file(1, "clip.wma", filetype=99)))

    def test_rejects_non_media(self) -> None:
        self.assertFalse(looks_like_track(_file(1, "cover.jpg", filetype=14)))
        self.assertFalse(looks_like_track(_file(1, "readme.txt", filetype=0)))
        self.assertFalse(looks_like_track(_file(1, "Music", filetype=0)))


class TrackRefsFromFilesTests(unittest.TestCase):
    def test_filters_and_maps(self) -> None:
        files = [
            _file(10, "a.mp3", filetype=2),
            _file(11, "cover.jpg", filetype=14),
            _file(12, "b.wma", filetype=3),
            _file(13, "notes.txt", filetype=0),
            _file(14, "c.FLAC", filetype=0),  # ext fallback
        ]
        refs = track_refs_from_files(files)
        self.assertEqual([r.item_id for r in refs], [10, 12, 14])
        self.assertEqual(refs[0].name, "a.mp3")
        self.assertEqual(refs[0].title, "")
        self.assertEqual(refs[0].artist, "")
        self.assertEqual(refs[0].parent_id, 100)
        self.assertEqual(refs[0].storage_id, 0x00010001)
        self.assertEqual(refs[0].filetype, 2)
        self.assertEqual(refs[2].name, "c.FLAC")
        self.assertEqual(refs[2].filetype, 0)

    def test_sort_by_name_when_tags_empty(self) -> None:
        files = [
            _file(3, "zeta.mp3", filetype=2),
            _file(1, "alpha.mp3", filetype=2),
            _file(2, "beta.mp3", filetype=2),
        ]
        refs = track_refs_from_files(files)
        self.assertEqual([r.name for r in refs], ["alpha.mp3", "beta.mp3", "zeta.mp3"])

    def test_empty(self) -> None:
        self.assertEqual(track_refs_from_files([]), [])


class MergeTrackRefsTests(unittest.TestCase):
    def test_prefers_tagged_and_adds_missing(self) -> None:
        tagged = [
            DeviceTrackRef(
                item_id=10,
                name="a.mp3",
                title="Alpha",
                artist="Artist",
                filetype=2,
            )
        ]
        from_files = track_refs_from_files(
            [
                _file(10, "a.mp3", filetype=2),
                _file(20, "b.mp3", filetype=2),
            ]
        )
        merged = merge_track_refs(tagged, from_files)
        by_id = {r.item_id: r for r in merged}
        self.assertEqual(set(by_id), {10, 20})
        # Sort is artist/title/name - empty artist (file-only) sorts first.
        self.assertEqual([r.item_id for r in merged], [20, 10])
        self.assertEqual(by_id[10].title, "Alpha")
        self.assertEqual(by_id[10].artist, "Artist")
        self.assertEqual(by_id[20].title, "")
        self.assertEqual(by_id[20].name, "b.mp3")


class LooksLikeMusicTests(unittest.TestCase):
    def test_accepts_audio(self) -> None:
        self.assertTrue(looks_like_music(_file(1, "a.mp3", filetype=2)))
        self.assertTrue(looks_like_music(_file(2, "b.flac", filetype=0)))

    def test_rejects_video_and_zen_cast(self) -> None:
        self.assertFalse(looks_like_music(_file(1, "clip.avi", filetype=9)))
        self.assertFalse(
            looks_like_music(_file(2, "show.wmv", filetype=8, parent_id=120))
        )
        self.assertFalse(
            looks_like_music(_file(3, "pod.mp3", filetype=2, parent_id=128))
        )


class MusicRefsFromFilesTests(unittest.TestCase):
    def test_filters_video(self) -> None:
        files = [
            _file(10, "a.mp3", filetype=2),
            _file(11, "clip.avi", filetype=9),
            _file(12, "b.wma", filetype=3),
        ]
        refs = music_refs_from_files(files)
        self.assertEqual([r.item_id for r in refs], [10, 12])


class LooksLikeVideoTests(unittest.TestCase):
    def test_accepts_video_filetype_and_ext(self) -> None:
        self.assertTrue(looks_like_video(_file(1, "clip.avi", filetype=9)))
        self.assertTrue(looks_like_video(_file(2, "show.wmv", filetype=0)))

    def test_accepts_mp4_under_video_folder(self) -> None:
        self.assertTrue(
            looks_like_video(_file(3, "movie.mp4", filetype=6, parent_id=120))
        )
        self.assertTrue(
            looks_like_video(_file(4, "ep.m4v", filetype=0, parent_id=124))
        )

    def test_rejects_audio_under_music(self) -> None:
        self.assertFalse(looks_like_video(_file(5, "a.mp3", filetype=2)))
        # mp4 under Music is not treated as device-video tab content
        self.assertFalse(
            looks_like_video(_file(6, "audio.mp4", filetype=6, parent_id=100))
        )

    def test_rejects_zencast_video(self) -> None:
        # Podcast video lives under ZENcast — Device → Podcasts, not Video.
        self.assertFalse(
            looks_like_video(
                _file(7, "Episode.avi", filetype=9, parent_id=128)
            )
        )
        self.assertFalse(
            looks_like_video(
                _file(8, "ep.mp4", filetype=0, parent_id=200),
                podcast_parents=frozenset({128, 200}),
            )
        )


class LooksLikePodcastTests(unittest.TestCase):
    def test_accepts_audio_and_video_under_zencast(self) -> None:
        self.assertTrue(
            looks_like_podcast(_file(1, "a.mp3", filetype=2, parent_id=128))
        )
        self.assertTrue(
            looks_like_podcast(
                _file(2, "Episode.avi", filetype=9, parent_id=128)
            )
        )

    def test_rejects_music_and_video_folders(self) -> None:
        self.assertFalse(
            looks_like_podcast(_file(1, "a.mp3", filetype=2, parent_id=100))
        )
        self.assertFalse(
            looks_like_podcast(
                _file(2, "clip.avi", filetype=9, parent_id=120)
            )
        )

    def test_show_folder_descendant(self) -> None:
        parents = frozenset({128, 500})  # 500 = show folder under ZENcast
        self.assertTrue(
            looks_like_podcast(
                _file(3, "ep.mp3", filetype=2, parent_id=500),
                podcast_parents=parents,
            )
        )


class ExpandPodcastParentsTests(unittest.TestCase):
    def test_includes_descendants(self) -> None:
        # 128 ZENcast → 500 show → 501 nested
        folder_parents = {500: 128, 501: 500, 120: 0}
        got = expand_podcast_parent_ids(128, folder_parents)
        self.assertIn(128, got)
        self.assertIn(500, got)
        self.assertIn(501, got)
        self.assertNotIn(120, got)


class PodcastRefsFromFilesTests(unittest.TestCase):
    def test_filters_to_zencast(self) -> None:
        files = [
            _file(10, "a.mp3", filetype=2, parent_id=100),
            _file(11, "pod.mp3", filetype=2, parent_id=128),
            _file(12, "Episode.avi", filetype=9, parent_id=128),
            _file(13, "movie.avi", filetype=9, parent_id=120),
        ]
        refs = podcast_refs_from_files(files)
        self.assertEqual([r.item_id for r in refs], [12, 11])


class VideoRefsFromFilesTests(unittest.TestCase):
    def test_filters_and_keeps_video_parents(self) -> None:
        files = [
            _file(10, "a.mp3", filetype=2, parent_id=100),
            _file(11, "clip.avi", filetype=9, parent_id=120),
            _file(12, "show.wmv", filetype=8, parent_id=124),
            _file(13, "notes.txt", filetype=0),
        ]
        refs = video_refs_from_files(files)
        self.assertEqual([r.item_id for r in refs], [11, 12])
        self.assertEqual(video_folder_label(120), "Video")
        self.assertEqual(video_folder_label(124), "TV")
        self.assertEqual(video_folder_label(99), "Other")


class ApplyTrackInfoTests(unittest.TestCase):
    def test_overlays_tags_keeps_id(self) -> None:
        ref = DeviceTrackRef(
            item_id=42,
            name="short.mp3",
            title="",
            artist="",
            parent_id=100,
            storage_id=0x00010001,
            filetype=2,
        )
        info = DeviceTrackInfo(
            item_id=42,
            name="short.mp3",
            title="Full Title",
            artist="The Artist",
            album="The Album",
            date="2005",
            tracknumber=3,
            genre="Audiobook",
            parent_id=100,
            storage_id=0x00010001,
            filetype=2,
        )
        out = apply_track_info(ref, info)
        self.assertEqual(out.item_id, 42)
        self.assertEqual(out.title, "Full Title")
        self.assertEqual(out.artist, "The Artist")
        self.assertEqual(out.album, "The Album")
        self.assertEqual(out.date, "2005")
        self.assertEqual(out.tracknumber, "3")
        self.assertEqual(out.genre, "Audiobook")
        self.assertEqual(out.name, "short.mp3")
        self.assertEqual(out.parent_id, 100)


class TrackLineFallbackTests(unittest.TestCase):
    def test_title_falls_back_to_name(self) -> None:
        from mtpmanager.ui.formatting import track_line

        line = track_line(
            DeviceTrackRef(item_id=1, name="song.mp3", title="", artist="", filetype=2)
        )
        # Empty title -> filename appears as the title column (not a bare em dash).
        self.assertIn("song.mp3", line)
        with_title = track_line(
            DeviceTrackRef(
                item_id=2,
                name="file.mp3",
                title="Real Title",
                artist="Band",
                filetype=2,
            )
        )
        self.assertIn("Real Title", with_title)
        self.assertIn("Band", with_title)


class PlaceholderTagTests(unittest.TestCase):
    def test_is_placeholder_tag(self) -> None:
        self.assertTrue(is_placeholder_tag(""))
        self.assertTrue(is_placeholder_tag("Unknown Artist"))
        self.assertTrue(is_placeholder_tag("unknown title"))
        self.assertTrue(is_placeholder_tag("—"))
        # Device firmware / Creative-style angle brackets (not our defaults).
        self.assertTrue(is_placeholder_tag("<Unknown>"))
        self.assertTrue(is_placeholder_tag("<unknown>"))
        self.assertTrue(is_placeholder_tag("<Unknown Artist>"))
        self.assertFalse(is_placeholder_tag("Radiohead"))
        self.assertFalse(is_placeholder_tag("Paranoid Android"))

    def test_tags_look_placeholder_requires_artist_and_title(self) -> None:
        self.assertTrue(
            tags_look_placeholder(
                title="Unknown Title",
                artist="Unknown Artist",
                album="Unknown Album",
            )
        )
        self.assertTrue(
            tags_look_placeholder(title="", artist="", album="Something")
        )
        # Device literal <Unknown> + filename-as-title.
        self.assertTrue(
            tags_look_placeholder(
                title="song.mp3",
                artist="<Unknown>",
                album="<Unknown>",
                object_name="song.mp3",
            )
        )
        self.assertTrue(
            tags_look_placeholder(
                title="song",
                artist="<Unknown>",
                album="<Unknown>",
                object_name="song.mp3",
            )
        )
        # Real title alone is enough to treat as usable listing.
        self.assertFalse(
            tags_look_placeholder(
                title="Song",
                artist="Unknown Artist",
                album="Unknown Album",
            )
        )
        self.assertFalse(
            tags_look_placeholder(
                title="Unknown Title",
                artist="Band",
                album="Unknown Album",
            )
        )

    def test_ref_and_meta_helpers(self) -> None:
        empty = DeviceTrackRef(
            item_id=1,
            name="x.mp3",
            title="Unknown Title",
            artist="Unknown Artist",
            album="Unknown Album",
        )
        self.assertTrue(ref_tags_look_placeholder(empty))
        # Mass-storage-ish dump: device tags are <Unknown>, title = filename.
        angle = DeviceTrackRef(
            item_id=3,
            name="dump.mp3",
            title="dump.mp3",
            artist="<Unknown>",
            album="<Unknown>",
        )
        self.assertTrue(ref_tags_look_placeholder(angle))
        good = DeviceTrackRef(
            item_id=2, name="y.mp3", title="T", artist="A", album="B"
        )
        self.assertFalse(ref_tags_look_placeholder(good))
        self.assertTrue(
            track_meta_looks_placeholder(
                TrackMetadata(
                    title="Unknown Title",
                    artist="Unknown Artist",
                    album="Unknown Album",
                )
            )
        )
        self.assertTrue(
            track_meta_is_usable(
                TrackMetadata(title="Hello", artist="Unknown Artist")
            )
        )
        self.assertFalse(
            track_meta_is_usable(
                TrackMetadata(
                    title="Unknown Title", artist="Unknown Artist"
                )
            )
        )
        # Angle-bracket artist from device is not "usable" identity alone.
        self.assertTrue(is_placeholder_tag("<Unknown>"))
        self.assertFalse(
            track_meta_is_usable(
                TrackMetadata(
                    title="Unknown Title", artist="<Unknown>"
                )
            )
        )


class GuidJoinTests(unittest.TestCase):
    def test_guid_stems_from_files(self) -> None:
        g = new_track_guid()
        files = [
            _file(1, f"{g}.mp3", filetype=2),
            _file(2, "08 Title.mp3", filetype=2),
            _file(3, f"{g.upper()}.WMA", filetype=3),
        ]
        stems = guid_stems_from_files(files)
        self.assertEqual(stems, {g})

    def test_enrich_refs_from_host(self) -> None:
        g = new_track_guid()
        refs = track_refs_from_files(
            [
                _file(1, f"{g}.mp3", filetype=2),
                _file(2, "foreign.mp3", filetype=2),
            ]
        )
        by_guid = {
            g: Track(
                path="/x.mp3",
                meta=TrackMetadata(
                    title="Host Title",
                    artist="Host Artist",
                    album="Host Album",
                    genre="Audiobook",
                ),
                guid=g,
            )
        }
        out = enrich_refs_from_host(refs, by_guid)
        by_id = {r.item_id: r for r in out}
        self.assertEqual(by_id[1].title, "Host Title")
        self.assertEqual(by_id[1].artist, "Host Artist")
        self.assertEqual(by_id[1].album, "Host Album")
        self.assertEqual(by_id[1].genre, "Audiobook")
        self.assertEqual(by_id[2].title, "")
        self.assertEqual(by_id[2].name, "foreign.mp3")

        display = resolve_device_tracks_for_display(out, by_guid)
        host_row = next(t for t in display if t.guid == g)
        self.assertEqual(host_row.meta.genre, "Audiobook")

    def test_resolve_device_tracks_for_display_priority(self) -> None:
        g = new_track_guid()
        refs = [
            DeviceTrackRef(
                item_id=1,
                name=f"{g}.mp3",
                title="Device Title",
                artist="Device Artist",
                album="Device Album",
                filetype=2,
            ),
            DeviceTrackRef(
                item_id=2,
                name="tagged.mp3",
                title="On Device",
                artist="Band",
                album="LP",
                filetype=2,
            ),
            DeviceTrackRef(
                item_id=3,
                name="orphan.mp3",
                title="",
                artist="",
                album="",
                filetype=2,
            ),
        ]
        by_guid = {
            g: Track(
                path="/host/song.mp3",
                meta=TrackMetadata(
                    title="Host Title",
                    artist="Host Artist",
                    album="Host Album",
                    date="2010",
                    tracknumber="4",
                ),
                guid=g,
            )
        }
        tracks = resolve_device_tracks_for_display(refs, by_guid)
        self.assertEqual(len(tracks), 3)
        # GUID → host wins over device tags.
        self.assertEqual(tracks[0].meta.title, "Host Title")
        self.assertEqual(tracks[0].meta.album, "Host Album")
        self.assertTrue(tracks[0].path.startswith("device:1:"))
        self.assertIn("/host/song.mp3", tracks[0].path)
        # Device tags when no GUID hit.
        self.assertEqual(tracks[1].meta.title, "On Device")
        self.assertEqual(tracks[1].meta.artist, "Band")
        self.assertEqual(tracks[1].meta.album, "LP")
        # Filename fallback.
        self.assertEqual(tracks[2].meta.title, "orphan.mp3")
        self.assertEqual(tracks[2].meta.artist, "Unknown Artist")

        need = refs_needing_device_tags(refs, by_guid)
        self.assertEqual([r.item_id for r in need], [3])

    def test_unknown_title_falls_back_to_filename(self) -> None:
        """Device tags often leave video title as the Unknown Title placeholder."""
        refs = [
            DeviceTrackRef(
                item_id=10,
                name="Holiday_Clip.avi",
                title="Unknown Title",
                artist="Unknown Artist",
                album="Unknown Album",
                filetype=9,
                parent_id=120,
            ),
            DeviceTrackRef(
                item_id=11,
                name="Real.wmv",
                title="Real Title",
                artist="Dir",
                album="",
                filetype=8,
                parent_id=124,
            ),
        ]
        tracks = resolve_device_tracks_for_display(refs, {})
        self.assertEqual(tracks[0].meta.title, "Holiday_Clip.avi")
        self.assertEqual(tracks[1].meta.title, "Real Title")


if __name__ == "__main__":
    unittest.main()
