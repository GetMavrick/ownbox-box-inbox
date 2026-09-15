#!/usr/bin/env python3
"""Phase 0/1 operator verb: prove each subaccount key live, list its inventory.

Read-only, zero risk. Run on the box once the three keys are in .env:

    python scripts/waalaxy_inventory.py            # every configured account
    python scripts/waalaxy_inventory.py acct1      # just one

Prints lists (with _id and prospect counts) and campaigns per subaccount - the
raw material for rewriting config routes against reality.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vendors.waalaxy import client, model                     # noqa: E402
from core.vendors.waalaxy.errors import WaalaxyError               # noqa: E402


def main() -> int:
    names = sys.argv[1:] or [a.name for a in model.accounts()]
    failures = 0
    for name in names:
        print(f"\n== account: {name} ==")
        try:
            acct = model.account(name)
            ok = client.test_connection(acct)
            print(f"  connection: {'OK' if ok else 'FAILED'}")
            if not ok:
                failures += 1
                continue
            for l in client.prospect_lists(acct):
                print(f"  list      {l.get('_id','?'):26} {l.get('name','?')!r:40} "
                      f"prospects={l.get('totalProspects', '?')}")
            for cmp in client.campaigns(acct):
                print(f"  campaign  {cmp.get('_id','?'):26} {cmp.get('name','?')!r}")
        except WaalaxyError as e:
            failures += 1
            print(f"  ERROR: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
