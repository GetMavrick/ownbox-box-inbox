"""A wall post lands exactly as it was sent: backslashes, percent signs, quotes and all.

WHAT HAPPENED, 2026-09-24. `devstate-post.sh` inserted the post with `awk -v entry="$ENTRY"`, and
`-v` decodes backslash escapes. A post quoting the CSS `content:"\\2212"` was written with `\\221`
turned into the byte 0x91, followed by a "2". DEVSTATE.md stopped being valid UTF-8 (a Python
reader raised UnicodeDecodeError and the watcher raised a false "wall clobbered" alarm). The
script's own check then could not find the text it had sent and printed "POST DID NOT LAND —
header missing", though the post had landed. Its author believed it and posted again, so the
wall carried it twice.

This suite posts to a throwaway wall and holds:
  1. the post is in the file byte for byte, the file is still valid UTF-8, and the script says it
     succeeded. This holds for EVERY wall script there is: ours, and the one a box ships to its
     buyer (docs/box/foundation), which is a different, smaller script;
  2. ours only, since only ours has a lander: the "land it" command it prints for an uncommitted
     post, pasted into bash, hands the lander exactly the line that was written;
  3. ours only: a missing header is still reported as a missing header, and an altered post is not.

Run: python tests/test_the_wall_takes_a_post_verbatim.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POST = ROOT / ".claude" / "scripts" / "devstate-post.sh"
# A BOX SHIPS A DIFFERENT SCRIPT UNDER THE SAME NAME. test_recipe_ships runs this suite inside an
# exported box, where `.claude/scripts/devstate-post.sh` is the buyer's foundation copy and there
# is no lander (first CI run of this suite, 2026-09-24). So what is universal is checked on every
# script present, and what belongs to ours only where ours is.
OURS = (POST.parent / "devstate-land.sh").exists()
FOUNDATION = ROOT / "docs" / "box" / "foundation" / "claude" / "scripts" / "devstate-post.sh"
SCRIPTS = [POST] + ([FOUNDATION] if FOUNDATION.exists() else [])
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# THE WALL IS OURS, NOT THE PRODUCT'S. A built box may not carry `.claude/scripts`, and
# test_recipe_ships runs every shipped suite inside one: absent is a true absence, said so.
if not POST.exists() or not shutil.which("bash"):
    print("  --   the wall's tooling is not on this box; nothing to check here")
    print("\nALL OK")
    raise SystemExit(0)

WALL = "# AIOS DEV STATE\n\n## Messages\n[OSDev1] 00:00 UTC (Sep 24): [OSDev1→ALL] FYI: an older post.\n"
# Every character that bit, or could: a CSS escape, a literal backslash-n, printf's %, both
# quotes, a dollar, a backtick, and the arrow the grammar uses.
MSG = ('[OSDev9→OSDev1] FYI: the minus was content:"\\2212", a literal \\n and \\t, 100% sure, '
       "it's $HOME and `date` and \"quoted\".")


def wall(git: bool) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".claude").mkdir()
    (d / "DEVSTATE.md").write_text(WALL, encoding="utf-8")
    if git:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["init", "-q", "-b", "main"], ["add", "DEVSTATE.md"], ["commit", "-qm", "wall"]):
            subprocess.run(["git", "-C", str(d), *cmd], check=True, env=env, capture_output=True)
    return d


def post(d: Path, msg: str, script: Path = POST) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(script), "OSDev9", msg], capture_output=True, text=True,
                          env={**os.environ, "AIOS_DIR": str(d)}, timeout=60)


for script in SCRIPTS:
    name = script.relative_to(ROOT)
    print(f"\n— a post with backslashes lands as sent: {name} —")
    d = wall(git=False)
    r = post(d, MSG, script)
    raw = (d / "DEVSTATE.md").read_bytes()
    try:
        text = raw.decode("utf-8")
        ok("the wall is still valid UTF-8", True)
    except UnicodeDecodeError as e:
        text = ""
        ok("the wall is still valid UTF-8", False, str(e))
    ok("the script says it succeeded", r.returncode == 0 and "✓" in r.stdout,
       f"rc={r.returncode} {r.stdout[-300:]}")
    line = next((l for l in text.split("\n") if l.startswith("[OSDev9]")), "")
    ok("the post is in the file byte for byte", line.endswith(": " + MSG), repr(line[-120:]))
    ok("...under the header, above the older post",
       all(k in text for k in ("## Messages", "[OSDev9]", "[OSDev1]"))
       and text.index("## Messages") < text.index("[OSDev9]") < text.index("[OSDev1]"))
    ok("no control byte was invented on the way in",
       not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", text))

if not OURS:
    print("\n  --   no lander on this box, so the land-it command and our messages are not here")
    print("\n" + ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    sys.exit(1 if FAILS else 0)

print("\n— the land-it command carries the same line —")
d = wall(git=True)
r = post(d, MSG)
written = next((l for l in (d / "DEVSTATE.md").read_text(encoding="utf-8", errors="replace").split("\n")
                if l.startswith("[OSDev9]")), "")
out = r.stdout.split("\n")
at = next((i for i, l in enumerate(out) if "devstate-land.sh" in l), None)
ok("an uncommitted post prints the command that lands it", at is not None and at + 1 < len(out),
   r.stdout[-400:])
if at is not None and at + 1 < len(out):
    # PASTE IT INTO BASH, which is what a person does, and see what the lander would receive.
    arg = subprocess.run(["bash", "-c", "printf %s " + out[at + 1].strip()],
                         capture_output=True, text=True).stdout
    ok("pasted into bash, it is exactly the line in the file", arg == written,
       f"\n     got  {arg[-90:]!r}\n     want {written[-90:]!r}")

print("\n— a failure says which failure it was —")
d = wall(git=False)
(d / "DEVSTATE.md").write_text("# AIOS DEV STATE\n\nno header here\n", encoding="utf-8")
r = post(d, "[OSDev9→ALL] FYI: plain.")
ok("a missing header is reported as a missing header",
   r.returncode == 1 and "header missing" in r.stdout, r.stdout[-200:])
live = "\n".join(l for l in POST.read_text().split("\n") if not l.lstrip().startswith("#"))
ok("the text never passes through awk -v, which decodes escapes",
   not re.search(r"awk\s+-v\s+entry=", live))
ok("an altered post is not blamed on the header", "POST ALTERED" in live)

print("\n" + ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
