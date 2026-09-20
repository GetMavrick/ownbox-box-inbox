"""The box tells its owner somebody is waiting — and can never tell anybody else.

WHY EMAIL AND NOT BROWSER PUSH (owner, 2026-09-17). Web Push needs VAPID (ECDSA P-256) and an
AES-GCM payload, so it needs `cryptography`: a new COMPILED dependency in a hash-pinned lock on
every sold box's bootstrap, and every notification's existence and endpoint transiting Google's,
Mozilla's or Apple's push service. Email rides the Resend account the box already has — no new
dependency, no new sub-processor, no permission prompt. The service worker stays built and
unused; this closes no door.

THE THING THIS SUITE GUARDS IS A NEGATIVE, and it is the reason the notifier lives in `core` and
not in `marketing/customer_voice`. That package's send ban (`_SENDS_BY_DESIGN = {"inbox"}`) exists
so the machine that reads what the world says can never say something back to a customer. A
notifier that took an address argument would be a way around it. This one cannot name an outsider:
every address comes from the box's own users table, and callers pass a user id or nothing.

THE SECOND NEGATIVE IS THE COLD LANE. `lead_machine.resend_client.send` is the cold-outreach door
— outbound lane gate, prospect suppression, unsubscribe footer, daily new-prospect quota. Routing
a notification through it would have meant NO NOTIFICATIONS AT ALL, silently, because that lane is
off; and any that did go would have spent the owner's new-prospect quota to tell him his inbox was
full. The ledger note is asserted here for that reason.

Run: python tests/test_box_notifies_its_owner.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "notify.db")

from core import box_mail, notify, state  # noqa: E402

state.init_db()

FAILS = []
SENT = []          # every envelope that reached the transport


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def fake_send(to, subject, text_body, html_body, *, idem_key, sender_name, note):
    SENT.append({"to": to, "subject": subject, "text": text_body, "html": html_body,
                 "idem_key": idem_key, "sender_name": sender_name, "note": note})
    return f"msg-{len(SENT)}"


_real_send = box_mail.send
box_mail.send = fake_send
box_mail.is_configured = lambda: True

OWNER = state.add_user("owner@example.com", name="Brian")["id"]
# INSIDE THE MORNING SLOT ON THE BUYER'S CLOCK, pinned rather than assumed: the owner ruled "an
# 8 AM and a 5 PM email", and every send below has to be somewhere those windows are open. The
# suite fixes the buyer's timezone too, because a test that reads the machine's is a test that
# passes in one office and fails in another.
notify._cfg = lambda: {"enabled": True, "timezone": "America/Los_Angeles"}
_TZ = ZoneInfo("America/Los_Angeles")
MORNING = datetime(2026, 9, 17, 9, 0, tzinfo=_TZ).astimezone(timezone.utc)
EVENING = datetime(2026, 9, 17, 18, 0, tzinfo=_TZ).astimezone(timezone.utc)
NIGHT = datetime(2026, 9, 17, 2, 0, tzinfo=_TZ).astimezone(timezone.utc)
NOW = MORNING


# ── 1. it cannot name an outsider ─────────────────────────────────────────────────────────
print("\ntest_the_box_can_only_email_its_own_people")

people = box_mail.to_box_people()
ok("the recipient set is the box's own users table",
   [p["email"] for p in people] == ["owner@example.com"], str(people))
# EVERY BOX SHIPS WITH ONE OF THESE: `state.init_db` seeds `owner@localhost` so a fresh box has
# a row to sign in as. It can never receive mail, and without the floor below every notice on
# every un-personalised box spends a Resend call to be rejected and tries again forever.
ok("...and the seeded owner@localhost row is NOT in it",
   all("localhost" not in p["email"] for p in people), str(people))
ok("...and that is a deliverability floor, not address validation",
   box_mail._deliverable("a@b.co") and not box_mail._deliverable("a@b"))
ok("...and a named user narrows it to that person",
   [p["email"] for p in box_mail.to_box_people(OWNER)] == ["owner@example.com"])
ok("...and an id that is not on this box resolves to NOBODY, never to everybody",
   box_mail.to_box_people("not-a-user") == [], str(box_mail.to_box_people("not-a-user")))

import inspect  # noqa: E402

sig = inspect.signature(notify.send_notice).parameters
ok("NO CALLER MAY PASS AN ADDRESS — the public notifier takes a user id and never an email",
   "user_id" in sig and not any("email" in n or "to" == n for n in sig), str(list(sig)))

src = inspect.getsource(notify)
ok("...and nothing in the notifier builds an envelope of its own",
   "api.resend.com" not in src and "requests" not in src)


# ── 2. it never spends the cold quota, and never uses the cold door ───────────────────────
print("\ntest_a_notice_is_transactional_and_never_cold_outreach")

SENT.clear()
res = notify.send_notice("k1", "3 people are waiting", "Open your inbox.", now=NOW)
ok("a notice is delivered", res["sent"] == 1, str(res))
ok("THE LEDGER NOTE IS 'notify', so it can never be charged to the new-prospect quota",
   SENT[0]["note"] == "notify", SENT[0]["note"])
ok("no unsubscribe footer — this is the box talking to its owner about his own box",
   "unsubscribe" not in (SENT[0]["text"] + SENT[0]["html"]).lower())

# ASSERTED ON IMPORTS, NOT ON TEXT. Both modules DISCUSS the cold door at length — explaining why
# they are not it is most of their docstrings — so a substring scan would fail on its own
# documentation. What matters is what they can reach.
import ast as _ast  # noqa: E402


def _imports(mod) -> set:
    tree = _ast.parse(inspect.getsource(mod))
    names = set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, _ast.ImportFrom):
            names.add(n.module or "")
    return names


_imp = _imports(notify) | _imports(box_mail)
ok("and the cold-outreach door is not IMPORTED from here, at any depth",
   not any("lead_machine" in i or "resend_client" in i for i in _imp), str(sorted(_imp)))
ok("...nor is an HTTP client reached directly — core.net is the one door",
   not any(i.split(".")[0] in ("requests", "httpx", "aiohttp", "urllib") for i in _imp),
   str(sorted(_imp)))


# ── 3. it cannot flood ────────────────────────────────────────────────────────────────────
print("\ntest_two_a_day_and_not_one_more")

SENT.clear()
ok("the morning slot sends", notify.send_notice("slot", "a", "b", now=MORNING)["sent"] == 1)
again = notify.send_notice("slot", "a", "b", now=MORNING + timedelta(minutes=15))
ok("...and every tick for the rest of that window is silent",
   again["sent"] == 0 and again["skipped"] == "already_sent", str(again))
noon = notify.send_notice("slot", "a", "b", now=MORNING + timedelta(hours=2))
ok("...still silent two hours later, inside the same slot", noon["sent"] == 0, str(noon))

between = notify.send_notice("slot", "a", "b",
                             now=datetime(2026, 9, 17, 14, 0, tzinfo=_TZ).astimezone(timezone.utc))
ok("BETWEEN THE WINDOWS NOTHING SENDS — two in the afternoon is neither slot",
   between["sent"] == 0 and between["skipped"] == "outside_slot", str(between))

ok("the evening slot is its own send", notify.send_notice("slot", "a", "b",
                                                          now=EVENING)["sent"] == 1)
ok("EXACTLY TWO EMAILS FOR THE WHOLE DAY", len(SENT) == 2, str(len(SENT)))

night = notify.send_notice("slot", "a", "b", now=NIGHT)
ok("TWO IN THE MORNING IS NOT A SLOT — this is the bug OSDev1 measured, and it cannot happen",
   night["sent"] == 0 and night["skipped"] == "outside_slot", str(night))

late = notify.send_notice("slot", "a", "b",
                          now=datetime(2026, 9, 17, 23, 0, tzinfo=_TZ).astimezone(timezone.utc))
ok("...nor is eleven at night, so a box that was off all evening waits for the morning",
   late["sent"] == 0, str(late))

tomorrow = notify.send_notice("slot", "a", "b", now=MORNING + timedelta(days=1))
ok("A NEW DAY IS A NEW SLOT — the marker carries the buyer's local date",
   tomorrow["sent"] == 1, str(tomorrow))

SENT.clear()
notify.send_notice("other", "a", "b", now=MORNING)
ok("a different kind of notice has its own marker", len(SENT) == 1)
ok("the idempotency key is per slot, per person, so a retry is one send",
   SENT[0]["idem_key"] == f"notify:other:2026-09-17:morning:{OWNER}", SENT[0]["idem_key"])

# ── 4. it cannot raise, and a failed round buys no silence ───────────────────────────────
print("\ntest_a_notifier_that_throws_would_take_intake_down_with_it")


def boom(*a, **k):
    raise RuntimeError("resend is down")


box_mail.send = boom
res = notify.send_notice("down", "a", "b", now=NOW + timedelta(hours=2))
ok("a vendor outage is an answer, not an exception", res["sent"] == 0 and res["skipped"] == "all_failed",
   str(res))

box_mail.send = fake_send
SENT.clear()
res = notify.send_notice("down", "a", "b", now=NOW + timedelta(hours=2, minutes=1))
ok("A FAILED ROUND BOUGHT NO SILENCE — the next real notice still goes",
   res["sent"] == 1, str(res))

box_mail.to_box_people = lambda uid=None: (_ for _ in ()).throw(RuntimeError("db gone"))
res = notify.send_notice("dbdown", "a", "b", now=MORNING + timedelta(minutes=90))
ok("even an unexpected bug INSIDE the notifier answers with a dict",
   res["sent"] == 0 and res["skipped"] == "error", str(res))
box_mail.to_box_people = lambda uid=None: [{"id": OWNER, "email": "owner@example.com", "name": "B"}]


# ── 5. off means off, and half-configured is off ─────────────────────────────────────────
print("\ntest_half_configured_is_off_not_nearly_on")

box_mail.is_configured = lambda: False
res = notify.send_notice("k2", "a", "b", now=EVENING + timedelta(minutes=30))
ok("no verified sender: nothing is sent and it SAYS so",
   res["sent"] == 0 and res["skipped"] == "off", str(res))
box_mail.is_configured = lambda: True


# ── 6. the buyer's clock, not the box's ──────────────────────────────────────────────────
print("\ntest_the_schedule_runs_on_the_buyers_clock_not_the_boxs")

# THE BUG OSDev1 MEASURED, 2026-09-17: a sold box ships `cost.timezone` UTC and nothing sets it to
# the buyer's, so "8 AM" reaches a Pacific buyer at one in the morning. The resolver is what fixes
# it, and the order it looks in is the whole of the fix.
_real_cfg = notify._cfg
notify._cfg = lambda: {"enabled": True, "timezone": "Australia/Sydney"}
ok("an explicit notify.timezone wins, because it is the only one a person can correct",
   notify.buyer_timezone() == "Australia/Sydney")

notify._cfg = lambda: {"enabled": True}
from core import claim as _claim  # noqa: E402

_claim.claimed = lambda: {"timezone": "America/New_York"}
ok("...then what the browser said when he claimed the box",
   notify.buyer_timezone() == "America/New_York")

_claim.claimed = lambda: {}
ok("...then the box's own cost.timezone, which is at least deliberate",
   notify.buyer_timezone() == "America/Los_Angeles", notify.buyer_timezone())

_claim.claimed = lambda: (_ for _ in ()).throw(RuntimeError("no such column"))
ok("a box too old for the column falls through rather than raising",
   notify.buyer_timezone() == "America/Los_Angeles")
# AN UNKNOWN ZONE MUST NOT RAISE. `_local` falls back to the instant as given rather than throwing
# inside a worker at eight in the morning — so the schedule is then judged on UTC, which is wrong
# for the buyer but is a decision rather than a crash. What is asserted is that it still ANSWERS,
# and answers something the rest of the module knows how to read.
_claim.claimed = lambda: {"timezone": "Not/AZone"}
ok("AN UNKNOWN ZONE NEVER TAKES A BOX DOWN — it answers, on UTC, rather than throwing",
   notify.due_slot(MORNING) in (None, "morning", "evening"))
ok("...and the same is true of the resolver that feeds it",
   isinstance(notify.buyer_timezone(), str))

# The same instant is a different slot for two buyers, which is the entire point.
_claim.claimed = lambda: {"timezone": "America/Los_Angeles"}
_pacific = notify.due_slot(MORNING)
_claim.claimed = lambda: {"timezone": "Europe/London"}
_london = notify.due_slot(MORNING)
ok("ONE INSTANT, TWO BUYERS, TWO ANSWERS", _pacific == "morning" and _london != "morning",
   f"pacific={_pacific} london={_london}")
_claim.claimed = lambda: {}
notify._cfg = _real_cfg

ok("the timezone the browser sends is validated before it is stored",
   _claim._clean_tz("America/New_York") == "America/New_York"
   and _claim._clean_tz("Not/AZone") is None and _claim._clean_tz("") is None)

# ── 7. what the inbox actually says, and what it refuses to carry ────────────────────────
print("\ntest_the_notice_says_the_number_and_nothing_else")

from marketing.customer_voice.inbox import notices  # noqa: E402

s1, b1 = notices._sentence(1)
ok("one person reads as one person, never '1 messages'", "1 person is waiting" in s1, s1)
s3, b3 = notices._sentence(3)
ok("three reads as three", s3.startswith("3 people are waiting"), s3)
ok("NO CUSTOMER NAME AND NO MESSAGE TEXT leaves the box in a notification",
   all(w not in (s3 + b3).lower() for w in ("said", "wrote:", "@", "http")), s3 + b3)

SENT.clear()
notices._spaces = lambda: ["default"]
notices.store.awaiting_reply = lambda space: 2
res = notices.tick(now=NIGHT)
ok("OUTSIDE THE WINDOWS IT DOES NOTHING, and cheaply — before touching the database",
   res["sent"] == 0 and res["skipped"] == "outside_slot", str(res))

res = notices.tick(now=MORNING)
ok("inside the morning slot, with two waiting, it sends", res["sent"] == 1, str(res))

notices.store.awaiting_reply = lambda space: 0
res = notices.tick(now=EVENING)
ok("SKIPPED WHEN NOBODY IS WAITING — an empty email twice a day is how he learns to ignore them",
   res["sent"] == 0, str(res))

notices.store.awaiting_reply = lambda space: (_ for _ in ()).throw(RuntimeError("no table"))
res = notices.tick(now=MORNING + timedelta(days=2))
ok("a broken count answers with a dict rather than taking the rail down", res["sent"] == 0, str(res))

notices.store.awaiting_reply = lambda space: 1
notices._spaces = lambda: ["acme", "beta"]
SENT.clear()
res = notices.tick(now=MORNING + timedelta(days=3))
ok("IT IS REGISTERED AS A PERIODIC, or it never runs at all",
   any(t["name"] == "inbox_notify" for t in __import__("core.worker", fromlist=["x"]).PERIODIC))

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_box_notifies_its_owner is in the workflow's suite list",
       "test_box_notifies_its_owner" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

box_mail.send = _real_send
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:"); [print("   -", f) for f in FAILS]; sys.exit(1)
print("ALL OK")
