"""The advisor: "Advice for today" in the Morning Review (Morning Review V2 step 3, docs/PLAN_MORNING_REVIEW_ADVISOR.md §3).

Owner, 2026-10-05, on the plan: "Agree with all of your recommendations. Yes it's a go for phase 2." Decision 1: Sonnet
for the one daily advice call (config models.review). Decision 4: "Ideas to try" becomes "Advice for today".

ONE CALL A DAY, three parts per piece of advice, the way a good consultant says it:

    Saturday is your busiest day for new messages. Answer Friday's by 9 AM.
    What we saw: 11 of your 27 new messages last week came in on a Friday or Saturday.
    Why it matters for you: "Saturday appointments book out a week ahead."
    Today: answer the people who wrote on Friday.                         -> the Inbox

THE MODEL ANSWERS THE TWO PARTS THAT MUST BE TRUE WITH NUMBERS, NEVER WORDS. "Why it matters for you" is the number of a
line of the business's own profile (core/business_context.py, quoted from its website), so the quote is word for word
by construction; the step's screen is the number of a screen this box has, so the link is never dead. The rest is
checked before it is shown (§6, each one a test): every number in it is a number in the facts it was handed (`grounded`);
it never leads with a problem; it adds something the to-do list doesn't already say. Advice that fails any check is
dropped, and a morning with none left shows none, on time. Nothing here sends anything or acts: the advice links to a
screen, and a person decides.

PRIVACY: the model is handed counts, times, the box's own figures and the business's own website lines. Never a
customer's name or words (the review's "Who to answer first" is drawn on the owner's page, not handed to the model).
"""
from __future__ import annotations

import json
import os
import re

from core.logging import get_logger

log = get_logger(__name__)

TASK = "review"                  # config models.review -> sonnet (decision 1)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# LIGHT AND OPTIMISTIC (§6.6): advice that leads with one of these is dropped. Short and owned on purpose.
PROBLEM_WORDS = frozenset({"failing", "bad", "worst", "losing"})

SYSTEM = (
    "You are the advisor in a small business owner's morning review. Write one warm, optimistic sentence about what "
    "went well yesterday, and two or three pieces of advice for today. Each piece of advice has: "
    "'title', the advice itself in under 70 characters; "
    "'saw', one sentence on what the numbers showed, naming a number from the facts; "
    "'because', the number of the profile line that makes it matter for this business, or null when none fits; "
    "'today', one concrete step to take today, in one sentence; "
    "'screen', the number of the screen where they take that step. "
    "Use ONLY the facts given: never invent a number, a name, a price or a competitor. "
    "THE WEEK: 'last_7_days' gives each headline by weekday, oldest first, the last being yesterday; notice a streak "
    "or a change across the week rather than judging one day alone. "
    "WHO THE BUSINESS IS: when a 'business' block and 'profile' lines are given, every piece of advice must fit that "
    "business, its offers, its customers and its goals (in the order given), so a med spa never gets a gym's advice. "
    "A planned offer may be prepared for, never described as available yet. "
    "NEVER RESTATE THE TO-DO LIST: the 'worth_their_time_today' lines are already shown above your advice, so a piece "
    "must add something new, never repeat or rephrase one of them, nor one in 'already_suggested'. "
    "Never scold and never lead with a problem. Plain, friendly words; no jargon, no exclamation marks. "
    "Reply with JSON only: "
    "{\"good_news\": \"…\", \"advice\": [{\"title\": \"…\", \"saw\": \"…\", \"because\": number or null, "
    "\"today\": \"…\", \"screen\": number}]}.")


def grounded(text: str, facts_text: str) -> bool:
    """Every number in a line appears in the facts it was handed (§6.1)."""
    have = {n.replace(",", "") for n in _NUM.findall(facts_text)}
    return all(n.replace(",", "") in have for n in _NUM.findall(text))


def leads_with_a_problem(text: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", str(text or "").lower())) & PROBLEM_WORDS)


def _pick(n, items: list):
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    return items[i - 1] if 1 <= i <= len(items) else None


def check(got: dict, *, facts_text: str, quotes: list[str], screens: list[dict]) -> tuple[str, list[dict]]:
    """The model's answer -> (good news, advice items) after every check (§6). Never raises."""
    good = str((got or {}).get("good_news") or "").strip()[:240]
    if not grounded(good, facts_text) or leads_with_a_problem(good):
        good = ""
    out = []
    for a in (got or {}).get("advice") or []:
        if not isinstance(a, dict):
            continue
        title = " ".join(str(a.get("title") or "").split())[:90]
        saw = " ".join(str(a.get("saw") or "").split())[:300]
        today = " ".join(str(a.get("today") or "").split())[:240]
        if not (title and saw and today):
            continue
        if not grounded(f"{title} {saw} {today}", facts_text):          # §6.1 and §6.4: no invented number or price
            continue
        if leads_with_a_problem(title):                                  # §6.6
            continue
        quote = _pick(a.get("because"), quotes)
        if quotes and quote is None:                                     # §6.2: the why is the business's own line
            continue
        screen = _pick(a.get("screen"), screens)
        if screen is None:                                               # §6.3: never a dead link
            continue
        out.append({"title": title, "why": saw, "href": screen["href"], "machine": "",
                    "saw": saw, "because": quote or "", "today": today, "where": screen["title"]})
    return good, out                         # capped by the caller, after restatements go (step 1.3)


def advise(facts: dict, *, business: dict | None, quotes: list[str], screens: list[dict], recent: list[str],
           think=None) -> tuple[str, list[dict]]:
    """("good news", [advice]) from ONE call, or ("", []) when there is no AI, nothing to go on, or the answer fails.

    `quotes` are the business's own profile lines; `screens` are [{title, href}] this box has. Never raises: a review
    with no advice is the plain review, on time."""
    if not (facts.get("what_happened_yesterday") or facts.get("worth_their_time_today") or facts.get("figures")):
        return "", []
    if not screens:
        return "", []
    payload = {"facts": facts, "already_suggested": recent[:20],
               "profile": [{"n": i, "line": q} for i, q in enumerate(quotes, 1)],
               "screens": [{"n": i, "title": s["title"]} for i, s in enumerate(screens, 1)]}
    if business:
        payload["business"] = business
    try:
        if think is None:
            if os.environ.get("AIOS_HERMETIC_TEST"):                     # a test never spends; it passes `think`
                return "", []
            from core import brain
            ready, why = brain.can_think()
            if not ready:
                log.info("review_advisor.no_ai", why=why[:120])
                return "", []
            think = brain.think
        # A BOUNDED WAIT: this runs in the worker's send tick; a review a minute late with no advice beats one that
        # holds every other periodic.
        raw = think(TASK, json.dumps(payload, ensure_ascii=False), system=SYSTEM, max_tokens=900, timeout=90)
        got = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception as e:                                               # noqa: BLE001 — no advice, still on time
        log.info("review_advisor.skipped", why=type(e).__name__)
        return "", []
    # THE OWNER'S OWN WORDS ARE FACTS TOO: a price on Your Business, a number in a line of their own website.
    facts_text = json.dumps({"facts": facts, "business": business or {}, "profile": quotes}, ensure_ascii=False)
    return check(got if isinstance(got, dict) else {}, facts_text=facts_text, quotes=quotes, screens=screens)


def parts(item: dict) -> list[tuple[str, str]]:
    """An advice item's three labelled parts, for every surface: [(label, text)], the empty ones left out."""
    out = [("What we saw", str(item.get("saw") or "")),
           ("Why it matters for you", f"“{item['because']}”" if item.get("because") else ""),
           ("Today", str(item.get("today") or ""))]
    return [(k, v) for k, v in out if v]
