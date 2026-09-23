#!/usr/bin/env bash
# UPDATE THIS BOX to the newest VERIFIED release. Runs ON the droplet, takes no arguments, and is the
# ONLY implementation of what a deploy does — `scripts/deploy.sh` pipes this exact file over ssh, and the
# /deploy endpoint runs it locally. One copy, two callers: the alternative was the same 100 lines living
# in a heredoc AND in a self-deploy path, where every future fix has to be made twice and the two
# silently drift (the defect I held a teammate's PR for on 2026-09-07 — 1,377 duplicated lines in a
# cloned pack).
#
# WHAT IT INSTALLS, AND WHAT IT NEVER WILL. A box runs this code as root and holds a company's
# conversations and keys, so whoever can put a commit on what it follows owns all of that. It therefore
# follows no branch at all. core/release/update.py fetches release TAGS, verifies each against the trust
# file of the release this box ALREADY runs (trust/allowed_signers), and names the newest one that passes;
# this script checks out that tag and nothing else. There is deliberately no ref argument: an endpoint that
# deploys a caller-named ref is an endpoint that runs a stranger's code as root. (Until 2026-09-15 this
# merged origin/main with no signature check — `git fetch` and `git checkout` verify nothing on their own.)
#
# IF THE NEW RELEASE DOES NOT COME UP, THE OLD ONE COMES BACK. After the restart it waits for /health to
# report the new commit; if that does not happen within two minutes it checks the previous commit back
# out and restarts. Migrations are additive and do not roll back; the pre-migration snapshot below is the
# way back for the database if one is ever needed.
set -euo pipefail
# Serialize deploys: two overlapping runs would interleave git ff / pip install /
# init_db / restart. flock auto-releases when fd 9 closes (remote shell exit).
exec 9>/tmp/aios-deploy.lock
if ! flock -n 9; then
  echo "DEPLOY ABORTED: another deploy is already in progress on this box."
  exit 1
fi
cd /opt/aios
UNIT_BACKUP="/root/aios-units-backup-$(date -u +%Y%m%dT%H%M%SZ)"
# >>> ssh-key-selection (tests/test_box_update_ssh_key.py runs this block)
# A SOLD BOX CARRIES ITS OWN SSH COMMAND, AND IT MUST WIN. scripts/box_update_key.sh sets the repository's
# core.sshCommand to the box's own read-only deploy key (/var/lib/aios/update_key) with GitHub's pinned host
# key (trust/github_known_hosts). GIT_SSH_COMMAND overrides core.sshCommand, and this line used to export it
# unconditionally with /root/.ssh/id_ed25519 — a key only the OPERATOR's box has. So every box built from the
# golden image failed its daily update in the same second: "Identity file /root/.ssh/id_ed25519 not
# accessible", then "Host key verification failed", then DEPLOY ABORTED, nothing installed. Measured
# 2026-09-17 by running aios-update on a box built from image v8; every clone-check before that had only
# asked whether the timer was ENABLED. A box that cannot update never receives a fix, security or otherwise.
# The operator's monorepo checkout has no core.sshCommand, so it keeps exactly the key it always used.
if ! git -C /opt/aios config --get core.sshCommand >/dev/null 2>&1; then
  export GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o IdentitiesOnly=yes"
fi
# <<< ssh-key-selection
# NO TERMINAL, EVER. Over https git asks for a username when a repository is private or missing,
# and a timer-run updater has nobody to answer: the fetch would sit until the unit timeout and the
# log would say nothing. With prompts off it fails in one second with the real reason.
export GIT_TERMINAL_PROMPT=0
# Where releases come from: this box's own git remote unless .env names another source — a URL, or a
# path to a bundle file for a box with no network. Read with sed, never `source`: .env values carry
# unquoted spaces.
#
# STRICT MODE KILLS A GREP THAT FINDS NOTHING. This line was `grep ... | head | cut | tr`, and under
# `set -euo pipefail` a .env with no UPDATE_SOURCE — every box that never set one — made grep exit 1,
# pipefail carried it, and the script died on this line in the same second it started: no output, no
# log, no install (measured on the live box, 2026-09-15; the first verified deploy did nothing).
# sed exits 0 on no match, and `|| SOURCE=""` covers a missing .env.
SOURCE=$(sed -n 's/^UPDATE_SOURCE=//p' .env 2>/dev/null | head -1 | tr -d "\"' ") || SOURCE=""
SOURCE=${SOURCE:-origin}
mkdir -p /var/lib/aios
PREV_SHA=$(git rev-parse HEAD)
PREV_RELEASE=$(cat /var/lib/aios/release 2>/dev/null || true)
set +e
.venv/bin/python -m core.release.update --repo /opt/aios --source "$SOURCE" \
  --log /var/lib/aios/updates.jsonl > /tmp/aios-release-choice.txt 2>&1
choice_rc=$?
set -e
cat /tmp/aios-release-choice.txt
TAG=$(tail -1 /tmp/aios-release-choice.txt)
case "$choice_rc" in
  0) if ! printf '%s' "$TAG" | grep -Eq '^release/[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$'; then
       echo "UP TO DATE: no newer verified release — nothing installed."
       exit 0
     fi ;;
  2) echo "DEPLOY REFUSED: every newer release failed verification — this box stays where it is."
     echo "The reasons are above and in /var/lib/aios/updates.jsonl."
     exit 1 ;;
  *) echo "DEPLOY ABORTED: the release check could not run (above). Nothing was installed."
     exit 1 ;;
esac
# EVERY DEPENDENCY HASH-CHECKED. requirements.lock pins each package a box installs to one version and
# PyPI's sha256 hashes (scripts/lock_dependencies.py); pip refuses any file that does not match, so a
# compromised or re-uploaded release of any dependency never reaches a box. A tree from before the lock
# existed (a rollback to an older release) installs the way that release was made.
install_deps() {                            # run from the tree being installed; no arguments (see the header)
  if [ -f requirements.lock ]; then
    .venv/bin/python -m pip install -q --require-hashes -r requirements.lock \
      && .venv/bin/python -m pip install -q --no-deps --no-build-isolation -e .
  else
    .venv/bin/python -m pip install -q -e .
  fi
}
# A CHECKOUT THAT FAILS MUST SAY SO. Under `set -e` a refused checkout used to exit here with the last
# line of updates.jsonl still reading "selected", so the Updates screen said "being installed" for
# ever (#1472 R7). verify.py now refuses the known cause (a file added on the box in the release's
# way) before we get here; this catches anything else git refuses, writes it where the screen reads,
# and leaves the box exactly as it was — nothing has been switched yet.
# The checkout line stays EXACTLY as it was — tests/test_self_deploy.py pins which refs this script
# may ever check out by those literal lines — so its stderr is captured by redirecting around it.
set +e
exec 3>&2 2>/tmp/aios-checkout-err.txt
git checkout --quiet --detach "$TAG"
checkout_rc=$?
exec 2>&3 3>&-
set -e
if [ "$checkout_rc" -ne 0 ]; then
  checkout_err=$(cat /tmp/aios-checkout-err.txt 2>/dev/null || true)
  echo "DEPLOY ABORTED: could not switch to $TAG — nothing was installed. git said:"
  printf '%s\n' "$checkout_err"
  .venv/bin/python - "$TAG" "$checkout_err" >> /var/lib/aios/updates.jsonl <<'PY' || true
import datetime, json, sys
print(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "status": "install_failed", "tag": sys.argv[1], "detail": sys.argv[2][:300]}))
PY
  exit 1
fi
printf '%s\n' "$TAG" > /var/lib/aios/release
echo "installing verified release $TAG (was ${PREV_RELEASE:-$PREV_SHA})"
install_deps
# Pre-migration snapshot. init_db() below runs _run_migrations() — ALTER TABLE on shipped
# tables — NOT "pure CREATE IF NOT EXISTS". A consistent online .backup (safe under WAL) is
# taken into backups/ so every schema change is one restore from reversible. BEST-EFFORT: a
# snapshot failure LOGS and continues (a full-disk/permission blip must never turn a deploy
# into an outage), and snapshots are NEVER auto-pruned (preserve-by-default; each is ~sub-MB).
.venv/bin/python - <<'PY' || echo "WARN: pre-deploy DB snapshot failed — continuing deploy"
import os, sqlite3, time
from core.config import settings
src = settings.db_path
if os.path.exists(src):
    d = os.path.join(os.path.dirname(src) or ".", "backups")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, "pre-deploy-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as b:
        s.backup(b)
    print("pre-deploy snapshot: " + dst + " (" + str(os.path.getsize(dst)) + " bytes)")
PY
# Schema evolution: init_db runs idempotent CREATE-IF-NOT-EXISTS for new tables AND the
# versioned _run_migrations() (user_version-gated, transactional) for changes to existing ones.
.venv/bin/python scripts/init_db.py
# A BOX BUILT FROM AN OLDER IMAGE STILL GETS ITS CONNECTOR. The handoff (provision.json -> .env)
# runs in bootstrap, and bootstrap runs ONCE, at first boot. Every box built from an image cut
# before that code existed therefore has a Zernio key sitting unread in provision.json forever —
# found 2026-09-15 with the live image still at .17 and the handoff shipped in .19. Running it
# here heals them on the next update instead of requiring a new image to reach every box: it is
# idempotent, it says "already set" when there is nothing to do, and it refuses to replace a key
# the buyer chose. A box with no connector block prints one line and moves on.
if [ -f scripts/connector_handoff.py ]; then
  .venv/bin/python scripts/connector_handoff.py || echo "   connector handoff failed; box unaffected"
fi
# UNIT FILES REACH THE BOX. Editing deploy/*.service did nothing: install_services.sh copies
# them once, and deploy only restarted. Live-found 2026-09-04 — the installed worker unit was
# from June 11, missing the Nice/IOScheduling priorities AND the NoNewPrivileges/PrivateTmp/
# ProtectSystem hardening its six siblings carried. A hardening change you cannot see is worse
# than no change, so the deploy now syncs any unit it ALREADY installed.
# Only already-installed units are touched: enabling a NEW service stays a deliberate act
# (scripts/install_services.sh). Each candidate is verified before it is copied, and the old
# copy is kept under /root/aios-units-backup-<ts>/ — nothing is overwritten without a backup.
changed=0
for f in /opt/aios/deploy/aios-*.service; do
  n="$(basename "$f")"
  [ -f "/etc/systemd/system/$n" ] || continue
  cmp -s "$f" "/etc/systemd/system/$n" && continue
  if ! systemd-analyze verify "$f" >/dev/null 2>&1; then
    echo "SKIPPED $n: systemd-analyze verify failed — the installed unit is left alone."
    continue
  fi
  [ -d "$UNIT_BACKUP" ] || mkdir -p "$UNIT_BACKUP"
  cp -a "/etc/systemd/system/$n" "$UNIT_BACKUP/"
  cp "$f" "/etc/systemd/system/$n"
  echo "unit updated: $n (previous copy in $UNIT_BACKUP)"
  changed=$((changed+1))
done
if [ "$changed" -gt 0 ]; then systemctl daemon-reload; echo "daemon-reload after $changed unit change(s)"; fi

# Restart services once they exist as systemd units (no-op until then).
for svc in aios-dispatch aios-worker aios-slack; do
  if systemctl is-enabled "$svc" >/dev/null 2>&1; then
    systemctl restart "$svc" && echo "restarted $svc"
  fi
done
# THE NEW RELEASE MUST COME UP, OR THE OLD ONE COMES BACK. Only where the web service runs — a box
# without it has no /health to ask.
if systemctl is-enabled aios-dispatch >/dev/null 2>&1; then
  want=$(git rev-parse HEAD); healthy=0
  for _ in $(seq 1 24); do
    sleep 5
    got=$(curl -s -m 5 http://127.0.0.1:8000/health | .venv/bin/python -c 'import sys,json; print(json.load(sys.stdin).get("commit") or "")' 2>/dev/null || true)
    [ "$got" = "$want" ] && { healthy=1; break; }
  done
  if [ "$healthy" != 1 ]; then
    echo "ROLLBACK: $TAG did not report healthy within 120s — returning to ${PREV_RELEASE:-$PREV_SHA}."
    git checkout --quiet --detach "$PREV_SHA"
    printf '%s\n' "$PREV_RELEASE" > /var/lib/aios/release
    install_deps
    for svc in aios-dispatch aios-worker aios-slack; do
      systemctl is-enabled "$svc" >/dev/null 2>&1 && systemctl restart "$svc"
    done
    printf '{"at":"%s","status":"rolled_back","from":"%s","to":"%s"}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" "${PREV_RELEASE:-$PREV_SHA}" >> /var/lib/aios/updates.jsonl
    exit 1
  fi
fi
echo "deployed: $TAG ($(git log -1 --oneline))"
# ── POST-DEPLOY: does the deployed code still understand the live board? ──────────────────
# CI cannot answer this. CI has no Airtable. On 2026-08-05 the single-select option `Brief`
# was renamed to `Article` — which relabels every record using it, instantly and silently —
# and because daily_seed._preflight() loads THIS SAME preflight module, the unrecognised Type
# hard-failed the ARM path: 5 of 8 rows stopped being publishable and nothing said so. CI was
# green through all of it. Only a run against the real board sees a rename.
#
# WHAT FAILS A DEPLOY, AND WHAT DOES NOT. A row that fails on its CONTENT — no keyword, body
# too short, a slug collision — is ordinary board state, often the owner mid-edit, and must
# NEVER fail a deploy; that is precisely how a check like this gets ignored into uselessness.
# A row that fails because the code does not RECOGNISE the board's vocabulary is a code/board
# mismatch, and a deploy that does not match the board is a deploy that does not work.
# Best-effort on everything else: no Airtable key, no network, no script — report and move on.
.venv/bin/python - <<'PY' || exit 1
import os, pathlib, subprocess, sys

MISMATCH = "does not know its rules"          # preflight's own words for an unrecognised Type

script = pathlib.Path("scripts/preflight_written.py")
if not script.exists():
    print("board check: skipped (no preflight script)"); raise SystemExit(0)

env = dict(os.environ)
try:                                          # parse .env, never `source` it — unquoted values
    for line in pathlib.Path(".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
except OSError:
    pass
if not env.get("AIRTABLE_API_KEY"):
    print("board check: skipped (no AIRTABLE_API_KEY)"); raise SystemExit(0)

try:
    r = subprocess.run([".venv/bin/python", str(script), "--all"],
                       capture_output=True, text=True, env=env, timeout=180)
except (OSError, subprocess.TimeoutExpired) as e:
    print(f"board check: skipped ({type(e).__name__}) — deploy stands"); raise SystemExit(0)

out = r.stdout + r.stderr
bad = [l for l in out.splitlines() if MISMATCH in l]
tally = next((l for l in out.splitlines()
              if "rows safe to arm" in l or "MUST NOT be armed" in l), "")
if bad:
    print("\n" + "=" * 72)
    print("BOARD MISMATCH — the deployed code does not recognise the live board.")
    print("Every affected row is UNPUBLISHABLE and the daily seed cannot arm it.")
    print("Most likely a column or single-select OPTION was renamed on the board.")
    print("=" * 72)
    for l in dict.fromkeys(bad):
        print("  " + l.strip())
    print(f"\n  full report:  ./.venv/bin/python {script} --all\n")
    raise SystemExit(1)
print(f"board check: {tally.strip() or 'ok'}")
PY