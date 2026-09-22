"""The drafter cannot be held up forever by work it can never do.

MEASURED ON THE OWNER'S BOX, 2026-09-22, seven hours after it began. 87 conversations needed a
draft. 8 existed. `periodic()` returned `{"drafted": 0}` every two minutes and said nothing.

  · `needs_a_draft` returns NEWEST-FIRST, and the sweep takes the first `per_sweep` (3)
  · the newest fifteen Instagram inbounds had arrived at 06:48 with EMPTY bodies — media-only
    messages, a photo or a reel share with no text
  · `draft_one` returns None on an empty inbound, which is correct and which is SILENT
  · so every sweep picked the same empty rows, wrote nothing, and reported success

72 conversations with real text were never reachable. `/health` was green, systemd said active,
the worker's threads were alive and looping. Every signal we had said the product was working.

TWO BUGS, FIXED SEPARATELY, because either one alone would let this happen again:

  1. the QUEUE must not offer work that can never be done — an inbound with no words is excluded
     in SQL, so it can never take a slot ahead of a customer who actually wrote something
  2. a SWEEP THAT DID NOTHING must say so — the alarm is on the OUTCOME, not on this cause.
     Logging "empty body" would catch this one and nothing else; the next silent cause would be
     invisible all over again.

Run: python tests/test_the_drafter_cannot_be_head_blocked.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "jam.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core import state                                           # noqa: E402
from core import spaces as _spaces                               # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "key": "k"}

from marketing.customer_voice.drafter import draft, store as ds  # noqa: E402
from marketing.customer_voice.inbox import store as inbox        # noqa: E402

SPACE = "acme"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def inbound(zcid: str, mid: str, body: str, when: str, who: str = "Sam") -> None:
    inbox.upsert_conversation(space=SPACE, zcid=zcid, participant=who, account_id="acct-1")
    inbox.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in", sent_by=who, body=body)
    with state.connect() as c:
        c.execute("UPDATE inbox_messages SET created_at = ? WHERE zernio_message_id = ?", (when, mid))


# ── 1. the queue ────────────────────────────────────────────────────────────────────────────
print("test_a_message_with_no_words_never_takes_a_slot")
# The owner's box, in miniature: the NEWEST arrivals are media-only, the real question is older.
inbound("c-asked", "m-asked", "Do you open on Sundays?", "2026-09-22T05:00:00Z", "Dana")
for i in range(6):
    inbound(f"c-media-{i}", f"m-media-{i}", "", f"2026-09-22T06:48:0{i}Z", f"Media {i}")

rows = ds.needs_a_draft(SPACE, limit=5)          # the default the sweep uses
ok("the queue offers only work that can be done", all(str(r["inbound_body"]).strip() for r in rows),
   str([(r["zcid"], r["inbound_body"]) for r in rows]))
ok("...so the person who actually wrote something is reachable",
   "c-asked" in [r["zcid"] for r in rows], str([r["zcid"] for r in rows]))
ok("...and the media-only ones are simply not in it",
   not any(str(r["zcid"]).startswith("c-media") for r in rows), str([r["zcid"] for r in rows]))

print("test_a_body_of_only_whitespace_counts_as_no_words")
inbound("c-blank", "m-blank", "   \n\t ", "2026-09-22T07:00:00Z", "Blank")
ok("whitespace is not a question", "c-blank" not in [r["zcid"] for r in ds.needs_a_draft(SPACE, limit=20)])

print("test_the_head_block_itself_cannot_recur")
# THE REGRESSION, STATED AS THE SHAPE IT HAD: enough empty arrivals to fill the whole sweep, all
# newer than the real one. Before the fix this returned five empties and the sweep wrote nothing
# for as long as they stayed newest — which was seven hours.
for i in range(20):
    inbound(f"c-flood-{i}", f"m-flood-{i}", "", f"2026-09-22T08:{i:02d}:00Z", f"Flood {i}")
rows = ds.needs_a_draft(SPACE, limit=5)
ok("twenty newer empty arrivals do not bury the one real question",
   "c-asked" in [r["zcid"] for r in rows], str([r["zcid"] for r in rows]))


# ── 2. the alarm ────────────────────────────────────────────────────────────────────────────
print("test_a_sweep_that_did_nothing_says_so")
SEEN: list[tuple] = []
_real = draft.log.warning
draft.log.warning = lambda msg, **kw: SEEN.append((msg, kw))
draft.enabled = lambda: True
draft.per_sweep = lambda: 3

# Nothing can be drafted — every candidate already has a draft — but the queue is NOT empty.
for r in ds.needs_a_draft(SPACE, limit=50):
    ds.put(space=SPACE, zcid=r["zcid"], in_reply_to=r["inbound_id"], body="already answered")
inbound("c-real", "m-real", "Are you open today?", "2026-09-22T09:00:00Z", "Ada")
draft.draft_one = lambda **kw: None              # the model declines, silently, as it may

SEEN.clear()
res = draft.sweep(SPACE)
ok("the sweep reports it wrote nothing", res.get("drafted") == 0, str(res))
ok("...and says how much work it was looking at", res.get("considered", 0) >= 1, str(res))
ok("...OUT LOUD, so seven hours of this cannot look like success",
   any(m == "drafter.sweep_wrote_nothing" for m, _ in SEEN), str(SEEN)[:200])
_extra = next((kw.get("extra", {}) for m, kw in SEEN if m == "drafter.sweep_wrote_nothing"), {})
ok("...naming the space, the count and the channels, so it can be acted on",
   _extra.get("space") == SPACE and _extra.get("considered", 0) >= 1 and "platforms" in _extra,
   str(_extra))

print("test_a_sweep_that_did_its_work_stays_quiet")
draft.draft_one = lambda **kw: "Yes — nine to five."
SEEN.clear()
res = draft.sweep(SPACE)
ok("it drafted", res.get("drafted", 0) >= 1, str(res))
ok("...and raised no alarm", not any(m == "drafter.sweep_wrote_nothing" for m, _ in SEEN), str(SEEN)[:160])

print("test_an_empty_queue_is_not_an_alarm")
# Nothing waiting is the healthy resting state of a box that is caught up, and it must never page.
with state.connect() as c:
    c.execute("DELETE FROM inbox_messages WHERE space = ?", (SPACE,))
SEEN.clear()
res = draft.sweep(SPACE)
ok("no work, no drafts, no noise",
   res.get("drafted") == 0 and not any(m == "drafter.sweep_wrote_nothing" for m, _ in SEEN), str(res))

draft.log.warning = _real
print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
