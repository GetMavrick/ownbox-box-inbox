"""The way back lives in the menu, once — never in the top bar as well.

Owner, 2026-09-23, on the inbox's set-up screen on his own box: *"Get rid of dashboard at the top
of this screen! ... the back button is supposed to be in the fucking menu which it already is."*
The inbox's top bar drew a "< Dashboard" link beside the menu button while the drawer's own
header already carried the way out. It had been hidden on desktop and left showing on a phone,
which is the width he uses.

So this suite loads the inbox's screens the way a buyer does and holds two things:
  1. the top bar carries no link to the dashboard at all;
  2. the page carries exactly one — the menu's back control.

Run: python tests/test_the_top_bar_has_no_way_back.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "bar.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from marketing.customer_voice import app as _machine                  # noqa: E402,F401
from core import dash                                                 # noqa: E402
from core.dispatch import app                                         # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))

print("\ntest_the_top_bar_has_no_way_back")
for path in ("/inbox/", "/inbox/inbox", "/inbox/setup"):
    r = c.get(path)
    if r.status_code != 200:
        ok(f"{path} renders", False, str(r.status_code))
        continue
    html = r.get_data(as_text=True)
    bar = re.search(r'<div class="bar">(.*?)</div>\s*</div>', html, re.S)
    ok(f"{path}: the page has its top bar", bool(bar))
    if bar:
        ok(f"{path}: ...and it holds no link to the dashboard",
           'href="/dashboard"' not in bar.group(1), bar.group(1)[:300])
    ok(f"{path}: the page's one way back is the menu's",
       html.count('href="/dashboard"') == 1 and '<a class="home" href="/dashboard"' in html,
       str(html.count('href="/dashboard"')))

print("\n" + ("all good" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
