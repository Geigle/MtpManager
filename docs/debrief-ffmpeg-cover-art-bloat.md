# Debrief: ffmpeg remuxed FLAC cover art into “audio” temps

**Symptom class:** low-bitrate encode presets produce **noticeably lossy audio** but **file sizes that barely shrink** (or stay enormous); device Track Info shows **absurd bitrates** (hundreds of kbps–Mbps); on ZEN Vision:M some tracks **take ~1 minute before playback starts**.

**Status:** Fixed in `infra/ffmpeg_transcode.py` (audio-only map). Re-sync required for objects already on the device.

**Related:** [transfer-and-modes.md](./transfer-and-modes.md) · [decisions.md](./decisions.md) D16 · [device-contract.md](./device-contract.md) (album art is MTP abstract albums, not track payloads)

---

## Summary

Default **ffmpeg stream mapping copies attached pictures** from source containers (common on FLACs) into the output as a second stream (MJPEG/PNG “video” / attached pic). MtpManager’s convert path only intended to produce a compact device audio file (`TRANSCODE_N.mp3` etc.). With cover art remuxed in:

1. **Size** is dominated by the image, not the audio recipe (VBR q=9 vs q=1 can look almost the same on art-heavy FLACs).
2. **Sound** still follows the encode settings → user hears aggressive compression while size “doesn’t make sense.”
3. **MTP / device bitrate tags** are often garbage for these objects (fixed or source-like values that do not match size÷duration).
4. **Playback start latency** on picky players (observed ~60s on ZEN Vision:M for one heavily bloated track) improves dramatically once the object is audio-only — firmware appears to walk or buffer a large object / extra stream before decode.

Host UI album art and **device** album art (abstract MTP album + JPEG sample) are separate pipelines. They must **not** be satisfied by stuffing cover pixels into the track file on the wire.

---

## Evidence (live user observation, 2026-08)

Same encode pipeline, two library sources:

| Source | Preset (example) | On-device size | Notes |
|--------|------------------|---------------|--------|
| Avicii *Levels* FLAC (little/no art) | MP3 VBR q=9 (~low) | ~1.7 MB / ~5:35 | Size tracks quality |
| Same album, instrumental | MP3 VBR q=1 (~high) | ~9.6 MB / ~5:35 | Size tracks quality |
| Christopher Tin *Sogno di Volare* FLAC (large embedded art) | MP3 VBR q=9 | ~17 MB / ~3:53 | Size barely responds to q |
| Same track | “higher” VBR step | ~19 MB / ~3:53 | Still art-dominated |
| Device Track Info bitrate | — | ~785–970 kbps claimed | Did not match size÷duration; tag untrustworthy |

After forcing audio-only convert, the same low-bitrate recipes produce small files and the previously slow track starts promptly.

Repro lab (synthetic FLAC + attached pic → convert without `-map 0:a`):

- Output contained **two** streams: `mp3` + `png`/`mjpeg`.
- With `-map 0:a:0 -vn …`, output is **audio-only** and much smaller.

---

## Root cause

`FFmpeg().input(src).output(dest, codec_opts)` without an explicit map uses ffmpeg’s **default mapping**, which includes:

- Audio streams (wanted)
- Video / attached-picture streams from FLAC/MP4/etc. (unwanted for DAP send)
- Often global metadata that can re-embed huge APIC frames

Encode options (`libmp3lame` `-qscale:a`, `-b:a`, …) only constrain the **audio** stream. They do not strip cover art.

This is easy to misread as “VBR quality flags are broken” because:

- Quality *does* change (transparency drops at high q).
- Size *does not* follow the preset on art-heavy sources.
- Track Info “Bitrate (device tag)” looks like a failed encode.

---

## Fix (do not rebreak)

In `mtpmanager/infra/ffmpeg_transcode.py`, every convert / extract recipe includes **audio-only** output options (`_audio_only_map_options()`):

| Flag | Purpose |
|------|---------|
| `-map 0:a:0` | First audio stream only |
| `-vn` `-sn` `-dn` | No video / subs / data |
| `-map_metadata -1` | Do not copy global metadata/APIC into the temp |
| `-map_chapters -1` | Drop chapter junk |

Tags for MTP send still come from the host library / `TrackMetadata` path, not from stuffing ID3 into the temp file. Device cover art continues via **abstract albums** after sync ([decisions.md](./decisions.md) D15), not via track-embedded pictures.

**Invariant:** `build_ffmpeg_audio_options` / `_legacy_format_options` must always apply audio-only mapping. Do not “simplify” convert back to codec-only kwargs.

**Already-sent bloat:** objects already on the player keep the fat file until deleted and re-synced under the fixed transcoder.

---

## Diagnostics going forward

1. **Device → Track Info…** shows both:
   - **Bitrate (device tag)** — often wrong for VBR / bad MTP property fills  
   - **Bitrate (size÷duration)** — sanity check; if this is ~500+ kbps on a “32 kbps” preset, suspect remux bloat or wrong file  
2. **ffprobe** on a pulled object or on `TRANSCODE_*.mp3` before send: more than one stream → mapping bug regressed.  
3. Compare two encodes of the same FLAC at q=9 vs q=1: sizes should differ a lot for music-only files; if both are multi‑MB and nearly equal, look for non-audio streams or accidental passthrough.

---

## What this is not

| Not the bug | Why |
|-------------|-----|
| LAME q scale inverted | Lab sine encodes and art-light tracks already followed q |
| Device “refusing” low bitrate | Same player plays small Avicii encodes fine |
| Need to strip art from host library FLACs | Hosts may keep art; convert must ignore it |
| Album art sync broken | D15 album samples are a separate, intentional path |

---

## Change surfaces

| Area | Module |
|------|--------|
| Convert / extract options | `infra/ffmpeg_transcode.py` (`_audio_only_map_options`, `build_ffmpeg_audio_options`) |
| Encode presets (unrelated to art, but same dialog) | `domain/audio_encode.py`, Config dialog |
| Track Info size/bitrate display | `ui/formatting.track_metadata_summary` |
| Device cover (correct place for art) | `app/album_art_device.py` |

Tests: `tests/test_audio_encode.py` asserts `map == 0:a:0` and `vn` on built options.

---

## Timeline (short)

1. Audio encode presets land; user tries aggressive VBR on orchestral FLAC with large cover.  
2. Files sound compressed but stay ~17 MB; device bitrate tags absurd; one track ~1 min to start playback.  
3. Side-by-side with art-light dance track shows size *can* track quality.  
4. Lab repro: default map keeps PNG attached pic in MP3.  
5. Fix: audio-only map; re-sync clears device objects; playback latency gone with explanation.
