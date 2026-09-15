"""Which commit is this box actually running — measured, not inferred.

WHY THIS EXISTS. "How far behind is the box?" has been answered all week by reading the last
DEVSTATE post and treating it as the box's state. That is not a measurement, it is a report of
somebody's last report, and it has been wrong every time somebody deployed without posting. The
cost is not the wrong number: it is that a real "this is not deployed yet" and a stale wall look
identical, so a genuinely undeployed fix gets waved through and a deployed one gets re-deployed.

TWO SHAS, AND THE DIFFERENCE IS THE INTERESTING PART:

    commit    the SHA when this PROCESS started — the code actually executing
    on_disk   the SHA in the working tree RIGHT NOW

`git pull` moves the second and not the first. That gap is the failure nobody can see today: the
deploy "succeeded", the files are current, and the running worker is still on last week's code
until something restarts it. `restart_needed` names it.

STDLIB ONLY, AND NO `git` BINARY. This is read by `/health`, which the watchdog hits on a timer
and which must never be the thing that breaks — a subprocess per request is both slower and one
more way to fail. `.git` is a documented file format; reading it is a few lines.

NEVER RAISES. A deploy from a tarball has no `.git` at all, which is a legitimate way to run a
clone. It reports `None` and the endpoint says "unknown" — an unknown SHA is a smaller problem
than a health check that 500s.
"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def head_sha(repo: str | None = None) -> str | None:
    """The commit the working tree is on, or None if this is not a git checkout.

    Resolves the three shapes `.git/HEAD` actually takes: a detached SHA, a ref with a loose
    file behind it, and a ref that has been packed away by `git gc` — which is the one that
    silently returns nothing if you only look in `refs/`, and `gc` runs on its own schedule.
    """
    git = os.path.join(repo or REPO, ".git")
    head = _read(os.path.join(git, "HEAD"))
    if not head:
        return None
    if not head.startswith("ref:"):
        return head[:40] or None                 # detached HEAD is already the SHA
    ref = head[4:].strip()
    if (sha := _read(os.path.join(git, ref))):
        return sha[:40]
    for line in _read(os.path.join(git, "packed-refs")).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref:
            return parts[0][:40]
    return None


# CAPTURED AT IMPORT, ON PURPOSE. This is the SHA of the code that is running, and it must not
# move when the working tree does — a value re-read per request would report the pulled commit
# while the process still executes the old one, which is precisely the lie this file exists to
# stop telling.
RUNNING_SHA = head_sha()

# WHICH RELEASE, IN WORDS A CUSTOMER CAN READ. scripts/box_update.sh writes the verified tag here right
# after checking it out and before restarting, and rewrites it on a rollback, so the process that starts
# reads the release it is actually running. Captured at import for the same reason as RUNNING_SHA. A box
# that has never installed a verified release reports None, which is the truth about it.
RELEASE_FILE = os.environ.get("AIOS_RELEASE_FILE", "/var/lib/aios/release")
RUNNING_RELEASE = _read(RELEASE_FILE) or None


UPDATE_KEY_PUB = "/var/lib/aios/update_key.pub"


def update_key() -> str | None:
    """The PUBLIC half of this box's update key (`ssh-ed25519 AAAA...`), or None if none was minted.

    Published on /health so the provisioner can register it, read-only, on this box type's release
    repository without any way into the box (scripts/box_update_key.sh mints it; the private half never
    leaves). Only the key type and body are returned, never the comment. Read at call time.
    """
    parts = _read(os.environ.get("AIOS_UPDATE_KEY_PUB", UPDATE_KEY_PUB)).split()
    return " ".join(parts[:2]) if len(parts) >= 2 and parts[0] == "ssh-ed25519" else None


def status(repo: str | None = None) -> dict:
    """`{commit, on_disk, restart_needed, release, update_key}` for `/health`. Never raises."""
    disk = head_sha(repo)
    return {"commit": RUNNING_SHA, "on_disk": disk,
            "restart_needed": bool(RUNNING_SHA and disk and RUNNING_SHA != disk),
            "release": RUNNING_RELEASE, "update_key": update_key()}
