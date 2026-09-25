"""The inbox's connections are the owner's to change, and nothing changes one by merely loading a URL.

Owner, 2026-09-24, asked who on a box with a team may change or switch off the mailbox, the social
accounts and drafting: "Owner only". A member still reads, replies and sends, and sees each
connection's state.

Found in the same launch sweep, and fixed with it:
  · "Turn off" on the drafts row called `clear_anthropic`: it deleted the BOX's AI key from this
    machine's screen (the key's one home is System Settings), took it from every machine, and on a
    box drafting with a Claude subscription did not stop drafting at all. It is a switch now,
    `inbox.drafts.enabled` in `core.box_settings`, and the AI account is untouched.
  · All three "off" controls were `<a href="...?off=1">`. A GET that changes state fires for an
    iPhone long-press preview, a link prefetch, or a link on another site (the Lax session cookie
    rides a top-level GET). They are posted forms now, and a GET changes nothing.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · a member's POST changes the mailbox, the social accounts or drafting;
  · a member is shown a control for any of them, or a form, on Settings or on their pages;
  · a GET with ?off=1 changes anything, or any inbox screen links one again;
  · turning drafting off touches the AI account, or stops being honoured by the drafter.

Run: python tests/test_the_inbox_connections_are_the_owners.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "owners.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets as bs, dash                                 # noqa: E402
from core.dispatch import app                                            # noqa: E402
from marketing.customer_voice.drafter import draft                       # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def client(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


OWNER = state.owner_user()["id"]
MEMBER = state.add_user("sam@acme.com", name="Sam")["id"]
owner, member = client(OWNER), client(MEMBER)
KEY = "sk-ant-api03-" + "k" * 60


def setup():
    bs.put(bs.ANTHROPIC, KEY)
    bs.put(bs.ANTHROPIC_STATUS, "connected")
    bs.put(bs.EMAIL, '{"host": "imap.gmail.com", "user": "owner@acme.com", "password": "x"}')
    bs.put(bs.EMAIL_STATUS, "connected")
    bs.put(bs.ZERNIO, "sk_" + "z" * 40)
    bs.put(bs.ZERNIO_STATUS, "connected")
    from core import box_settings
    box_settings.clear("inbox", "drafts.enabled")


def intact():
    return (bs.get(bs.ANTHROPIC) == KEY and bool(bs.email_credential())
            and bs.zernio_key() != "" and draft.enabled())


print("test_a_get_changes_nothing")
setup()
for path in ("/inbox/drafts?off=1", "/inbox/mailbox?off=1", "/inbox/connect?off=1"):
    owner.get(path)
    ok(f"the owner loading {path} changes nothing", intact())

print("\ntest_no_screen_links_a_switch")
for path in ("/inbox/settings", "/inbox/drafts", "/inbox/mailbox", "/inbox/connect"):
    html = owner.get(path).get_data(as_text=True)
    ok(f"{path} links no ?off=1", "?off=1" not in html, re.findall(r'href="[^"]*off=1[^"]*"', html))

print("\ntest_the_owner_turns_drafting_off_and_the_account_stays")
r = owner.post("/inbox/drafts", data={"drafting": "off"})
ok("turn off is honoured", r.status_code == 303 and not draft.enabled(), str(r.status_code))
ok("...and the AI account is untouched", bs.get(bs.ANTHROPIC) == KEY)
s = owner.get("/inbox/settings").get_data(as_text=True)
ok("Settings says Off, with a Turn on switch", "Off. Ownbox is not writing replies" in s
   and 'name="drafting" value="on"' in s)
owner.post("/inbox/drafts", data={"drafting": "on"})
ok("turn on brings it back with no sign-in", draft.enabled() and bs.get(bs.ANTHROPIC) == KEY)

print("\ntest_the_owner_switches_off_with_a_post")
r = owner.post("/inbox/mailbox", data={"off": "1"})
ok("stop reading this inbox is a POST that works", r.status_code == 303 and not bs.email_credential())
setup()
r = owner.post("/inbox/connect", data={"off": "1"})
ok("disconnect is a POST that works", r.status_code == 303 and bs.zernio_key() == "")

print("\ntest_a_member_changes_nothing")
setup()
for path, data in (("/inbox/drafts", {"drafting": "off"}), ("/inbox/mailbox", {"off": "1"}),
                   ("/inbox/mailbox", {"user": "x@y.com", "password": "abcdabcdabcdabcd"}),
                   ("/inbox/connect", {"off": "1"}), ("/inbox/connect", {"key": "sk_" + "q" * 40})):
    r = member.post(path, data=data)
    ok(f"a member's POST to {path} {sorted(data)} is refused", r.status_code == 403, str(r.status_code))
    ok("...and the box is exactly as it was", intact())
r = member.get("/inbox/connect/instagram")
ok("a member cannot start connecting a social account", r.status_code == 403, str(r.status_code))

print("\ntest_a_member_reads_the_state_and_whose_it_is")
for path in ("/inbox/settings", "/inbox/drafts", "/inbox/mailbox", "/inbox/connect"):
    html = member.get(path).get_data(as_text=True)
    main = html.split("</nav>", 1)[-1]
    ok(f"{path} renders for a member", member.get(path).status_code == 200)
    ok(f"{path} offers a member no switch", 'name="off"' not in main and 'name="drafting"' not in main
       and 'name="password"' not in main and 'name="key"' not in main)
    ok(f"{path} says whose it is", "Only the owner of this box can change" in main)
ok("the member still sees which inbox is being read",
   "owner@acme.com" in member.get("/inbox/mailbox").get_data(as_text=True))

print("\n— and this file cannot silently fall out of CI —")
if (ROOT / ".github").is_dir():                  # a buyer's box has no repository
    ok("test_the_inbox_connections_are_the_owners is in the workflow's suite list",
       "test_the_inbox_connections_are_the_owners" in (ROOT / ".github/workflows/tests.yml").read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
