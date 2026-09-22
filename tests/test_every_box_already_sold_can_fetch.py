"""A box whose ssh key was never registered heals itself on the next tick.

MEASURED 2026-09-22 on a live box: the image mints /var/lib/aios/update_key for every box and
sets origin to `git@github.com:GetMavrick/ownbox-box-inbox.git`, but nothing ever registered that
key on the repository. Every fetch was "Permission denied (publickey)", so the twice-daily
updater had never installed anything on any box in the field — a security fix could not have
reached one. Those boxes are already sold. They cannot be edited one by one, and an image cut
only helps the boxes sold after it, so the repair has to be something the boxes already out there
do for themselves the next time the timer fires.

WHAT THIS MEASURES. `https_mirror_of` derives the public https URL of the SAME repository from the
ssh one — a derivation, never a configured value, so nothing an attacker can write on a box points
it at someone else's tags. `choose` tries that door ONLY after the configured one has failed, and
the signature check is untouched either way: the transport carries no trust and never did.

THE SUBSTITUTION. Three cases need a mirror that actually answers, and a test must not reach
GitHub, so they replace `https_mirror_of` with one that returns a local signed repository. That
function's real behaviour is measured on its own, exhaustively, in the first section — the two
halves together are the claim.

Run: python tests/test_every_box_already_sold_can_fetch.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.release import update as up                          # noqa: E402
from core.release.update import choose, https_mirror_of        # noqa: E402
from core.release.verify import TRUST_PATH                     # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen") if not shutil.which(t)]
if missing:
    # A SKIP THAT EXITS 0 IS A SUITE THAT PASSED HAVING RUN NOTHING. In CI the tools are part of
    # the contract, so their absence is a failure, not a shrug.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found")
    raise SystemExit(1 if os.environ.get("CI") else 0)


# ── 1. the derivation, exhaustively ─────────────────────────────────────────────────────────
print("test_the_mirror_is_derived_from_the_same_repository_and_nothing_else")
MIRRORS = "https://github.com/GetMavrick/ownbox-box-inbox.git"
for src, want in (
    ("git@github.com:GetMavrick/ownbox-box-inbox.git", MIRRORS),
    ("git@github.com:GetMavrick/ownbox-box-inbox", MIRRORS),           # as git remote may hold it
    ("  git@github.com:GetMavrick/ownbox-box-inbox.git  ", MIRRORS),   # whitespace from a .env line
    ("git@github.com:GetMavrick/ownbox-box-lead-2.git",
     "https://github.com/GetMavrick/ownbox-box-lead-2.git"),
):
    ok(f"{src.strip()} -> the same repository over https", https_mirror_of(src) == want,
       https_mirror_of(src))

for src in (
    "git@github.com:Someone/ownbox-box-inbox.git",        # somebody else's repository
    "git@github.com:GetMavrick/AIOS.git",                 # the monorepo: a box never follows it
    "git@github.com:GetMavrick/ownbox-box-Inbox.git",     # not the slug alphabet
    "git@evil.example.com:GetMavrick/ownbox-box-inbox.git",
    "https://github.com/GetMavrick/ownbox-box-inbox.git",  # already https: no second door needed
    "ssh://git@github.com/GetMavrick/ownbox-box-inbox.git",
    "/srv/mirror/box.bundle", "origin", "",
):
    ok(f"no mirror for {src or '<empty>'}", https_mirror_of(src) == "", https_mirror_of(src))


# ── a signed world ──────────────────────────────────────────────────────────────────────────
T = Path(tempfile.mkdtemp())
os.environ["AIOS_RELEASE_FILE"] = str(T / "no-marker")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
GIT = ["git", "-c", "user.name=release", "-c", "user.email=release@ownbox.test",
       "-c", "gpg.format=ssh", "-c", "advice.detachedHead=false"]


def run(*args, cwd=None, check=True):
    r = subprocess.run(list(args), cwd=cwd, env=ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def key(name):
    p = T / name
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(p))
    return p, " ".join((T / f"{name}.pub").read_text().split()[:2])


CI_KEY, CI_PUB = key("ci")
ROGUE_KEY, _ = key("rogue")
TRUST = f'ci@ownbox namespaces="git" {CI_PUB}\n'

SRC = T / "mirror"                       # stands in for the public https repository
SRC.mkdir()


def g(*args, cwd=SRC, check=True):
    return run(*GIT, *args, cwd=cwd, check=check)


def commit(msg, files, cwd=SRC):
    for rel, text in files.items():
        (cwd / rel).parent.mkdir(parents=True, exist_ok=True)
        (cwd / rel).write_text(text)
    run(*GIT, "add", "-A", cwd=cwd); run(*GIT, "commit", "-q", "-m", msg, cwd=cwd)


g("init", "-q")
commit("r1", {TRUST_PATH: TRUST, "app.py": "v1\n"})
g("-c", f"user.signingkey={CI_KEY}", "tag", "-s", "release/2026.09.15.1", "-m", "r1")

BOX = T / "box"
run("git", "clone", "-q", str(SRC), str(BOX))
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.15.1", cwd=BOX)
LOG = T / "updates.jsonl"

commit("r2", {"app.py": "v2\n"})
g("-c", f"user.signingkey={CI_KEY}", "tag", "-s", "release/2026.09.16.1", "-m", "r2")

DEAD = "git@github.com:GetMavrick/ownbox-box-inbox.git"   # the origin every sold box carries
_real_mirror = up.https_mirror_of


def mirror_is(path):
    up.https_mirror_of = lambda s: str(path) if s == DEAD else ""


def last_log():
    return json.loads(LOG.read_text().strip().splitlines()[-1])


# ── 2. the box in the field ─────────────────────────────────────────────────────────────────
print("\ntest_a_box_whose_key_was_never_registered_still_gets_the_release")
mirror_is(SRC)
d = choose(BOX, DEAD, log_path=LOG)
ok("the newer signed release is selected", d.status == "selected"
   and d.selected == "release/2026.09.16.1", str(d))
ok("...and the box records which door served it", d.served == str(SRC), d.served)
ok("...while the log still says what it was CONFIGURED to use, so the real fault stays visible",
   last_log()["source"] == DEAD and last_log()["served"] == str(SRC), str(last_log()))

print("test_the_source_a_real_box_passes_is_the_word_origin_not_a_url")
# `scripts/box_update.sh` passes the literal "origin" unless .env names a source. Deriving a
# mirror straight from that string finds nothing, so this shipped green and did nothing until the
# live box was asked. The remote is resolved to its url first.
run(*GIT, "remote", "set-url", "origin", DEAD, cwd=BOX)
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.15.1", cwd=BOX)
d = choose(BOX, "origin", log_path=LOG)
ok("a box updating from its own remote NAME still reaches the mirror",
   d.status == "selected" and d.selected == "release/2026.09.16.1", str(d))
ok("...and says which door served it", d.served == str(SRC), d.served)

print("test_the_signature_is_still_what_decides_whatever_door_it_came_through")
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.16.1", cwd=BOX)
commit("r3", {"app.py": "v3\n"})
g("-c", f"user.signingkey={ROGUE_KEY}", "tag", "-s", "release/2026.09.17.1", "-m", "rogue")
d = choose(BOX, DEAD, log_path=LOG)
ok("a release signed by a key the box does not trust is refused, mirror or not",
   d.status == "all_refused" and [r["reason"] for r in d.refused] == ["untrusted_signer"], str(d))
g("tag", "-d", "release/2026.09.17.1")
run(*GIT, "tag", "-d", "release/2026.09.17.1", cwd=BOX, check=False)

print("test_a_working_primary_is_never_second_guessed")
# A box whose key DOES work must not take this path at all: the mirror here is a directory that
# does not exist, so consulting it would be visible.
mirror_is(T / "does-not-exist")
commit("r4", {"app.py": "v4\n"})
g("-c", f"user.signingkey={CI_KEY}", "tag", "-s", "release/2026.09.18.1", "-m", "r4")
d = choose(BOX, str(SRC), log_path=LOG)
ok("the configured source serves, and `served` says so",
   d.status == "selected" and d.selected == "release/2026.09.18.1" and d.served == str(SRC), str(d))

print("test_a_source_that_tries_to_move_a_tag_is_still_reported_when_the_mirror_succeeds")
# THE SIGNAL MUST SURVIVE THE FALLBACK. A primary that answers and tries to move a tag the box
# already holds has said something about itself; a second fetch succeeding must not wash it away.
EVIL = T / "evil"
run("git", "clone", "-q", str(SRC), str(EVIL))
run(*GIT, "tag", "-d", "release/2026.09.16.1", cwd=EVIL)
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.15.1", cwd=EVIL)
commit("swapped", {"app.py": "swapped\n"}, cwd=EVIL)
run(*GIT, "-c", f"user.signingkey={CI_KEY}", "tag", "-s", "release/2026.09.16.1", "-m", "swapped",
    cwd=EVIL)
mirror_is(SRC)
d = choose(BOX, str(EVIL), log_path=LOG)
ok("the attempt to move a tag we hold is reported", d.tamper, str(d))

print("test_when_both_doors_are_shut_the_box_says_so_and_installs_nothing")
mirror_is(T / "also-missing")
d = choose(BOX, str(T / "gone"), log_path=LOG)
ok("cannot_run, with the reason", d.status == "cannot_run" and "fetch failed" in d.detail, str(d))
ok("...and nothing was selected", d.selected is None, str(d))

print("test_a_box_repo_that_is_not_ours_gets_no_second_door")
up.https_mirror_of = _real_mirror
d = choose(BOX, "git@github.com:Someone/ownbox-box-inbox.git", log_path=LOG)
ok("no mirror is tried for somebody else's repository",
   d.status == "cannot_run" and d.served == "git@github.com:Someone/ownbox-box-inbox.git", str(d))

print("test_the_updater_never_waits_on_a_prompt")
src = Path(up.__file__).read_text()
ok("_git runs git with GIT_TERMINAL_PROMPT=0", 'GIT_TERMINAL_PROMPT": "0"' in src)

print("\nFAILED" if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
