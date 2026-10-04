"""Approvals has a row in the box's main menu (owner, 10-04: "Where is the approvals Page? It should be in the
dashboard I guess").

Its only doors were the Dashboard card, drawn only while something waits, and the push. Now:
  · the owner's menu has an Approvals row on every page, right below Base Machine, linking /approvals;
  · with the number waiting beside its name when there are any, and no number when there are none;
  · an expired request is not counted, and counting never writes (the menu asks on every page);
  · a member, who cannot approve, is shown no row;
  · the same rows are the menu on a phone (the icon opens this rail);
  · a count that cannot be read costs the number, never the menu.
Run: python tests/test_approvals_in_the_menu.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "approvals_menu.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import state  # noqa: E402

state.init_db()
from core import approvals, dash  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:700]}"))
    if not cond:
        FAILS.append(label)


owner = app.test_client()
owner.set_cookie("aios_session", dash.new_session(state.owner_user()["id"]), domain="localhost")
member = app.test_client()
member.set_cookie("aios_session", dash.new_session(state.add_user("sam@glowmedspa.com", name="Sam")["id"]),
                  domain="localhost")
approvals.register_kind("menu_test_publish", run=lambda detail: {"ok": True, "text": "done"})


def nav(c, url="/dashboard") -> str:
    html = c.get(url).get_data(as_text=True)
    m = re.search(r'<nav[^>]*id="railnav".*?</nav>', html, re.S) or re.search(r"<nav\b.*?</nav>", html, re.S)
    return m.group(0) if m else ""


def row(html: str) -> str:
    m = re.search(r'<a href="/approvals"[^>]*>.*?</a>', html, re.S)
    return m.group(0) if m else ""


print("\nthe owner's menu")
n0 = nav(owner)
ok("an Approvals row, linking /approvals, with nothing waiting and so no number",
   "Approvals" in row(n0) and 'class="count"' not in row(n0), row(n0) or n0[:600])
ok("...right below Base Machine", n0.find('href="/dashboard"') < n0.find('href="/approvals"') < n0.find(
    'href="/settings"'), n0[:900])
for i in range(3):
    approvals.propose("menu_test_publish", machine="aeo_machine", title=f"Publish article {i}",
                      detail={"n": i}, quiet=True)
r3 = row(nav(owner))
ok("three waiting: 'Approvals 3'", re.search(r'<span class="count"[^>]*>3</span>', r3) is not None, r3)
for url in ("/approvals", "/app/review", "/add-machine"):     # a sub-menu (System Settings) shows its own rows
    ok(f"...on every page with the main menu ({url})", re.search(r'<span class="count"[^>]*>3</span>', row(nav(owner, url))) is not None,
       row(nav(owner, url)))
first = approvals.waiting()[0]
approvals.decide(first["id"], True, by="owner")
ok("deciding one takes it off the count", re.search(r'<span class="count"[^>]*>2</span>', row(nav(owner))), row(
    nav(owner)))
with state.connect() as c:
    c.execute("UPDATE approvals SET expires_at = '2000-01-01T00:00:00+00:00' WHERE status='waiting' AND title=?",
              ("Publish article 1",))
def status_of(title: str) -> str:
    with state.connect() as c:
        return c.execute("SELECT status FROM approvals WHERE title=?", (title,)).fetchone()[0]


# ON A PAGE WITHOUT THE DASHBOARD'S OWN CARD, which reads approvals.waiting() and so expires rows as it always has.
before = status_of("Publish article 1")
r1 = row(nav(owner, "/add-machine"))
after = status_of("Publish article 1")
ok("an expired request is not counted", re.search(r'<span class="count"[^>]*>1</span>', r1) is not None, r1)
ok("...and drawing the menu wrote nothing", before == after == "waiting", (before, after))

print("\na member, and a phone")
ok("a member, who cannot approve, is shown no Approvals row", 'href="/approvals"' not in nav(member), nav(member)[:600])
html = owner.get("/dashboard").get_data(as_text=True)
ok("the rail the phone's menu button opens is this one (one menu, every width)",
   'aria-controls="railnav"' in html and 'id="railnav"' in html and 'href="/approvals"' in nav(owner))

print("\na count that cannot be read")
real = approvals.waiting_count
approvals.waiting_count = lambda: (_ for _ in ()).throw(RuntimeError("database is locked"))
r = owner.get("/dashboard")
ok("costs the number, never the menu", r.status_code == 200 and "Approvals" in row(nav(owner))
   and 'class="count"' not in row(nav(owner)), r.status_code)
approvals.waiting_count = real

print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
