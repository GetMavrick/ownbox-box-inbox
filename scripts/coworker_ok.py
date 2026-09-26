#!/usr/bin/env python3
"""Give the OK for a coworker that reads the box's data and reaches the web (docs/SCOPE_SHIFTS.md §4.3).

    python3 scripts/coworker_ok.py <name>          shows what it may do and asks you to type yes
    python3 scripts/coworker_ok.py <name> --yes    records the OK without asking

The Shifts screen is the usual place for this. This is the owner's way on the server, for a coworker
of their own (from: my) or a hired one, until and alongside the screen. The OK covers exactly the
grant shown; if the coworker's file later asks for more, it is asked again.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import state  # noqa: E402
from core.coworkers import contract, hire, runner  # noqa: E402


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("usage: coworker_ok.py <name> [--yes]", file=sys.stderr)
        return 2
    name = args[0]
    if not contract._SLUG.match(name):
        print(f"{name!r} is not a coworker's name", file=sys.stderr)
        return 2
    state.init_db()
    cw, why = contract.load(runner.coworkers_dir() / name)
    if cw is None:
        print(f"There is no coworker called {name} that can be used: " + "; ".join(why[:2]),
              file=sys.stderr)
        return 1
    if not contract.risks(cw):
        print(f"{cw.title} does not both read your data and reach the web, so it needs no OK.")
        return 0
    if hire.acknowledged(cw):
        print(f"{cw.title} already has your OK for exactly what it asks for now.")
        return 0
    sheet = hire.sheet(cw, machine=cw.source)
    print(f"{sheet['who']} wants to:")
    for w in sheet["wants"]:
        print(f"  - {w}")
    print()
    print(hire.RISK)
    if "--yes" not in argv:
        try:
            said = input("Type yes to let it run: ").strip().lower()
        except EOFError:
            said = ""
        if said != "yes":
            print("Nothing recorded.")
            return 1
    hire.acknowledge(cw, by="owner (server)")
    print(f"Recorded. {cw.title} can run its shifts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
