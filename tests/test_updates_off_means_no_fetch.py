"""A box whose Managed has ended fetches nothing: not over ssh, and not over the public https mirror.

docs/PLAN_NO_GHOST_BOXES.md P5 (owner-approved direction, 2026-09-29), carrying out the owner's ruling of
2026-09-23: updates come with Managed and stop at the end of the period already paid for.

THE LEAK THIS CLOSES. Ending Managed used to be one lock: Ownbox removed the box's read-only key from its
release repository. But core/release/update.py retries a failed ssh fetch over public https (the same
repository, which is public), so a lapsed box kept installing releases while its Updates screen said it
"no longer receives updates". The box now reads its own plan and stops before either door is tried.

WHAT THIS SUITE MEASURES, with a `git` on PATH that records every invocation and refuses every fetch (so
nothing here touches the network):
  * plan off  → status no_managed, logged for the Updates screen, and NO `git fetch` at all
  * plan on / never told / unreadable → the fetch runs exactly as before, mirror retry included
  * only a box's updater honours the plan: without --honour-plan (the provisioner's own install,
    provisioner/deploy/setup.sh) nothing changes
  * scripts/box_update.sh passes --honour-plan and treats "updates off" as a clean exit, not a failure
  * the Updates screen reads the new log line, while the plan is off and after it comes back on
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="updates_off_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_UPDATES_LOG"] = str(T / "updates.jsonl")
# NO GIT CONFIG BUT OURS. A developer's or CI runner's config may rewrite ssh URLs to https
# (url.<base>.insteadOf, in a file or in GIT_CONFIG_COUNT/KEY_n/VALUE_n), which would hide the very
# ssh-then-mirror path this suite measures.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
for _k in [k for k in os.environ if k == "GIT_CONFIG_COUNT" or k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]:
    del os.environ[_k]

from core import state  # noqa: E402

state.init_db()
from core import box_updates  # noqa: E402
from core.release import update  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


REAL_GIT = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
BIN = T / "bin"
BIN.mkdir()
CALLS = T / "git_calls.txt"
fake = BIN / "git"
fake.write_text(f"""#!/bin/sh
echo "$*" >> "{CALLS}"
for a in "$@"; do
  if [ "$a" = "fetch" ]; then echo "fatal: refused by the test (no network here)" >&2; exit 128; fi
done
exec "{REAL_GIT}" "$@"
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
PATH = f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"
os.environ["PATH"] = PATH


def box_repo() -> pathlib.Path:
    """A work tree shaped like a sold box: a trust file, and an ssh origin in the box-repo form, which is
    exactly the shape that earns a retry over the public https mirror."""
    repo = pathlib.Path(tempfile.mkdtemp(prefix="box_", dir=T))
    g = lambda *a: subprocess.run([REAL_GIT, "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    (repo / "trust").mkdir()
    (repo / "trust" / "allowed_signers").write_text("ci@ownbox namespaces=\"git\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest\n")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "box")
    g("remote", "add", "origin", "git@github.com:GetMavrick/ownbox-box-test.git")
    return repo


def fetches() -> list[str]:
    if not CALLS.exists():
        return []
    return [ln for ln in CALLS.read_text().splitlines() if " fetch " in f" {ln} "]


def reset():
    CALLS.unlink(missing_ok=True)


print("— the module: updates off means no fetch —")
repo, log = box_repo(), T / "choose.jsonl"
reset()
d = update.choose(repo, "origin", log_path=log, updates_off={"updates": "off", "until": "2026-10-01"})
ok("a box told 'off' decides no_managed", d.status == "no_managed", d.status)
ok("...and runs NO git fetch at all: not origin, not the https mirror", fetches() == [], str(fetches()))
last = json.loads(log.read_text().splitlines()[-1])
ok("...and writes it to the log the Updates screen reads, with the date it ended",
   last["status"] == "no_managed" and "2026-10-01" in last["detail"], str(last))

reset()
d = update.choose(repo, "origin", log_path=log)
tried = fetches()
ok("a box with updates on still fetches, exactly as before", d.status == "cannot_run" and len(tried) == 2, str(tried))
ok("...origin first, then the SAME repository over public https (the door the leak went through)",
   len(tried) == 2 and "origin" in tried[0].split()
   and "https://github.com/GetMavrick/ownbox-box-test.git" in tried[1], str(tried))


def run_cli(*extra: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", "core.release.update", "--repo", str(repo), "--source", "origin",
                        "--log", str(T / "cli.jsonl"), *extra],
                       cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PATH=PATH))
    return r.returncode, r.stdout + r.stderr


print("\n— the command line: only a box's updater honours the plan —")
box_updates.set_plan("off", "2026-10-01")
reset()
rc, out = run_cli("--honour-plan")
ok("plan off + --honour-plan: exit 3, and no fetch", rc == 3 and fetches() == [], f"rc={rc} {fetches()} {out[-300:]}")
ok("...saying why", "updates are off" in out, out[-300:])

reset()
rc, out = run_cli()
ok("plan off WITHOUT --honour-plan (the provisioner's own install): the fetch runs as before",
   rc == 1 and len(fetches()) == 2, f"rc={rc} {fetches()}")

box_updates.set_plan("on")
reset()
rc, out = run_cli("--honour-plan")
ok("plan back on: the box's updater fetches again", rc == 1 and len(fetches()) == 2, f"rc={rc} {fetches()}")

reset()
r = subprocess.run([sys.executable, "-m", "core.release.update", "--repo", str(repo), "--source", "origin",
                    "--log", str(T / "cli.jsonl"), "--honour-plan"], cwd=ROOT, capture_output=True, text=True,
                   env=dict(os.environ, PATH=PATH, AIOS_DB_PATH=str(T / "never_told.db")))
ok("a box never told anything reads as ON (unknown is never 'off'), and fetches",
   r.returncode == 1 and len(fetches()) == 2, f"rc={r.returncode} {fetches()} {(r.stdout + r.stderr)[-300:]}")


print("\n— scripts/box_update.sh —")
sh = (ROOT / "scripts" / "box_update.sh").read_text()
call = sh[sh.index(".venv/bin/python -m core.release.update"):]
call = call[:call.index("> /tmp/aios-release-choice.txt")]
ok("the box's updater passes --honour-plan", "--honour-plan" in call, call)
case = sh[sh.index('case "$choice_rc" in'):]
case = case[:case.index("esac")]
three = case[case.index("  3)"):] if "  3)" in case else ""
three = three[:three.index(";;")] if three else ""
ok("...and exit 3 is a clean stop (exit 0): the timer does not report a failed unit every twelve hours",
   "exit 0" in three and "UPDATES OFF" in three, three)
ok("...that installs nothing: it stops before any checkout",
   sh.index("UPDATES OFF") < sh.index('git checkout --quiet --detach "$TAG"'))
setup = ROOT / "provisioner" / "deploy" / "setup.sh"
if setup.exists():
    ok("the provisioner's own install does not honour a box plan", "--honour-plan" not in setup.read_text())


print("\n— the Updates screen —")
upd_log = pathlib.Path(os.environ["AIOS_UPDATES_LOG"])
upd_log.write_text(json.dumps({"at": "2026-10-02T08:00:00+00:00", "source": "origin", "status": "no_managed",
                               "current": None, "detail": "updates are off: Ownbox Managed ended 2026-10-01"}) + "\n")
box_updates.set_plan("off", "2026-10-01")
st = box_updates.state()
ok("plan off: the screen says Managed ended, and now it is true", st["state"] == "no_managed", str(st))
box_updates.set_plan("on")
st = box_updates.state()
ok("plan back on after a no_managed check: 'back on', never 'a result this page does not recognise'",
   st["state"] == "resumed" and st["ok"] is None and "back on" in st["said"], str(st))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL OK")
