"""What people search for: the Search Console half of the Performance screen.

OWNER, 2026-09-25, in OSDev6's session: *"I'm not interested in any Page speed stuff… I like the
referring websites and maybe something about keywords an opportunities would be better than Page
speed."* OSDev1 approved the plan the same evening: Top searches, and Opportunities (average
position 4 to 20, most impressions first), read through the box's existing Search Console
connection. It uses the same read-only permission, so nobody has to connect again.

  * **Top searches**: what the site was found for and clicked on, most clicks first.
  * **Opportunities**: searches where the site shows up at position 4 to 20, most shown first.
    It is already being seen for these, and one good article can move it onto the first screen.
    For an answer-engine buyer this is the list of what to write next.

GOOGLE REPORTS LATE. Search Console's numbers settle about three days after the day they describe,
so the window is the 28 days ending 3 days ago. A window ending today would show a false drop.

READ-ONLY, AND IT NEVER REASONS. One Search Console query, no model call (CLAUDE.md
non-negotiable 3). Cached like PostHog's numbers, and a failure costs the page this card, never
the page.
"""
from __future__ import annotations

import datetime as _dt
import threading
import time

from core.logging import get_logger
from core.vendors import google_search_console as gsc

log = get_logger(__name__)

LAG_DAYS = 3
WINDOW_DAYS = 28
TOP_N = 5
OPPORTUNITY = (4.0, 20.0)       # average position, both ends included
ROWS = 250                      # enough rows that the opportunities are not only the top clicks

CACHE_S = 600
FAIL_CACHE_S = 60
_cache: dict = {}
_lock = threading.Lock()

# What each refusal means to the person reading the page. `not_connected` and `no_property` are not
# failures: the screen offers the next step instead of a sentence.
SIGNED_OUT = "Google signed this box out of Search Console. Connect it again to see your searches."
UNREACHABLE = "Google did not answer. Your searches show here again in a minute."


def window(today: _dt.date | None = None) -> tuple[str, str]:
    """(start, end) as YYYY-MM-DD: the 28 days ending LAG_DAYS ago, both ends included."""
    end = (today or _dt.date.today()) - _dt.timedelta(days=LAG_DAYS)
    return (end - _dt.timedelta(days=WINDOW_DAYS - 1)).isoformat(), end.isoformat()


def _row(r: dict) -> dict | None:
    keys = r.get("keys")
    if not isinstance(keys, list) or not keys or not str(keys[0]).strip():
        return None
    try:
        return {"query": str(keys[0]).strip(), "clicks": int(r.get("clicks") or 0),
                "impressions": int(r.get("impressions") or 0),
                "position": float(r.get("position") or 0)}
    except (TypeError, ValueError):
        return None


def searches(*, fresh: bool = False, today: _dt.date | None = None) -> dict:
    """{"ok": True, "top": [...], "opportunities": [...], "start", "end"} or {"ok": False, "why"}.

    `why` is "not_connected", "no_property", or a sentence for the page. Never raises.
    """
    st = gsc.status()
    if not st.get("connected"):
        return {"ok": False, "why": "not_connected"}
    if not st.get("property"):
        return {"ok": False, "why": "no_property"}
    start, end = window(today)
    ck = (st["property"], start, end)
    with _lock:
        hit = _cache.get(ck)
        if hit and not fresh and time.time() - hit[0] < (CACHE_S if hit[1].get("ok")
                                                         else FAIL_CACHE_S):
            return hit[1]
    out = _fetch(start, end)
    with _lock:
        _cache[ck] = (time.time(), out)
    return out


def _fetch(start: str, end: str) -> dict:
    try:
        raw = gsc.search_analytics(start, end, ("query",), ROWS)
    except gsc.Refused as e:
        log.info("aeo.searches_unavailable", reason=e.key)
        if e.key in ("not_connected", "no_property"):
            return {"ok": False, "why": e.key}
        return {"ok": False, "why": SIGNED_OUT if e.key == "signed_out" else UNREACHABLE}
    rows = [x for x in (_row(r) for r in raw) if x]
    top = sorted((r for r in rows if r["clicks"]), key=lambda r: -r["clicks"])[:TOP_N]
    lo, hi = OPPORTUNITY
    opps = sorted((r for r in rows if lo <= r["position"] <= hi),
                  key=lambda r: (-r["impressions"], r["position"]))[:TOP_N]
    return {"ok": True, "top": top, "opportunities": opps, "start": start, "end": end}


def forget() -> None:
    with _lock:
        _cache.clear()
