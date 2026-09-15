#!/usr/bin/env bash
# Install Litestream (continuous off-box SQLite replication) on the AIOS box.
# Idempotent. Run ON the box as root:  bash /opt/aios/scripts/install_litestream.sh
#
# Litestream itself is FREE (Apache-2.0). The only external piece is an
# S3-compatible bucket (Cloudflare R2 free tier / Backblaze B2 ≈ $0 at our size).
#
# Green-but-inert pattern (house standard, same as aios-slack): the binary and
# unit install unconditionally; the service is ENABLED only when the bucket
# credentials are present in /opt/aios/.env. Fill OBJECT_STORE_* later and
# re-run this script — it picks them up and starts replication.
#
# PINNED version (doctrine: pre-1.0 tools get pinned, never @latest).
set -euo pipefail

AIOS=/opt/aios
LITESTREAM_VERSION="0.3.13"
TARBALL="litestream-v${LITESTREAM_VERSION}-linux-amd64.tar.gz"
URL="https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/${TARBALL}"

echo "== litestream binary (pinned v${LITESTREAM_VERSION}) =="
if ! /usr/local/bin/litestream version 2>/dev/null | grep -q "$LITESTREAM_VERSION"; then
  tmp=$(mktemp -d)
  curl -fsSL "$URL" -o "$tmp/$TARBALL"
  tar -xzf "$tmp/$TARBALL" -C "$tmp"
  install -m 755 "$tmp/litestream" /usr/local/bin/litestream
  rm -rf "$tmp"
fi
/usr/local/bin/litestream version

echo "== unit =="
install -m 644 "$AIOS/deploy/aios-litestream.service" /etc/systemd/system/
systemctl daemon-reload

echo "== credentials check =="
# shellcheck disable=SC1091
set +u; . "$AIOS/.env" 2>/dev/null || true; set -u
if [ -n "${OBJECT_STORE_BUCKET:-}" ] && [ -n "${OBJECT_STORE_ENDPOINT:-}" ] \
   && [ -n "${OBJECT_STORE_KEY:-}" ] && [ -n "${OBJECT_STORE_SECRET:-}" ]; then
  systemctl enable --now aios-litestream
  systemctl restart aios-litestream
  sleep 2
  if systemctl is-active --quiet aios-litestream; then
    echo "aios-litestream: ACTIVE — replication running to ${OBJECT_STORE_BUCKET}"
    echo "Now PROVE it restores:  bash $AIOS/scripts/restore_drill.sh"
  else
    echo "aios-litestream: NOT ACTIVE — journalctl -u aios-litestream -n 50"; exit 1
  fi
else
  echo "OBJECT_STORE_* not (fully) set in $AIOS/.env — binary + unit installed,"
  echo "service left disabled (inert). Fill the four OBJECT_STORE_ keys and re-run."
fi
