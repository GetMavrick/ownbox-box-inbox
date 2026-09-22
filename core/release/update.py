"""Choose the release this box should install: fetch release tags, verify, pick the newest that passes.

WHAT A BOX DOES TO UPDATE, IN ORDER, AND WHY EACH STEP IS WHERE IT IS.

  1. Read the trust file and the installed release from the CURRENT checkout, before touching anything.
     The candidate is exactly what is being judged; its own trust file must never decide whether it is
     trusted.
  2. Fetch release tags only — never a branch — from a remote URL or a bundle file, with the same
     command. A non-forced refspec: a source that tries to MOVE an existing release tag is refused by git
     and reported here as tampering, and the box keeps the tag it already had.
  3. Consider only releases newer than the one installed, newest first, and verify each with
     core/release/verify.py. Install the newest one that passes. A newer release that fails (unsigned,
     wrong key, rewritten) is logged and skipped, so a junk tag cannot freeze a box's updates — and a
     box never moves backwards, because older releases are not candidates at all.
  4. Record every decision — installed, refused and why, up to date — as one JSON line in the update log.
     Enterprise buyers ask what changed on the box and who signed it; this is the answer.

WHAT IT DOES NOT DO. It does not check out, install packages, migrate or restart. It returns the tag,
and scripts/box_update.sh does the rest, so the decision and the installation are separately testable
and a refusal can never leave a half-installed box.

Exit codes for the CLI: 0 = a release was selected (its tag is the last stdout line) or already up to
date (prints nothing); 2 = newer releases exist and every one was refused; 1 = cannot run (no trust file,
not a git tree, fetch failed outright).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.release.verify import TRUST_PATH, verify_release, version_of

REFSPEC = "refs/tags/release/*:refs/tags/release/*"     # no leading '+': never move a tag we hold


@dataclass
class Decision:
    status: str                         # "selected" | "up_to_date" | "all_refused" | "cannot_run"
    current: str | None = None
    selected: str | None = None
    principal: str | None = None
    refused: list[dict] = field(default_factory=list)
    tamper: list[str] = field(default_factory=list)
    detail: str = ""
    served: str = ""                    # the source the tags actually came from (see https_mirror_of)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # NEVER PROMPT. Over https git asks for a username when a repository is private or missing, and
    # this runs from a timer with nobody to answer: the fetch would sit until the unit timeout with
    # nothing in the log. With prompts off it fails in a second with the real reason.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)


# The ssh form every box built from an image carries, and the public https form of the SAME
# repository. Only these two shapes — a mirror is derived, never configured, so nothing can point
# a box at somebody else's tags by editing a file on it.
_SSH_BOX = re.compile(r"^git@github\.com:(GetMavrick/ownbox-box-[a-z0-9-]+?)(?:\.git)?$")


def _url_of(repo: Path, source: str) -> str:
    """`source` as a URL: a git remote NAME resolved to its url, anything else unchanged."""
    r = _git(repo, "remote", "get-url", source)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else source


def https_mirror_of(source: str) -> str:
    """The public https URL for the SAME box repository, or "" when `source` is not an ssh box repo.

    WHY A BOX NEEDS THIS. Every box sold before 2026-09-22 was built from an image whose origin is
    the ssh URL, and the per-box key that origin needs was never registered on the repository —
    measured on a live box that day: every fetch was "Permission denied (publickey)", so the
    twice-daily updater had never installed anything on any box in the field, and a security fix
    could not have reached one. Those boxes cannot be edited one by one; they have to heal
    themselves on the next tick.

    WHY IT IS SAFE. The transport carries no trust here and never did: `verify_release` checks the
    signed tag against the trust file the box already holds, whichever way the bytes arrived. What
    changes is only which door the same tags come through.
    """
    m = _SSH_BOX.match(str(source or "").strip())
    return f"https://github.com/{m.group(1)}.git" if m else ""


RELEASE_FILE = "/var/lib/aios/release"   # written by scripts/box_update.sh after each verified install


def installed_release(repo: Path, release_file: str | Path | None = None) -> str | None:
    """The release this box last installed and verified: HEAD's release tag, else the install marker.

    THE MARKER IS THE DOWNGRADE CHECK'S MEMORY. With HEAD not on a release tag and no marker, there is
    nothing to compare a candidate against, and the verifier accepts ANY signed release, older ones
    included; a source offering only an old release with a known hole would "update" the box to it.
    HEAD leaves its tag more easily than it sounds: measured on the live box 2026-09-15, a periodic
    fast-forwarded the checkout off release/2026.09.15.2 and the next decision logged `current: null`.
    The marker is read at call time (AIOS_RELEASE_FILE), so a test can point it away.
    """
    r = _git(repo, "describe", "--tags", "--exact-match", "--match", "release/*", "HEAD")
    tag = r.stdout.strip() if r.returncode == 0 else ""
    if version_of(tag):
        return tag
    path = Path(release_file or os.environ.get("AIOS_RELEASE_FILE", RELEASE_FILE))
    try:
        marked = path.read_text().strip()
    except OSError:
        return None
    return marked if version_of(marked) else None


def choose(repo: str | Path, source: str, *, log_path: str | Path | None = None,
           now: datetime | None = None) -> Decision:
    repo = Path(repo)
    if _git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return _log(Decision("cannot_run", detail=f"{repo} is not a git work tree"), log_path, source)

    trust = repo / TRUST_PATH
    if not trust.is_file():
        return _log(Decision("cannot_run", detail=f"no {TRUST_PATH} in the installed release — nothing to "
                             "verify against, so nothing is installed"), log_path, source)
    pinned = Path(_tmp_copy(trust))
    current = installed_release(repo)

    before = {t: _git(repo, "rev-parse", f"refs/tags/{t}").stdout.strip() for t in _tags(repo)}
    fetched = _git(repo, "fetch", "--no-tags", source, REFSPEC)
    served, output = source, fetched.stdout + fetched.stderr
    # THE SAME REPOSITORY THROUGH THE OTHER DOOR, and only after the configured one has failed. A
    # box whose key works never takes this path and nothing about it changes.
    if fetched.returncode != 0:
        # A REMOTE NAME FIRST. `scripts/box_update.sh` passes the literal "origin" unless .env names
        # a source, so deriving straight from `source` would find no mirror on the very boxes this
        # exists for — green, and doing nothing. Measured on the live box before it was fixed.
        mirror = https_mirror_of(_url_of(repo, source))
        if mirror:
            retry = _git(repo, "fetch", "--no-tags", mirror, REFSPEC)
            output += retry.stdout + retry.stderr
            if retry.returncode == 0:
                fetched, served = retry, mirror
    # TAMPER IS READ FROM BOTH ATTEMPTS. A source that tried to move a tag we hold has said
    # something about itself, and a second fetch succeeding must not wash that away.
    tamper = [line.strip() for line in output.splitlines()
              if "would clobber" in line or "[rejected]" in line]
    if fetched.returncode != 0 and not tamper:
        pinned.unlink(missing_ok=True)
        return _log(Decision("cannot_run", current=current, served=served,
                             detail=f"fetch failed: {fetched.stderr.strip()[:200]}"), log_path, source)
    moved = [t for t, sha in before.items() if _git(repo, "rev-parse", f"refs/tags/{t}").stdout.strip() != sha]
    tamper += [f"local tag {t} changed during fetch" for t in moved]

    floor = version_of(current) if current else None
    candidates = sorted((t for t in _tags(repo) if floor is None or version_of(t) > floor),
                        key=version_of, reverse=True)
    if not candidates:
        pinned.unlink(missing_ok=True)
        return _log(Decision("up_to_date", current=current, tamper=tamper, served=served), log_path, source)

    refused = []
    for tag in candidates:
        v = verify_release(repo, tag, pinned, current_tag=current, now=now)
        if v.ok:
            pinned.unlink(missing_ok=True)
            return _log(Decision("selected", current=current, selected=tag, principal=v.principal,
                                 refused=refused, tamper=tamper, served=served), log_path, source)
        refused.append({"tag": tag, "reason": v.reason, "detail": v.detail[:160]})
    pinned.unlink(missing_ok=True)
    return _log(Decision("all_refused", current=current, refused=refused, tamper=tamper,
                         served=served), log_path, source)


def _tags(repo: Path) -> list[str]:
    out = _git(repo, "tag", "-l", "release/*").stdout.split()
    return [t for t in out if version_of(t)]


def _tmp_copy(path: Path) -> str:
    import tempfile
    fd, name = tempfile.mkstemp(prefix="allowed_signers.")
    with open(fd, "w") as f:
        f.write(path.read_text())
    return name


def _log(d: Decision, log_path, source: str) -> Decision:
    if log_path:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now(timezone.utc).isoformat(), "source": source, **d.__dict__}
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return d


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Select the newest verified release for this box.")
    ap.add_argument("--repo", default="/opt/aios")
    ap.add_argument("--source", required=True, help="a git remote URL or a path to a bundle file")
    ap.add_argument("--log", default="/var/lib/aios/updates.jsonl")
    a = ap.parse_args(argv)
    d = choose(a.repo, a.source, log_path=a.log)
    for r in d.refused:
        print(f"refused {r['tag']}: {r['reason']}")
    for t in d.tamper:
        print(f"TAMPER: {t}")
    if d.status == "selected":
        print(d.selected)
        return 0
    if d.status == "up_to_date":
        return 0
    print(d.detail or d.status)
    return 2 if d.status == "all_refused" else 1


if __name__ == "__main__":
    raise SystemExit(main())
