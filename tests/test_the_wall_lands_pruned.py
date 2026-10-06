"""The wall is pruned on the copy that lands, not only on the poster's own (OSDev1, 2026-10-05).

devstate-post.sh prunes the poster's local DEVSTATE.md to the newest 12 posts. devstate-land.sh then ignores
that file: it inserts the one new line into a fresh copy of origin/main and pushes that. So the prune never
reached origin, and by 2026-10-05 the wall was 132 KB, 247 posts, riding in every session's context on every
turn. Two operator messages had also leaked into it above the first post, where nothing could ever drop them.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * landing a post leaves origin's wall longer than the newest 12 posts plus each author's newest 2;
  * a post is cut in half (its header dropped, its lines left, or the other way round);
  * an author who posts rarely loses their newest posts to a loud one;
  * an indented post, or a stray line above the first post, survives every prune;
  * the new post is not first, or the file's top and bottom (rules comment, footer) are touched.

Runs the real script against a bare repository standing in for origin. No network.

Run: python tests/test_the_wall_lands_pruned.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAND = ROOT / ".claude/scripts/devstate-land.sh"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:600]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


# THE WALL IS OURS, NOT THE PRODUCT'S. A built box does not carry the lander, and test_recipe_ships runs every
# shipped suite inside one (this suite's first CI run went red exactly there): absent is a true absence, said so.
if not LAND.exists() or not shutil.which("bash") or not shutil.which("git"):
    print("  --   the wall's lander is not on this box; nothing to check here")
    print("\nALL WALL-LANDS-PRUNED CHECKS PASS")
    raise SystemExit(0)

TOP = ["# AIOS DEV STATE", "<!-- POST: rules. AUTO-PRUNED to the newest 12 posts. -->", "## Messages"]
LEAKED = ["  Grammar (TRIAGE_SPEC §1):  [FROM→TO] TYPE: tl;dr", "OSDev0 intent: services A/B"]
FOOT = ["<!-- footer: kept -->", "the end"]


def wall_with(posts):
    return "\n".join(TOP + LEAKED + posts + FOOT) + "\n"


# 30 posts, newest first: OSDev1 is loud (24), WebDev2 posts rarely (4, all old), one OSDev4 post spans
# three lines, and one of WebDev1's was indented, the way two real ones were on 10-05.
posts = []
for i in range(30, 0, -1):
    if i in (3, 5, 9, 11):
        who = "WebDev2"
    elif i == 20:
        posts += [f"[OSDev4] {i:02d}:00 UTC: [OSDev4→OSDev1] FYI: line one of a long post",
                  "  ...its second line", "  ...and its third"]
        continue
    elif i == 18:
        posts.append(f"  [WebDev1] {i:02d}:00 UTC: [WebDev1→ALL] FYI: an indented post")
        continue
    else:
        who = "OSDev1"
    posts.append(f"[{who}] {i:02d}:00 UTC: [{who}→ALL] FYI: post {i}")

tmp = pathlib.Path(tempfile.mkdtemp())
try:
    origin, clone = tmp / "origin.git", tmp / "clone"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp)
    git("clone", "-q", str(origin), str(clone), cwd=tmp)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git("config", k, v, cwd=clone)
    (clone / "DEVSTATE.md").write_text(wall_with(posts))
    (clone / ".claude/scripts").mkdir(parents=True)
    shutil.copy(LAND, clone / ".claude/scripts/devstate-land.sh")
    git("add", "-A", cwd=clone)
    git("commit", "-qm", "wall", cwd=clone)
    git("push", "-q", "origin", "HEAD:main", cwd=clone)

    NEW = "[OSDev1] 31:00 UTC: [OSDev1→ALL] FYI: the newest post"
    env = {**os.environ, "DEVSTATE_KEEP": "12", "DEVSTATE_FLOOR": "2"}
    r = subprocess.run(["bash", ".claude/scripts/devstate-land.sh", NEW], cwd=clone, env=env,
                       capture_output=True, text=True)
    git("fetch", "-q", "origin", cwd=clone)
    landed = git("show", "origin/main:DEVSTATE.md", cwd=clone).split("\n")

    print("test_the_landed_wall_is_pruned")
    ok("the post lands, once", r.returncode == 0 and "copies on origin: 1" in r.stdout, r.stdout + r.stderr)
    heads = [l for l in landed if l.startswith("[")]
    ok("the new post is first", heads[:1] == [NEW], heads[:2])
    newest = [NEW] + [x.lstrip() for x in posts if x.lstrip().startswith("[")][:11]
    ok("the newest 12 posts are kept, in order", heads[:12] == newest, heads[:12])
    ok("...plus each author's newest two, however old (a quiet dev is never drowned out)",
       [h for h in heads if h.startswith("[WebDev2]")] == ["[WebDev2] 11:00 UTC: [WebDev2→ALL] FYI: post 11",
                                                          "[WebDev2] 09:00 UTC: [WebDev2→ALL] FYI: post 9"], heads)
    ok("...and nothing else: 31 posts become 15 (12, then WebDev1's one and WebDev2's two)",
       len(heads) == 15 and len([h for h in heads if h.startswith("[OSDev1]")]) == 11, heads)

    print("\ntest_whole_posts_only")
    i = landed.index("[OSDev4] 20:00 UTC: [OSDev4→OSDev1] FYI: line one of a long post")
    ok("a kept post keeps all its lines", landed[i + 1:i + 3] == ["  ...its second line", "  ...and its third"],
       landed[i:i + 3])
    r2 = subprocess.run(["bash", ".claude/scripts/devstate-land.sh",
                         "[OSDev2] 32:00 UTC: [OSDev2→ALL] FYI: tighter"], cwd=clone,
                        env={**env, "DEVSTATE_KEEP": "3", "DEVSTATE_FLOOR": "0"}, capture_output=True, text=True)
    git("fetch", "-q", "origin", cwd=clone)
    tight = git("show", "origin/main:DEVSTATE.md", cwd=clone)
    ok("a dropped post drops all its lines, never leaving them behind",
       r2.returncode == 0 and "line one of a long post" not in tight and "its second line" not in tight
       and "and its third" not in tight and tight.count("\n[") == 3, tight)

    print("\ntest_what_nothing_could_prune_before")
    ok("an indented post is a post: kept as its author's newest, at the margin",
       "[WebDev1] 18:00 UTC: [WebDev1→ALL] FYI: an indented post" in landed
       and not any(l.startswith("  [") for l in landed), [l for l in landed if "WebDev1" in l])
    ok("the operator messages that leaked above the first post are gone",
       not any(x in landed for x in LEAKED), landed[:6])
    ok("the rules at the top and the footer are untouched",
       landed[:3] == TOP and landed[-3:-1] == FOOT, (landed[:3], landed[-3:]))

    print("\ntest_a_post_already_landed_changes_nothing")
    before = git("rev-parse", "origin/main", cwd=clone).strip()
    r3 = subprocess.run(["bash", ".claude/scripts/devstate-land.sh", NEW], cwd=clone, env=env,
                        capture_output=True, text=True)
    git("fetch", "-q", "origin", cwd=clone)
    ok("landing the same post twice pushes nothing", r3.returncode == 0
       and git("rev-parse", "origin/main", cwd=clone).strip() == before, r3.stdout + r3.stderr)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\ntest_the_wall_on_main_stays_small")
# THE GUARD THAT WAS MISSING (owner 2026-10-05: "make sure that this never happens again. No wonder I'm always out
# of tokens"). The prune failed silently for weeks; this makes a regrown wall red on every PR the same day.
if (ROOT / ".github").is_dir() and (ROOT / "DEVSTATE.md").exists():   # a box has no repository
    w = (ROOT / "DEVSTATE.md").read_text()
    n = sum(1 for l in w.split("\n") if l.startswith("["))
    ok("the wall in this checkout is bounded (25 KB, 40 posts), so a prune that stops landing goes red here",
       len(w.encode()) <= 25000 and n <= 40, f"{len(w.encode())} bytes, {n} posts")

print("\ntest_the_suite_runs_in_ci")
if (ROOT / ".github").is_dir():                         # a box has no repository
    ok("test_the_wall_lands_pruned is in the workflow's suite list",
       "test_the_wall_lands_pruned \\" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL WALL-LANDS-PRUNED CHECKS PASS" if not _failed else f"\n{_failed} WALL-LANDS-PRUNED CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
