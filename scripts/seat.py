#!/usr/bin/env python3
"""Mint, list and revoke connector seats. Runs ON THE BOX (docs/PLAN_AIOS_CONNECTOR.md §6 step 2).

    python scripts/seat.py mint "Mavrick (prod)" service
    python scripts/seat.py list
    python scripts/seat.py revoke seat_1a2b3c4d5e6f7890

THE CREDENTIAL IS PRINTED ONCE. It is not stored, not recoverable, and not logged — only its
SHA-256 reaches the database. If it is lost, mint a new seat and revoke the old one; that leaves
a trail the owner can read, which reading a secret back out of a table would not.

WHY THIS IS A SCRIPT AND NOT ONLY A SCRIPT. Step 11 of the plan adds mint and revoke from Slack,
and the reason is worth knowing before anyone calls this finished: a CLI runs on the box, so
every customer's first seat waits on someone with SSH — today that is one person, and that one
person IS the real number behind "time to first question". This file is the mechanism; it is not
yet the workflow.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import state                       # noqa: E402
from core.connector import seats             # noqa: E402


def _usage(code: int = 2) -> int:
    print(__doc__.strip().split("\n\nTHE CREDENTIAL")[0], file=sys.stderr)
    return code


def main(argv: list[str]) -> int:
    if not argv:
        return _usage()
    state.init_db()
    cmd, rest = argv[0], argv[1:]

    if cmd == "mint":
        if len(rest) != 2:
            print("usage: seat.py mint <label> <read|act|service>", file=sys.stderr)
            return 2
        label, role = rest
        try:
            seat_id, credential = seats.mint(label, role)
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        print(f"seat:  {seat_id}")
        print(f"label: {label}")
        print(f"role:  {role}")
        print()
        print("CREDENTIAL — copy it now, it is shown once and cannot be recovered:")
        print()
        print(f"    {credential}")
        print()
        print("Send it as:  Authorization: Bearer <credential>")
        return 0

    if cmd == "list":
        rows = seats.all_seats()
        if not rows:
            # An empty table is the shipped state of every box, so say what it MEANS rather than
            # printing nothing and letting the operator wonder whether the command worked.
            print("no seats — the /api/ door refuses everything on this box")
            return 0
        print(f"{'seat':<24} {'role':<8} {'label':<28} {'last seen':<28} state")
        for r in rows:
            state_s = f"REVOKED {r['revoked_at'][:19]}" if r["revoked_at"] else "live"
            print(f"{r['id']:<24} {r['role']:<8} {r['label'][:27]:<28} "
                  f"{(r['last_seen_at'] or 'never')[:27]:<28} {state_s}")
        return 0

    if cmd == "revoke":
        if len(rest) != 1:
            print("usage: seat.py revoke <seat_id>", file=sys.stderr)
            return 2
        if seats.revoke(rest[0]):
            print(f"revoked {rest[0]} — its credential stops working on the next request")
            print("its audit rows are kept, and still resolve to its label")
            return 0
        print(f"no LIVE seat {rest[0]} (already revoked, or never existed)", file=sys.stderr)
        return 1

    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
