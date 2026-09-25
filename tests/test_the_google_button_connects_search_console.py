"""One button, sign in with Google, pick your site: the box side of Search Console.

Owner, 2026-09-25: "we need to add a button to the settings page that has them login into their Google
account and choose a property/website as their primary." Everything below runs against a temp database
with `core.net`'s two doors replaced at the net module itself, so every line of the vendor module runs,
and the fakes assert on the exact URL, headers and body the box would send.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "gsc.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_MY_MACHINES"] = os.path.join(_T, "none")
for k in ("OWNBOX_GOOGLE_BROKER", "OWNBOX_GOOGLE_RELAY", "OWNBOX_GOOGLE_CLIENT_ID"):
    os.environ.pop(k, None)

from core import state  # noqa: E402

state.init_db()

from core import box_secrets, claim, dash, net  # noqa: E402
from core.config import settings  # noqa: E402
from core.dispatch import app  # noqa: E402
from core.vendors import google_search_console as gsc  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


HOST = "acme.ownbox.app"
SITES = [{"siteUrl": "sc-domain:acme.com", "permissionLevel": "siteOwner"},
         {"siteUrl": "https://blog.acme.com/", "permissionLevel": "siteFullUser"},
         {"siteUrl": "https://unverified.example/", "permissionLevel": "siteUnverifiedUser"}]


def id_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class Wire:
    """Stands in for core.net's post_public/get_public, and records exactly what was sent."""

    def __init__(self):
        self.posts, self.gets = [], []
        self.token_answer = (200, {"refresh_token": "1//refresh", "access_token": "ya29.a",
                                   "expires_in": 3599, "scope": f"openid email {gsc.SCOPE}",
                                   "id_token": id_token("owner@acme.com")})
        self.refresh_answer = (200, {"access_token": "ya29.fresh", "expires_in": 3599})
        self.sites_answer = (200, {"siteEntry": SITES})

    def post(self, url, *, data=None, json=None, headers=None, **_):
        self.posts.append({"url": url, "data": data, "json": json, "headers": dict(headers or {})})
        if url.endswith("/google/token"):
            s, b = self.token_answer
        elif url.endswith("/google/refresh"):
            s, b = self.refresh_answer
        else:
            s, b = 200, {}
        return s, globals()["json"].dumps(b)

    def get(self, url, *, headers=None, **_):
        self.gets.append({"url": url, "headers": dict(headers or {})})
        s, b = self.sites_answer
        return s, json.dumps(b)


wire = Wire()
net.post_public, net.get_public = wire.post, wire.get
object.__setattr__(settings, "deploy_token", "dtok-acme")
claim.provisioned_host = lambda: HOST


def reset():
    for n in (gsc.REFRESH, gsc.ACCOUNT, gsc.PROPERTY, gsc.PENDING):
        box_secrets.clear(n)
    gsc._cached.clear()
    wire.__init__()


print("\ntest_the_sign_in_link")
url = gsc.begin(HOST, now=1000)
q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
pending = json.loads(box_secrets.get(gsc.PENDING))
ok("it goes to Google's own sign-in page", url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
ok("...for the shared Ownbox app", q["client_id"] == gsc.CLIENT_ID)
ok("...coming back through the ownbox.io relay", q["redirect_uri"] == "https://www.ownbox.io/connect/google")
ok("...asking to READ Search Console only, plus who is signing in",
   q["scope"].split() == ["openid", "email", "https://www.googleapis.com/auth/webmasters.readonly"], q["scope"])
ok("...offline, so the box keeps working after the buyer leaves", q["access_type"] == "offline")
ok("state is '<nonce>.<this box>' and the nonce has no dot",
   q["state"] == f"{pending['nonce']}.{HOST}" and "." not in pending["nonce"])
want = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).decode().rstrip("=")
ok("PKCE: the challenge is S256 of the verifier the box kept",
   q["code_challenge"] == want and q["code_challenge_method"] == "S256")
ok("the verifier never leaves the box in the link", pending["verifier"] not in url)
try:
    gsc.begin("evil.example.com")
    ok("a host outside ownbox.app cannot start a sign-in", False)
except gsc.Refused as e:
    ok("a host outside ownbox.app cannot start a sign-in", e.key == "wrong_host")

print("\ntest_the_code_comes_back")
reset()
gsc.begin(HOST, now=1000)
pending = json.loads(box_secrets.get(gsc.PENDING))
nonce = pending["nonce"]
try:
    gsc.finish("4/code", f"someoneelse.{HOST}", now=1001)
    ok("a state this box did not issue is refused", False)
except gsc.Refused as e:
    ok("a state this box did not issue is refused", e.key == "not_ours" and not wire.posts)
ok("...and does not burn the real pending sign-in", box_secrets.is_set(gsc.PENDING))
email = gsc.finish("4/code", f"{nonce}.{HOST}", now=1001)
p = wire.posts[-1]
ok("the code is redeemed at the provisioner's hand-off, never on the website",
   p["url"] == "https://orders.ownbox.app/google/token", p["url"])
ok("...with the code and the verifier only this box holds",
   p["json"] == {"code": "4/code", "code_verifier": pending["verifier"]}, str(p["json"])[:80])
ok("...and the box proves who it is with its own name and deploy token",
   p["headers"] == {"X-Ownbox-Host": HOST, "Authorization": "Bearer dtok-acme"}, str(p["headers"]))
ok("the refresh token is kept on the box", box_secrets.get(gsc.REFRESH) == "1//refresh")
ok("the account is named from Google's own answer", email == "owner@acme.com" == box_secrets.get(gsc.ACCOUNT))
ok("the pending sign-in is spent", not box_secrets.is_set(gsc.PENDING))
try:
    gsc.finish("4/code", f"{nonce}.{HOST}", now=1002)
    ok("the same code cannot be redeemed twice", False)
except gsc.Refused as e:
    ok("the same code cannot be redeemed twice", e.key == "not_ours")

print("\ntest_every_way_the_return_goes_wrong")
for label, answer, key in (
        ("the hand-off has no secret yet", (503, {"error": "not_configured"}), "not_switched_on"),
        ("a box we did not build", (401, {"error": "not_a_box"}), "not_a_sold_box"),
        ("Google refuses the code", (400, {"error": "invalid_grant"}), "google_said_no"),
        ("the buyer unticked Search Console on Google's page",
         (200, {"refresh_token": "r", "scope": "openid email"}), "no_search_console")):
    reset()
    gsc.begin(HOST, now=1000)
    n = json.loads(box_secrets.get(gsc.PENDING))["nonce"]
    wire.token_answer = answer
    try:
        gsc.finish("4/c", f"{n}.{HOST}", now=1001)
        ok(label, False, "no refusal")
    except gsc.Refused as e:
        ok(f"{label} -> {key}, and nothing is stored",
           e.key == key and not box_secrets.is_set(gsc.REFRESH), e.key)
reset()
gsc.begin(HOST, now=1000)
n = json.loads(box_secrets.get(gsc.PENDING))["nonce"]
try:
    gsc.finish("4/c", f"{n}.{HOST}", now=1000 + gsc.PENDING_TTL_S + 1)
    ok("a sign-in finished after ten minutes is refused", False)
except gsc.Refused as e:
    ok("a sign-in finished after ten minutes is refused, before the code is sent anywhere",
       e.key == "too_slow" and not wire.posts)

print("\ntest_picking_the_site")
reset()
gsc.begin(HOST, now=1000)
gsc.finish("4/c", f"{json.loads(box_secrets.get(gsc.PENDING))['nonce']}.{HOST}", now=1001)
rows = gsc.sites()
r = wire.posts[-1]
ok("an access token is minted through the hand-off, as this box",
   r["url"] == "https://orders.ownbox.app/google/refresh" and r["json"] == {"refresh_token": "1//refresh"}
   and r["headers"]["X-Ownbox-Host"] == HOST, str(r))
ok("Search Console is asked with that token", wire.gets[-1]["headers"] == {"Authorization": "Bearer ya29.fresh"})
ok("only sites the account can read are offered, sorted",
   [x["url"] for x in rows] == ["https://blog.acme.com/", "sc-domain:acme.com"], str(rows))
gsc.sites()
ok("the access token is reused until it expires", sum(1 for x in wire.posts if x["url"].endswith("/refresh")) == 1)
try:
    gsc.choose("https://unverified.example/")
    ok("a site the account cannot read is refused", False)
except gsc.Refused as e:
    ok("a site the account cannot read is refused", e.key == "not_a_site" and not box_secrets.is_set(gsc.PROPERTY))
ok("one of its own sites becomes the primary",
   gsc.choose("sc-domain:acme.com") == "sc-domain:acme.com" == box_secrets.get(gsc.PROPERTY))
gsc._cached.clear()
wire.refresh_answer = (400, {"error": "invalid_grant"})
try:
    gsc.sites()
    ok("a sign-in Google has ended says so", False)
except gsc.Refused as e:
    ok("a sign-in Google has ended says so", e.key == "signed_out")

print("\ntest_disconnect")
wire.refresh_answer = (200, {"access_token": "ya29.fresh", "expires_in": 3599})
gsc.disconnect()
ok("Google is asked to revoke the token", wire.posts[-1]["url"] == gsc.REVOKE
   and wire.posts[-1]["data"] == {"token": "1//refresh"})
ok("...and the box forgets all of it", not any(box_secrets.is_set(n) for n in
                                              (gsc.REFRESH, gsc.ACCOUNT, gsc.PROPERTY, gsc.PENDING)))

print("\ntest_the_screen")
reset()
c = app.test_client()
owner = state.owner_user()
c.set_cookie(dash.COOKIE, dash.new_session(owner["id"]))
_QUIET = {"ghost", "ui-ghost", "danger", "ui-danger"}


def pills(page):
    main = page.split("</nav>", 1)[-1]
    out = []
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", main, re.S):
        cls = set((re.search(r'class="([^"]*)"', m.group(1)) or [None, ""])[1].split())
        if not cls & _QUIET:
            out.append(m.group(2).strip())
    return out


r = c.get(gsc_door := "/settings/seo/google")
page = r.get_data(as_text=True)
ok("the screen renders for the owner", r.status_code == 200, str(r.status_code))
ok("not connected: one button, Connect Google Search Console",
   pills(page) == ["Connect Google Search Console"], str(pills(page)))
r = c.post(gsc_door, data={"do": "connect"})
ok("pressing it goes straight to Google", r.status_code == 303 and
   r.headers["Location"].startswith("https://accounts.google.com/"), r.headers.get("Location", ""))
n = json.loads(box_secrets.get(gsc.PENDING))["nonce"]
r = c.get(f"/settings/seo/google/done?code=4/x&state={n}.{HOST}")
ok("Google's return lands back on the screen, saying it connected",
   r.status_code == 303 and r.headers["Location"].endswith("?said=connected"), r.headers.get("Location", ""))
page = c.get(f"{gsc_door}?said=connected").get_data(as_text=True)
ok("connected: it names the account and offers its sites", "owner@acme.com" in page and "sc-domain:acme.com" in page)
ok("...with one primary: make this the primary site", pills(page) == ["Make this the primary site"], str(pills(page)))
c.post(gsc_door, data={"do": "choose", "site": "sc-domain:acme.com"})
page = c.get(f"{gsc_door}?said=chosen").get_data(as_text=True)
ok("chosen: the primary site is named", "Primary site: <b>sc-domain:acme.com</b>" in page)
ok("...and nothing on the screen shouts: changing it and disconnecting are quiet", pills(page) == [], str(pills(page)))
r = c.get("/settings/seo/google/done?error=access_denied&state=x.acme.ownbox.app")
ok("cancelling on Google's page says it was cancelled", r.headers["Location"].endswith("?said=cancelled"))
page = c.get(f"{gsc_door}?said=<script>alert(1)</script>").get_data(as_text=True)
ok("an unknown word in the address shows nothing", "<script>alert(1)</script>" not in page)
member = state.add_user("m@acme.com", role="member") if hasattr(state, "add_user") else None
if member:
    c2 = app.test_client()
    c2.set_cookie(dash.COOKIE, dash.new_session(member["id"] if isinstance(member, dict) else member))
    ok("a member is told it is the owner's", c2.get(gsc_door).status_code == 403)
claim.provisioned_host = lambda: ""
box_secrets.clear(gsc.REFRESH)
page = c.get(gsc_door).get_data(as_text=True)          # the test client is "localhost", not ownbox.app
ok("a box not on ownbox.app says where to open it, with no button",
   "ownbox.app address" in page and pills(page) == [], str(pills(page)))

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
