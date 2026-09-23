"""A new buyer lands on the dashboard and is told what to do next.

WHAT THIS BOX DID ON GO-TO-MARKET MORNING. Measured on a box exported from
release/2026.09.18.3 and signed into with nothing connected: `/dashboard` carried ZERO links to
the set-up screen, the box's own settings page carried ZERO, and the rail did not list it — the
only link in the whole product was one on the inbox page. So the first thing a buyer read after
signing in was "Nothing to report yet", with **Stop everything** as the loudest control on the
page, on a box that had not started.

The set-up screen itself was never the problem. It says "Set up your box. Three things only you
can do. Your box is already running — this is what tells it where to listen", with numbered
Google instructions. It was the best screen in the product and it was reachable by accident.

WHAT THIS SUITE HOLDS:
  · the card names how much is left, and names WHICH steps, and goes away when it is done
  · it never claims nothing arrives once something is connected
  · `Stop everything` stands down while the box has not started, and comes back after
  · core finds the set-up screen through the REGISTRY, never by knowing a machine's URL — so a
    box with no set-up screen draws no card rather than a button to a 404

Run: python tests/test_the_box_shows_a_buyer_the_way_in.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "wayin.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on — OSDev5's rule, 2026-09-18,
# and it cost him four assertions. Our dev boxes carry these in the environment; CI carries none.
#
# BY PREFIX, NAMING NO TENANT. The first version of this loop spelled out the operator's own
# `ZERNIO_API_KEY_<slug>` variable, copied from OSDev5's post without noticing that THIS FILE SHIPS
# INTO EVERY CUSTOMER BOX. `tests/test_box_uses_the_buyers_key.py` refuses a box carrying that slug
# in any file, in any case, and was right to: a buyer opening their own box must not find our
# tenant name in it. Caught by OSDev1 reading the log rather than calling both PRs one flake.
#
# The prefix is also strictly more correct than the literal was. A Space's key variable is
# `ZERNIO_API_KEY_<tenant>`, so this strips whatever tenant the machine running the suite happens
# to carry — ours today, somebody else's on the next dev box — instead of the one name I knew.
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, dash, shell                                # noqa: E402
from core.dispatch import app                                            # noqa: E402
# THE MACHINE, NAMED AT COLUMN 0 SO THE EXPORTER CAN SEE IT. Every assertion below about the rail
# and the set-up card needs the Inbox section, which only exists because this module registers it —
# but the suite reached it TRANSITIVELY through `core.dispatch`, and `export_box.sh` decides which
# suites ship by scanning for a literal `marketing.<pkg>` import. A transitive dependency is
# invisible to that scan, so this suite shipped into a Lead box that cannot satisfy it and failed
# there on 13 assertions, every one of them reading an empty rail (measured 2026-09-18, inside a
# built Lead box). Naming the package here is the whole fix: the exporter now drops this suite from
# any box without the inbox machine, which is the only kind of box it was ever about.
from marketing.customer_voice import app as _inbox_app                   # noqa: E402,F401

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def owner():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


def home() -> str:
    return owner().get("/dashboard").get_data(as_text=True)


def clear_all():
    for name in (box_secrets.EMAIL, box_secrets.EMAIL_STATUS, box_secrets.ZERNIO,
                 box_secrets.ANTHROPIC):
        box_secrets.clear(name)


def connect_mailbox():
    # THROUGH `put`, NOT `put_email`, deliberately: `put_email` signs in to the mail server, and a
    # suite that reached the network would be a suite that fails in CI and costs money here.
    box_secrets.put(box_secrets.EMAIL, json.dumps(
        {"host": "imap.gmail.com", "user": "a@b.co", "password": "x" * 16}))


# ── 1. core finds the screen through the registry, not by knowing a URL ───────────────
print("test_core_finds_the_setup_screen_without_learning_a_machines_url")
href = shell.setup_href()
ok("the registry answers with a set-up screen on this box", bool(href), repr(href))
ok("...and it is the one the machine registered",
   any(i.key == shell.SETUP_KEY and i.href == href
       for s in shell.sections() for i in s.items), href)
# THE RATCHET SAYS THIS TOO, and belt-and-braces is right for the rule that keeps clones clean:
# core naming a machine's URL is what `tests/test_core_boundary.py` exists to refuse.
import pathlib                                                           # noqa: E402
_src = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "home.py").read_text()
ok("core/dash/home.py names no machine URL of its own", "/inbox/" not in _src,
   "core learned a machine's path")


# ── 2. nothing connected: the way in, and no offer to stop ───────────────────────────
print("\ntest_a_buyer_with_nothing_connected_is_told_what_to_do")
clear_all()
h = home()
# THE HEADLINE LINK GOES TO THE BOX'S OWN SETTINGS, not to a machine. Owner, 2026-09-23: *"The
# link in the middle to set up your box should go into the System Settings not the unified inbox
# machine. It should immediately get them their LLM connection and their progressive Web App."*
_card = h[h.find("Finish setting up"):]
_card = _card[:_card.find("</div></div>") if "</div></div>" in _card else 4000]
ok("the card's headline link goes to System Settings",
   '<a href="/settings">Set up your box' in h, _card[:400])
ok("...and says how many things are waiting", "3 things to connect" in h, h[h.find("Finish"):][:160])
# THE AI ACCOUNT FIRST, THE MOBILE APP SECOND, THEN THE CHANNELS — the order he named them in.
_order = [_card.find(x) for x in ('href="/settings/ai"', 'href="/settings/mobile"', f'href="{href}#')]
ok("...then straight to the AI account, the mobile app, and the machine's channels, in that order",
   -1 not in _order and _order == sorted(_order), str(_order))
ok("...and the channel rows still reach the machine through the registry",
   f'href="{href}#email"' in _card and f'href="{href}#zernio"' in _card, _card[:600])
# THE CONTROL THAT CANNOT DO WHAT IT SAYS. A button to stop a box that has not started was the
# most prominent thing on this page.
ok("Stop everything is NOT offered on a box that has not started",
   "Stop everything" not in h, "the loudest control is still a stop button")


# ── 3. one connected: the count moves, and the false sentence is gone ────────────────
print("\ntest_the_card_counts_and_stops_claiming_nothing_arrives")
connect_mailbox()
h = home()
ok("the count moves as steps are connected", "1 of 3 connected" in h, h[h.find("Finish"):][:160])
ok("...and names which ones are left, so nobody hunts",
   "Your social accounts" in h and "Your AI account" in h)
# FOUND BY READING THE RENDERED CARD, not the code: with the mailbox connected it still said
# "Nothing arrives until at least one of these is connected" over a box already receiving mail.
ok("...and no longer claims nothing arrives, because something does",
   "Nothing arrives until at least one" not in h,
   "the card tells a buyer his working channel is not working")
ok("...and the connected channel drops out of the rows", f'href="{href}#email"' not in h)


# ── 4. finished: the card goes, the stop button returns ──────────────────────────────
print("\ntest_the_card_goes_away_and_the_stop_button_comes_back")
box_secrets.put(box_secrets.ZERNIO, "z" * 67)
box_secrets.put(box_secrets.ANTHROPIC, "sk-ant-" + "A" * 60)
h = home()
ok("a finished box is not nagged", "Finish setting up your box" not in h)
ok("...and no longer carries a set-up link it does not need",
   f'href="{href}' not in h and '<a href="/settings">Set up your box' not in h)
# A SET-UP CARD THAT OUTLIVES SET-UP is the banner every product trains its users to ignore, and
# this is the one screen that must survive that training.
ok("Stop everything comes back once there is something running to stop",
   "Stop everything" in h)
clear_all()


# ── 4b. THE REGRESSION THIS SUITE DID NOT CATCH THE FIRST TIME ───────────────────────
print("\ntest_a_box_that_is_running_can_still_be_stopped")
# WHAT THE FIRST VERSION DID. `_stop_card` stood down whenever set-up was UNFINISHED, which is
# not the same question as "has anything started". A box with the mailbox connected and two steps
# outstanding is RECEIVING MAIL — worker sweeping, messages arriving — and its owner was offered
# no way to stop it, because he had not connected two things he may never connect at all. That
# undoes #1341 for the most common box there is: a buyer who did step one and stopped.
#
# EVERY ASSERTION IN THIS FILE PASSED WHILE THAT WAS TRUE, which is the point of writing it down.
# The states it checked were "none connected" and "all connected"; the bug lived in between.
clear_all()
connect_mailbox()
h = home()
ok("a box with one channel connected is still being set up", "Finish setting up your box" in h)
ok("...and can be STOPPED, because it is running", "Stop everything" in h,
   "the owner cannot stop a box that is receiving mail")
ok("...and the stop control is still a POST, not a link a crawler can follow",
   'action="/dash/stop"' in h and 'href="/dash/stop"' not in h)
clear_all()


# ── 4c. a member is not nagged with a job they cannot do ─────────────────────────────
print("\ntest_a_member_is_not_given_a_task_only_the_owner_can_finish")
# MEASURED: a member saw "Finish setting up your box", followed the link, and could not finish it
# — the set-up screen takes their POST and stores nothing, because these are the box OWNER's
# credentials. A card offering a task the reader cannot complete, pointing at a form that will not
# accept them, is the dead-control rule this codebase keeps deleting by name.
sam = state.add_user("sam@acme.test", name="Sam", role="member")
_m = app.test_client()
_m.set_cookie(dash.COOKIE, dash.new_session(sam["id"]))
_who = dash.session_user
with app.test_request_context("/", headers={"Cookie": f"{dash.COOKIE}={dash.new_session(sam['id'])}"}):
    from flask import request as _rq
    _role = (dash.session_user(_rq) or {}).get("role")
ok("the member really is signed in — otherwise this proves nothing", _role == "member", str(_role))
mh = _m.get("/dashboard")
if mh.status_code == 200:
    mb = mh.get_data(as_text=True)
    ok("a member is not shown the set-up card", "Finish setting up your box" not in mb)
    ok("...nor a link to a screen that will not take their input", f'href="{href}"' not in mb)
else:
    ok(f"...and /dashboard does not serve a member at all ({mh.status_code})", True)


# ── 5. a box with no machine set-up screen still gets the box's own rows ──────────────
print("\ntest_a_box_with_no_channel_screen_still_gets_the_box_rows")
# A CARD MUST NEVER BE A BUTTON TO A 404. A Lead box registers no `setup` item, and this is that
# box: its channel rows have nowhere to go, so they are not drawn.
_saved = dict(shell._SECTIONS)
try:
    for k, sec in list(shell._SECTIONS.items()):
        shell._SECTIONS[k] = type(sec)(
            **{**sec.__dict__, "items": tuple(i for i in sec.items if i.key != shell.SETUP_KEY)})
    ok("the registry now answers with nowhere", shell.setup_href() == "", shell.setup_href())
    h = home()
    # THE CARD NOW GOES TO THE BOX'S OWN SETTINGS, which every box serves, so there IS a way in
    # here and no 404 to protect anyone from. What this box lacks is a machine's channel screen,
    # so the card draws with the box's own rows and without the channel rows.
    ok("...so the card still draws, because System Settings is on every box",
       '<a href="/settings">Set up your box' in h)
    ok("...with the AI account row", 'href="/settings/ai"' in h)
    ok("...and no channel rows, because no machine offered a screen for them",
       'Your inbox' not in h[h.find("Finish setting up"):h.find("Set up your box")])
    ok("...and the page still renders rather than failing", len(h) > 500, f"{len(h)} bytes")
finally:
    shell._SECTIONS.clear()
    shell._SECTIONS.update(_saved)

# ── 6. the screen lights its own row ─────────────────────────────────────────────────
print("\ntest_the_setup_screen_lights_the_setup_row")
# OSDEV5 FOUND THIS BY READING THE RENDERED BAR ON MY BRANCH, not the diff, and four suites
# passed with it live — including this one and both rail suites. `r_setup` passed
# `here="/inbox/settings"`, which was the honest answer until #1374 gave set-up a row of its own;
# after it, the bar lit Settings on the one screen go-to-market funnels every buyer to, and the
# new row never lit at all. A menu that tells you that you are somewhere you are not is worse
# than a menu with a gap in it.
_setup_page = owner().get(href)
ok("the set-up screen renders", _setup_page.status_code == 200, str(_setup_page.status_code))
try:
    _lit = shell.current_item(href)
    ok("...and the rail lights the row for THIS screen",
       _lit is not None and _lit.key == shell.SETUP_KEY,
       f"lit {_lit.key if _lit else None!r} while standing on {href}")
except Exception as e:                           # noqa: BLE001
    ok("...and the rail lights the row for THIS screen", False, f"{type(e).__name__}: {e}")
# AND THE SOURCE, because `current_item` answers about the registry while the bug was in what the
# PAGE passed to `_shell`. Both halves have to agree or the guard only covers one of them.
# THIS SUITE SHIPS INTO EVERY BOX, AND A LEAD BOX HAS NO customer_voice/app.py. Without this guard
# the file read raises FileNotFoundError inside `test_recipe_ships`, which runs every shipped suite
# inside a built Lead box — red on a PR whose logic is fine. Same trap as #1320. The sections above
# are kernel-only and run everywhere; only the two that read the MACHINE's source are skipped, and
# they are skipped loudly enough that nobody mistakes a skip for a pass.
_app_path = (pathlib.Path(__file__).resolve().parents[1]
             / "marketing" / "customer_voice" / "app.py")
_app_src = _app_path.read_text() if _app_path.exists() else ""
if not _app_src:
    print("  --   the set-up route's own source: skipped, this box carries no customer_voice")
if _app_src:
  _r_setup = _app_src[_app_src.find("def r_setup("):]
  _r_setup = _r_setup[:_r_setup.find("\ndef ", 10)]
  ok("...and the set-up route itself claims the set-up row, not Settings",
     'here="/inbox/settings"' not in _r_setup and 'here="/inbox/setup"' in _r_setup,
     "r_setup still tells the shell it is the Settings screen")
  # DELIBERATELY NOT A SWEEP: `r_drafts` carries the identical line and must KEEP it — it is a
  # screen of Settings with no row of its own. OSDev5 paid for that revert once already.
  _r_drafts = _app_src[_app_src.find("def r_drafts("):]
  _r_drafts = _r_drafts[:_r_drafts.find("\ndef ", 10)]
  ok("...while the drafts screen still lights Settings, which is its true home",
     'here="/inbox/settings"' in _r_drafts)

# ── 7. OSDEV5'S GUARD, ON THE RENDERED PAGE ─────────────────────────────────────────
# HIS TEST, TAKEN AS HE WROTE IT, because it holds something mine does not. Section 6 asks the
# registry and reads r_setup's source; this asks the HTML a buyer's browser actually receives,
# and — the part neither of mine covers — that no OTHER row claims to be the page you are on.
# Four suites passed with the bug live, his two included, which is why he wrote a new one rather
# than assuming an existing one would catch the next occurrence.
print("\ntest_exactly_one_row_says_you_are_here")
import re as _re                                                          # noqa: E402


def _current(path: str) -> list:
    h = owner().get(path).get_data(as_text=True)
    return sorted({m for m in _re.findall(
        r'<a[^>]*href="([^"]+)"[^>]*aria-current="page"', h)})


_cur = _current(href)
ok("the set-up screen marks itself current", _cur == [href], str(_cur))
ok("...and no other row claims to be the page you are on", len(_cur) == 1, str(_cur))
# THE OTHER HALF OF THE SAME RULE. A fix that made every screen mark set-up would pass the two
# above and be a worse bug; Settings must still own its own screen.
_settings = _current("/inbox/settings")
ok("...while Settings still marks itself on its own screen",
   _settings == ["/inbox/settings"], str(_settings))

print("\nall ok" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
