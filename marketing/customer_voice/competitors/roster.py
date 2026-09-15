"""The owner-named roster, resolved once and re-read monthly.

COST IS THE DESIGN CONSTRAINT HERE, not an afterthought. Every Places call is metered against
`google_places`, the box runs to a hard monthly budget, and this rail is the only part of
Customer Voice that spends money at all. So:

  · the roster is BOUNDED — `max_roster`, and going over is a REFUSAL that names the entries it
    will not take, never a silent truncation that quietly stops watching somebody;
  · a `place_id` is resolved ONCE and reused forever, because re-searching a competitor to learn
    the id we already stored is the same money spent twice;
  · the interval is MONTHLY, because a rating moves over months and polling it hourly would buy
    the same number sixty times;
  · and a cap that is already spent is an ACTIONABLE state, not a crash and not a red light —
    the box measured `google_places` at 201 of 200 on the day this shipped.
"""
import re
from datetime import datetime, timezone

from core import state
from core.exceptions import BudgetExceeded, RetryableError, VendorError
from core.logging import get_logger
from core.vendors import places

from .. import rails

log = get_logger(__name__)

RAIL = "competitors"


def _cfg() -> dict:
    from core.config import get_config
    return dict((get_config().get("customer_voice") or {}).get("competitors") or {})


def slug_of(entry: str) -> str:
    """A stable key for a roster line. Stable is the whole requirement: it is the primary key the
    history hangs off, so re-ordering his list or fixing its capitalisation must not orphan the
    numbers already collected under it."""
    return re.sub(r"[^a-z0-9]+", "-", str(entry or "").strip().lower()).strip("-")[:60]


def configured() -> tuple[list, str | None]:
    """-> (entries, refusal). `entries` is [{slug, label, query, is_self}]; `refusal` is a
    sentence to show HIM when the roster cannot be used as written.

    A ROSTER OVER THE CAP IS REFUSED WHOLE, and the refusal names what it will not take. The
    alternative — quietly watching the first eight — means he adds a ninth competitor, sees no
    error, and never learns that the box stopped watching it. A limit nobody is told about is
    indistinguishable from a bug.
    """
    cfg = _cfg()
    raw = cfg.get("roster") or []
    if isinstance(raw, str):
        raw = [raw]
    entries = [str(x).strip() for x in raw if str(x).strip()]
    me = str(cfg.get("self") or "").strip()
    cap = int(cfg.get("max_roster", 8) or 8)
    if len(entries) > cap:
        over = entries[cap:]
        return [], (f"{len(entries)} competitors named and the limit is {cap} — "
                    f"remove {len(over)} ({', '.join(over[:3])}"
                    f"{'…' if len(over) > 3 else ''}) or raise customer_voice.competitors.max_roster")
    out = []
    if me:
        out.append({"slug": slug_of(me), "label": me, "query": me, "is_self": 1})
    for e in entries:
        s = slug_of(e)
        if s and s not in {o["slug"] for o in out}:
            out.append({"slug": s, "label": e, "query": e, "is_self": 0})
    return out, None


def _store(entry: dict, *, place_id=None, rating=None, reviews=None, error=None) -> None:
    now = state._now()
    with state.connect() as c:
        c.execute(
            "INSERT INTO voice_competitors (slug, label, query, place_id, is_self, rating, "
            "  reviews_total, checked_at, last_error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "  label = excluded.label, query = excluded.query, is_self = excluded.is_self, "
            # THE RESOLVED ID AND THE LAST GOOD NUMBERS SURVIVE A FAILED CHECK. A month when
            # Places refused is not a month this competitor ceased to exist, and blanking the
            # row would erase the comparison rather than reporting that it could not be refreshed.
            "  place_id = COALESCE(excluded.place_id, voice_competitors.place_id), "
            "  rating = COALESCE(excluded.rating, voice_competitors.rating), "
            "  reviews_total = COALESCE(excluded.reviews_total, voice_competitors.reviews_total), "
            "  checked_at = CASE WHEN excluded.last_error IS NULL THEN excluded.checked_at "
            "                    ELSE voice_competitors.checked_at END, "
            "  last_error = excluded.last_error, updated_at = excluded.updated_at",
            (entry["slug"], entry["label"], entry["query"], place_id, entry["is_self"],
             rating, reviews, now, (str(error)[:200] if error else None), now))
    if rating is not None or reviews is not None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with state.connect() as c:
            for metric, v in (("rating", rating), ("reviews", reviews)):
                if v is None:
                    continue
                c.execute(
                    "INSERT INTO voice_metrics (source, metric, day, value, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(source, metric, day) DO UPDATE SET "
                    "  value = excluded.value, updated_at = excluded.updated_at",
                    (RAIL, f"{metric}:{entry['slug']}", day, float(v), now))


def _resolve(entry: dict) -> dict:
    """One competitor, read from Places. Returns {} and records the reason on any refusal —
    this rail never raises upward, because one unreachable competitor is not a broken machine."""
    with state.connect() as c:
        row = c.execute("SELECT place_id FROM voice_competitors WHERE slug = ?",
                        (entry["slug"],)).fetchone()
    known = row["place_id"] if row else None
    try:
        if known:
            # ALREADY RESOLVED: one Details call, not a search. Cheaper and exact — a search
            # could drift onto a different business with a similar name in the same city.
            raw = places.place_details(known)
            return {"place_id": known,
                    "rating": raw.get("rating"),
                    "reviews": raw.get("userRatingCount")}
        found, _ = places.text_search(entry["query"], max_results=1)
        if not found:
            _store(entry, error="no Google listing matched that name and place")
            return {}
        p = found[0]
        return {"place_id": p.get("place_id"), "rating": p.get("rating"),
                "reviews": p.get("reviews_total")}
    except BudgetExceeded as e:
        # THE CAP IS SOMETHING HE CAN RAISE, so it is an actionable state and not a failure.
        _store(entry, error=f"Places is at its monthly cap — {str(e)[:90]}")
        raise
    except (VendorError, RetryableError) as e:
        _store(entry, error=f"{type(e).__name__}: {str(e)[:120]}")
        return {}


def check() -> dict:
    """Read every competitor once. Monthly. Never raises."""
    entries, refusal = configured()
    if refusal:
        rails.record(RAIL, error=refusal, actionable=True)
        return {"status": "refused", "error": refusal}
    if not entries:
        return {"status": "no_roster"}
    if not places.is_configured():
        msg = "set GOOGLE_PLACES_API_KEY to watch competitors"
        rails.record(RAIL, error=msg, actionable=True)
        return {"status": "unconfigured", "error": msg}
    read, failed, capped = 0, 0, False
    for e in entries:
        try:
            got = _resolve(e)
        except BudgetExceeded:
            capped = True
            break                                # stop the whole pass: the cap is box-wide
        if got:
            _store(e, place_id=got.get("place_id"), rating=got.get("rating"),
                   reviews=got.get("reviews"))
            read += 1
        else:
            failed += 1
    if capped:
        rails.record(RAIL, error="Places is at its monthly cap — raise vendors.google_places."
                                 "monthly_unit_cap or wait for the cycle to reset", actionable=True)
        return {"status": "capped", "read": read}
    rails.record(RAIL, error=(f"{failed} of {len(entries)} could not be read" if failed and not read
                              else None))
    if read:
        rails.mark_connected(RAIL)
    log.info("voice.competitors", read=read, failed=failed)
    return {"status": "ok" if read else "failed", "read": read, "failed": failed}


def min_reviews() -> int:
    """How many reviews a listing needs before its rating is COMPARABLE.

    A JUDGMENT, STATED AND CONFIGURABLE RATHER THAN HIDDEN IN A FORMULA. A 5.0 from two people
    is not ahead of a 4.9 from four hundred, and sorting on the number alone says it is — which
    would put a brand-new competitor at the top of the most-read page in the product and quietly
    teach him the ranking is nonsense.

    The honest fix is not a weighting curve I invent; it is a floor I can say out loud. Below it
    a listing is still SHOWN — dropping it would hide a real competitor — but it is not ranked,
    and the page says why. Default 5, and it is his to move.
    """
    try:
        return max(0, int(_cfg().get("min_reviews", 5) or 0))
    except (TypeError, ValueError):
        return 5


def standings() -> list:
    """The roster as the page draws it. Each row gains `ranked`: False when it has a rating but
    too few reviews for that rating to mean anything, so the page can show it and say so.

    Ranked rows first, best rating first, then the shown-but-unranked, then the unread.
    """
    floor = min_reviews()
    with state.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM voice_competitors")]
    for r in rows:
        r["ranked"] = (r.get("rating") is not None
                       and int(r.get("reviews_total") or 0) >= floor)
    rows.sort(key=lambda r: (not r["ranked"],
                             r.get("rating") is None,
                             -(r.get("rating") or 0),
                             -(r.get("reviews_total") or 0),
                             r["label"]))
    return rows


def periodic() -> dict:
    if not rails.enabled() or not rails.owns(RAIL):
        return {"status": "off"}
    return check()
