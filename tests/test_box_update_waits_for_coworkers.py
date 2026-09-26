"""An update never lands on a coworker's shift (scripts/box_update.sh, the coworkers-first block).

Runs the script's own block, extracted between its markers and not re-typed, with its lock and
next-start paths pointed at a temporary folder and its waits shortened. A copy of the logic would
pass while the script regressed; the block itself can't.

Needs flock(1), which every box and CI runner has. On a laptop without it, it says so and exits
0; with CI set, a missing flock is a failure, because a gate that can't run proves nothing.

Run: python tests/test_box_update_waits_for_coworkers.py
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


src = (ROOT / "scripts" / "box_update.sh").read_text()
m = re.search(r"# >>> coworkers-first.*?\n(.*?)# <<< coworkers-first", src, re.S)
ok("the block is in box_update.sh between its markers", m is not None)
if m is None or shutil.which("flock") is None:
    if m is not None:
        print("  SKIP flock(1) is not installed here" + (" -- and CI is set, so this FAILS" if os.environ.get("CI") else ""))
        if os.environ.get("CI"):
            FAILS.append("flock missing in CI")
    print(); print("FAILED" if FAILS else "ALL OK (skipped)"); sys.exit(1 if FAILS else 0)

before = src[:m.start()]
ok("it runs BEFORE anything else takes the deploy lock",
   "aios-deploy.lock" not in before.split("set -euo pipefail", 1)[-1], "a lock is taken above the block")
ok("the deploy lock is taken nowhere else in the script", src.count("flock -n 9") == 1)

tmp = Path(tempfile.mkdtemp())
DL, SL, NX = tmp / "deploy.lock", tmp / "shift.lock", tmp / "shift.next"
script = tmp / "block.sh"
script.write_text("set -euo pipefail\n" + m.group(1) + 'echo "PROCEEDED waited=$waited"\n')


def run(wait_max=3, timeout=30) -> tuple[int, str, float]:
    env = dict(os.environ, AIOS_DEPLOY_LOCK=str(DL), AIOS_SHIFT_LOCK=str(SL), AIOS_SHIFT_NEXT=str(NX),
               AIOS_SHIFT_WAIT_MAX=str(wait_max), AIOS_SHIFT_WAIT_STEP="1")
    t0 = time.time()
    p = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr, time.time() - t0


def hold(path: Path, seconds: float) -> subprocess.Popen:
    """Hold a flock on `path` in another process for `seconds`, as a coworker run or update would."""
    return subprocess.Popen(["flock", str(path), "sleep", str(seconds)])


print("-- nothing running, nothing due --")
rc, out, took = run()
ok("the update goes ahead at once", rc == 0 and "PROCEEDED waited=0" in out, out)

print("\n-- a shift starts in 30 minutes --")
NX.write_text(f"{int(time.time()) + 1800}\n")
rc, out, took = run(wait_max=3)
ok("the update waits, saying why", "starts within the hour" in out, out)
ok("...but only up to its limit, then goes ahead", rc == 0 and "PROCEEDED waited=3" in out, out)

print("\n-- the next shift is two hours away --")
NX.write_text(f"{int(time.time()) + 7200}\n")
rc, out, took = run()
ok("the update goes ahead at once", rc == 0 and "PROCEEDED waited=0" in out, out)

print("\n-- a garbled next-start file --")
NX.write_text("soon\n")
rc, out, took = run()
ok("is ignored, never trusted", rc == 0 and "PROCEEDED waited=0" in out, out)
NX.unlink()

print("\n-- a coworker is working --")
h = hold(SL, 3); time.sleep(0.3)
rc, out, took = run(wait_max=0)
h.wait()
ok("the update waits while it works, even with the soon-limit at zero", "a coworker is working" in out, out)
ok("...and goes ahead once it's done", rc == 0 and "PROCEEDED" in out and took >= 2.5, f"{took:.1f}s {out}")

print("\n-- another update is already running --")
h = hold(DL, 3); time.sleep(0.3)
rc, out, took = run()
h.kill(); h.wait()
ok("this one aborts, as before", rc == 1 and "DEPLOY ABORTED" in out, out)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
