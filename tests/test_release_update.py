"""The box's update decision: fetch, verify, install the newest release that passes — and never go backwards.

Each case below is something that happens to a box in the field: a normal release, a junk tag published
above a good one, a source that tries to move a tag the box already has, a trust-file rotation, a box
with nothing new, a box that has lost its trust file. The box under test is a real git checkout sitting
on a signed release, fed from a remote path and from a bundle file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.release.update import choose, installed_release  # noqa: E402
from core.release.verify import TRUST_PATH  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


missing = [t for t in ("git", "ssh-keygen") if not shutil.which(t)]
if missing:
    # A SKIP THAT EXITS 0 IS A SUITE THAT PASSED HAVING RUN NOTHING, and CI's loop cannot tell the two
    # apart (OSDev5 hit exactly that reproducing a red on a container without ssh-keygen). Outside CI a
    # missing tool is a skip; in CI the tools are part of the contract, so it is a failure.
    print(("FAIL" if os.environ.get("CI") else "SKIP") + f": {', '.join(missing)} not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

T = Path(tempfile.mkdtemp())
# No install marker unless a section sets one, even when this suite runs inside a box that has one.
os.environ["AIOS_RELEASE_FILE"] = str(T / "no-marker")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
GIT = ["git", "-c", "user.name=release", "-c", "user.email=release@ownbox.test", "-c", "gpg.format=ssh",
       "-c", "advice.detachedHead=false"]


def run(*args, cwd=None, check=True):
    r = subprocess.run(list(args), cwd=cwd, env=ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def key(name):
    p = T / name
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(p))
    return p, " ".join((T / f"{name}.pub").read_text().split()[:2])


ROOT_KEY, ROOT_PUB = key("root")
CI_KEY, CI_PUB = key("ci")
NEW_KEY, NEW_PUB = key("ci2")
ROGUE_KEY, _ = key("rogue")
TRUST = f'root-active@ownbox namespaces="git" {ROOT_PUB}\nci@ownbox namespaces="git" {CI_PUB}\n'

SRC = T / "src"
SRC.mkdir()


def g(*args, cwd=SRC, check=True):
    return run(*GIT, *args, cwd=cwd, check=check)


def commit(msg, files):
    for rel, text in files.items():
        (SRC / rel).parent.mkdir(parents=True, exist_ok=True)
        (SRC / rel).write_text(text)
    g("add", "-A"); g("commit", "-q", "-m", msg)


def sign(tag, keyfile):
    g("-c", f"user.signingkey={keyfile}", "tag", "-s", tag, "-m", tag)


g("init", "-q")
commit("r1", {TRUST_PATH: TRUST, "app.py": "v1\n"})
sign("release/2026.09.15.1", CI_KEY)

BOX = T / "box"
run("git", "clone", "-q", str(SRC), str(BOX))
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.15.1", cwd=BOX)
LOG = T / "updates.jsonl"


def box_head_tag():
    return installed_release(BOX)


print("\n— the ordinary day —")
ok("the box knows which release it runs", box_head_tag() == "release/2026.09.15.1", str(box_head_tag()))
d = choose(BOX, str(SRC), log_path=LOG)
ok("nothing newer: up to date, nothing selected", d.status == "up_to_date" and d.selected is None, str(d))
commit("r2", {"app.py": "v2\n"})
sign("release/2026.09.16.1", CI_KEY)
d = choose(BOX, str(SRC), log_path=LOG)
ok("a newer signed release is selected, with who signed it",
   d.status == "selected" and d.selected == "release/2026.09.16.1" and d.principal == "ci@ownbox", str(d))
run(*GIT, "checkout", "-q", "--detach", d.selected, cwd=BOX)

print("\n— a junk tag published above a good one cannot freeze the box —")
commit("r3", {"app.py": "v3\n"})
sign("release/2026.09.17.1", CI_KEY)
commit("junk", {"app.py": "evil\n"})
g("tag", "-a", "release/2026.09.18.1", "-m", "unsigned")
d = choose(BOX, str(SRC), log_path=LOG)
ok("the newest valid release is installed and the unsigned one above it is refused and logged",
   d.status == "selected" and d.selected == "release/2026.09.17.1"
   and [r["reason"] for r in d.refused] == ["unsigned"], str(d))
run(*GIT, "checkout", "-q", "--detach", d.selected, cwd=BOX)
g("tag", "-d", "release/2026.09.18.1")

print("\n— everything newer is bad —")
sign("release/2026.09.18.2", ROGUE_KEY)
d = choose(BOX, str(SRC), log_path=LOG)
ok("all_refused, nothing selected, and the box stays on the release it runs",
   d.status == "all_refused" and d.selected is None and box_head_tag() == "release/2026.09.17.1", str(d))
g("tag", "-d", "release/2026.09.18.2")
run("git", "-C", str(BOX), "tag", "-d", "release/2026.09.18.2")

print("\n— a source that tries to MOVE a release tag the box already holds —")
evil = T / "evil"
run("git", "clone", "-q", str(SRC), str(evil))
(evil / "app.py").write_text("moved\n")
run(*GIT, "commit", "-qam", "moved", cwd=evil)
run(*GIT, "-c", f"user.signingkey={CI_KEY}", "tag", "-f", "-s", "release/2026.09.16.1", "-m", "release/2026.09.16.1", cwd=evil)
held = run("git", "-C", str(BOX), "rev-parse", "refs/tags/release/2026.09.16.1").stdout.strip()
d = choose(BOX, str(evil), log_path=LOG)
now = run("git", "-C", str(BOX), "rev-parse", "refs/tags/release/2026.09.16.1").stdout.strip()
ok("the box keeps the tag it had and reports the attempt as tampering", now == held and d.tamper, str(d))

print("\n— rotating the everyday key needs a root signature —")
rotated = TRUST + f'ci2@ownbox namespaces="git" {NEW_PUB}\n'
commit("rotate", {TRUST_PATH: rotated})
sign("release/2026.09.19.1", CI_KEY)
d = choose(BOX, str(SRC), log_path=LOG)
ok("a trust change signed by the everyday key is refused",
   d.status == "all_refused" and d.refused[0]["reason"] == "trust_change_needs_root", str(d))
g("tag", "-d", "release/2026.09.19.1"); run("git", "-C", str(BOX), "tag", "-d", "release/2026.09.19.1")
sign("release/2026.09.19.2", ROOT_KEY)
d = choose(BOX, str(SRC), log_path=LOG)
ok("the same change signed by the root key is selected", d.status == "selected" and d.principal == "root-active@ownbox", str(d))
run(*GIT, "checkout", "-q", "--detach", d.selected, cwd=BOX)
commit("r5", {"app.py": "v5\n"})
sign("release/2026.09.20.1", NEW_KEY)
d = choose(BOX, str(SRC), log_path=LOG)
ok("once installed, the NEW key is trusted for the next release — the box reads its own installed trust file",
   d.status == "selected" and d.principal == "ci2@ownbox", str(d))

print("\n— air-gapped: the same decision from a bundle file —")
bundle = T / "r.bundle"
g("bundle", "create", str(bundle), "--all")
box2 = T / "box2"
run("git", "clone", "-q", str(SRC), str(box2))
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.19.2", cwd=box2)
via_bundle = choose(box2, str(bundle), log_path=None)
ok("a bundle-fed box selects exactly what the remote-fed box selected",
   (via_bundle.status, via_bundle.selected) == (d.status, d.selected), f"{via_bundle} vs {d}")

print("\n— a box that has lost its trust file installs nothing —")
(BOX / TRUST_PATH).unlink()
d = choose(BOX, str(SRC), log_path=LOG)
ok("no trust file: cannot_run, nothing selected", d.status == "cannot_run" and d.selected is None, str(d))
run(*GIT, "checkout", "-q", "--", TRUST_PATH, cwd=BOX)

print("\n— HEAD moved off its tag: the install marker is the downgrade check's memory —")
# Measured on the live box 2026-09-15: a periodic fast-forwarded the checkout off its release tag and
# the next decision logged `current: null`. A source that then offers only OLDER signed releases must
# not be able to walk the box back to one of them.
old_bundle = T / "old.bundle"
g("bundle", "create", str(old_bundle), "refs/tags/release/2026.09.15.1", "refs/tags/release/2026.09.16.1")
box3 = T / "box3"
run("git", "clone", "-q", "--no-checkout", str(old_bundle), str(box3))
run(*GIT, "checkout", "-q", "--detach", "release/2026.09.16.1", cwd=box3)
(box3 / "app.py").write_text("moved by something that is not a release\n")
run(*GIT, "commit", "-qam", "a commit no release signed", cwd=box3)
marker = T / "release-marker"
marker.write_text("release/2026.09.20.1\n")
ok("off its tag with no marker, the box cannot say what it runs", installed_release(box3, T / "no-marker") is None)
ok("with the marker box_update.sh wrote, it can", installed_release(box3, marker) == "release/2026.09.20.1")
os.environ["AIOS_RELEASE_FILE"] = str(marker)
d = choose(box3, str(old_bundle), log_path=None)
ok("a source offering only older signed releases installs NOTHING", d.selected is None and d.current == "release/2026.09.20.1", str(d))
os.environ["AIOS_RELEASE_FILE"] = str(T / "no-marker")
d = choose(box3, str(old_bundle), log_path=None)
ok("...and without the marker the same source WOULD have walked the box back (the hole this closes)",
   d.selected == "release/2026.09.16.1", str(d))

print("\n— the record —")
lines = [json.loads(l) for l in LOG.read_text().splitlines()]
ok("every decision is one JSON line in the update log", len(lines) >= 9, str(len(lines)))
ok("each line says what happened, from where, and when",
   all({"at", "source", "status"} <= set(l) for l in lines))
ok("refusals keep their reasons in the log", any(r.get("reason") == "unsigned" for l in lines for r in l["refused"]))

print()
shutil.rmtree(T, ignore_errors=True)
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
