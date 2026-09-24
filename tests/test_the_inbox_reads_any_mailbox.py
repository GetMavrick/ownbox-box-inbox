"""The inbox reads a mailbox wherever it lives — not only Gmail — and says plainly when it cannot.

#1483 finding 1, OSDev1's top backend item on 2026-09-23: *"the inbox only knows Gmail. Add a server
field with presets… DONE-WHEN: a non-Gmail mailbox connects on a delivered box."* Finding 2 with it:
*"link straight to Google App passwords page."*

What this suite holds:

  1. PRESETS AND A SERVER FIELD. Gmail, Yahoo, iCloud, AOL, Zoho, Fastmail and GoDaddy's classic mail
     each map to their IMAP host, and "Another provider" takes the host the buyer types — a host,
     never a URL or a port, and nothing that is not one.
  2. MICROSOFT IS REFUSED UP FRONT, WITH THE REASON. Microsoft turned off password sign-in for IMAP
     (Microsoft 365 in 2022–23, Outlook.com and Hotmail on 2024-09-16), app passwords included, so
     no password could ever connect one. A Microsoft server or address is refused before any
     password is sent anywhere — the verifier is never even asked.
  3. THE GOOGLE-ONLY RULES STAY GOOGLE'S. Sixteen letters is Google's app-password shape; iCloud's
     has dashes, a web host's is whatever the buyer chose. A refusal from another provider never
     tells a buyer to go and look in their Google account.
  4. BOTH SCREENS OFFER THE SAME THING: the setup card and /inbox/mailbox show the provider list,
     the server field, the App passwords link (new tab) and the other providers' one-liners.

Run: python tests/test_the_inbox_reads_any_mailbox.py
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/any-mailbox.db"
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import box_secrets as bs, state  # noqa: E402
from core.vendors.mailbox import (classify_auth_failure, providers, smtp_host_for,  # noqa: E402
                                  smtp_port_for)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


state.init_db()
ASKED = []
bs._mailbox_verify = lambda host, user, password: (ASKED.append((host, user, password))
                                                   or (True, "connected", ""))
bs._mailbox_verify_send = lambda host, user, password: (True, "can_send", "")


def stored() -> dict:
    return bs.email_credential()


def refused(label, needle, **kw):
    ASKED.clear()
    try:
        bs.put_email(**kw)
    except bs.SecretRejected as e:
        ok(label, needle.lower() in str(e).lower(), str(e))
        return str(e)
    ok(label, False, "was stored")
    return ""


# ── 1. presets and the server field ──────────────────────────────────────────────────────────────
print("— presets and the server field —")
# IMAP host, then the sending server and port each provider's own help page gives (#1485 review,
# OSDev1: "every preset's SMTP host is checked against the provider's own help page").
expect = {"gmail": ("imap.gmail.com", "smtp.gmail.com", 587),
          "yahoo": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 587),
          "icloud": ("imap.mail.me.com", "smtp.mail.me.com", 587),
          "aol": ("imap.aol.com", "smtp.aol.com", 465),
          "zoho": ("imap.zoho.com", "smtp.zoho.com", 587),
          "zoho_pro": ("imappro.zoho.com", "smtppro.zoho.com", 587),
          "fastmail": ("imap.fastmail.com", "smtp.fastmail.com", 587),
          "godaddy": ("imap.secureserver.net", "smtpout.secureserver.net", 587)}
for key, (host, smtp, port) in expect.items():
    ok(f"{key} reads from {host}", providers.host_for(key) == host)
    ok(f"...and sends through {smtp}:{port}, the server its provider documents",
       (smtp_host_for(host), smtp_port_for(host)) == (smtp, port),
       f"{smtp_host_for(host)}:{smtp_port_for(host)}")
ok("every preset with a server has its sending server written down, with a source",
   all(p[1] in providers.SUBMISSION and providers.SUBMISSION[p[1]][2].startswith("https://")
       for p in providers.PRESETS.values() if p[1]),
   str([k for k, p in providers.PRESETS.items() if p[1] and p[1] not in providers.SUBMISSION]))
ok("a server the buyer typed still follows the imap. -> smtp. convention on 587",
   (smtp_host_for("imap.lopezplumbing.com"), smtp_port_for("imap.lopezplumbing.com"))
   == ("smtp.lopezplumbing.com", 587))
ok("Another provider takes the typed server", providers.host_for("other", "mail.lopezplumbing.com")
   == "mail.lopezplumbing.com")
ok("...a pasted URL or port is cut to the host",
   providers.host_for("other", "https://Mail.LopezPlumbing.com:993/inbox") == "mail.lopezplumbing.com")
for bad in ("", "localhost", "not a host", "imap", "http://"):
    try:
        providers.host_for("other", bad)
        ok(f"'{bad}' is refused as a server", False)
    except ValueError as e:
        ok(f"'{bad}' is refused as a server, with what to put in", "imap.example.com" in str(e))
try:
    providers.host_for("myspace")
    ok("an unknown provider is refused", False)
except ValueError:
    ok("an unknown provider is refused", True)

# ── 1b. the connection each port needs ──────────────────────────────────────────────────────────
print("\n— 587 is STARTTLS, 465 is TLS from the first byte, and both paths use one door —")
import smtplib as _smtplib  # noqa: E402

from core.vendors.mailbox import open_submission  # noqa: E402


class _FakeConn:
    def __init__(self, kind, host, port, **kw):
        self.kind, self.host, self.port, self.steps = kind, host, port, []
        self.tls_context = kw.get("context")

    def ehlo(self):
        self.steps.append("EHLO")

    def starttls(self):
        self.steps.append("STARTTLS")

    def close(self):
        pass


_real = (_smtplib.SMTP, _smtplib.SMTP_SSL)
_smtplib.SMTP = lambda host, port, **kw: _FakeConn("plain", host, port, **kw)      # type: ignore
_smtplib.SMTP_SSL = lambda host, port, **kw: _FakeConn("tls", host, port, **kw)    # type: ignore
try:
    aol = open_submission("imap.aol.com", 5)
    gmail = open_submission("imap.gmail.com", 5)
finally:
    _smtplib.SMTP, _smtplib.SMTP_SSL = _real
ok("AOL opens smtp.aol.com:465 with TLS from the first byte, verified against the system CAs",
   (aol.kind, aol.host, aol.port, aol.steps) == ("tls", "smtp.aol.com", 465, ["EHLO"])
   and aol.tls_context is not None, f"{aol.kind} {aol.host}:{aol.port} {aol.steps}")
ok("Gmail opens smtp.gmail.com:587, STARTTLS, and EHLO again on the encrypted channel",
   (gmail.kind, gmail.host, gmail.port, gmail.steps)
   == ("plain", "smtp.gmail.com", 587, ["EHLO", "STARTTLS", "EHLO"]),
   f"{gmail.kind} {gmail.host}:{gmail.port} {gmail.steps}")
# ONE DOOR: a check that passes on the set-up screen must mean the real send reaches the same
# server the same way. Two hand-rolled connections are how the send drifted to 587-only before.
_verify_src = (ROOT / "core/vendors/mailbox/verify.py").read_text()
_send_fn = _verify_src.split("def verify_send", 1)[1].split("\ndef ", 1)[0]
ok("the set-up check connects through open_submission, not its own smtplib.SMTP",
   "open_submission(" in _send_fn and "smtplib.SMTP(" not in _send_fn)
_ec = ROOT / "marketing/customer_voice/inbox/email_channel.py"
if _ec.is_file():
    _ec_src = _ec.read_text()
    ok("the real reply send connects through open_submission, not its own smtplib.SMTP",
       "open_submission(" in _ec_src and "smtplib.SMTP(" not in _ec_src)
else:
    print("  --   no inbox machine ships on this box, so there is no reply send to check here")


# ── 2. Microsoft, up front ───────────────────────────────────────────────────────────────────────
print("\n— Microsoft is refused before a password goes anywhere —")
for host, user in (("outlook.office365.com", "maria@lopezplumbing.com"),
                   ("imap-mail.outlook.com", "maria@outlook.com"),
                   ("imap.gmail.com", "maria@hotmail.com"),
                   ("imap.gmail.com", "maria@live.com")):
    said = refused(f"{host} / {user}: refused, and says why", "Microsoft",
                   host=host, user=user, password="abcdabcdabcdabcd")
    ok("...AND THE VERIFIER WAS NEVER ASKED — no password left the box", ASKED == [], str(ASKED))
ok("the sentence says what is true: no password works, and nothing was stored",
   "not even an app password" in said and "Nothing was stored" in said, said)

# ── 3. the Google-only rules stay Google's ───────────────────────────────────────────────────────
print("\n— each provider's own password —")
refused("Gmail still refuses a password that is not an app password", "Google password",
        host="imap.gmail.com", user="maria@gmail.com", password="hunter2hunter2")
bs.put_email(host="imap.gmail.com", user="maria@gmail.com", password="abcd efgh ijkl mnop")
ok("Gmail still takes the sixteen letters, spaces and all", stored().get("password") == "abcdefghijklmnop")
bs.put_email(host="imap.mail.yahoo.com", user="maria@yahoo.com", password="qwertyuiopasdfgh")
ok("A YAHOO MAILBOX IS STORED, on Yahoo's server", stored().get("host") == "imap.mail.yahoo.com"
   and stored().get("user") == "maria@yahoo.com")
bs.put_email(host="imap.mail.me.com", user="maria@icloud.com", password="abcd-efgh-ijkl-mnop")
ok("an iCloud app-specific password keeps its dashes", stored().get("password") == "abcd-efgh-ijkl-mnop")
bs.put_email(host="mail.lopezplumbing.com", user="maria@lopezplumbing.com", password=" Plumb3r! ")
ok("a web host's own password is taken as typed, bar the paste's surrounding space",
   stored().get("password") == "Plumb3r!" and stored().get("host") == "mail.lopezplumbing.com")
ok("...and the verifier was asked on THAT server", ASKED[-1][0] == "mail.lopezplumbing.com", str(ASKED[-1:]))
refused("an empty password on another provider is refused, naming app passwords", "app password",
        host="imap.mail.yahoo.com", user="maria@yahoo.com", password="   ")
refused("a server that is not one is refused in the store too", "imap.example.com",
        host="not a host", user="maria@x.com", password="pw")

g = classify_auth_failure("[AUTHENTICATIONFAILED] Invalid credentials", "imap.gmail.com").detail
y = classify_auth_failure("[AUTHENTICATIONFAILED] Invalid credentials", "imap.mail.yahoo.com").detail
ok("a Gmail refusal still names Google", "Google" in g)
ok("A YAHOO REFUSAL NEVER SENDS THE BUYER TO GOOGLE", "Google" not in y and "app password" in y, y)
ok("...and an empty host is Gmail, the only default there is",
   "Google" in classify_auth_failure("x", "").detail)

# ── 4. the screens ───────────────────────────────────────────────────────────────────────────────
# THE SCREENS ARE THE INBOX MACHINE'S, and a Lead box ships this suite (it imports only core) without
# them — test_recipe_ships runs every shipped suite inside one. There the store and the refusal
# wording above are still checked; the screens are a true absence, said so, not a skip.
if (ROOT / "marketing" / "customer_voice" / "app.py").is_file():
    print("\n— both screens —")
    from core.config import settings  # noqa: E402
    from core.dispatch import app  # noqa: E402

    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})


    def text(html_: str) -> str:
        import html as _h
        s = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
        return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", s)).split())


    bs.clear_email()
    for path in ("/inbox/setup", "/inbox/mailbox"):
        page = c.get(path).get_data(as_text=True)
        words = text(page)
        ok(f"{path}: the provider list, Gmail first", '<select name="provider"' in page
           and page.index('value="gmail"') < page.index('value="yahoo"'))
        ok(f"{path}: every preset is offered", all(f'value="{k}"' in page for k in providers.PRESETS))
        ok(f"{path}: the server field for Another provider", 'name="host"' in page)
        ok(f"{path}: THE APP PASSWORDS LINK, in a new tab",
           'href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer"' in page)
        ok(f"{path}: 'Not on Gmail?' lists the other providers and Microsoft's reason",
           "Not on Gmail?" in words and "Yahoo Mail" in words and "not even an app password" in words)

    r = c.post("/inbox/mailbox", data={"provider": "yahoo", "user": "maria@yahoo.com",
                                       "password": "qwertyuiopasdfgh"})
    ok("A YAHOO MAILBOX CONNECTS THROUGH THE SCREEN", r.status_code in (302, 303)
       and stored().get("host") == "imap.mail.yahoo.com", str(r.status_code))
    r = c.post("/inbox/mailbox", data={"provider": "other", "host": "nonsense", "user": "m@x.com",
                                       "password": "pw"})
    ok("a bad server is refused ON the screen, with what to put in",
       "imap.example.com" in text(r.get_data(as_text=True)))
    r = c.post("/inbox/mailbox", data={"provider": "gmail", "user": "maria@outlook.com",
                                       "password": "abcdabcdabcdabcd"})
    ok("an Outlook address is refused on the screen with Microsoft's reason",
       "Microsoft" in text(r.get_data(as_text=True)))
    r = c.post("/inbox/mailbox", data={"user": "maria@gmail.com", "password": "abcdabcdabcdabcd"})
    ok("a form from before the provider field posts no provider, and that is Gmail",
       r.status_code in (302, 303) and stored().get("host") == "imap.gmail.com")
else:
    print("  --   no inbox machine ships on this box, so there are no mailbox screens to check here")

print("\n— the vocabulary —")
RESERVED = re.compile(r"\b(phone|phones|ring|rings|call|calls|called|calling|dial|line|lines|voice|"
                      r"answer|answers|answered)\b", re.I)
said = " ".join([providers.MICROSOFT_SAID, y] + [v[2] for v in providers.PRESETS.values()]
                + [v[0] for v in providers.PRESETS.values()])
hits = sorted({m.group(0) for m in RESERVED.finditer(said)})
ok("no reserved noun in anything this adds for a buyer to read", not hits, str(hits))

print("\nALL ANY-MAILBOX CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
