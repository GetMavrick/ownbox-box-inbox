"""Sending has a screen: the first message and the hourly cap, settable by the owner (Inbox Settings, Sending).

Owner, 2026-10-04: "Seems like there's some things that are not even built out yet." Until today both lived only in
the configuration file, which a buyer cannot edit, and inbox.settings reported them as "not on a screen yet".
Measured here, with no network and no model:
  * untouched, every reader gives exactly what the configuration file gave
  * the owner saves the first message, its words and the cap; the handler, the reply path and the tools read them
  * the fail-closed rule stands: a first message turned on with no words is refused; the box sends one only when
    the setting says on
  * a bad cap is refused in words; 16px fields and 48px controls; a member reads; the settings menu lists it
  * the owner's AI reads it in inbox.settings and can propose a change, which lands only on his tap

Run: python tests/test_sending_has_a_screen.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "sending.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import approvals, dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.inbox import handler, sending  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()
CFG = {"autonomy": "off", "hourly_send_cap": 40, "opener_template": "Hi from the file."}
handler._cfg = lambda: CFG


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:300]}"))
    if not cond:
        FAILS.append(label)


def inbound(n: int) -> dict:
    """One new Instagram message with no account to reply from: past the sending gate the handler stops at
    no_account, before it would ever reach a vendor."""
    return {"raw_text": json.dumps({"space": SPACE, "zcid": f"conv-{n}", "platform": "instagram",
                                    "inbound_text": "Hi, do you do Botox?",
                                    "inbound_at": datetime.now(timezone.utc).isoformat()})}


print("\nUntouched, the configuration file decides\n")
g = sending.get()
ok("nothing sends on its own; the words and the cap are the file's", g["first_message"] == "off" and g["hourly_cap"] == 40
   and g["text"].startswith("Hey") and sending.autonomy(CFG) == "off", g)
ok("...the handler only observes", handler.handle(inbound(1)).get("status") == "observed")
ok("...and the handler's first message is the file's", handler._opener_text({}) == "Hi from the file."
   and sending.first_message_text(CFG, {"opener_template": "Hi from the Space."}) == "Hi from the Space.")
ok("a 0 in the file still means send nothing, never the default 40; only a missing or unreadable cap takes 40",
   sending.hourly_cap({"hourly_send_cap": 0}) == 0 and sending.hourly_cap({"hourly_send_cap": "0"}) == 0
   and sending.hourly_cap({}) == 40 and sending.hourly_cap({"hourly_send_cap": "lots"}) == 40,
   [sending.hourly_cap(c) for c in ({"hourly_send_cap": 0}, {"hourly_send_cap": "0"}, {}, {"hourly_send_cap": "lots"})])
s = {x["name"]: x for x in inbox_tools.settings()["settings"]}
ok("inbox.settings says off, 40, and where to change them", s["opener"]["value"] == "off"
   and s["hourly_send_cap"]["value"] == 40 and s["opener"]["changed_at"] == "/inbox/sending"
   and s["hourly_send_cap"]["changed_at"] == "/inbox/sending", s)

print("\nThe owner turns the first message on and sets the cap\n")
try:
    sending.put(first_message="on", text="", by="owner")
    said = ""
except ValueError as e:
    said = str(e)
ok("a first message with no words is refused in words", "Write the first message" in said and not sending.first_message_on(), said)
cur = sending.put(first_message="on", text="  Thanks!  I got your message and I'm on it. ", hourly_cap="25", by="owner")
ok("saved, tidied", cur == {"first_message": "on", "text": "Thanks! I got your message and I'm on it.", "hourly_cap": 25}, cur)
ok("the handler now sends: it passes the gate and stops only for want of an account",
   handler.handle(inbound(2)).get("status") == "no_account")
ok("...with the owner's words, over the file's and the Space's",
   handler._opener_text({"opener_template": "Hi from the Space."}) == "Thanks! I got your message and I'm on it.")
ok("...under the owner's cap", sending.hourly_cap(CFG) == 25 and inbox_tools.status()["hourly_send_cap"] == 25)
from core.exceptions import RateCapped  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

sending.put(hourly_cap=1, by="owner")
store.record_send(space=SPACE, zcid="conv-earlier", idem_key="opener:earlier", kind="opener", status="ok")
job = inbound(9)
job["raw_text"] = json.dumps({**json.loads(job["raw_text"]), "account_id": "acc-1"})
try:
    handler.handle(job)
    capped = False
except RateCapped:
    capped = True
except Exception as e:                                   # noqa: BLE001 — past the cap it reaches for a vendor
    capped = f"went past the cap: {type(e).__name__}"
ok("the handler keeps the owner's cap: at 1 an hour, with one sent, the next waits", capped is True, capped)
sending.put(hourly_cap=25, by="owner")
src = (ROOT / "marketing/customer_voice/inbox/reply.py").read_text()
ok("the reply path reads the same cap, both places", src.count("sending.hourly_cap()") == 2
   and "hourly_send_cap" not in src.replace("inbox.hourly_send_cap", ""), src.count("sending.hourly_cap()"))
for bad in ("0", "201", "ten", ""):
    try:
        sending.put(hourly_cap=bad, by="owner")
        said = ""
    except ValueError as e:
        said = str(e)
    ok(f"a cap of {bad!r} is refused in words", "whole number from 1 to 200" in said, said)
ok("...and nothing changed", sending.get()["hourly_cap"] == 25)
sending.put(first_message="off", by="owner")
ok("off again: the handler observes, the words are kept", handler.handle(inbound(3)).get("status") == "observed"
   and sending.get()["text"].startswith("Thanks!"))

print("\nInbox Settings, Sending\n")
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
html = o.get("/inbox/sending").get_data(as_text=True)
ok("the owner sees the switch, the words and the cap", 'name="first_message"' in html and 'name="text"' in html
   and 'name="hourly_cap"' in html and 'value="25"' in html and "I&#x27;m on it" in html, html[:400])
ok("...at 16px, 48px tall, on a mobile", "max(16px" in html and "min-height:48px" in html and 'inputmode="numeric"' in html)
ok("...and says in plain words that the default is safe and a high cap can get mail marked as junk (OSDev1, #1945)",
   "The default, 40, is safe" in html and "marked as junk" in html)
r = o.post("/inbox/sending", data={"first_message": "on", "text": "Hello! One moment.", "hourly_cap": "30"})
ok("saving works", r.status_code == 303 and sending.get() == {"first_message": "on", "text": "Hello! One moment.",
                                                                "hourly_cap": 30}, sending.get())
r = o.post("/inbox/sending", data={"first_message": "on", "text": "", "hourly_cap": "30"})
ok("turning it on with no words is refused on the page, in words", r.status_code == 200
   and "Write the first message" in r.get_data(as_text=True) and sending.get()["text"] == "Hello! One moment.")
r = o.post("/inbox/sending", data={"first_message": "off", "text": "Hello! One moment.", "hourly_cap": "500"})
ok("a bad cap is refused on the page, in words", r.status_code == 200
   and "whole number from 1 to 200" in r.get_data(as_text=True) and sending.get()["hourly_cap"] == 30)
ok("the settings menu lists it", "/inbox/sending" in o.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-medspa.com", name="Sam", role="member")["id"]))
page = m.get("/inbox/sending").get_data(as_text=True)
ok("a member reads it", "First message on its own: On" in page and "Most messages in an hour: 30" in page, page[-500:])
m.post("/inbox/sending", data={"first_message": "off", "hourly_cap": "5"})
ok("...but can't change it", sending.get()["hourly_cap"] == 30 and sending.first_message_on())

print("\nFrom the owner's AI\n")
asked = inbox_tools.propose_hourly_cap(cap=60, seat={"label": "Claude"})
ok("it can ask, and nothing changes yet", asked.get("asked") is True and sending.get()["hourly_cap"] == 30, asked)
approvals.decide(asked["approval"], True, by="owner@example-medspa.com")
ok("approved, the cap is 60", sending.get()["hourly_cap"] == 60, sending.get())
ok("asking for the cap it already has asks nothing",
   inbox_tools.propose_hourly_cap(cap=60, seat={"label": "Claude"}).get("asked") is False)
ok("a bad cap is refused before anyone is asked",
   inbox_tools.propose_hourly_cap(cap=0, seat={"label": "Claude"}).get("asked") is False)
ok("turning it on with no words is refused before anyone is asked",
   inbox_tools.propose_first_message(on=True, text="", seat={"label": "Claude"}).get("asked") is False)
ok("asking for what is already set asks nothing",
   inbox_tools.propose_first_message(on=True, seat={"label": "Claude"}).get("asked") is False)
asked = inbox_tools.propose_first_message(on=False, seat={"label": "Claude"})
approvals.decide(asked["approval"], True, by="owner@example-medspa.com")
ok("approved, off: the handler observes again", not sending.first_message_on()
   and handler.handle(inbound(4)).get("status") == "observed")

print("\nALL SENDING CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SENDING CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
