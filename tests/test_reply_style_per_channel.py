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
from marketing.customer_voice.inbox import answering, pitch_back, reply_style, store  # noqa: E402
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
ok("an email draft learns the sales style", "STYLE FOR THIS REPLY: STRONG SALES" in c.get("system", "")
   and "push for the booking or the sale" in c.get("system", ""), c.get("system", "")[-300:])
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
    ok(f"{bad!r} is refused in words", "Customer service, Subtle sales or Strong sales" in said, said)
ok("...and nothing changed", reply_style.get() == {"email": "sales", "dms": "sales"})

print("\nInbox Settings, Reply Style\n")
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
# THE CHOICE IS ON CHANNELS (2026-10-07), a card per channel (tests/test_each_channel_has_a_card.py); this page keeps
# what each level does, for the owner and a member alike.
html = o.get("/inbox/reply-style").get_data(as_text=True)
ok("the page explains the levels and sends the choice to Channels",
   all(w in html for w in reply_style.STYLES.values()) and 'href="/inbox/channels"' in html and "<select" not in html)
r = o.post("/inbox/channels", data={"channel": "email", "style": "service"})
ok("saving email's style on Channels works", r.status_code == 303
   and reply_style.get() == {"email": "service", "dms": "sales"}, reply_style.get())
r = o.post("/inbox/channels", data={"channel": "email", "style": "rude"})
ok("a bad value is refused on the page, in words", r.status_code == 200 and "Subtle sales or Strong sales"
   in r.get_data(as_text=True) and reply_style.get()["email"] == "service")
ok("the settings menu lists it", "/inbox/reply-style" in o.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-medspa.com", name="Sam", role="member")["id"]))
ok("a member reads it", "Strong sales" in m.get("/inbox/reply-style").get_data(as_text=True))
m.post("/inbox/channels", data={"channel": "email", "style": "sales"})
ok("...but can't change a style", reply_style.get() == {"email": "service", "dms": "sales"})

print("\nFrom the owner's AI\n")
s = {x["name"]: x["value"] for x in inbox_tools.settings()["settings"]}
ok("inbox.settings says the style per channel", s.get("reply_style_email") == "service"
   and s.get("reply_style_instagram") == "sales" and s.get("reply_style_messenger") == "sales", s)
asked = inbox_tools.propose_reply_style(email="sales", seat={"label": "Claude"})
ok("it can ask, and nothing changes yet", asked.get("asked") is True and reply_style.get()["email"] == "service",
   asked)
approvals.decide(asked["approval"], True, by="owner@example-medspa.com")
ok("approved, email drafts in sales", reply_style.get() == {"email": "sales", "dms": "sales"}, reply_style.get())
ok("a bad style is refused before anyone is asked",
   inbox_tools.propose_reply_style(dms="loud", seat={"label": "Claude"}).get("asked") is False)
ok("asking for what is already set asks nothing",
   inbox_tools.propose_reply_style(email="sales", seat={"label": "Claude"}).get("asked") is False)

print("\nLevels (owner, 2026-10-04: \"That's why we should have levels and settings.\")\n")
reply_style.put(email="subtle", dms="service", by="owner")
c = ask("email")
sm = c.get("system", "")
ok("Subtle sales: every written reply ends with one light sentence about the business, his website named",
   "SUBTLE SALES" in sm and "end every reply you write (cases 1, 2 and 4; never case 3)" in sm
   and "one short, light, natural sentence" in sm and "(www.example-medspa.com)" in sm, sm[-600:])
ok("...without the strong close", "STRONG SALES" not in sm and "ask for the booking or the sale" not in sm)
ok("every level reads who wrote first: a prospect, a customer, anyone else (owner 10-04: adaptive)",
   all(k in sm for k in ("A PROSPECT", "A CUSTOMER", "ANYONE ELSE", "This level only shades")), sm[-900:])
ok("...and a customer with a problem gets service first, at every level", "never sell on top of a problem" in sm
   and "except to a customer with an open problem" in sm)
reply_style.put(email="sales", by="owner")
sm = ask("email").get("system", "")
ok("Strong sales: prospects are pushed for the booking or the sale (owner 10-04: PROSPECTS)",
   "STRONG SALES" in sm and "- Prospect: answer it first, then always push for the booking or the sale" in sm
   and "ask for the booking or the sale, plainly" in sm, sm[-900:])
ok("...and every reply to a prospect ends with a CTA (owner 10-04: \"all include CTA's\")",
   "Every reply to a prospect ends with one clear CTA" in sm and "never let a reply to a prospect end without one" in sm)
ok("...customers switch to problem-solving service", "- Customer: switch to problem-solving service" in sm)
ok("...and everyone else gets the light sentence, never a hard sell", "THE LIGHT SENTENCE" in sm
   and "- Anyone else: the light sentence, never a hard sell." in sm)
sv = ask("instagram").get("system", "")
ok("Customer service adapts too, and sells to nobody", "CUSTOMER SERVICE" in sv and "A PROSPECT" in sv
   and "THE LIGHT SENTENCE" not in sv and "No selling, no upsell" in sv, sv[-600:])
ok("...pure service: how to book only if they asked (owner 10-04: \"more extreme one way or the other\")",
   "never to sell" in sv and "only if they asked" in sv)
page = o.get("/inbox/reply-style").get_data(as_text=True)
ok("the page explains each level for prospects, customers and anyone else (owner 10-04)",
   all(x in page for x in ("Your box reads every message", "a <b>prospect</b>", "a <b>customer</b>",
                           "Startup mode", "a clear next step in every reply", "Customers: problem-solving service", "sets how far it leans")),
   page[:800])
_p = o.get("/inbox/reply-style").get_data(as_text=True)
ok("the levels are offered gentlest first, on the page", [*reply_style.STYLES] == ["service", "subtle", "sales"]
   and -1 < _p.find("Customer service") < _p.find("Subtle sales") < _p.find("Strong sales"))
ok("names a person might use are understood", reply_style.clean("Strong") == "sales"
   and reply_style.clean("aggressive") == "sales" and reply_style.clean("Subtle sales") == "subtle")

# LAST, because it styles a DM channel on its own, which the checks above don't expect: a chat asking for DMs still
# reaches a DM channel the owner styled on Channels, which reads its own style before the all-DMs one.
answering.put("instagram", style="service", by="owner")
asked = inbox_tools.propose_reply_style(dms="subtle", seat={"label": "Claude"})
approvals.decide(asked["approval"], True, by="owner@example-medspa.com")
ok("approved for DMs, every DM channel writes that way, one styled on its own included",
   reply_style.get()["dms"] == "subtle"
   and all(r["style"] == "subtle" for r in answering.get() if r["channel"] != "email"), answering.get())

print("\nALL REPLY STYLE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} REPLY STYLE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
