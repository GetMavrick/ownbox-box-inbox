"""Each channel has a card: the Channels screen in Inbox Settings (owner, 2026-10-07).

Owner, 2026-10-07: "We also need to build different settings per channel. For example, DM's get an auto reply whereas
email just gets an auto draft." The backend is inbox/answering.py (tests/test_each_channel_answers_its_own_way.py);
this holds the screen on it (docs/SCOPE_CHANNELS_SCREEN.md) and OSDev1's fold-in: Channels is the ONE home for
answering a channel, so Sending and Reply Style keep no control for it and link here.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a channel has no card, or email is offered Auto-reply;
  * a choice on the screen doesn't reach answering.put (by the form, or by a tap, which asks for JSON);
  * a refused choice writes anything, or isn't said in words;
  * a member can change a channel;
  * Sending or Reply Style still changes how a channel is answered, or Sending's save switches Auto-reply off;
  * the screen is missing from the Settings menu.

No network, no model.

Run: python tests/test_each_channel_has_a_card.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "channel-cards.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "dash-pw"

from core import dash, state  # noqa: E402

state.init_db()
try:
    import marketing.customer_voice.app  # noqa: F401,E402
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)
from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import answering, sending  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def row(ch: str) -> dict:
    return next(r for r in answering.get() if r["channel"] == ch)


def card(html: str, ch: str) -> str:
    m = re.search(rf'<form class="card ans" id="ch-{ch}".*?</form>', html, re.S)
    return m.group(0) if m else ""


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
JSON = {"Accept": "application/json"}

print("test_the_owner_sees_a_card_per_channel")
html = o.get("/inbox/channels").get_data(as_text=True)
for r in answering.get():
    c = card(html, r["channel"])
    ok(f"{r['label']} has a card, its choices the backend's own, its current one chosen",
       c and [v for v in re.findall(r'name="mode" value="(\w+)"', c)] == [m["value"] for m in r["modes"]]
       and f'value="{r["mode"]}" checked' in c and f'value="{r["style"]}" selected' in c, c[:300])
ok("...and email offers no Auto-reply", 'value="auto"' not in card(html, "email"))
ok("each control is a 48px target at 16px on a phone", "min-height:48px" in html and "max(16px" in html)
ok("the Settings menu lists it", 'href="/inbox/channels"' in o.get("/inbox/settings").get_data(as_text=True))

print("\ntest_a_choice_is_saved")
r = o.post("/inbox/channels", data={"channel": "messenger", "mode": "off", "style": "subtle"})
ok("by the form: saved, and back to its card", r.status_code == 303
   and r.headers["Location"].endswith("/inbox/channels?saved=messenger#ch-messenger")
   and (row("messenger")["mode"], row("messenger")["style"]) == ("off", "subtle"), r.headers.get("Location"))
r = o.post("/inbox/channels", data={"channel": "instagram", "mode": "auto", "style": "sales"}, headers=JSON)
j = r.get_json() or {}
ok("by a tap (JSON): saved, and the card's new line comes back",
   r.status_code == 200 and j.get("ok") and j.get("mode") == "auto" and j.get("style") == "sales"
   and j.get("line") and sending.auto_reply_since("instagram"), j)
ok("...the page shows it on a reload", 'value="auto" checked' in card(o.get("/inbox/channels")
                                                                     .get_data(as_text=True), "instagram"))
before = answering.get()
r = o.post("/inbox/channels", data={"channel": "email", "mode": "auto", "style": "sales"}, headers=JSON)
ok("email set to Auto-reply is refused in words, by a tap", r.status_code == 400
   and (r.get_json() or {}).get("ok") is False and (r.get_json() or {}).get("error"), r.get_data(as_text=True))
try:
    answering.clean_mode("email", "auto")
    said = ""
except ValueError as e:
    said = str(e).replace("'", "&#x27;")
r = o.post("/inbox/channels", data={"channel": "email", "mode": "auto"})
ok("...and by the form, on the page, in the backend's own words", r.status_code == 200 and said
   and said in r.get_data(as_text=True), said)
r = o.post("/inbox/channels", data={"channel": "email", "mode": "draft", "style": "loud"}, headers=JSON)
ok("a bad style is refused before the mode is written", r.status_code == 400)
r = o.post("/inbox/channels", data={"channel": "fax", "mode": "draft"}, headers=JSON)
ok("an unknown channel is refused", r.status_code == 400)
ok("...and nothing changed on any of them", answering.get() == before)

print("\ntest_a_member_reads_and_cannot_change")
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-medspa.com", name="Sam",
                                                          role="member")["id"]))
page = m.get("/inbox/channels").get_data(as_text=True)
ok("a member sees every channel and no control", all(f'id="ch-{r["channel"]}"' in page for r in answering.get())
   and 'name="mode"' not in page and "<select" not in page, page[:300])
r = m.post("/inbox/channels", data={"channel": "instagram", "mode": "off"}, headers=JSON)
ok("...and can't change one", r.status_code == 403 and row("instagram")["mode"] == "auto", r.status_code)

print("\ntest_channels_is_the_one_home")
page = o.get("/inbox/sending").get_data(as_text=True)
ok("Sending has no auto-reply control, and links to Channels",
   "auto_instagram" not in page and "auto_messenger" not in page and 'href="/inbox/channels"' in page)
r = o.post("/inbox/sending", data={"first_message": "off", "text": "", "hourly_cap": "40"})
ok("...and saving it leaves Auto-reply as Channels set it", r.status_code == 303
   and row("instagram")["mode"] == "auto", row("instagram"))
page = o.get("/inbox/reply-style").get_data(as_text=True)
ok("Reply Style explains the levels, has no control, and links to Channels",
   "<select" not in page and 'href="/inbox/channels"' in page and "Strong sales" in page)
ok("...and changes nothing (there is no form to post)", o.post("/inbox/reply-style",
                                                              data={"dms": "service"}).status_code == 405)

print("\nALL CHANNEL-CARD CHECKS PASS" if not FAILS else f"\n{len(FAILS)} CHANNEL-CARD CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
