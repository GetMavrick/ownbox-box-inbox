#!/usr/bin/env python3
"""The coworker tick (docs/SCOPE_SHIFTS.md §3). aios-shifts.timer starts this every minute.

It starts whatever shift is due, in its own unit, and returns: it never waits for a run. All of it
is core/coworkers/runner.tick(), which never raises; this prints what it did for the journal.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import state  # noqa: E402
from core.coworkers import runner  # noqa: E402

if __name__ == "__main__":
    state.init_db()
    did = runner.tick()
    print(json.dumps(did, default=str))
    sys.exit(1 if did.get("error") else 0)
