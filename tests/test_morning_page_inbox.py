"""The morning page reports the inbox — the product's own segment, which was not on it.

MEASURED 2026-09-16: this page reported uptime, PageSpeed, competitors, reviews, SEO — and not one
line about messages, on a box whose headline product is a unified inbox. OSDev0's claim-check
called it out at 02:30 as one of the live homepage's overstated claims: "morning page (zero inbox
data)". A morning review of a messaging product that never mentions the messages is the dashboard
version of the empty reply box: it works, and it does not do the thing.

WHAT THIS SUITE DEFENDS:
  · one number, "waiting on you", computed from the DIRECTION of the newest message rather than a
    clock — being ignored for a week is worse than being ignored since breakfast, not resolved
  · the count and the row tag come from ONE sql fragment, so the page and the screen cannot
    disagree about how many people are waiting
  · a box with no conversations says NOTHING, rather than a row of zeroes that reads as broken
  · a SOLD box — which owns no rails at all — still gets its inbox lines, the case where writing
    `watch = [...]` instead of appending would have deleted them

Run: python tests/test_morning_page_inbox.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "morning.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()

from core.report import window  # noqa: E402
from marketing.customer_voice import report as voice_report  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

FAILS: list[str] = []
SPACE = "default"
TODAY = date.today()
LO, HI = window(TODAY)
_REAL_SPACE_NAME = voice_report._space_name       # kept, so the stub can be UNDONE rather than
voice_report._space_name = lambda: SPACE          # deleted — `del` on a name that was assigned
                                                  # over a def removes the def too.


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


# TIMES ARE ANCHORED TO THE REPORT WINDOW, NOT TO "HOURS AGO", and that is not fussiness. The
# Morning Review counts a LOCAL day (core.report.window; this box is America/Los_Angeles), so at
# 07:07 UTC the local day is seven minutes old — and a fixture written as "an hour ago" lands
# BEFORE it and is counted as yesterday. Written the obvious way first, and the whole traffic half
# of this suite reported zero while the direction half passed, which is a confusing way to be told.
def _in_window(seconds_in: float = 60) -> str:
    """A timestamp inside the day this page reports on."""
    return (datetime.fromisoformat(LO) + timedelta(seconds=seconds_in)).isoformat()


def _before_window(days: float = 20) -> str:
    """A timestamp comfortably before it — a conversation from another week."""
    return (datetime.fromisoformat(LO) - timedelta(days=days)).isoformat()


def _texts(seg: dict, key: str) -> list:
    return [x["text"] for x in seg.get(key) or []]


def _msg(zcid: str, zmid: str, direction: str, when: str) -> None:
    """One mirrored message at a chosen time. `record_message` stamps `state._now()` and takes no
    override — correct for the real path, since the row's clock is the moment we saw it — so the
    timestamp is set afterwards rather than by widening a write path for a test's convenience."""
    store.record_message(space=SPACE, zcid=zcid, zmid=zmid, direction=direction,
                         sent_by="contact" if direction == "in" else "human",
                         body=f"{direction} {zmid}")
    with state.connect() as c:
        c.execute("UPDATE inbox_messages SET created_at = ? WHERE zernio_message_id = ?",
                  (when, zmid))


def _conv(zcid: str, who: str, msgs: list, *, opted_out: bool = False) -> None:
    """msgs = [(direction, iso_timestamp), ...] in the order they happened."""
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="messenger", participant=who,
                              account_id="acct-1",
                              last_inbound_at=next((t for d, t in reversed(msgs) if d == "in"),
                                                   None))
    for i, (direction, when) in enumerate(msgs):
        _msg(zcid, f"{zcid}-m{i}", direction, when)
    if opted_out:
        store.set_opted_out(SPACE, zcid)


# ── an empty box says nothing ────────────────────────────────────────────────────────────────
print("\n— a box with no conversations adds no line, rather than a row of zeroes —")
seg = voice_report.report(TODAY)
ok("no inbox figure on an empty box", "inbox_waiting" not in (seg.get("figures") or {}),
   str(seg.get("figures")))
ok("nothing waiting on him", not any("waiting on your reply" in t for t in _texts(seg, "needs_you")),
   str(_texts(seg, "needs_you")))
ok("and no inbox line to worry about", not any(t.startswith("Inbox") for t in _texts(seg, "watch")),
   str(_texts(seg, "watch")))

# ── waiting is about direction, not a clock ──────────────────────────────────────────────────
print("\n— waiting means THEIR message was the last one, at any age —")
_conv("zc-fresh",  "Dana",    [("in", _in_window(60))])                 # waiting, today
_conv("zc-stale",  "Marcus",  [("in", _before_window(20))])              # waiting, three weeks
_conv("zc-done",   "Sofia",   [("in", _in_window(30)),
                               ("out", _in_window(120))])                # answered
_conv("zc-stop",   "Joanna",  [("in", _in_window(40))], opted_out=True)  # said STOP
_conv("zc-quiet",  "Whitmore", [])                                       # never heard a word

ok("a conversation answered an hour ago is not waiting", store.awaiting_reply(SPACE) == 2,
   f"{store.awaiting_reply(SPACE)} waiting")
rows = {r["participant"]: r for r in store.list_conversations(SPACE)}
ok("the ROW says the same thing the count does — fresh", rows["Dana"]["awaiting_reply"] == 1)
ok("...and stale is still waiting, not resolved by age", rows["Marcus"]["awaiting_reply"] == 1)
ok("...and answered is not", rows["Sofia"]["awaiting_reply"] == 0)
ok("...and a conversation with no messages is not", rows["Whitmore"]["awaiting_reply"] == 0)
ok("SOMEBODY WHO SAID STOP IS NOT WAITING FOR A REPLY — excluded from the count",
   rows["Joanna"]["awaiting_reply"] == 1 and store.awaiting_reply(SPACE) == 2,
   "the row is inbound-last, but the count must not include it")

# THE PAGE AND THE SCREEN CANNOT DISAGREE, which is why the fragment is shared.
listed = sum(1 for r in store.list_conversations(SPACE)
             if r["awaiting_reply"] and not r["opted_out"])
ok("the count and the list agree exactly", listed == store.awaiting_reply(SPACE),
   f"list={listed} count={store.awaiting_reply(SPACE)}")
searched = sum(1 for r in store.search_conversations(SPACE, "in")
               if r["awaiting_reply"] and not r["opted_out"])
ok("...and so does the search screen", searched == store.awaiting_reply(SPACE),
   f"search={searched} count={store.awaiting_reply(SPACE)}")

# ── the page itself ──────────────────────────────────────────────────────────────────────────
print("\n— and the morning page finally says so —")
seg = voice_report.report(TODAY)
fig = seg.get("figures") or {}
ok("the page carries the waiting figure", fig.get("inbox_waiting", {}).get("value") == 2, str(fig))
ok("...labelled in words, not a column name",
   fig.get("inbox_waiting", {}).get("label") == "waiting on you", str(fig))
ok("messages that arrived today are counted",
   any("messages came in" in t for t in _texts(seg, "happened")), str(_texts(seg, "happened")))

needs = _texts(seg, "needs_you")
ok("IT IS AN INSTRUCTION, beside the one about the front door",
   any("2 conversations are waiting on your reply" == t for t in needs), str(needs))
ok("...and it links to the inbox, not to a settings page",
   any(x.get("href") == "/inbox/inbox" for x in seg["needs_you"]), str(seg["needs_you"]))
ok("...and the watch list says it too", any(t == "Inbox — 2 waiting on you"
                                            for t in _texts(seg, "watch")), str(_texts(seg, "watch")))

# ── the answered state is visible, or the segment only ever nags ─────────────────────────────
print("\n— the good state shows too, or nobody opens the page —")
for zcid in ("zc-fresh", "zc-stale"):
    _msg(zcid, f"{zcid}-reply", "out", _in_window(300))
ok("nothing is waiting now", store.awaiting_reply(SPACE) == 0, str(store.awaiting_reply(SPACE)))
seg = voice_report.report(TODAY)
ok("the page says everyone has been answered",
   any(t == "Inbox — everyone has been answered" for t in _texts(seg, "watch")),
   str(_texts(seg, "watch")))
ok("...and stops instructing him", not any("waiting on your reply" in t
                                           for t in _texts(seg, "needs_you")),
   str(_texts(seg, "needs_you")))
ok("...but still reports the day's traffic",
   any("messages came in" in t for t in _texts(seg, "happened")), str(_texts(seg, "happened")))

# ── the case that would have silently deleted all of the above ───────────────────────────────
print("\n— A SOLD BOX OWNS NO RAILS, and must still get its inbox lines —")
import marketing.customer_voice.rails as rails  # noqa: E402

_real_declared = rails.declared
rails.declared = lambda: set()                    # type: ignore[assignment]
try:
    _msg("zc-done", "zc-done-new", "in", _in_window(360))
    seg = voice_report.report(TODAY)
    ok("the setup prompt is there, as it was", any("Nothing set up yet" in t
                                                   for t in _texts(seg, "watch")),
       str(_texts(seg, "watch")))
    ok("AND THE INBOX LINE SURVIVED IT — `watch = [...]` would have wiped this",
       any(t.startswith("Inbox —") for t in _texts(seg, "watch")), str(_texts(seg, "watch")))
    ok("...and the headline is the number that matters on such a box",
       seg["headline"] == {"value": 1, "label": "waiting on you"}, str(seg["headline"]))
    ok("...and he is still told to go and answer it",
       any("waiting on your reply" in t for t in _texts(seg, "needs_you")),
       str(_texts(seg, "needs_you")))
finally:
    rails.declared = _real_declared               # type: ignore[assignment]

# ── never raises, whatever the store does ────────────────────────────────────────────────────
print("\n— a count is never worth taking the morning page down for —")
voice_report._space_name = _REAL_SPACE_NAME       # type: ignore[assignment]
_vr = voice_report

from core import spaces as _sp  # noqa: E402

_real_all = _sp.all_spaces
_sp.all_spaces = lambda: (_ for _ in ()).throw(RuntimeError("no database yet"))  # type: ignore
try:
    ok("a Space resolver that cannot answer falls back instead of raising",
       _vr._space_name() == "default")
except Exception as e:                            # noqa: BLE001 — that IS the assertion
    ok("a Space resolver that cannot answer falls back instead of raising", False, repr(e))
finally:
    _sp.all_spaces = _real_all                    # type: ignore[assignment]

print("\n" + ("FAILED: " + ", ".join(FAILS) if FAILS else "ALL OK"))
sys.exit(1 if FAILS else 0)
