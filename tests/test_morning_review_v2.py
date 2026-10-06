"""Morning Review v2 (docs/SCOPE_MORNING_REVIEW_V2.md): a quote for every date, one stored brief a day, a
standing count that never nags twice, an AI that can't invent a number, and no email on an empty morning.
No network, no spend: the AI path runs on a stub `think`."""
import os, sys, tempfile, json, calendar
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
os.environ.setdefault("AIOS_DB_PATH", tempfile.mkdtemp() + "/t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, datetime, timedelta

from core import state
state.init_db()
from core import report, review_brief, review_quotes, review_email, box_settings

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        _failed += 1


def wipe():
    with state.connect() as c:
        c.execute("DELETE FROM daily_reports")
    box_settings.put(review_brief.NS, review_brief.SEEN, {}, set_by="test")
    box_settings.put(review_brief.NS, review_brief.IDEAS_SEEN, {}, set_by="test")


def row(day, machine, title, **parts):
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO daily_reports (day, machine, report_json, written_at, final) "
                  "VALUES (?,?,?,?,1)", (day.isoformat(), machine,
                                         json.dumps({"machine": machine, "title": title, **parts}), state._now()))


# ── 1. a short quote for every day of the year ───────────────────────────────────────────────────────────
Q = review_quotes.QUOTES
ok("twelve months of quotes", sorted(Q) == list(range(1, 13)), sorted(Q))
ok("one quote per calendar date, Feb 29 included (366)",
   all(len(Q[m]) == calendar.monthrange(2024, m)[1] for m in Q) and sum(len(v) for v in Q.values()) == 366,
   {m: len(v) for m, v in Q.items()})
every = [review_quotes.quote_for(date(2024, 1, 1) + timedelta(days=i)) for i in range(366)]
ok("every date of a leap year has a quote", all(isinstance(q, str) and q.strip() for q in every))
ok("no two dates share a quote", len(set(every)) == 366, 366 - len(set(every)))
ok("every quote is short (a headline, not a paragraph)", all(len(q) <= 90 for q in every),
   [q for q in every if len(q) > 90][:3])
ok("October 1 opens the month the mockup shows", review_quotes.quote_for(date(2026, 10, 1)).startswith("A new month"))

# ── 2. the no-repeat rule: a standing count is shown once, and again only when it really grows ──────────────
wipe()
ABOUT, NOW = date(2026, 9, 30), datetime(2026, 10, 1, 8, 0)
T = date(2026, 10, 1)


def standing(n, day):
    row(day, "reel", "Reels", needs_you=[{"text": f"{n} scripts waiting for a look", "href": "/reel"}])


standing(152, T)
b1 = review_brief.build(ABOUT, NOW)
ok("first sight: the count is worth their time", [w["title"] for w in b1["worth"]] == ["152 scripts waiting for a look"],
   b1["worth"])
standing(153, T + timedelta(days=1))
b2 = review_brief.build(T, NOW + timedelta(days=1))
ok("152 becoming 153 the next morning is not news — it is not shown again", b2["worth"] == [], b2["worth"])
standing(170, T + timedelta(days=2))
b3 = review_brief.build(T + timedelta(days=1), NOW + timedelta(days=2))
ok("a real rise (a tenth or more) comes back once", len(b3["worth"]) == 1, b3["worth"])

wipe()
row(T, "inbox", "Inbox", needs_you=[{"text": "2 people would love a reply", "href": "/inbox"}])
review_brief.build(ABOUT, NOW)
row(T + timedelta(days=1), "inbox", "Inbox", needs_you=[{"text": "3 people would love a reply", "href": "/inbox"}])
b = review_brief.build(T, NOW + timedelta(days=1))
ok("a small count rising by one is news", len(b["worth"]) == 1, b["worth"])
box_settings.put(review_brief.NS, review_brief.SEEN,
                 {"inbox:# people would love a reply": {"v": 3.0, "day": "2026-09-01"}}, set_by="test")
row(T + timedelta(days=2), "inbox", "Inbox", needs_you=[{"text": "3 people would love a reply", "href": "/inbox"}])
b = review_brief.build(T + timedelta(days=1), NOW + timedelta(days=2))
ok(f"after {review_brief.FORGET_DAYS} quiet days an item is forgotten and may be shown fresh", len(b["worth"]) == 1,
   b["worth"])

wipe()
row(T, "gtm", "Outreach", watch=[{"text": "Sending domain not warmed", "state": "warn"},
                                 {"text": "Mailbox sign-in failing", "state": "fail", "href": "/gtm"}])
b = review_brief.build(ABOUT, NOW, remember=False)
ok("a failing check is worth their time; a warning is not (a warning is a standing nag)",
   [w["title"] for w in b["worth"]] == ["Mailbox sign-in failing"], b["worth"])
ok("a preview (remember=False) remembers nothing",
   box_settings.get(review_brief.NS, review_brief.SEEN, default={}) == {})

# ── 3. the AI: grounded in the facts, skipped in tests, and paid for once a day ──────────────────────────────
wipe()
row(ABOUT, "reel", "Reels", happened=[{"text": "4 reels published", "value": 4}])
row(T, "inbox", "Inbox", needs_you=[{"text": "2 people would love a reply", "href": "/inbox"}])
calls = []


def think_ok(task, prompt, **kw):
    calls.append((task, kw.get("timeout")))
    return json.dumps({"good_news": "Four reels went out yesterday, 4 in one day.",
                       "ideas": [{"title": "Share yesterday's 4 reels in your stories", "why": "You made 4."},
                                 {"title": "Double down: aim for 8 reels this week", "why": "You made 4."},
                                 {"title": "Give them a call back", "why": "2 people waiting."}]})


b = review_brief.build(ABOUT, NOW, think=think_ok)
ok("one AI call, on the review task, with a bounded wait", calls == [("review", 60)], calls)
ok("a grounded good-news sentence is kept", b["good_news"] == "Four reels went out yesterday, 4 in one day.",
   b["good_news"])
titles = [i["title"] for i in b["ideas"]]
ok("an idea naming a number from the facts is kept", "Share yesterday's 4 reels in your stories" in titles, titles)
ok("an idea with an INVENTED number (8) is dropped", not any("8" in t for t in titles), titles)
ok("an idea is not dropped for its words: 'call' is an ordinary word", "Give them a call back" in titles, titles)
ok("ideas_from says the AI wrote them", b["ideas_from"] == "ai")

b = review_brief.build(ABOUT, NOW, think=think_ok)
ok("an idea suggested this week is not suggested again", "Share yesterday's 4 reels in your stories" not in
   [i["title"] for i in b["ideas"]], b["ideas"])

b = review_brief.build(ABOUT, NOW, think=lambda *a, **k: json.dumps({"good_news": "You earned $9,999 yesterday."}))
ok("an invented money figure in good news falls back to the plain true line",
   b["good_news"] == "Yesterday, Reels: 4 reels published.", b["good_news"])
b = review_brief.build(ABOUT, NOW, think=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
ok("an AI that times out still gives an on-time review", b["good_news"] and b["ideas"] == [], b)

wipe()
row(ABOUT, "reel", "Reels", happened=[{"text": "4 reels published", "value": 4}])
ok("under AIOS_HERMETIC_TEST with no stub, the AI is never called",
   review_brief._ai({"what_happened_yesterday": ["x"], "worth_their_time_today": [], "figures": {}}, []) == ("", []))

calls.clear()
review_brief.ensure(ABOUT, NOW, think=think_ok)
review_brief.ensure(ABOUT, NOW, think=think_ok)
ok("the brief is built and stored once a day — a retry does not pay for the AI twice", len(calls) == 1, calls)
ok("the stored brief is not a machine segment", all(r.get("machine") != "brief" for r in report.read(ABOUT)))
ok("the page reads the stored brief", review_brief.for_page(ABOUT, NOW) == review_brief.get(ABOUT))

# ── 4. the brief's shape (the contract the app page reads) ───────────────────────────────────────────────────
KEYS = {"about", "date_label", "quote", "good_news", "worth", "moving", "ideas", "ideas_from", "empty", "link",
        "built_at", "numbers", "welcome", "learned", "aims", "coming", "first"}
b = review_brief.get(ABOUT)
ok("the brief carries exactly the documented keys", set(b) == KEYS, set(b) ^ KEYS)
ok("every item is {title, why, href, machine}",
   all(set(i) == {"title", "why", "href", "machine"} for i in b["worth"] + b["moving"] + b["ideas"]))
ok("the date label is the morning it is read", b["date_label"] == "Thursday · October 1, 2026", b["date_label"])
ok("no zero is ever a line", not any(i["title"].startswith("0 ") for i in b["worth"] + b["moving"]))

# ── 5. an empty morning sends no email, and the email has no buttons ─────────────────────────────────────────
wipe()
e = review_email.build(ABOUT, NOW)
ok("nothing happened and nothing waits: the brief is empty and the email is skipped", e["empty"] and e["skip"], e)
wipe()
row(T, "inbox", "Inbox", needs_you=[{"text": "2 people would love a reply", "href": "/inbox"}])
e = review_email.build(ABOUT, NOW)
html = review_email.html(e)
ok("a morning with something in it is mailed", not e["skip"])
ok("the subject is the day's quote", e["subject"] == "Your Morning Review: " + review_quotes.quote_for(T), e["subject"])
ok("no buttons in the email", "<button" not in html.lower())

# ── 6. OSDev1's review of #1779, item by item ─────────────────────────────────────────────────────────────────
from datetime import timezone as _tz
from zoneinfo import ZoneInfo
import pathlib as _pl

# 6.1 A BOX WITH ONLY THE APP STORES THE BRIEF AND REMEMBERS WHAT IT SHOWED (only the email used to build it).
wipe()
_st = _pl.Path(tempfile.mkdtemp()) / "review.json"
_saved = (report._state_path, report._cfg, report.snapshot, report.close_open_days, report._owner_email)
report._state_path = lambda: _st
report._cfg = lambda: {"enabled": True, "hour_local": 8, "send_days": "mon,tue,wed,thu,fri,sat,sun"}
report.snapshot = lambda d: {"written": [], "failed": []}
report.close_open_days = lambda now=None: []
report._owner_email = lambda owner: ""
_op = report.settings.operator_slack_user_id
report.settings.operator_slack_user_id = ""
from core import notify as _notify
_btz0 = _notify.buyer_timezone
_notify.buyer_timezone = lambda: str(report.tz())        # the buyer and the box agree here; 6.4 tests when not
try:
    for i, n in enumerate((152, 153, 154)):
        day = T + timedelta(days=i)
        standing(n, day)
        r = report.run(datetime.combine(day, datetime.min.time(), tzinfo=report.tz()).replace(hour=9),
                       send_app=lambda d: "sent")
        if i == 0:
            first = review_brief.get(ABOUT)
    ok("app only: the morning's brief is built and stored by the send", first is not None and r.get("app") == "sent",
       str(r))
    shown = [len((review_brief.get(T + timedelta(days=i - 1)) or {}).get("worth") or []) for i in range(3)]
    ok("app only: '152 scripts waiting' is said on the first morning, then not again", shown == [1, 0, 0], shown)
finally:
    (report._state_path, report._cfg, report.snapshot, report.close_open_days, report._owner_email) = _saved
    report.settings.operator_slack_user_id = _op
    _notify.buyer_timezone = _btz0

# 6.2 THE COUNT IS THE NUMBER AN ITEM OPENS WITH, never an age inside the sentence; people waiting always count.
wipe()
for i in range(3):
    row(T + timedelta(days=i), "content_machine", "Content",
        watch=[{"text": f"Publishing last ran {4 + i}d ago", "state": "fail", "href": "/content"}])
    row(T + timedelta(days=i), "customer_voice", "Unified Inbox",
        needs_you=[{"text": f"{21 + i} conversations are waiting on your reply", "key": "waiting", "value": 21 + i,
                    "person": True}])
got = [[w["title"] for w in review_brief.build(T + timedelta(days=i - 1), NOW + timedelta(days=i))["worth"]]
       for i in range(3)]
ok("an age in the text ('last ran 4d ago', then 5d) is not a rise: said once", 
   sum("Publishing" in t for g in got for t in g) == 1, got)
ok("21 then 22 then 23 people waiting: every new person is news", all(any("waiting on your reply" in t for t in g)
                                                                      for g in got), got)
ok("plurals fold: '1 reply waiting' and '2 replies waiting' are one item",
   review_brief._key("lead", "1 reply waiting for a person") == review_brief._key("lead", "2 replies waiting for a person"))

# 6.3 SPEND (scope decision 3): over half the ceiling, or a vendor near or at its cap. Under half: nothing.
def meters(spend, watch=()):
    row(T, "meters", "Meters", figures={"spend": {"value": spend}, "ceiling": {"value": 90}}, watch=list(watch))
wipe(); meters(40)
ok("spend under half the ceiling is not worth a line", review_brief.build(ABOUT, NOW, remember=False)["worth"] == [])
wipe(); meters(50, [{"text": "x", "state": "fail", "vendor": "Google Places", "pct": 101},
                   {"text": "y", "state": "warn", "vendor": "Hunter", "pct": 90}])
titles = [w["title"] for w in review_brief.build(ABOUT, NOW, remember=False)["worth"]]
ok("over half the ceiling, at cap, and near cap: each said plainly",
   titles == ["$50 of your $90 monthly budget is used", "Google Places has reached its monthly limit",
              "Hunter has used 90% of its monthly limit"], titles)
from core import cost_digest, cost_guard
_m0 = (cost_digest.meters, cost_guard.month_to_date_spend, cost_guard.ceiling)
cost_digest.meters = lambda at: [{"vendor": "hunter", "used": 45, "cap": 50, "pct": 90.0}]
cost_guard.month_to_date_spend = lambda at=None: 50.0
cost_guard.ceiling = lambda: 90.0
try:
    mr = report._meters_report(T)
finally:
    cost_digest.meters, cost_guard.month_to_date_spend, cost_guard.ceiling = _m0
ok("the real meters reporter carries the spend figures and the vendor the brief weighs",
   mr["figures"]["spend"]["value"] == 50.0 and mr["figures"]["ceiling"]["value"] == 90.0
   and mr["watch"][0]["vendor"] == "Hunter" and mr["watch"][0]["pct"] == 90, mr)

# 6.4 THE DATE AND THE QUOTE FOLLOW THE BUYER'S CLOCK, not the box's.
from core import notify
_btz = notify.buyer_timezone
try:
    notify.buyer_timezone = lambda: "Australia/Sydney"
    at = datetime(2026, 10, 1, 22, 0, tzinfo=_tz.utc)          # Friday 8am in Sydney; Thursday on a UTC box
    wipe(); standing(3, T)
    b = review_brief.build(ABOUT, at, remember=False)
    ok("a Sydney buyer on a UTC box reads Friday's date and Friday's quote on Friday morning",
       b["date_label"].startswith("Friday") and b["quote"] == review_quotes.quote_for(date(2026, 10, 2)), b["date_label"])
    notify.buyer_timezone = lambda: "UTC"
    ok("...and a UTC buyer reads Thursday's", review_brief.build(ABOUT, at, remember=False)["date_label"]
       .startswith("Thursday"))
finally:
    notify.buyer_timezone = _btz

# 6.5 THE QUOTES ARE ORIGINAL AND SEASON-FREE (the same line goes to Sydney and Seattle).
BORROWED = ("Start where you are", "Gratitude turns what you have into enough", "main thing the main thing",
            "see the whole road", "next right thing", "Bloom where you", "You can do hard things",
            "best time to start was yesterday", "expert was once a beginner", "investment is in yourself")
SEASONS = ("spring", "summer", "autumn", "fall ", "winter", "midsummer", "longest day", "darkest day")
ok("no well-known saying, reworded", not [q for q in every for b in BORROWED if b.lower() in q.lower()])
ok("no season: true in both hemispheres", not [q for q in every for s_ in SEASONS if s_ in q.lower()],
   [q for q in every for s_ in SEASONS if s_ in q.lower()])

# 6.6 TWO SENDS AT ONCE PAY FOR THE AI ONCE: the row is claimed before the AI is asked.
wipe(); row(ABOUT, "reel", "Reels", happened=[{"text": "4 reels published", "value": 4}])
with state.connect() as c:
    c.execute("INSERT INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,1)",
              (ABOUT.isoformat(), "brief", review_brief._BUILDING, state._now()))
calls.clear()
b = review_brief.ensure(ABOUT, NOW, think=think_ok)
ok("while another send is building it: a preview, no AI, nothing remembered",
   calls == [] and review_brief.get(ABOUT) is None
   and box_settings.get(review_brief.NS, review_brief.SEEN, default={}) == {}, calls)
with state.connect() as c:
    c.execute("UPDATE daily_reports SET written_at = ? WHERE machine = 'brief'", ("2000-01-01T00:00:00+00:00",))
review_brief.ensure(ABOUT, NOW, think=think_ok)
ok("a claim left by a build that died is taken over", len(calls) == 1 and review_brief.get(ABOUT) is not None, calls)

# A BUILD SLOWER THAN THE STALE WINDOW, TAKEN OVER WHILE IT WAITED ON THE AI (OSDev1's re-review): the slow build
# must not overwrite the row now held by another claim, nor record what was seen.
wipe(); row(ABOUT, "reel", "Reels", happened=[{"text": "4 reels published", "value": 4}])
def think_overtaken(task, prompt, **kw):
    with state.connect() as c:
        c.execute("UPDATE daily_reports SET report_json = ? WHERE machine = 'brief'", ('{"building": "someone-else"}',))
    return think_ok(task, prompt, **kw)
review_brief.ensure(ABOUT, NOW, think=think_overtaken)
with state.connect() as c:
    _row = c.execute("SELECT report_json FROM daily_reports WHERE machine = 'brief'").fetchone()["report_json"]
ok("a build whose claim was taken over writes nothing and remembers nothing",
   _row == '{"building": "someone-else"}' and box_settings.get(review_brief.NS, review_brief.SEEN, default={}) == {}
   and box_settings.get(review_brief.NS, review_brief.IDEAS_SEEN, default={}) == {}, _row)
with state.connect() as c:
    _fin = c.execute("SELECT final FROM daily_reports WHERE machine = 'brief'").fetchone()["final"]
ok("the claim is written final, so close_open_days never reopens the day for it", _fin == 1, _fin)

# 6.7 A MACHINE THAT COULD NOT BE READ IS SAID, so a morning of failures is not an empty one marked sent.
wipe(); row(T, "lead_machine", "Lead Machine", error="boom")
b = review_brief.build(ABOUT, NOW, remember=False)
ok("a reporter that failed reads 'Couldn't read Lead Machine', and the morning is not empty",
   [w["title"] for w in b["worth"]] == ["Couldn't read Lead Machine this morning"] and not b["empty"], b["worth"])

# 6.8 AN EMPTY MORNING: no email leaves, and it is marked done so it isn't retried every hour.
wipe()
mailed = []
out = report._email("o@example.com", ABOUT, NOW, T, send_email=lambda *a, **k: mailed.append(a))
ok("an empty morning: report._email sends nothing and says done", out == "sent" and mailed == [], (out, mailed))
wipe(); standing(5, T)
out = report._email("o@example.com", ABOUT, NOW, T, send_email=lambda *a, **k: mailed.append(a))
ok("...and a morning with something in it is mailed", out == "sent" and len(mailed) == 1, (out, mailed))

print("ALL MORNING REVIEW V2 CHECKS PASS" if not _failed else f"{_failed} MORNING REVIEW V2 CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
