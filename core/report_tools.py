"""The Morning Review's three questions, offered to the connector.

TIER 1 (docs/PLAN_AIOS_CONNECTOR.md section 6 step 4): the day's report, the days that have one,
and the thirty-day trend. Almost no new logic, and that is the point — the value here is proving
credential -> role -> validation -> audit row -> JSON end to end on functions that cannot
themselves be wrong.

THIS FILE IS ALSO THE PROOF OF THE REGISTRY'S CENTRAL CLAIM. Section 11.5 says a machine registers
the questions it can answer and the connector serves whatever registered, so adding answers needs
no edit to the connector. Three tools land here and core/connector/manifest.py and
core/connector/http.py are untouched by this change. If that had required editing them, the
registry was a closed tuple wearing a registry's clothes.

PURE SQL IN THE REQUEST, NO MACHINE IMPORTED. core/report.py's own docstring records why that
matters: the worker and the web process do not load the same modules, so a reporter registered at
machine import exists in one and not the other, and an import-time registry once showed numbers
in the 8 AM message and blanks on the page for the same machine. `read`, `days` and `history` are
the three calls that are pure SQL against stored rows, which is exactly why these three are Tier
1 and nothing else is.

TWO THINGS ARE DELIBERATE AND NEITHER IS COSMETIC.

  FRESHNESS IS ON EVERY ANSWER. A six-hour-old snapshot must not read identically to a quiet day.
  Every response carries `written_at` and an explicit `stale` flag past three refresh intervals.
  Stale-and-labelled is survivable; stale-and-confident is not.

  THE MONEY RAIL IS WITHHELD FROM A `read` SEAT, and withheld LOUDLY. It is not returned empty
  and it is not masked: `withheld: ["meters:role_read"]` says the field exists and this seat may
  not see it. An empty meters section would be read as "nothing was spent", which is a different
  and much worse sentence than "you may not see this."
"""
from datetime import datetime, timezone

import re

from core import report
from core.connector import prompts, tools
from core.connector import words as say
from core.logging import get_logger

log = get_logger(__name__)

MACHINE = "morning_review"

# Withheld from a read seat. It is the box's SPEND — the one number in the report that is nobody's
# business but the owner's, and the plan names it explicitly.
_OWNER_ONLY_SEGMENTS = {report.METERS}


def _not_started():
    """No report has ever been stored. Present but not running is not the same as a quiet day."""
    return tools.NotConfigured(
        "no Morning Review has been stored on this box yet — the worker's daily snapshot has "
        "not run, so there is nothing to read")


def _stale(rows) -> tuple[bool, str | None]:
    """(stale, written_at) for a set of stored rows, using the report module's own threshold."""
    if not rows:
        return False, None
    newest = max((r.get("written_at") or "") for r in rows) or None
    if not newest:
        return False, None
    if all(r.get("final") for r in rows):
        return False, newest              # a closed day cannot go stale; it is finished
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds()
    except ValueError:
        return True, newest               # an unparseable timestamp is not evidence of freshness
    return age > report.STALE_AFTER_S, newest


def report_day(day: str | None = None, *, seat: dict):
    """One day's Morning Review, as stored. Meters withheld from a read seat."""
    known = report.days()
    if not known:
        return _not_started()
    d = day or known[0]
    rows = report.read(d)
    stale, written_at = _stale(rows)

    withheld = []
    # BY CAPABILITY, not by role. A coworker's run seat is minted `act` and holds only what it was
    # granted, so asking "is this a read seat" would hand the meters to a coworker allowed to read
    # the report and nothing about spend. A read seat does not hold read:spend either, so for every
    # seat that existed before run seats, this is the same answer as before.
    if "read:spend" not in tools.held(seat):
        kept = [r for r in rows if r.get("machine") not in _OWNER_ONLY_SEGMENTS]
        if len(kept) != len(rows):
            # NAMED, not silently dropped. A caller that cannot tell a withheld field from an
            # absent one will report the absence as a fact. `role_read` is what a read seat has
            # always been told; a run seat is told the capability it was not granted.
            why = "role_read" if seat.get("role") == "read" else "not_granted_read_spend"
            withheld = [f"{m}:{why}" for m in sorted(_OWNER_ONLY_SEGMENTS)]
        rows = kept

    return {
        "day": d,
        "written_at": written_at,
        "stale": stale,
        "final": bool(rows) and all(r.get("final") for r in rows),
        "segments": rows,
        "withheld": withheld,
        # Said out loud so a caller never has to infer it from an empty list.
        "note": None if rows else f"no Morning Review was stored for {d}",
    }


def report_days():
    """Every day that HAS a stored report, newest first.

    Nothing generates a date range, so no day can be offered that has nothing behind it. A caller
    picking from this list cannot ask for a day that will come back empty.
    """
    known = report.days()
    if not known:
        return _not_started()
    return {"days": known, "count": len(known)}


def report_trend(day: str | None = None, back: int = report.HISTORY_DAYS):
    """The stored headline per machine over the last `back` days, oldest first.

    DAYS WITH NO ROW ARE ABSENT, NOT ZERO, and that is carried through from report.history()
    rather than filled in here: a box that was off on Sunday did not do nothing on Sunday, it
    did not report. Those two look identical on a chart and mean opposite things.

    Only numeric headlines appear. The meters headline is a currency string by design, so it has
    no series — which also means this tool leaks no spend and needs no role check.
    """
    known = report.days()
    if not known:
        return _not_started()
    back = max(1, min(int(back), 365))        # a caller asking for ten years gets a year
    series = report.history(day, back=back)
    return {
        "days_back": back,
        "series": series,
        # WHAT EACH SERIES IS, in the words the review itself uses ("Unified Inbox", "articles published"), from
        # the newest stored day. A series keyed by a machine's slug is a number nobody can name.
        "titles": _titles(known[0]),
        "note": "days with no stored report are absent rather than zero; a gap is not a zero",
    }


def _titles(day: str) -> dict:
    try:
        return {r["machine"]: {"title": str(r.get("title") or ""),
                               "label": str((r.get("headline") or {}).get("label") or "")}
                for r in report.read(day) if r.get("machine")}
    except Exception as e:                    # noqa: BLE001 — the series still answers without names
        log.warning("report_tools.titles_unreadable", error=type(e).__name__)
        return {}


# ── the answers in words (core/connector/words.py) ─────────────────────────────────────────────────
# Owner, 2026-10-02, asking his own AI for this review and getting its stored fields back: *"This is not an AI
# business machine. This is a dumb box."* So the review now answers the way its page reads: what needs him first,
# every machine's together, then what happened, machine by machine, with the full link to each page, how fresh it
# is, and what to ask next. Nothing here knows what a machine's numbers mean: a machine says what its segment lets
# him ask and start (`words.review_offers`), beside its own tools.

_ZERO_BUDGET = re.compile(r"\b0 of 0\b")


def _item_line(x: dict) -> str:
    """One `happened` line, as the page draws it: the number, then the words. "" for a line with nothing in it."""
    text = str(x.get("text") or "").strip()
    if not text:
        return ""
    v = x.get("value")
    if v in (None, ""):
        return say.plain(text)
    if not report.has_value(v):
        return ""                                      # "0 messages came in" is not a line (owner, 2026-09-29)
    return say.plain(f"{say.n(v)} {text}")


def _with_link(text: str, href) -> str:
    return f"{text}: {say.link(href)}" if href else text


def _segment(r: dict) -> str:
    """One machine's part: what happened, where things stand, what is worth watching. "" when it says nothing."""
    title = str(r.get("title") or r.get("machine") or "").replace("_", " ").strip() or "Your box"
    if r.get("error"):
        return f"{title}\n- Could not be read for this day."
    said = {say.first_number(n.get("text")) for n in (r.get("needs_you") or [])}
    lines = []
    for x in r.get("happened") or []:
        line = _item_line(x) if isinstance(x, dict) else ""
        if line:
            said.add(say.first_number(line))
            lines.append(line)                         # unlinked, as the page draws a happened line
    h = r.get("headline") or {}
    if not lines and report.has_value(h.get("value")) and str(h.get("label") or "").strip():
        # A HEADLINE ONLY WHEN NOTHING ELSE IS SAID: beside its own lines it repeats them in a vaguer way.
        lines.append(f"{say.n(h['value'])} {h['label']}")
        said.add(say.first_number(lines[-1]))
    for f in (r.get("figures") or {}).values():
        if not isinstance(f, dict) or not report.has_value(f.get("value")):
            continue
        if say.first_number(f.get("value")) in said and say.first_number(f.get("value")):
            continue                                   # already said, in a needs-you or a happened line
        label = say.plain(f.get("label") or "")
        if label:
            lines.append(f"{label}: {say.n(f['value'])}")
    for w in r.get("watch") or []:
        if not isinstance(w, dict) or w.get("state") == "ok" or not str(w.get("text") or "").strip():
            continue
        num = say.first_number(w.get("text"))
        if num and num in said:
            continue
        lines.append(_with_link(say.plain(w["text"]), w.get("href")))
    lines += [say.plain(x) for x in (r.get("notes") or []) if str(x or "").strip()]
    body = say.bullets(lines)
    return f"{title}\n{body}" if body else ""


def _spending(r: dict) -> str:
    """The meters, for a seat that holds read:spend: only what was used, never a 0-of-0 budget."""
    if r.get("error"):
        return "Spending\n- Could not be read for this day."
    lines = []
    h = r.get("headline") or {}
    if report.has_value(h.get("value")):
        lines.append(f"Spent {h['value']} {str(h.get('label') or '').strip()}".strip())
    for x in r.get("happened") or []:
        text = str((x or {}).get("text") or "").strip()
        if not text or _ZERO_BUDGET.search(text) or not report.has_value(x.get("value")):
            continue
        lines.append(f"{text} ({x['value']})")
    for w in r.get("watch") or []:
        if isinstance(w, dict) and w.get("state") != "ok" and str(w.get("text") or "").strip():
            lines.append(say.plain(w["text"]))
    body = say.bullets(lines)
    return f"Spending\n{body}" if body else ""


def render_day(r: dict) -> str:
    d = str(r.get("day") or "")
    today = report.today().isoformat()
    head = (f"Today's Morning Review ({say.day_words(d)})." if d == today else f"Morning Review for {say.day_words(d)}.")
    fresh = ("This day is closed, so these are its final numbers." if r.get("final")
             else say.as_of(r.get("written_at"), bool(r.get("stale"))))
    segs = [s for s in (r.get("segments") or []) if isinstance(s, dict)]
    machines = [s for s in segs if s.get("machine") != report.METERS]
    meters = next((s for s in segs if s.get("machine") == report.METERS), None)

    # NEEDS YOU FIRST, EVERY MACHINE'S TOGETHER: the reason he opens the review (core/dash/review.py `_needs`).
    needs = []
    for s in machines:
        if s.get("error"):
            continue
        for it in s.get("needs_you") or []:
            text = say.plain((it or {}).get("text"))
            if text:
                needs.append(_with_link(text, it.get("href")))
    parts = [f"{head} {fresh}".strip(),
             say.section("Needs you:", needs) if needs else "Nothing needs you right now."]
    happened = [b for b in (_segment(s) for s in machines) if b]
    if happened:
        parts.append("What happened:\n" + "\n".join(happened))
    if meters is not None:
        parts.append(_spending(meters))
    if any(str(w).startswith(f"{report.METERS}:") for w in r.get("withheld") or []):
        parts.append("Spending is hidden from this connection.")
    if not segs and r.get("note"):
        parts.append(f"No Morning Review was stored for {say.day_words(d)}.")
    if d:
        parts.append(f"The full review: {say.link(f'/app/review/{d}')}")

    asks, starts = [], []
    for s in machines:
        got = say.offers_for(s)
        asks += got["ask"]
        starts += got["start"]
    asks += [(f"{MACHINE}.report_trend", "How have these numbers changed over the last month?"),
             (f"{MACHINE}.report_days", "Which other days can I look at?"),
             ("core.health", "Is my box running?")]
    return say.answer(*parts, say.ask_next(*asks), say.can_start(*starts))


def render_days(r: dict) -> str:
    days = [str(x) for x in (r.get("days") or [])]
    if not days:
        return say.answer("No Morning Review is stored on this box yet.", say.ask_next(
            ("core.health", "Is my box running?")))
    span = (f"from {say.day_words(days[-1])} to {say.day_words(days[0])}" if len(days) > 1
            else f"for {say.day_words(days[0])}")
    return say.answer(
        f"Your box has a Morning Review for {say.plural(len(days), 'day')}, {span}.",
        f"The newest, {say.day_words(days[0])}: {say.link(f'/app/review/{days[0]}')}",
        "Recent days: " + "; ".join(say.day_words(x) for x in days[:7]) + ".",
        say.ask_next((f"{MACHINE}.report_day", f"What happened on {say.day_words(days[0])}?"),
                     (f"{MACHINE}.report_trend", "How have my numbers changed this month?")))


def render_trend(r: dict) -> str:
    series = r.get("series") or {}
    names = r.get("titles") or {}
    lines = []
    for machine in sorted(series):
        pts = [p for p in series.get(machine) or [] if isinstance(p, dict)]
        if not pts:
            continue
        meta = names.get(machine) or {}
        title = meta.get("title") or str(machine).replace("_", " ").title()
        label = f", {meta['label']}" if meta.get("label") else ""
        last, first = pts[-1], pts[0]
        line = f"{title}{label}: {say.n(last['value'])} on {say.day_words(last['day'])}"
        if len(pts) > 1:
            line += f", from {say.n(first['value'])} on {say.day_words(first['day'])}"
        peak = max(pts, key=lambda p: p["value"])
        if len(pts) > 2 and peak is not last and peak["value"] != last["value"]:
            line += f"; highest {say.n(peak['value'])} on {say.day_words(peak['day'])}"
        lines.append(line + ".")
    back = r.get("days_back") or report.HISTORY_DAYS
    body = (say.section(f"Over the last {say.plural(back, 'day')}:", lines) if lines
            else f"No numbers are stored for the last {say.plural(back, 'day')} yet.")
    return say.answer(body, "Days with no review are left out, never counted as zero.", say.ask_next(
        (f"{MACHINE}.report_day", "What happened on my box yesterday?"),
        (f"{MACHINE}.report_days", "Which days can I look at?")))


tools.register(
    "report_day",
    title="Read a day's Morning Review",
    fn=report_day, wants_seat=True, machine=MACHINE, min_role="read", render=render_day,
    capability="read:reports",
    description="One day's Morning Review as stored, with its freshness. Defaults to the most "
                "recent stored day. The meters (spend) segment is withheld from a read seat.",
    args={"day": {"type": "string", "required": False,
                  "description": "ISO date, e.g. 2026-09-12. Omit for the latest stored day."}},
)

tools.register(
    "report_days",
    title="See which days have a Morning Review",
    fn=report_days, machine=MACHINE, min_role="read", render=render_days,
    capability="read:reports",
    description="Every day that has a stored Morning Review, newest first.",
)

tools.register(
    "report_trend",
    title="See how your Morning Review numbers change",
    fn=report_trend, machine=MACHINE, min_role="read", render=render_trend,
    capability="read:reports",
    description="The stored headline per machine over recent days, oldest first. Days with no "
                "report are absent rather than zero.",
    args={"day": {"type": "string", "required": False,
                  "description": "ISO date to end the window on. Omit for today."},
          "back": {"type": "integer", "required": False,
                   "description": "How many days back, 1-365. Defaults to 30."}},
)


# ── READY-MADE ASKS (core/connector/prompts.py; the owner's ruling D3, 2026-10-02) ───────────────────
# The three that read the review first. Machines add their own tools to them with `prompts.use` beside their
# tools (the inbox's waiting replies, the AEO Machine's articles), so this file names none of them. The box's own
# brief and its own AI answer them whole when this box has them (OSDev4 builds both): preferred by name, looked
# up when the ask is made, so they are used the day they register.
_BOX_AI = ("core.brief", "core.ask")

prompts.register(
    "morning_brief", title="Morning brief", machine=MACHINE, prefer=_BOX_AI,
    description="Today's Morning Review, thought through: the few things that matter, why, and what to do.",
    ask="Give me my morning brief from my box: the two or three things that matter most today, why each one "
        "matters, and what I should do about it.",
    uses=[(f"{MACHINE}.report_day", "today's Morning Review: what needs me first, then what happened on each "
                                    "part of the box, with the link to each page")])

prompts.register(
    "what_needs_me", title="What needs me today", machine=MACHINE, prefer=_BOX_AI,
    description="Only what is waiting on you, most important first, and what each one needs you to do.",
    ask="What needs me today? Only what is waiting on me, most important first, and what each one needs me to do.",
    uses=[(f"{MACHINE}.report_day", "the review's Needs you list, every part of the box's together")])

prompts.register(
    "websites", title="How are my websites doing", machine=MACHINE, prefer=_BOX_AI[1:],
    description="Visits from people on each of your sites this week, against the week before, and what moved them.",
    ask="How are my websites doing this week? Tell me which site is pulling, which is quiet, and why, where the "
        "box can see it.",
    uses=[(f"{MACHINE}.report_day", "the review's website lines: visits from people this week, per site, against "
                                    "the week before")])
