#!/usr/bin/env bash
# Build Zernio's inbox screens for the box (docs/PLAN_INBOX_MIRRORS_ZERNIO.md, #1990 1.1).
#
#   scripts/inbox_ui_build.sh           build into marketing/customer_voice/static/inbox-ui/ (commit what it writes)
#   scripts/inbox_ui_build.sh --check   build into a scratch folder and fail if it differs from the committed files
#
# THEIR CODE IS NEVER EDITED. web/inbox-ui/upstream/ is zernio-dev/unified-inbox byte for byte (UPSTREAM.lock); this
# copies it, applies web/inbox-ui/patches/*.patch to the copy in order, installs THEIR dependencies from THEIR lock,
# runs THEIR tests (vitest), then bundles the list, the thread and the composer with OUR pinned tools
# (web/inbox-ui/tools, the esbuild and Tailwind versions their lock uses) and OUR layer (web/inbox-ui/ownbox).
#
# THE COMMITTED BUILD IS THE BUILD. The output is committed because no Node runs on a box. CI runs --check on every
# PR, so nobody ships screens that no source produces.
#
# THEIR LICENCE TRAVELS WITH THEIR CODE. MIT asks for the notice: every built file opens with it, and the licence
# itself sits beside them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI="$ROOT/web/inbox-ui"
OUT="$ROOT/marketing/customer_voice/static/inbox-ui"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
command -v node >/dev/null && command -v npm >/dev/null || { echo "✗ needs node and npm (a developer's machine or CI, never a box)" >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BUILD="$WORK/build"
DEST="$WORK/out"
COMMIT="$(sed -n 's/^# commit: //p' "$UI/UPSTREAM.lock")"
[ -n "$COMMIT" ] || { echo "✗ web/inbox-ui/UPSTREAM.lock names no commit" >&2; exit 1; }

echo "== their code, at ${COMMIT:0:8}, copied"
cp -R "$UI/upstream" "$BUILD"
for p in "$UI"/patches/*.patch; do
  [ -e "$p" ] || continue
  echo "   patch: $(basename "$p")"
  (cd "$BUILD" && patch -p1 --forward --quiet < "$p")
done

echo "== their dependencies (their lock) and their tests"
(cd "$BUILD" && npm ci --no-audit --no-fund --loglevel=error)
(cd "$BUILD" && npx --no-install vitest run --reporter=dot)

echo "== our tools (our lock)"
cp -R "$UI/tools" "$WORK/tools"
(cd "$WORK/tools" && npm ci --no-audit --no-fund --loglevel=error)

echo "== our layer, type-checked against their code"
cp -R "$UI/ownbox" "$BUILD/ownbox"
(cd "$BUILD" && npx --no-install tsc --noEmit -p .)

echo "== the screens"
# THEIR PUBLIC SETTINGS ARE FIXED HERE, as Next.js fixes NEXT_PUBLIC_* at its build. Left alone, a browser meets
# `process.env` and the thread throws as it opens. WhatsApp calling is off: no box offers WhatsApp yet (#1990 Phase 2).
# A new NEXT_PUBLIC_ name in their code fails tests/test_inbox_ui_upstream.py until it is decided here.
mkdir -p "$DEST"
(cd "$BUILD" && "$WORK/tools/node_modules/.bin/esbuild" \
    ownbox/entry/list.tsx ownbox/entry/thread.tsx ownbox/entry/composer.tsx ownbox/entry/inbox.tsx \
    --bundle --splitting --format=esm --outdir="$DEST" --entry-names='[name]' --chunk-names='chunk-[hash]' \
    --minify --target=es2020 --jsx=automatic --charset=utf8 --legal-comments=eof \
    --define:process.env.NODE_ENV='"production"' \
    --define:process.env.NEXT_PUBLIC_WHATSAPP_CALLING_ENABLED='"false"' \
    --alias:next/link=./ownbox/shims/next-link.tsx --alias:next/navigation=./ownbox/shims/next-navigation.ts \
    --log-level=error)
(cd "$BUILD" && "$WORK/tools/node_modules/.bin/tailwindcss" -i src/app/globals.css -o "$DEST/inbox-ui.css" \
    --minify >/dev/null 2>&1)

echo "== their licence on every file"
{
  printf '/*! Zernio unified-inbox, https://github.com/zernio-dev/unified-inbox at %s (MIT).\n' "$COMMIT"
  printf '    Built for Ownbox by scripts/inbox_ui_build.sh from web/inbox-ui. The licence:\n\n'
  sed 's#\*/#* /#g' "$UI/upstream/LICENSE"
  printf '*/\n'
} > "$WORK/header"
for f in "$DEST"/*.js "$DEST"/*.css; do
  cat "$WORK/header" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done
cp "$UI/upstream/LICENSE" "$DEST/LICENSE-zernio-unified-inbox.txt"

if [ "$CHECK" = 1 ]; then
  if diff -r "$DEST" "$OUT" >/dev/null 2>&1; then
    echo "✓ the committed build is the build ($(ls "$DEST" | wc -l | tr -d ' ') files)"
  else
    diff -rq "$DEST" "$OUT" || true
    echo "✗ marketing/customer_voice/static/inbox-ui/ is not what web/inbox-ui builds. Run scripts/inbox_ui_build.sh and commit what it writes." >&2
    exit 1
  fi
else
  rm -rf "$OUT"
  mkdir -p "$(dirname "$OUT")"
  cp -R "$DEST" "$OUT"
  echo "✓ wrote $(ls "$OUT" | wc -l | tr -d ' ') files to marketing/customer_voice/static/inbox-ui/"
fi
