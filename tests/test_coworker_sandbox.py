"""The coworker's locked unit and the update/shift locks (docs/SCOPE_SHIFTS.md §4.1 and §3).

The properties themselves were proven on a real box (core/coworkers/sandbox.py's docstring). This
suite makes sure they can't quietly drift: every protection that proof relied on is still in the
command the runner will use, the credential never becomes a readable property, a CLI inside /root
is refused by name, and the two locks keep a coworker and an update apart in both directions.

Run: python tests/test_coworker_sandbox.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.coworkers import locks, sandbox  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


def refused(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except sandbox.SandboxError as e:
        return str(e)
    return ""


print("-- the unit carries every protection the proof relied on --")
ws = sandbox.workspace("front-desk")
cmd = sandbox.argv(unit="aios-coworker-front-desk-1", command=["/usr/local/lib/claude-code/2.1.278", "-p", "x"],
                   workspace_dir=ws, minutes=30, env_file="/run/aios-coworker/abc.env")
props = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-p" and i + 1 < len(cmd) and "=" in cmd[i + 1]]
for need in ("User=aios-shift", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes",
             "NoNewPrivileges=yes", "CapabilityBoundingSet=", f"WorkingDirectory={sandbox.MOUNT}",
             "Environment=HOME=/tmp", "MemoryMax=1G", "RuntimeMaxSec=1920"):
    ok(f"has {need}", need in props, str(props))
hidden = next((p for p in props if p.startswith("InaccessiblePaths=")), "")
ok("hides /opt/aios (code, .env, database) outright", " /opt/aios " in f" {hidden.split('=', 1)[1]} ", hidden)
ok("the workspace is the only way in", f"BindPaths={ws}:{sandbox.MOUNT}" in props, str(props))
ok("the workspace lives in my/, which updates never touch", ws == "/opt/aios/my/coworkers/front-desk/workspace", ws)
ok("the credential arrives by EnvironmentFile, not a readable Environment= property",
   "EnvironmentFile=/run/aios-coworker/abc.env" in props
   and not any(p.startswith("Environment=") and "TOKEN" in p.upper() for p in props), str(props))
ok("the command runs after --, so nothing in it is read as an option", cmd[cmd.index("--") + 1].endswith("2.1.278"))
ok("it waits for the run and cleans up the unit", {"--wait", "--pipe", "--collect"} <= set(cmd))

ok("HOME is the unit's private /tmp, so the CLI's own state never lands in the workspace",
   "Environment=HOME=/tmp" in props and not any(p == f"Environment=HOME={sandbox.MOUNT}" for p in props))

print("\n-- the run's private files: read-only inside, credential by file --")
base = tempfile.mkdtemp()
rd = sandbox.run_dir("run_20260926_0730_ab12", base=base)
ok("one private directory per run, 0750", oct(os.stat(rd).st_mode & 0o777) == "0o750", oct(os.stat(rd).st_mode))
try:
    sandbox.run_dir("run_20260926_0730_ab12", base=base); again = False
except FileExistsError:
    again = True
ok("a second run with the same id is refused, never merged into the first", again)
inside = sandbox.write_private(rd, "mcp.json", "{}")
ok("a private file is addressed by its path INSIDE the unit", inside == f"{sandbox.RUN_DIR}/mcp.json", inside)
ok("...and is 0640 on the host", oct(os.stat(os.path.join(rd, "mcp.json")).st_mode & 0o777) == "0o640")
envp = sandbox.write_env(rd, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abc_DEF-123", "TZ": "America/Los_Angeles"})
ok("the credential file is 0600, root only", oct(os.stat(envp).st_mode & 0o777) == "0o600")
ok("...one KEY=value per line", open(envp).read() == "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abc_DEF-123\nTZ=America/Los_Angeles\n")
ok("a value systemd would rewrite (a quote) is refused", "cannot put" in refused(
    sandbox.write_env, sandbox.run_dir("r2", base=base), {"X": 'a"b'}))
bound = sandbox.properties(workspace_dir=ws, minutes=5, env_file=envp, ro_dir=rd)
ok("the run directory is bound READ-ONLY at RUN_DIR", f"BindReadOnlyPaths={rd}:{sandbox.RUN_DIR}" in bound, str(bound))
ok("...and without one, nothing is bound there", not any(p.startswith("BindReadOnlyPaths=") for p in props))
un = sandbox.unit_name("front-desk", "run_20260926_0730_AB12")
ok("a unit name per run, legal for systemd", un == "aios-coworker-front-desk-run-20260926-0730-ab12", un)
ok("a caller that gives up stops the unit", sandbox.stop_argv(un) == ["systemctl", "stop", un])
ok("...and can stop nothing else", "not a coworker unit" in refused(sandbox.stop_argv, "aios-dispatch"))

print("\n-- what is refused, by name --")
ok("a unit name outside aios-coworker-*", "unit must be" in refused(
    sandbox.argv, unit="ssh", command=["x"], workspace_dir=ws, minutes=5))
ok("a limit past two hours", "minutes" in refused(sandbox.properties, workspace_dir=ws, minutes=121))
ok("a limit that is not a whole number", "minutes" in refused(sandbox.properties, workspace_dir=ws, minutes=True))
ok("a workspace path with a colon (it would split BindPaths)",
   "workspace" in refused(sandbox.properties, workspace_dir="/opt/x:/etc", minutes=5))
ok("a relative credential file", "env_file" in refused(sandbox.properties, workspace_dir=ws, minutes=5, env_file="t.env"))
ok("a coworker name that is not a slug", "not a coworker name" in refused(sandbox.workspace, "../root"))

print("\n-- the CLI must be outside /root, or the coworker silently does nothing --")
d = tempfile.mkdtemp()
real = os.path.join(d, "2.1.278"); open(real, "w").write("#!/bin/sh\n"); link = os.path.join(d, "claude")
os.symlink(real, link)
ok("a CLI outside any home folder is used", sandbox.cli_path(link) == os.path.realpath(real))
orig = os.path.realpath
try:
    os.path.realpath = lambda p: "/root/.local/share/claude/versions/2.1.278"
    os.path.isfile, _isfile = (lambda p: True), os.path.isfile
    why = refused(sandbox.cli_path, link)
finally:
    os.path.realpath = orig; os.path.isfile = _isfile
ok("a CLI under /root is refused with the fix in the reason", "/root" in why and "update" in why, why)
ok("no CLI at all is refused, not crashed on", "not installed" in refused(sandbox.cli_path, os.path.join(d, "nope")))

print("\n-- a coworker and an update never overlap --")
t = tempfile.mkdtemp()
SL, DL, NX = (os.path.join(t, n) for n in ("shift.lock", "deploy.lock", "shift.next"))
with locks.shift(SL, DL) as held:
    ok("with nothing else running, a shift gets the lock", held is True)
    ok("...and while it holds it, a second run can't start", locks._busy(SL))
    with locks.shift(SL, DL) as second:
        ok("...a second shift gets False, not a wait or an error", second is False)
ok("the lock is released when the run ends", not locks._busy(SL))

import fcntl  # noqa: E402
fd = os.open(DL, os.O_RDWR | os.O_CREAT)
fcntl.flock(fd, fcntl.LOCK_EX)
ok("an update in progress is seen", locks.update_running(DL))
with locks.shift(SL, DL) as held:
    ok("a shift during an update gets False and holds nothing", held is False and not locks._busy(SL))
fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
ok("a missing lock file is not busy, and is not created by looking",
   not locks._busy(os.path.join(t, "never")) and not os.path.exists(os.path.join(t, "never")))

orig_dl = locks.DEPLOY_LOCK
locks.DEPLOY_LOCK = DL
fd = os.open(DL, os.O_RDWR | os.O_CREAT); fcntl.flock(fd, fcntl.LOCK_EX)
ok("the lock paths are read when called, so pointing them elsewhere really moves the check",
   locks.update_running() is True)
fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd); locks.DEPLOY_LOCK = orig_dl

print("\n-- the next start, for box_update.sh --")
locks.write_next(1790000000, NX)
ok("written as whole seconds and a newline", open(NX).read() == "1790000000\n")
ok("no temporary file is left beside it", sorted(os.listdir(t)).count("shift.next") == 1
   and not any(n.startswith(".aios-shift.next.") for n in os.listdir(t)), str(os.listdir(t)))
locks.write_next(None, NX)
ok("None clears it", not os.path.exists(NX))
locks.write_next(None, NX)
ok("...and clearing twice is fine", not os.path.exists(NX))
try:
    locks.write_next(-5, NX); bad = False
except ValueError:
    bad = True
ok("a negative time is refused", bad)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
