#!/usr/bin/env bash
# Expose the box: Caddy + auto-HTTPS in front of the loopback gunicorn.
# Idempotent. Run ON the box as root AFTER the domain's A record points here:
#
#   bash /opt/aios/scripts/expose.sh aios.getmavrick.com
#
# This is the moment the spec's TLS-only-inbound invariant is SATISFIED rather
# than sidestepped: gunicorn stays on 127.0.0.1; only Caddy listens publicly.
set -euo pipefail

DOMAIN="${1:?usage: expose.sh <domain> [demo-zone]}"
ZONE="${2:-}"   # e.g. nlvl.co on the demo box: *.nlvl.co answers, certificates on demand, gated by /tls/ask
AIOS=/opt/aios

echo "== caddy =="
if ! command -v caddy >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq caddy
fi

echo "== caddyfile ($DOMAIN${ZONE:+ + *.$ZONE}) =="
# VALIDATE BEFORE THE LIVE FILE MOVES. A bad Caddyfile reloaded onto a running box takes the
# dashboard down with it; the candidate is checked first and the old file is untouched on failure.
bash "$AIOS/scripts/render_caddyfile.sh" "$DOMAIN" $ZONE > /etc/caddy/Caddyfile.candidate
if ! caddy validate --config /etc/caddy/Caddyfile.candidate --adapter caddyfile >/tmp/caddy-validate.txt 2>&1; then
  echo "✗ the new Caddyfile does not validate — the live one is untouched:"; sed 's/^/    /' /tmp/caddy-validate.txt | head -8; rm -f /etc/caddy/Caddyfile.candidate; exit 1
fi
mv /etc/caddy/Caddyfile.candidate /etc/caddy/Caddyfile
# CADDY'S HOME MUST EXIST AND BE CADDY'S before it starts. It runs as user caddy and cannot create /var/lib/caddy
# itself; without it every certificate fails with "permission denied" (image v2 shipped that way, measured
# 2026-09-15, see image_prepare.sh). Created only when missing, so a working box's directory is left alone.
if id caddy >/dev/null 2>&1; then
  [ -d /var/lib/caddy ] || install -d -o caddy -g caddy -m 0700 /var/lib/caddy
  [ "$(stat -c %U /var/lib/caddy)" = caddy ] || chown caddy:caddy /var/lib/caddy
fi
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy
[ -n "$ZONE" ] && echo "  *.$ZONE: on-demand certificates, gated by https://$DOMAIN/tls/ask (needs the one wildcard DNS record → this box)"

echo "== firewall =="
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
  echo "  ufw: 80/443 allowed"
else
  echo "  ufw inactive — nothing to open (DO cloud firewall, if any, is in the DO panel)"
fi

echo "== DASHBOARD_BASE_URL =="
if grep -q "^DASHBOARD_BASE_URL=" "$AIOS/.env"; then
  sed -i "s|^DASHBOARD_BASE_URL=.*|DASHBOARD_BASE_URL=https://$DOMAIN|" "$AIOS/.env"
else
  printf 'DASHBOARD_BASE_URL=https://%s\n' "$DOMAIN" >> "$AIOS/.env"
fi
chmod 600 "$AIOS/.env"
systemctl restart aios-dispatch aios-worker

echo "== verify =="
sleep 2
if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then
  echo "LIVE: https://$DOMAIN  (health OK, certificate issued)"
else
  echo "Not answering on https yet — usually DNS still propagating or the cert"
  echo "being issued. Check:  journalctl -u caddy -n 30"
fi
