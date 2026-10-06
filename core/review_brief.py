"""The Morning Review, as one light page: a quote, a good-news line, what's worth his time, what's moving, ideas.

docs/SCOPE_MORNING_REVIEW_V2.md, approved by the owner 2026-10-01 ("Ok go"). His words that shaped it:
  * "display the data points that we have and make suggestions on how to improve"
  * "Light and optimistic. That's what we want. We want to motivate and inspire people."
  * "if they're stale information in there that they can't instantly change then we don't continue to harass
    and annoy them every day."
  * "a short motivational quote for every day of the year and it goes out to everybody. That should be the
    headline"

WHAT THIS IS. A rendering of the rows the reporters already store (core/report.py). A reporter needs no change:
three OPTIONAL fields on a needs_you item sharpen it (below), and `m.reporter` keeps its SDK promise. The brief for a morning is BUILT ONCE, when the review goes out, and STORED
as one more row of the day it is about (`daily_reports`, machine "brief"), so the email, the app page, the
mobile notification and the owner's AI read the same words, and the page never pays for the AI.

THE CONTRACT (what `get` and `build` return; WebDev2's page draws exactly this):

    {
      "about":      "YYYY-MM-DD",            the day the review is about (yesterday)
      "date_label": "Thursday · October 1, 2026",   the morning it is read, on the buyer's clock
      "quote":      "A new month, a clean slate, a bright start.",
      "good_news":  "…" | "",                one optimistic sentence on yesterday; "" when there is none
      "best":       "…" | "",                a personal best (step 1.4): "New personal best: …"; "" when none fell
      "your_week":  [item …],                Mondays only (V2 step 2): seven days against the seven before, the biggest
                                             change first; [] on every other morning
      "worth":      [item …],                worth your time today: decisions only he can make, new or changed
      "first":      [item …],                who to answer first, by name: up to three, from a reporter's answer_first
      "moving":     [item …],                what the machines did, one per machine, only what happened
      "ideas":      [item …],                two or three from the box's AI, grounded in the numbers above
      "ideas_from": "ai" | "",               "ai" when the ideas came from the box's AI
      "empty":      True | False,            nothing in worth, moving or ideas: no email goes out
      "link":       "https://…/app/review/YYYY-MM-DD",
      "built_at":   ISO time,
    }
    item = {"title": str, "why": str, "href": str, "machine": str}   (href "" when there is nowhere to go)

NEVER THE SAME NAG TWICE (owner, 2026-10-01). A decision is in `worth` when it is new, or when its number has
gone up enough that something really changed. One that sits unchanged ("152 scripts waiting") is said once, then
stays on its machine's own screen. The box remembers what it has seen (`SEEN`, box settings); a key not seen for
FORGET_DAYS counts as new again. An idea is not offered again within IDEA_DAYS. A needs_you item may carry:
    "key":    a stable name for it (else its words, numbers out, plurals folded)
    "value":  its count (else the number its text OPENS with; a number mid-sentence is an age, not a count)
    "person": True when the count is people waiting on him: any rise is news ("a new message always counts")

SPEND (scope decision 3): over half the monthly ceiling, or a vendor near or at its cap, is worth his time too.

NO AI, NO PROBLEM. With no AI signed in, the AI call refused, the ceiling reached, or an answer that fails the
checks, the review still goes out on time with a plain good-news line and no ideas.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone

from core import report, state
from core.logging import get_logger

log = get_logger(__name__)

BRIEF = "brief"                 # the reserved machine key of the stored brief (report.read leaves it out)
NS = "core"
SEEN = "review_seen"            # {key: {"v": number | None, "day": "YYYY-MM-DD"}}
IDEAS_SEEN = "review_ideas"     # {normalised idea title: "YYYY-MM-DD"}
FORGET_DAYS = 14
IDEA_DAYS = 7
MAX_WORTH, MAX_MOVING, MAX_IDEAS = 5, 5, 3
AI_TASK = "review"              # config models: -> sonnet (Morning Review V2 decision 1)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ── what happened, in sentences ─────────────────────────────────────────────────────────────────────────────
def _phrase(item: dict) -> str:
    """One happened item as words: "7 companies found", "PageSpeed score: 87". "" when it has no value."""
    text, v = str(item.get("text") or "").strip(), item.get("value")
    if not text:
        return ""
    if v in (None, ""):
        return text
    if not report.has_value(v):
        return ""
    v = report._fmt_value(v)
    if str(v) in _NUM.findall(text):                    # "4 reels published" already says it; never "…: 4"
        return text
    return f"{v} {text}" if text[:1].islower() else f"{text}: {v}"


def _moving(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("error"):
            continue
        bits = [p for p in (_phrase(x) for x in r.get("happened") or []) if p]
        if not bits:
            continue
        title = ", ".join(bits[:3])
        # A SITE IS NOT A SENTENCE: "example.com: 163 visits" keeps its name; "7 companies found" takes a capital.
        if "." not in title.split(" ", 1)[0]:
            title = title[:1].upper() + title[1:]
        out.append({"title": title, "why": "", "href": "",
                    "machine": str(r.get("title") or r.get("machine") or "")})
    return out[:MAX_MOVING]


# ── worth your time: new or changed, never the same nag twice ───────────────────────────────────────────────
# AN ITEM IS ITS MACHINE'S `key` WHEN IT GIVES ONE, else its words with the numbers taken out and the plurals
# folded, so "1 reply waiting" and "2 replies waiting" are one item and "X last ran 4d ago" is the same item on
# day 5. Its COUNT is the item's `value`, else the number it OPENS with ("152 scripts"); a number inside the
# sentence is an age or a date, never a count (OSDev1's review of #1779: "last ran 4d ago" read as a rise of one,
# every morning, for 8 mornings).
_FILLER = {"is", "are", "was", "were", "has", "have", "a", "an", "the"}
MAX_NUMBERS = 5


def _numbers(rows: list[dict]) -> list[dict]:
    """By the numbers: each machine's headline, as a number with its label, the way the numbers page
    draws it big (core/dash/review.py `_segment`). Only a real headline counts (`report._stored_number`: a number
    with a label), never a zero (owner, 2026-09-29: a line with no data is not shown). Stored with the brief, so
    the page, the email and Slack show the same figures (owner, 2026-10-04: "pulling all the best data and
    displaying it on the box and in the email equally")."""
    out = []
    for r in rows:
        if r.get("error"):
            continue
        v = report._stored_number(r)
        label = str((r.get("headline") or {}).get("label") or "").strip()
        if v is None or not label or not v:
            continue
        if label.endswith(" today"):                  # the brief is about yesterday: "15 messages", not "today"
            label = label[:-len(" today")]
        out.append({"machine": str(r.get("title") or r.get("machine") or ""), "value": v, "label": label})
    return out[:MAX_NUMBERS]


def _plain_good_news(moving: list[dict]) -> str:
    """The good-news line with no AI: true, grounded, never a zero, and never the same words as "Already moving"
    item 01. One phrase from each of up to three machines, in a sentence a person says ("Yesterday: 15 messages came
    in, 15 companies found and 1 article published."), never the machines' names (polish, owner 2026-10-06)."""
    bits = []
    for m in (moving or [])[:3]:
        first = str(m.get("title") or "").split(", ", 1)[0].strip()
        word = first.split(" ", 1)[0]
        if first and "." not in word:                        # a site's line ("example.com: 163 visits") stays put
            bits.append(first[:1].lower() + first[1:] if word[1:].islower() else first)   # never "pageSpeed"
    if not bits:
        line = str(((moving or [{}])[0]).get("title") or "").split(", ", 1)[0].strip()
        return f"Yesterday, {line}." if line else ""
    said = bits[0] if len(bits) == 1 else ", ".join(bits[:-1]) + " and " + bits[-1]
    return f"Yesterday: {said}."


_LEAD_NUM = re.compile(r"\s*(\d[\d,]*(?:\.\d+)?)")


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _key(machine: str, text: str, given=None) -> str:
    if given:
        return f"{machine}:{given}"
    words = re.findall(r"[a-z#]+", _NUM.sub("#", text.lower()))
    return f"{machine}:{' '.join(_singular(w) for w in words if w not in _FILLER)}"


def _value(item: dict, text: str):
    v = item.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = _LEAD_NUM.match(text)
    return float(m.group(1).replace(",", "")) if m else None


def _changed_enough(before, now, person: bool = False) -> bool:
    """Has a standing count gone up enough that something new really happened? A step of at least one for a
    small count, and of a tenth for a large one, so "152 scripts" becoming 153 stays quiet. A count of PEOPLE
    waiting on him rises by any amount and it is news: the scope's "a new message from a person always counts"."""
    if before is None or now is None:
        return False
    if person:
        return now > before
    return now - before >= max(1.0, math.ceil(before * 0.10))


def _candidates(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        machine = str(r.get("machine") or "")
        title = str(r.get("title") or machine)
        if r.get("error"):
            # A MACHINE THAT COULD NOT BE READ IS SAID, not silently left out: a morning where every reporter
            # failed would otherwise read as a quiet one and be marked sent.
            out.append({"key": f"unread:{machine}", "v": None, "person": False,
                        "item": {"title": f"Couldn't read {title} this morning",
                                 "why": "Its numbers will be back here once it reports again.", "href": "",
                                 "machine": title}})
            continue
        items = list(r.get("needs_you") or [])
        # A FAILING CHECK IS SOMETHING TO ACT ON; a warning or a set-up prompt is not (it is a standing nag).
        items += [w for w in r.get("watch") or [] if w.get("state") == "fail"]
        for it in items:
            text = str(it.get("text") or "").strip()
            if not text or it.get("truncated"):
                continue
            out.append({"key": _key(machine, text, it.get("key")), "v": _value(it, text),
                        "person": bool(it.get("person")),
                        "item": {"title": text, "why": str(it.get("why") or ""), "href": str(it.get("href") or ""),
                                 "machine": title, "person": bool(it.get("person"))}})
    return out


def _meter_candidates(meters: dict | None) -> list[dict]:
    """SPEND, ONLY WHEN IT'S WORTH READING (scope decision 3, approved): over half the monthly ceiling, or a vendor
    near or at its cap. Said in plain words; the meters' own lines are written for the operator."""
    if not meters or meters.get("error"):
        return []
    out = []
    figs = meters.get("figures") or {}
    spend = (figs.get("spend") or {}).get("value")
    ceiling = (figs.get("ceiling") or {}).get("value")
    if isinstance(spend, (int, float)) and isinstance(ceiling, (int, float)) and ceiling > 0 and spend > ceiling / 2:
        out.append({"key": "meters:spend", "v": float(spend), "person": False,
                    "item": {"title": f"${spend:.0f} of your ${ceiling:.0f} monthly budget is used",
                             "why": "Past the halfway mark for this cycle; worth a glance at what is using it.",
                             "href": "", "machine": "Spend"}})
    for w in meters.get("watch") or []:
        vendor, pct = str(w.get("vendor") or ""), w.get("pct")
        if not vendor or w.get("state") not in ("fail", "warn"):
            continue
        if w.get("state") == "fail":
            out.append({"key": f"meters:cap:{vendor}", "v": None, "person": False,
                        "item": {"title": f"{vendor} has reached its monthly limit",
                                 "why": "It picks up again when the cycle resets, or raise its limit to keep going.",
                                 "href": "", "machine": "Spend"}})
        elif isinstance(pct, (int, float)):
            out.append({"key": f"meters:near:{vendor}", "v": float(pct), "person": False,
                        "item": {"title": f"{vendor} has used {pct:.0f}% of its monthly limit",
                                 "why": "", "href": "", "machine": "Spend"}})
    return out


def _worth(rows: list[dict], about: date, seen: dict, meters: dict | None = None) -> tuple[list[dict], dict]:
    """The items to show, and the remembered state after seeing today's. Pure: the caller stores it."""
    seen = {k: v for k, v in (seen or {}).items()
            if isinstance(v, dict) and _days_since(v.get("day"), about) <= FORGET_DAYS}
    show = []
    for c in _candidates(rows) + _meter_candidates(meters):
        was = seen.get(c["key"])
        if was is None or _changed_enough(was.get("v"), c["v"], c["person"]):
            show.append(c["item"])
        seen[c["key"]] = {"v": c["v"], "day": about.isoformat()}
    return show[:MAX_WORTH], seen


def _days_since(d, about: date) -> int:
    try:
        return (about - date.fromisoformat(str(d))).days
    except (TypeError, ValueError):
        return 10 ** 6


# ── the box's AI: one good-news sentence and two or three ideas ─────────────────────────────────────────────
_SYSTEM = ("You write two parts of a small business owner's morning review: one warm, optimistic sentence about "
           "what went well yesterday, and two or three short ideas for what they could do differently. Use ONLY "
           "the facts given; never invent a number, a name or an event. Every idea must be something they can "
           "do today, and must name a number from the facts. Never scold, never list problems, never repeat an "
           "idea from the 'already suggested' list. Plain, friendly words; no jargon, no exclamation marks. "
           "THE WEEK: 'last_7_days' gives each headline by weekday, oldest first, the last being yesterday; notice a "
           "streak or a change across the week rather than judging one day alone. "
           "WHO THE BUSINESS IS: when a 'business' block is given, every idea must fit that business, its offers, its "
           "customers and its goals (in the order given), so a med spa never gets a gym's advice. A planned offer may be "
           "prepared for, never described as available yet. "
           "NEVER RESTATE THE TO-DO LIST: the 'worth_their_time_today' lines are already shown above your ideas, so an "
           "idea must add something new, never repeat or rephrase one of them. "
           "Reply with JSON only: {\"good_news\": \"…\", \"ideas\": [{\"title\": \"…\", \"why\": \"…\"}]}. "
           "A title is under 60 characters; a why is one or two sentences.")


WEEK_DAYS = 7


def _week(rows_y: list[dict], about: date) -> dict:
    """THE LAST SEVEN DAYS, NOT ONE (#1953 step 1.2): each machine's stored headline for the week ending `about`,
    oldest first, from `report.history` (already kept). "Up three days running" needs the week; yesterday alone can
    only say yesterday. Days are named by weekday and never by date, so no day-of-month joins the numbers an idea is
    allowed to use (`_grounded`). A machine with fewer than two days, or no label, says nothing here. Never raises."""
    try:
        hist = report.history(about, WEEK_DAYS)
    except Exception:                                    # noqa: BLE001 — no week is the old one-day review
        return {}
    out = {}
    for r in rows_y:
        label = str((r.get("headline") or {}).get("label") or "").strip()
        series = [x for x in hist.get(r.get("machine")) or [] if report.has_value(x.get("value"))]
        if label and len(series) >= 2:
            out[f"{r.get('title') or r.get('machine')}: {label}"] = {
                date.fromisoformat(str(x["day"])).strftime("%A"): x["value"] for x in series}
    return out


def _facts(rows_y: list[dict], rows_t: list[dict], worth: list[dict], moving: list[dict],
           week: dict | None = None) -> dict:
    def figs(r):
        return {k: v.get("value") for k, v in (r.get("figures") or {}).items()
                if isinstance(v, dict) and report.has_value(v.get("value"))}
    out = {
        "what_happened_yesterday": [f"{m['machine']}: {m['title']}" for m in moving],
        "worth_their_time_today": [f"{w['machine']}: {w['title']}" for w in worth],
        "figures": {str(r.get("title") or r.get("machine")): figs(r) for r in rows_y + rows_t if figs(r)},
    }
    if week:
        out["last_7_days"] = week
    return out


def _grounded(text: str, facts_text: str) -> bool:
    """Every number in an AI line must appear in the facts it was given."""
    have = {n.replace(",", "") for n in _NUM.findall(facts_text)}
    return all(n.replace(",", "") in have for n in _NUM.findall(text))


# ── THE WELCOME REVIEW (#1957 C4) ─────────────────────────────────────────────────────────────────────────────────
# Owner, 2026-10-04: the Morning Review should "be delivered to them the day after they purchase the box and have some
# sort of information and aspirational ideas on how they can improve ... their business ... Otherwise it's just a dumb
# box." The FIRST brief a box ever stores is a Welcome edition: what the box learned about the business (from
# core/business_context.py, quoted where a page said it), the owner's goals and how the box on THIS box works toward
# each, what's coming, and ideas grounded in all of it. Core names no machine (tests/test_core_boundary.py): the
# screens a goal may point at are the box's own installed sections, read from the shell at the moment of writing.
WELCOME_PLANS_MONTHS = 12
MAX_LEARNED = 4

_WELCOME_SYSTEM = (
    "You write the first morning review a small business owner gets from their new box. Reply with JSON only: "
    "{\"good_news\": \"…\", \"aims\": [{\"goal\": \"…\", \"why\": \"…\", \"href\": \"…\"}], "
    "\"ideas\": [{\"title\": \"…\", \"why\": \"…\"}]}. good_news is one warm sentence welcoming them, about their "
    "business. aims has one entry per goal in 'goals', in that order: why says in one sentence how the box will work "
    "toward it, using ONLY a section listed in 'installed', and href is that section's href exactly, or \"\" when no "
    "installed section serves the goal (then say the box will track it in the morning review). ideas are two or three "
    "things they could do this week, grounded in the business facts; a title is under 60 characters, a why one or two "
    "sentences. Use ONLY the facts given: never invent a number, a price, a name or a promise. Never mention a planned "
    "offer as available. Plain, friendly words; no exclamation marks.")


def _is_first(about: date) -> bool:
    """The morning after the box was bought: no brief stored for any earlier day, and the day it is about is on or
    after the day the box's owner was created. A box that has been running (an older box, or one whose history was
    stored before this edition existed) never gets a welcome out of the blue."""
    try:
        made = str((state.owner_user() or {}).get("created_at") or "")[:10]
        if not made or about.isoformat() < made:
            return False
        with state.connect() as c:
            row = c.execute("SELECT 1 FROM daily_reports WHERE machine = ? AND day < ? AND report_json NOT LIKE ? "
                            "LIMIT 1", (BRIEF, about.isoformat(), _CLAIM_LIKE)).fetchone()
        return row is None
    except Exception:                                    # noqa: BLE001 — unsure means an ordinary review
        return False


def _installed() -> list[dict]:
    """The machines' own sections on this box, as {title, href}. Never core's rows; never one that isn't here."""
    try:
        from core import shell
        return [{"title": s.title, "href": s.href} for s in shell.sections()
                if s.machine != "core" and s.href.startswith("/")]
    except Exception:                                    # noqa: BLE001 — no menu, no pointers: still a welcome
        return []


def _ctx() -> dict:
    try:
        from core import business_context
        return business_context.get()
    except Exception:                                    # noqa: BLE001 — no context yet is an empty one
        return {}


def _host(url: str) -> str:
    return str(url or "").split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")


def _found(ctx: dict) -> list[dict]:
    """THE LIGHT SCAN'S FIND, AS A QUESTION, when no website is confirmed (OSDev1 on #1964: most buyers won't have
    filled Your Business in by the first morning). Only what the page said; one tap to Your Business answers it."""
    s = ctx.get("suggested") or {}
    if ctx.get("website") or not s.get("website"):
        return []
    bits = ", ".join(str(s[k]).strip() for k in ("name", "area") if str(s.get(k) or "").strip())
    return [{"title": f"We found {_host(s['website'])}{': ' + bits if bits else ''}. Is this you?",
             "why": "Tell your box, and tomorrow's review starts from it.", "href": "/settings/business",
             "machine": ""}]


def _learned(ctx: dict) -> list[dict]:
    """What the box knows, said back: the light scan's find when nothing is confirmed, then the profile's quoted lines
    (each with its page), then the owner's answers."""
    out = _found(ctx)
    for p in (ctx.get("profile") or [])[:MAX_LEARNED]:
        out.append({"title": p["line"], "why": f"From {_host(p.get('source'))}", "href": "/settings/business",
                    "machine": ""})
    sells = [s.get("name") for s in ctx.get("sells") or [] if s.get("name")]
    if sells and len(out) < MAX_LEARNED:
        listed = ", ".join(sells[:4]) + (f" and {len(sells) - 4} more" if len(sells) > 4 else "")
        out.append({"title": f"You offer {listed}.", "why": "", "href": "/settings/business", "machine": ""})
    if ctx.get("customers") and len(out) < MAX_LEARNED:
        out.append({"title": f"Your customers: {ctx['customers']}.", "why": "", "href": "/settings/business",
                    "machine": ""})
    if ctx.get("area") and len(out) < MAX_LEARNED:
        out.append({"title": f"You serve {ctx['area']}.", "why": "", "href": "/settings/business", "machine": ""})
    return out[:MAX_LEARNED]


def _coming(ctx: dict, today: date) -> list[dict]:
    """Plans starting from this month to WELCOME_PLANS_MONTHS ahead, soonest first, with the weeks to get ready."""
    out = []
    for p in ctx.get("coming") or []:
        try:
            y, mth = (int(x) for x in str(p.get("month") or "").split("-"))
            start = date(y, mth, 1)
        except (TypeError, ValueError):
            continue
        months = (start.year - today.year) * 12 + start.month - today.month
        if 0 <= months <= WELCOME_PLANS_MONTHS:
            weeks = max(0, (start - today).days // 7)
            out.append({"title": f"{p['what']} starts in {start:%B}", "_at": start,
                        "why": (f"{weeks} weeks to get a page and the word ready." if weeks > 1 else
                                "It starts this month."), "href": "/settings/business", "machine": ""})
    return [{k: v for k, v in x.items() if k != "_at"} for x in sorted(out, key=lambda x: x["_at"])]


def _business(ctx: dict, today: date | None = None) -> dict:
    """What the review's AI is told about the business (#1953 step 1.1): the owner's answers on Your Business and the
    lines the full scan quoted from their own site. Empty when the box knows nothing yet, so the call is unchanged.

    WHY BOTH AND NOT `brain.knowledge_context()` AGAIN: the review's call is not isolated, so `my/knowledge/` (the
    sent-mail summary, `business-profile.md`) already rides in as its cached context. What never reached it are the
    owner's own answers, which live in box_settings. A plan whose month has passed is not a plan any more."""
    about = {k: ctx.get(k) for k in ("name", "industry", "area", "customers", "customer_kinds", "sells", "push",
                                      "stage", "team_size", "workflows") if ctx.get(k)}
    profile = [p["line"] for p in ctx.get("profile") or [] if p.get("line")][:12]
    if profile:
        about["profile"] = profile
    month = f"{today or date.today():%Y-%m}"
    plans = [{"what": p.get("what"), "month": p.get("month")} for p in ctx.get("coming") or []
             if p.get("what") and str(p.get("month") or "") >= month]
    out = {}
    if about:
        out["about"] = about
    if ctx.get("goals"):
        out["goals"] = list(ctx["goals"])
    if plans:
        out["planned_not_yet_available"] = plans
    return out


_STOP = frozenset("a an and are at be by for from has have in into is it its of on or our so that the their them "
                  "they this to today was were what when who will with would you your".split())


def _words(text: str) -> set[str]:
    """The words that carry meaning, crudely stemmed, so "reply" and "replies", "waiting" and "wait" meet."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if w in _STOP or len(w) < 3:
            continue
        for end in ("ing", "ies", "es", "s"):
            if len(w) > len(end) + 2 and w.endswith(end):
                w = w[: -len(end)] + ("y" if end == "ies" else "")
                break
        out.add(w)
    return out


def _restates(idea: dict, worth: list[dict]) -> bool:
    """Does this idea only say again what "Worth your time" already says (#1953 step 1.3)? Owner's example of the
    failure: "Reply to the 4 people waiting" under "4 people waiting". Two shared meaning-words, and most of the
    idea's title among them, is a restatement; an idea that adds something new has words of its own."""
    mine = _words(idea.get("title"))
    if not mine:
        return False
    for w in worth:
        shared = mine & _words(w.get("title"))
        if len(shared) >= 2 and len(shared) * 3 >= len(mine) * 2:
            return True
    return False


def _welcome_ai(ctx: dict, installed: list[dict], facts: dict, think=None) -> tuple[str, list[dict], list[dict]]:
    """(good news, aims, ideas) for the Welcome review, from one call, or ("", [], []) without an AI or on a bad answer.
    Every href must be an installed section's; every number must be in the facts."""
    goals = list(ctx.get("goals") or [])
    business = {k: ctx.get(k) for k in ("name", "industry", "area", "customers", "customer_kinds", "sells", "push",
                                         "stage", "team_size", "workflows") if ctx.get(k)}
    business["profile"] = [p["line"] for p in ctx.get("profile") or []][:12]
    payload = {"business": business, "goals": goals, "installed": installed, "facts": facts,
               "planned_not_yet_available": [p.get("what") for p in ctx.get("coming") or []]}
    try:
        if think is None:
            if os.environ.get("AIOS_HERMETIC_TEST"):
                return "", [], []
            from core import brain
            ready, why = brain.can_think()
            if not ready:
                log.info("review_brief.no_ai", why=why[:120])
                return "", [], []
            think = brain.think
        raw = think(AI_TASK, json.dumps(payload, ensure_ascii=False), system=_WELCOME_SYSTEM, max_tokens=900,
                    timeout=60)
        got = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception as e:                               # noqa: BLE001 — a plain welcome, on time
        log.info("review_brief.welcome_ai_skipped", why=type(e).__name__)
        return "", [], []
    facts_text = json.dumps(payload, ensure_ascii=False)
    hrefs = {i["href"] for i in installed}
    good = str(got.get("good_news") or "").strip()[:240]
    good = good if _grounded(good, facts_text) else ""
    aims = []
    by_goal = {str(a.get("goal") or "").strip().lower(): a for a in got.get("aims") or [] if isinstance(a, dict)}
    for g in goals:
        a = by_goal.get(g.lower()) or {}
        why, href = str(a.get("why") or "").strip()[:300], str(a.get("href") or "").strip()
        if href and href not in hrefs:
            href, why = "", ""                           # a screen this box doesn't have: never pointed at
        if why and not _grounded(why, facts_text):
            why = ""
        aims.append({"title": g[:1].upper() + g[1:], "why": why, "href": href, "machine": ""})
    ideas = []
    for i in got.get("ideas") or []:
        if not isinstance(i, dict):
            continue
        t, w = str(i.get("title") or "").strip()[:80], str(i.get("why") or "").strip()[:300]
        if t and _grounded(t + " " + w, facts_text):
            ideas.append({"title": t, "why": w, "href": "", "machine": ""})
    return good, aims, ideas[:MAX_IDEAS]


def _ai(facts: dict, recent_ideas: list[str], think=None, business: dict | None = None) -> tuple[str, list[dict]]:
    """("good news", [advice]) from the advisor (core/review_advisor.py, Morning Review V2 step 3): one call, three
    parts per piece, the why a line of the business's own website and the step a screen this box has. ("", []) when
    there is no AI or nothing passes its checks: the plain review, on time."""
    from core import review_advisor
    quotes = [str(q) for q in ((business or {}).get("about") or {}).get("profile") or [] if str(q).strip()]
    screens = _installed() + [{"title": "Approvals", "href": "/approvals"}]
    return review_advisor.advise(facts, business=business, quotes=quotes, screens=screens, recent=recent_ideas,
                                 think=think)


def _norm_idea(t: str) -> str:
    return re.sub(r"[^a-z ]", "", t.lower()).strip()


# ── build, store, read ──────────────────────────────────────────────────────────────────────────────────────
def _label(d: date) -> str:
    return f"{d:%A} · {d:%B} {d.day}, {d.year}"


def _morning(about: date, now: datetime) -> date:
    """The morning the brief is read, ON THE BUYER'S CLOCK (`report._send_clock`, the same answer the send uses).
    A sold box runs on UTC: at 8am in Sydney the box still says yesterday, and the date and quote must not
    (OSDev1's review of #1779). A brief about an older day is read the morning after it."""
    read = report._send_clock(now).date()
    return read if about < read <= about + timedelta(days=2) else about + timedelta(days=1)


# A PERSONAL BEST (#1953 step 1.4): one cheerful line when a headline beats its own record for the last 30 days.
# Arithmetic on the stored history (report.history), no AI. A record needs a week behind it, or a new box would break
# one every morning; it is never a zero; and only a headline whose machine says more is better is ever cheered.
BEST_DAYS = 30
BEST_MIN_DAYS = 7


def _best(rows_y: list[dict], about: date) -> str:
    """"New personal best: 23 emails sent, the most in 30 days (Lead Machine)." or "". The biggest jump over its
    old record wins when several fall on one day."""
    heads = {r.get("machine"): r for r in rows_y if isinstance(r.get("headline"), dict)}
    found = []
    for machine, pts in report.history(about, back=BEST_DAYS).items():
        r = heads.get(machine)
        if not r or (r.get("headline") or {}).get("better") != "more" or r.get("error"):
            continue
        if not pts or pts[-1]["day"] != about.isoformat():
            continue
        now_v, before = pts[-1]["value"], [p["value"] for p in pts[:-1]]
        if len(before) < BEST_MIN_DAYS or not now_v or now_v <= 0 or now_v <= max(before):
            continue
        found.append((now_v / max(max(before), 1), machine, now_v, r))
    if not found:
        return ""
    _, machine, v, r = max(found, key=lambda f: (f[0], f[2]))
    label = str(r["headline"].get("label") or "").strip()
    title = str(r.get("title") or machine).strip()
    num = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
    return f"New personal best: {num} {label}, the most in {BEST_DAYS} days ({title})." if label else ""


# YOUR WEEK, ON MONDAYS (Morning Review V2 step 2; plan §4: "On Mondays, your week: seven days against the seven
# before, and the one biggest change"). Arithmetic on report.history, no AI. A machine is in it only when its headline
# says how a week adds up (headline.week) and both weeks have most of their days stored.
WEEK_MIN_DAYS = 4


def _your_week(rows_y: list[dict], about: date) -> list[dict]:
    """[{"title": "161 emails sent", "why": "126 the week before, up 28%", "machine": "Lead Machine"}, ...], the
    biggest change first. The week is the seven days ending `about` (Sunday, read on Monday). Never raises."""
    try:
        hist = report.history(about, back=2 * WEEK_DAYS)
    except Exception:                                    # noqa: BLE001
        return []
    cut = (about - timedelta(days=WEEK_DAYS - 1)).isoformat()
    out = []
    for r in rows_y:
        head = r.get("headline") or {}
        how, label = head.get("week"), str(head.get("label") or "").strip()
        if how not in ("sum", "last") or not label or r.get("error"):
            continue
        pts = [p for p in hist.get(r.get("machine")) or [] if report.has_value(p.get("value"))]
        now_w = [p["value"] for p in pts if p["day"] >= cut]
        then_w = [p["value"] for p in pts if p["day"] < cut]
        if len(now_w) < WEEK_MIN_DAYS or len(then_w) < WEEK_MIN_DAYS:
            continue
        a, b = (sum(now_w), sum(then_w)) if how == "sum" else (now_w[-1], then_w[-1])
        if not a and not b:
            continue
        pct = round((a - b) * 100 / b) if b else None
        move = ("the same" if a == b else f"up {pct}%" if pct is not None and pct > 0 else
                f"down {-pct}%" if pct is not None else "up from none")
        num = (lambda v: f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}")
        out.append((abs(pct) if pct is not None else (1e9 if a else 0),
                    {"title": f"{num(a)} {label}", "why": f"{num(b)} the week before, {move}", "href": "",
                     "machine": str(r.get("title") or r.get("machine") or "")}))
    out.sort(key=lambda x: -x[0])
    items = [i for _, i in out]
    if items and len(items) > 1 and out[0][0] > 0:
        items[0] = {**items[0], "why": items[0]["why"] + ", the biggest change"}
    return items


def _compose(about: date, now: datetime | None, *, think=None, ai: bool = True) -> tuple[dict, dict, dict]:
    """(brief, what was seen, the idea log after it). Pure apart from the reads and the one AI call: the caller
    decides whether anything is remembered."""
    now = now or report.now_local()
    t = report.today(now)
    rows_y = [r for r in report.read(about) if r.get("machine") != report.METERS]
    rows_t_all = report.read(t)
    rows_t = [r for r in rows_t_all if r.get("machine") != report.METERS]
    meters = next((r for r in rows_t_all if r.get("machine") == report.METERS), None)
    from core import box_settings
    seen = box_settings.get(NS, SEEN, default={}) or {}
    worth, seen_after = _worth(rows_t, about, seen, meters)
    moving = _moving(rows_y)
    week = _week(rows_y, about)                                           # the last 7 days (1.2)
    # WHO TO ANSWER FIRST, BY NAME (#1953 step 1.5): as today's reporters rank them, three at most, each with the
    # parts its card draws (owner, 2026-10-06: right below the quote, "speed to lead is where the money is at").
    first = [_person(x, r) for r in rows_t for x in (r.get("answer_first") or []) if str(x.get("text") or "").strip()][:3]
    if first:                                 # SAID ONCE: "4 people waiting" is this section's own line now
        worth = [w for w in worth if not w.pop("person", False)]
    for w in worth:
        w.pop("person", None)
    idea_log = {k: v for k, v in (box_settings.get(NS, IDEAS_SEEN, default={}) or {}).items()
                if _days_since(v, about) < IDEA_DAYS}
    good, ideas = ("", [])
    if ai:
        good, ideas = _ai(_facts(rows_y, rows_t, worth, moving, week), list(idea_log), think=think,
                          business=_business(_ctx(), t))
        ideas = [i for i in ideas if _norm_idea(i["title"]) not in idea_log]
    welcome, learned, aims, coming = _is_first(about), [], [], []
    if welcome:
        ctx = _ctx()
        learned, coming = _learned(ctx), _coming(ctx, t)
        aims = [{"title": g[:1].upper() + g[1:], "why": "", "href": "", "machine": ""} for g in ctx.get("goals") or []]
        if ai:
            w_good, w_aims, w_ideas = _welcome_ai(ctx, _installed(), _facts(rows_y, rows_t, worth, moving, week),
                                                  think=think)
            good, aims = (w_good or good), (w_aims or aims)
            ideas = [i for i in w_ideas if _norm_idea(i["title"]) not in idea_log] or ideas
        if not good:
            name = str(ctx.get("name") or "").strip()
            good = (f"Welcome to your box{', ' + name if name else ''}. Here is what it knows so far, and where it "
                    "will help.")
    # NEVER THE TO-DO LIST AGAIN (1.3), and the cap comes AFTER: capped first, two restatements cost two of three slots.
    ideas = [i for i in ideas if not _restates(i, worth)][:MAX_IDEAS]
    if not good and moving:
        good = _plain_good_news(moving)                                     # plain, true, and never a zero
    try:
        best = _best(rows_y, about)
    except Exception:                            # noqa: BLE001 — a cheerful line is never the review's failure
        best = ""
    m = _morning(about, now)
    try:
        your_week = _your_week(rows_y, about) if m.weekday() == 0 else []
    except Exception:                            # noqa: BLE001 — a Monday block is never the review's failure
        your_week = []
    brief = {
        "about": about.isoformat(), "date_label": _label(m), "quote": _quote(m),
        "good_news": good, "best": best, "worth": worth, "first": first, "moving": moving, "ideas": ideas,
        "your_week": your_week,
        "numbers": _numbers(rows_y),
        "welcome": welcome, "learned": learned, "aims": aims, "coming": coming,
        "ideas_from": "ai" if ideas else "",
        "empty": not (worth or first or moving or ideas or learned or aims or coming or best or your_week),
        "link": report.page_url(about), "built_at": state._now(),
    }
    for i in ideas:
        idea_log[_norm_idea(i["title"])] = about.isoformat()
    return brief, seen_after, idea_log


# ── WHAT EACH PERSON CHOSE NOT TO SEE (#1953 step 1.6, the first of the per-person controls, §4b) ──────────────────
# Owner, 2026-10-04: "Please ensure that the morning review has the capability for per user controls, where they can
# change what they want to see." Stored per person in box_settings (which already scopes by user), applied to the page
# and to their email (OSDev1's recommendation), and never to Slack, which is everyone's. "See everything" undoes it for
# one look; "Show again" undoes it for good.
HIDDEN = "review.hidden"


def section_keys() -> tuple:
    from core import review_email
    return tuple(k for k, _ in review_email.SECTIONS) + tuple(k for k, _ in review_email.LINES)


def hidden_for(user_id) -> set:
    """The sections this person hid. Empty for nobody (a token, a send with no owner). Never raises."""
    if not user_id:
        return set()
    try:
        from core import box_settings
        got = box_settings.get(NS, HIDDEN, user_id=str(user_id), default=[]) or []
        return {k for k in got if k in section_keys()}
    except Exception:                                    # noqa: BLE001 — a preference never breaks the review
        return set()


def set_hidden(user_id, key: str, hide: bool) -> bool:
    """Hide or show one section for one person. False for no person or a section that doesn't exist."""
    if not user_id or key not in section_keys():
        return False
    from core import box_settings
    now = hidden_for(user_id)
    now = (now | {key}) if hide else (now - {key})
    box_settings.put(NS, HIDDEN, sorted(now), user_id=str(user_id), set_by=str(user_id))
    return True


def _person(x: dict, row: dict) -> dict:
    """One person to answer, as the brief stores it: the list item every surface reads, and the card's parts."""
    try:
        of = max(0, int(x.get("of") or 0))
    except (TypeError, ValueError):
        of = 0
    return {"title": str(x.get("text") or "")[:80], "why": str(x.get("why") or "")[:200],
            "href": str(x.get("href") or ""), "machine": str(row.get("title") or ""),
            "said": str(x.get("said") or "")[:160], "waited": str(x.get("waited") or "")[:12],
            "reasons": [str(c)[:40] for c in (x.get("reasons") or []) if str(c).strip()][:3],
            "ready": bool(x.get("ready")), "channel": str(x.get("channel") or "")[:30], "of": of}


def _remember(seen_after: dict, idea_log: dict) -> None:
    from core import box_settings
    box_settings.put(NS, SEEN, seen_after, set_by="morning_review")
    box_settings.put(NS, IDEAS_SEEN, idea_log, set_by="morning_review")


def build(about: date, now: datetime | None = None, *, think=None, remember: bool = True) -> dict:
    """The brief for the morning after `about`. Reads the stored rows: what happened from `about`'s, what needs
    him from today's. `remember=False` builds a preview that changes nothing and asks no AI (the page, before
    the send). The send goes through `ensure`, which stores it once."""
    brief, seen_after, idea_log = _compose(about, now, think=think, ai=remember)
    if remember:
        _remember(seen_after, idea_log)
    return brief


def _quote(d: date) -> str:
    from core import review_quotes
    return review_quotes.quote_for(d)


# THE ROW IS CLAIMED BEFORE THE AI IS ASKED (OSDev1's review of #1779: two sends at once paid for the AI twice and
# both wrote "already seen"). The claim is a placeholder in the brief's own row; whoever inserts it builds, and
# anyone else gets a preview. A claim older than STALE_CLAIM_S belongs to a build that died, and is taken over.
# EACH CLAIM IS ITS OWN (OSDev1's re-review): a build slower than STALE_CLAIM_S may have been taken over while it
# waited on the AI, so the final write lands only if the row still holds THIS claim, and only the build that
# stored the brief records what was seen.
_BUILDING = '{"building": true}'
_CLAIM_LIKE = '{"building"%'
STALE_CLAIM_S = 600


def _claim() -> str:
    import uuid
    return json.dumps({"building": uuid.uuid4().hex})


def _is_claim(text: str) -> bool:
    return str(text or "").startswith('{"building"')


def ensure(about: date, now: datetime | None = None, *, think=None) -> dict:
    """The stored brief for `about`, building and storing it the first time. Once stored it never changes, so a
    retried send, the page and the email all show the same words, and the AI is paid for once. Called by the
    send whatever the channel (email, app, Slack), so a box with only the app remembers what it has shown."""
    have = get(about)
    if have is not None:
        return have
    d, mine = about.isoformat(), _claim()
    stale = (datetime.now(timezone.utc) - timedelta(seconds=STALE_CLAIM_S)).isoformat()   # state._now()'s form
    with state.connect() as c:
        # final = 1, like every past day, so close_open_days never reopens the day for the claim.
        held = c.execute("INSERT OR IGNORE INTO daily_reports (day, machine, report_json, written_at, final) "
                         "VALUES (?, ?, ?, ?, 1)", (d, BRIEF, mine, state._now())).rowcount == 1
        if not held:
            held = c.execute("UPDATE daily_reports SET report_json = ?, written_at = ? WHERE day = ? AND machine = ? "
                             "AND report_json LIKE ? AND written_at < ?",
                             (mine, state._now(), d, BRIEF, _CLAIM_LIKE, stale)).rowcount == 1
    if not held:
        return get(about) or build(about, now, remember=False)
    try:
        brief, seen_after, idea_log = _compose(about, now, think=think)
    except Exception:
        with state.connect() as c:
            c.execute("DELETE FROM daily_reports WHERE day = ? AND machine = ? AND report_json = ?", (d, BRIEF, mine))
        raise
    with state.connect() as c:
        stored = c.execute("UPDATE daily_reports SET report_json = ?, written_at = ? WHERE day = ? AND machine = ? "
                           "AND report_json = ?",
                           (json.dumps(brief, sort_keys=True), state._now(), d, BRIEF, mine)).rowcount == 1
    if not stored:
        log.info("review_brief.claim_lost", about=d)
        return get(about) or brief
    _remember(seen_after, idea_log)
    return brief


def get(about: date | str) -> dict | None:
    """The stored brief, or None (none yet, or one still being built). Pure SQL: the page's call."""
    d = about.isoformat() if isinstance(about, date) else str(about)
    with state.connect() as c:
        row = c.execute("SELECT report_json FROM daily_reports WHERE day = ? AND machine = ?", (d, BRIEF)).fetchone()
    if not row or _is_claim(row["report_json"]):
        return None
    try:
        return json.loads(row["report_json"])
    except ValueError:
        return None


def for_page(about: date | str, now: datetime | None = None) -> dict:
    """What the page shows for `about`: the stored brief, or a preview of it (no AI, nothing remembered) for a
    morning whose review hasn't gone out yet."""
    d = date.fromisoformat(about) if isinstance(about, str) else about
    return get(d) or build(d, now, remember=False)
