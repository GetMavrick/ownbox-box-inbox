"""/voice is the AI receptionist's now. The inbox lives at /inbox, and nothing redirects.

THE RULING (owner, 2026-09-17): "We are definitely going to have an AI receptionist machine and it
will definitely be on this path: /voice." — "No redirect, please. Full cut over." Runbook #1311.

WHAT BREAKS IF THIS GOES RED, and why each check is here:
  * A /voice route left behind, or a redirect added "to be kind", would answer on a path the next
    machine needs, and a redirect can never be permanent — it would be a second migration later.
  * A login that lands on /voice would put every person who signs in onto the receptionist.
  * The installed phone app is the one part a string-replace cannot fix: a worker registered at
    /voice/ can never control /inbox/, so /voice/sw.js must serve a TOMBSTONE that only unregisters
    itself. If that file ever grows a fetch handler or a cache, it would start answering requests on
    a path that is no longer ours.
  * A functional "/voice" literal creeping back into the code is how the cut quietly un-does itself.

Run: python tests/test_voice_path_is_free.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


from core.dispatch import app  # noqa: E402
# A MODULE-LEVEL IMPORT OF THE INBOX MACHINE, ON PURPOSE. scripts/export_box.sh ships a suite into a box only
# when its marketing imports resolve there, so this line is what keeps this guard in the boxes that carry
# the inbox and out of a Lead or Content box, where there is no /inbox to check (test_recipe_ships).
from marketing.customer_voice.app import PUBLIC_PATHS, TOMBSTONE_SW_JS  # noqa: E402

rules = {str(r): r for r in app.url_map.iter_rules()}
voice = sorted(p for p in rules if p == "/voice" or p.startswith("/voice/"))
inbox = sorted(p for p in rules if p == "/inbox" or p.startswith("/inbox/"))

print("\n— the routes —")
ok("EXACTLY ONE /voice route remains, and it is the worker tombstone", voice == ["/voice/sw.js"], str(voice))
ok("the inbox app is served under /inbox", len(inbox) >= 17, str(inbox))
for path in ("/inbox/", "/inbox/inbox", "/inbox/settings", "/inbox/setup", "/inbox/mailbox",
             "/inbox/connect", "/inbox/drafts", "/inbox/manifest.webmanifest", "/inbox/sw.js",
             "/inbox/icon-192.png", "/inbox/icon-512.png"):
    ok(f"{path} is a route", path in rules, "missing")

c = app.test_client()
print("\n— no redirect, anywhere on the old path —")
for path in ("/voice", "/voice/", "/voice/inbox", "/voice/settings", "/voice/setup",
             "/voice/manifest.webmanifest", "/voice/icon-192.png"):
    r = c.get(path)
    ok(f"GET {path} is a plain 404, not a redirect", r.status_code == 404,
       f"{r.status_code} {r.headers.get('Location')}")

print("\n— the tombstone does one thing —")
ok("the tombstone is on the allow-list a phone needs, and nothing else joined it",
   "/voice/sw.js" in PUBLIC_PATHS and len(PUBLIC_PATHS) == 5, str(sorted(PUBLIC_PATHS)))
ok("the served tombstone is the module's own text, not a copy", c.get("/voice/sw.js").get_data(as_text=True) == TOMBSTONE_SW_JS)
r = c.get("/voice/sw.js")
body = r.get_data(as_text=True)
ok("/voice/sw.js answers WITHOUT a login (a phone's update check carries no session)", r.status_code == 200,
   str(r.status_code))
ok("...as JavaScript, never cached", r.mimetype == "application/javascript"
   and "no-cache" in (r.headers.get("Cache-Control") or ""), f"{r.mimetype} {r.headers.get('Cache-Control')}")
ok("...and it unregisters itself", "self.registration.unregister()" in body, body[:200])
ok("...and answers NO request: no fetch handler, no cache, no navigation",
   not re.search(r"fetch|caches|clients|navigate|importScripts", body), body)

print("\n— the installed app moved with it —")
m = c.get("/inbox/manifest.webmanifest")
try:
    manifest = json.loads(m.get_data(as_text=True) or "{}")
except ValueError:
    manifest = {}
ok("the manifest is served at /inbox/ without a login", m.status_code == 200, str(m.status_code))
ok("its scope and start_url are /inbox/", manifest.get("scope") == "/inbox/" and manifest.get("start_url") == "/inbox/",
   f"{manifest.get('scope')} {manifest.get('start_url')}")
ok("its icons are under /inbox/", all(str(i.get("src", "")).startswith("/inbox/") for i in manifest.get("icons") or [])
   and bool(manifest.get("icons")), str(manifest.get("icons")))
w = c.get("/inbox/sw.js")
ok("the real worker is served at /inbox/sw.js", w.status_code == 200 and "unregister" not in w.get_data(as_text=True),
   str(w.status_code))

print("\n— nobody lands on the receptionist —")
from core import dash  # noqa: E402

ok("no login landing names /voice", not any("/voice" in p for p in dash._LANDINGS), str(dash._LANDINGS))
with app.test_request_context("/"):
    ok("the landing this box actually serves is the inbox's", dash.landing().startswith("/inbox/")
       or dash.landing() == "/dash/home", dash.landing())

print("\n— the cut cannot quietly un-do itself —")
URL_LITERAL = re.compile(r"""['"]/voice(?:[/'"?#]|$)""")
found = []
for folder in ("core", "marketing", "operations", "provisioner", "scripts"):
    for f in (ROOT / folder).rglob("*.py"):
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if URL_LITERAL.search(line):
                found.append(f"{f.relative_to(ROOT)}:{n}")
tombstone = [x for x in found if x.startswith("marketing/customer_voice/app.py:")]
ok("the only /voice URL literals in shipped code are the tombstone's two (route + allow-list)",
   len(found) == 2 and len(tombstone) == 2, str(found))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
