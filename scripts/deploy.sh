#!/usr/bin/env bash
# Pull-deploy AIOS to a droplet.
#
# GitHub is the source of truth. This NEVER edits the droplet's code directly —
# it fast-forwards /opt/aios from origin/main and reinstalls. The droplet holds a
# read-only deploy key, so it can pull but never push.
#
# `--ff-only` is deliberate: if the droplet has somehow drifted from origin, the
# deploy FAILS LOUDLY rather than silently discarding the drift. That is the exact
# failure mode that nearly lost the predecessor system's code — surface it, never
# destroy it. To investigate a drift: ssh in and `git status` before reconciling.
#
# Usage:
#   scripts/deploy.sh                      # default: the owner's box
#   scripts/deploy.sh root@<other-ip>      # a specific clone
set -euo pipefail

# NO baked-in default host. This previously defaulted to the owner's production droplet, so a
# clone operator running the documented bare `scripts/deploy.sh` shipped their code onto SOMEONE
# ELSE'S box. Resolution order: explicit arg → AIOS_DEPLOY_HOST → untracked .deploy-host file.
HOST="${1:-${AIOS_DEPLOY_HOST:-}}"
if [ -z "$HOST" ] && [ -f "$(dirname "$0")/../.deploy-host" ]; then
  HOST="$(tr -d '[:space:]' < "$(dirname "$0")/../.deploy-host")"
fi
if [ -z "$HOST" ]; then
  echo "✗ no deploy target. Pass one explicitly:" >&2
  echo "    scripts/deploy.sh root@<your-droplet-ip>" >&2
  echo "  or set AIOS_DEPLOY_HOST, or write it to .deploy-host (untracked)." >&2
  exit 1
fi
echo "→ deploying to $HOST"

ssh "$HOST" 'bash -s' < "$(dirname "$0")/box_update.sh"
