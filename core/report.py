"""The Morning Review — one report per machine per day, computed once, stored, rendered twice.

WHY THE PAGE NEVER COMPUTES (docs/PLAN_MORNING_REVIEW.md §1.1). The worker and the web process
do not import the same modules: the worker loads `modules:` (eleven of them), dispatch loads
`web_modules:` (four). A reporter registered at machine import therefore exists in the worker
and NOT in the process that serves the page, so an import-time registry would have shown
numbers in the 8 AM message and blanks on the page for the same machine. That is the failure the
spec calls unrecoverable — a dashboard contradicting itself — and it has shipped here before
(core/dispatch.py `_load_packs` exists because of it).

So:

    worker ──periodic──▶ compute report ──▶ daily_reports table
                                                 ├──▶ 8 AM DM   (worker reads the table)
                                                 └──▶ page      (web reads the table, ONLY)

`register_reporter` + `snapshot` run in the worker. `read`/`days` are pure SQL and are the only
calls the page makes; this module imports no machine, ever (tests/test_morning_review.py scans
for it). Today is a row like any other, rewritten every fifteen minutes while `final = 0`; a
closed day cannot match the UPSERT's WHERE, so history is immutable by construction rather than
by two code paths agreeing to be careful (§2.2).

Owner, 2026-09-09: "a crucial feature that every business owner will want to receive. Every
single morning." The default is every day; `review.send_days` is the single value that
ratchets it back.
"""
import json
import pathlib
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core import state
from core.config import get_config, settings
from core.logging import get_logger

log = get_logger(__name__)

_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = "my/review.json"                 # {"sent", "emailed"}: one once-a-day marker per channel; "retry_at": the attempt lease
SEND_TICK_S = 300                        # review_send LOOKS this often; run() says why it is not hourly
RETRY_S = 3600                           # ...and ATTEMPTS at most once in this long

# THE SEGMENT ORDER IS FIXED, whatever is installed. Owner: Customer Voice, then Content, then
# Lead. A box with only the Content Machine still shows Content in the middle; fixed position
# is what lets an owner learn where to look. `meters` is not a segment — it is the rail at the
# bottom that absorbed the cost digest (§1.7), stored with the day so a past day keeps its meters.
#
# THESE THREE ARE THE OWNER'S RULING AND THEY DO NOT MOVE. OSDev1 asked for the order to come
# from the box's recipe (#1332 step 2); measured, the recipe would put them Content, Lead,
# Customer Voice — a complete reversal of the line above. Reordering the morning page a buyer
# has learned to read, on the way past, is not something to do while carrying out a different
# instruction. So the ruling stands here and the question goes back to him; what changes below
# is only what happens to a machine the owner never ruled on.
RULED = ("customer_voice", "content_machine", "lead_machine")
ORDER = RULED                            # kept as a name: three modules read it
METERS = "meters"
WATCH_STATES = ("ok", "warn", "fail", "connect")
STALE_AFTER_S = 45 * 60                  # three refresh intervals (§2.2b) — older is "no report since"
REFRESH_S = 15 * 60

# BOUNDS, because a report is a row in a database and a message on a phone, and a machine having
# a bad day must not be able to write either one out of usefulness. A reporter that returns four
# hundred `needs_you` items is not telling anyone anything — it is a wall of text with a scroll
# bar. Truncation is VISIBLE: the extra items become one "+N more" line, never a silent drop.
MAX_ITEMS = 12                           # per rail, per machine
MAX_TEXT = 240                           # one item's text
SLACK_LIMIT = 3900                       # chat.postMessage caps at 4000; leave room for the tail

REPORTERS: dict[str, dict] = {}


def _cfg() -> dict:
    return dict(get_config().get("review") or {})


def tz():
    """ONE timezone key for the whole box (§1.8): `cost.timezone`, the one the cost ledger and
    the digest already read. A second key is how a Tuesday report gets labelled Wednesday."""
    return ZoneInfo((get_config().get("cost") or {}).get("timezone", "UTC"))


def now_local() -> datetime:
    return datetime.now(tz())


def today(now: datetime | None = None) -> date:
    return (now or now_local()).astimezone(tz()).date()


def window(day: date) -> tuple[str, str]:
    """The ISO-8601 UTC bounds of one local day, for reporters counting rows by timestamp.
    Every table stores UTC (`state._now()`), so a local day is a UTC half-open interval."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz())
    return (start.astimezone(ZoneInfo("UTC")).isoformat(),
            (start + timedelta(days=1)).astimezone(ZoneInfo("UTC")).isoformat())


# ── the registry ──────────────────────────────────────────────────────────────────────

def register_reporter(machine: str, title: str, fn) -> None:
    """fn(day: date) -> Report. Local DB reads only. Never raises. Never slow. NOT the same
    registry as `register_periodic` (§1.4): that one takes a zero-arg callable; this one takes
    a day. Idempotent on machine so a re-import (tests) cannot stack two."""
    # A NEW MACHINE MAY REPORT ON THE DAY IT LANDS. This used to raise for any machine not in the
    # three above, so the AI receptionist arriving in two weeks could put nothing on the morning
    # page or the dashboard until somebody hand-edited core — the arm-per-product growth
    # `test_core_boundary` exists to stop, and the reason the registry was built at all.
    #
    # THE KEY IS STILL CHECKED, just not against a list of products: a machine name is a short
    # lowercase slug, so a typo or a path fragment is still refused at import where it is a failed
    # boot line rather than a missing segment nobody notices.
    if machine != METERS and not _KEY.match(str(machine or "")):
        raise ValueError(f"machine {machine!r} must be a short lowercase slug, "
                         f"like {RULED[0]!r}")
    REPORTERS[machine] = {"title": title, "fn": fn}
    log.info("report.registered", machine=machine)


def _plain(v):
    """JSON-safe, or the row is lost. A reporter returning a `date`, a `Decimal` or a sqlite3.Row
    would raise inside `json.dumps` — and that raise used to happen INSIDE the shared transaction,
    so ONE careless machine took down every other machine's row for the day. Coerced here, once,
    where the damage is a stringified value instead of an empty dashboard."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    return str(v)


def _item(x, extra_keys=()) -> dict:
    """One rail item, bounded and JSON-safe. Unknown keys are kept (a reporter may carry an `href`
    or an `id` the page wants) but every value is coerced and text is cut at MAX_TEXT."""
    x = dict(x or {})
    out = {}
    for k, v in x.items():
        v = _plain(v)
        out[str(k)] = (v[:MAX_TEXT] + "…") if isinstance(v, str) and len(v) > MAX_TEXT else v
    return out


def _bounded(items, label: str) -> list:
    """MAX_ITEMS, then one visible line saying how many were left out. A silent truncation is a
    number that quietly stops being true, which is the one thing this whole feature exists to
    avoid."""
    items = [_item(x) for x in (items or [])]
    if len(items) <= MAX_ITEMS:
        return items
    rest = len(items) - MAX_ITEMS
    return items[:MAX_ITEMS] + [{"text": f"+{rest} more {label}", "truncated": True}]


def _normalize(machine: str, title: str, rep) -> dict:
    """The one shape (§2.4), defaults filled, states bounded, every value JSON-safe. A reporter
    that returns garbage still yields a row the page can draw, because every list is a list and
    every state is one of four. Zero stays zero — nothing here drops a row for being empty."""
    rep = dict(rep or {})
    head = dict(rep.get("headline") or {})
    watch = []
    for w in _bounded(rep.get("watch"), "checks"):
        w["state"] = w.get("state") if w.get("state") in WATCH_STATES else "warn"
        watch.append(w)
    return {
        "machine": machine,
        "title": str(rep.get("title") or title)[:80],
        "headline": {"value": _plain(head.get("value", 0)), "label": str(head.get("label") or "")[:80],
                     "delta": _plain(head.get("delta"))},
        "needs_you": _bounded(rep.get("needs_you"), "waiting"),
        "happened": _bounded(rep.get("happened"), "outcomes"),
        "watch": watch,
        "notes": [str(x)[:MAX_TEXT] for x in (rep.get("notes") or [])][:MAX_ITEMS],
        # EVERY NUMBER THE PAGE COULD WANT, ALREADY COMPUTED AND NAMED. OSDev5 renders `figures`
        # without deciding what anything means: {key: {"value": …, "label": …, "href": …}}.
        "figures": {str(k): _item(v) for k, v in (rep.get("figures") or {}).items()},
    }


def _meters_report(day: date) -> dict:
    """The rail that absorbed the cost digest (§1.7): the same meters, from the same guard the
    vendors are charged against, stored with the day."""
    from core import cost_digest, cost_guard
    at = datetime.combine(day, datetime.min.time(), tzinfo=tz()) + timedelta(hours=23, minutes=59)
    at = min(at, now_local())
    rows = cost_digest.meters(at)
    happened = [{"text": f"{r['vendor']} {r['used']:.0f} of {r['cap']:.0f}",
                 "value": f"{r['pct']:.0f}%"} for r in rows]
    watch = []
    for r in rows:
        if r["cap"] and r["used"] >= r["cap"]:
            watch.append({"text": f"{r['vendor']} is AT CAP — calls are refused until the cycle resets",
                          "state": "fail"})
        elif r["pct"] >= cost_digest.WARN_PCT:
            watch.append({"text": f"{r['vendor']} is at {r['pct']:.0f}% of its cap", "state": "warn"})
    usd = None
    try:
        usd = {"spend": float(cost_guard.month_to_date_spend(at)), "ceiling": float(cost_guard.ceiling())}
    except Exception as e:  # noqa: BLE001 — the meters still land if the USD line cannot
        log.warning("report.usd_line_failed", error=type(e).__name__)
    return {"title": "Meters",
            "headline": {"value": f"${usd['spend']:.0f}" if usd else "—",
                         "label": f"of ${usd['ceiling']:.0f} this cycle" if usd else "USD unknown"},
            "happened": happened, "watch": watch}


REPORTERS[METERS] = {"title": "Meters", "fn": _meters_report}


# ── storage ───────────────────────────────────────────────────────────────────────────

_UPSERT = """
INSERT INTO daily_reports (day, machine, report_json, written_at, final)
VALUES (?,?,?,?,0)
ON CONFLICT(day, machine) DO UPDATE SET
  report_json = excluded.report_json,
  written_at  = excluded.written_at
WHERE daily_reports.final = 0
"""


def _previous_headline(c, day: date, machine: str):
    row = c.execute("SELECT report_json FROM daily_reports WHERE day = ? AND machine = ?",
                    ((day - timedelta(days=1)).isoformat(), machine)).fetchone()
    if not row:
        return None
    try:
        v = (json.loads(row["report_json"]).get("headline") or {}).get("value")
        return v if isinstance(v, (int, float)) else None
    except (ValueError, AttributeError):
        return None


def snapshot(day: date | None = None) -> dict:
    """Run every registered reporter for `day` and UPSERT the rows. Worker only. One statement
    per row, and the WHERE carries the guarantee: a closed day cannot be rewritten (§2.2).

    A reporter that raises yields ONE `could not report` row and every other machine still
    lands (§1.11 — a source that failed must not look like a source with nothing to say)."""
    day = day or today()
    written_at = state._now()
    out = {"day": day.isoformat(), "written": [], "failed": []}
    with state.connect() as c:
        for machine, r in list(REPORTERS.items()):
            try:
                rep = _normalize(machine, r["title"], r["fn"](day))
                if rep["headline"].get("delta") is None and isinstance(rep["headline"]["value"], (int, float)):
                    prev = _previous_headline(c, day, machine)
                    rep["headline"]["delta"] = (rep["headline"]["value"] - prev) if prev is not None else None
            except Exception as e:  # noqa: BLE001 — one bad machine is one line, never the page
                log.warning("report.reporter_failed", machine=machine, error=type(e).__name__)
                rep = _normalize(machine, r["title"], {"title": r["title"]})
                rep["error"] = f"could not report ({type(e).__name__})"
                out["failed"].append(machine)
            try:
                blob = json.dumps(rep, sort_keys=True)
            except (TypeError, ValueError) as e:   # _plain should make this unreachable; prove it
                log.warning("report.unserializable", machine=machine, error=type(e).__name__)
                blob = json.dumps(_normalize(machine, r["title"], {"title": r["title"]})
                                  | {"error": "report could not be stored"}, sort_keys=True)
                out["failed"].append(machine)
            c.execute(_UPSERT, (day.isoformat(), machine, blob, written_at))
            out["written"].append(machine)
    return out


def close_open_days(now: datetime | None = None) -> list[str]:
    """Any row whose day is before today and still `final = 0` gets one last snapshot and is
    closed — NOT a midnight window (§2.3): a box that was down across midnight would otherwise
    leave yesterday mutable forever. Idempotent; a closed day is untouched."""
    t = today(now).isoformat()
    with state.connect() as c:
        open_days = [r["day"] for r in c.execute(
            "SELECT DISTINCT day FROM daily_reports WHERE day < ? AND final = 0 ORDER BY day", (t,))]
    for d in open_days:
        snapshot(date.fromisoformat(d))
        with state.connect() as c:
            c.execute("UPDATE daily_reports SET final = 1 WHERE day = ? AND final = 0", (d,))
        log.info("report.day_closed", day=d)
    return open_days


def _recipe_rank() -> dict:
    """Where each machine sits in this box's recipe — `{machine: index}`.

    THE RECIPE IS ALREADY THE LIST OF MACHINES ON THE BOX, so nothing new has to be maintained:
    a machine that is installed appears in `modules:`, and one that is not, does not. The entries
    are dotted module paths (`marketing.customer_voice.inbox`), so a machine's rank is where its
    key FIRST appears as a component — the inbox and the reviews side of Customer Voice are two
    modules and one machine.

    Matched on components, never on substring: `marketing.lead_machine` must not rank a machine
    called `lead` and `content_machine.voice` must not rank one called `voice`.
    """
    seen: dict = {}
    for i, path in enumerate(get_config().get("modules") or ()):
        for part in str(path).split("."):
            seen.setdefault(part, i)
    return seen


def _segment_rank(rep) -> tuple:
    """Sort key: the owner's three first and in his order, then every other machine in the order
    the recipe installs it, then meters, then anything we cannot place at all.

    A MACHINE THE OWNER NEVER RULED ON GETS A PLACE RATHER THAN A GUESS. Before this it sorted to
    99 with everything else, so two new machines had no defined order between them and the page
    could reshuffle between reads.

    FOUR TIERS, AND THE LAST TWO USED TO BE THE WRONG WAY ROUND — caught by OSDev1 reviewing this
    PR against the sentence above. An unplaceable machine took a huge rank INSIDE the recipe tier,
    so it sorted ahead of meters while the docstring promised it came after. Both orders are
    arguable; a docstring that disagrees with its own function is not, and that is the defect.

    THE LAST TIER IS A FALLBACK BUCKET AND BELONGS AT THE BOTTOM. Reaching it means a machine
    registered a reporter without appearing in `modules:` — which is a misconfiguration, not a
    fourth product. Sorting it in among the installed machines is how a box that is wrong looks
    like a box that is fine.
    """
    machine = str((rep or {}).get("machine") or "")
    if machine in RULED:
        return (0, RULED.index(machine), machine)
    if machine == METERS:
        return (2, 0, machine)
    rank = _recipe_rank().get(machine)
    if rank is None:
        # ITS OWN TIER, NOT A BIG NUMBER IN SOMEBODY ELSE'S. `10 ** 6` was a sentinel doing a
        # tier's job, and a sentinel inside a tier can only ever sort within it.
        return (3, 0, machine)
    return (1, rank, machine)


def read(day: date | str, machine: str | None = None) -> list[dict]:
    """Stored rows for a day, in segment ORDER with meters last. THE ONLY CALL THE PAGE MAKES.
    Pure SQL — no reporter runs, no machine is imported. Each dict carries `written_at` and
    `final` beside the report so the page can refuse a stale one (§2.2b)."""
    d = day.isoformat() if isinstance(day, date) else str(day)
    with state.connect() as c:
        if machine:
            rows = c.execute("SELECT * FROM daily_reports WHERE day = ? AND machine = ?", (d, machine)).fetchall()
        else:
            rows = c.execute("SELECT * FROM daily_reports WHERE day = ?", (d,)).fetchall()
    out = []
    for r in rows:
        try:
            rep = json.loads(r["report_json"])
        except ValueError:
            rep = {"machine": r["machine"], "title": r["machine"], "error": "unreadable report"}
        rep["written_at"] = r["written_at"]
        rep["final"] = bool(r["final"])
        out.append(rep)
    out.sort(key=_segment_rank)
    return out


def days() -> list[str]:
    """Every day with a stored report, newest first. THE DROPDOWN IS THIS LIST — nothing generates
    a date range, so no day can be offered that has nothing behind it."""
    with state.connect() as c:
        return [r["day"] for r in c.execute("SELECT DISTINCT day FROM daily_reports ORDER BY day DESC")]


HISTORY_DAYS = 30                        # a month reads as a trend; a week reads as noise


def history(day: date | str | None = None, back: int = HISTORY_DAYS) -> dict:
    """`{machine: [{"day", "value"}, ...]}` — the STORED headline for each of the last `back`
    days, oldest first, up to and including `day`. ONE query for every machine. Pure SQL.

    WHY THIS IS HERE AND NOT IN THE PAGE. OSDev5, 2026-09-09, asking rather than building it:
    the owner wants charts, `view()` returns one day, and drawing a trend in the renderer means
    `days()` plus `read()` plus a date loop in the WEB process — which is precisely what the
    storage seam exists to stop (§1.1). A number the page has to compute is a number the page
    can get wrong on its own. So the series is computed once, here, and the sparkline is SVG.

    ONLY NUMERIC HEADLINES APPEAR. The meters headline is "$11" — a string, deliberately, because
    it is money with a currency on it — and a chart of strings is a chart of nothing. A machine
    whose headline is not a number gets an empty list, which the page renders as no chart rather
    than as a flat line at zero. Those two look identical and mean opposite things.

    DAYS WITH NO ROW ARE ABSENT, not zero. A box that was off on Sunday did not do nothing on
    Sunday; it did not report. The caller draws a gap.
    """
    d = day.isoformat() if isinstance(day, date) else str(day or today().isoformat())
    try:
        end = date.fromisoformat(d)
    except ValueError:
        end = today()
    start = (end - timedelta(days=max(1, int(back)) - 1)).isoformat()
    out: dict[str, list] = {}
    with state.connect() as c:
        rows = c.execute(
            "SELECT day, machine, report_json FROM daily_reports "
            "WHERE day >= ? AND day <= ? ORDER BY day ASC", (start, end.isoformat())).fetchall()
    for r in rows:
        try:
            v = (json.loads(r["report_json"]).get("headline") or {}).get("value")
        except (ValueError, AttributeError):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue                            # bool is an int in Python; it is not a headline
        out.setdefault(r["machine"], []).append({"day": r["day"], "value": v})
    return out


def view(day: date | str | None = None, now: datetime | None = None) -> dict:
    """EVERYTHING THE PAGE NEEDS, IN ONE CALL. Pure SQL; imports no machine; decides nothing the
    renderer would otherwise have to decide.

    Added 2026-09-09 on the owner's instruction — *"make the key data available so that OSDev5 can
    wire the data into the page without thinking too hard"*. Before this the page had to call
    `read`, call `days`, work out whether the day was today, work out whether the newest
    `written_at` was too old, and know that meters are a row rather than a segment. Four chances
    to get it subtly wrong on a surface whose entire value is being trusted.

    Returns:
        {
          "day":       "YYYY-MM-DD",          the day being shown
          "label":     "Wed, Sep 9",          human, in cost.timezone
          "live":      True|False,            today, still moving
          "final":     True|False,            the day is closed
          "exists":    True|False,            False -> render the empty state, NOT zeros
          "stale":     None | "HH:MM",        set only when today's newest row is too old
          "as_of":     "HH:MM",               when the newest row was written, local
          "days":      [{"day","label","live"} …],   the picker, newest first, Live first
          "needs":     N,                     needs_you across every segment
          "segments":  [report, …],           fixed order, only machines that reported;
                                              each carries `history` -> [{"day","value"}],
                                              oldest first, numeric headlines only, gaps absent
          "meters":    report | None,
          "history_days": N,                  how far back a segment's `history` reaches
          "empty_line": "…"                   the sentence to show when `exists` is False
        }
    """
    now = now or datetime.now(ZoneInfo("UTC"))
    t = today(now)
    d = (day.isoformat() if isinstance(day, date) else str(day or t.isoformat())).strip()
    try:
        date.fromisoformat(d)
    except ValueError:
        d = t.isoformat()
    rows = read(d)
    stored = days()
    live = d == t.isoformat()
    # EVERY MACHINE'S ROW IS A SEGMENT. This read `in ORDER`, so a stored report from a machine
    # not in the ruled three was silently dropped from the page that exists to show it.
    segs = [r for r in rows if r.get("machine") and r.get("machine") != METERS]
    meters = next((r for r in rows if r.get("machine") == METERS), None)
    newest = max((r.get("written_at") or "" for r in rows), default="")
    stale_at = is_stale(rows, now) if live else None
    picker = [{"day": t.isoformat(), "label": day_label(t.isoformat()), "live": True}]
    picker += [{"day": x, "label": day_label(x), "live": False} for x in stored if x != t.isoformat()]
    series = history(d)
    for r in segs:
        # ALREADY ORDERED, oldest first, and the last point is this day when there is one. The
        # page draws it; it never asks for it.
        r["history"] = series.get(r.get("machine"), [])
    if meters is not None:
        meters["history"] = series.get(METERS, [])
    return {
        "history_days": HISTORY_DAYS,
        "day": d, "label": day_label(d), "live": live,
        "final": bool(rows) and all(r.get("final") for r in rows),
        "exists": bool(rows),
        "stale": local_hm(stale_at) if stale_at else None,
        "as_of": local_hm(newest) if newest else "",
        "days": picker,
        "needs": sum(len(r.get("needs_you") or []) for r in segs if not r.get("error")),
        "segments": segs,
        "meters": meters,
        "empty_line": ("No report yet — the first one is written within fifteen minutes of the "
                       "machine starting, and every day from then on is kept here." if live else
                       f"No report was written for {day_label(d)}. The picker lists every day "
                       f"that has one."),
    }


def day_label(d: str) -> str:
    """`2026-09-09` -> `Wed, Sep 9`. Here and not in the page, so the picker, the heading and the
    message cannot label the same day three ways."""
    try:
        return date.fromisoformat(d).strftime("%a, %b %-d")
    except ValueError:
        return d


def local_hm(iso: str) -> str:
    """A UTC timestamp as the clock on the wall behind the owner (cost.timezone)."""
    try:
        return datetime.fromisoformat(iso).astimezone(tz()).strftime("%H:%M")
    except (ValueError, TypeError):
        return str(iso or "")


def is_stale(rows: list[dict], now: datetime | None = None) -> str | None:
    """For TODAY's rows: the `written_at` of the freshest row, if it is older than three refresh
    intervals — the page renders that instead of numbers (§2.2b). Stale-and-labelled is
    survivable; stale-and-confident is not. None means fresh (or final, which cannot be stale)."""
    if not rows or all(r.get("final") for r in rows):
        return None
    newest = max(r.get("written_at") or "" for r in rows)
    try:
        age = ((now or datetime.now(ZoneInfo("UTC"))) - datetime.fromisoformat(newest)).total_seconds()
    except ValueError:
        return newest
    return newest if age > STALE_AFTER_S else None


# ── the 8 AM message ──────────────────────────────────────────────────────────────────

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _state_path() -> pathlib.Path:
    return ROOT / STATE


def _read_state() -> dict:
    p = _state_path()
    try:
        d = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        d = {}
    d.setdefault("sent", "")
    return d


def _write_state(d: dict) -> None:
    p = _state_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, sort_keys=True))


def _leased(st: dict, now: datetime) -> bool:
    """True while the last attempt's hour is still running. A missing, unreadable or
    incomparable lease is NO lease: the worst case is one early retry, never a lost morning."""
    try:
        return now < datetime.fromisoformat(str(st.get("retry_at") or ""))
    except (TypeError, ValueError):
        return False


def send_days() -> set[str]:
    raw = _cfg().get("send_days")
    if not raw:
        return set(_DAYS)
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return {str(x).strip().lower()[:3] for x in raw} & set(_DAYS)


def page_url(day: date | str) -> str:
    base = (getattr(settings, "dashboard_base_url", "") or "").rstrip("/")
    d = day.isoformat() if isinstance(day, date) else str(day)
    return f"{base}/app/review/{d}"


def _fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:.0f}"
    return str(v)


def render(day: date, now: datetime | None = None) -> str:
    """The message: Needs you → Yesterday → Watch → Meters (spec §2.4). A phone, at breakfast,
    in eight seconds; urgency first. The PAGE orders by machine instead, and the divergence is
    deliberate — somebody will one day 'fix' one to match the other, and the fix is the bug.

    `day` is the day the message is ABOUT (yesterday, closed); needs_you and watch are read from
    today's row, which `run` refreshes right before sending."""
    now = now or now_local()
    yday = read(day)
    tday = read(today(now))
    segs = lambda rows: [r for r in rows                                     # noqa: E731
                         if r.get("machine") and r.get("machine") != METERS]
    needs = [(r["title"], n) for r in segs(tday) for n in r.get("needs_you") or []]
    lines = [f"Morning review, {now:%a %d %b}"]
    if needs:
        lines.append(f"Needs you ({len(needs)})")
        lines += [f"  • {n.get('text', '')}  [{t}]" for t, n in needs]
    else:
        lines.append("Nothing needs you this morning.")
    lines.append(f"Yesterday, {day:%a %d %b}")
    for r in segs(yday):
        if r.get("error"):
            lines.append(f"  {r['title']}: ⚠ {r['error']}")
            continue
        h = r.get("headline") or {}
        delta = h.get("delta")
        dtxt = f" ({'+' if delta > 0 else ''}{_fmt_value(delta)} vs the day before)" if isinstance(delta, (int, float)) and delta else ""
        lines.append(f"  {r['title']}: {_fmt_value(h.get('value', 0))} {h.get('label', '')}{dtxt}".rstrip())
        for x in r.get("happened") or []:
            v = x.get("value")
            lines.append(f"    · {x.get('text', '')}" + (f" — {_fmt_value(v)}" if v not in (None, "") else ""))
    if not segs(yday):
        lines.append("  (no machine reported yesterday — the first full day lands tomorrow)")
    # METERS ARE IN THE WATCH LIST, not only in the rail at the bottom. SMOKETESTED against the
    # live box 2026-09-09: google_places read 201 of 200 — AT CAP, every further call refused —
    # and the message did not say so anywhere, because this list was built from the SEGMENTS and
    # meters is not a segment. The one line the owner most needs at breakfast was the one line
    # the digest-absorption dropped. Failures first, because a cap that has already bitten
    # outranks a warning about one that might.
    watch = [(r["title"], w) for r in tday for w in r.get("watch") or [] if w.get("state") != "ok"]
    watch.sort(key=lambda tw: {"fail": 0, "warn": 1, "connect": 2}.get(tw[1].get("state"), 3))
    if watch:
        lines.append("Watch")
        lines += [f"  {'!!' if w.get('state') == 'fail' else '→' if w.get('state') == 'connect' else ' !'} "
                  f"{w.get('text', '')}  [{t}]" for t, w in watch]
    meters = next((r for r in tday if r.get("machine") == METERS), None)
    if meters and not meters.get("error"):
        h = meters.get("headline") or {}
        lines.append(f"Meters  {_fmt_value(h.get('value', ''))} {h.get('label', '')}".rstrip())
        lines += [f"  {x.get('text', '')} ({x.get('value', '')})" for x in meters.get("happened") or []]
    lines.append(page_url(day))
    text = "\n".join(lines)
    if len(text) > SLACK_LIMIT:
        # chat.postMessage REFUSES over 4000 characters, so an unbounded digest is not a long
        # message — it is NO message, on the morning it had the most to say. Cut at a line
        # boundary and keep the link, which is the whole point of the last line.
        link = lines[-1]
        keep = text[: SLACK_LIMIT - len(link) - 40].rsplit("\n", 1)[0]
        text = keep + "\n… (trimmed — the page has all of it)\n" + link
    return text


def _send_clock(now: datetime) -> datetime:
    """`now` on the clock of the person being told — the buyer's, not the box's.

    A SOLD BOX SHIPS ON UTC (`cost.timezone`), so "8am" on the box's clock is 1am for a Pacific
    buyer: a notification that wakes them in the night. `notify.buyer_timezone()` is the same
    answer the inbox notices already use — the claim-time browser zone, else the config — so the
    two things that tell the owner something agree on when morning is. On the operator's box the
    two clocks are the same, so nothing there moves.
    """
    try:
        from core import notify
        return now.astimezone(ZoneInfo(notify.buyer_timezone()))
    except Exception:  # noqa: BLE001 — an unreadable zone is the box's clock, not a crash
        return now


def _owner_id() -> str:
    try:
        return str((state.owner_user() or {}).get("id") or "")
    except Exception:  # noqa: BLE001
        return ""


def _owner_devices(owner: str) -> list:
    """The owner's devices with the app installed. The review is owner-only, so only theirs."""
    if not owner:
        return []
    try:
        from core import push
        return push.subscriptions_for(owner)
    except Exception:  # noqa: BLE001 — no table, no crypto: no device
        return []


def _owner_email(owner: str) -> str:
    """The owner's own sign-in address, when the box has a way to send and it can receive mail."""
    try:
        from core import box_mail
        if not owner or not box_mail.is_configured():
            return ""
        people = box_mail.to_box_people(owner)
        return people[0]["email"] if people else ""
    except Exception:  # noqa: BLE001
        return ""


def _app(devices: list, about: date) -> str:
    """Tell each of the owner's devices the review is ready. -> "sent" | "failed". Never raises.

    ONE FIXED SENTENCE, NEVER A FIGURE. The notification renders on a locked screen; what the day
    held belongs behind the login it opens.
    """
    from core import push
    got = 0
    for sub in devices:
        try:
            ok, _why = push.send(sub, title="Morning Review", body="Your morning review is ready",
                                 navigate=f"/app/review/{about.isoformat()}")
            got += 1 if ok else 0
        except Exception:  # noqa: BLE001
            pass
    return "sent" if got else "failed"


def run(now: datetime | None = None, send=None, send_email=None, send_app=None) -> dict:
    """The send periodic. Once a day at or after `review.hour_local` (default 8) on a
    `review.send_days` day — a box asleep at 08:00 sends when it wakes rather than skipping (§1.7,
    the cost_digest shape, copied rather than reinvented). Refreshes today's row first so
    needs_you is current, and closes any open past day so yesterday is final before it is sent.

    IT LOOKS EVERY `SEND_TICK_S` AND ATTEMPTS AT MOST ONCE PER `RETRY_S`. A periodic's phase is the
    worker's last restart, so an hourly tick sent "the 8am review" at whatever minute the last
    deploy happened: measured 2026-09-10, a restart at 07:46:26 UTC would have put it at 08:46
    Pacific, and the day before it went at 08:13. Looking every five minutes lands it by 08:05.
    The attempt lease is written BEFORE sending, so a Slack send that raises (and may have
    delivered) is not retried five minutes later as a second DM; a failed channel waits an hour.

    THE EMAIL (owner, 2026-09-10: "Wish it was an email with the 3 sections") goes beside the DM
    when `review.email_to` is set: the same once-a-day gate, its own marker. A Slack outage never
    costs the email, and a mail outage never re-sends the DM; the next tick retries only the
    channel that failed. Either channel alone is enough to run; neither is `no_operator`.

    THE APP IS THE THIRD CHANNEL, AND ON A SOLD BOX THE FIRST (owner, 2026-09-23: "Go with C"). A
    sold box has no Slack and, deliberately, no email of ours; until the owner adds their own on
    /settings/email, the review would reach nobody. So the owner's devices with the app installed
    are told "Your morning review is ready", and the tap opens the review itself. With email set up
    on the box, the review is ALSO mailed to the owner's own sign-in address when `email_to` is not
    configured. Each channel keeps its own marker, as the two above always have.

    THE HOUR AND THE DAY ARE THE OWNER'S (`_send_clock`), and so are the markers — a marker keyed
    on the box's UTC date would roll over in a Pacific afternoon and send the morning twice."""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return {"status": "off"}
    operator = getattr(settings, "operator_slack_user_id", "") or ""
    owner = _owner_id()
    email_to = str(cfg.get("email_to") or "").strip() or _owner_email(owner)
    devices = _owner_devices(owner) if send_app is None else ["injected"]
    if not operator and not email_to and not devices:
        return {"status": "no_operator"}
    if send is None and operator:
        from core import slack
        send = lambda text: slack.send_dm(operator, text)     # noqa: E731
    now = now or now_local()
    t = today(now)
    clock = _send_clock(now)
    mark = clock.date().isoformat()
    st = _read_state()
    want_dm = bool(operator) and st.get("sent") != mark
    want_mail = bool(email_to) and st.get("emailed") != mark
    want_app = bool(devices) and st.get("pushed") != mark
    if clock.hour < int(cfg.get("hour_local", 8)) or not (want_dm or want_mail or want_app):
        return {"status": "quiet"}
    if _DAYS[clock.weekday()] not in send_days():
        return {"status": "not_a_send_day"}
    if _leased(st, now):
        return {"status": "retry_later", "retry_at": st.get("retry_at")}
    st["retry_at"] = (now + timedelta(seconds=RETRY_S)).isoformat(timespec="seconds")
    _write_state(st)
    close_open_days(now)
    snapshot(t)
    yday = t - timedelta(days=1)
    out = {"status": "send_failed", "about": yday.isoformat()}
    if want_dm:
        if send(render(yday, now)):
            st["sent"] = mark
            out["dm"] = "sent"
            log.info("report.sent", day=t.isoformat(), about=yday.isoformat())
        else:
            out["dm"] = "send_failed"
    if want_mail:
        out["email"] = _email(email_to, yday, now, t, send_email)
        if out["email"] == "sent":
            st["emailed"] = mark
    if want_app:
        out["app"] = send_app(yday) if send_app is not None else _app(devices, yday)
        if out["app"] == "sent":
            st["pushed"] = mark
            log.info("report.app_notified", day=t.isoformat(), about=yday.isoformat())
    _write_state(st)
    if "sent" in (out.get("dm"), out.get("email"), out.get("app")):
        out["status"] = "sent"
    return out


def _email(to: str, yday: date, now: datetime, t: date, send_email=None) -> str:
    """The email half of `run`. -> "sent" | "failed:<Kind>". Never raises: a mail failure must not
    cost the DM, its marker or the worker. The idempotency key is per day and recipient, so a
    retry after a timeout that DID deliver cannot deliver the same morning twice."""
    try:
        from core import review_email
        e = review_email.build(yday, now)
        sender = send_email or review_email.send
        sender(to, e["subject"], review_email.text(e), review_email.html(e),
               idem_key=f"morning-review:{t.isoformat()}:{to.lower()}")
    except Exception as ex:  # noqa: BLE001
        log.warning("report.email_failed", day=t.isoformat(), error=f"{type(ex).__name__}: {str(ex)[:160]}")
        return f"failed:{type(ex).__name__}"
    log.info("report.emailed", day=t.isoformat(), about=yday.isoformat())
    return "sent"

def refresh() -> dict:
    """The 15-minute periodic: today's row, rewritten. `beat=` on the registration means a
    stalled worker AGES this heartbeat and the watchdog pages on it (§2.2b)."""
    out = snapshot(today())
    return {"status": "ok" if not out["failed"] else f"failed:{','.join(out['failed'])}", **out}


def close() -> dict:
    closed = close_open_days()
    return {"status": f"closed:{len(closed)}" if closed else "ok", "closed": closed}


try:
    from core.worker import register_periodic
    register_periodic(refresh, interval_s=REFRESH_S, name="review_snapshot", beat="review_snapshot")
    register_periodic(close, interval_s=3600, name="review_close")
    register_periodic(run, interval_s=SEND_TICK_S, name="review_send", beat="review_send")
except Exception as e:  # noqa: BLE001 — importable without a worker (dispatch, tests, scripts)
    log.info("report.not_registered", why=type(e).__name__)
