"""Every connector answer is in plain words, with what to ask next and what the box can start; five ready-made asks.

Owner, 2026-10-02, after asking his own AI for his Morning Review through this box's connector and getting the
stored record back as raw text: *"We sell AI Business machines… This is not an AI business machine. This is a dumb
box."* (docs/SCOPE_SMART_BOX_ANSWERS.md, Phase 0 and the prompts of Phase 1.) The cause was one line: the MCP
transport's only text was `json.dumps(result)`. This file holds the fix in place:

  · every tool a read connection and an act connection can see has a `render`, a connected app's excepted;
  · for each, on a realistic answer, the text has no braces, no `"field":`, no field name and no None, is not
    empty, and ends with what to ask next; the data still travels whole in structuredContent;
  · a render that raises, or writes nothing, falls back to the JSON the AI always got, and the box logs which
    tool without logging the answer;
  · the Morning Review says what needs you FIRST, says how fresh it is, gives full links when the box knows its
    address (bare paths when it doesn't), says out loud when spending is hidden, drops 0-of-0 meters, and offers
    to send the written replies only to a connection that may ask for it;
  · core.health names the release it runs, from the same place the box's HTTP /health reads it;
  · the server instructions say how to answer, in initialize too; prompts are advertised, listed per seat (five
    for a seat that sees their tools, fewer for one that doesn't), fetched as one user message, prefer the box's
    own AI when it registers, and an unknown or hidden one is the spec's -32602.

Run: python tests/test_connector_answers_in_words.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/answers_in_words.db"
os.environ["AIOS_PAUSE_FILE"] = os.path.join(tempfile.mkdtemp(), "PAUSED")
os.environ["DISPATCH_BEARER_TOKEN"] = "the-wide-token"

from core import state  # noqa: E402

state.init_db()

from marketing.customer_voice.schema import DDL  # noqa: E402
from marketing.customer_voice.inbox import tools as inbox_tools  # noqa: E402,F401 — registers inbox.*
from marketing.aeo_machine import tools as aeo_tools  # noqa: E402,F401 — registers aeo.*

with state.connect() as _c:
    _c.executescript(DDL)

from core import approvals, dispatch, report, version  # noqa: E402 — dispatch registers core's own tools
from core import box_tools  # noqa: E402
from core.config import settings  # noqa: E402
from core.connector import manifest, mcp, prompts, seats, tools, words  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _seat(role):
    sid, cred = seats.mint(f"the owner's {role} assistant", role)
    with state.connect() as c:
        return dict(c.execute("SELECT * FROM seats WHERE id = ?", (sid,)).fetchone()), cred


READ, READ_CRED = _seat("read")
ACT, ACT_CRED = _seat("act")
NARROW = {"id": "seat_narrow", "role": "act", "capabilities": ["read:reports"]}   # a coworker's run seat
BASE = "https://brightline-demo.ownbox.app"
NOW = datetime.now(timezone.utc)


def iso(**ago) -> str:
    return (NOW - timedelta(**ago)).isoformat()


def text_of(name, result, seat=ACT):
    """The MCP result for `result` from tool `name`, as the transport builds it for `seat`."""
    out = mcp._tool_result({"tool": name, "result": result}, 200, tools.lookup(name), seat)
    return out["content"][0]["text"], out


def snake_keys(v, found=None) -> set:
    """Every field name with an underscore, anywhere in a result: words a person never reads."""
    found = set() if found is None else found
    if isinstance(v, dict):
        for k, x in v.items():
            if "_" in str(k):
                found.add(str(k))
            snake_keys(x, found)
    elif isinstance(v, list):
        for x in v:
            snake_keys(x, found)
    return found


def an_approval(title):
    return approvals.propose("box_pause", machine="core", title=title, detail={"t": title, "n": NOW.isoformat()})["id"]


# ── fixtures: one realistic answer per tool (a med spa's box) ──────────────────────────────────────
VERSION_RELEASE = "release/2026.10.02.10"
version.RUNNING_RELEASE = VERSION_RELEASE

DAY = report.today().isoformat()
INBOX_SEG = {
    "machine": "customer_voice", "title": "Unified Inbox",
    "headline": {"value": 3, "label": "good checks today", "delta": None},
    "needs_you": [{"text": "120 conversations are waiting on your reply", "href": "/inbox/inbox", "key": "waiting",
                   "value": 120, "person": True},
                  {"text": "22 replies are written and ready to send", "href": "/inbox/waiting"}],
    "happened": [{"text": "messages came in", "value": 43}, {"text": "people wrote for the first time", "value": 38},
                 {"text": "replies written for you", "value": 35}],
    "watch": [{"text": "Inbox — 120 waiting on you", "state": "warn"}],
    "notes": [],
    "figures": {"inbox_waiting": {"value": 120, "label": "waiting on you"},
                "inbox_today": {"value": 43, "label": "messages today"},
                "inbox_drafts": {"value": 22, "label": "drafts ready to send"},
                "inbox_oldest": {"value": "317 days", "label": "oldest waiting"}},
    "written_at": iso(minutes=5), "final": False}
AEO_SEG = {"machine": "aeo_machine", "title": "AEO Machine",
           "headline": {"value": 1, "label": "article published", "delta": None},
           "needs_you": [], "happened": [{"text": "Published “AI Business Machine”"}], "watch": [], "notes": [],
           "figures": {}, "written_at": iso(minutes=5), "final": False}
WEB_SEG = {"machine": "website", "title": "Website", "headline": {"value": 0, "label": "", "delta": None},
           "needs_you": [], "watch": [], "notes": [], "figures": {},
           "happened": [{"text": "brian-macdonald.com: 179 visits from people this week (+231%)",
                         "href": "/settings/sources"},
                        {"text": "ownbox.io: 6 visits from people this week", "href": "/settings/sources"}],
           "written_at": iso(minutes=5), "final": False}
METERS_SEG = {"machine": "meters", "title": "Meters", "headline": {"value": "$11", "label": "of $90 this cycle"},
              "needs_you": [], "notes": [], "figures": {},
              "happened": [{"text": "HeyGen 3 of 50", "value": "6%"}, {"text": "Apollo 0 of 0", "value": "0%"},
                           {"text": "Hunter 0 of 25", "value": "0%"}],
              "watch": [], "written_at": iso(minutes=5), "final": False}
REVIEW = {"day": DAY, "written_at": iso(minutes=5), "stale": False, "final": False,
          "segments": [INBOX_SEG, AEO_SEG, WEB_SEG], "withheld": ["meters:role_read"], "note": None}

ARTICLE_STOPPED = {"id": 7, "status": "refused", "state": "held back", "topic": "Botox prices",
                   "question": "How much does Botox cost in Austin?", "title": None, "url": None,
                   "published_at": None, "added_at": iso(days=3),
                   "why": "the draft named a price that is not on the allowed numbers list",
                   "what_to_do": "Reword the question, or remove the topic, on AEO → Articles."}
ARTICLE_LIVE = {"id": 3, "status": "published", "state": "live", "topic": "AI Business Machine",
                "question": "What is an AI business machine?", "title": "AI Business Machine",
                "url": "https://brian-macdonald.com/articles/ai-business-machine",
                "published_at": iso(days=1), "added_at": iso(days=9)}
CONNS = {"mailbox": {"status": "connected", "address": "hello@glowmedspa.com", "can_send": "can_send"},
         "social_accounts": {"status": "needs_reauth"}, "ai_account": {"status": "connected"},
         "set_up_at": "/inbox/settings"}
CONVO = {"id": "ig_123", "channel": "instagram", "who": "Dana Whitfield", "last_inbound_at": iso(hours=2),
         "messages": 4, "opted_out": False}


def fixtures() -> dict:
    aid = an_approval("Send 22 replies")
    asked = {"asked": True, "approval": aid, "repeat": False, "note": "waiting for the owner"}
    return {
        "core.manifest": manifest.build(seat=ACT),
        "core.health": box_tools.health(),
        "core.ask": {"ask_id": "ask_1", "status": "answered", "question": "What needs me today?", "text": (
            "Two things need you today.\n\n1. 22 replies are written and waiting. They answer the people who wrote "
            "first this week.\n\nAsk next:\n- Who wrote for the first time today?\n\nI can start:\n- Send the 22 "
            "written replies. You approve it on Approvals.")},
        "core.ask_result": {"ask_id": "ask_1", "status": "answered", "text": (
            "ownbox.io had 6 visits from people this week, and brian-macdonald.com 179.\n\nAsk next:\n- Which "
            "page brought the most people?")},
        "core.brief": {"from": "ai", "as_of": iso(minutes=5), "text": (
            "Your business today, as of 3:12 PM.\n\nNeeds you:\n- 22 replies are written and ready to send: "
            "https://brightline-demo.ownbox.app/inbox/waiting\n\nAsk next:\n- Who is waiting on a reply?\n\n"
            "I can start:\n- Send the 22 written replies. You approve it on Approvals.")},
        "core.spend": {"checked_at": iso(minutes=1), "degraded": [],
                       "claude": {"cycle_to_date_usd": 11.2, "ceiling_usd": 90, "remaining_usd": 78.8,
                                  "at_ceiling": False, "cycle_started": "2026-10-01T07:00:00+00:00"},
                       "vendors": [{"vendor": "heygen", "units_used": 3, "units_cap": 50, "units_soft_goal": None,
                                    "usd_per_unit": 0.5},
                                   {"vendor": "apollo", "units_used": 0, "units_cap": 0, "units_soft_goal": None,
                                    "usd_per_unit": None}],
                       "vendors_note": None},
        "core.propose_stop": asked,
        "core.propose_start": {"asked": False, "note": "the box is already running"},
        "morning_review.report_day": REVIEW,
        "morning_review.report_days": {"days": [DAY, "2026-10-01", "2026-09-30"], "count": 3},
        "morning_review.report_trend": {
            "days_back": 30, "note": "days with no stored report are absent rather than zero; a gap is not a zero",
            "series": {"aeo_machine": [{"day": "2026-09-28", "value": 2}, {"day": "2026-09-30", "value": 4},
                                       {"day": DAY, "value": 1}]},
            "titles": {"aeo_machine": {"title": "AEO Machine", "label": "articles published"}}},
        "inbox.list_conversations": {"conversations": [CONVO], "note": "newest inbound first"},
        "inbox.search": {"query": "facial", "conversations": [CONVO], "note": "matches message text"},
        "inbox.read_conversation": {"id": "ig_123", "messages": [
            {"at": iso(hours=3), "direction": "inbound", "by": None,
             "text": "Hi! Do you have an opening Friday for a hydrafacial?"},
            {"at": iso(hours=2, minutes=50), "direction": "outbound", "by": "owner", "text": "We do, 2 PM works."},
            {"at": iso(hours=2), "direction": "inbound", "by": None, "text": "Perfect, how much is it?"}]},
        "inbox.waiting": {"waiting_on_you": 120, "conversations": [CONVO, {**CONVO, "id": "fb_9", "who": "Marco Ruiz",
                                                                          "channel": "facebook"}],
                          "drafts_ready": [{"conversation": "ig_123", "who": "Dana Whitfield", "channel": "instagram",
                                            "they_said": "Perfect, how much is it?", "they_said_at": iso(hours=2),
                                            "draft": "A hydrafacial is $189, and Friday at 2 PM is yours.",
                                            "draft_id": 41}],
                          "note": "waiting means their message was the last one"},
        "inbox.status": {"box": "running", "box_note": "running", "writing_replies": "off", "sent_this_hour": 3,
                         "hourly_send_cap": 40, "waiting_on_you": 120,
                         "channels": [{"channel": "instagram", "name": "Instagram", "conversations": 40,
                                       "last_message_in": iso(hours=2)}],
                         "connections": CONNS,
                         "handled_by_automations": [{"conversation": "ig_77", "who": "Sam Lee",
                                                     "handled_by": "the spring offer", "since": iso(hours=1),
                                                     "until": iso(hours=-5)}]},
        "inbox.settings": {"settings": [
            {"name": "writing_replies", "value": "on", "means": "the box writes a reply for each new message",
             "changed_at": "/inbox/settings"},
            {"name": "hourly_send_cap", "value": 40, "means": "the most messages the box sends in any hour",
             "changed_at": "the box's configuration (not on a screen yet)"}],
            "connections": CONNS, "note": "reading only: nothing here changes a setting"},
        "inbox.connect": inbox_tools.connect(),
        "inbox.draft_reply": {"id": "ig_123", "written": True, "replying_to": "m_1", "note": "waiting on the screen"},
        "inbox.propose_reply": asked,
        "inbox.propose_saved_reply": asked,
        "inbox.saved_replies": {"saved_replies": [{"id": 1, "name": "Pricing", "words": "Hi Dana, here is our pricing.",
                                                   "times_used": 3}], "where": "/inbox/snippets", "note": None},
        "inbox.propose_signature": asked,
        "inbox.propose_pitch_back": asked,
        "inbox.propose_reply_style": asked,
        "inbox.propose_drafts": {**asked, "skipped": ["fb_9"]},
        "inbox.propose_drafting": asked,
        "inbox.propose_discard_draft": {"asked": False, "note": "no written reply is waiting for that conversation"},
        "inbox.propose_opt_out": asked,
        "aeo.status": {"ready": False, "missing": ["a signed-in AI account (System Settings → AI account)"],
                       "ai": {"state": "no_ai_key", "note": "no AI key"}, "articles_a_week": 3, "paused": False,
                       "published_last_7_days": 1, "left_this_week": 2,
                       "topics": {"planned": 4, "writing": 0, "published": 12, "refused": 1, "failed": 0},
                       "waiting_on_you": 1, "suggestions_waiting_for_ok": 1, "note": "The machine publishes"},
        "aeo.articles": {"articles": [ARTICLE_STOPPED, ARTICLE_LIVE], "total": 2, "note": "waiting topics first"},
        "aeo.article": ARTICLE_STOPPED,
        "aeo.performance": {"available": True, "visits_recorded": True, "site": "brian-macdonald.com",
                            "visitors": 179, "visitors_change": 231, "views": 420, "views_change": 120,
                            "article_views": 85, "article_views_change": None, "ai_visits": 12,
                            "ai_visits_change": -10,
                            "top_articles": [{"path": "/articles/ai-business-machine", "views": 60}],
                            "top_sources": [{"site": "google.com", "views": 40}], "note": "Changes are percentages"},
        "website.detail": {"available": True, "site": "www.glowmedspa.com", "sites": ["www.glowmedspa.com"], "week": 0,
                           "from": "2026-09-27", "to": "2026-10-03", "visitors": 140, "visitors_change": 100.0,
                           "pageviews": 280, "pageviews_change": 12.5, "converted": 2,
                           "by_source": {"search": 35, "ai": 14, "social": 0, "email": 0, "direct": 60, "other": 3},
                           "assistants": {"ChatGPT": 14}, "top_pages": [{"path": "/", "views": 140}],
                           "referrers": [{"site": "google.com", "visits": 35}],
                           "conversions": [{"name": "Booking click", "count": 2}], "left_out": [], "unchecked": 0},
        "core.person": {"found": True, "id": "p_1", "shown": False, "name": "D. S.", "channels": ["email"],
                        "ids": [{"kind": "email", "value": "d•••@example.com", "added_by": "lead_magnet"}],
                        "first_seen_at": "2026-09-30T10:00:00+00:00", "first_seen_by": "lead_magnet",
                        "last_touch_at": "2026-10-02T09:00:00+00:00",
                        "touches": [{"at": "2026-10-02T09:00:00+00:00", "machine": "lead_magnet",
                                     "kind": "guide_sent", "ref": "botox-guide"}]},
        "aeo.searches": {"available": True, "from": "2026-09-02", "to": "2026-09-29",
                         "top_searches": [{"query": "ai business machine", "clicks": 40, "impressions": 900,
                                           "position": 3.2}],
                         "opportunities": [{"query": "what is an ai receptionist", "clicks": 0, "impressions": 1200,
                                            "position": 8.4}], "note": "Opportunities are searches"},
        "aeo.settings": {"site_url": "https://glowmedspa.com", "weekly_cap": 3, "facts": ["Open since 2019"],
                         "allowed_numbers": [], "never_words": ["cheap"], "never_phrases": [],
                         "competitors": ["Radiance Spa"], "note": "These are the only claims"},
        "aeo.sources": {"sanity": {"connected": True, "required": True, "project": "abc123", "dataset": "production"},
                        "airtable": {"connected": False, "required": True, "table": None},
                        "posthog": {"connected": True, "required": False, "recommended": True},
                        "google_search_console": {"connected": False, "site": None},
                        "google_analytics": {"connected": False, "note": "coming soon"},
                        "note": "Credentials are never shown."},
        "aeo.propose_topic": {"asked": True, "approval": aid, "repeat": True, "text": "Already waiting"},
        "aeo.propose_retry": {"asked": False, "error": "Only an article that was held back can be tried again."},
        "aeo.propose_setting": asked,
        "aeo.propose_unpublish": asked,
        "aeo.propose_rewrite": {"asked": False, "error": "There are no facts for the writer yet, so a rewrite "
                                                           "would say no more than the first one. Add them on AEO Settings first."},
    }


FIX = fixtures()

# ── every tool a read and an act connection can see has words ─────────────────────────────────────
print("\n— every tool a person's AI can see answers in words —")
for label, seat in (("read", READ), ("act", ACT)):
    seen = [s for s in tools.visible_to(seat) if not s["machine"].startswith("app_")]
    missing = [s["name"] for s in seen if not callable(s.get("render"))]
    ok(f"every tool a {label} connection sees has a render ({len(seen)} tools)", seen and not missing, str(missing))
    nofix = [s["name"] for s in seen if s["name"] not in FIX]
    ok(f"...and every one of them is exercised below on a realistic answer", not nofix, str(nofix))

RAW_FIELD = re.compile(r'"\w+"\s*:')
for name in sorted(FIX):
    result = FIX[name]
    text, out = text_of(name, result)
    keys = sorted(k for k in snake_keys(result) if re.search(rf"\b{re.escape(k)}\b", text))
    bad = [p for p in ("{", "}") if p in text] + RAW_FIELD.findall(text)
    ok(f"{name}: plain words — no braces, no \"field\":, no field name, no None",
       text.strip() and not bad and not keys and not re.search(r"\b(None|True|False)\b", text),
       f"braces/fields={bad[:3]} keys={keys[:4]} text={text[:240]!r}")
    ok(f"{name}: ends by offering what to ask next", "\nAsk next:\n- " in text, text[-200:])
    sc = out["structuredContent"]
    ok(f"{name}: the data travels whole beside the words", {k: v for k, v in sc.items() if k != mcp.IN_WORDS} == result
       and out["isError"] is False)
    # THE WORDS RIDE IN THE DATA, FIRST: Claude's apps hand the model structuredContent and drop the text (measured
    # on the owner's box, 2026-10-02, release .12), so words only in `content` never reach the AI.
    ok(f"{name}: the words are the first thing in the data a Claude app hands its model",
       list(sc)[0] == mcp.IN_WORDS and sc[mcp.IN_WORDS] == text, list(sc)[:3])

# NOT VACUOUS: the same check reads the old transport's text as raw, so it can fail.
_old = json.dumps(FIX["morning_review.report_day"])
ok("...and the check above is not vacuous: the old JSON text fails it", "{" in _old and RAW_FIELD.search(_old))

# ── a broken render never costs the answer ───────────────────────────────────────────────────────
print("\n— a render that fails falls back to the JSON the AI always got —")
LOGGED = []
_real_warning = mcp.log.warning
mcp.log.warning = lambda event, **kw: LOGGED.append((event, kw))


def _boom(r):
    raise RuntimeError("secret customer text that must never reach a log")


tools.register("words_probe_boom", fn=lambda: {"who": "Dana"}, machine="words_probe", capability="read:reports",
               title="Probe a render that raises", description="d", render=_boom)
tools.register("words_probe_blank", fn=lambda: {"who": "Dana"}, machine="words_probe", capability="read:reports",
               title="Probe a render that writes nothing", description="d", render=lambda r: "  ")
for name in ("words_probe.words_probe_boom", "words_probe.words_probe_blank"):
    payload, status = tools.call(name, {}, READ)
    out = mcp._tool_result(payload, status, tools.lookup(name), READ)
    ok(f"{name.split('.')[1]}: the text is today's JSON", out["content"][0]["text"] == json.dumps({"who": "Dana"}),
       out["content"][0]["text"])
    ok("...and the data is still there, not an error", out["structuredContent"] == {"who": "Dana"}
       and out["isError"] is False)
mcp.log.warning = _real_warning
ok("the box logs which tool's words broke", [e for e, _ in LOGGED] == ["connector.render_failed",
                                                                        "connector.render_empty"]
   and LOGGED[0][1].get("tool") == "words_probe.words_probe_boom", str(LOGGED))
ok("...and never the answer or the error's text", "Dana" not in str(LOGGED) and "secret" not in str(LOGGED),
   str(LOGGED))
try:
    tools.register("words_probe_bad", fn=lambda: {}, machine="words_probe", capability="read:reports",
                   title="Probe a render that is not a function", description="d", render="words")
    ok("a render that is not a function is refused at import", False)
except ValueError:
    ok("a render that is not a function is refused at import", True)
ok("a tool with no render (a connected app's) answers as before",
   mcp._tool_result({"result": {"a": 1}}, 200, {"name": "app_notes.list"}, READ)["content"][0]["text"] == '{"a": 1}')

# ── the Morning Review ───────────────────────────────────────────────────────────────────────────
print("\n— the Morning Review: what needs you first, fresh, linked, spending said in words —")
_real_base = settings.dashboard_base_url
settings.dashboard_base_url = BASE
text, _ = text_of("morning_review.report_day", REVIEW, ACT)
ok("needs-you comes before what happened", 0 <= text.find("Needs you:") < text.find("What happened:"), text[:300])
ok("...and the first thing said after the heading is the 120 waiting",
   text.split("Needs you:\n", 1)[-1].startswith("- 120 conversations are waiting on your reply"), text[:300])
ok("it says how fresh it is", "As of " in text and "out of date" not in text, text[:160])
ok("links are full addresses on the box", f"{BASE}/inbox/waiting" in text and f"{BASE}/app/review/{DAY}" in text,
   text)
_links = re.findall(r"\S*/(?:inbox|app/review|approvals)\S*", text)
ok("...never a bare path", len(_links) >= 4 and all(x.startswith(BASE + "/") for x in _links), str(_links))
ok("what happened reads number first, in the review's own words", "- 43 messages came in" in text
   and "- 38 people wrote for the first time" in text and "Published “AI Business Machine”" in text
   and "brian-macdonald.com: 179 visits from people this week (+231%)" in text, text)
ok("a number already said is not said twice (120 and 43 live in the lines, not again as figures)",
   # COUNTED BELOW THE "As of" LINE: at 4:43 PM the clock itself said 43 (OSDev1, 2026-10-02 23:43 UTC).
   (_body := text.split("\n", 1)[-1]).count("120") == 1 and _body.count("43") == 1
   and "Oldest waiting: 317 days" in text, text)
ok("the act connection is offered the 22 written replies, through Approvals",
   "I can start:\n- Send the 22 written replies. You approve it on Approvals: " + BASE + "/approvals" in text, text)
ok("...and asked next about who is waiting and the website (the machines' own follow-ons lead)",
   "Who is waiting on a reply?" in text and "How is my website doing?" in text, text[-400:])
read_text, _ = text_of("morning_review.report_day", REVIEW, READ)
ok("a read connection is NOT offered a send it may not ask for", "I can start" not in read_text
   and "Send the 22" not in read_text, read_text[-300:])
ok("withheld spending is said in words", "Spending is hidden from this connection." in read_text, read_text)
stale_text, _ = text_of("morning_review.report_day", {**REVIEW, "stale": True}, READ)
ok("a stale review says it may be out of date", "This may be out of date" in stale_text, stale_text[:200])
final_text, _ = text_of("morning_review.report_day", {**REVIEW, "final": True}, READ)
ok("a closed day says its numbers are final", "final numbers" in final_text, final_text[:200])
with_meters, _ = text_of("morning_review.report_day", {**REVIEW, "segments": REVIEW["segments"] + [METERS_SEG],
                                                       "withheld": []}, ACT)
ok("the act connection sees what was spent and the meter in use", "Spent $11 of $90 this cycle" in with_meters
   and "HeyGen 3 of 50 (6%)" in with_meters, with_meters)
ok("...and no 0-of-0 or unused meter is a line", "Apollo" not in with_meters and "Hunter" not in with_meters,
   with_meters)
settings.dashboard_base_url = ""
bare, _ = text_of("morning_review.report_day", REVIEW, ACT)
ok("with no known address, the path is written as it is (never a guessed host)",
   "- 22 replies are written and ready to send: /inbox/waiting" in bare and "https://" not in bare, bare)
settings.dashboard_base_url = BASE

print("\n— the same, end to end over MCP, from rows the box stored —")
with state.connect() as c:
    for seg in (INBOX_SEG, METERS_SEG):
        body = {k: v for k, v in seg.items() if k not in ("written_at", "final")}
        c.execute("INSERT INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,0)",
                  (DAY, seg["machine"], json.dumps(body), iso(minutes=3)))
client = dispatch.app.test_client()


def rpc(method, params=None, cred=READ_CRED, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/api/v1/mcp", json=body, headers={"Authorization": f"Bearer {cred}"}).get_json()


r = rpc("tools/call", {"name": "morning_review.report_day", "arguments": {}})["result"]
t = r["content"][0]["text"]
ok("a read connection's review over MCP is words, needs-you first",
   0 < t.find("Needs you:") < t.find("What happened:") and "{" not in t
   and r["structuredContent"]["withheld"] == ["meters:role_read"], t[:300])
ok("...and says spending is hidden", "Spending is hidden from this connection." in t, t)
r = rpc("tools/call", {"name": "morning_review.report_day", "arguments": {}}, cred=ACT_CRED)["result"]
t = r["content"][0]["text"]
ok("an act connection's review shows what was spent and offers the replies", "Spent $11" in t
   and "Send the 22 written replies" in t and "Apollo" not in t, t)
r = rpc("tools/call", {"name": "aios.morning_review.report_day", "arguments": {}})["result"]
ok("an old tool name gets the same words", r["content"][0]["text"].startswith("Today's Morning Review"),
   r["content"][0]["text"][:80])

# ── core.health names its release ────────────────────────────────────────────────────────────────
print("\n— core.health names the release it runs, as /health does —")
h = box_tools.health()
ok("the answer carries the release, from the same place /health reads it",
   h.get("release") == VERSION_RELEASE == version.status().get("release"), str(h.get("release")))
r = rpc("tools/call", {"name": "core.health", "arguments": {}})["result"]
ok("...and says it in words: Running release 2026.10.02.10", "Running release 2026.10.02.10." in
   r["content"][0]["text"] and r["structuredContent"]["release"] == VERSION_RELEASE, r["content"][0]["text"])
version.RUNNING_RELEASE = None
h = box_tools.health()
ok("a box that never installed a release says None in the data and nothing in the words",
   h.get("release") is None and "release" not in text_of("core.health", h)[0].lower(), str(h.get("release")))
version.RUNNING_RELEASE = VERSION_RELEASE

# ── instructions and prompts ─────────────────────────────────────────────────────────────────────
print("\n— the server tells every AI how to answer, and offers the ready-made asks —")
init = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
disc = rpc("server/discover")["result"]
for label, got in (("initialize", init), ("server/discover", disc)):
    ok(f"{label} advertises prompts", "prompts" in got["capabilities"] and "tools" in got["capabilities"]
       and not got["capabilities"]["prompts"].get("listChanged"), str(got["capabilities"]))
    ins = got.get("instructions") or ""
    ok(f"{label} carries the instructions: safety kept, plain words, ask next, what it can start, prompts",
       "no tool sends, publishes or spends on its own" in ins and "plain words" in ins and "Never show raw fields"
       in ins and "what to ask next" in ins and "can start" in ins and "prompts/list" in ins, ins)
FIVE = ["morning_brief", "websites", "what_needs_me", "what_to_write_next", "who_to_follow_up"]
listed = rpc("prompts/list")["result"]["prompts"]
ok("prompts/list gives a read connection the five ready-made asks", [p["name"] for p in listed] == FIVE,
   str([p["name"] for p in listed]))
ok("...each titled as the owner approved them",
   {p["name"]: p["title"] for p in listed} == {"morning_brief": "Morning brief", "what_needs_me": "What needs me today",
                                              "who_to_follow_up": "Who should I follow up with",
                                              "websites": "How are my websites doing",
                                              "what_to_write_next": "What should we write next"}, str(listed))
narrow = [p["name"] for p in prompts.listed(NARROW)]
ok("a seat that sees only the review gets fewer: the three that read it",
   narrow == ["morning_brief", "websites", "what_needs_me"], str(narrow))
got = rpc("prompts/get", {"name": "morning_brief"})["result"]
msg = got["messages"][0]
ok("prompts/get returns one user message", len(got["messages"]) == 1 and msg["role"] == "user"
   and msg["content"]["type"] == "text", str(got)[:200])
ok("...naming the tools this connection can use, and how to answer",
   "morning_review.report_day" in msg["content"]["text"] and "inbox.waiting" in msg["content"]["text"]
   and "Ask next:" in msg["content"]["text"] and "I can start:" in msg["content"]["text"], msg["content"]["text"])
ok("...stamped complete like every result", got.get("resultType") == "complete")
narrow_msg = prompts.message("morning_brief", NARROW)["messages"][0]["content"]["text"]
ok("a narrower seat's message names only the tools it may use", "inbox.waiting" not in narrow_msg
   and "morning_review.report_day" in narrow_msg, narrow_msg)
e = rpc("prompts/get", {"name": "no_such_ask"})
ok("an unknown prompt is the spec's invalid-params error", e.get("error", {}).get("code") == -32602
   and "result" not in e, str(e))
e = mcp._handle("prompts/get", {"name": "who_to_follow_up"}, 9, NARROW)
ok("...and so is one this seat can't answer (listing hides it, so getting does too)",
   e.get("error", {}).get("code") == -32602, str(e))
e = rpc("prompts/get", {"name": "morning_brief", "arguments": {"day": "2026-10-01"}})
ok("an argument the ask doesn't take is refused, not ignored", e.get("error", {}).get("code") == -32602, str(e))
ok("no prompt says aios", "aios" not in json.dumps([listed, got]).lower())

print("\n— the box's own AI is preferred the day it registers —")
_real_brief = tools._REGISTRY.get("core.brief")         # core/brief.py registers it since #1842
if _real_brief is None:
    tools.register("brief", fn=lambda: {"brief": "x"}, machine="core", capability="read:reports",
                   title="Read the box's own brief", description="d", render=lambda r: "The brief.\n\nAsk next:\n- x")
pref = prompts.message("morning_brief", READ)["messages"][0]["content"]["text"]
ok("with core.brief here, the morning brief starts with it", "Start with the box's own answer: core.brief" in pref,
   pref)
ok("...and still lists the detail tools", "morning_review.report_day" in pref)
tools._REGISTRY.pop("core.brief", None)
ok("without it, the ask goes to the tools directly", "core.brief" not in
   prompts.message("morning_brief", READ)["messages"][0]["content"]["text"])
if _real_brief is not None:
    tools._REGISTRY["core.brief"] = _real_brief

settings.dashboard_base_url = _real_base
print("\n" + ("ALL OK" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
