"""Your business, in your own words, has a field: the first card on Your Business (owner, 2026-10-08).

Owner, 2026-10-08: "one big open field would probably be the best rather than a bunch of little entries", and yes to
the screen saying "Anything here may be said to a customer." The backend is core/business_context (#2059,
tests/test_your_business_in_your_own_words.py); this holds the field on core/dash/business.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * the field isn't the page's first card, or loses its limit, its warning or its count;
  * saving the field blanks any other answer, or the big form's Save touches the field;
  * line breaks are lost, or the owner's words aren't the owner's after saving;
  * the website's first draft, saved unchanged, becomes the owner's own;
  * text past the limit is cut short instead of refused, or what was typed vanishes on a refusal;
  * a member can change the words.

No network, no model.

Run: python tests/test_your_own_words_have_a_field.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "own-words.db")
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


def field(page: str) -> str:
    m = re.search(r'<textarea id="bz-words"[^>]*>(.*?)</textarea>', page, re.S)
    return html.unescape(m.group(1)) if m else None


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
bc.put("website", "https://glowmedspa.example", by="owner")
bc.put("name", "Glow Med Spa", by="owner")
DRAFT = "Glow Med Spa offers Botox and HydraFacials in South Austin.\nServices\n- Botox from $12 a unit"
bc.put("description", DRAFT, by="box")
box_settings.put(bc.NS, bc.WORDS_DRAFT, DRAFT, set_by="box")

print("test_the_field_is_the_first_card")
page = o.get(DOOR).get_data(as_text=True)
first = re.search(r'<div class="card[^"]*"[^>]*><h2>([^<]+)</h2>', page)
ok("the page's first card is the owner's own words", first and first.group(1) == "Your business, in your own words",
   first and first.group(1))
ok("one field, named description, up to the backend's limit, pre-filled with what the box holds",
   f'name="description" rows="10" maxlength="{bc.WORDS_MAX}"' in page and field(page) == DRAFT, field(page))
ok("...with the warning, the count, and its own Save that sends this field alone",
   re.search(r'<p class="bz-warn">[^<]+</p>', page) and f"of {bc.WORDS_MAX:,} characters" in page
   and '<input type="hidden" name="action" value="words">' in page and "Save your words" in page)
ok("still the box's first draft: the screen says so", "A first draft from your website." in page)

print("\ntest_saving_the_field")
r = o.post(DOOR, data={"action": "words", "description": DRAFT})
ok("saved unchanged, the first draft stays a draft, not the owner's words",
   r.status_code == 303 and bc.own_words() == "" and bc.words()["from"] == "your website", bc.words())
MINE = ("We help busy professionals look rested without downtime.\n\n"
        "For cold prospects: lead with the free consultation and our 5-star reviews.")
r = o.post(DOOR, data={"action": "words", "description": MINE})
ok("the owner's words are saved as written, line breaks kept, and are theirs",
   r.status_code == 303 and r.headers["Location"].endswith("?saved=words#bz-words-card") and bc.own_words() == MINE,
   bc.own_words())
ok("...and nothing else on the page changed", bc.get().get("website") == "https://glowmedspa.example"
   and bc.get().get("name") == "Glow Med Spa", bc.get())
page = o.get(DOOR + "?saved=words").get_data(as_text=True)
ok("...the card says it saved, and no longer calls it a draft", "<b>Saved.</b>" in page
   and "A first draft from your website." not in page and field(page) == MINE)

print("\ntest_past_the_limit")
TOO_LONG = "x" * (bc.WORDS_MAX + 1)
r = o.post(DOOR, data={"action": "words", "description": TOO_LONG})
page = r.get_data(as_text=True)
ok("past the limit it is refused with a sentence under the field, nothing saved, what was typed kept",
   r.status_code == 400 and re.search(r'<p class="bz-err">[^<]+</p>', page) and bc.own_words() == MINE
   and field(page) == TOO_LONG, (r.status_code, bc.own_words()[:40]))

print("\ntest_the_big_form_leaves_it_alone")
r = o.post(DOOR, data={"action": "save", "website": "https://glowmedspa.example", "name": "Glow Med Spa"})
ok("the rest of the page saves without touching the owner's words", r.status_code == 303 and bc.own_words() == MINE)

print("\ntest_a_member_reads_it")
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@glowmedspa.example", name="Sam", role="member")["id"]))
page = m.get(DOOR).get_data(as_text=True)
ok("a member reads the words, line breaks kept, with no field", 'id="bz-words"' not in page
   and "white-space:pre-wrap" in page and html.escape(MINE).split("\n")[0] in page)
r = m.post(DOOR, data={"action": "words", "description": "Cheapest Botox in town!"})
ok("...and can't change them", r.status_code == 403 and bc.own_words() == MINE)

print("\nALL OWN-WORDS FIELD CHECKS PASS" if not FAILS else f"\n{len(FAILS)} OWN-WORDS FIELD CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
