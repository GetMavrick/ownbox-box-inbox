"""A form on another site must not act here with this box's cookies: the one app-wide rule, executed.

Every sold box is a sibling under ownbox.app, which is not on the Public Suffix List, so to a browser
acme.ownbox.app and evil.ownbox.app are the SAME SITE and SameSite=Lax cookies ride along on a form the
other box submits. Every check here sends a real request through the real app (its whole before_request
chain) with the headers a browser actually sends, and asserts whether a route was REACHED, not just a
status code. The session cookie is the one a real /dash/login mints.

Run: python tests/test_same_origin_gate.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_TMP, "gate.db")
os.environ["AIOS_PAUSE_FILE"] = os.path.join(_TMP, "PAUSED")   # never the real marker
os.environ["DISPATCH_BEARER_TOKEN"] = "test-token"
os.environ.pop("DASH_TOKEN", None)
os.environ.pop("DASHBOARD_BASE_URL", None)

from flask import request  # noqa: E402

from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# A route that records that it ran, mounted on the REAL app before its first request, so it sits
# behind exactly the gates every blueprint sits behind.
REACHED = []


def _probe():
    REACHED.append(request.method)
    return "done", 200


app.add_url_rule("/__gate_probe", "gate_probe", _probe, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
app.testing = True
HOST = "acme.ownbox.app"
BASE = f"https://{HOST}"
c = app.test_client(use_cookies=False)       # cookies are passed explicitly, so none is sent by accident

login = c.post("/dash/login", data={"token": "test-token"}, base_url=BASE)
set_cookie = login.headers.get("Set-Cookie") or ""
SESSION = set_cookie.split(";")[0]
ok("a real login mints the session cookie these checks carry", login.status_code == 302 and "=" in SESSION,
   f"status={login.status_code} set-cookie={set_cookie[:60]!r}")


def send(method="POST", *, cookie=True, base=BASE, **headers):
    """One request as a browser would send it. Returns (status, reached)."""
    hdrs = {k.replace("_", "-"): v for k, v in headers.items()}
    if cookie:
        hdrs["Cookie"] = SESSION
    before = len(REACHED)
    r = c.open("/__gate_probe", method=method, base_url=base, headers=hdrs)
    return r.status_code, len(REACHED) > before


print("\n— this box's own pages —")
s, reached = send(Sec_Fetch_Site="same-origin", Origin=BASE)
ok("a form on this box posts normally", s == 200 and reached, f"status={s}")
s, reached = send(Sec_Fetch_Site="none")
ok("a request the person started themselves (typed, bookmark) goes through", s == 200 and reached, f"status={s}")

print("\n— another box on ownbox.app (same-site, so Lax sends the cookie) —")
s, reached = send(Sec_Fetch_Site="same-site", Origin="https://evil.ownbox.app")
ok("a sibling box's form is REFUSED and the route never runs", s == 403 and not reached, f"status={s}")
for m in ("PUT", "PATCH", "DELETE"):
    s, reached = send(m, Sec_Fetch_Site="same-site", Origin="https://evil.ownbox.app")
    ok(f"...and so is {m}", s == 403 and not reached, f"status={s}")
s, reached = send(Sec_Fetch_Site="cross-site", Origin="https://evil.example")
ok("a cross-site request carrying the cookie is refused", s == 403 and not reached, f"status={s}")

print("\n— a browser that sends Origin but not Sec-Fetch-Site —")
s, reached = send(Origin="https://evil.ownbox.app")
ok("a sibling's Origin is refused", s == 403 and not reached, f"status={s}")
s, reached = send(Origin=BASE)
ok("this box's own Origin goes through", s == 200 and reached, f"status={s}")
s, reached = send(Origin="null")
ok("an opaque Origin (null: a sandboxed frame, a redirect) is refused", s == 403 and not reached, f"status={s}")
s, reached = send(Origin=f"{BASE}:8443")
ok("the same name on another port is another origin, refused", s == 403 and not reached, f"status={s}")

print("\n— behind Caddy, as on the real box —")
proxied = dict(X_Forwarded_Host=HOST, X_Forwarded_Proto="https", X_Forwarded_For="203.0.113.9")
s, reached = send(base="http://127.0.0.1:8000", Origin=BASE, **proxied)
ok("gunicorn sees 127.0.0.1, but the public name decides: this box's Origin goes through",
   s == 200 and reached, f"status={s}")
s, reached = send(base="http://127.0.0.1:8000", Origin="https://evil.ownbox.app", **proxied)
ok("...and a sibling's Origin is still refused", s == 403 and not reached, f"status={s}")

print("\n— what the gate must NOT touch —")
s, reached = send("GET", Sec_Fetch_Site="same-site", Origin="https://evil.ownbox.app")
ok("a GET is not refused here (a GET that changes state is that route's bug)", s == 200 and reached, f"status={s}")
s, reached = send()
ok("no browser signal at all (curl, a server) passes to the route's own auth", s == 200 and reached, f"status={s}")
s, reached = send(cookie=False, Sec_Fetch_Site="cross-site", Origin="https://evil.example")
ok("a cross-site request with NO cookie has nothing to borrow and is not refused here",
   s == 200 and reached, f"status={s}")
r = c.post("/deploy", base_url=BASE, headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example",
                                               "Authorization": "Bearer wrong"})
ok("/deploy keeps its own gate: a bad token is its 401, not this gate's 403", r.status_code == 401, f"status={r.status_code}")

print("\n— a real route —")
r = c.post("/dash/login", data={"token": "test-token"}, base_url=BASE,
           headers={"Cookie": SESSION, "Sec-Fetch-Site": "same-site", "Origin": "https://evil.ownbox.app"})
ok("POST /dash/login from a sibling box, carrying the session, is refused before the view runs",
   r.status_code == 403 and "Set-Cookie" not in r.headers, f"status={r.status_code}")

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("all ok")
