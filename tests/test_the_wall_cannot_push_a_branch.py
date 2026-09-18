"""Posting to the wall must never be able to push code to main.

WHAT HAPPENED, 2026-09-18. `devstate-post.sh` printed, as its landing instruction,
`git push origin HEAD:main`. A dev ran it from a feature branch: it pushed the WHOLE BRANCH, GitHub
marked their pull request merged, and main was red for nine minutes carrying code no one had reviewed.
The helper printed "⚠ THIS IS NOT main" one line above the command that did it — which is the lesson:
a caution beside a loaded command is not a safety, and the fix has to be in what the command CAN do.

So: no helper prints or runs `HEAD:main` from an arbitrary checkout any more. `devstate-land.sh` builds
a DETACHED worktree at origin/main, writes only DEVSTATE.md into it, and pushes from there — a branch
has nothing to ride on.

Run: python tests/test_the_wall_cannot_push_a_branch.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


post = ROOT / ".claude" / "scripts" / "devstate-post.sh"
land = ROOT / ".claude" / "scripts" / "devstate-land.sh"

# THE WALL IS OURS, NOT THE PRODUCT'S — and this suite ships into every box, where `.claude/scripts`
# does not exist. `test_recipe_ships` runs every shipped suite inside a built box, so without this the
# read raises FileNotFoundError and reddens a PR whose subject a customer's box has no opinion about.
# I diagnosed exactly this trap for OSDev4 on #1374 four hours ago and then walked into it myself.
# BOTH, NOT EITHER. A box DOES carry `.claude/scripts/devstate-post.sh`, so the first version of this
# guard (skip only when BOTH are absent) still fell through and read the lander that a box has not
# got. Anything short of 'every file I am about to read is here' is a guess about what ships.
if not (post.exists() and land.exists()):
    print("  --   the wall's tooling is not part of a box; nothing to check here")
    print("\nALL OK")
    raise SystemExit(0)

print("\n— the instruction that fired —")
ok("the post helper ships", post.exists())
src = post.read_text()
live = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
ok("it no longer tells anyone to push HEAD to main",
   "push origin HEAD:main" not in live,
   next((l.strip() for l in live.split("\n") if "HEAD:main" in l), ""))
ok("...and points at the lander instead", "devstate-land.sh" in live)

print("\n— the lander cannot carry a branch —")
ok("the lander ships", land.exists())
lsrc = land.read_text()
lland = "\n".join(l for l in lsrc.split("\n") if not l.lstrip().startswith("#"))
ok("it works from a DETACHED worktree at origin/main, not from your checkout",
   "worktree add" in lland and "--detach" in lland and "origin/main" in lland)
ok("...and stages only the wall file",
   re.search(r"add DEVSTATE\.md", lland) is not None and " add -A" not in lland and " add ." not in lland)
ok("...pushing from that worktree, never from the caller's HEAD",
   re.search(r'git -C "\$TMP" push -q origin HEAD:main', lland) is not None,
   "the only HEAD:main allowed is the detached one")
ok("...and it retries when the wall moves under it", "for try in" in lland)

print("\n— and it says whether it worked —")
ok("it reports how many copies reached origin, and fails if that is not one",
   "copies on origin:" in lland and '[ "$n" = 1 ]' in lland)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
