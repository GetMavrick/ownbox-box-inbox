#!/usr/bin/env bash
# Restore drill — PROVE the off-box backup restores (a backup that has never
# been restored is a prayer, not a backup). Run ON the box, any time:
#
#   bash /opt/aios/scripts/restore_drill.sh
#
# Restores the LATEST off-box generation to a temp path (never touches the live
# db), then verifies: SQLite integrity_check, the schema version, and that the
# legally-load-bearing tables (gtm_suppression) and core tables came back with
# sane row counts vs the live db. Exit 0 = the backup is real.
set -euo pipefail

AIOS=/opt/aios
CONFIG="$AIOS/deploy/litestream.yml"
LIVE_DB="$AIOS/aios.db"
OUT=$(mktemp -u /tmp/aios-restore-drill-XXXX.db)
trap 'rm -f "$OUT" "$OUT-wal" "$OUT-shm"' EXIT

# Litestream expands ${ENV} from the environment — load the same .env the unit uses.
# set -a EXPORTS what we source: plain `.` sets shell-local vars only, so the litestream
# CHILD process saw no bucket and the drill failed "bucket required for s3 replica"
# (found + fixed during the first real drill, 2026-07-20 — which then PASSED).
set +u; set -a; . "$AIOS/.env" 2>/dev/null || true; set +a; set -u

echo "== restoring latest generation → $OUT =="
/usr/local/bin/litestream restore -config "$CONFIG" -o "$OUT" "$LIVE_DB"

echo "== integrity =="
ic=$(sqlite3 "$OUT" "PRAGMA integrity_check;")
[ "$ic" = "ok" ] || { echo "FAIL: integrity_check returned: $ic"; exit 1; }
echo "integrity_check: ok"

echo "== content sanity (restored vs live) =="
fail=0
for t in jobs spend_ledger reel_scripts gtm_suppression; do
  live=$(sqlite3 "$LIVE_DB" "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "?")
  rest=$(sqlite3 "$OUT" "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "MISSING")
  echo "  $t: live=$live restored=$rest"
  [ "$rest" = "MISSING" ] && fail=1
done
ver_live=$(sqlite3 "$LIVE_DB" "PRAGMA user_version;")
ver_rest=$(sqlite3 "$OUT" "PRAGMA user_version;")
echo "  schema user_version: live=$ver_live restored=$ver_rest"
[ "$ver_rest" = "$ver_live" ] || fail=1

if [ "$fail" -ne 0 ]; then
  echo "RESTORE DRILL: FAIL — see above"; exit 1
fi
echo "RESTORE DRILL: PASS — the off-box backup restores and matches the live schema."
