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

from core import report
from core.connector import tools
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
    if seat.get("role") == "read":
        kept = [r for r in rows if r.get("machine") not in _OWNER_ONLY_SEGMENTS]
        if len(kept) != len(rows):
            # NAMED, not silently dropped. A caller that cannot tell a withheld field from an
            # absent one will report the absence as a fact.
            withheld = [f"{m}:role_read" for m in sorted(_OWNER_ONLY_SEGMENTS)]
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
    if not report.days():
        return _not_started()
    back = max(1, min(int(back), 365))        # a caller asking for ten years gets a year
    series = report.history(day, back=back)
    return {
        "days_back": back,
        "series": series,
        "note": "days with no stored report are absent rather than zero; a gap is not a zero",
    }


tools.register(
    "report_day",
    fn=report_day, wants_seat=True, machine=MACHINE, min_role="read",
    capability="read:reports",
    description="One day's Morning Review as stored, with its freshness. Defaults to the most "
                "recent stored day. The meters (spend) segment is withheld from a read seat.",
    args={"day": {"type": "string", "required": False,
                  "description": "ISO date, e.g. 2026-09-12. Omit for the latest stored day."}},
)

tools.register(
    "report_days",
    fn=report_days, machine=MACHINE, min_role="read",
    capability="read:reports",
    description="Every day that has a stored Morning Review, newest first.",
)

tools.register(
    "report_trend",
    fn=report_trend, machine=MACHINE, min_role="read",
    capability="read:reports",
    description="The stored headline per machine over recent days, oldest first. Days with no "
                "report are absent rather than zero.",
    args={"day": {"type": "string", "required": False,
                  "description": "ISO date to end the window on. Omit for today."},
          "back": {"type": "integer", "required": False,
                   "description": "How many days back, 1-365. Defaults to 30."}},
)
