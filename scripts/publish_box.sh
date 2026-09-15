#!/usr/bin/env bash
# publish_box.sh — one box type's release, as a signed commit in that type's OWN repository.
#
#   bash scripts/publish_box.sh --type customer_voice --tag release/2026.09.15.3 --repo DIR [--export-dir DIR]
#
# WHY A REPOSITORY PER BOX TYPE (docs/PLAN_GOLDEN_IMAGE_CUT.md §3.1b, option B). A sold box updates with
# `git fetch` from its origin. If that origin were this monorepo, every customer box could read every
# other machine, the provisioner and the sites. Its own repository holds exactly what export_box.sh ships
# for that type and nothing else, so a box's read-only deploy key can see only its own code.
#
# THE SAME NAME, THE SAME CHAIN. The tag in the box repository carries the AIOS release's name and is
# signed by the same CI key, so scripts/box_update.sh and core/release/verify.py work unchanged on a sold
# box: downgrade, replay, trust-change and dirty-tree rules included.
#
# NOTHING IS PUSHED HERE. The new tag is verified the way a box on the previous release would verify it,
# and the caller pushes only after this script exits 0, so an unverifiable tag never leaves the machine.
# Signing is configured by the caller in the box repository (gpg.format=ssh, user.signingkey), exactly as
# release.yml does for the AIOS tag.
set -euo pipefail

TYPE="" TAG="" REPO="" EXPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --type) TYPE=${2:-}; shift 2 ;;
    --tag) TAG=${2:-}; shift 2 ;;
    --repo) REPO=${2:-}; shift 2 ;;
    --export-dir) EXPORT=${2:-}; shift 2 ;;
    *) echo "publish_box: unknown argument $1" >&2; exit 2 ;;
  esac
done
case "$TYPE" in
  customer_voice|lead|content|aios) ;;
  *) echo "publish_box: --type must be customer_voice, lead, content or aios" >&2; exit 2 ;;
esac
printf '%s' "$TAG" | grep -Eq '^release/[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$' \
  || { echo "publish_box: --tag must look like release/YYYY.MM.DD.N" >&2; exit 2; }
{ [ -n "$REPO" ] && git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; } \
  || { echo "publish_box: --repo must be a git work tree (a clone of the box repository)" >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"

if [ -z "$EXPORT" ]; then
  # EXPORT EXACTLY THE RELEASE: HEAD must be the commit the AIOS tag names, with nothing edited on top.
  want=$(git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG^{commit}") \
    || { echo "publish_box: $TAG is not a tag in $ROOT" >&2; exit 1; }
  [ "$(git -C "$ROOT" rev-parse HEAD)" = "$want" ] \
    || { echo "publish_box: $ROOT is not checked out at $TAG" >&2; exit 1; }
  [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] \
    || { echo "publish_box: $ROOT has modified files; a release is exported from a clean tree" >&2; exit 1; }
  EXPORT="$WORK/box"
  bash "$ROOT/scripts/export_box.sh" "$TYPE" "$EXPORT" >/dev/null
fi
[ -s "$EXPORT/trust/allowed_signers" ] \
  || { echo "publish_box: the export has no trust/allowed_signers, so a box built from it could never verify an update" >&2; exit 1; }

[ -z "$(git -C "$REPO" status --porcelain)" ] || { echo "publish_box: $REPO has uncommitted changes" >&2; exit 1; }
if git -C "$REPO" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "publish_box: $TAG already exists in $REPO; a published release is never re-cut" >&2; exit 1
fi
prev=$(git -C "$REPO" tag -l 'release/*' --sort=-v:refname | head -1)

# THE BOX REPOSITORY BECOMES THE EXPORT, deletions included: a file dropped from a box type must leave
# every box of that type at its next update, not linger because nothing overwrote it.
#
# --checksum, NEVER rsync's quick check. By default rsync skips a file whose size and modification time
# match, and the export and the fresh clone are written within the same second on a fast runner. A
# changed file of the same size (a version string, one flipped flag) was then silently NOT copied, and
# the release was committed, signed and verified with the OLD content under the NEW name: the signature
# proves who published a tree, not that it is the tree they meant. Measured 2026-09-15: 11 of 25 runs
# on Ubuntu (rsync 3.2.7) committed a stale file; CI caught it on main.
# my/ AND DEVSTATE.md BELONG TO THE BOX, NOT TO A RELEASE. They are written by the box and its people; carried in
# a release, an update would overwrite their edits or refuse to install over them (dirty tree). They reach a new
# box as starter/ copies, which install.sh seeds when missing.
rsync -a --delete --checksum --exclude=.git --exclude=/my/ --exclude=/DEVSTATE.md "$EXPORT/" "$REPO/"
git -C "$REPO" add -A
if [ -n "$(git -C "$REPO" ls-files -- my DEVSTATE.md)" ]; then
  echo "publish_box: $REPO tracks my/ or DEVSTATE.md, which belong to each box; remove them from the box repository first" >&2
  exit 1
fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  aios_sha=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null) || aios_sha="an unversioned tree"
  git -C "$REPO" commit -q -m "$TAG" -m "Built from AIOS $aios_sha by scripts/publish_box.sh ($TYPE)."
elif ! git -C "$REPO" rev-parse -q --verify HEAD >/dev/null; then
  echo "publish_box: the export is empty; nothing to publish" >&2; exit 1
fi
git -C "$REPO" tag -s "$TAG" -m "$TAG" HEAD

trust="$WORK/trust_allowed_signers"
if [ -n "$prev" ]; then
  git -C "$REPO" show "refs/tags/$prev^{commit}:trust/allowed_signers" > "$trust"
  (cd "$ROOT" && "$PY" -m core.release.verify "$REPO" "$TAG" --trust "$trust" --current "$prev")
else
  cp "$EXPORT/trust/allowed_signers" "$trust"
  (cd "$ROOT" && "$PY" -m core.release.verify "$REPO" "$TAG" --trust "$trust")
fi
echo "ready to push: $TAG $(git -C "$REPO" rev-parse HEAD) (previous: ${prev:-none})"
