"""Stop everything is the BOX's switch, and every box has it.

WHAT WAS TRUE BEFORE THIS (OSDev4, measured 2026-09-17 on a customer_voice box exported from
main). `core/pause.py` ships on every box and works on every box — and the only two callers of
`pause.halt` / `pause.resume` anywhere outside it lived in `marketing/lead_machine/dash.py`. So on
the box we actually sell, `core/pause.py` imported fine and `/dash/stop` did not exist: the
machinery was under the buyer's feet and the only switch had been built into a machine they did
not buy. A person who wanted to stop their own box had no button.

The owner, 2026-09-17: *"Right now the lead machine has those core features and it needs to be
switched over to core."* OSDev1 as core owner, reviewing #1332: *"Stop/Resume move into core as
one route pair and one /dashboard button… owner-only by role, and the audit line names who
pressed it."*

MOVED, NOT COPIED, AND ON THE SAME TWO URLS — so the lead machine's own button keeps working and
every other box gains one, over ONE marker file rather than two switches.

Run: python tests/test_the_kill_switch_is_the_boxs.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "kill.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["AIOS_PAUSE_FILE"] = os.path.join(_T, "PAUSED")
os.environ["DASH_TOKEN"] = "the-owners-own-password"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer-only"

ORDER = "cs_live_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
    json.dump({"buyer": "Acme Plumbing", "order": ORDER, "host": "acme.ownbox.app",
               "box_type": "customer_voice"}, fh)

from core import state  # noqa: E402

state.init_db()

from core import pause  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def owner_client():
    c = app.test_client()
    c.post("/claim", data={"c": ORDER, "email": "owner@acme.test",
                           "password": "a-long-enough-password"})
    return c


def _owner_session_id():
    from core import dash as _d
    return _d.new_session(state.owner_user()["id"])


pause.resume()
OWNER = owner_client()
_owner_sid = _owner_session_id()


# ── 1. every box has the switch, including the one we sell ───────────────────────────────
print("\ntest_the_box_we_sell_can_stop_itself")

rules = {str(r) for r in app.url_map.iter_rules()}
ok("/dash/stop is served by CORE, so it exists wherever core does", "/dash/stop" in rules)
ok("/dash/resume too", "/dash/resume" in rules)

# AND IT IS CORE'S, NOT A MACHINE'S — asserted from the other end, because "it works here" would
# also have been true on the old lead-machine-only build when run in the repo.
import inspect  # noqa: E402

from core import dash as _dash  # noqa: E402

ok("the route is defined in core/dash, not in a machine",
   "core/dash" in inspect.getsourcefile(_dash.stop_everything).replace("\\", "/"),
   inspect.getsourcefile(_dash.stop_everything))


# ── 2. it actually stops the box, and says who did it ────────────────────────────────────
print("\ntest_it_stops_the_box_and_the_marker_names_who")

ok("nothing is paused to begin with", not pause.is_paused())
r = OWNER.post("/dash/stop")
ok("the owner pressing Stop is accepted", r.status_code in (301, 302, 303), str(r.status_code))
ok("...and the box is actually stopped", pause.is_paused())
ok("THE MARKER NAMES WHO PRESSED IT — the answer to 'why is nothing running?'",
   "owner@acme.test" in pause.status(), pause.status())

# THROUGH landing(), NEVER A PATH SPELLED OUT. The lead machine's version sent everyone to
# /dash/home, which does not exist on the box we sell: pressing Stop there would have 404'd.
dest = r.headers.get("Location", "")
ok(f"it returns them somewhere this box actually serves ({dest})",
   OWNER.get(dest).status_code != 404, dest)

r = OWNER.post("/dash/resume")
ok("the owner can start it again", pause.is_paused() is False, pause.status())


# ── 3. who may press it ──────────────────────────────────────────────────────────────────
print("\ntest_only_the_owner_may_stop_the_box")

# A STRANGER. A box that any passer-by could stop is not a box anybody would buy.
stranger = app.test_client()
r = stranger.post("/dash/stop")
ok("a stranger is refused", r.status_code in (301, 302, 303) and "login" in
   (r.headers.get("Location") or ""), f"{r.status_code} {r.headers.get('Location')}")
ok("...and the box is still running", not pause.is_paused())

# A MEMBER. Halting stops work every other person on the box depends on, which is not a thing one
# employee should be able to do to the rest — OSDev1's ruling on #1332.
sam = state.add_user("member@acme.test", name="Sam", role="member")
member = app.test_client()
_sid = _dash.new_session(sam["id"])
member.set_cookie(_dash.COOKIE, _sid)
# PROVED FROM THE SESSION ITSELF, not from a page. An earlier version of this check asked
# whether the member could load /dashboard, and they cannot — that page bounces them (see the
# note in the PR; it belongs to the gate's author, not to this change). Asking the page would
# have made the refusal below VACUOUS: a member who was never signed in is refused as a
# stranger, which proves nothing about role.
with app.test_request_context("/", headers={"Cookie": f"{_dash.COOKIE}={_sid}"}):
    from flask import request as _req
    _who = _dash.session_user(_req)
ok("the member really is signed in — otherwise the refusal below proves nothing",
   bool(_who) and _who.get("role") == "member", str(_who))
r = member.post("/dash/stop")
ok("A MEMBER IS REFUSED RATHER THAN OBEYED — stopping the box stops everyone's work",
   r.status_code in (302, 303, 403), str(r.status_code))
ok("...and the box is still running", not pause.is_paused(), pause.status())
ok("...and the member is refused by the ROUTE, which is the half that matters",
   r.status_code != 200 and not pause.is_paused())

# THE GATE IS ON THE ROUTE, NOT ONLY ON THE BUTTON. A form can be posted without ever loading the
# page that would have hidden it, which is the whole reason the check lives at the write.
src = inspect.getsource(_dash.stop_everything)
ok("the route itself checks the owner session, not the page that drew the button",
   "_owner_session" in src, src[:200])


# ── 4. the button is on the box's home ───────────────────────────────────────────────────
print("\ntest_the_owner_can_find_it")

# THE BOX HAS TO HAVE STARTED BEFORE THERE IS ANYTHING TO STOP, and since 2026-09-18 the
# dashboard says so: `_setup_card` takes the page while set-up is unfinished and `_stop_card`
# stands down behind it, because an offer to halt work that has not begun is a control that
# cannot do what it says. That is a real rule, so this suite states its precondition rather than
# asserting through it — the fixture connects the box, which is the state every assertion below
# was always about.
from core import box_secrets as _bs                                      # noqa: E402
_bs.put(_bs.EMAIL, json.dumps({"host": "imap.gmail.com", "user": "a@b.co", "password": "x" * 16}))
_bs.put(_bs.ZERNIO, "z" * 67)
_bs.put(_bs.ANTHROPIC, "sk-ant-" + "A" * 60)

body = OWNER.get("/dashboard").get_data(as_text=True)
ok("the owner sees Stop everything on /dashboard", "Stop everything" in body)
ok("...as a POST, so a crawler or a link preview cannot flip the box",
   'action="/dash/stop"' in body and 'method="post"' in body)

OWNER.post("/dash/stop")
body = OWNER.get("/dashboard").get_data(as_text=True)
ok("once stopped, the same place offers to start it again", "Start it again" in body)
ok("...and says plainly that nothing is running", "Nothing is running" in body)
OWNER.post("/dash/resume")

member_body = stranger.get("/dashboard").get_data(as_text=True)
ok("a signed-out visitor is not shown the button at all",
   "Stop everything" not in member_body)

# AND A MEMBER WILL NOT SEE IT EITHER, ONCE THEY CAN REACH THIS PAGE. Today a member is bounced
# off /dashboard, so asking through the page would prove nothing; OSDev1 has ruled that gate open
# to members (2026-09-17, OSDev5 building it), and on the day it lands this button must already
# be right rather than discovered wrong. So the card is asked DIRECTLY, in a member's request
# context — the one form of this check that is true both before and after that change.
from core.dash import home as _home_mod  # noqa: E402

with app.test_request_context("/dashboard", headers={"Cookie": f"{_dash.COOKIE}={_sid}"}):
    member_card = _home_mod._stop_card()
with app.test_request_context("/dashboard",
                              headers={"Cookie": f"{_dash.COOKIE}={_owner_sid}"}):
    owner_card = _home_mod._stop_card()
ok("the owner's card is drawn", "Stop everything" in owner_card)
ok("A MEMBER IS DRAWN NOTHING — true today, and still true when /dashboard opens to members",
   member_card == "", member_card[:120])


# ── 5. one switch, one marker ────────────────────────────────────────────────────────────
print("\ntest_the_machine_no_longer_carries_its_own_copy")

lead = (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        + "/marketing/lead_machine/dash.py")
if os.path.isfile(lead):
    text = open(lead, encoding="utf-8").read()
    ok("the lead machine no longer defines /dash/stop",
       '@blueprint.post("/dash/stop")' not in text)
    ok("...nor /dash/resume", '@blueprint.post("/dash/resume")' not in text)
    ok("...but its button still posts to the same URL, so nothing there had to change",
       'action="/dash/' in text)
else:
    print("  --   no lead machine in this box — nothing to check")


# ── 6. and the buyer is told, on the page they live on ───────────────────────────────────
print("\ntest_the_inbox_says_the_box_is_stopped")

# THE GAP THIS CLOSES IS ONE THIS PR MADE. Stop moved into core and onto /dashboard, which says
# plainly that the box is stopped — and the INBOX said nothing at all. Measured on an exported
# customer_voice box: dashboard said stopped; inbox, thread and settings said nothing. New
# messages just stop arriving, the screen looks normal, and the only conclusion available to the
# buyer is that the thing is broken.
#
# ASKED ONLY WHERE THE INBOX EXISTS. This suite ships into every box, and a Lead box has no
# `marketing/customer_voice` and therefore no /inbox routes — the shape that has bitten six
# suites. A missing directory means this box has no inbox to check, not a failure.
import pathlib as _pl  # noqa: E402

_HAS_INBOX = (_pl.Path(__file__).resolve().parents[1] / "marketing" / "customer_voice").is_dir()
if not _HAS_INBOX:
    print("  --   no inbox on this box — nothing to check")
else:
    from marketing.customer_voice.inbox import store as _istore  # noqa: E402

    _istore.upsert_conversation(space="default", zcid="c-stopped", platform="instagram",
                                participant="Priya", last_inbound_at="2026-09-17T18:00:00Z",
                                account_id="a1")
    _istore.record_message(space="default", zcid="c-stopped", zmid="m-stopped", direction="in",
                           sent_by="contact", body="Are you open Sunday?")

    pause.resume()
    running = OWNER.get("/inbox/inbox").get_data(as_text=True)
    ok("a running box says nothing about being stopped", "box is stopped" not in running)

    OWNER.post("/dash/stop")
    stopped = OWNER.get("/inbox/inbox").get_data(as_text=True)
    ok("A STOPPED BOX SAYS SO ON THE INBOX — the page the buyer actually lives on",
       "box is stopped" in stopped)
    ok("...and says what stopped: no new messages are arriving",
       "no new messages are arriving" in stopped)
    # HALF A FACT IS WORSE THAN NONE. core/pause is read by core/worker and nothing else, so the
    # MACHINES stop and a person can still answer by hand. Saying "stopped" without that would
    # have a buyer thinking a reply they typed went nowhere.
    ok("...and names what STILL works, because a human send is not a machine",
       "still reply" in stopped)
    ok("...and offers the way back", 'href="/dashboard"' in stopped)

    # THE THREAD IS DELIBERATELY NOT CHANGED. Replying works while stopped, so a note there would
    # be telling someone that the thing they are doing does not work. The confusing state is
    # "nothing NEW arrives", which is a list-level observation.
    thread = OWNER.get("/inbox/inbox/c-stopped").get_data(as_text=True)
    ok("the thread is left alone, because replying still works there",
       "box is stopped" not in thread)
    OWNER.post("/dash/resume")


print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_kill_switch_is_the_boxs is in the workflow's suite list",
       "test_the_kill_switch_is_the_boxs" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    [print("   -", f) for f in FAILS]
    sys.exit(1)
print("ALL OK")
