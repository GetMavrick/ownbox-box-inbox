#!/usr/bin/env bash
# One-shot backfill: remux every EXISTING render to +faststart (stream copy —
# seconds per file, no re-encode, atomic replace). New downloads and finals
# already get faststart at produce time (PR #43); this fixes the BACKLOG so
# pre-fix reels stream instantly too instead of downloading fully first.
# Safe to re-run (a faststart file remuxes to itself). Run ON the box:
#
#   bash /opt/aios/scripts/remux_faststart_backfill.sh
set -euo pipefail
DIR="${1:-/opt/aios/renders}"
command -v ffmpeg >/dev/null || { echo "ffmpeg not installed"; exit 1; }
shopt -s nullglob
n=0
for f in "$DIR"/*.mp4; do
  tmp="$f.fs.mp4"
  if ffmpeg -y -v error -i "$f" -c copy -movflags +faststart "$tmp" 2>/dev/null \
     && [ -s "$tmp" ]; then
    mv "$tmp" "$f"; echo "remuxed: $(basename "$f")"; n=$((n+1))
  else
    rm -f "$tmp"; echo "skipped (not remuxable): $(basename "$f")"
  fi
done
echo "done — $n file(s) remuxed to faststart"
