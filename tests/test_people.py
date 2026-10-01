"""One record per person (core/people.py, docs/SCOPE_ONE_PERSON_RECORD.md phase 1).

  · a person is created the first time any machine meets them, and the same id always finds the same person;
  · emails and Instagram handles are case-blind; a bad id is refused, never raised;
  · names never join people; a join needs evidence, keeps the earlier-met person, and is undone exactly;
  · spaces never mix; joins chain; a machine telling the same thing twice records it once;
  · THE PHASE 1 DONE-WHEN: one person's Instagram DM, the email they typed in it, and their email thread show
    as one timeline, with every id shown plainly (owner: "No need to mask").

Run: python tests/test_people.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")

from core import people, state  # noqa: E402

state.init_db()
FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


S, OTHER = "glow-med-spa", "fitlab-gym"


def at(minute):
    return f"2026-10-01T09:{minute:02d}:00+00:00"


print("\nmeeting people —")
p1 = people.identify(S, "email", "Ava@Example.com", machine="customer_voice", name="Ava Reyes", at=at(1))
ok("the first time a machine meets someone, they become a person", bool(p1) and p1.startswith("p_"), str(p1))
ok("the same email, in any case, always finds the same person",
   people.identify(S, "email", " ava@example.COM ", machine="lead_machine", at=at(2)) == p1
   and people.find(S, "email", "AVA@example.com") == p1)
ok("an Instagram handle is case-blind and ignores the @",
   people.identify(S, "instagram", "@GlowAva", machine="customer_voice", at=at(3))
   == people.find(S, "instagram", "glowava"))
for kind, bad in (("email", "not-an-email"), ("phone", "12"), ("instagram", "has space"), ("fax", "123"),
                  ("email", None)):
    ok(f"a bad id ({kind} {bad!r}) is refused, never raised", people.identify(S, kind, bad, machine="x") is None)
ok("a call with no machine is refused", people.identify(S, "email", "z@example.com", machine="") is None)

print("\nnames never join people —")
ana = people.identify(S, "email", "ana@other.com", machine="customer_voice", name="Ava Reyes", at=at(4))
ok("two people with the same name stay two people", ana and ana != p1)

print("\nspaces never mix —")
gym = people.identify(OTHER, "email", "ava@example.com", machine="customer_voice", at=at(5))
ok("the same email in another business on the box is another person", gym and gym != p1)

print("\njoining, on evidence —")
ig = people.find(S, "instagram", "glowava")
ok("no evidence, no join", people.link(S, ("instagram", "glowava"), ("email", "ava@example.com"),
                                       machine="customer_voice", evidence="  ") is None
   and people.find(S, "instagram", "glowava") != people.find(S, "email", "ava@example.com"))
kept = people.link(S, ("instagram", "glowava"), ("email", "ava@example.com"), machine="customer_voice",
                   evidence="email typed in DM message m_101", at=at(6))
ok("with evidence, the two ids are one person, the one met first", kept == p1
   and people.find(S, "instagram", "glowava") == p1, f"{kept} {p1} {ig}")
ok("joining them again changes nothing", people.link(S, ("instagram", "glowava"), ("email", "ava@example.com"),
                                                     machine="customer_voice", evidence="again") == p1)
rec = people.person(p1)
ok("the person shows every way we know them, plainly", rec["name"] == "Ava Reyes"
   and {(i["kind"], i["value"]) for i in rec["ids"]} == {("email", "ava@example.com"), ("instagram", "glowava")},
   str(rec))
ok("...and asking by the joined id gives the same record", people.person(ig) == rec)

print("\nwhat happened —")
ok("an event is recorded", people.touch(S, ig, machine="customer_voice", kind="sent_dm", ref="m_101", at=at(3)))
ok("the same event twice is recorded once",
   not people.touch(S, p1, machine="customer_voice", kind="sent_dm", ref="m_101", at=at(3)))
ok("a bad event is refused, never raised", not people.touch(S, p1, machine="customer_voice", kind="Sent DM!"))

print("\nundoing a wrong join —")
p3 = people.identify(S, "phone", "+1 (310) 555-0199", machine="customer_voice", at=at(7))
people.touch(S, p3, machine="customer_voice", kind="called", ref="c_1", at=at(7))
people.link(S, ("phone", "+13105550199"), ("email", "ava@example.com"), machine="customer_voice",
            evidence="a wrong guess", at=at(8))
with state.connect() as c:
    wrong = c.execute("SELECT id FROM person_links WHERE evidence='a wrong guess'").fetchone()["id"]
ok("before the undo the phone is the same person", people.find(S, "phone", "+13105550199") == p1)
ok("undo puts it back exactly: the phone is its own person again, with its own history",
   people.undo_link(wrong, by="owner") and people.find(S, "phone", "+13105550199") == p3
   and [e["kind"] for e in people.timeline(p3)] == ["called"]
   and all(e["kind"] != "called" for e in people.timeline(p1)))
ok("an undone join can't be undone twice", not people.undo_link(wrong, by="owner"))

print("\njoins chain —")
w = people.identify(S, "web", "ph_visitor_42", machine="aeo_machine", at=at(0))
people.touch(S, w, machine="aeo_machine", kind="read_article", ref="/blog/microneedling-aftercare", at=at(0))
people.link(S, ("web", "ph_visitor_42"), ("instagram", "glowava"), machine="aeo_machine",
            evidence="opened link sent in DM m_102", at=at(9))
ok("a web visitor joined to the DM sender is the same person as the email too",
   people.find(S, "web", "ph_visitor_42") == people.find(S, "email", "ava@example.com"))

print("\nTHE PHASE 1 DONE-WHEN: one timeline —")
people.touch(S, people.find(S, "email", "ava@example.com"), machine="customer_voice", kind="emailed",
             ref="gmail_thread_t9", at=at(10))
tl = people.timeline(people.find(S, "email", "ava@example.com"))
ok("her article read, DM and email thread show as one timeline, newest first",
   [(e["machine"], e["kind"]) for e in tl] == [("customer_voice", "emailed"), ("customer_voice", "sent_dm"),
                                               ("aeo_machine", "read_article")], str(tl))
ok("...and the first touch says where she came from", tl[-1]["ref"] == "/blog/microneedling-aftercare")
ok("the record is the earlier-met person: she was a web visitor first",
   people.find(S, "email", "ava@example.com") == w, f"{people.find(S, 'email', 'ava@example.com')} {w}")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
