"""Saved replies (snippets, #1821): a dropdown above every reply box, picked, read, then sent with Send.

Owner, 2026-10-02: "a list of snippets where on each message you could pull a drop-down and see all of them and
select them and then have a send button where that snippet could be sent." His four picks: replies only; he edits the
list and members use it; a pick FILLS the box and the person presses Send; {first_name} and {my_name}. On 2026-10-03:
"Yes, proceed and do numbers one and then two. Finish them." Measured here:
  * the list: add, edit, archive, most used first, bounded, words refused in words, one Space never sees another's
  * fill-ins: a first name when there is one, "there" when there isn't, the sender's own first name
  * the dropdown: its own GET form above the reply box (picking can never send), filled words ready to read, a
    picked one lands in the box with no script, and Undo is offered
  * a send that started from a snippet counts one use; a refused send counts none
  * Inbox Settings, Saved Replies: the owner edits, a member reads, a member's change is refused
  * the owner's AI can read the list and propose one, which lands only on his tap

Run: python tests/test_saved_replies.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "snippets.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import approvals, dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.inbox import reply as reply_mod  # noqa: E402
from marketing.customer_voice.inbox import snippets  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


print("\nThe list\n")
a = snippets.add(SPACE, "Pricing", "Hi {first_name}, our pricing is at https://example.com/pricing. {my_name}")
b = snippets.add(SPACE, "  Free   guide ", "Hi {first_name}, here is the guide: https://example.com/guide")
ok("a snippet is saved with its name tidied", b["title"] == "Free guide", b)
snippets.used(SPACE, b["id"])
ok("most used first", [r["title"] for r in snippets.all_for(SPACE)] == ["Free guide", "Pricing"])
for title, body, why in (("", "x", "name"), ("x" * 61, "x", "60"), ("Ok", "", "words"), ("Ok", "y" * 1801, "1,800")):
    try:
        snippets.add(SPACE, title, body)
        said = ""
    except snippets.SnippetRefused as e:
        said = str(e)
    ok(f"refused in words when {why!r} is wrong", why in said, said)
snippets.edit(SPACE, a["id"], "Pricing", "Hi {first_name}, pricing: https://example.com/p")
ok("an edit keeps its place and changes its words", snippets.get(SPACE, a["id"])["body"].endswith("/p"))
ok("another Space never sees this one", snippets.all_for("someone-else") == []
   and snippets.get("someone-else", a["id"]) is None)
c = snippets.add(SPACE, "Old", "Old words")
snippets.archive(SPACE, c["id"])
ok("archived leaves the list, and is kept", "Old" not in [r["title"] for r in snippets.all_for(SPACE)]
   and [r["title"] for r in snippets.all_for(SPACE, archived=True)] == ["Old"])

print("\nFill-ins\n")
ok("{first_name} is the first name", snippets.fill("Hi {first_name}", participant="Dana Smith") == "Hi Dana")
ok("...and 'there' with no name a person would use", snippets.fill("Hi {first_name}", participant="dana@x.com")
   == "Hi there" and snippets.fill("Hi {first_name}", participant="") == "Hi there")
ok("{my_name} is the sender's first name", snippets.fill("— {my_name}", my_name="Alex Owner") == "— Alex")

print("\nThe dropdown above the reply box\n")
owner_token = dash.new_session(state.owner_user()["id"])
state_user = state.owner_user()
conv = {"zernio_conversation_id": "z1", "participant": "Dana Smith", "platform": "email"}
with app.test_request_context("/inbox/inbox/z1", headers={"Cookie": f"{dash.COOKIE}={owner_token}"}):
    html, picked, pid = inbox_app._snippet_picker("z1", conv)
ok("it lists every saved reply by name", 'id="snip-pick"' in html and ">Free guide<" in html and ">Pricing<" in html)
ok("...each already filled in for this person", 'Hi Dana, here is the guide' in html, html[:400])
ok("its own GET form, so picking can never send", 'method="get"' in html and "/reply" not in html.split("</form>")[0])
ok("full width, 48px, 16px or larger on a mobile", "min-height:48px" in html and "max(16px" in html)
ok("Undo is offered, to put back what was in the box", 'id="snip-undo"' in html)
ok("nothing is picked until someone picks", picked is None and pid == "")
with app.test_request_context(f"/inbox/inbox/z1?snippet={a['id']}",
                              headers={"Cookie": f"{dash.COOKIE}={owner_token}"}):
    html, picked, pid = inbox_app._snippet_picker("z1", conv)
ok("without a script, Use puts the filled words in the box", picked == "Hi Dana, pricing: https://example.com/p"
   and pid == a["id"], (picked, pid))
with app.test_request_context("/inbox/inbox/z1?snippet=snp_nothere", headers={"Cookie": f"{dash.COOKIE}={owner_token}"}):
    _, picked, pid = inbox_app._snippet_picker("z1", conv)
ok("an id that isn't on this box picks nothing", picked is None and pid == "")

print("\nSent from a snippet\n")
c1 = app.test_client()
c1.set_cookie(dash.COOKIE, owner_token)
sent = []
_send = reply_mod.send_reply
reply_mod.send_reply = lambda **kw: sent.append(kw) or {"status": "sent", "message_id": "m1"}
before = snippets.get(SPACE, a["id"])["uses"]
r = c1.post("/inbox/inbox/z1/reply", data={"text": "Hi Dana, pricing: https://example.com/p", "n": "nonce-1",
                                           "snippet": a["id"]})
ok("the send goes through the one send path, with the words in the box", sent and sent[-1]["text"]
   == "Hi Dana, pricing: https://example.com/p", (r.status_code, sent[-1:] if sent else None))
ok("...and counts one use", snippets.get(SPACE, a["id"])["uses"] == before + 1)


def refused(**kw):
    raise reply_mod.ReplyRefused("this person has opted out — nothing is sent to them")


reply_mod.send_reply = refused
c1.post("/inbox/inbox/z1/reply", data={"text": "Hi", "n": "nonce-2", "snippet": a["id"]})
ok("a refused send (opted out) counts none", snippets.get(SPACE, a["id"])["uses"] == before + 1)
reply_mod.send_reply = _send

print("\nInbox Settings, Saved Replies\n")
page = c1.get("/inbox/snippets").get_data(as_text=True)
ok("the owner sees the list and the add form", "Pricing" in page and 'value="add"' in page, page[:300])
r = c1.post("/inbox/snippets", data={"act": "add", "title": "Book a demo", "body": "Hi {first_name}, book here."})
ok("adding one works", r.status_code == 303 and "Book a demo" in [x["title"] for x in snippets.all_for(SPACE)])
r = c1.post("/inbox/snippets", data={"act": "add", "title": "", "body": "x"})
ok("a refused one says why on the page, and keeps what was typed", r.status_code == 200
   and "Give the snippet a name" in r.get_data(as_text=True))
demo = next(x for x in snippets.all_for(SPACE) if x["title"] == "Book a demo")
c1.post("/inbox/snippets", data={"act": "save", "id": demo["id"], "title": "Book a demo", "body": "Book: https://x.co"})
ok("editing one works", snippets.get(SPACE, demo["id"])["body"] == "Book: https://x.co")
c1.post("/inbox/snippets", data={"act": "archive", "id": demo["id"]})
ok("archiving one takes it out of the dropdown", snippets.get(SPACE, demo["id"]) is None)
ok("the settings menu lists it", "/inbox/snippets" in c1.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-business.com", name="Sam", role="member")["id"]))
ok("a member reads the list", "Pricing" in m.get("/inbox/snippets").get_data(as_text=True))
r = m.post("/inbox/snippets", data={"act": "add", "title": "Mine", "body": "x"})
ok("...but can't change it", "Mine" not in [x["title"] for x in snippets.all_for(SPACE)], r.status_code)

print("\nFrom the owner's AI\n")
got = inbox_tools.saved_replies()
ok("the AI can read the list, a tie broken by the most recent use", [x["name"] for x in got["saved_replies"]][:2] == ["Pricing", "Free guide"], got)
ok("...in words", "saved replies, most used first" in inbox_tools._render_saved(got))
asked = inbox_tools.propose_saved_reply(name="Partner offer", words="Hi {first_name}, see https://example.com",
                                        seat={"label": "Claude"})
ok("it can propose one, and nothing changes yet", asked.get("asked") is True
   and "Partner offer" not in [x["title"] for x in snippets.all_for(SPACE)], asked)
approvals.decide(asked["approval"], True, by="owner@example-business.com")
ok("approved, it is in the list", "Partner offer" in [x["title"] for x in snippets.all_for(SPACE)])
bad = inbox_tools.propose_saved_reply(name="", words="x", seat={"label": "Claude"})
ok("a bad one is refused before anyone is asked", bad.get("asked") is False and "name" in str(bad.get("error")))

print("\nALL SAVED REPLY CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SAVED REPLY CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
