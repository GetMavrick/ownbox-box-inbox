"""The Morning Review's "Who to answer first" names people who want an answer, newest first (OSDev1, 2026-10-05).

Read on the owner's box on 10-05 (release 05.2), its three names were the owner's own onboarding notice
(brian@mail.mavrick.pro), a cold sales pitch and a funding pitch, all 27 days old: oldest first surfaced the junk.
OSDev1's assignment: "Skip the box's own addresses and domains, automated senders and anything the inbox already treats
as a pitch or newsletter; prefer real customers, ads first, then the newest first-time writers. Test with exactly those
three shapes."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * mail from the box's own sending domain (a subdomain the mailbox check never knew) is named;
  * a conversation the box already judged a cold pitch, or as needing no reply, is named;
  * a sender nobody judged yet, that the shared classifier calls a robot by its address, is named;
  * a customer is hidden because they write from a free provider the owner also uses (gmail.com), or from a placeholder
    domain a settings file mentions;
  * the order is not: from an ad, then first-time writers, then the rest, each newest first.

No network, no model.

Run: python tests/test_who_to_answer_first_is_a_person.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "first_person.db")
os.environ["DASH_TOKEN"] = "pw"
# The box sends as a subdomain its mailbox never reads; its owner signs in with a free address; a settings file carries
# a placeholder. Each is read the way the box reads its own settings.
os.environ["GTM_FROM_EMAIL"] = "Brian MacDonald <brian@mail.mavrick.pro>"
os.environ["OPERATOR_EMAIL"] = "owner.personal@gmail.com"
os.environ["SUPPORT_EMAIL"] = "you@example.com"

from core import state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402,F401  (applies every machine's schema)
from marketing.customer_voice import report as cv_report  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:500]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


now = datetime.now(timezone.utc)
SP = cv_report._space_name()


def thread(z, who, sender, days, *, ad=False, answered=False, said="Hello"):
    at = (now - timedelta(days=days)).isoformat()
    store.upsert_conversation(space=SP, zcid=z, platform="email", participant=who, account_id="me@yourbusiness.com",
                              last_inbound_at=at, ad_meta_id="ad-1" if ad else None)
    with state.connect() as c:
        if answered:
            c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                      "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (f"o-{z}", SP, z, f"om-{z}", "out", "human", "Thanks!", (now - timedelta(days=60)).isoformat()))
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction, "
                  "sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (f"i-{z}", SP, z, f"m-{z}", "in", sender, said, at))
    return f"m-{z}"


# THE THREE SHAPES FROM 10-05, all 27 days old, so oldest-first would name exactly them.
thread("z-notice", "Brian MacDonald", "Brian MacDonald <brian@mail.mavrick.pro>", 27,
       said="Welcome to your Ownbox: here is what happens next")
thread("z-team", "Ownbox Team", "Ownbox Team <team@mail.mavrick.pro>", 25, said="Your box is ready")
for z in ("z-notice", "z-team"):                 # judged a person on its headers, which is how it reached the list
    store.mark_automated(SP, z, 0)
mid = thread("z-sales", "Growth Partners", "Jake <jake@growthpartners.io>", 27,
             said="We help agencies like yours book 30 more calls a month")
drafts.mark_pitch_back(SP, mid)
mid = thread("z-fund", "Capital Bridge", "Ana <ana@capitalbridge.vc>", 27, said="Are you raising? We invest in AI")
drafts.put(space=SP, zcid="z-fund", in_reply_to=mid, body=drafts.NO_REPLY_BODY)
drafts.dismiss(SP, drafts.for_inbound(SP, mid)["id"])
# A robot nobody judged yet (automated stays NULL), known by its address alone.
thread("z-robot", "Stripe", "Stripe <no-reply@stripe.com>", 26, said="Your receipt")
# REAL PEOPLE: a customer from a free provider the owner also uses, one from a placeholder domain, an ad lead, and a
# returning customer.
thread("z-gmail", "Dana Whitfield", "Dana <dana.whitfield@gmail.com>", 2, said="Do you have Saturday openings?")
thread("z-example", "Sam Lee", "Sam <sam@example.com>", 5, said="Quote for a deck?")
thread("z-ad", "Marcus Cole", "Marcus <marcus@colehomes.com>", 10, ad=True, said="Saw your ad, how much?")
thread("z-back", "Priya Raman", "Priya <priya@raman.co>", 1, answered=True, said="One more question")

print("test_who_to_answer_first_is_a_person")
rows = store.list_conversations(SP, limit=500, waiting=True)
ok("all nine are waiting by the inbox's own count (the filter is the review's, not the list's)",
   len(rows) == 9, [r["zernio_conversation_id"] for r in rows])
kept = {r["zernio_conversation_id"] for r in cv_report.real_people(SP, rows)}
ok("the box's own onboarding notice, from a sending subdomain its mailbox never reads, is left out",
   "z-notice" not in kept, sorted(kept))
ok("...and so is any other address on the domain the box sends from", "z-team" not in kept, sorted(kept))
ok("a cold sales pitch the box already turned around is left out", "z-sales" not in kept, sorted(kept))
ok("a funding pitch the box already judged needs no reply is left out", "z-fund" not in kept, sorted(kept))
ok("a robot nobody judged yet, known by its address, is left out", "z-robot" not in kept, sorted(kept))
ok("a customer on gmail.com is kept though the owner signs in with gmail.com", "z-gmail" in kept, sorted(kept))
ok("a customer on a domain a settings placeholder mentions is kept", "z-example" in kept, sorted(kept))

first = cv_report.report(date.today(), SP).get("answer_first") or []
# SPEED TO LEAD (owner, 2026-10-06): Dana asked to book two days ago; Marcus came from an ad ten days ago and asked a
# price; Sam asked for a quote five days ago; Priya, an answered customer, has a question with nothing to win.
ok("named by speed to lead: a fresh booking, the ad lead, the quote; the returning customer waits behind them",
   [f["text"] for f in first] == ["Dana Whitfield", "Marcus Cole", "Sam Lee"], [f["text"] for f in first])
ok("none of the three shapes from 10-05 is named",
   not {"Brian MacDonald", "Ownbox Team", "Growth Partners", "Capital Bridge", "Stripe"} & {f["text"] for f in first})

own, domains = cv_report.own_senders()
ok("the box's own senders include its sending address, its settings' and its people's",
   {"brian@mail.mavrick.pro", "owner.personal@gmail.com"} <= own, sorted(own))
ok("...but a free provider or a placeholder is never one of its domains",
   "gmail.com" not in domains and "example.com" not in domains and "mail.mavrick.pro" in domains, sorted(domains))

print("\nALL WHO-TO-ANSWER-FIRST CHECKS PASS" if not _failed else f"\n{_failed} WHO-TO-ANSWER-FIRST CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
