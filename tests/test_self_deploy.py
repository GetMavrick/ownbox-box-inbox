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

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
