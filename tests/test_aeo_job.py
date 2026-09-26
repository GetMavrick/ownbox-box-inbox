"""The writing job: planned topic in, live article out, and every other outcome written on the row.

NO NETWORK AND NO TOKENS. The writer, the publisher and the settings are scripted stand-ins, so
every assertion is about what the job did with them: which row it chose, what it recorded, and
what it refused to spend. The table is the REAL `seo_plan`, declared by the machine and reached
only through `plan.py`, the same path the Topics screen uses.
"""
import os, sys, tempfile
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "aeo_job.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from core import state

state.init_db()

from marketing.aeo_machine import guard, job, plan                       # noqa: E402
from marketing.aeo_machine import publisher as real_publisher            # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def reset(*rows):
    """Each row: (question, status='planned', published_at=None, updated_at=None, slug=None).
    Inserted through plan.add, then moved to its state the way the job would move it."""
    with state.connect() as c:
        c.execute("DELETE FROM seo_plan")
    ids = []
    for r in rows:
        q, status = r[0], (r[1] if len(r) > 1 else "planned")
        rid = plan.add(f"topic {len(ids) + 1}", q)
        with state.connect() as c:
            c.execute("UPDATE seo_plan SET status = ?, published_at = ?, updated_at = COALESCE(?, updated_at), "
                      "slug = ? WHERE id = ?",
                      (status, r[2] if len(r) > 2 else None, r[3] if len(r) > 3 else None,
                       r[4] if len(r) > 4 else None, rid))
        ids.append(rid)
    return ids


def row(i):
    return plan.get(i)


class Settings:
    def __init__(self, **over):
        self.s = {"weekly_cap": 4, "facts": ["The Base Machine is $499 once."]}
        self.s.update(over)
        self.l = {"never_words": ("call",), "never_phrases": (), "competitors": (),
                  "allowed_numbers": ("499",)}

    def get(self):
        return dict(self.s)

    def lists(self):
        return dict(self.l)

    def facts(self):
        return tuple(self.s.get("facts") or ())


class Writer:
    def __init__(self, refuse=None, touch_db=False):
        self.calls, self.refuse, self.touch_db = [], refuse, touch_db
        self.row_during_write = []

    def write(self, question, **kw):
        self.calls.append({"question": question, **kw})
        with state.connect() as c:
            r = c.execute("SELECT status, slug FROM seo_plan WHERE status = 'writing'").fetchone()
            self.row_during_write.append(dict(r) if r else None)
        if self.touch_db:
            # The Topics screen, mid-draft. Blocks (then errors) if the job still held a write lock.
            plan.add("someone else's topic", "Added while an article was being written?")
        if self.refuse:
            raise guard.GuardRefused([guard.Refusal("never_word", self.refuse,
                                                    "on this box's never-use list")])
        # The model's title drifts between attempts, and the writer derives its slug from it.
        return {"title": question, "slug": f"drafted-title-{len(self.calls)}"}


class Publisher:
    def __init__(self, configured=True, base="https://example.com", boom=None, ping_boom=None,
                 live=(), lookup_boom=None):
        self.configured, self.base, self.boom, self.ping_boom = configured, base, boom, ping_boom
        self.published, self.pinged, self.looked_up = [], [], []
        self.live, self.lookup_boom = set(live), lookup_boom        # slugs already on the site

    def find_by_slug(self, slug):
        self.looked_up.append(slug)
        if self.lookup_boom:
            raise self.lookup_boom
        return {"_id": f"doc-{slug}"} if slug in self.live else None

    def is_configured(self):
        return (True, "") if self.configured else (False, "no Sanity project set for this box")

    def publish(self, *, lists=None, **fields):
        if self.boom:
            raise self.boom
        self.published.append({"lists": lists, **fields})
        return {"doc_id": "d1", "slug": fields["slug"], "created": True,
                "url": f"{self.base}/articles/{fields['slug']}" if self.base else f"/articles/{fields['slug']}"}

    def address_refusals(self, slug, *, lists=None):
        # THE REAL CHECK: it is pure (guard only, no network), so faking it would test nothing.
        return real_publisher.address_refusals(slug, lists=lists or {})

    def ping_indexnow(self, urls):
        if self.ping_boom:
            raise self.ping_boom
        self.pinged.extend(urls)
        return True


def wire(settings=None, writer=None, publisher=None):
    job.settings = settings or Settings()
    job.writer = writer or Writer()
    job.publisher = publisher or Publisher()
    return job.settings, job.writer, job.publisher


now = datetime.now(timezone.utc)

print("-- the worker runs it --")
from core.worker import PERIODIC                                          # noqa: E402
tick = [p for p in PERIODIC if p["name"] == "aeo_publish"]
ok("importing the machine registers the writing job's tick", len(tick) == 1, [p["name"] for p in PERIODIC])
ok("…every minute, so 'Write and publish now' is honoured within one", tick and tick[0]["interval"] == 60.0)

print("\n-- a fresh box does nothing, and says why --")
(a,) = reset(("What is an AI business machine?",))
_, w, p = wire(publisher=Publisher(configured=False))
r = job.periodic()
ok("an unconfigured box skips with its reason", r.get("skipped") == "not_configured" and r.get("why"), r)
ok("…and spends nothing", w.calls == [], w.calls)
ok("…and leaves the row planned", row(a)["status"] == "planned", row(a))

print("\n-- the happy path --")
a, b = reset(("What is an AI business machine?",), ("Why do AI employees need a place to work?",))
s, w, p = wire()
r = job.periodic()
ok("one tick publishes one article", r["status"] == "published" and len(p.published) == 1, r)
ok("…the oldest planned topic first", w.calls[0]["question"] == "What is an AI business machine?", w.calls)
ok("…and leaves the next one for the next tick", row(b)["status"] == "planned", row(b))
done = row(a)
ok("the row records slug, url and when", done["status"] == "published" and done["slug"]
   and done["url"].startswith("https://example.com/articles/") and done["published_at"], done)
ok("the writer gets the box's facts", w.calls[0]["facts"] == ("The Base Machine is $499 once.",), w.calls[0])
ok("the writer and the publisher get the SAME lists, the box's own",
   w.calls[0]["lists"] == s.lists() and p.published[0]["lists"] == s.lists())
ok("the reasoning call is tagged to its row for the spend ledger", w.calls[0]["job_id"] == f"aeo:{a}")
ok("the live URL is pinged to IndexNow", p.pinged == [done["url"]], p.pinged)
wire()
job.periodic()
ok("the next tick takes the next topic", row(b)["status"] == "published", row(b))
_, w, _ = wire()
ok("with nothing planned, a tick spends nothing", job.periodic().get("skipped") == "nothing_planned"
   and w.calls == [])

print("\n-- 'Write and publish now' goes first --")
a, b, c = reset(("First in the queue?",), ("Second in the queue?",), ("Asked for now?",))
plan.request_now(c)
_, w, _ = wire()
job.periodic()
ok("an asked-for row jumps the queue", w.calls[0]["question"] == "Asked for now?", w.calls)
ok("…and is published", row(c)["status"] == "published" and row(a)["status"] == "planned")

print("\n-- no lock is held while the model writes --")
reset(("What is an AI business machine?",))
wire(writer=Writer(touch_db=True))
r = job.periodic()
ok("the Topics screen can add a row mid-draft", r["status"] == "published", r)

print("\n-- the weekly cap is a rolling seven days, and only publishes count --")
reset(("a?", "published", (now - timedelta(days=1)).isoformat()), ("b?", "refused"), ("c?",))
wire(settings=Settings(weekly_cap=1))
r = job.periodic()
ok("a box at its cap publishes nothing", r.get("skipped") == "weekly_cap", r)
reset(("a?", "published", (now - timedelta(days=8)).isoformat()), ("c?",))
wire(settings=Settings(weekly_cap=1))
ok("an article from eight days ago no longer counts", job.periodic().get("status") == "published")
(a,) = reset(("c?",))
plan.request_now(a)
wire(settings=Settings(weekly_cap=0))
ok("an asked-for row does not get around the owner's cap either", job.periodic().get("skipped") == "weekly_cap"
   and row(a)["status"] == "planned")

print("\n-- a refusal is recorded, word for word, and never retried on its own --")
(a,) = reset(("What is an AI business machine?",))
_, w, p = wire(writer=Writer(refuse="call"))
job.periodic()
ok("a refused draft marks the row refused", row(a)["status"] == "refused", row(a))
ok("…with the exact word and the reason", '"call"' in row(a)["refusal"]
   and "never-use list" in row(a)["refusal"], row(a)["refusal"])
ok("…and nothing reached the publisher", p.published == [])
_, w, _ = wire()
ok("the job does not spend another call on it", job.periodic().get("skipped") == "nothing_planned"
   and w.calls == [])
ok("the screen's 'try again' sends it back through", plan.request_now(a)
   and job.periodic().get("status") == "published", row(a))

print("\n-- a failure is recorded, not raised --")
(a,) = reset(("What is an AI business machine?",))
wire(publisher=Publisher(boom=RuntimeError("Sanity said 401")))
job.periodic()
ok("a publisher error marks the row failed", row(a)["status"] == "failed", row(a))
ok("…with the reason", "Sanity said 401" in (row(a)["refusal"] or ""), row(a))

print("\n-- the slug belongs to the row --")
(a,) = reset(("What is an AI business machine?",))
_, w, p = wire()
job.periodic()
ok("a row with no slug gets one minted from its question", row(a)["slug"] == "what-is-an-ai-business-machine",
   row(a)["slug"])
ok("…stored on the row BEFORE the write", (w.row_during_write[0] or {}).get("slug") == row(a)["slug"],
   w.row_during_write)
ok("…and the publisher uses it, not the drafted title's", p.published[0]["slug"] == row(a)["slug"])
(a,) = reset(("What is an AI business machine?", "refused", None, None, "what-is-an-ai-business-machine"))
plan.request_now(a)
_, _, p = wire()
job.periodic()
ok("a retried row publishes at the SAME URL", p.published[0]["slug"] == "what-is-an-ai-business-machine"
   and row(a)["url"].endswith("/articles/what-is-an-ai-business-machine"), row(a))
(a,) = reset(("Anything", "planned", None, None, "the-owners-own-slug"))
_, _, p = wire()
job.periodic()
ok("a slug already on the row wins", p.published[0]["slug"] == "the-owners-own-slug")
ok("…and is never looked up: the live article at that slug is the row's own", p.looked_up == [],
   p.looked_up)

print("\n-- a minted slug never lands on another article (OSDev1, #1561) --")
a, b = reset(("How do I start?", "published", now.isoformat(), None, "how-do-i-start"),
             ("How do I start?",))
_, _, p = wire()
job.periodic()
ok("the same question asked again gets its own address, not the live article's",
   row(b)["slug"] == "how-do-i-start-2" and p.published[0]["slug"] == "how-do-i-start-2", row(b))
ok("…and the first row keeps its own", row(a)["slug"] == "how-do-i-start", row(a))
a, b = reset(("How do I start?", "published", now.isoformat(), None, "how-do-i-start"),
             ("How do I start",))
wire()
job.periodic()
ok("the question without its mark does too", row(b)["slug"] == "how-do-i-start-2", row(b))
a, b, c = reset(("How do I start?", "failed", None, None, "how-do-i-start"),
                ("How do I start?", "refused", None, None, "how-do-i-start-2"), ("How do I start?",))
wire()
job.periodic()
ok("a slug held by a row in ANY status is taken, so the next free one is used",
   row(c)["slug"] == "how-do-i-start-3", row(c))
(a,) = reset(("How do I start?",))
_, _, p = wire(publisher=Publisher(live={"how-do-i-start"}))
job.periodic()
ok("a slug already live on the site (put there by hand) is skipped too",
   row(a)["slug"] == "how-do-i-start-2" and p.looked_up == ["how-do-i-start", "how-do-i-start-2"],
   (row(a), p.looked_up))
(a,) = reset(("How do I start?",))
_, w, p = wire(publisher=Publisher(lookup_boom=RuntimeError("Sanity query 503")))
job.periodic()
ok("a site that cannot be asked fails the row, with the reason, before any spend",
   row(a)["status"] == "failed" and "Sanity query 503" in (row(a)["refusal"] or "")
   and w.calls == [] and not row(a)["slug"], row(a))

print("\n-- a row interrupted mid-write is released, never stuck --")
old = (now - timedelta(minutes=45)).isoformat()
fresh = (now - timedelta(minutes=2)).isoformat()
a, b, c = reset(("a?", "writing", None, old), ("b?", "writing", None, fresh), ("c?",))
_, w, _ = wire()
job.periodic()
ok("a writing row untouched for 30 minutes becomes failed", row(a)["status"] == "failed", row(a))
ok("…saying what to do", row(a)["refusal"] == job.INTERRUPTED, row(a)["refusal"])
ok("…without spending a call on it", all(x["question"] != "a?" for x in w.calls), w.calls)
ok("a row being written right now is left alone", row(b)["status"] == "writing", row(b))
ok("the tick still publishes the next planned topic", row(c)["status"] == "published", row(c))
ok("…and the released row can be sent again from the screen", plan.request_now(a)
   and job.periodic().get("status") == "published")
ok("a row being written cannot be asked for twice", not plan.request_now(b))

print("\n-- a ping never undoes a publish --")
(a,) = reset(("What is an AI business machine?",))
wire(publisher=Publisher(ping_boom=ConnectionError("indexnow down")))
try:
    r, raised = job.periodic(), False
except Exception:
    raised, r = True, {}
ok("a ping that raises does not escape the tick", not raised)
ok("…and the row still says published", row(a)["status"] == "published" and r.get("status") == "published",
   row(a))

print("\n-- facts and lists saved as text reach the writer as entries, not letters --")
from core import box_settings                                             # noqa: E402
from marketing.aeo_machine import settings as real_settings               # noqa: E402
box_settings.put("seo", "facts", "The Base Machine is $499 once.\nPro is $1,599 once.")
box_settings.put("seo", "never_words", "call\nvoice")
(a,) = reset(("What is an AI business machine?",))
_, w, p = wire()
job.settings = real_settings
job.periodic()
ok("the writer is handed each fact, not each character",
   w.calls and w.calls[0]["facts"] == ("The Base Machine is $499 once.", "Pro is $1,599 once."),
   w.calls and w.calls[0]["facts"])
ok("...and each never-use word", w.calls and w.calls[0]["lists"]["never_words"] == ("call", "voice"),
   w.calls and w.calls[0]["lists"])
box_settings.put("seo", "facts", "")
box_settings.put("seo", "never_words", "")

print("\n-- a relative URL is never pinged --")
# The real publisher refuses a box with no site URL (test_aeo_publisher). This is the guard behind it.
(a,) = reset(("What is an AI business machine?",))
_, _, p = wire(publisher=Publisher(base=""))
r = job.periodic()
ok("if a publish ever came back relative, the row still records it", r["status"] == "published", r)
ok("…and a relative URL is not pinged", p.pinged == [], p.pinged)

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
