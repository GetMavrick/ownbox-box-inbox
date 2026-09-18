#!/usr/bin/env bash
# AIOS bootstrap — bare Ubuntu 24.04 → a running clone (W2.4; the script README.md
# and CLAUDE.md always pointed at but which never existed — audit C8).
#
#   As root on a fresh droplet:
#     curl -fsSL https://raw.githubusercontent.com/GetMavrick/AIOS/main/scripts/bootstrap.sh | bash -s -- <git-clone-url>
#   or, with the repo already cloned to /opt/aios:
#     bash /opt/aios/scripts/bootstrap.sh
#
#   ONBOARDING SOMEONE ELSE'S BOX — one line, no questions, and it prints the handover card:
#     bash /opt/aios/scripts/bootstrap.sh --buyer "Acme Roofing" --order gift-01 --host acme.nlvl.co
#
#   Getting the archive onto a fresh VPS (a stock Ubuntu image has tar but NOT unzip — use the .tar.gz):
#     scp lead-machine-<version>.tar.gz root@<ip>:
#     ssh root@<ip> 'mkdir -p /opt && tar -xzf lead-machine-<version>.tar.gz -C /opt'    # lands at /opt/aios
#   then the bootstrap line above. Run it straight after "create droplet" — bootstrap waits for
#   the image's first-boot apt itself.
#
#   --host is a subdomain of nlvl.co (owner, 2026-09-05): <client>.nlvl.co for a sold box, <industry>.nlvl.co
#   (health-wellness.nlvl.co) for one of our own demo apps. Make the record FIRST, from your machine, never
#   from this box:  python scripts/dns_add.py acme <this droplet's IP>   — the Cloudflare token stays with you.
#
# Idempotent: re-running upgrades packages, refreshes the venv, and re-installs units.
# It STOPS before starting services when .env is missing — filling credentials is a
# human step by design (see .env.example, every var documented).
set -euo pipefail

AIOS=/opt/aios
REPO_URL=""; BUYER=""; ORDER=""; HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --buyer) BUYER="$2"; shift 2 ;;
    --order) ORDER="$2"; shift 2 ;;
    --host)  HOST="$2";  shift 2 ;;      # the public hostname this box will answer on
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) REPO_URL="$1"; shift ;;
  esac
done

echo "== 1/6 system packages (python3.12, git, sqlite3, curl; ffmpeg only with the Content Machine) =="
export DEBIAN_FRONTEND=noninteractive
# A FRESH IMAGE IS STILL INSTALLING ITSELF. On a new DigitalOcean/Ubuntu droplet, cloud-init and
# unattended-upgrades hold the dpkg lock for the first minutes after boot; run this line right
# after "create droplet" and apt-get dies with "Could not get lock /var/lib/dpkg/lock-frontend"
# and nothing is installed (measured 2026-09-05 on a throwaway droplet — the owner's exact path).
# Wait for the image's own apt to finish, then ask apt to wait on the lock too.
wait_for_apt() {
  local waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1; do
    [ "$waited" -eq 0 ] && echo "== waiting for the image's first-boot apt to finish (up to 10 min) =="
    sleep 5; waited=$((waited + 5))
    [ "$waited" -ge 600 ] && { echo "✗ apt is still locked after 10 minutes — check: ps aux | grep apt" >&2; return 1; }
  done
  return 0
}
wait_for_apt
apt-get -o DPkg::Lock::Timeout=600 update -q
# ffmpeg is the Content Machine's renderer and drags the desktop library chain (gtk, icon
# themes, x11, alsa — measured 2026-09-06 on a throwaway droplet) onto a 1GB box. A Lead box
# never renders, so it only pays for ffmpeg when the tree beside this script ships the
# Content Machine. With no tree yet (clone mode) we cannot know, so install it — a missing
# renderer is a worse surprise than a spare one.
ROOT_HERE="$(cd "$(dirname "$0")/.." && pwd)"
MEDIA="ffmpeg"
if [ -d "$ROOT_HERE/marketing" ] && [ ! -d "$ROOT_HERE/marketing/content_machine" ]; then
  MEDIA=""; echo "   (no Content Machine in this box — skipping ffmpeg)"
fi
# shellcheck disable=SC2086 — MEDIA is one word or empty, on purpose
apt-get -o DPkg::Lock::Timeout=600 install -y -q python3.12 python3.12-venv git sqlite3 $MEDIA curl

echo "== 2/6 repo =="
# THE BOX IS THE TREE. The source used to decide "is this a clone?" by .git and the exporter
# rewrote that test into every archive so a tarball would pass; the two copies disagreed, and a
# repo bootstrap dropped onto an archive refused it after installing every package (measured
# 2026-09-05 on a throwaway droplet). One rule now, in the source: a tree with scripts/install.sh
# is complete, clone or archive; a clone URL is only needed when nothing is there at all.
if [ -f "$AIOS/scripts/install.sh" ]; then
  echo "   using the tree at $AIOS (a clone or an unpacked archive — both are complete)"
elif [ -n "$REPO_URL" ]; then
  git clone "$REPO_URL" "$AIOS"
else
  echo "✗ nothing at $AIOS. Unpack the archive there (tar -xzf <box>.tar.gz -C /opt lands at /opt/aios) or pass a <git-clone-url>." >&2
  exit 1
fi
cd "$AIOS"
# A TARBALL BUILT ON A MAC carries an AppleDouble `._<name>` beside every file unless it was made with
# COPYFILE_DISABLE=1. They are metadata, never content, and install.sh's schema proof dies reading
# the `._*.py` ones — so a hand-built handover archive would fail at "5/6 proof" with nothing started
# (measured on the first golden-image clones, 2026-09-15). Removed here so the box still comes up.
_apple=$(find "$AIOS" -name '._*' -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$_apple" -gt 0 ]; then
  find "$AIOS" -name '._*' -type f -delete
  echo "   removed $_apple macOS metadata files (._*) from the archive — build with COPYFILE_DISABLE=1"
fi

echo "== 3/6 install (venv, .env, secrets, licence, database) =="
# ONE INSTALL PATH. This used to do its own venv, copy .env.example and stop — which meant it
# never minted DISPATCH_BEARER_TOKEN or UNSUB_SIGNING_KEY, so every box brought up this way had
# a dead /dispatch, a dashboard login that refused every password, and a send path that refused
# every send. install.sh has minted both since #748 and this script never called it, so the
# defect stayed alive in the path an operator actually uses on a VPS — the same shape as the
# README bug (#758). A fix inside a script nobody runs is not a fix.
INSTALL_ARGS=()
[ -n "$BUYER" ] && INSTALL_ARGS+=(--buyer "$BUYER")
[ -n "$ORDER" ] && INSTALL_ARGS+=(--order "$ORDER")
bash scripts/install.sh "${INSTALL_ARGS[@]}"
# A BOX THAT FETCHES RELEASES FROM ITS BOX REPOSITORY gets its own read-only update key here, with
# GitHub's host keys pinned (scripts/box_update_key.sh). Any other checkout is left as it is.
bash scripts/box_update_key.sh
# A BOX BUILT WITH A CONNECTOR takes its own scoped key out of provision.json and into .env. Python, not
# shell: the key is read from a file and written to a file, never passed as an argument. Silent and green
# on every box that has no connector block, which is every box the owner installs by hand.
python3 scripts/connector_handoff.py || echo "   connector handoff failed; the box works, Instagram waits"


echo "== 4/6 the box's public address =="
# Sending refuses without an https base (every email carries an unsubscribe link built from it)
# and the dash login rides the same host. Set it here rather than leaving it for a later
# surprise; --host is the only per-client value bootstrap needs that install.sh cannot guess.
if [ -n "$HOST" ]; then
  if grep -q '^DASHBOARD_BASE_URL=' .env; then
    sed -i -E "s|^DASHBOARD_BASE_URL=.*|DASHBOARD_BASE_URL=https://$HOST|" .env
  else
    printf 'DASHBOARD_BASE_URL=%s\n' "https://$HOST" >> .env
  fi
  echo "   DASHBOARD_BASE_URL=https://$HOST"
else
  echo "   no --host given; set DASHBOARD_BASE_URL in .env before this box can send"
fi

echo "== 5/6 services =="
bash scripts/install_services.sh

# THE ADDRESS ON THE HANDOVER CARD MUST ANSWER. This used to stop at the systemd units: gunicorn
# on 127.0.0.1, deploy/Caddyfile shipped as a file, and the card printed https://<host> "(Caddy
# issues the certificate)" — while nothing listened publicly until someone ran scripts/expose.sh
# by hand. Measured 2026-09-05 on a throwaway droplet: bootstrap green, host answered nothing.
# expose.sh is idempotent (installs Caddy, renders the Caddyfile for the host, enables it, sets
# DASHBOARD_BASE_URL, restarts the units); with --host, bootstrap runs it.
if [ -n "$HOST" ]; then
  echo "== 5b/6 public edge (Caddy + auto-HTTPS for $HOST) =="
  bash scripts/expose.sh "$HOST" || echo "   ✗ expose.sh failed — the address on the card will not answer until it succeeds (journalctl -u caddy -n 30)"
fi

echo "== 6/6 durability (off-box DB replication) =="
bash scripts/install_litestream.sh || true

# AND THE CLAUDE CLI, IF THE IMAGE DID NOT CARRY IT. Boxes built from an image cut before 2026-09-18
# have no `claude` binary, and bootstrap is the only place that runs again for them. Idempotent: on a
# baked image this finds the binary and does nothing. `|| true` because a box that cannot reach
# claude.ai must still finish booting — it simply cannot draft until the binary arrives.
bash scripts/install_claude_code.sh || true

echo ""
echo "== bootstrap complete — final gate =="
"$AIOS/.venv/bin/python" scripts/doctor.py || true
echo ""
echo "══ HANDOVER ══════════════════════════════════════════════"
# What the person receiving this box needs, in one place, at the moment it exists. Onboarding
# ten of these by hand is ten chances to forget which password went where.
echo "  box:        $AIOS on $(hostname)"
[ -n "$BUYER" ] && echo "  licensed:   $BUYER${ORDER:+  (order $ORDER)}"
if [ -n "$HOST" ]; then
  echo "  address:    https://$HOST     (point DNS here, then Caddy issues the certificate)"
fi
_tok=$(grep -E '^DASH_TOKEN=.+' .env | cut -d= -f2- || true)
[ -n "$_tok" ] || _tok=$(grep -E '^DISPATCH_BEARER_TOKEN=.+' .env | cut -d= -f2- || true)
if [ -n "$_tok" ]; then
  echo "  password:   $_tok"
  echo "              ↑ give this to them over something private, then consider setting a"
  echo "                separate DASH_TOKEN so the dashboard password is not the box's API key."
fi
echo "  still off:  every timer. Nothing sends, posts or spends until it is armed."
echo "              .venv/bin/python scripts/doctor.py   # what this box still needs"
echo "══════════════════════════════════════════════════════════"
