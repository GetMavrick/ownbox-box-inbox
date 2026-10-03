#!/usr/bin/env bash
# dev_env.sh — the developer's Python is the box's Python (plan #1857 H10). Run first, every session:
#
#   bash scripts/dev_env.sh            # makes or checks ./.venv on Python 3.12, with the locked dependencies
#
# WHY. Boxes and CI run Python 3.12 (scripts/bootstrap.sh, scripts/install.sh, .github/workflows/tests.yml). A
# developer on 3.11 or 3.14, or a script that found the Mac's own 3.9 first on PATH, saw tests fail that never fail
# on a box, and a team that learns to ignore "the known 7" stops seeing the eighth.
#
# It uses `uv` when present (uv python install 3.12), otherwise a python3.12 already on PATH. It installs nothing
# else on the machine: no download of uv, no system package. The dependencies are the same locked, hashed set CI
# installs (requirements.lock), then the project itself, as CI does.
set -euo pipefail
cd "$(dirname "$0")/.."
WANT=3.12

if command -v uv >/dev/null 2>&1; then
  uv python install "$WANT" >/dev/null
  PY=$(uv python find "$WANT")
elif command -v "python$WANT" >/dev/null 2>&1; then
  PY=$(command -v "python$WANT")
else
  echo "✗ No Python $WANT here. Install uv (https://docs.astral.sh/uv/) or python$WANT, then run this again." >&2
  exit 1
fi

if [ -x .venv/bin/python ] && .venv/bin/python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))"; then
  echo "✓ .venv is Python $WANT"
else
  [ -d .venv ] && echo "  .venv is $(.venv/bin/python --version 2>&1 || echo broken); making it again on $WANT"
  rm -rf .venv
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q --require-hashes -r requirements.lock
.venv/bin/python -m pip install -q --no-deps --no-build-isolation -e .
echo "✓ $(.venv/bin/python --version) in .venv, with requirements.lock and the project. Run a suite with:"
echo "    .venv/bin/python tests/test_X.py"
