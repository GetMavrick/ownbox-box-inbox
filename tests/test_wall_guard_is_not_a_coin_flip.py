"""The wall's append-only guard must give the same answer twice.

WHY THIS FILE EXISTS. `.claude/scripts/wall_diff_check.py` decides whether a DEVSTATE.md change
is a legitimate prune or a stale snapshot clobbering main. On 2026-09-17 it was found to be
NON-DETERMINISTIC: it built its expectation from `sorted(set(old) | set(new), key=<timestamp>)`,
and posts can share a timestamp — OSDev1 posted three at 07:18 UTC that morning. `sorted` is
stable, so tied posts came out in the iteration order of a SET OF STRINGS, which Python
randomises per process. Measured on commit e9eabcc9 against its own parent: SIX RED, FOUR GREEN
across ten hash seeds, on byte-identical input.

THAT IS THE WORST FAILURE A GUARD HAS. Red on correct operation teaches everyone to ignore it,
which is how the real clobber walks through — and a verdict that changes run to run cannot even
be debugged, because reproducing it is luck. Three wall posts went red on main that morning and
nobody noticed, because a wall post is a chore commit nobody watches.

So the first test here is not about pruning at all. It is that the same two files give the same
answer, every time.

Run: python tests/test_wall_guard_is_not_a_coin_flip.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECK = ROOT / ".claude/scripts/wall_diff_check.py"

# THIS SUITE SHIPS INTO EVERY BOX AND HAS NOTHING TO DO THERE. The exporter ships a suite unless
# it imports a machine at module level, and this one imports nothing but the standard library —
# so it travels into a sold box, which carries no `.claude/` at all. It then failed on the one
# thing it cannot have: the guard it exists to test. Caught by OSDev1 in `test_recipe_ships`
# ("every shipped suite passes with the pack installed"), and the same trap had bitten his own
# `/voice` guard an hour earlier, which is why the exit is the shape he named rather than a
# clever one — the same early return the `.github`-reading suites already use.
#
# EXIT 0, NOT A SKIPPED ASSERTION. A box owner running the suite should see one plain line, not a
# passing test that proves nothing about their box; and CI, which does have the file, still runs
# every check below.
if not CHECK.is_file():
    print("  --   no .claude/scripts/wall_diff_check.py here (a box, not the repo) — nothing to test")
    raise SystemExit(0)

FAILS = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


HEAD = "# AIOS DEV STATE\n\n## Messages\n"
TAIL = "\n<!-- trailing comment block -->\n"


def wall(posts):
    """A DEVSTATE.md whose post lines are exactly `posts`, newest first."""
    return HEAD + "".join(p + "\n" for p in posts) + TAIL


def run(old_posts, new_posts, seed=None, keep=None, floor=None):
    """-> (exit code, stdout). Each call is its own process, so each gets its own hash seed."""
    env = dict(os.environ)
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    if keep is not None:
        env["DEVSTATE_KEEP"] = str(keep)
    if floor is not None:
        env["DEVSTATE_FLOOR"] = str(floor)
    with tempfile.TemporaryDirectory() as d:
        o, n = pathlib.Path(d) / "old.md", pathlib.Path(d) / "new.md"
        o.write_text(wall(old_posts), encoding="utf-8")
        n.write_text(wall(new_posts), encoding="utf-8")
        r = subprocess.run([sys.executable, str(CHECK), str(o), str(n)],
                           capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def post(who, hhmm, body="x"):
    return f"[{who}] {hhmm} UTC (Sep 17): [{who}→ALL] FACT: {body}"


# ── 1. the same input gives the same answer ──────────────────────────────────────────────
print("\ntest_the_guard_is_not_a_coin_flip")

# THE EXACT SHAPE THAT BROKE IT: several posts sharing one timestamp, so a set-ordered sort has
# ties to resolve, plus enough posts that the window has to drop some.
TIED = ([post("OSDev1", "07:18", f"tied {i}") for i in range(3)]
        + [post("OSDev4", "07:15"), post("OSDev5", "07:14"), post("OSDev1", "07:11"),
           post("OSDev1", "07:10"), post("OSDev5", "07:09"), post("OSDev4", "07:08"),
           post("OSDev1", "07:05"), post("OSDev4", "07:02"), post("OSDev1", "07:01"),
           post("OSDev0", "06:11"), post("OSDev2", "00:00"), post("OSDev7", "19:16"),
           post("OSDev8", "21:22"), post("OSDev3", "19:45")])
fresh = post("OSDev4", "07:19", "the new one")
# WORKED OUT BY HAND, not by calling the code under test. The prune walks [fresh] + TIED (18
# posts): positions 1-12 survive on position, which is `fresh` plus TIED[:11]. Of what is left,
# TIED[12:] are five authors holding one post each, so every one of them survives on its floor.
# That leaves exactly one casualty — TIED[11], OSDev1's 07:01, whose author spent both floor
# slots higher up the file. It is NOT the oldest post in the wall, and dropping it is correct.
pruned = [fresh] + TIED[:11] + TIED[12:]
ok("the fixture drops exactly one post, and not the oldest",
   len(pruned) == len(TIED) and TIED[11] not in pruned and TIED[-1] in pruned)

verdicts = {run(TIED, pruned, seed=s)[0] for s in range(12)}
ok("TWELVE HASH SEEDS, ONE VERDICT — the bug this file is named for",
   len(verdicts) == 1, f"got {sorted(verdicts)}")

# And it is the RIGHT verdict, not merely a stable wrong one.
ok("...and that verdict is 'allowed', because this prune is the policy", verdicts == {0})


# ── 2. the policy it models is the prune's, not "newest N" ───────────────────────────────
print("\ntest_a_quiet_authors_older_post_outlives_a_loud_authors_newer_one")

# The floor exists because a flat window silences whoever posts least. The consequence the old
# checker got wrong: a quiet dev's OLDER post legitimately survives while a loud dev's NEWER one
# is dropped, so "was anything newer removed?" has no right answer. Only the policy does.
LOUD = [post("OSDev1", f"07:{m:02d}") for m in (30, 29, 28, 27, 26)] + [post("OSDev0", "01:00")]
new = post("OSDev1", "07:31")
# keep=3, floor=2: the top 3 survive on position; OSDev0 survives on its floor although it is
# the OLDEST post in the file; OSDev1's 4th and 5th are dropped although they are newer than it.
kept = [new, LOUD[0], LOUD[1], LOUD[5]]
code, out = run(LOUD, kept, keep=3, floor=2)
ok("the oldest post in the file survives on its author's floor", code == 0, out.strip()[:160])
ok("...and the output calls it a prune", "PRUNE" in out)


# ── 3. it still catches the thing it was built for ───────────────────────────────────────
print("\ntest_a_stale_snapshot_is_still_a_clobber")

# A dev holding a wall from before the last four posts appends one line to THAT and pushes. The
# newest posts vanish. This is what cost 11 posts in #452, 11 in #455 and 12 in #458.
CURRENT = [post("OSDev1", f"08:{m:02d}") for m in (40, 39, 38, 37)] + \
          [post("OSDev5", f"07:{m:02d}") for m in (30, 29, 28, 27)]
stale = [post("OSDev4", "08:41", "written from an old copy")] + CURRENT[4:]
code, out = run(CURRENT, stale)
ok("a snapshot missing the four newest posts is REFUSED", code == 1, out.strip()[:160])
ok("...and the message names what was lost", "08:40" in out)
ok("...and tells the dev how to fix it", "fresh-pull" in out.lower())

# The same refusal must not depend on luck either.
ok("the refusal is deterministic too",
   {run(CURRENT, stale, seed=s)[0] for s in range(8)} == {1})


# ── 4. the easy cases stay easy ──────────────────────────────────────────────────────────
print("\ntest_the_ordinary_cases")

base = [post("OSDev1", "09:00"), post("OSDev5", "08:59")]
code, out = run(base, [post("OSDev4", "09:01")] + base)
ok("a pure addition passes", code == 0 and "pure addition" in out, out.strip()[:120])

code, _ = run([], [post("OSDev4", "09:01")])
ok("the first post on an empty wall passes", code == 0)

code, out = run(base, base)
ok("no change at all passes", code == 0, out.strip()[:120])

# A DELIBERATE TRIM IS STILL REFUSED HERE, and that is correct: the workflow's `wall-rewrite`
# label is the sanctioned way to say "I meant it", and it is checked before this script runs.
code, _ = run(base, [base[0]])
ok("deleting a post the window would have kept is refused", code == 1)


# ── 5. the real commit that started this ─────────────────────────────────────────────────
print("\ntest_the_commit_that_was_red_on_main")

E9 = "e9eabcc9c3a7cd5d903485fec920a04b506f9f1a"     # chore(devstate): OSDev4 wall post
PARENT = "7ee81a89980aec6a1c7ef8a0ca511f45d00d555e"


def blob(rev):
    r = subprocess.run(["git", "show", f"{rev}:DEVSTATE.md"], capture_output=True, text=True,
                       cwd=ROOT)
    return r.stdout if r.returncode == 0 else None


old_txt, new_txt = blob(PARENT), blob(E9)
if old_txt is None or new_txt is None:
    print("  --   those commits are not in this checkout (a box, or a shallow clone) — skipped")
else:
    with tempfile.TemporaryDirectory() as d:
        o, n = pathlib.Path(d) / "o.md", pathlib.Path(d) / "n.md"
        o.write_text(old_txt, encoding="utf-8")
        n.write_text(new_txt, encoding="utf-8")
        codes = set()
        for s in range(10):
            env = dict(os.environ, PYTHONHASHSEED=str(s))
            codes.add(subprocess.run([sys.executable, str(CHECK), str(o), str(n)],
                                     capture_output=True, text=True, env=env).returncode)
    ok("e9eabcc9 — six red and four green before, green on all ten seeds now", codes == {0},
       f"got {sorted(codes)}")


print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_wall_guard_is_not_a_coin_flip is in the workflow's suite list",
       "test_wall_guard_is_not_a_coin_flip" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    [print("   -", f) for f in FAILS]
    sys.exit(1)
print("ALL OK")
