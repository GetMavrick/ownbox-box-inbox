#!/usr/bin/env bash
# box_update_key.sh — give a sold box its OWN read-only key to its box repository, and pin who it fetches from.
#
#   bash scripts/box_update_key.sh        # as root, from bootstrap.sh; idempotent
#
# WHY A KEY PER BOX (docs/PLAN_GOLDEN_IMAGE_CUT.md §3.1d S6). A box fetches releases from its type's
# private repository. One key shared by every box would be a secret inside every image and on every
# customer's disk, and cutting off one box would cut off all of them. So each box mints its own on first
# boot. The private half never leaves this machine; Ownbox registers only the PUBLIC half, read-only, on
# that one repository (the provisioner reads it from /health).
#
# WHO THE BOX TALKS TO. Fetches are pinned to the GitHub host keys in trust/github_known_hosts, which
# arrive inside a signed release like every other file, with StrictHostKeyChecking=yes: the box never
# trusts a first answer from whatever answers as github.com.
#
# ONLY ON A BOX THAT FETCHES FROM A BOX REPOSITORY. A checkout of the monorepo (our own box, a developer
# clone, an unpacked archive) keeps whatever access it already has; this says so and changes nothing.
set -euo pipefail

AIOS="${AIOS_DIR:-/opt/aios}"
STATE="${AIOS_STATE_DIR:-/var/lib/aios}"
KEY="$STATE/update_key"
HOSTS="$AIOS/trust/github_known_hosts"

origin=$(git -C "$AIOS" remote get-url origin 2>/dev/null) || origin=""
if ! printf '%s' "$origin" | grep -Eq '^(git@github\.com:|https://github\.com/)GetMavrick/ownbox-box-[a-z0-9-]+(\.git)?$'; then
  echo "   update key: origin is not a box repository (${origin:-none}), nothing to do"
  exit 0
fi
grep -q '^github\.com ssh-ed25519 ' "$HOSTS" 2>/dev/null \
  || { echo "✗ update key: no GitHub host key in $HOSTS, so this box could not check who it fetches from" >&2; exit 1; }

mkdir -p "$STATE"
if [ -f "$KEY" ]; then
  echo "   update key: present (never replaced: its public half is what the box repository trusts)"
else
  ( umask 077; ssh-keygen -q -t ed25519 -N "" -C "ownbox-update" -f "$KEY" )
  echo "   update key: generated"
fi
chmod 600 "$KEY"
chmod 644 "$KEY.pub"
git -C "$AIOS" config core.sshCommand \
  "ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$HOSTS -o StrictHostKeyChecking=yes -o BatchMode=yes"
echo "   update key: fetches from $origin use it, pinned to $HOSTS"
