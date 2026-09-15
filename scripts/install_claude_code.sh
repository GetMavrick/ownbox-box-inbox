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

echo "== claude CLI =="
if ! command -v claude >/dev/null 2>&1 && [ ! -x /root/.local/bin/claude ]; then
  curl -fsSL https://claude.ai/install.sh | bash
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
