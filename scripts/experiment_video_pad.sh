#!/usr/bin/env bash
# Experiment: ZEN-style scale/pad vs SAR-aware variants.
# Usage:
#   ./scripts/experiment_video_pad.sh
#   ./scripts/experiment_video_pad.sh "/path/to/movie.mkv" 320 240
set -euo pipefail

SRC="${1:-/Volumes/video/Movies/I/Iron Will (1994) [tmdb=24767]/Iron Will (1994).mkv}"
W="${2:-320}"
H="${3:-240}"
OUTDIR="${4:-/tmp/mtpmanager_video_pad_exp}"
SECS="${SECS:-20}"   # encode first N seconds (override: SECS=60 ./script …)

mkdir -p "$OUTDIR"

if [[ ! -f "$SRC" ]]; then
  echo "Source not found: $SRC" >&2
  exit 1
fi

echo "=== source ==="
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio,codec_name \
  -of default=noprint_wrappers=1 "$SRC"
echo
echo "Target frame: ${W}x${H}  (first ${SECS}s)  →  $OUTDIR"
echo

# Shared encode bits (ZEN-ish AVI · mpeg4/XVID · MP3)
enc=(
  -t "$SECS"
  -map 0:v:0 -map 0:a:0?
  -c:v mpeg4 -vtag XVID -qscale:v 5
  -c:a libmp3lame -b:a 128k -ac 2 -ar 44100
  -pix_fmt yuv420p
  -f avi
)

run() {
  local name="$1"
  local vf="$2"
  local dest="$OUTDIR/${name}_${W}x${H}.avi"
  echo "--- $name ---"
  echo "vf=$vf"
  ffmpeg -y -hide_banner -loglevel error -stats \
    -i "$SRC" \
    -vf "$vf" \
    "${enc[@]}" \
    "$dest"
  echo "wrote $dest"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio \
    -of default=noprint_wrappers=1 "$dest"
  echo
}

# A) Current MtpManager chain (setsar=1 FIRST — ignores source SAR)
run "A_current_setsar_first" \
  "setsar=1,scale=${W}:${H}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"

# B) Same pad math, but keep SAR until after square-pixel expand
#    iw*sar × ih → square pixels, then fit+pad into WxH
run "B_expand_sar_then_fit" \
  "scale=trunc(iw*sar/2)*2:ih,setsar=1,scale=${W}:${H}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"

# C) Let scale honor DAR/SAR (no leading setsar=1)
run "C_scale_honors_dar" \
  "scale=${W}:${H}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"

# D) Explicit eval=frame on pad (same as A, but forces expression timing)
run "D_pad_eval_frame" \
  "setsar=1,scale=${W}:${H}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:black:eval=frame,setsar=1,format=yuv420p"

# E) Numeric center using Python-precomputed style (no ow/iw exprs) —
#    only valid AFTER scale; here we use pad's expressions still for x/y
#    but named args for clarity
run "E_pad_named_args" \
  "setsar=1,scale=w=${W}:h=${H}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=w=${W}:h=${H}:x=(ow-iw)/2:y=(oh-ih)/2:color=black,setsar=1,format=yuv420p"

echo "=== done ==="
echo "Open the AVIs (or send each via Device → Send Video with Encode off) and compare aspect."
echo "If B or C look correct and A is stretched/squashed, the bug is leading setsar=1 discarding SAR."
ls -la "$OUTDIR"/*_${W}x${H}.avi
