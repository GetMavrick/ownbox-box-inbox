#!/usr/bin/env bash
# Install the Codex CLI on a box for "Sign in with ChatGPT" — the buyer's own ChatGPT subscription
# drafting on their own box, no API key. The twin of install_claude_code.sh, for the second of the
# four logins the product sells.
#
# Idempotent. Run ON the box as root:  bash /opt/aios/scripts/install_codex.sh
#
# ── THE VERSION IS PINNED AND THE BYTES ARE CHECKED ─────────────────────────────────────────
# Everything a buyer sees of this — the device-code text the box parses, `codex exec`'s flags,
# what `login status` prints — is a human-facing CLI, not an API with a contract. The Claude twin
# broke silently once when its prompt changed shape; this one is pinned to the release whose
# output core/codex_login.py was written against, and the tarball's sha256 is checked before
# anything is installed. Bump the version and the hash together, after re-measuring on a box.
#
# NO NODE. Boxes ship without a Node toolchain (measured 2026-09-22: node/npm absent on the
# image), so this uses OpenAI's standalone musl binary from the GitHub release, not npm.
set -euo pipefail

CODEX_VERSION="${CODEX_VERSION:-rust-v0.155.1}"
# sha256 of codex-x86_64-unknown-linux-musl.tar.gz for rust-v0.155.1, measured 2026-09-22 on the
# test box (101,581,479 bytes).
CODEX_SHA256_X86_64="a0ef8b2debc3bf747e07b1a039354de31300ac0dcc2276498ba281470b5d9115"
BIN="${CODEX_BIN:-/usr/local/bin/codex}"           # the overrides exist for tests/test_chatgpt_asks_with_tools.py
# THE CODE-MODE HOST, BESIDE THE CLI (measured 2026-10-03 on rust-v0.155.1). The CLI's default model runs its tools
# through JavaScript in this separate executable, looked for next to the `codex` binary; without it that model is
# offered NO tools at all ("Code mode will fail closed"), so Ask your box on ChatGPT could reach none of the box's
# tools. It is a release asset of the same tag. sha256 of the tarball (25,727,325 bytes):
HOST_SHA256_X86_64="9fd083743af55be818aceb351d371fb5136f5b6aa3938f167087373d27067b2d"
# ...and of the executable inside it, so a box that already has it downloads nothing: box_update.sh runs this
# installer on every update (#1857 H3), and 25 MB per update per box would be a tax for nothing.
HOST_BIN_SHA256="${CODEX_HOST_BIN_SHA256:-210ab8ebaebf4bc1421d9e30339c858354ca35fa91e2f87f4c6204e5382f8a63}"
HOST="${CODEX_HOST_BIN:-/usr/local/bin/codex-code-mode-host}"
HOME_DIR="${CODEX_HOME_DIR:-/var/lib/aios/codex}"    # CODEX_HOME for the box: auth.json lives here, 0700, root only

case "$(uname -m)" in
  x86_64) ASSET="codex-x86_64-unknown-linux-musl"; SHA="$CODEX_SHA256_X86_64" ;;
  *) echo "install_codex: no pinned build for $(uname -m) — Sign in with ChatGPT is unavailable on this box" >&2
     exit 1 ;;
esac

want="${CODEX_VERSION#rust-v}"
if [ -x "$BIN" ] && "$BIN" --version 2>/dev/null | grep -q "codex-cli ${want}\$"; then
  echo "codex ${want} already installed"
else
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/openai/codex/releases/download/${CODEX_VERSION}/${ASSET}.tar.gz"
  curl -fsSL --retry 3 -o "$tmp/codex.tar.gz" "$url"
  echo "${SHA}  $tmp/codex.tar.gz" | sha256sum -c - >/dev/null
  tar xzf "$tmp/codex.tar.gz" -C "$tmp"
  install -m 0755 "$tmp/${ASSET}" "$BIN"
  echo "codex $("$BIN" --version | awk '{print $2}') installed at $BIN"
fi

# THE CREDENTIAL DIRECTORY EXISTS BEFORE ANYONE SIGNS IN, and it is not under /tmp: the CLI
# refuses to place helpers under a temporary dir and says so on every run. Root-only, like the
# secrets table it stands beside.
# The host carries no version flag, so the installed file is compared by its own sha256: present and exact means
# nothing is downloaded. Otherwise the pinned tarball is fetched, checked, and the host inside it installed.
case "$(uname -m)" in x86_64) HASSET="codex-code-mode-host-x86_64-unknown-linux-musl"; HSHA="$HOST_SHA256_X86_64" ;; esac
if [ -x "$HOST" ] && [ "$(sha256sum "$HOST" | awk '{print $1}')" = "$HOST_BIN_SHA256" ]; then
  echo "codex code-mode host already installed"
else
  htmp=$(mktemp -d)
  hurl="https://github.com/openai/codex/releases/download/${CODEX_VERSION}/${HASSET}.tar.gz"
  curl -fsSL --retry 3 -o "$htmp/host.tar.gz" "$hurl"
  echo "${HSHA}  $htmp/host.tar.gz" | sha256sum -c - >/dev/null
  tar xzf "$htmp/host.tar.gz" -C "$htmp"
  install -m 0755 "$htmp/${HASSET}" "$HOST"
  rm -rf "$htmp"
  echo "codex code-mode host installed at $HOST"
fi

install -d -m 0700 "$HOME_DIR"
install -d -m 0700 "$HOME_DIR/empty"    # the working root `codex exec` is pointed at: no project, no files
echo "codex home: $HOME_DIR"
