#!/usr/bin/env bash
# Restore drill: PROVE the off-box backup comes back. One command, on the box, any time:
#
#   bash /opt/aios/scripts/restore_drill.sh                  # restore the replica into a temp file and compare
#   bash /opt/aios/scripts/restore_drill.sh --copy FILE      # compare a copy already restored (last night's)
#
# It restores into a TEMPORARY file (never the live database), then prints what it compared and one verdict line:
#   RESTORE DRILL: GREEN — …   or   RESTORE DRILL: RED — <each reason>
# Per table: rows live and in the copy, the newest row time in each, and what changed since the copy's moment; then
# rows read back from the copy and matched against live by hash. It never prints a row's contents or a secret.
# Exit 0 is GREEN. The work is in scripts/restore_drill.py (tests/test_restore_drill.py holds it).
set -euo pipefail

AIOS="${AIOS_ROOT:-/opt/aios}"
# Litestream expands ${ENV} from the environment: load the same .env the service uses, EXPORTED (set -a), so the
# litestream child process sees the bucket (found and fixed during the first real drill, 2026-07-20).
set +u; set -a; . "$AIOS/.env" 2>/dev/null || true; set +a; set -u
cd "$AIOS"
exec "$AIOS/.venv/bin/python" "$AIOS/scripts/restore_drill.py" --db "$AIOS/aios.db" "$@"
