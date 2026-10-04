#!/usr/bin/env bash
# checkin_setup.sh — install and enable the box's check-in timer (docs/PLAN_NO_GHOST_BOXES.md P1).
#
#   bash scripts/checkin_setup.sh      # as root; idempotent; run by install_services.sh and box_update.sh
#
# EVERY BOX ALREADY SOLD GETS IT ON ITS NEXT UPDATE, with no new image: box_update.sh runs this after
# installing a release, the same way coworker_setup.sh brings the coworker tick. A changed unit is backed
# up before it is replaced. The unit itself only ever runs on a sold box (its Condition lines), so
# enabling it anywhere else costs nothing and says nothing. Never fails its caller.
set -uo pipefail
AIOS="${AIOS_DIR:-/opt/aios}"
changed=0
for n in aios-checkin.service aios-checkin.timer aios-keyfeatures.service aios-keyfeatures.timer \
         aios-restore-drill.service aios-restore-drill.timer; do
  f="$AIOS/deploy/$n"; t="/etc/systemd/system/$n"
  [ -f "$f" ] || { echo "check-in: $n missing from this release; skipped"; exit 0; }
  cmp -s "$f" "$t" 2>/dev/null && continue
  if [ -f "$t" ]; then
    b="/root/aios-units-backup-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$b"; cp -a "$t" "$b/"
  fi
  cp "$f" "$t" && changed=$((changed+1))
done
[ "$changed" -gt 0 ] && systemctl daemon-reload
systemctl enable --now aios-checkin.timer >/dev/null 2>&1 && echo "check-in: enabled" \
  || echo "check-in: could not be enabled"
# THE KEY FEATURES GATE (core/key_features.py): the box uses its MCP connector, its AI, its Morning Review and an
# approval the way a buyer does, every hour, and the check-in it nudges carries the counts.
systemctl enable --now aios-keyfeatures.timer >/dev/null 2>&1 && echo "key features check: enabled" \
  || echo "key features check: could not be enabled"
# THE BOX PROVES ITS OWN BACKUP, EVERY WEEK (core/restore_check.py, launch bar 9): last night's copy restored beside
# live and compared, the verdict kept for core.health, the Dashboard and the check-in.
systemctl enable --now aios-restore-drill.timer >/dev/null 2>&1 && echo "weekly restore drill: enabled" \
  || echo "weekly restore drill: could not be enabled"
exit 0
