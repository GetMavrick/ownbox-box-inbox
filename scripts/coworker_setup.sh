#!/usr/bin/env bash
# MAKE THIS BOX ABLE TO RUN COWORKERS (docs/SCOPE_SHIFTS.md §4.1). Idempotent: bootstrap runs it on a
# new box and box_update.sh runs it on every update, so boxes already sold get it without a new image.
#
#   1. the aios-shift user a coworker runs as (no login, no home, no keys)
#   2. the mount point its workspace appears at inside its locked unit
#   3. the AI CLI copied OUTSIDE /root. The native installer puts it in /root/.local/share/claude/
#      versions/<v>, and behind ProtectHome aios-shift can't reach that. Measured 2026-09-26 on a
#      box from the live image: `claude --version` as aios-shift printed nothing and exited, which
#      is a coworker that starts on time and silently does no work (core/coworkers/sandbox.py).
#   4. the one-minute tick. It does nothing on a box with no coworker (the service's Conditions
#      stop it before any process starts), so enabling it everywhere costs nothing.
#
# Prints one line per step and exits 0 unless the box itself is broken; box_update.sh treats a
# failure here as "coworkers can't run yet", never as a failed update.
set -euo pipefail
AIOS="${AIOS_ROOT:-/opt/aios}"
CLI_DIR=/usr/local/lib/claude-code

# 1. the user
if id -u aios-shift >/dev/null 2>&1; then echo "coworker user: present"
else useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin aios-shift
     echo "coworker user: created"; fi

# 2. where the workspace appears inside the unit, and the coworkers' home in my/ (updates never touch my/)
install -d -m 755 -o root -g root /var/lib/aios-shift /var/lib/aios-shift/work /var/lib/aios-shift/run
install -d -m 755 "$AIOS/my/coworkers"
for w in "$AIOS"/my/coworkers/*/workspace; do
  [ -d "$w" ] && chown -R aios-shift:aios-shift "$w"
done
echo "coworker workspaces: ready"

# 3. the CLI outside /root. The source is the pinned install (scripts/install_claude_code.sh). A copy
#    is made per version, so a version bump lands as a new copy and the link moves to it.
src=""
[ -x /root/.local/bin/claude ] && src=$(readlink -f /root/.local/bin/claude || true)
[ -z "$src" ] && src=$(readlink -f /usr/local/bin/claude 2>/dev/null || true)
case "$src" in
  /root/*)
    v=$(basename "$src")
    if [ ! -x "$CLI_DIR/$v" ]; then
      install -d -m 755 "$CLI_DIR"
      install -m 755 "$src" "$CLI_DIR/$v.tmp" && mv -f "$CLI_DIR/$v.tmp" "$CLI_DIR/$v"
    fi
    ln -sfn "$CLI_DIR/$v" /usr/local/bin/claude
    echo "AI CLI: $CLI_DIR/$v" ;;
  "") echo "AI CLI: not installed; coworkers report FAILED until it is" ;;
  *)  echo "AI CLI: $src (outside /root already)" ;;
esac

# 4. the tick. A changed unit is backed up before it is replaced, as box_update.sh does.
changed=0
for n in aios-shifts.service aios-shifts.timer; do
  f="$AIOS/deploy/$n"; t="/etc/systemd/system/$n"
  [ -f "$f" ] || { echo "tick: $n missing from this release; skipped"; continue; }
  cmp -s "$f" "$t" 2>/dev/null && continue
  if [ -f "$t" ]; then
    b="/root/aios-units-backup-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$b"; cp -a "$t" "$b/"
  fi
  cp "$f" "$t"; changed=$((changed+1))
done
[ "$changed" -gt 0 ] && systemctl daemon-reload
if [ -f /etc/systemd/system/aios-shifts.timer ]; then
  systemctl enable --now aios-shifts.timer >/dev/null 2>&1 && echo "tick: enabled" \
    || echo "tick: could not be enabled"
fi
