#!/usr/bin/env bash
# Print the box's Caddyfile. One argument = the box's own address (today's file, unchanged).
# Two = also the demo zone: Caddy answers *.<zone> and issues each certificate ON DEMAND, only
# after this box's /tls/ask says yes (an industry a pack claims). No DNS token on the box.
#   bash scripts/render_caddyfile.sh aios.example.com              > /etc/caddy/Caddyfile
#   bash scripts/render_caddyfile.sh aios.example.com nlvl.co      > /etc/caddy/Caddyfile
set -euo pipefail
DOMAIN="${1:?usage: render_caddyfile.sh <domain> [demo-zone]}"; ZONE="${2:-}"
AIOS="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "$ZONE" ]; then
  cat <<EOF
# global options must come first: Caddy asks the box before issuing any on-demand certificate
{
	on_demand_tls {
		ask http://127.0.0.1:8000/tls/ask
	}
}

EOF
fi
sed "s/{\$AIOS_DOMAIN}/$DOMAIN/" "$AIOS/deploy/Caddyfile"
if [ -n "$ZONE" ]; then
  cat <<EOF

# the demo zone — one box, many subdomains; a certificate per hostname, issued only on /tls/ask = 200
*.$ZONE {
	tls {
		on_demand
	}
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
EOF
fi
