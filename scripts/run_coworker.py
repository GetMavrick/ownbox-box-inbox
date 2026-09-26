#!/usr/bin/env python3
"""One coworker shift, in the unit the tick started for it (docs/SCOPE_SHIFTS.md §4).

    run_coworker.py <slot>

The slot must be one the tick claimed; anything else is refused and nothing runs. All of it is
core/coworkers/runner.run(), which always ends the run with a receipt and a report.
"""
import json
import pathlib
import signal
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import state  # noqa: E402
from core.coworkers import runner  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: run_coworker.py <slot>")
    # THE UNIT'S TIME LIMIT ENDS THIS PROCESS WITH SIGTERM, which by default skips every
    # `finally`. As an exit it runs them: the seat is revoked and the run's private files go.
    # The tick still closes the run FAILED and stops its AI (runner._clean_up).
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    state.init_db()
    rc = runner.run(sys.argv[1])
    print(json.dumps({"slot": sys.argv[1], "outcome": rc and rc["outcome"]}))
    sys.exit(0 if rc else 2)
