"""Reply Style, per channel: the owner chooses how the box drafts, Sales or Customer service, for email and for DMs.

Owner, 2026-10-04: "I wish there was a setting of a style of response that we could select per channel. Like for me I
would choose sales focus for email and DM's. Other people might choose customer service for emails and sales for DMS.
I just always want to be trying to get more business so want to always be closing ABC." Measured here, with only the
model's words stood in for:
  * nobody has chosen: the drafter's instructions are exactly what they were
  * Sales on email: an email draft's one model call learns the sales style, with his website when he has one; a DM's
    does not
  * Customer service on DMs: a DM's call learns the service style
  * still one model call, still nothing sent
  * Inbox Settings, Reply Style: the owner picks both, a member reads them, a bad value is refused in words, 16px
    fields and 48px controls on a mobile, listed in the settings menu
  * the owner's AI reads it in inbox.settings and can propose a change, which lands only on his tap

Run: python tests/test_reply_style_per_channel.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "style.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import approvals, brain, cost_guard, dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.drafter import draft  # noqa: E402
from marketing.customer_voice.inbox import pitch_back, reply_style, store  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:300]}"))
    if not cond:
        FAILS.append(label)


calls = []
cost_guard.check_vendor = lambda *a, **k: None
brain.think = lambda **kw: calls.append(kw) or "Thanks, happy to help."
n = [0]


def ask(platform: str, text="Do you do Botox?") -> dict:
    n[0] += 1
    zcid, mid = f"conv-{n[0]}", f"m-{n[0]}"
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=f"Person {n[0]}",
                              last_inbound_at="2026-10-04T09:00:00Z", account_id="acc-1")
    store.record_message(space=SPACE, zcid=zcid, zmid=mid, direction="in", sent_by="contact", body=text)
    calls.clear()
    draft.draft_one(space=SPACE, zcid=zcid, in_reply_to=mid, inbound=text, platform=platform)
    return calls[-1] if calls else {}


print("\nNobody has chosen\n")
c = ask("email")
ok("the instructions are exactly what they were", c.get("system") == draft.SYSTEM, c.get("system", "")[-200:])
ok("...and Settings shows Customer service for both", reply_style.get() == {"email": "service", "dms": "service"})

print("\nSales on email, Customer service on DMs\n")
reply_style.put(email="sales", dms="service", by="owner")
c = ask("email")
ok("an email draft learns the sales style", "STYLE FOR THIS REPLY: SALES" in c.get("system", "")
   and "closer to buying or booking" in c.get("system", ""), c.get("system", "")[-300:])
ok("...in the same one model call", len(calls) == 1)
ok("...and the rules still bind it: nothing invented", "never invented" in c.get("system", "")
   and "Never invent a price" in c.get("system", ""))
c = ask("instagram")
ok("a DM draft learns the service style instead", "STYLE FOR THIS REPLY: CUSTOMER SERVICE" in c.get("system", "")
   and "SALES" not in c.get("system", ""), c.get("system", "")[-200:])
pitch_back.put(False, "www.example-medspa.com", by="owner")
c = ask("email")
ok("with his website on file, the sales step names it", "(www.example-medspa.com)" in c.get("system", ""))
reply_style.put(dms="sales", by="owner")
ok("every non-email channel is a DM", "SALES" in ask("messenger").get("system", "")
   and "SALES" in ask("facebook").get("system", ""))
ok("the sweep passes the conversation's platform", 'platform=row.get("platform")'
   in (ROOT / "marketing/customer_voice/drafter/draft.py").read_text())
for bad in ("pushy", "", "SALES!"):
    try:
        reply_style.put(email=bad, by="owner")
        said = ""
    except ValueError as e:
        said = str(e)
    ok(f"{bad!r} is refused in words", "Sales or Customer service" in said, said)
ok("...and nothing changed", reply_style.get() == {"email": "sales", "dms": "sales"})

print("\nInbox Settings, Reply Style\n")
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
html = o.get("/inbox/reply-style").get_data(as_text=True)
ok("the owner sees both channels, Sales selected", 'name="email"' in html and 'name="dms"' in html
   and html.count('value="sales" selected') == 2, html[:300])
ok("...at 16px, 48px tall, on a mobile", "max(16px" in html and "min-height:48px" in html)
r = o.post("/inbox/reply-style", data={"email": "service", "dms": "sales"})
ok("saving works", r.status_code == 303 and reply_style.get() == {"email": "service", "dms": "sales"})
r = o.post("/inbox/reply-style", data={"email": "rude", "dms": "sales"})
ok("a bad value is refused on the page, in words", r.status_code == 200 and "Sales or Customer service"
   in r.get_data(as_text=True) and reply_style.get()["email"] == "service")
ok("the settings menu lists it", "/inbox/reply-style" in o.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-medspa.com", name="Sam", role="member")["id"]))
ok("a member reads it", "Direct messages: Sales" in m.get("/inbox/reply-style").get_data(as_text=True))
m.post("/inbox/reply-style", data={"email": "sales", "dms": "service"})
ok("...but can't change it", reply_style.get() == {"email": "service", "dms": "sales"})

print("\nFrom the owner's AI\n")
s = {x["name"]: x["value"] for x in inbox_tools.settings()["settings"]}
ok("inbox.settings says the style per channel", s.get("reply_style_email") == "service"
   and s.get("reply_style_dms") == "sales", s)
asked = inbox_tools.propose_reply_style(email="sales", seat={"label": "Claude"})
ok("it can ask, and nothing changes yet", asked.get("asked") is True and reply_style.get()["email"] == "service",
   asked)
approvals.decide(asked["approval"], True, by="owner@example-medspa.com")
ok("approved, email drafts in sales", reply_style.get() == {"email": "sales", "dms": "sales"}, reply_style.get())
ok("a bad style is refused before anyone is asked",
   inbox_tools.propose_reply_style(dms="loud", seat={"label": "Claude"}).get("asked") is False)
ok("asking for what is already set asks nothing",
   inbox_tools.propose_reply_style(email="sales", seat={"label": "Claude"}).get("asked") is False)

print("\nALL REPLY STYLE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} REPLY STYLE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
