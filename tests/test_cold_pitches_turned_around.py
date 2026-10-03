"""Cold pitches, turned around: the box drafts a reply that sells back, and nothing sends until a person does.

Owner, 2026-10-02: "I get tons of cold email and I want to advertise right back to them and turn it right around on
them." He chose: the box drafts each one and he approves (one at a time or all at once); the link is www.ownbox.io;
no promo code yet. On 2026-10-03: "Yes, proceed and do numbers one and then two. Finish them." Measured here, with
only the model's words stood in for:
  * OFF until the owner turns it on with a link: the drafter's instructions are exactly what they were
  * ON: the same one model call learns a fourth case, carrying his link; no second call
  * a pitch-back draft has its marker removed, always carries his link, and is marked as a cold pitch
  * a model that says PITCH_BACK while it is off gets an ordinary draft
  * Replies to send labels each one and ticks them all with one tap; it still sends nothing by itself
  * Inbox Settings, Cold Pitches: the owner turns it on with a website, a member reads it, a bad address is refused
  * the owner's AI can propose turning it on, which lands only on his tap

Run: python tests/test_cold_pitches_turned_around.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "pitch.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import approvals, brain, cost_guard, dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.drafter import draft, store as drafts  # noqa: E402
from marketing.customer_voice.inbox import pitch_back, store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()
LINK = "www.example-business.com"


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


calls = []
answer = {"text": ""}
cost_guard.check_vendor = lambda *a, **k: None
brain.think = lambda **kw: calls.append(kw) or answer["text"]


def pitch(n: int, text: str) -> str:
    zcid, mid = f"conv-{n}", f"m-{n}"
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="email", participant=f"Vendor {n}",
                              last_inbound_at="2026-10-03T09:00:00Z", account_id="acc-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in", sent_by="contact", body=text)
    return zcid


print("\nOff until the owner turns it on\n")
z1 = pitch(1, "Hi! We help companies 10x their leads with our agency. Want a call next week?")
answer["text"] = "Thanks for reaching out."
draft.draft_one(space=SPACE, zcid=z1, in_reply_to="m-1", inbound="Hi! We help companies 10x their leads.")
ok("off: the instructions are exactly what they were", calls and calls[-1]["system"] == draft.SYSTEM)
ok("off: one model call, as before", len(calls) == 1)

print("\nOn, with his link\n")
pitch_back.put(True, LINK, by="owner")
z2 = pitch(2, "We build websites for businesses like yours. Interested?")
answer["text"] = "PITCH_BACK: Thanks for thinking of us. We run AI business machines for owners."
calls.clear()
got = draft.draft_one(space=SPACE, zcid=z2, in_reply_to="m-2", inbound="We build websites. Interested?")
ok("on: still one model call", len(calls) == 1, len(calls))
ok("on: the same call learns the fourth case, carrying his link", "SOMEONE IS SELLING TO THE BUSINESS"
   in calls[-1]["system"] and LINK in calls[-1]["system"])
ok("the marker never reaches the draft", got and not got.upper().startswith("PITCH_BACK"), got)
ok("the model forgot the link, so the box adds it", got.endswith(f"Take a look: {LINK}"), got)
ok("the draft is marked as a cold pitch", "m-2" in drafts.pitch_backs(SPACE))
z3 = pitch(3, "Can we hop on a call about SEO?")
answer["text"] = "PITCH_BACK: Thanks! Take a look at https://example-business.com/ to see what we do."
got = draft.draft_one(space=SPACE, zcid=z3, in_reply_to="m-3", inbound="SEO call?")
ok("a reply that already carries the link is not given it twice", got.lower().count("example-business.com") == 1,
   got)
z4 = pitch(4, "Do you open on Sundays?")
answer["text"] = "Yes, we open at ten on Sundays."
got = draft.draft_one(space=SPACE, zcid=z4, in_reply_to="m-4", inbound="Do you open on Sundays?")
ok("a real customer gets an ordinary draft, unmarked", got == "Yes, we open at ten on Sundays."
   and "m-4" not in drafts.pitch_backs(SPACE))

print("\nA model that pitches back while it is off\n")
pitch_back.put(False, LINK, by="owner")
z5 = pitch(5, "Our agency can help.")
answer["text"] = "PITCH_BACK: Thanks, not right now."
got = draft.draft_one(space=SPACE, zcid=z5, in_reply_to="m-5", inbound="Our agency can help.")
ok("gets an ordinary draft, marker gone, unmarked", got == "Thanks, not right now." and "m-5" not in
   drafts.pitch_backs(SPACE), got)
pitch_back.put(True, LINK, by="owner")

print("\nReplies to send\n")
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = c.get("/inbox/waiting").get_data(as_text=True)
ok("each turned-around reply is labelled", page.count("A cold pitch, turned around") == 2, page.count("cold pitch"))
ok("one tap ticks them all", "Tick all 2 cold pitches" in page and page.count("data-pitch=1") == 2)
ok("...and that tap only ticks: it is not a submit button", 'type="button"' in page.split("Tick all 2")[0][-400:])

print("\nInbox Settings, Cold Pitches\n")
html = c.get("/inbox/pitch-back").get_data(as_text=True)
ok("the owner sees the switch and his website", 'name="on"' in html and LINK in html and "checked" in html)
ok("...at 16px or larger on a mobile", "max(16px" in html)
r = c.post("/inbox/pitch-back", data={"link": LINK})
ok("unticking turns it off", r.status_code == 303 and pitch_back.get()["on"] is False)
r = c.post("/inbox/pitch-back", data={"on": "1", "link": "not a website"})
ok("a bad address is refused in words", r.status_code == 200 and "web address" in r.get_data(as_text=True)
   and pitch_back.get()["on"] is False)
r = c.post("/inbox/pitch-back", data={"on": "1", "link": ""})
ok("on with no website is refused: every reply points there", r.status_code == 200
   and "Add your website first" in r.get_data(as_text=True))
c.post("/inbox/pitch-back", data={"on": "1", "link": LINK})
ok("on with his website saves", pitch_back.get() == {"on": True, "link": LINK})
ok("the settings menu lists it", "/inbox/pitch-back" in c.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-business.com", name="Sam", role="member")["id"]))
ok("a member reads it", LINK in m.get("/inbox/pitch-back").get_data(as_text=True))
m.post("/inbox/pitch-back", data={"link": ""})
ok("...but can't change it", pitch_back.get()["on"] is True)

print("\nFrom the owner's AI\n")
pitch_back.put(False, "", by="owner")
asked = inbox_tools.propose_pitch_back(on=True, link=LINK, seat={"label": "Claude"})
ok("it can ask to turn it on, and nothing changes yet", asked.get("asked") is True and not pitch_back.get()["on"],
   asked)
approvals.decide(asked["approval"], True, by="owner@example-business.com")
ok("approved, it is on with his link", pitch_back.get() == {"on": True, "link": LINK})
ok("on with no link is refused before anyone is asked",
   inbox_tools.propose_pitch_back(on=True, link="", seat={"label": "Claude"}).get("asked") is False)

print("\nALL COLD PITCH CHECKS PASS" if not FAILS else f"\n{len(FAILS)} COLD PITCH CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
