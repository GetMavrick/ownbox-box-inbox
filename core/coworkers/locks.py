"""Coworkers and box updates wait for each other (docs/SCOPE_SHIFTS.md §3, piece 3).

Two locks, one file:

  DEPLOY_LOCK  /tmp/aios-deploy.lock   held by scripts/box_update.sh for a whole update (unchanged)
  SHIFT_LOCK   /run/aios-shift.lock    held by the runner for a whole coworker run
  NEXT_FILE    /run/aios-shift.next    the next shift window's start, epoch seconds, from the tick

THE RULE, BOTH WAYS. Each side takes ITS OWN lock, then checks the OTHER's. If the other is held, it
lets go of its own and waits. Holding one lock while waiting for the other is how two careful
processes deadlock: an update holding the deploy lock while it waits for a shift that cannot start
because the deploy lock is held would turn every 1:00 PM shift into a MISSED.

  - A coworker never starts during an update. It waits inside its window, and if the window closes
    first it is MISSED, with that reason.
  - An update never starts during a coworker run, or when a shift starts within the hour. It waits
    up to 2 hours (box_update.sh), then goes ahead, and it still never interrupts a running shift.

/run is tmpfs, so a reboot clears both files and the tick rewrites NEXT_FILE within a minute. That's
safer than a stale "next start" surviving on disk and holding back updates for hours.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile

DEPLOY_LOCK = "/tmp/aios-deploy.lock"
SHIFT_LOCK = "/run/aios-shift.lock"
NEXT_FILE = "/run/aios-shift.next"


def _busy(path: str) -> bool:
    """Is `path` flock-held by someone else? A missing file is not busy. Never creates the file."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# PATHS ARE READ AT CALL TIME, never bound as defaults. A default is fixed when the module loads, so a
# caller (or a test) that points DEPLOY_LOCK somewhere else would still be checking the old file,
# and a check against the wrong file passes silently. Found 2026-09-26 by the upgrade's own tests.
def update_running(deploy_lock: str | None = None) -> bool:
    return _busy(deploy_lock or DEPLOY_LOCK)


@contextlib.contextmanager
def shift(shift_lock: str | None = None, deploy_lock: str | None = None):
    """Hold the shift lock for a run, or yield False without holding anything.

        with locks.shift() as held:
            if not held: ...wait and try again, or MISSED when the window closes...

    False means an update is running or another run holds the lock. The shift lock is taken
    FIRST, then the deploy lock is checked: in that order, an update that starts after this
    returns True sees the shift lock and waits.
    """
    shift_lock, deploy_lock = shift_lock or SHIFT_LOCK, deploy_lock or DEPLOY_LOCK
    fd = os.open(shift_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        if update_running(deploy_lock):
            fcntl.flock(fd, fcntl.LOCK_UN)
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def write_next(epoch: int | None, next_file: str | None = None) -> None:
    """Record the next shift window's start (the tick calls this every minute). None clears it.

    Written to a temporary file and renamed, so box_update.sh never reads half a number.
    """
    next_file = next_file or NEXT_FILE
    if epoch is None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(next_file)
        return
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError(f"epoch must be whole seconds, got {epoch!r}")
    d = os.path.dirname(next_file) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".aios-shift.next.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"{epoch}\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, next_file)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
