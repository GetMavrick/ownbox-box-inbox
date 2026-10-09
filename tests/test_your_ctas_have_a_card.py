"""Your CTAs have a card: right under your own words on Your Business, one field, one CTA a line (owner, 2026-10-09).

Owner, 2026-10-09, to OSDev4: "another field called CTAs ... these will be placed at the end of email drafts to drive
traffic and Leeds. If you don't want a CTA leave this box empty." The store and the drafter are #2078's
(core/business_context ctas, tests/test_your_ctas.py); this holds the card on core/dash/business.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the card stops sitting directly under your own words, or loses its field, its help or its own Save;
  * saving it blanks any other answer, or the big form's Save or the words' Save touches it;
  * one CTA too many, or one too long, is cut instead of refused, or what was typed vanishes on the refusal;
  * an empty field doesn't clear them;
  * the Customer service note shows when email isn't set to Customer service, or hides when it is;
  * a member can change them, or can't read them.

No network, no model.

Run: python tests/test_your_ctas_have_a_card.py
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ctas-card.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "dash-pw"

from core import box_settings, dash, state  # noqa: E402
from core import business_context as bc  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402

DOOR = "/settings/business"
FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def field(page: str):
    m = re.search(r'<textarea id="bz-ctas"[^>]*>(.*?)</textarea>', page, re.S)
    return html.unescape(m.group(1)) if m else None


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
bc.put("website", "https://glowmedspa.example", by="owner")
bc.put("name", "Glow Med Spa", by="owner")
bc.put("description", "Glow Med Spa offers Botox and HydraFacials in South Austin.", by="owner")

print("test_the_card_sits_under_your_own_words")
page = o.get(DOOR).get_data(as_text=True)
cards = re.findall(r'<div class="card[^"]*" id="([^"]+)"', page)
ok("the CTAs card comes directly after your own words", cards[:2] == ["bz-words-card", "bz-ctas-card"], cards)
ok("...one field named ctas, about four rows, empty to begin with, with its help and its own Save",
   'name="ctas" rows="4"' in page and field(page) == "" and 'id="bz-ctas-help"' in page
   and '<input type="hidden" name="action" value="ctas">' in page and "Save CTAs</button>" in page, field(page))
ok("...and with no Customer service note while email isn't set to it", "so no CTA is added" not in page)
# THE EXAMPLE IS IN THE BOX (owner, 2026-10-09, on #2079: "just put that example inside the box"), and the help keeps
# saying how many and how ("It already explains they can write up to 3. 1 per line.").
ok("the example sits in the box as its placeholder, word for word, and nowhere under the help",
   'placeholder="' + html.escape("Everyone's busier than ever, so we made booking take thirty seconds: "
                                 "yourbusiness.com/book. Would love to hear what's on your mind.") + '"' in page
   and "Example:" not in page and "bz-ex" not in page)
ok("...and the help still says up to 3, one per line", "Write up to 3, one per line." in page)

print("\ntest_saving_them")
CTAS = ("Everyone's busier than ever, so we made booking take thirty seconds: glowmedspa.example/book.\n"
        "New here? Your first consultation is free: glowmedspa.example/consult.")
r = o.post(DOOR, data={"action": "ctas", "ctas": CTAS})
ok("saved as written, one a line, and back to the card", r.status_code == 303
   and r.headers["Location"].endswith("?saved=ctas#bz-ctas-card") and bc.ctas() == CTAS.split("\n"), bc.ctas())
ok("...and nothing else on the page changed", bc.get().get("name") == "Glow Med Spa"
   and bc.get().get("description").startswith("Glow Med Spa offers"), bc.get())
page = o.get(DOOR + "?saved=ctas").get_data(as_text=True)
ok("...the card says it saved and what that means, and shows them", "<b>Saved.</b>" in page
   and "end with one of these 2" in page and field(page) == CTAS)

print("\ntest_too_many_or_too_long")
FOUR = CTAS + "\nThird: gift cards at glowmedspa.example/gift.\nFourth: follow us."
r = o.post(DOOR, data={"action": "ctas", "ctas": FOUR})
page = r.get_data(as_text=True)
ok("four are refused with the store's sentence under the field, nothing saved, what was typed kept",
   r.status_code == 400 and re.search(r'<p class="bz-err">[^<]*Write up to 3[^<]*</p>', html.unescape(page)) is not None
   and bc.ctas() == CTAS.split("\n") and field(page) == FOUR, (r.status_code, bc.ctas()))
LONG = "x" * (bc.CTA_CHARS + 1)
r = o.post(DOOR, data={"action": "ctas", "ctas": LONG})
ok("one too long is refused, never cut", r.status_code == 400 and bc.ctas() == CTAS.split("\n")
   and field(r.get_data(as_text=True)) == LONG)

print("\ntest_the_other_saves_leave_them_alone")
r = o.post(DOOR, data={"action": "save", "website": "https://glowmedspa.example", "name": "Glow Med Spa"})
ok("the big form's Save leaves the CTAs alone", r.status_code == 303 and bc.ctas() == CTAS.split("\n"))
r = o.post(DOOR, data={"action": "words", "description": "We help busy professionals look rested."})
ok("...and so does the words' Save", r.status_code == 303 and bc.ctas() == CTAS.split("\n"))

print("\ntest_customer_service_says_why")
box_settings.put("inbox", "reply_style.email", "service", set_by="owner")
page = o.get(DOOR).get_data(as_text=True)
ok("email set to Customer service: the note says no CTA is added, and links to Channels",
   "set to Customer service, so no CTA is added" in page and '<a href="/inbox/channels">' in page)
page = o.get(DOOR + "?saved=ctas").get_data(as_text=True)
ok("...and a save there promises no ending", "<b>Saved.</b></p>" in page and "end with one of these" not in page)
box_settings.put("inbox", "reply_style.email", "subtle", set_by="owner")
ok("...gone again in a selling style", "so no CTA is added" not in o.get(DOOR).get_data(as_text=True))

print("\ntest_empty_clears_them")
r = o.post(DOOR, data={"action": "ctas", "ctas": "  \n  "})
ok("an empty field clears them, and says no CTA will be added", r.status_code == 303 and bc.ctas() == []
   and "No CTA will be added." in o.get(DOOR + "?saved=ctas").get_data(as_text=True))
bc.put("ctas", CTAS, by="owner")

print("\ntest_a_member_reads_them")
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.example", name="Sam", role="member")["id"]))
page = m.get(DOOR).get_data(as_text=True)
ok("a member reads the CTAs, one a line, with no field", 'id="bz-ctas"' not in page and "CTAs:" in page
   and html.escape(CTAS.split("\n")[1]) in page)
r = m.post(DOOR, data={"action": "ctas", "ctas": "Cheapest Botox in town!"})
ok("...and can't change them", r.status_code == 403 and bc.ctas() == CTAS.split("\n"))

print("\nALL CTA CARD CHECKS PASS" if not FAILS else f"\n{len(FAILS)} CTA CARD CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
