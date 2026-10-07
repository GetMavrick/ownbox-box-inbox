"""A sign-in returns to the page it interrupted (OSDev1, 10-06, from WebDev2's visitor walk of the owner's box).

The inbox's pages bounced a visitor to /dash/login?next=<the page>, but /settings, /settings/mobile and /approvals
bounced to a bare /dash/login: after signing in he landed on the default page, not where he was going. /approvals is
where every proposal's push sends him, so a phone whose session had run out lost the approval it was opened for.
Those pages share one gate (review._admit), and it now bounces with dash.login_redirect(), the same path
require_session takes, kept a path on this box by safe_next.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a signed-out visitor to /settings, /settings/mobile or /approvals is sent to a login that will not bring him
    back, or that drops the page's own query;
  · signing in with that `next` lands anywhere but the page;
  · `next` can name another site (//host, a scheme, a backslash) on the way in or out;
  · a signed-in member on an owner-only page is sent to a login that would send them straight back (a loop).
Run: python tests/test_a_sign_in_returns_where_it_was_going.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(pathlib.Path(tempfile.mkdtemp()) / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-only"
os.environ["DASH_TOKEN"] = "the-owners-own-password"

from core import dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:600]}"))
    if not cond:
        FAILS.append(label)


def next_of(resp) -> str | None:
    loc = resp.headers.get("Location") or ""
    parts = urlsplit(loc)
    if resp.status_code not in (301, 302, 303) or parts.path != "/dash/login":
        return None
    return (parse_qs(parts.query).get("next") or [""])[0]


print("\na visitor who is not signed in")
for page in ("/settings", "/settings/mobile", "/approvals", "/settings?tab=mobile"):
    r = app.test_client().get(page)
    ok(f"{page} sends him to the login carrying next={page}", next_of(r) == page,
       (r.status_code, r.headers.get("Location")))

print("\n...and signing in brings him back")
for page in ("/approvals", "/settings/mobile"):
    c = app.test_client()
    carried = next_of(c.get(page))
    r = c.post("/dash/login", data={"token": os.environ["DASH_TOKEN"], "next": carried or ""})
    ok(f"signing in from {page}'s bounce lands on {page}",
       r.status_code in (302, 303) and urlsplit(r.headers.get("Location") or "").path == page,
       (r.status_code, r.headers.get("Location")))
    ok(f"...and {page} then opens", c.get(page).status_code == 200)

print("\nnext stays a path on this box")
# The raw path a request line can carry (a test URL would read "//evil.example" as a host and keep only "/approvals").
with app.test_request_context("/", environ_overrides={"PATH_INFO": "//evil.example/approvals"}):
    r = dash.login_redirect()
    carried = next_of(r)
    ok("a request whose path reads as another host carries only a path on this box (one leading slash)",
       carried is not None and (carried == "" or (carried.startswith("/") and not carried.startswith("//")
                                                  and dash.safe_next(carried) == carried)), r.headers.get("Location"))
for bad in ("//evil.example/x", "https://evil.example/x", "/\\evil.example/x", "/\t/evil.example"):
    c = app.test_client()
    r = c.post("/dash/login", data={"token": os.environ["DASH_TOKEN"], "next": bad})
    dest = r.headers.get("Location") or ""
    ok(f"signing in with next={bad!r} stays on this box", r.status_code in (302, 303) and dest.startswith("/")
       and not dest.startswith("//") and "evil.example" not in dest, dest)

print("\na signed-in member on an owner-only page")
who = state.add_user("jordan.reyes@testco.example", name="Jordan Reyes", role="member")
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(who["id"]))
r = m.get("/approvals")
ok("is not handed a next back to the page that refused them (it would only refuse again)",
   r.status_code in (302, 303, 403) and "next=" not in (r.headers.get("Location") or ""),
   (r.status_code, r.headers.get("Location")))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    sys.exit(1)
print("all passed")
