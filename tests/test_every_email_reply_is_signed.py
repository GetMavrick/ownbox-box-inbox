"""Every email reply the box drafts or sends ends with the buyer's signature (marketing/customer_voice/inbox/signature.py).

Owner, 2026-10-02: "I see that the machine is writing drafts and putting them in my drafts on Gmail and that's
great. But I always want to finish with a signature that includes a link to my website." Then: "Yes, perfect add
that open field please." Measured here:
  * the signature is added once, never twice, and a reply with no signature set is untouched
  * a drafted reply going into the buyer's Gmail Drafts carries it (Gmail adds none to a draft placed over IMAP)
  * an email reply sent from the box carries it; an Instagram message does not
  * Inbox Settings has the open field: the owner saves it, a member can read it but not change it
  * the reply box says what will be added before Send, or where to add one

Run: python tests/test_every_email_reply_is_signed.py
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "signature.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import box_secrets, dash  # noqa: E402
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app as inbox_app  # noqa: E402
from marketing.customer_voice.inbox import email_channel as ec  # noqa: E402
from marketing.customer_voice.inbox import mailbox_drafts as md  # noqa: E402
from marketing.customer_voice.inbox import reply, signature, store, window  # noqa: E402

FAILS: list[str] = []
SPACE = inbox_app._space()
SIG = "A. Owner\nFounder\nwww.example-business.com"


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


print("\nThe signature itself\n")
ok("with no signature set, a reply is untouched", signature.apply(SPACE, "Thanks, see you Friday.")
   == "Thanks, see you Friday.")
signature.put(SPACE, "  " + SIG + "  \n\n\n")
ok("it is stored clean (no stray spaces or blank runs)", signature.get(SPACE) == SIG, repr(signature.get(SPACE)))
signed = signature.apply(SPACE, "Thanks, see you Friday.")
ok("it is added at the end, after a blank row", signed == "Thanks, see you Friday.\n\n" + SIG, repr(signed))
ok("never twice: an already-signed reply is left as it is", signature.apply(SPACE, signed) == signed)
ok("...even when its spacing differs", signature.apply(SPACE, "Hi\n\nA. Owner \nFounder\n www.example-business.com")
   .count("Founder") == 1)
for bad, why in (("x" * (signature.MAX_CHARS + 1), "characters"), ("\n".join("r" * 9), "rows")):
    try:
        signature.put(SPACE, bad)
        refused = ""
    except ValueError as e:
        refused = str(e)
    ok(f"a signature too long is refused in words ({why})", why in refused, refused)
ok("...and the saved one is unchanged", signature.get(SPACE) == SIG)

print("\nIn the buyer's Gmail Drafts\n")
appended = []
_saved = (md.waiting, md._claim, box_secrets.email_credential, ec._connect, ec.append_draft, md.enabled)
md.enabled = lambda: True
md.waiting = lambda space, limit=5: [{"id": "d1", "zcid": "z1", "in_reply_to": "<m1@x>", "body": "Happy to help."}]
md._claim = lambda space, draft_id: True
box_secrets.email_credential = lambda: {"user": "owner@example-business.com", "password": "x"}


class _Conn:
    def logout(self):
        pass

    def close(self):
        pass


ec._connect = lambda cred: _Conn()
ec.append_draft = lambda **kw: appended.append(kw["body"]) or True
out = md.sweep(SPACE)
ok("a drafted reply lands in Gmail Drafts signed", appended == ["Happy to help.\n\n" + SIG], (out, appended))
md.waiting, md._claim, box_secrets.email_credential, ec._connect, ec.append_draft, md.enabled = _saved

print("\nSent from the box\n")
delivered = []
_get, _lane, _deliver = store.get_conversation, window.no_send_lane_why, reply._deliver
window.no_send_lane_why = lambda platform: None
reply._deliver = lambda **kw: delivered.append((kw["conv"]["platform"], kw["text"])) or "mid-1"
for platform in ("email", "instagram"):
    store.get_conversation = lambda space, zcid, p=platform: {"zernio_conversation_id": zcid, "platform": p,
                                                               "account_id": "acct", "opted_out": 0}
    try:
        reply.send_reply(space=SPACE, zcid=f"z-{platform}", text="On my way.", user_id="usr_1",
                         nonce=reply.new_nonce())
    except Exception as e:                       # noqa: BLE001 — the record below says what happened
        delivered.append((platform, f"raised {type(e).__name__}: {e}"))
got = dict(delivered)
ok("an email reply sent from the box is signed", got.get("email") == "On my way.\n\n" + SIG, got)
ok("an Instagram message is not", got.get("instagram") == "On my way.", got)
store.get_conversation, window.no_send_lane_why, reply._deliver = _get, _lane, _deliver

print("\nThe open field in Inbox Settings\n")
c = app.test_client()
c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
page = c.get("/inbox/signature").get_data(as_text=True)
ok("the owner sees the field, filled with the signature", 'name="signature"' in page and "Founder" in page, page[:300])
ok("...at 16px or larger, so a mobile doesn't zoom", "max(16px" in page)
r = c.post("/inbox/signature", data={"signature": "B. Owner\nwww.example-business.com"})
ok("saving it works and comes back saved", r.status_code == 303 and signature.get(SPACE)
   == "B. Owner\nwww.example-business.com", (r.status_code, signature.get(SPACE)))
r = c.post("/inbox/signature", data={"signature": "y" * (signature.MAX_CHARS + 1)})
ok("too long is refused on the page, in words", r.status_code == 200 and "characters" in r.get_data(as_text=True))
ok("the settings menu lists it", "/inbox/signature" in c.get("/inbox/settings").get_data(as_text=True))
m = app.test_client()
m.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@example-business.com", name="Sam", role="member")["id"]))
ok("a member can read it", "B. Owner" in m.get("/inbox/signature").get_data(as_text=True))
r = m.post("/inbox/signature", data={"signature": "hijacked"})
ok("...but not change it", r.status_code in (302, 303, 403) and signature.get(SPACE).startswith("B. Owner"),
   (r.status_code, signature.get(SPACE)))

print("\nThe reply box says it first\n")
note = inbox_app._signed_note({"platform": "email"})
ok("under an email reply: what goes at the end", "Your signature goes at the end" in note and "B. Owner" in note,
   note)
ok("not under an Instagram reply", inbox_app._signed_note({"platform": "instagram"}) == "")
signature.put(SPACE, "")
ok("cleared, the reply box says where to add one", "/inbox/signature" in inbox_app._signed_note({"platform": "email"}))
ok("...and replies go unsigned again", signature.apply(SPACE, "Hi") == "Hi")

print("\nFrom the owner's AI, on one tap\n")
from core import approvals  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402

asked = inbox_tools.propose_signature(text="C. Owner\nwww.example-business.com", seat={"label": "Claude"})
ok("the AI can ask to set it, and nothing changes yet", asked.get("asked") is True and signature.get(SPACE) == "",
   asked)
approvals.decide(asked["approval"], True, by="owner@example-business.com")
ok("approved, it is the signature", signature.get(SPACE) == "C. Owner\nwww.example-business.com", signature.get(SPACE))
too_long = inbox_tools.propose_signature(text="z" * (signature.MAX_CHARS + 1), seat={"label": "Claude"})
ok("a signature too long is refused before anyone is asked", too_long.get("asked") is False
   and "characters" in str(too_long.get("error")), too_long)

print("\nALL SIGNATURE CHECKS PASS" if not FAILS else f"\n{len(FAILS)} SIGNATURE CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
