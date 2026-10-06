#!/usr/bin/env bash
# Pull Zernio's unified-inbox at one commit into web/inbox-ui/upstream/, unmodified (docs/PLAN_INBOX_MIRRORS_ZERNIO.md,
# #1990: "pulling their next commit is one script").
#
#   scripts/inbox_ui_pull.sh <commit>
#
# Replaces upstream/ with their tree at <commit> and rewrites UPSTREAM.lock (every file with its git blob sha). Then:
# scripts/inbox_ui_build.sh, check that every patch in web/inbox-ui/patches still applies, look at the screens at 390
# before desktop, and commit upstream/, the lock and the build together.
set -euo pipefail
COMMIT="${1:?usage: scripts/inbox_ui_pull.sh <commit>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI="$ROOT/web/inbox-ui"
REPO="https://github.com/zernio-dev/unified-inbox"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone -q "$REPO" "$WORK/src"
FULL="$(git -C "$WORK/src" rev-parse --verify "$COMMIT^{commit}")"
rm -rf "$UI/upstream"
mkdir -p "$UI/upstream"
git -C "$WORK/src" archive "$FULL" | tar -x -C "$UI/upstream"
{
  echo "# zernio-dev/unified-inbox, vendored UNMODIFIED (docs/PLAN_INBOX_MIRRORS_ZERNIO.md, #1990 1.1)."
  echo "# repo: $REPO"
  echo "# commit: $FULL"
  echo "# Every file below is upstream/<path>, with the git blob sha it has at that commit. tests/test_inbox_ui_upstream.py"
  echo "# holds upstream/ to exactly this list. Change it only with scripts/inbox_ui_pull.sh <commit>."
  git -C "$WORK/src" ls-tree -r "$FULL" | awk '{print $3"  "$4}'
} > "$UI/UPSTREAM.lock"
echo "✓ web/inbox-ui/upstream/ is $REPO at ${FULL:0:8}. Now: scripts/inbox_ui_build.sh"
