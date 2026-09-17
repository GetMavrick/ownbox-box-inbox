"""The box's own address, typed bare, lands somewhere — never a 404 (core/dash `bare_domain`).

FOUND BY WALKING THE ONBOARDING AS CUSTOMER #1 (OSDev4, 2026-09-17). The claim email names the box
by its address; the first thing a buyer does is type or bookmark it. The claim and the login both
redirect to `landing()`, but nothing answered `/`, and the Caddyfile is a plain reverse_proxy — so
`acme.ownbox.app` was a blank Not Found on the one site he owns.

Run: python tests/test_bare_domain_lands.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "bare.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-only"
os.environ["DASH_TOKEN"] = "the-owners-own-password"

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


from core import state  # noqa: E402

state.init_db()

from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

print("\n— a stranger at the bare address —")
c = app.test_client()
r = c.get("/")
ok("GET / is not a 404", r.status_code != 404, str(r.status_code))
ok("...it sends a person who is not signed in to the login", r.status_code in (302, 303)
   and (r.headers.get("Location") or "").endswith("/dash/login"), f"{r.status_code} {r.headers.get('Location')}")
ok("...and shows nothing of the box on the way", len(r.get_data()) < 400, f"{len(r.get_data())} bytes")

print("\n— the owner at the bare address —")
c.post("/dash/login", data={"token": os.environ["DASH_TOKEN"]})
r = c.get("/")
with app.test_request_context("/"):
    expected = dash.landing()
ok("a signed-in person goes straight to where the login would have landed him",
   r.status_code in (302, 303) and (r.headers.get("Location") or "").endswith(expected),
   f"{r.status_code} {r.headers.get('Location')} (landing {expected})")

print("\n— one landing decision, not two —")
src = (ROOT / "core" / "dash" / "__init__.py").read_text()
body = src[src.index("def bare_domain"):src.index("def landing")]
ok("/ redirects to landing() rather than naming a page of its own", "redirect(landing())" in body, body[-300:])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
