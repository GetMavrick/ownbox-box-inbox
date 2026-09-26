"""The box's half of the upgrade to Pro (core/upgrade.py, docs/SCOPE_UPGRADE_TO_PRO.md §2.3).

Two doors behind the per-box deploy token: the stage the provisioner reports, and "get ready to be
shut down". The second must hold the real update lock before it says ready, must never say ready
while a coworker works or an update installs, and must refuse outright when no upgrade is under way.
The holder unit is faked with a real process holding a real flock(2) lock on a temporary file,
the same kind of lock `flock(1)` takes on the box, so the locks here are real ones.

Run: python tests/test_upgrade_box_side.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["DEPLOY_TOKEN"] = "deploy-narrow"

from core import state, upgrade  # noqa: E402
from core.coworkers import locks  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


locks.DEPLOY_LOCK = str(T / "deploy.lock")
locks.SHIFT_LOCK = str(T / "shift.lock")


HOLDER = ("import fcntl, os, sys, time\n"
          "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "print('held', flush=True)\n"
          "time.sleep(float(sys.argv[2]))\n")


def hold(path, seconds, wait=True):
    """A process holding `path`'s lock, as a coworker run, an update or the holder unit would."""
    p = subprocess.Popen([sys.executable, "-c", HOLDER, path, str(seconds)], stdout=subprocess.PIPE, text=True)
    if wait:
        p.stdout.readline()
    return p


def stop(p):
    p.kill(); p.wait()


class FakeSystemd:
    """systemd-run / systemctl for the one holder unit, backed by a real lock-holding process."""
    def __init__(self):
        self.proc = None
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "systemd-run":
            i = cmd.index("flock")
            self.proc = hold(cmd[i + 1], cmd[i + 3], wait=False)   # blocks until it gets the lock, like flock(1)
        elif cmd[:2] == ["systemctl", "stop"] and self.proc:
            stop(self.proc); self.proc = None
        alive = self.proc is not None and self.proc.poll() is None
        rc = 0 if (cmd[:2] == ["systemctl", "is-active"] and alive) or cmd[0] != "systemctl" or cmd[1] == "stop" else 3
        return subprocess.CompletedProcess(cmd, rc, b"", b"")


print("-- the stage the provisioner reports --")
ok("nothing said yet reads as nothing", upgrade.status() == {})
try:
    upgrade.set_status("almost"); bad = False
except ValueError:
    bad = True
ok("an unknown stage is refused, never stored", bad and upgrade.status() == {})
upgrade.set_status("paid", "cs_test_abc")
ok("a known stage is kept, with when", upgrade.status()["stage"] == "paid" and upgrade.status()["at"])

print("\n-- getting ready to be shut down --")
upgrade.set_status("done")
sd = FakeSystemd()
got = upgrade.prepare_restart(run=sd)
ok("refused when no upgrade is under way (a leaked token can't hold updates back)",
   got.get("refused") and not any(c[0] == "systemd-run" for c in sd.calls), str(got))

old = datetime.now(timezone.utc) - timedelta(hours=4)
upgrade.set_status("paid", now=old)
ok("...and when the payment is hours old, it isn't 'under way' any more",
   upgrade.prepare_restart(run=FakeSystemd()).get("refused"))

upgrade.set_status("paid")
h = hold(locks.SHIFT_LOCK, 5)
sd = FakeSystemd()
got = upgrade.prepare_restart(run=sd)
ok("a coworker at work: 'wait', and nothing is held", got == {"ready": False, "wait": "a coworker is working"}
   and sd.proc is None, str(got))
stop(h)

h = hold(locks.DEPLOY_LOCK, 5)
got = upgrade.prepare_restart(run=FakeSystemd())
ok("an update installing: 'wait'", got == {"ready": False, "wait": "an update is installing"}, str(got))
stop(h)

sd = FakeSystemd()
got = upgrade.prepare_restart(run=sd)
ok("otherwise: ready, and the update lock really is held", got == {"ready": True} and locks.update_running(), str(got))
ok("...by the holder unit, which lets go by itself after 30 minutes",
   any(c[0] == "systemd-run" and "RuntimeMaxSec=1860" in c and f"--unit={upgrade.HOLD_UNIT}" in c for c in sd.calls))
ok("...and the stage says restarting", upgrade.status()["stage"] == "restarting")
with locks.shift(locks.SHIFT_LOCK, locks.DEPLOY_LOCK) as held:
    ok("a shift due now can't start while it holds", held is False)
got = upgrade.prepare_restart(run=sd)
ok("asked again, it is still ready and starts nothing new",
   got == {"ready": True} and sum(1 for c in sd.calls if c[0] == "systemd-run") == 1)
sd(["systemctl", "stop", upgrade.HOLD_UNIT])

print("\n-- the doors, behind the deploy token --")
from core.dispatch import app  # noqa: E402
c = app.test_client()
H = {"Authorization": "Bearer deploy-narrow"}
ok("shut without the deploy token", c.post("/deploy/upgrade-status", json={"stage": "paid"}).status_code == 401)
ok("...and to the wide token", c.post("/deploy/upgrade-status", json={"stage": "paid"},
                                      headers={"Authorization": "Bearer bearer"}).status_code == 401)
ok("an unknown stage is a 400", c.post("/deploy/upgrade-status", json={"stage": "nope"}, headers=H).status_code == 400)
ok("a known one is stored", c.post("/deploy/upgrade-status", json={"stage": "held", "detail": "not a Base box"},
                                  headers=H).status_code == 200 and upgrade.status()["stage"] == "held")
ok("prepare-restart with no upgrade under way is a 409",
   c.post("/deploy/prepare-restart", headers=H).status_code == 409)
ok("...and shut without the token", c.post("/deploy/prepare-restart").status_code == 401)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
