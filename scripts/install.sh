#!/usr/bin/env bash
# install.sh — the one command. Idempotent: re-running upgrades the venv and re-checks;
# it never overwrites .env, a licence, or a database that already exists.
#
#   bash scripts/install.sh                                   # asks for the licence details
#   bash scripts/install.sh --buyer "Your Co" --order "ref"   # no questions
#
# Mac and Linux: run it in Terminal. Windows: run it in Git Bash (installed by the GitHub step
# in setup-instructions.md) — the same script, the same commands. On a VPS, bootstrap.sh
# already did the system packages; this does the rest.
set -euo pipefail

# EVERY DEPENDENCY HASH-CHECKED. requirements.lock pins each package a box installs to one version and
# PyPI's sha256 hashes (scripts/lock_dependencies.py); pip refuses any file that does not match, so a
# compromised or re-uploaded release of any dependency never reaches a box. A tree from before the lock
# existed (a rollback to an older release) installs the way that release was made.
install_deps() {                            # $1 = the venv's python; run from the tree being installed
  if [ -f requirements.lock ]; then
    "$1" -m pip install -q --require-hashes -r requirements.lock \
      && "$1" -m pip install -q --no-deps --no-build-isolation -e .
  else
    "$1" -m pip install -q -e .
  fi
}
cd "$(dirname "${BASH_SOURCE[0]}")/.."
BUYER=""; ORDER=""
while [ $# -gt 0 ]; do case "$1" in --buyer) BUYER="$2"; shift 2;; --order) ORDER="$2"; shift 2;; --into) export AIOS_INTO="$2"; shift 2;; *) echo "unknown flag $1"; exit 2;; esac; done

say() { printf '\n== %s\n' "$*"; }
PY=""; for c in python3.12 python3.13 python3.14 python3 py; do
  if command -v "$c" >/dev/null 2>&1; then v="$("$c" -c 'import sys;print(sys.version_info[:2]>=(3,12))' 2>/dev/null || echo False)"; [ "$v" = True ] && { PY="$c"; break; }; fi
done
[ -n "$PY" ] || { echo "✗ Python 3.12 or newer is required and was not found. Install it (python.org, or 'brew install python@3.12' / 'apt install python3.12'), then run this again."; exit 1; }

# D1b — A SECOND CORE MACHINE PLUGS IN; IT NEVER INSTALLS A SECOND AIOS. If a foundation already
# exists at --into <path>, this box is a PACK to add to it: the machine's folder, its config
# sections and modules: lines, its licence line — and that box's my/, wall, database and settings
# are untouched. Detected by the markers every foundation carries.
is_foundation() { [ -f "$1/CLAUDE.md" ] && [ -f "$1/core/brain.py" ] && [ -f "$1/config/aios.config.yaml" ] && [ -f "$1/core/FOUNDATION_VERSION" ]; }
if [ -n "${AIOS_INTO:-}" ]; then
  is_foundation "$AIOS_INTO" || { echo "✗ $AIOS_INTO is not an AIOS foundation (no CLAUDE.md / core/brain.py / config / FOUNDATION_VERSION there)"; exit 1; }
  HAVE="$(cat "$AIOS_INTO/core/FOUNDATION_VERSION")"; NEED="$(cat core/FOUNDATION_VERSION)"
  say "existing foundation at $AIOS_INTO (v$HAVE) — plugging this machine into it"
  if [ "$(printf '%s\n%s\n' "$NEED" "$HAVE" | sort -V | head -1)" != "$NEED" ]; then
    echo "✗ that foundation is v$HAVE; this machine needs v$NEED. In $AIOS_INTO run: git pull && bash scripts/install.sh"; exit 1; fi
  ADDED=0
  for M in marketing/lead_machine marketing/content_machine marketing/ads_machine; do
    [ -d "$M" ] || continue
    if [ -d "$AIOS_INTO/$M" ]; then echo "   $M: already there — left alone"; continue; fi
    cp -R "$M" "$AIOS_INTO/$M" && echo "   $M: added" && ADDED=$((ADDED+1))
  done
  [ -f "$AIOS_INTO/marketing/__init__.py" ] || cp marketing/__init__.py "$AIOS_INTO/marketing/__init__.py"
  VPY2="$AIOS_INTO/.venv/bin/python"; [ -x "$VPY2" ] || VPY2="$AIOS_INTO/.venv/Scripts/python.exe"; [ -x "$VPY2" ] || VPY2="$(command -v python3.12 || command -v python3)"
  "$VPY2" - "$AIOS_INTO" <<'PYX'
import sys, pathlib, yaml, re, json
into = pathlib.Path(sys.argv[1]); tracked = into/"config"/"aios.config.yaml"; mine_p = pathlib.Path("config/aios.config.yaml")
t = tracked.read_text(); have = yaml.safe_load(t) or {}; mine = yaml.safe_load(mine_p.read_text()) or {}; src = mine_p.read_text()
KERNEL = {"modules","spaces","operator","brain","cost","worker","vendors","models","model_ids","rates","web_modules"}
mods = [x for x in (mine.get("modules") or []) if x not in (have.get("modules") or [])]
if mods:
    t = re.sub(r"^modules:\n", "modules:\n" + "".join(f"  - {x}\n" for x in mods), t, count=1, flags=re.M)
webs = [x for x in (mine.get("web_modules") or []) if x not in (have.get("web_modules") or [])]
if webs and re.search(r"^web_modules:\n", t, re.M):
    t = re.sub(r"^web_modules:\n", "web_modules:\n" + "".join(f"  - {x}\n" for x in webs), t, count=1, flags=re.M)
added = 0
for k in mine:
    if k in KERNEL or k in have: continue
    mm = re.search(rf"^{re.escape(k)}:\n(?:[ \t].*\n|\n)*", src, re.M)
    if mm: t = t.rstrip("\n") + "\n\n" + mm.group(0).rstrip("\n") + "\n"; added += 1
tracked.write_text(t); yaml.safe_load(t)
lic = into/"licence.json"; d = json.loads(lic.read_text()) if lic.exists() else {}
minelic = json.loads(pathlib.Path("licence.json").read_text()) if pathlib.Path("licence.json").exists() else {}
d.setdefault("machines", [])
if d.get("product") and d["product"] not in d["machines"]: d["machines"].append(d["product"])   # the foundation's own machine, first
for prod in [n for n, m in (("lead-machine","marketing/lead_machine/handler.py"),("content-machine","marketing/content_machine/written")) if pathlib.Path(m).exists()]:
    if prod not in d["machines"]: d["machines"].append(prod)
lic.write_text(json.dumps(d, indent=2) + "\n")
print(f"   config: +{len(mods)} module(s), +{len(webs)} web module(s), +{added} section(s) — overlay untouched; licence machines={d['machines']}")
PYX
  ( cd "$AIOS_INTO" && install_deps "$VPY2" 2>/dev/null; "$VPY2" tests/test_schema_integrity.py >/dev/null 2>&1 && echo "   proof: schema ok" || echo "   proof: schema FAILED — run tests/test_schema_integrity.py there" )
  echo; echo "Done: one foundation, $ADDED machine(s) added. Restart the worker there (or re-run its install.sh)."
  exit 0
fi

say "1/6 virtual environment ($PY)"
[ -d .venv ] || "$PY" -m venv .venv
VPY=".venv/bin/python"; [ -x "$VPY" ] || VPY=".venv/Scripts/python.exe"      # Git Bash on Windows
install_deps "$VPY" && echo "   installed (every dependency hash-checked against requirements.lock)"

say "2/6 .env"
# THE BOX'S OWN FILES START FROM starter/, and a file that exists is NEVER touched. my/ and DEVSTATE.md are written
# by the box and its people, so a box that updates from its box repository never receives them in a release
# (scripts/export_box.sh, scripts/publish_box.sh); a fresh box gets the starters here, once.
if [ -d starter ]; then
  while IFS= read -r f; do
    [ -e "$f" ] && continue
    mkdir -p "$(dirname "$f")" && cp "starter/$f" "$f" && echo "   seeded $f"
  done < <(cd starter && find . -type f | sed 's#^\./##')
fi
if [ -f .env ]; then echo "   .env exists — left exactly as it is"; else cp .env.example .env && echo "   created from .env.example — fill in your keys after this finishes"; fi
# Secrets the box mints for itself. An existing value is NEVER touched — rotating one is a
# deliberate act, and UNSUB_SIGNING_KEY in particular must outlive every email it signed.
mint() {                                    # $1 = var, $2 = what it is for
  if grep -qE "^$1=.+" .env; then echo "   $1 present"; return; fi
  sed -i.bak -E "/^$1=[[:space:]]*\$/d" .env 2>/dev/null || true; rm -f .env.bak
  printf '%s=%s\n' "$1" "$("$VPY" -c 'import secrets;print(secrets.token_hex(32))')" >> .env
  echo "   generated $1 ($2)"
}
mint UNSUB_SIGNING_KEY     "keep it forever: opt-out links are signed with it"
# Until this was minted here, a fresh box shipped with DISPATCH_BEARER_TOKEN empty — and an
# empty bearer fails BOTH front doors: /dispatch refuses every request, and the dashboard
# login form refuses every password. The buyer's first act after install was failing the login
# of the thing they had just bought, with nothing on screen saying why.
mint DISPATCH_BEARER_TOKEN "the box's own API key — /dispatch, and the dashboard login"
# A SEPARATE DASHBOARD PASSWORD, so the box can admit a second person (docs/DESIGN_PER_PERSON_LOGIN.md).
# core/config falls back to the bearer when DASH_TOKEN is unset, and core/state.add_user REFUSES to add
# anyone while the dashboard password IS the box's API key: signing an employee in would hand them the
# credential that drives the whole box. Minted here, every fresh box can invite people from day one; the
# owner signs in with the email and password he chose at /claim, and this stays in .env as his break-glass.
mint DASH_TOKEN            "the owner's break-glass dashboard password, never the API key"

say "3/6 licence"
if [ -f licence.json ]; then echo "   licence.json exists — kept"; else
  PRODUCT=aios; [ -d marketing/content_machine/written ] || PRODUCT=lead-machine; [ -f marketing/lead_machine/handler.py ] || PRODUCT=content-machine
  [ -n "$BUYER" ] || { printf '   your name or company: '; read -r BUYER; }
  [ -n "$ORDER" ] || { printf '   order or gift reference: '; read -r ORDER; }
  "$VPY" scripts/licence_stamp.py --product "$PRODUCT" --buyer "$BUYER" --order "$ORDER"
fi

say "4/6 database"
"$VPY" -c "from core import state; state.init_db()" && echo "   ready (existing data untouched)"

say "5/6 proof"
"$VPY" tests/test_schema_integrity.py >/dev/null 2>&1 && echo "   schema integrity: ok" || { echo "   schema integrity: FAILED — run .venv/bin/python tests/test_schema_integrity.py to see why"; exit 1; }

say "6/6 the doctor"
"$VPY" scripts/doctor.py || true
