#!/usr/bin/env bash
# Install the Claude Code CLI on a box for brain.backend=claude_code (the OWNER's
# subscription powering the owner's own box — resale clones stay on the api
# backend with their own key; see config/aios.config.yaml `brain:`).
#
# Idempotent. Run ON the box as root:  bash /opt/aios/scripts/install_claude_code.sh
#
# Uses Anthropic's native installer (no Node required — the render toolchain's
# Node is provisioned separately and may not exist yet on a fresh box).
set -euo pipefail

AIOS=/opt/aios

# ── THE VERSION IS PINNED, AND IT IS LOAD-BEARING ────────────────────────────────────────────
# "Sign in with your Claude subscription, no API key" is one of the product's four selling
# points, and the whole of it runs through `claude setup-token` on a pty. That is NOT an API
# with a contract — it is a human-facing prompt, and its behaviour is Anthropic's to change.
#
# IT ALREADY BROKE ONCE, SILENTLY (2026-09-21/22). The prompt reads the code in RAW MODE with
# masked echo. Two things follow from that and neither is documented anywhere:
#   · its Enter is \r — a bare \n is just another character in the buffer
#   · its reader cannot take a long burst; a real 92-character code written in one call is
#     echoed in full as asterisks and then never acted on
# Measured on this exact CLI: a 92-char code took 92.1s and returned nothing; chunked with CR
# it answers in 2.4s. The owner lost a day of demo recording to it.
#
# WHY `latest` IS THE WRONG DEFAULT FOR US. Every image cut baked whatever shipped that day, so
# a CLI change would break the sign-in on all NEW boxes at once, with no signal: CI cannot test
# it (GitHub runners have no `claude` binary) and the failure looks like the buyer's code being
# wrong. Pinning makes a CLI upgrade a DELIBERATE act with a verification step attached, which
# is what a dependency this load-bearing deserves.
#
# TO MOVE IT: bump CLAUDE_CLI_VERSION, cut an image, and run the sign-in mechanism check in
# scratchpad/verify_image.sh against it — start() and finish() must both answer in seconds. If
# finish() goes quiet for 90s, the prompt changed again and core/claude_login.submit_code needs
# revisiting before that version ships to anybody.
CLAUDE_CLI_VERSION="${CLAUDE_CLI_VERSION:-2.1.278}"

echo "== claude CLI (pinned $CLAUDE_CLI_VERSION) =="
if ! command -v claude >/dev/null 2>&1 && [ ! -x /root/.local/bin/claude ]; then
  # The installer takes [stable|latest|VERSION]; we always name a version.
  curl -fsSL https://claude.ai/install.sh | bash -s -- "$CLAUDE_CLI_VERSION"
fi
# systemd units use a fixed PATH — expose the binary at /usr/local/bin.
if [ -x /root/.local/bin/claude ] && [ ! -e /usr/local/bin/claude ]; then
  ln -sf /root/.local/bin/claude /usr/local/bin/claude
fi
/usr/local/bin/claude --version || { echo "ERROR: claude CLI did not install"; exit 1; }

echo "== token slot in .env =="
if ! grep -q "^CLAUDE_CODE_OAUTH_TOKEN=" "$AIOS/.env" 2>/dev/null; then
  printf '\n# Owner subscription token for brain.backend=claude_code — generate on\n# your laptop with `claude setup-token` and paste the sk-ant-oat... value:\nCLAUDE_CODE_OAUTH_TOKEN=\n' >> "$AIOS/.env"
  echo "added empty CLAUDE_CODE_OAUTH_TOKEN= to $AIOS/.env (paste the token there)"
else
  echo "CLAUDE_CODE_OAUTH_TOKEN line already present — leaving it untouched"
fi
chmod 600 "$AIOS/.env"

echo
echo "Done. After the token is in $AIOS/.env:"
echo "  systemctl restart aios-worker aios-dispatch"
