"""The Claude sign-in says where you go and how you come back (walk #4).

docs/JOURNEY_WALK_2026-09-23.md, finding 4: after Connect, /settings/ai sends the buyer to Claude in
a new tab, and on a mobile that is switch app, sign in, copy a code, find the box again, paste. The
round trip is Claude's design (it shows a code rather than returning to the box), so the box's job
is to say it before it happens, say it again while it is happening, and keep its place.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · the page before Connect stops saying that Claude opens in a new tab and a code comes back;
  · the page during the sign-in stops saying the box waits, where to paste, or how to get back from
    the browser to the installed app;
  · a reload mid-sign-in stops showing the same link (the place the buyer is coming back to);
  · a buyer reads a reserved noun (CLAUDE.md, mobile first).

Run: python tests/test_the_claude_sign_in_says_the_way_back.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "signin.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import claude_login, dash                                   # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def text(html):
    """What a buyer reads: the page with its <style> and <script> removed, then its tags."""
    html = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


owner = state.owner_user()["id"]
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(owner))
_real = claude_login.pending_url

print("\ntest_before_connect_it_says_what_will_happen")
claude_login.pending_url = lambda: ""
try:
    before = text(c.get("/settings/ai").get_data(as_text=True))
finally:
    claude_login.pending_url = _real
ok("it says Claude opens in a new tab", "Claude opens in a new tab" in before)
ok("...that a short code comes back to paste on this page", "paste back on this page" in before)

print("\ntest_during_the_sign_in_it_says_the_way_back")
LINK = "https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y"
claude_login.pending_url = lambda: LINK
try:
    raw = c.get("/settings/ai").get_data(as_text=True)
finally:
    claude_login.pending_url = _real
during = text(raw)
ok("a reload mid-sign-in still shows the same link, so there is a place to come back to",
   f'href="{LINK.replace("&", "&amp;")}"' in raw)
ok("it says this page stays and waits", "this page stays here and waits for you" in during)
ok("it says to copy the code, come back and paste it", "Copy it, come back to this tab and paste it" in during)
ok("it says how to get back from the browser to the installed app",
   "switch back to the app: it keeps your place" in during)
ok("the field to paste into is right there", 'name="code"' in raw)

print("\ntest_the_vocabulary")
_banned = re.findall(r"\b(phone|phones|ring|call|calls|dial|line|voice|answer)\b", before + " " + during, re.I)
ok("no reserved noun on either state of the page", not _banned, str(sorted(set(_banned))))

print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_claude_sign_in_says_the_way_back is in the workflow's suite list",
       "test_the_claude_sign_in_says_the_way_back" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
