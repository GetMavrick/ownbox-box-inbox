#!/usr/bin/env bash
# Provision the reel-render toolchain on the AIOS box (the $12 droplet — no resize).
# Idempotent; run as root on the box (or: ssh root@<box> 'bash -s' < this file).
#
# Installs exactly what the measured strategy needs:
#   2 GB swap (OOM insurance — render peaks 0.37 GB, this is belt-and-suspenders)
#   FFmpeg + ffprobe, Chromium system libs (HyperFrames captures via headless Chrome)
#   Node 22 via nvm (root-local)
#   the scaffolded HyperFrames workspace at <db_dir>/renders/.studio
#   pip ".[render]" → faster-whisper tiny.en (caption timing anchors)
#
# Lesson encoded from the spike: apt installs are SPLIT per concern — one giant
# all-or-nothing apt line silently skipped ffmpeg when a single package name
# (libasound2 vs libasound2t64) differed.
set -uo pipefail
APP=/opt/aios

echo "== swap (2 GB, free) =="
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
free -h | awk '/Swap:/{print "  swap: "$2}'

echo "== apt: ffmpeg + python venv tooling =="
apt-get update -qq
apt-get install -y -qq ffmpeg python3-venv time || { echo "ERROR: ffmpeg install failed"; exit 1; }

echo "== apt: Chromium system libs (split — names vary across releases) =="
apt-get install -y -qq libnss3 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 fonts-liberation
apt-get install -y -qq libasound2t64 2>/dev/null || apt-get install -y -qq libasound2

echo "== node 22 (nvm, root-local) =="
export NVM_DIR=/root/.nvm
[ -s "$NVM_DIR/nvm.sh" ] || curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash >/dev/null 2>&1
. "$NVM_DIR/nvm.sh"
nvm install 22 >/dev/null 2>&1 && nvm alias default 22 >/dev/null 2>&1
# systemd units don't read .bashrc — give the worker a stable node path:
ln -sf "$(dirname "$(nvm which 22)")/node" /usr/local/bin/node
ln -sf "$(dirname "$(nvm which 22)")/npx"  /usr/local/bin/npx
ln -sf "$(dirname "$(nvm which 22)")/npm"  /usr/local/bin/npm
echo "  node: $(/usr/local/bin/node -v)"

echo "== HyperFrames render workspace =="
# PINNED — hyperframes is pre-1.0 and its API can move (spec §7.3). The measured
# $12-box numbers are only valid for the version actually rendered with; bump this
# pin deliberately, re-measuring a render after. (Spike measured @latest, which
# resolved to 0.6.x at the time.)
HYPERFRAMES_VERSION="${HYPERFRAMES_VERSION:-0.6.88}"
STUDIO="$APP/renders/.studio"
if [ ! -f "$STUDIO/hyperframes.json" ]; then
  mkdir -p "$APP/renders"
  cd "$APP/renders"
  /usr/local/bin/npx --yes "hyperframes@${HYPERFRAMES_VERSION}" init .studio >/dev/null 2>&1
fi
[ -f "$STUDIO/hyperframes.json" ] && echo "  workspace: $STUDIO ✓ (hyperframes@${HYPERFRAMES_VERSION})" || { echo "ERROR: workspace scaffold failed"; exit 1; }
cd "$STUDIO" && /usr/local/bin/npx "hyperframes@${HYPERFRAMES_VERSION}" lint >/dev/null 2>&1 && echo "  lint: ok" || echo "  lint: reported issues (inspect later)"

# Vendor GSAP into the workspace (pinned) — renders must not depend on a CDN at
# render time. hyperframes_render.py self-heals if this is ever missing.
GSAP_VERSION="3.14.2"
if [ ! -s "$STUDIO/gsap.min.js" ]; then
  curl -fsSL "https://cdn.jsdelivr.net/npm/gsap@${GSAP_VERSION}/dist/gsap.min.js" \
    -o "$STUDIO/gsap.min.js"
fi
[ -s "$STUDIO/gsap.min.js" ] && echo "  gsap ${GSAP_VERSION}: vendored ✓" || { echo "ERROR: gsap fetch failed"; exit 1; }

echo "== python render extras (faster-whisper tiny.en) =="
cd "$APP"
.venv/bin/pip install -q -e ".[render]" && echo "  faster-whisper: installed"
# Pre-download the tiny.en model so the first real render doesn't pay the fetch:
.venv/bin/python - <<'PY' || echo "  (model prefetch skipped — will fetch on first render)"
from faster_whisper import WhisperModel
WhisperModel("tiny.en", device="cpu", compute_type="int8")
print("  tiny.en model: cached")
PY

echo ""
echo "PROVISIONED. Render strategy: composite path, sequential, ~6.5 min per 45s reel,"
echo "peak RAM ~0.4 GB — measured on this hardware class. No resize needed, ever."
