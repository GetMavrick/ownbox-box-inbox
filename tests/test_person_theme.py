"""Appearance, per person: stored per seat, stamped on <html> before the first paint.

core/dash/theme.py, for WebDev2's app redesign (2026-09-28):
  · nobody has chosen: every page is white (owner, 2026-09-16: "white screens first and foremost");
  · each person's choice is their own, and a member's never changes the owner's screen;
  · chrome() and the inbox both stamp it; System carries the script that decides before
    paint, is stamped light without it, and sends both theme-colours;
  · the save refuses a stranger, a value that isn't one of the three, and a `next` off the box;
  · the inbox's older Light/Dark switch writes the same setting, so one choice governs both.

Run: python tests/test_person_theme.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = _T + "/theme.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core import dash  # noqa: E402
from core.dash import theme  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def html_tag(page: str) -> str:
    m = re.search(r"<html[^>]*>", page)
    return m.group(0) if m else ""


OWNER_ID = state.owner_user()["id"]
MEMBER_ID = state.add_user("coach@studio.example", role="member")["id"]
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(OWNER_ID))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(MEMBER_ID))
stranger = app.test_client()
PAGE = "/dashboard/upgrade"          # any chrome() page a member may also open


def page(client, path=PAGE):
    return client.get(path).get_data(as_text=True)


print("\nnobody has chosen —")
ok("the default is Light", theme.get(OWNER_ID) == "light" and theme.chosen(OWNER_ID) == "")
p = page(owner)
ok("chrome() stamps light on <html>", 'data-theme="light"' in html_tag(p), html_tag(p))
ok("...with the light ground as theme-color and no script",
   '<meta name="theme-color" content="#f6f4ef">' in p and "prefers-color-scheme" not in p)

print("\nsaving —")
r = stranger.post(theme.ROUTE, data={"theme": "dark"})
ok("a stranger is sent to sign in and nothing is stored",
   r.status_code in (302, 303) and "/dash/login" in r.headers.get("Location", "")
   and theme.chosen(OWNER_ID) == "", str(r.status_code))
r = owner.post(theme.ROUTE, data={"theme": "purple"})
ok("a value that isn't one of the three is refused", r.status_code == 400 and theme.chosen(OWNER_ID) == "")
r = owner.post(theme.ROUTE, data={"theme": "dark", "next": "//evil.example/x"})
ok("dark is saved, and a next off the box goes to Settings instead",
   r.status_code == 303 and theme.chosen(OWNER_ID) == "dark"
   and "evil" not in r.headers.get("Location", ""), r.headers.get("Location", ""))
r = owner.post(theme.ROUTE, data={"theme": "dark", "next": "/shifts"})
ok("...and a next on the box is where it goes back to", r.headers.get("Location", "").endswith("/shifts"))
r = owner.post(theme.ROUTE, data={"theme": "dark"}, headers={"Accept": "application/json"})
ok("a fetch() gets JSON back", r.status_code == 200 and r.get_json() == {"theme": "dark"})

print("\nper person —")
p = page(owner)
ok("the owner's pages are stamped dark", 'data-theme="dark"' in html_tag(p), html_tag(p))
ok("...with the dark ground as theme-color", f'<meta name="theme-color" content="{theme.GROUND["dark"]}">' in p)
ok("the member, who never chose, still sees light", 'data-theme="light"' in html_tag(page(member)))
member.post(theme.ROUTE, data={"theme": "system"})
ok("the member's choice doesn't touch the owner's", theme.get(OWNER_ID) == "dark"
   and theme.get(MEMBER_ID) == "system")

print("\nsystem —")
p = page(member)
ok("stamped light, so a page without JavaScript is white", 'data-theme="light"' in html_tag(p))
head = p.split("</head>")[0]
ok("the script that follows the phone is in <head>, before anything is painted",
   "matchMedia('(prefers-color-scheme: dark)')" in head and "setAttribute('data-theme'" in head)
ok("...and both theme-colours are sent, matched to the phone",
   f'media="(prefers-color-scheme: light)" content="{theme.GROUND["light"]}"' in head
   and f'media="(prefers-color-scheme: dark)" content="{theme.GROUND["dark"]}"' in head)
# THE VALUES LIVE IN theme.GROUND, and test_the_box_wears_one_look.py holds that table to box.css's
# --ground in each theme, so a palette change is made once and this suite follows it.

print("\nthe inbox —")
fresh = state.add_user("front@studio.example", role="member")["id"]
_inbox = ROOT / "marketing/customer_voice/app.py"
if _inbox.is_file():
    from marketing.customer_voice import app as inbox
    with app.test_request_context("/inbox/inbox"):
        from flask import request
        request.cookies = {dash.COOKIE: dash.new_session(OWNER_ID), inbox.THEME_COOKIE: "light"}
        ok("the person's choice beats the phone's old cookie", inbox._theme() == "dark", inbox._theme())
    with app.test_request_context("/inbox/inbox"):
        from flask import request
        request.cookies = {dash.COOKIE: dash.new_session(fresh), inbox.THEME_COOKIE: "dark"}
        seen = inbox._theme()
    ok("a phone's old cookie is carried over once into that person's setting",
       seen == "dark" and theme.chosen(fresh) == "dark", f"{seen} {theme.chosen(fresh)}")
    r = owner.get("/inbox/theme?to=light")
    ok("the inbox's own switch writes the person's setting", theme.chosen(OWNER_ID) == "light")
    owner.get("/inbox/theme?to=system")
    ok("...but its old ?to=system bookmark doesn't change it; the redesign's control does",
       theme.chosen(OWNER_ID) == "light")
else:
    print("  --   no inbox machine ships on this box, so there is no inbox switch to check here")

print("\nthe switch — (WebDev2, PR #1659 §14: Light / Dark / Automatic, one control everywhere)")


def _pressed(p: str) -> list:
    """The values the page marks as chosen in the appearance control."""
    form = re.search(r'<form class="ui-seg"[^>]*action="' + re.escape(theme.ROUTE) + r'".*?</form>', p, re.S)
    return re.findall(r'value="(\w+)" aria-pressed="true"', form.group(0)) if form else ["<no control>"]


s = page(owner, "/settings")
ok("System Settings offers the switch, posting to the person's setting",
   f'action="{theme.ROUTE}"' in s and 'class="ui-seg"' in s)
ok("...with all three choices, as buttons, so no script is needed",
   all(f'name="theme" value="{v}"' in s for v in ("light", "dark", "system")) and ">Automatic<" in s)
ok("...and exactly the person's own choice is marked", _pressed(s) == [theme.chosen(OWNER_ID)],
   f"{_pressed(s)} vs {theme.chosen(OWNER_ID)}")
r = owner.post(theme.ROUTE, data={"theme": "system", "next": "/settings"})
ok("choosing Automatic saves it and comes back to Settings",
   r.status_code == 303 and r.headers["Location"].endswith("/settings")
   and theme.chosen(OWNER_ID) == "system", f"{r.status_code} {r.headers.get('Location')}")
ok("...and Settings then shows Automatic chosen", _pressed(page(owner, "/settings")) == ["system"])
ok("a member gets the same switch: appearance is each person's, not the owner's",
   'class="ui-seg"' in page(member, "/settings"))
if _inbox.is_file():
    # ONE SWITCH, ONE HOME (owner, 2026-09-29, IA D3): Appearance is each person's and the whole
    # box's, so it lives in System Settings. The inbox's Settings carries no second copy — it
    # says where Appearance is, and links there.
    i = page(owner, "/inbox/settings")
    ok("the inbox carries no second switch, and says where Appearance is",
       'class="ui-seg"' not in i and "Appearance" in i and 'href="/settings"' in i,
       str(_pressed(i)))

print("\nthe iPhone's clock — (WebDev2, PR #1659 install phase: white over a dark app)")
_SB = re.compile(r'<meta name="apple-mobile-web-app-status-bar-style" content="(\w[\w-]*)">')
for choice, want in (("dark", "black"), ("light", "default"), ("system", "default")):
    owner.post(theme.ROUTE, data={"theme": choice})
    for path in ("/dashboard",) + (("/inbox/inbox",) if _inbox.is_file() else ()):
        got = _SB.findall(page(owner, path).split("</head>")[0])
        ok(f"{choice}: {path} declares the clock once, as {want}", got == [want], str(got))
ok("core's screens also say they open full screen on an older iPhone",
   '<meta name="apple-mobile-web-app-capable" content="yes">' in page(owner, "/dashboard"))
owner.post(theme.ROUTE, data={"theme": "light"})

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all passed")
