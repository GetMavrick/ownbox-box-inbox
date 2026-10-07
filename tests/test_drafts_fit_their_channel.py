"""A draft fits the channel it came in on, and a photo with no words gets one too (owner, 2026-10-07).

Owner, 2026-10-07: "Drafts should fit the channel they come in on. Emails are in frequent and there's a finality to
them so they should just be like a call action, you're doing perfect on those. DM's are designed to continue the
conversation very chatty friendly way and also to do soft cells and to be of service and educate and provide links
and education." And, asked whether a photo or a shared post with no words should get a draft: "Yes, draft the photo
ones too". The queue side of that (wordless messages last, so they can never hold it up) is
tests/test_the_drafter_cannot_be_head_blocked.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * an email's instructions change (he called them perfect), or gain the DM's chatty shape;
  * a DM's instructions lose any part of his list: chatty and friendly, keep the conversation going, a soft sell,
    of service, teach, share links (never an invented one);
  * the DM shape stops overriding the two-or-three-sentence rule, or overrides the style the owner chose;
  * a message with no words is described as anything but what the platform said was sent, or the model is not told
    it cannot see it.

No network, no model.

Run: python tests/test_drafts_fit_their_channel.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "channel.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"

from core import box_settings, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401
from marketing.customer_voice.drafter import draft, store  # noqa: E402
from marketing.customer_voice.inbox import store as inbox_store  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


print("test_an_email_is_left_as_it_was")
E, D = draft._system("email"), draft._system("instagram")
ok("an email's instructions are the ones he called perfect: the base, nothing added for the channel",
   E == draft.SYSTEM and "Two or three sentences at most" in E, E[-300:])
ok("...and never the DM's shape", "DIRECT MESSAGE" not in E)

print("\ntest_a_dm_is_a_conversation")
ok("a DM carries its own shape, on Instagram and Messenger alike",
   draft.DM_SHAPE in D and draft.DM_SHAPE in draft._system("messenger"))
for part, words in (("chatty and friendly", "warm, friendly, casual"),
                    ("keeps the conversation going", "Keep the conversation going"),
                    ("of service", "Be of service"), ("teaches", "teach them something useful"),
                    ("shares links", "Share a helpful link"), ("never invents one", "Never invent a link"),
                    ("a soft sell", "A soft sell is welcome"), ("never a hard pitch", "Never a hard pitch")):
    ok(f"...{part}", words in D, D[-900:])
ok("...it replaces the two-or-three-sentence rule, said in so many words",
   "it replaces the two-or-three-sentence rule" in D)
box_settings.put("inbox", "reply_style.dms", "service", set_by="t")
S = draft._system("instagram")
ok("the owner's chosen style still decides how far it sells: the shape comes first, the style last",
   S.index(draft.DM_SHAPE) < S.index("CUSTOMER SERVICE") and "the style below allows it" in draft.DM_SHAPE)
box_settings.put("inbox", "reply_style.dms", "", set_by="t")
ok("an email and a DM are fingerprinted apart, so only DM drafts are rewritten in the new voice",
   draft.rules("email") != draft.rules("instagram"))

print("\ntest_a_photo_is_said_as_a_photo")
SP = "acme"


def media(zmid: str, atts) -> None:
    inbox_store.upsert_conversation(space=SP, zcid=f"z-{zmid}", participant="Ana", account_id="a1", platform="instagram")
    inbox_store.record_message(space=SP, zcid=f"z-{zmid}", zmid=zmid, direction="in", sent_by="contact", body="")
    if atts is not None:
        inbox_store.record_extras(zmid, {"attachments": atts})


media("m-photo", [{"type": "image", "url": "https://x/1.jpg", "name": "", "mimeType": "image/jpeg"}])
media("m-two", [{"type": "video", "url": "https://x/1.mp4"}, {"type": "share", "url": "https://x/p"}])
media("m-mime", [{"type": "file", "url": "https://x/a", "mimeType": "audio/mp4"}])
media("m-none", None)
ok("a photo is 'a photo'", store.what_was_sent(SP, "m-photo") == "a photo", store.what_was_sent(SP, "m-photo"))
ok("two things are both said", store.what_was_sent(SP, "m-two") == "a video and a shared post",
   store.what_was_sent(SP, "m-two"))
ok("a file the platform calls a file reads as one",
   store.what_was_sent(SP, "m-mime") == "a file", store.what_was_sent(SP, "m-mime"))
ok("never told: said honestly as something with no words",
   store.what_was_sent(SP, "m-none").startswith("something with no words"))
w = draft._words(SP, "m-photo", "")
ok("the model hears what was sent, that it cannot see it, and to carry the conversation on",
   "They sent a photo, with no words" in w and "never describe it" in w and "carry the conversation on" in w, w)
ok("words are passed as they are", draft._words(SP, "m-photo", "  Do you open Sunday?  ") == "Do you open Sunday?")

print("\nALL FIT-THE-CHANNEL CHECKS PASS" if not FAILS else f"\n{len(FAILS)} FIT-THE-CHANNEL CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
