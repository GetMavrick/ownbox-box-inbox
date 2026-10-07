"""No other site's page may frame a box (WebDev2's visitor walk of the owner's box, release 2026.10.06.4; OSDev1, 10-06).

Every sold box is a sibling under ownbox.app, and to a browser the siblings are the SAME SITE (core/dispatch.py's
_cross_site note), so a box's Lax session cookie rides into an iframe on any of them. A page on another box could frame
the owner's signed-in inbox under a decoy and borrow his click on Send, Done or Delete; the click comes from the framed
page itself, so the cross-site gate sees same-origin. The walk found no frame protection on any answer.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · any answer (the sign-in page, a redirect to it, /health, a signed-in inbox page or its API) goes out without
    frame-ancestors 'self', X-Frame-Options SAMEORIGIN and HSTS;
  · HSTS grows includeSubDomains or preload (easy to add, hard to undo: it binds every sibling and the browsers' list);
  · a route's own Content-Security-Policy is replaced instead of extended, or a route's own frame-ancestors or
    X-Frame-Options is overwritten.
Run: python tests/test_no_other_site_frames_a_box.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(pathlib.Path(tempfile.mkdtemp()) / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402

INBOX = (ROOT / "marketing" / "customer_voice").is_dir()      # a Lead or Content box ships no Inbox Machine

# Routes that send their own headers, registered before the first request (Flask refuses a route after it).
OWN_CSP = "default-src 'none'; img-src data:"


@app.get("/__test/own-csp")
def _own_csp():
    return "x", 200, {"Content-Security-Policy": OWN_CSP + ";"}


@app.get("/__test/own-frame-rules")
def _own_frame_rules():
    return "x", 200, {"Content-Security-Policy": "frame-ancestors 'none'", "X-Frame-Options": "DENY"}


FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:600]}"))
    if not cond:
        FAILS.append(label)


def guarded(resp) -> bool:
    h = resp.headers
    return (h.get("Content-Security-Policy") == "frame-ancestors 'self'" and h.get("X-Frame-Options") == "SAMEORIGIN"
            and h.get("Strict-Transport-Security") == "max-age=31536000")


def seen(resp) -> dict:
    return {k: resp.headers.get(k) for k in ("Content-Security-Policy", "X-Frame-Options", "Strict-Transport-Security")}


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
stranger = app.test_client()

print("\na visitor, signed out")
r = stranger.get("/dash/login?next=/inbox/inbox")
ok("the sign-in page: frame-ancestors 'self', X-Frame-Options SAMEORIGIN, HSTS for a year",
   r.status_code == 200 and guarded(r), (r.status_code, seen(r)))
r = stranger.get("/health")
ok("/health, public by design, carries the same three", r.status_code == 200 and guarded(r), (r.status_code, seen(r)))
r = stranger.get("/")
ok("the bare domain's redirect to the sign-in carries them", r.status_code in (301, 302, 303) and guarded(r),
   (r.status_code, seen(r)))
if INBOX:
    r = stranger.get("/inbox")
    ok("the /inbox redirect to the sign-in carries them", r.status_code == 302 and "/dash/login" in r.location
       and guarded(r), (r.status_code, r.location, seen(r)))
    r = stranger.get("/inbox/api/conversations")
    ok("an /inbox/api route, signed out, carries them", r.status_code in (302, 401, 403) and guarded(r),
       (r.status_code, seen(r)))

print("\nthe owner, signed in: the page a decoy would frame")
if INBOX:
    r = owner.get("/inbox/api/conversations")
    ok("an /inbox/api answer with his conversations carries them", r.status_code == 200 and guarded(r),
       (r.status_code, seen(r)))
    r = owner.get("/inbox/inbox")
    ok("his inbox page carries them", r.status_code == 200 and guarded(r), (r.status_code, seen(r)))
else:
    print("  --   this box ships no Inbox Machine")
r = owner.get("/settings")
ok("his settings page carries them", r.status_code == 200 and guarded(r), (r.status_code, seen(r)))

print("\nHSTS binds this name only")
hsts = stranger.get("/health").headers.get("Strict-Transport-Security") or ""
ok("no includeSubDomains and no preload", "includesubdomains" not in hsts.lower() and "preload" not in hsts.lower(), hsts)

print("\na route's own rules are kept")
r = stranger.get("/__test/own-csp")
ok("a route's own CSP is extended with frame-ancestors, never replaced",
   r.headers.get("Content-Security-Policy") == OWN_CSP + "; frame-ancestors 'self'", seen(r))
r = stranger.get("/__test/own-frame-rules")
ok("a route's own frame-ancestors and X-Frame-Options are left as it set them",
   r.headers.get("Content-Security-Policy") == "frame-ancestors 'none'" and r.headers.get("X-Frame-Options") == "DENY"
   and r.headers.get("Strict-Transport-Security") == "max-age=31536000", seen(r))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    sys.exit(1)
print("all passed")
