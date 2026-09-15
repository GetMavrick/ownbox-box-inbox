#!/usr/bin/env bash
# Install/refresh the AIOS systemd services on a box. Idempotent — safe to re-run
# on every clone and after every unit-file change.
#
#   bash /opt/aios/scripts/install_services.sh        # run ON the box, as root
#
# What it does:
#   1. Creates /opt/aios/.env on first run with a fresh DISPATCH_BEARER_TOKEN
#      (0600, root-only). NEVER overwrites an existing .env — owner keys live there.
#   2. Installs gunicorn with the package (now a core dependency).
#   3. Installs + enables + starts: aios-dispatch, aios-worker, aios-watchdog.timer.
#   4. Verifies /health answers on loopback.
set -euo pipefail

AIOS=/opt/aios
cd "$AIOS"

# 1. Secrets file — create once, never clobber.
if [ ! -f "$AIOS/.env" ]; then
  umask 077
  {
    printf 'DISPATCH_BEARER_TOKEN=%s\n' "$(openssl rand -hex 32)"
    printf 'UNSUB_SIGNING_KEY=%s\n' "$(openssl rand -hex 32)"
    cat <<'KEYS'
# Owner keys — fill in as provisioned, then: systemctl restart aios-dispatch aios-worker aios-slack
# Reasoning (one of): ANTHROPIC_API_KEY (brain.backend=api) or
#   CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` (brain.backend=claude_code)
# ANTHROPIC_API_KEY=
# CLAUDE_CODE_OAUTH_TOKEN=
# Vendors:
# HEYGEN_API_KEY=
# SCRAPECREATORS_API_KEY=
# GTM Engine (outbound) — the compliance layer requires the postal address;
# sends fail closed without it. Vendor keys unlock discovery/verify/push.
# AIOS_PHYSICAL_ADDRESS=
# Object store (S3-compatible: Cloudflare R2 / Backblaze B2 / Supabase / S3) —
# serve finished reels from a CDN instead of the box (fixes choppy playback).
# Same bucket can also hold the Litestream DB replica. Set ALL FIVE to activate:
# OBJECT_STORE_BUCKET=
# OBJECT_STORE_ENDPOINT=        # e.g. https://<account>.r2.cloudflarestorage.com
# OBJECT_STORE_KEY=
# OBJECT_STORE_SECRET=
# OBJECT_STORE_PUBLIC_URL=      # CDN base for <video> tags, e.g. https://media.nlvl.co
# INSTANTLY_API_KEY=
# INSTANTLY_CAMPAIGN_ID=   # destination campaign for approved gtm sends
# RESEND_API_KEY=          # gtm send backend (gtm.send_backend: resend)
# GTM_FROM_EMAIL=          # from-address on a Resend-VERIFIED domain, e.g. brian@nlvl.co
# APOLLO_API_KEY=   # bulk lead discovery (people search)
# HUNTER_API_KEY=   # email finder/verification for discovered leads
# Airtable two-way script sync — set ALL THREE to mirror scripts ↔ VIDEOS table:
# AIRTABLE_API_KEY=        # personal access token (scopes: data.records:read+write)
# AIRTABLE_BASE_ID=        # e.g. appSO2rrnYCqiQCtu
# AIRTABLE_VIDEOS_TABLE=   # e.g. tblQFxhs3L6FywUTb
# Slack (clone bot): bot token + Socket-Mode app token + channel→intent map
# SLACK_BOT_TOKEN=
# SLACK_APP_TOKEN=
# SLACK_CHANNEL_INTENTS=C0CHANNELID:reel,C0OTHERID:brain
# REEL_SLACK_CHANNEL_ID=
# Public links (set by scripts/expose.sh):
# DASHBOARD_BASE_URL=
# Ops:
# OPERATOR_SLACK_USER_ID=
# HEALTHCHECK_URL=
KEYS
  } > "$AIOS/.env"
  echo "created $AIOS/.env with a fresh DISPATCH_BEARER_TOKEN"
else
  echo "$AIOS/.env exists — leaving it untouched"
fi
# Opt-out links must survive bearer rotation — dedicated signing key (idempotent).
if ! grep -q "^UNSUB_SIGNING_KEY=" "$AIOS/.env"; then
  printf 'UNSUB_SIGNING_KEY=%s\n' "$(openssl rand -hex 32)" >> "$AIOS/.env"
  echo "added UNSUB_SIGNING_KEY to .env"
fi
chmod 600 "$AIOS/.env"

# 2. Dependencies (brings in gunicorn).
.venv/bin/pip install -q -e .

# 3. Unit files.
install -m 644 deploy/aios-dispatch.service /etc/systemd/system/
install -m 644 deploy/aios-worker.service   /etc/systemd/system/
install -m 644 deploy/aios-watchdog.service /etc/systemd/system/
install -m 644 deploy/aios-watchdog.timer   /etc/systemd/system/
install -m 644 deploy/aios-morning.service  /etc/systemd/system/
install -m 644 deploy/aios-morning.timer    /etc/systemd/system/
install -m 644 deploy/aios-slack.service    /etc/systemd/system/
install -m 644 deploy/aios-backup.service   /etc/systemd/system/
install -m 644 deploy/aios-backup.timer     /etc/systemd/system/
systemctl daemon-reload

# 4. Enable + (re)start. restart not reload: gunicorn must re-read .env via systemd.
# aios-slack idles green when its tokens aren't in .env yet — safe to enable always.
systemctl enable --now aios-dispatch aios-worker aios-watchdog.timer \
  aios-morning.timer aios-backup.timer aios-slack
systemctl restart aios-dispatch aios-worker aios-slack

# 5. Verify.
sleep 2
for svc in aios-dispatch aios-worker; do
  if systemctl is-active --quiet "$svc"; then
    echo "$svc: active"
  else
    echo "$svc: NOT ACTIVE — journalctl -u $svc -n 50"; exit 1
  fi
done
curl -fsS http://127.0.0.1:8000/health >/dev/null && echo "/health: OK"

echo
echo "Box is ON. From your laptop:"
echo "  ssh -N -L 8000:127.0.0.1:8000 root@\$THIS_BOX     # keep open, then browse"
echo "  http://localhost:8000/dash"
echo "Login token (never printed here):"
echo "  ssh root@\$THIS_BOX \"grep DISPATCH_BEARER_TOKEN /opt/aios/.env\""
