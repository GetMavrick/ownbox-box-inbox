"""The phone's deploy button: authenticated, ref-less, detached, and one implementation.

The owner's whole operating model is that he runs the business from wherever he is. Until this
existed a deploy needed his office Mac, because that is where the droplet key lives. These cases
hold the three properties that make a root-privileged HTTP endpoint safe to leave switched on.
"""
import os, pathlib, re, sys, tempfile
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1

DISPATCH = (ROOT / "core/dispatch.py").read_text()
UPDATE = (ROOT / "scripts/box_update.sh")
DEPLOY = (ROOT / "scripts/deploy.sh").read_text()

# ── one implementation, two callers ───────────────────────────────────────────────
ok("the box-side updater exists as its own script", UPDATE.exists())
body = UPDATE.read_text() if UPDATE.exists() else ""
ok("deploy.sh pipes that file instead of carrying its own copy",
   "box_update.sh" in DEPLOY)
for marker in ("core.release.update", "systemctl restart", "pip install"):
    ok(f"'{marker}' lives in box_update.sh and NOT in deploy.sh",
       marker in body and marker not in DEPLOY,
       f"in_update={marker in body} in_deploy={marker in DEPLOY}")

# ── the caller chooses nothing ────────────────────────────────────────────────────
route = DISPATCH[DISPATCH.index('@app.post("/deploy")'):DISPATCH.index('@app.get("/health")')]
ok("the route reads NOTHING off the request body",
   "get_json" not in route and "request.args" not in route and "request.form" not in route)
ok("...and names the only thing it will ever install: the newest verified release",
   "newest verified release" in route)
ok("no shell: the subprocess call passes a fixed argv, never shell=True",
   "shell=True" not in route and "subprocess.Popen([" in route)
# ── what the updater will check out, and nothing else ───────────────────────────────────────
# It used to be "only origin/main" — a branch, which anyone who can push to it controls. Now the only
# ref ever checked out is the tag core/release/update.py selected after verifying it, or the commit
# that was running before, on a rollback. The old regex for this passed by accident once flags sat
# between `checkout` and the variable, so the checks below name the exact lines instead.
checkouts = [l.strip() for l in re.findall(r"^\s*git checkout[^\n]*", body, re.M)]
ok("the updater takes no arguments — a caller cannot name a ref",
   not re.search(r"\$[1-9@*]", body.split("set -euo pipefail", 1)[1]), "found a positional parameter")
ok("exactly two checkouts: the verified TAG, and PREV_SHA on rollback",
   sorted(checkouts) == sorted(['git checkout --quiet --detach "$TAG"', 'git checkout --quiet --detach "$PREV_SHA"']),
   str(checkouts))
ok("TAG comes only from the verified selection's output",
   re.findall(r"^\s*TAG=.*$", body, re.M) == ["TAG=$(tail -1 /tmp/aios-release-choice.txt)"],
   str(re.findall(r"^\s*TAG=.*$", body, re.M)))
ok("...and is refused unless it is a release tag, so a stray output line is never checked out",
   "grep -Eq '^release/" in body and body.index("grep -Eq '^release/") < body.index('git checkout --quiet --detach "$TAG"'))
ok("a refused or failed selection exits before any checkout",
   body.index("DEPLOY REFUSED") < body.index('git checkout --quiet --detach "$TAG"')
   and body.index("DEPLOY ABORTED") < body.index('git checkout --quiet --detach "$TAG"'))
ok("a release that does not come up healthy is rolled back",
   "ROLLBACK:" in body and body.index("ROLLBACK:") > body.index("systemctl restart"))
ok("no branch is fetched or merged any more", "git merge" not in body and "origin main" not in body)

# ── the source line, EXECUTED under the script's own strict mode ─────────────────────────────
# The first verified deploy on the live box did nothing: this line died under `set -euo pipefail`
# when .env had no UPDATE_SOURCE, and every static check above passed. So the exact lines are cut
# out of the real file and run the way the box runs them.
import subprocess, tempfile
_start = body.index("SOURCE=$(")
_end = body.index("SOURCE=${SOURCE:-origin}") + len("SOURCE=${SOURCE:-origin}")
_block = body[_start:_end]
for label, env_text, want in (("no UPDATE_SOURCE line — every box that never set one", "DEPLOY_TOKEN=x\nA=b c\n", "origin"),
                              ("UPDATE_SOURCE set, quoted", 'UPDATE_SOURCE="/media/usb/r.bundle"\n', "/media/usb/r.bundle"),
                              ("no .env at all", None, "origin")):
    _d = tempfile.mkdtemp()
    if env_text is not None:
        pathlib.Path(_d, ".env").write_text(env_text)
    _r = subprocess.run(["bash", "-c", f"set -euo pipefail\ncd {_d}\n{_block}\necho \"SOURCE=$SOURCE\""],
                        capture_output=True, text=True)
    ok(f"strict mode, {label}: the script survives the source line and resolves {want}",
       _r.returncode == 0 and _r.stdout.strip() == f"SOURCE={want}", f"rc={_r.returncode} out={_r.stdout!r} err={_r.stderr[:120]!r}")

# ── auth, twice ───────────────────────────────────────────────────────────────────
ok("/deploy is checked in the before_request guard", '"/deploy"' in DISPATCH)

# ── the deploy key is NARROW, because it has to be typed into a phone ──────────────
# DISPATCH_BEARER_TOKEN is also the dashboard login (config.dash_token falls back to it) and
# the unsubscribe signing seed (core/compliance). Handing that to a remote session hands over
# all three. DEPLOY_TOKEN can only ship origin/main, which /deploy would do for anyone anyway.
ok("a separate DEPLOY_TOKEN exists", "DEPLOY_TOKEN" in (ROOT / "core/config.py").read_text())
ok("when it is set it is the ONLY key to /deploy (the wide bearer stops working here)",
   "if not narrow:" in DISPATCH and "return _authorized(req)" in DISPATCH)
ok("when it is unset nothing changes for an existing box", "_deploy_authorized" in DISPATCH)
ok("the narrow token is also compared in constant time",
   DISPATCH.count("hmac.compare_digest") >= 2)
ok("...and the route re-checks authorization itself", "_authorized(request)" in route)
ok("the token comparison is constant-time", "hmac.compare_digest" in DISPATCH)
ok("an unset token authorizes nobody", "if not token:" in DISPATCH and "return False" in DISPATCH)

# ── it must not kill its own reply ────────────────────────────────────────────────
ok("the work is handed to systemd, so restarting dispatch cannot kill the deploy",
   "systemd-run" in route)
# 202 not 200: "started", not "done". (Checked on the return statement, not the whole route —
# the error path truncates with [:200] and a substring search on "200" reads that as a status.)
ok("it answers 202 ACCEPTED (started), never 200 (done)",
   "), 202" in route and "), 200" not in route)
ok("the reply tells the caller how to verify without a shell", "/health" in route)

# ── the updater's own safety rails, preserved by the extraction ───────────────────
# The drift guard moved, it did not go: `--ff-only` refused a diverged tree, and a detached tag
# checkout would silently drop that (OSDev4, 2026-09-15). The verifier now refuses a dirty tree before
# anything is checked out, and the updater never forces.
ok("the updater still refuses to install over local drift — the verifier's dirty_tree refusal runs first",
   '"dirty_tree"' in (ROOT / "core/release/verify.py").read_text()
   and "core.release.update" in body and not re.search(r"reset --hard|checkout -f|--force", body))
ok("the updater still snapshots the database before migrations", "backup" in body.lower())
ok("the updater still serializes with a lock", "flock" in body)

# ── A SOLD BOX UPDATES ITSELF (2026-09-15) ────────────────────────────────────────────────────────────
# box_update.sh's only caller was POST /deploy, and Ownbox holds no box's bearer: a sold box would never
# install a release. The daily timer runs the same verified updater, only on a box-repository checkout.
svc = (ROOT / "deploy/aios-update.service").read_text()
tmr = (ROOT / "deploy/aios-update.timer").read_text()
inst = (ROOT / "scripts/install_services.sh").read_text()
key = (ROOT / "scripts/box_update_key.sh").read_text()
ok("the update service runs the one updater, from /opt/aios, as a oneshot",
   "ExecStart=/bin/bash /opt/aios/scripts/box_update.sh" in svc and "WorkingDirectory=/opt/aios" in svc
   and "Type=oneshot" in svc)
ok("...unhardened like /deploy's systemd-run, since it rewrites unit files (ProtectSystem would forbid it)",
   not re.search(r"^ProtectSystem=", svc, re.M))
# TWICE A DAY SINCE 2026-09-22 (owner: "we are in heavy development phase"). One window meant a
# fix merged at 09:00 UTC did not reach a sold box for 23 hours, and he hit exactly that with a
# demo hours away. The pattern accepts one OR MORE hours so the cadence can move again without a
# test edit — what it still pins is the three properties that matter.
_cal = re.search(r"^OnCalendar=\*-\*-\* ([\d,]+):\d\d:\d\d UTC$", tmr, re.M)
ok("the timer fires on a fixed UTC schedule", _cal is not None,
   "OnCalendar must be an explicit hour list, so a box's window is predictable")
ok("...twice a day while we are building",
   _cal is not None and len(_cal.group(1).split(",")) == 2,
   f"hours={_cal.group(1) if _cal else None!r}")
# SPREAD, so a fleet does not arrive at GitHub as one herd — and the spread must stay INSIDE the
# interval, or two windows overlap and a box can check twice in a row while another waits.
_delay = re.search(r"^RandomizedDelaySec=(\d+)h$", tmr, re.M)
ok("...spread across the fleet", _delay is not None)
ok("...by less than the gap between windows, so the windows cannot overlap",
   _delay is not None and _cal is not None and int(_delay.group(1)) < 24 // len(_cal.group(1).split(",")),
   f"delay={_delay.group(1) if _delay else None}h across {len(_cal.group(1).split(',')) if _cal else 0} windows")
# AND IT CATCHES A BOX THAT WAS OFF — without Persistent a powered-down box silently skips.
ok("...and catches a day the box was off", "Persistent=true" in tmr)
ok("install_services.sh installs both units",
   "deploy/aios-update.service" in inst and "deploy/aios-update.timer" in inst)
rx = re.search(r"grep -Eq '(\^git@github[^']+)'", inst)
key_rx = re.search(r"grep -Eq '(\^git@github[^']+)'", key)
ok("...and enables the timer only when origin is a box repository, by the same test the update key uses",
   rx is not None and key_rx is not None and rx.group(1) == key_rx.group(1)
   and "systemctl enable --now aios-update.timer" in inst.split(rx.group(0), 1)[1].split("else", 1)[0])
ok("...never in the fixed list every box enables, so the operator's monorepo box keeps gated deploys",
   "aios-update.timer" not in inst.split("systemctl enable --now aios-dispatch", 1)[1].split("\n\n", 1)[0])
ok("the updater exits 0 when there is nothing newer, so a daily run on a current box changes nothing",
   "UP TO DATE" in body and re.search(r"UP TO DATE[^\n]*\n\s*exit 0", body) is not None)

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
