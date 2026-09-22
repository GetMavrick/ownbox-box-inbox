#!/usr/bin/env bash
# image_prepare.sh — turn a droplet into a GOLDEN IMAGE candidate, then prove it carries no identity.
#
#   bash scripts/image_prepare.sh --type customer_voice             # on the droplet, as root, with /opt/aios a
#                                                                  # checkout of its box repository at a release tag
#   bash scripts/image_prepare.sh --verify /opt/aios                # the scrub assertions only
#
# WHAT AN IMAGE IS (docs/PLAN_GOLDEN_IMAGE_CUT.md §2): a bootstrap that stopped after step 2.
# The OS packages, the exported one-machine tree at /opt/aios, its .venv, the unit files installed
# but NOT enabled, Caddy and Litestream present but unconfigured. Nothing else.
#
# WHY THE SCRUB IS A SCRIPT AND NOT A CHECKLIST. Everything bootstrap writes from step 3 onward is
# IDENTITY: the two secrets install mints, the licence, the database, the tenant overlay, the
# hostname in .env, Caddy's certificate and account key, the host keys, the machine-id. A snapshot
# taken one step too late clones one box's identity onto every customer — the same secret signing
# every unsubscribe link, the same bearer opening every /dispatch, one box_id for a fleet. A person
# who remembers eleven files will one day remember ten, so this script deletes them and then
# REFUSES TO FINISH if any of them is still there (--verify). The refusal is the product.
#
# Nothing here deletes anything on a LIVE box: it runs on a throwaway droplet built for the cut,
# and it refuses to run where a database or a licence exists (that is a box, not an image).
set -euo pipefail

AIOS=/opt/aios
TYPE=""; SHA=""; MODE="prepare"
while [ $# -gt 0 ]; do
  case "$1" in
    --type)   TYPE="$2"; shift 2 ;;
    --sha)    SHA="$2";  shift 2 ;;
    --verify) MODE="verify"; AIOS="${2:-$AIOS}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown flag $1" >&2; exit 2 ;;
  esac
done

# ── the scrub list, in one place, used by both the delete and the assertion ────────────────────
# A path here is either identity (it names ONE box) or state (it is one box's life). Two lists
# because the tree's paths are relative to $AIOS and the system's are absolute.
TREE_PATHS=(.env licence.json my data backups wall .x_articles_token.json)
TREE_GLOBS=('*.db' '*.db-wal' '*.db-shm')
SYS_PATHS=(/etc/caddy/Caddyfile /root/.ssh/authorized_keys /root/.bash_history /var/lib/aios/update_key /var/lib/aios/update_key.pub)

# CADDY'S HOME IS EMPTIED, NEVER DELETED. What is inside is one box's (its certificates and its ACME account), but the
# directory belongs to the package: Caddy runs as user caddy, which cannot create it under root's /var/lib.
# Measured 2026-09-15 on image v2, which deleted it: every clone logged "failed storage check: mkdir /var/lib/caddy:
# permission denied", never asked Let's Encrypt at all, and no box built from it ever got a certificate.
CADDY_HOME=${AIOS_CADDY_HOME:-/var/lib/caddy}
CADDY_USER=${AIOS_CADDY_USER:-caddy}

caddy_home_problem() {   # prints why an image's Caddy home would stop a clone getting a certificate; nothing if fine
  local dir="$1" user="$2" owner first
  [ -d "$dir" ] || { echo "$dir is missing, and Caddy (user $user) cannot recreate it: no clone would ever get a certificate"; return; }
  owner=$(stat -c %U "$dir" 2>/dev/null || stat -f %Su "$dir")
  [ "$owner" = "$user" ] || { echo "$dir belongs to $owner, not $user: Caddy could not store a certificate there"; return; }
  first=$(find "$dir" -mindepth 1 -print -quit 2>/dev/null)
  [ -z "$first" ] || echo "Caddy state remains: $first (every clone would share its certificates and ACME account)"
}

verify() {
  local root="$1" bad=0 p
  for p in "${TREE_PATHS[@]}"; do
    [ -e "$root/$p" ] && { echo "  ✗ identity remains: $root/$p"; bad=1; }
  done
  for p in "${TREE_GLOBS[@]}"; do
    local hit
    # NAME THE FILE, never the pattern. "a database remains (*.db)" is a refusal nobody can act
    # on; the path is the whole point, because the next question is always "which one".
    hit=$(find "$root" -name "$p" -print -quit 2>/dev/null)
    [ -n "$hit" ] && { echo "  ✗ a database remains: $hit"; bad=1; }
  done
  # A tree is only half of it. The system half exists only on the droplet, so it is checked when
  # the paths are the real ones — a --verify of an arbitrary directory is the tree check alone.
  if [ "$root" = "/opt/aios" ] && [ -d /etc/systemd/system ]; then
    for p in "${SYS_PATHS[@]}"; do
      [ -e "$p" ] && { echo "  ✗ identity remains: $p"; bad=1; }
    done
    [ -s /etc/machine-id ] && { echo "  ✗ /etc/machine-id is not empty — every clone would share it"; bad=1; }
    if compgen -G "/etc/ssh/ssh_host_*_key" >/dev/null; then
      echo "  ✗ SSH host keys remain — every clone would present the same fingerprint"; bad=1
    fi
    local caddy
    caddy=$(caddy_home_problem "$CADDY_HOME" "$CADDY_USER")
    [ -n "$caddy" ] && { echo "  ✗ $caddy"; bad=1; }
    local on
    on=$(systemctl list-unit-files 'aios-*' --no-legend 2>/dev/null | awk '$2=="enabled"{print $1}' | tr '\n' ' ')
    [ -n "$on" ] && { echo "  ✗ enabled on the image: $on — an image must boot inert"; bad=1; }
  fi
  # MACOS METADATA IS NOT CODE. A tarball built on a Mac without COPYFILE_DISABLE=1 carries an
  # AppleDouble `._<name>` file beside every file — 215 in the Unified Inbox box, 74 of them named
  # `._*.py` under marketing/ and core/. Linux unpacks them as real files, and install.sh's schema
  # proof reads every *.py there and dies on byte 45 (0xa3) of the first one. Measured 2026-09-15 on
  # image #0: both clones failed bootstrap at "5/6 proof", so nothing started on either.
  local apple
  apple=$(find "$root" -name '._*' -type f -print -quit 2>/dev/null)
  [ -n "$apple" ] && { echo "  ✗ macOS metadata files remain (first: $apple) — rebuild the tarball with COPYFILE_DISABLE=1"; bad=1; }
  [ ! -f "$root/scripts/install.sh" ] && { echo "  ✗ no tree at $root — an image without its box is not an image"; bad=1; }
  [ ! -f "$root/trust/allowed_signers" ] && { echo "  ✗ no trust/allowed_signers — a clone could never verify an update, so it could never safely take one"; bad=1; }
  [ ! -x "$root/.venv/bin/python" ] && { echo "  ✗ no .venv at $root — the clone would pay for it on first boot"; bad=1; }
  [ ! -f "$root/image.json" ] && { echo "  ✗ no image.json — a clone must be able to say which image it came from"; bad=1; }
  # THE VENDOR SDK IS CHECKED HERE, WHERE A BAD IMAGE COSTS NOTHING. The box already verifies the Zernio
  # SDK's pin and its real signatures at worker boot, and honours the answer — but that is the CUSTOMER'S
  # first boot. An image baked with a drifted SDK would be snapshotted, pointed at by the provisioner, and
  # then fail on every box built from it, one buyer at a time. Asking the same question at the cut turns
  # that into a build that refuses. Run with the baked venv (the one a clone will actually use) and with
  # verify.py loaded BY PATH, so importing it cannot drag core.config in and freeze settings at import.
  if [ -x "$root/.venv/bin/python" ] && [ -f "$root/core/vendors/zernio/verify.py" ]; then
    local sdk
    sdk=$("$root/.venv/bin/python" - "$root/core/vendors/zernio/verify.py" <<'SDKPY' 2>&1
import importlib.util, sys
spec = importlib.util.spec_from_file_location("_zv", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ok_, detail = m.verify_sdk()
print(detail)
sys.exit(0 if ok_ else 1)
SDKPY
)
    # shellcheck disable=SC2181
    if [ $? -ne 0 ]; then
      echo "  ✗ the baked SDK is not the pinned one: $sdk — every clone of this image would fail at boot"; bad=1
    else
      echo "  ✓ $sdk"
    fi
  fi
  # ── AN IMAGE IS A SIGNED CHECKOUT OF ITS BOX REPOSITORY (image v2) ─────────────────────────────
  # Image #1 was an exported tarball: it booted, and it could never take an update, because /deploy
  # has nothing to fetch into a tree with no repository. A sold box now updates the way this box does
  # (scripts/box_update.sh verifies every release), so the image IS that starting point: a checkout of
  # its type's box repository, at a release tag its own trust file accepts, with no credential inside.
  if [ ! -e "$root/.git" ]; then
    echo "  ✗ $root is not a checkout of its box repository — a clone could never take an update"; bad=1
  else
    local urls origin shown tag verdict
    urls=$(git -C "$root" config --get-regexp '^remote\..*\.url$' 2>/dev/null | awk '{print $2}')
    if printf '%s\n' "$urls" | grep -Eq '^[a-z+]+://[^/]*@'; then
      echo "  ✗ a remote URL carries a credential — every clone would hold it"; bad=1
    fi
    origin=$(git -C "$root" remote get-url origin 2>/dev/null) || origin=""
    # Shown with any credential masked: a refusal is read in a terminal, a log and a chat.
    shown=$(printf '%s' "${origin:-missing}" | sed -E 's#(://)[^/@]*@#\1***@#')
    printf '%s' "$origin" | grep -Eq '^(git@github\.com:|https://github\.com/)GetMavrick/ownbox-box-[a-z0-9-]+(\.git)?$' \
      || { echo "  ✗ origin is $shown, not a box repository — a sold box never follows the monorepo"; bad=1; }
    tag=$(git -C "$root" describe --tags --exact-match --match 'release/*' HEAD 2>/dev/null) || tag=""
    if [ -z "$tag" ]; then
      echo "  ✗ HEAD is not on a release tag — an image is cut from a signed release, never from a branch"; bad=1
    elif ! verdict=$(cd "$root" && "${IMAGE_PYTHON:-python3}" -m core.release.verify "$root" "$tag" \
              --trust "$root/trust/allowed_signers" 2>&1); then
      echo "  ✗ $tag does not verify against this image's own trust file: $(printf '%s' "$verdict" | tail -1 | cut -c1-200)"; bad=1
    fi
    [ -n "$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null)" ] \
      && { echo "  ✗ tracked files were edited after checkout — the image would not be the release it names"; bad=1; }
  fi
  return $bad
}

if [ "$MODE" = "verify" ]; then
  echo "== verifying $AIOS as an image =="
  if verify "$AIOS"; then echo "  ✓ no identity, no state, nothing enabled — this is an image"; exit 0
  else echo "REFUSED: this is a box, not an image (above)"; exit 1; fi
fi

# ── prepare ───────────────────────────────────────────────────────────────────────────────────
[ "$(id -u)" = 0 ] || { echo "✗ run as root on the image droplet" >&2; exit 1; }
[ -n "$TYPE" ] || { echo "✗ --type is required (lead | content | customer_voice | aios)" >&2; exit 1; }
[ -f "$AIOS/scripts/install.sh" ] || { echo "✗ no tree at $AIOS — clone the box repository there first" >&2; exit 1; }
RELEASE=$(git -C "$AIOS" describe --tags --exact-match --match 'release/*' HEAD 2>/dev/null) \
  || { echo "✗ $AIOS is not a checkout at a release tag — check out the release this image is cut from" >&2; exit 1; }
SHA=${SHA:-$(git -C "$AIOS" rev-parse --short=8 HEAD)}
# AN IMAGE IS NEVER CUT FROM A BOX. If a licence or a database is here, somebody pointed this at a
# real box; deleting its database to make an image is exactly the accident this refuses to have.
for guard in licence.json aios.db; do
  [ -e "$AIOS/$guard" ] && { echo "✗ $AIOS/$guard exists — this is a live box. Refusing." >&2; exit 1; }
done

cd "$AIOS"
echo "== 1/5 system packages (bootstrap step 1, verbatim) =="
export DEBIAN_FRONTEND=noninteractive
MEDIA="ffmpeg"
[ -d marketing ] && [ ! -d marketing/content_machine ] && { MEDIA=""; echo "   (no Content Machine — skipping ffmpeg)"; }
apt-get -o DPkg::Lock::Timeout=600 update -q
# shellcheck disable=SC2086
apt-get -o DPkg::Lock::Timeout=600 install -y -q python3.12 python3.12-venv git sqlite3 $MEDIA curl
command -v caddy >/dev/null 2>&1 || apt-get install -y -qq caddy   # binary only; expose.sh configures it on the clone

echo "== 2/5 virtual environment (install.sh step 1, and ONLY step 1) =="
[ -d .venv ] || python3.12 -m venv .venv
# Every dependency hash-checked, exactly as install.sh and box_update.sh install it (requirements.lock).
.venv/bin/python -m pip install -q --require-hashes -r requirements.lock
.venv/bin/python -m pip install -q --no-deps --no-build-isolation -e .

echo "== 3/5 unit files, installed and DISABLED =="
# install_services.sh cannot be reused here: it mints .env and enables everything, which is the
# identity step. The file list is the same one, and tests/test_image_prepare.py asserts it stays
# the same — a unit added there and forgotten here would be missing from every image.
for u in deploy/aios-*.service deploy/aios-*.timer; do
  [ -e "$u" ] && install -m 644 "$u" /etc/systemd/system/
done
systemctl daemon-reload
bash scripts/install_litestream.sh || true   # binary + unit; stays disabled with no .env to read
# THE CLAUDE CLI, BAKED. A buyer may connect an OAuth token instead of an API key — the owner ruled
# that choice is the CLIENT's (2026-09-18) — and #1380 routes an `sk-ant-oat` credential to the
# claude_code backend. That backend shells out to `claude`, so a box without the binary stores the
# token, reports the CLI missing and drafts NOTHING: the one failure a buyer reads as "it does not
# work". Baked here rather than fetched at first boot so a customer's box never waits on
# claude.ai being up at the moment they are watching it start.
bash scripts/install_claude_code.sh || true   # idempotent; a failure must not stop an image cut

echo "== 4/5 the stamp =="
cat > image.json <<JSON
{"box_type": "$TYPE", "release": "$RELEASE", "sha": "$SHA", "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
 "image": "aios-$TYPE-${RELEASE#release/}", "prepared_by": "scripts/image_prepare.sh"}
JSON
chmod 644 image.json
# THE RELEASE THIS IMAGE IS. box_update.sh writes this marker after every verified install; an image is
# the first install, and without the marker a clone's first update has no downgrade memory until HEAD
# is re-read (core/release/update.installed_release).
mkdir -p /var/lib/aios && printf '%s\n' "$RELEASE" > /var/lib/aios/release

echo "== 5/5 scrub =="
for p in "${TREE_PATHS[@]}"; do rm -rf "${AIOS:?}/$p"; done
find "$AIOS" -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' | while read -r f; do rm -f "$f"; done
find "$AIOS" -name '._*' -type f -delete               # AppleDouble files from a Mac-built tarball (see verify)
rm -rf "${SYS_PATHS[@]}"
[ -d "$CADDY_HOME" ] && find "$CADDY_HOME" -mindepth 1 -delete          # its certificates and account, dotfiles too
id "$CADDY_USER" >/dev/null 2>&1 && install -d -o "$CADDY_USER" -g "$CADDY_USER" -m 0700 "$CADDY_HOME"
rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
: > /etc/machine-id                                   # regenerated on first boot; dbus follows it
rm -f /var/lib/dbus/machine-id
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
journalctl --rotate >/dev/null 2>&1 || true; journalctl --vacuum-time=1s >/dev/null 2>&1 || true
find /root /home -name '.bash_history' -delete 2>/dev/null || true
# cloud-init must run again on the clone — that is what injects the clone's SSH key and hostname.
cloud-init clean --logs >/dev/null 2>&1 || true

echo "== verify =="
if verify "$AIOS"; then
  echo "  ✓ no identity, no state, nothing enabled"
  echo
  echo "IMAGE READY — power off, then snapshot:"
  echo "  doctl compute droplet-action shutdown <id> --wait"
  echo "  doctl compute droplet-action snapshot <id> --snapshot-name aios-$TYPE-${RELEASE#release/} --wait"
  echo "Then prove it: python scripts/image_clone_check.py --image <snapshot-id> --type $TYPE"
else
  echo "REFUSED: the scrub did not finish (above). Do NOT snapshot this droplet." >&2
  exit 1
fi
