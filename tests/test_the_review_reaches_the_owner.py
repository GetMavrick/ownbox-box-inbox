"""The Morning Review reaches the owner of a SOLD box — in the mobile app, and by email if they add one.

OSDev1, 16:52 on 2026-09-23: *"a SOLD box cannot send email AT ALL, and this is the Morning Review
transport… the 8am review is registered, running and has nowhere to go."* Owner ruling, same day:
*"Go with C, build it. But we should have a screen in System Settings where a business owner can add
a resend email key. And then some brief instructions if they want to add some other type of email,
sending SMTP service."*

So this suite proves, on a box shaped like a sold one (no Slack, no operator key, a UTC clock):

  1. THE APP GETS IT. The owner's devices are told "Your morning review is ready", the tap opens that
     day's review, once a day, at 8 on the OWNER's clock — not 8 UTC, which is 1am in California.
  2. A NOTIFICATION NO LONGER WAITS ON EMAIL. The inbox notices went quiet on any box with no email,
     because the app notification was gated on the email beside it.
  3. THE OWNER'S OWN SENDER WORKS: a Resend key or an SMTP login, stored as one row, never echoed.
     The operator's own key still wins on the operator's box, byte for byte.
  4. SMTP IS AT MOST ONCE AND NEVER IN THE CLEAR. A dropped connection mid-send is indeterminate and
     is not sent again (invariant 4); a server that will not encrypt never receives the password.
  5. THE SCREEN REFUSES WHAT CANNOT SEND. A save is tested with a real message to the owner, and a
     failed test puts the previous setting back instead of storing a sender that fails every morning.

Run: python tests/test_the_review_reaches_the_owner.py
"""
import os
import pathlib
import re
import smtplib
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/review-reach.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + [
        "ANTHROPIC_API_KEY", "RESEND_API_KEY", "GTM_FROM_EMAIL"]:
    os.environ.pop(_k, None)

from core import box_mail, box_secrets, notify, push, report, state  # noqa: E402
from core.config import settings  # noqa: E402
from core.exceptions import RetryableError, VendorError  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


state.init_db()
settings.resend_api_key = ""
settings.gtm_from_email = ""
settings.operator_slack_user_id = ""
OWNER = state.owner_user()["id"]
with state.connect() as c:
    c.execute("UPDATE users SET email=? WHERE id=?", ("owner@acme.co", OWNER))

# ── 1. the owner's own sender ──────────────────────────────────────────────────────────────────
print("— the owner's own sender —")
ok("a sold box starts with no way to send email, and says so", not box_mail.is_configured()
   and box_mail.describe()["kind"] == "")


def refused(details, needle, label):
    try:
        box_mail.put_own(details, user_id=OWNER)
    except ValueError as e:
        ok(label, needle.lower() in str(e).lower(), str(e))
    else:
        ok(label, False, "was stored")


refused({"kind": "fax", "from": "a@acme.co"}, "Resend or another", "an unknown kind is refused")
refused({"kind": "resend", "from": "acme", "key": "re_123456789012"}, "full address",
        "a from-address that cannot exist is refused")
refused({"kind": "resend", "from": "a@acme.co", "key": "sk_live_nope_nope"}, "start with re_",
        "a key that is not a Resend key is refused, naming where to get one")
refused({"kind": "smtp", "from": "a@acme.co", "host": "smtp.acme.co", "port": "25", "user": "a",
         "password": "p"}, "587", "PORT 25 IS REFUSED — server-to-server, never where a login goes")
refused({"kind": "smtp", "from": "a@acme.co", "host": "nohost", "port": "587", "user": "a",
         "password": "p"}, "SMTP server", "a server name with no dot is refused")
refused({"kind": "smtp", "from": "a@acme.co", "host": "smtp.acme.co", "port": "587", "user": "",
         "password": ""}, "username and password", "a missing login is refused")
ok("nothing refused was stored", not box_mail.is_configured())

box_mail.put_own({"kind": "resend", "from": "hello@acme.co", "key": "re_OwnersOwnKey123"}, user_id=OWNER)
ok("a Resend key and a from-address make the box able to send", box_mail.is_configured())
d = box_mail.describe()
ok("describe() names the service and the address", d["kind"] == "resend" and d["from"] == "hello@acme.co")
ok("...and NEVER the key", "re_OwnersOwnKey123" not in repr(d), repr(d))
ok("the row in the secrets table is not the key in plain sight either",
   "re_OwnersOwnKey123" not in (box_secrets.get(box_mail.MAIL_OWN) or ""))

from core import net  # noqa: E402

seen = {}


def fake_post(url, json=None, headers=None, timeout=None, **k):
    seen.update(url=url, json=json, headers=headers)
    return 200, '{"id":"msg_1"}'


_real_post = net.post_public
net.post_public = fake_post
ref = box_mail.send("owner@acme.co", "s", "t", "<p>t</p>", idem_key="k1", sender_name="Morning Review",
                    note="morning_review")
ok("a send uses the OWNER'S key", seen["headers"]["Authorization"] == "Bearer re_OwnersOwnKey123")
ok("...from the OWNER'S address", seen["json"]["from"] == "Morning Review <hello@acme.co>", seen["json"]["from"])
ok("...and still carries the mark the inbox sweep skips", seen["json"]["headers"] == {box_mail.ORIGIN_HEADER: "notice"})

settings.resend_api_key = "re_OPERATOR"
settings.gtm_from_email = "review@operator.example"
box_mail.send("owner@acme.co", "s", "t", "<p>t</p>", idem_key="k2", sender_name="Morning Review", note="x")
ok("THE OPERATOR'S ENVIRONMENT STILL WINS on the operator's box",
   seen["headers"]["Authorization"] == "Bearer re_OPERATOR" and "review@operator.example" in seen["json"]["from"])
ok("...and the screen is told it is the operator's, so it offers no forms", box_mail.describe()["operator"] is True)
settings.resend_api_key = ""
settings.gtm_from_email = ""
net.post_public = _real_post

# ── 2. SMTP: never in the clear, at most once ───────────────────────────────────────────────────
print("\n— SMTP —")


class FakeSMTP:
    log = []
    starttls_offered = True
    fail_on_send = None
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.log.append(("connect", type(self).__name__, host, port))

    def ehlo(self):
        pass

    def has_extn(self, name):
        return FakeSMTP.starttls_offered if name == "starttls" else False

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.log.append(("login", user, password))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"no")

    def send_message(self, msg):
        FakeSMTP.log.append(("send", msg["To"], msg["From"], msg[box_mail.ORIGIN_HEADER]))
        if FakeSMTP.fail_on_send:
            raise FakeSMTP.fail_on_send

    def quit(self):
        pass

    def close(self):
        pass


class FakeSMTP_SSL(FakeSMTP):
    pass


smtplib.SMTP, smtplib.SMTP_SSL = FakeSMTP, FakeSMTP_SSL
box_mail.put_own({"kind": "smtp", "from": "hello@acme.co", "host": "smtp.gmail.com", "port": "587",
                  "user": "hello@acme.co", "password": "abcd efgh ijkl mnop"}, user_id=OWNER)
ok("a Google app password is stored WITHOUT the spaces Google displays it with",
   box_mail.own()["password"] == "abcdefghijklmnop")
box_mail.send("owner@acme.co", "s", "t", "", idem_key="s1", sender_name="Morning Review", note="x")
kinds = [e[0] for e in FakeSMTP.log]
ok("587 upgrades with STARTTLS BEFORE the login", kinds[:3] == ["connect", "starttls", "login"], str(kinds))
ok("...and the message goes, from the owner's address, carrying the mark",
   ("send", "owner@acme.co", "Morning Review <hello@acme.co>", "notice") in FakeSMTP.log, str(FakeSMTP.log))

FakeSMTP.log.clear()
box_mail.send("owner@acme.co", "s", "t", "", idem_key="s1", sender_name="Morning Review", note="x")
ok("THE SAME idem_key IS NOT SENT TWICE", not any(e[0] == "send" for e in FakeSMTP.log), str(FakeSMTP.log))

FakeSMTP.log.clear()
FakeSMTP.fail_on_send = OSError("connection reset")
try:
    box_mail.send("owner@acme.co", "s", "t", "", idem_key="s2", sender_name="R", note="x")
    ok("a connection dropped mid-send is an error", False)
except VendorError as e:
    ok("A CONNECTION DROPPED MID-SEND IS INDETERMINATE (invariant 4)", "indeterminate" in str(e), str(e))
FakeSMTP.fail_on_send = None
FakeSMTP.log.clear()
box_mail.send("owner@acme.co", "s", "t", "", idem_key="s2", sender_name="R", note="x")
ok("...and it is NEVER resent under the same key", not any(e[0] == "send" for e in FakeSMTP.log))

FakeSMTP.log.clear()
FakeSMTP.starttls_offered = False
try:
    box_mail.send("owner@acme.co", "s", "t", "", idem_key="s3", sender_name="R", note="x")
    ok("a server that will not encrypt is refused", False)
except VendorError as e:
    ok("A SERVER THAT WILL NOT ENCRYPT NEVER RECEIVES THE PASSWORD",
       not any(e2[0] == "login" for e2 in FakeSMTP.log) and "STARTTLS" in str(e), str(FakeSMTP.log))
FakeSMTP.starttls_offered = True

FakeSMTP.log.clear()
FakeSMTP.fail_login = True
try:
    box_mail.send("owner@acme.co", "s", "t", "", idem_key="s4", sender_name="R", note="x")
    ok("a refused login is an error", False, "it returned")
except VendorError as e:
    ok("a refused login is said as a refused login", "refused the username or password" in str(e), str(e))
FakeSMTP.fail_login = False
FakeSMTP.log.clear()
box_mail.send("owner@acme.co", "s", "t", "", idem_key="s4", sender_name="R", note="x")
ok("...and because nothing was claimed before the login, a later retry DOES send",
   any(e[0] == "send" for e in FakeSMTP.log), str(FakeSMTP.log))

box_mail.put_own({"kind": "smtp", "from": "hello@acme.co", "host": "smtp.acme.co", "port": "465",
                  "user": "u", "password": "p w"}, user_id=OWNER)
FakeSMTP.log.clear()
box_mail.send("owner@acme.co", "s", "t", "", idem_key="s5", sender_name="R", note="x")
ok("465 is TLS from the first byte", FakeSMTP.log[0][1] == "FakeSMTP_SSL", str(FakeSMTP.log))
ok("...and a non-Google password keeps its spaces", ("login", "u", "p w") in FakeSMTP.log)

FakeSMTP.log.clear()
FakeSMTP.fail_on_send = smtplib.SMTPRecipientsRefused({"x": (550, b"no")})
okd, said = box_mail.send_test(OWNER)
ok("send_test reports a refusal as a sentence, not a traceback", okd is False and said, said)
FakeSMTP.fail_on_send = None
okd, said = box_mail.send_test(OWNER)
ok("SEND_TEST SAYS WHERE IT WENT AND TO LOOK — never 'connected'",
   okd and "owner@acme.co" in said and "Look for it" in said, said)

# ── 3. the app notification no longer waits on email ────────────────────────────────────────────
print("\n— the app notification stands on its own —")
box_mail.clear_own(user_id=OWNER)
pushed = []
_real_send = push.send
push.send = lambda sub, **k: (pushed.append(k) or (True, "ok"))
_real_subs = push.subscriptions_for
devices = {OWNER: [{"endpoint": "https://push.example/1", "p256dh": "x", "auth": "y"}]}
push.subscriptions_for = lambda uid: devices.get(str(uid), [])
_mailed = []
_real_mail_send = box_mail.send
box_mail.send = lambda *a, **k: _mailed.append(a) or "id"
notify._cfg = lambda: {"enabled": True, "timezone": "America/Los_Angeles"}
morning = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)          # 09:30 Pacific
res = notify.send_notice("inbox_waiting", "subject", "body", now=morning)
ok("WITH NO EMAIL SET UP, THE APP NOTIFICATION STILL GOES", res.get("notified") == 1 and pushed, str(res))
ok("...and no email is attempted", not _mailed)
devices.clear()
res = notify.send_notice("other_key", "s", "b", now=morning + timedelta(minutes=5))
ok("with neither channel the notice says OFF — never 'sent' and never 'failed'",
   res["skipped"] == "off" and "nobody has the app" in res["detail"], str(res))
box_mail.send = _real_mail_send

# ── 4. the review itself, on a box shaped like a sold one ───────────────────────────────────────
print("\n— the Morning Review on a sold box —")
TMP = pathlib.Path(tempfile.mkdtemp())
report._state_path = lambda: TMP / "review.json"
report.tz = lambda: ZoneInfo("UTC")                                    # sold boxes ship on UTC
report._cfg = lambda: {"enabled": True, "hour_local": 8}
report.snapshot = lambda d: {"written": [], "failed": []}
report.close_open_days = lambda now=None: []
devices[OWNER] = [{"endpoint": "https://push.example/1", "p256dh": "x", "auth": "y"}]
pushed.clear()

night = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)                # 8am UTC = 2am Pacific
r = report.run(night)
ok("8AM UTC IS 2AM IN CALIFORNIA — the owner is not woken", r["status"] == "quiet" and not pushed, str(r))
at8 = datetime(2026, 9, 24, 15, 5, tzinfo=timezone.utc)                 # 08:05 Pacific
r = report.run(at8)
ok("at 8 on the owner's clock the app is told", r["status"] == "sent" and r.get("app") == "sent", str(r))
ok("...with one fixed sentence, never a figure", pushed and pushed[-1]["body"] == "Your morning review is ready"
   and pushed[-1]["title"] == "Morning Review", str(pushed[-1:]))
ok("...and the tap opens THAT day's review", pushed[-1]["navigate"] == "/app/review/2026-09-23", pushed[-1]["navigate"])
n = len(pushed)
ok("once a day: the same morning again sends nothing", report.run(at8 + timedelta(hours=1))["status"] == "quiet" and len(pushed) == n)
ok("...NOR in the Pacific evening, when the box's UTC date has already rolled over",
   report.run(datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc))["status"] == "quiet" and len(pushed) == n)
ok("the next morning, once again", report.run(at8 + timedelta(days=1))["status"] == "sent" and len(pushed) == n + 1)

devices.clear()
ok("no device, no email, no Slack: the review says it has nobody, rather than pretending",
   report.run(at8 + timedelta(days=2))["status"] == "no_operator")

mailed = []
box_mail.put_own({"kind": "resend", "from": "hello@acme.co", "key": "re_OwnersOwnKey123"}, user_id=OWNER)
r = report.run(at8 + timedelta(days=2), send_email=lambda to, *a, **k: mailed.append(to))
ok("WITH EMAIL SET UP, the review is mailed to the OWNER'S OWN sign-in address",
   r.get("email") == "sent" and mailed == ["owner@acme.co"], str(r))

push.send, push.subscriptions_for = _real_send, _real_subs

# ── 5. the payload and the service worker ───────────────────────────────────────────────────────
print("\n— what reaches the device —")
import json as _json  # noqa: E402

raw = {}
_enc = push.encrypt
push.encrypt = lambda body, **k: raw.setdefault("b", body) or b"x"
_post = net.post_public
net.post_public = lambda *a, **k: (201, "")
_vap = push._vapid_header
push._vapid_header = lambda *a, **k: {}
push.send({"endpoint": "https://push.example/1", "p256dh": "x", "auth": "y"}, title="Morning Review",
          body="Your morning review is ready", navigate="/app/review/2026-09-23")
got = _json.loads(raw["b"])
ok("the payload carries the caller's title and sentence", got["title"] == "Morning Review"
   and got["body"] == "Your morning review is ready", str(got))
raw.clear()
push.send({"endpoint": "https://push.example/1", "p256dh": "x", "auth": "y"}, waiting=3)
ok("...and the inbox's own notice is exactly what it was", _json.loads(raw["b"]) ==
   {"title": "Unified Inbox", "body": "3 waiting for a reply", "navigate": "/inbox/inbox"})
push.encrypt, net.post_public, push._vapid_header = _enc, _post, _vap

# THE SERVICE WORKER IS THE INBOX MACHINE'S, and a Lead box ships without it (test_recipe_ships runs
# every shipped suite inside one). There, no worker means no mobile app notification to open anything
# — a true absence, said so, not a skip. The repo suite, which has the inbox machine, checks it.
_sw_file = ROOT / "marketing/customer_voice/app.py"
if _sw_file.is_file():
    sw = _sw_file.read_text()
    guard = re.search(r"(?:var door = .*?)?if \(typeof to !== 'string'.*?\{ to = '/inbox/inbox'; \}", sw, re.S).group(0)
    ok("the service worker lets a tap open the review", "'/app/review/'" in guard and "'/app/review'" in guard)
    ok("...and still sends anything else back to the inbox", "to.indexOf('/inbox/') !== 0" in guard)
else:
    print("  --   no inbox machine ships on this box, so there is no service worker to check here")

# ── 6. the screen ───────────────────────────────────────────────────────────────────────────────
print("\n— /settings/email —")
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402


def client_for(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def visible(html: str) -> str:
    import html as _h
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return _h.unescape(re.sub(r"<[^>]+>", " ", html))


box_mail.clear_own(user_id=OWNER)
owner = client_for(OWNER)
member = client_for(state.add_user("sam@acme.co", name="Sam", role="member")["id"])
page = owner.get("/settings/email").get_data(as_text=True)
text = visible(page)
ok("the owner sees that no email is set up — and that the app still carries the review",
   "No email is set up" in text and "mobile app" in text)
ok("...a Resend form with brief steps", "Resend API key" in text and "Under Domains" in text and "API Keys" in text)
ok("...and brief SMTP instructions for another service",
   "smtp.gmail.com" in text and "smtp.office365.com" in text and "app password" in text)
ok("fields are 16px on a mobile device (the shared input rule), and the port asks for digits",
   'inputmode="numeric"' in page)
ok("a member is refused the page", member.get("/settings/email").status_code == 403)
ok("...and the POST", member.post("/settings/email", data={"do": "resend", "from": "x@y.co",
                                                           "key": "re_123456789012"}).status_code == 403)
ok("...and stored nothing", not box_mail.is_configured())
anon = app.test_client().get("/settings/email")
ok("a stranger is sent to sign in", anon.status_code in (302, 303) and "/dash/login" in anon.headers.get("Location", ""))

text = visible(owner.post("/settings/email", data={"do": "resend", "from": "hello@acme.co",
                                                   "key": "nope"}).get_data(as_text=True))
ok("a bad key is refused ON the page it was typed into", "That was not saved" in text and "re_" in text)

_real_test = box_mail.send_test
box_mail.send_test = lambda uid: (False, "Resend did not accept that key.")
text = visible(owner.post("/settings/email", data={"do": "resend", "from": "hello@acme.co",
                                                   "key": "re_WrongButShaped1"}).get_data(as_text=True))
ok("A SAVE WHOSE TEST FAILS IS NOT KEPT", "Not saved" in text and not box_mail.is_configured(), text[:300])

box_mail.put_own({"kind": "smtp", "from": "old@acme.co", "host": "smtp.acme.co", "port": "587",
                  "user": "u", "password": "p"}, user_id=OWNER)
owner.post("/settings/email", data={"do": "resend", "from": "hello@acme.co", "key": "re_WrongButShaped1"})
ok("...and the setting that was there before is put back, untouched",
   box_mail.own().get("kind") == "smtp" and box_mail.own().get("from") == "old@acme.co", str(box_mail.describe()))

box_mail.send_test = lambda uid: (True, "Sent to owner@acme.co. Look for it.")
page = owner.post("/settings/email", data={"do": "resend", "from": "hello@acme.co",
                                           "key": "re_GoodKey12345"}).get_data(as_text=True)
text = visible(page)
ok("a save whose test sends is kept, and says to look for it",
   "Saved, and a test is on its way" in text and box_mail.describe()["kind"] == "resend")
ok("THE KEY IS NEVER ECHOED BACK onto the page", "re_GoodKey12345" not in page)
ok("the page now says email is set up, from which address", "Email is set up" in text and "hello@acme.co" in text)
settings_page = visible(owner.get("/settings").get_data(as_text=True))
ok("System Settings lists it for the owner", "Outbound Email" in settings_page and "hello@acme.co" in settings_page)
ok("...and not for a member", "Outbound Email" not in visible(member.get("/settings").get_data(as_text=True)))

text = visible(owner.post("/settings/email", data={"do": "remove"}).get_data(as_text=True))
ok("the owner can stop it, and the page says the app carries on",
   not box_mail.is_configured() and "Email is off" in text and "mobile app" in text)
box_mail.send_test = _real_test

settings.resend_api_key, settings.gtm_from_email = "re_OPERATOR", "review@operator.example"
text = visible(owner.get("/settings/email").get_data(as_text=True))
ok("on the operator's box the screen shows the service it came with and offers no forms",
   "service it was set up with" in text and "Resend API key" not in text and "Stop sending" not in text)
settings.resend_api_key = settings.gtm_from_email = ""

print("\n— the vocabulary —")
RESERVED = re.compile(r"\b(phone|phones|ring|rings|call|calls|called|calling|dial|line|lines|voice|"
                      r"answer|answers|answered)\b", re.I)
screens = [visible(owner.get("/settings/email").get_data(as_text=True))]
box_mail.put_own({"kind": "smtp", "from": "hello@acme.co", "host": "smtp.acme.co", "port": "587",
                  "user": "u", "password": "p"}, user_id=OWNER)
screens.append(visible(owner.get("/settings/email").get_data(as_text=True)))
from core.dash import box_email  # noqa: E402

screens += [box_email._note(True, "x", "Email is off"), visible(box_email._smtp_form())]
hits = sorted({m.group(0) for s in screens for m in RESERVED.finditer(s)})
ok("no reserved noun anywhere a buyer reads on this screen", not hits, str(hits))

print("\nALL REVIEW-REACHES-THE-OWNER CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
