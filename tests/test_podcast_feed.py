"""RSS podcast feed parse tests (fixtures, no network)."""

from __future__ import annotations

import unittest

from mtpmanager.infra.podcast_feed import parse_feed_xml


_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Show</title>
    <link>https://example.com/show</link>
    <description>A test podcast</description>
    <itunes:author>Host Name</itunes:author>
    <itunes:image href="https://example.com/art.jpg"/>
    <item>
      <title>Episode Two</title>
      <guid>ep-2</guid>
      <pubDate>Mon, 02 Jun 2025 12:00:00 GMT</pubDate>
      <itunes:duration>1:02:03</itunes:duration>
      <description>Second episode</description>
      <enclosure url="https://cdn.example.com/ep2.mp3" length="1000" type="audio/mpeg"/>
    </item>
    <item>
      <title>Episode One</title>
      <guid>ep-1</guid>
      <pubDate>Mon, 01 Jun 2025 12:00:00 GMT</pubDate>
      <itunes:duration>300</itunes:duration>
      <enclosure url="https://cdn.example.com/ep1.mp3" length="500" type="audio/mpeg"/>
    </item>
    <item>
      <title>Video only</title>
      <guid>vid-1</guid>
      <enclosure url="https://cdn.example.com/v.mp4" length="9" type="video/mp4"/>
    </item>
  </channel>
</rss>
"""

_DUAL_ENCLOSURE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Dual Show</title>
    <item>
      <title>Has both</title>
      <guid>both-1</guid>
      <enclosure url="https://cdn.example.com/ep.mp3" length="1" type="audio/mpeg"/>
      <enclosure url="https://cdn.example.com/ep.mp4" length="99" type="video/mp4"/>
    </item>
  </channel>
</rss>
"""


class PodcastFeedParseTests(unittest.TestCase):
    def test_parse_rss_includes_video(self) -> None:
        ch = parse_feed_xml(_SAMPLE_RSS)
        self.assertEqual(ch.title, "Test Show")
        self.assertEqual(ch.author, "Host Name")
        self.assertEqual(ch.image_url, "https://example.com/art.jpg")
        self.assertEqual(len(ch.episodes), 3)
        # Newest first among dated items; video-only has no date so last-ish
        by_guid = {e.feed_guid: e for e in ch.episodes}
        self.assertIn("ep-2", by_guid)
        self.assertIn("ep-1", by_guid)
        self.assertIn("vid-1", by_guid)
        self.assertFalse(by_guid["ep-2"].is_video)
        self.assertTrue(by_guid["vid-1"].is_video)
        self.assertEqual(
            by_guid["vid-1"].enclosure_url, "https://cdn.example.com/v.mp4"
        )
        self.assertAlmostEqual(by_guid["ep-2"].duration_sec, 3723.0)
        self.assertAlmostEqual(by_guid["ep-1"].duration_sec, 300.0)
        self.assertTrue(by_guid["ep-2"].pub_date.startswith("2025-06-02"))

    def test_dual_enclosure_prefers_audio_marks_video(self) -> None:
        ch = parse_feed_xml(_DUAL_ENCLOSURE_RSS)
        self.assertEqual(len(ch.episodes), 1)
        ep = ch.episodes[0]
        self.assertTrue(ep.is_video)
        self.assertEqual(ep.enclosure_url, "https://cdn.example.com/ep.mp3")
        self.assertEqual(ep.enclosure_type, "audio/mpeg")
        self.assertEqual(ep.video_enclosure_url, "https://cdn.example.com/ep.mp4")
        self.assertEqual(ep.video_enclosure_type, "video/mp4")

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_feed_xml("not xml at all")


if __name__ == "__main__":
    unittest.main()
