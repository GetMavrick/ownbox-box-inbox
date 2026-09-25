"""The Mobile app page shows every app this box puts on a home screen, and each one is installable.

Owner, 2026-09-24, with a screenshot of two home-screen icons side by side, Ownbox and Unified Inbox:
*"put this on the Mobile app screen showing the PWA add to homescreen for the base machine and add
on machines"*. Until then only the inbox declared a web manifest, so adding the Base Machine offered
its page title as the name and opened it in the browser.

This suite holds:
  1. the Base Machine serves a manifest of its own: public, standalone, named Ownbox, with icons
     that exist, and only the sizes it names;
  2. every core screen links it, and a machine's own screens do not (a page carries one manifest);
  3. /settings/mobile draws one tile per manifest the box serves, the Base Machine first, each with
     its icon, its name, and a link that opens it, read from the routes rather than a list;
  4. a box without the inbox still shows the Base Machine, and only what it has.

Run: python tests/test_the_mobile_page_shows_every_app.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "apps.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

# A BOX SHIPS ONLY ITS OWN MACHINES. On a Lead box there is no inbox, and that is a true absence,
# checked below as such rather than skipped.
try:
    from marketing.customer_voice import app as _inbox                # noqa: E402,F401
    HAS_INBOX = True
except ImportError:
    HAS_INBOX = False

from core import dash                                                 # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


anon = app.test_client()
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("\ntest_the_base_machine_is_an_app")
r = anon.get("/ui/manifest.webmanifest")
ok("its manifest answers without a session, as a home screen asks for it",
   r.status_code == 200 and r.mimetype == "application/manifest+json", f"{r.status_code} {r.mimetype}")
try:
    m = json.loads(r.get_data(as_text=True) or "{}")
except ValueError:
    m = {}
ok("it is named Ownbox on the icon", m.get("short_name") == "Ownbox" and m.get("name") == "Ownbox", str(m))
ok("it opens standalone, like the inbox, starting at the box's front door",
   m.get("display") == "standalone" and m.get("start_url") == "/" and m.get("scope") == "/")
for icon in m.get("icons") or []:
    got = anon.get(icon["src"])
    ok(f"{icon['src']} is a real PNG, public", got.status_code == 200 and got.data[:4] == b"\x89PNG",
       str(got.status_code))
ok("...and it names both sizes an install needs",
   sorted(i["sizes"] for i in m.get("icons") or []) == ["192x192", "512x512"])
ok("a size it does not name is a 404, never drawn on demand", anon.get("/ui/icon-4096.png").status_code == 404)

print("\ntest_core_screens_link_it_and_machines_do_not")
page = owner.get("/settings").get_data(as_text=True)
ok("a core screen links the Base Machine's manifest",
   '<link rel="manifest" href="/ui/manifest.webmanifest">' in page)
ok("...and names it Ownbox for iOS", '<meta name="apple-mobile-web-app-title" content="Ownbox">' in page)
ok("...exactly once", page.count('rel="manifest"') == 1)
if HAS_INBOX:
    inbox = owner.get("/inbox/").get_data(as_text=True)
    ok("the inbox links its own manifest and not the Base Machine's",
       "/inbox/manifest.webmanifest" in inbox and "/ui/manifest.webmanifest" not in inbox)
else:
    print("  --   no inbox on this box; nothing of its to check")

print("\ntest_the_mobile_page_shows_every_app")
page = owner.get("/settings/mobile").get_data(as_text=True)
strip = re.search(r'<div class="apps">(.*?)</div>', page, re.S)
ok("the page draws the apps as they will sit on a home screen", bool(strip))
tiles = re.findall(r'<a href="([^"]+)"><img src="([^"]+)"[^>]*><span>([^<]+)</span></a>',
                   strip.group(1) if strip else "")
names = [t[2] for t in tiles]
ok("the Base Machine is first, as Ownbox, opening the box", bool(tiles) and tiles[0][:1] == ("/",)
   and names[0] == "Ownbox", str(tiles[:1]))
want = ["Ownbox"] + (["Unified Inbox"] if HAS_INBOX else [])
ok(f"one tile per app the box serves: {want}", names == want, str(names))
for href, src, name in tiles:
    ok(f"{name}: its icon is a real image", owner.get(src).data[:4] == b"\x89PNG", src)
    ok(f"{name}: its link opens something", owner.get(href).status_code in (200, 302, 303), href)
ok("the tiles are read from the box's routes, not written down in core",
   "Unified Inbox" not in open(os.path.join(os.path.dirname(os.path.dirname(
       os.path.abspath(__file__))), "core", "dash", "box_settings.py"), encoding="utf-8").read())

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
