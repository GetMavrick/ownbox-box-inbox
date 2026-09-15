#!/bin/bash
# devstate-post.sh — append a message to the shared wall (DEVSTATE.md) SAFELY.
#
# Two sessions hand-editing one file stomp each other: read → read → write → write loses a
# post, silently. This serializes with an atomic lock, prepends newest-first, enforces the
# grammar and the cap, and prunes to the newest N. It WRITES A FILE and stops — committing
# and pushing is yours.
#
#   bash .claude/scripts/devstate-post.sh "Dev1" "[Dev1→Dev2] GOTCHA: the API wants url, not company"
#   ROLE=Dev2 bash .claude/scripts/devstate-post.sh "[Dev2→ALL] SHIPPED: importer live"
set -euo pipefail
AIOS_DIR="${AIOS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
WALL="$AIOS_DIR/DEVSTATE.md"
KEEP="${DEVSTATE_KEEP:-12}"
CAP="${DEVSTATE_CAP:-700}"

if [ $# -ge 2 ]; then ROLE="$1"; MSG="$2"; else ROLE="${ROLE:?set ROLE or pass it as the first argument}"; MSG="${1:?message required}"; fi
[ -f "$WALL" ] || { echo "✗ no wall at $WALL"; exit 1; }

# GRAMMAR: the arrow and the TYPE are what make the wall scannable. Rejected, not corrected —
# a post that does not say who it is for is usually a post nobody reads.
grep -qE '^\[[A-Za-z0-9_]+→[A-Za-z0-9_|]+\] (ASSIGNED|BLOCKED|SHIPPED|FACT|GOTCHA|OWNER|FYI):' <<<"$MSG" || {
  echo "✗ grammar: need '[FROM→TO] TYPE: msg' with TYPE ∈ ASSIGNED|BLOCKED|SHIPPED|FACT|GOTCHA|OWNER|FYI"; exit 1; }
[ "${#MSG}" -le "$CAP" ] || { echo "✗ ${#MSG} chars, cap is $CAP. Detail belongs in a doc or the PR body."; exit 1; }

LOCK="/tmp/.devstate-post.lock"
for _ in $(seq 1 50); do mkdir "$LOCK" 2>/dev/null && break || sleep 0.2; done
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

ENTRY="[$ROLE] $(date -u '+%H:%M UTC (%b %d)'): $MSG"
python3 - "$WALL" "$ENTRY" "$KEEP" <<'PY'
import sys, re
wall, entry, keep = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = open(wall).read()
head, sep, body = s.partition("## Messages\n")
if not sep:
    print("✗ no '## Messages' section"); sys.exit(1)
posts = [p for p in body.split("\n") if p.startswith("[")]
rest  = [p for p in body.split("\n") if not p.startswith("[")]
posts = [entry] + posts
open(wall, "w").write(head + sep + "\n".join(rest[:1] + posts[:keep]) + "\n")
print(f"✓ posted to {wall} ({len(posts[:keep])} posts on the wall)")
PY
echo "  now commit it:  git add DEVSTATE.md && git commit -m 'wall: $ROLE' && git push"
