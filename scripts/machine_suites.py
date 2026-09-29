#!/usr/bin/env python3
"""Run every add-on machine's own suites, as its machine.yaml declares them (#1665 §3.3).

A machine's suites live in its own folder and are named in its `suites:`, so CI runs them without
anyone adding a line to .github/workflows/tests.yml. That's half of "adding a machine is adding a
folder"; core/machines.py is the other half.

    python scripts/machine_suites.py            run them all; exit 1 if any fails or a manifest is refused
    python scripts/machine_suites.py --list     print what would run
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def planned() -> list[tuple[str, pathlib.Path]]:
    from core import machines
    return [(m["slug"], pathlib.Path(m["folder"]) / s) for m in machines.discover() for s in m["suites"]]


def main(argv) -> int:
    from core import machines
    refused = machines.invalid()
    for path, why in refused:
        print(f"✗ refused {path}: {why}")
    runs = planned()
    if "--list" in argv:
        for slug, suite in runs:
            print(f"{slug}\t{suite.relative_to(ROOT) if suite.is_relative_to(ROOT) else suite}")
        return 1 if refused else 0
    env = dict(os.environ, AIOS_HERMETIC_TEST="1", PYTHONPATH=os.pathsep.join(
        [str(ROOT)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    failed = []
    for slug, suite in runs:
        print(f"=== {slug}: {suite.name}", flush=True)
        if subprocess.run([sys.executable, str(suite)], cwd=ROOT, env=env).returncode != 0:
            failed.append(f"{slug}/{suite.name}")
    print(f"machine suites run: {len(runs)}" + (f", FAILED: {failed}" if failed else ""))
    return 1 if (failed or refused) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
