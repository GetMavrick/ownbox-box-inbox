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


def tz_name() -> str:
    """THE BOX'S ONE TIME ZONE, by name (plan #1857 H9). Every sold box shipped `cost.timezone: UTC`, so the owner's
    review read "As of 1:57 AM" while the claim form had caught his browser's zone and kept it unused. In order:
      1. the owner's choice in Settings (box setting `core.timezone`);
      2. the zone the buyer's browser gave when they claimed the box (`box_claim.timezone`);
      3. config `cost.timezone`, a deliberate value on a hand-built box;
      4. UTC.
    One resolver for the whole box (§1.8): the review, "As of", the brief, the cost ledger and the digest all ask it.
    A second key is how a Tuesday report gets labelled Wednesday."""
    def _ok(name) -> str:
        name = str(name or "").strip()
        try:
            return name if name and ZoneInfo(name) else ""
        except Exception:                                # noqa: BLE001 — an unknown zone is no zone
            return ""
    try:
        from core import box_settings
        chosen = _ok(box_settings.get(TZ_NS, TZ_KEY))
    except Exception:                                    # noqa: BLE001 — a database too old for settings
        chosen = ""
    if chosen:
        return chosen
    try:
        from core import claim
        claimed = _ok((claim.claimed() or {}).get("timezone"))
    except Exception:                                    # noqa: BLE001
        claimed = ""
    return claimed or _ok((get_config().get("cost") or {}).get("timezone")) or "UTC"


TZ_NS, TZ_KEY = "core", "timezone"


def tz():
    """The box's one time zone (tz_name), as a ZoneInfo."""
    return ZoneInfo(tz_name())


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


# ONE IS NOT PLURAL (owner, 2026-10-04, before an investor demo: the live review read "1 messages came in, 1 people
# wrote for the first time"). A reporter writes its noun once, for every count; this agrees it with a count of one,
# here, so the page, the email, Slack and the AI's answers all read it right without each reporter counting.
_ONE = {"people": "person", "children": "child", "men": "man", "women": "woman"}


def _one(text: str) -> str:
    """The text for a count of one: the first plural noun in its first two words made singular. "messages came in"
    -> "message came in", "first emails sent" -> "first email sent", "people wrote" -> "person wrote"."""
    words = str(text or "").split(" ")
    for i, w in enumerate(words[:2]):
        low = w.lower()
        if low in _ONE:
            words[i] = _ONE[low]
            break
        if len(low) > 3 and low[:1].isalpha() and low.replace("-", "").isalpha() and low.endswith("s") \
                and not low.endswith(("ss", "us", "is")):
            words[i] = w[:-3] + "y" if low.endswith("ies") and len(low) > 4 else w[:-1]
            break
    return " ".join(words)


def _agree(item: dict, value_key: str = "value", text_key: str = "text") -> dict:
    v = item.get(value_key)
    t = item.get(text_key)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 1 and isinstance(t, str) and t[:1].islower():
        item[text_key] = _one(t)
    return item


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
        "headline": _agree({"value": _plain(head.get("value", 0)), "label": str(head.get("label") or "")[:80],
                            "delta": _plain(head.get("delta"))}, text_key="label"),
        "needs_you": _bounded(rep.get("needs_you"), "waiting"),
        "happened": [_agree(x) for x in _bounded(rep.get("happened"), "outcomes")],
        "watch": watch,
        "notes": [str(x)[:MAX_TEXT] for x in (rep.get("notes") or [])][:MAX_ITEMS],
        # EVERY NUMBER THE PAGE COULD WANT, ALREADY COMPUTED AND NAMED. OSDev5 renders `figures`
        # without deciding what anything means: {key: {"value": …, "label": …, "href": …}}.
        "figures": {str(k): _agree(_item(v), text_key="label") for k, v in (rep.get("figures") or {}).items()},
    }


# VENDORS AS A PERSON READS THEM (owner, 2026-09-29, via WebDev2: the review listed "apollo" and
# "mev_leads"). Two meters can share one account, so the name says which. A vendor not listed here
# reads as its key, spaced and capitalised, never as a config key.
_VENDOR_NAMES = {
    "heygen": "HeyGen", "scrapecreators": "ScrapeCreators", "mev_wallet": "MyEmailVerifier",
    "email_verify": "MyEmailVerifier (inbox checks)", "mev_leads": "MyEmailVerifier (lead checks)",
    "google_places": "Google Places", "prospeo": "Prospeo", "apollo": "Apollo", "hunter": "Hunter",
    "tomba": "Tomba", "resend": "Resend",
}


def vendor_name(key) -> str:
    return _VENDOR_NAMES.get(str(key)) or str(key).replace("_", " ").strip().title()[:40]


def meter_pct(used, cap) -> str:
    """How full a meter is, in words that never round use away. "9 of 3,000" read "0%" on the owner's review
    (OSDev1, 2026-10-02), which says nothing was used and hid the line on the page (has_value("0%") is False). So
    any use under 1% is "<1%", a meter short of its cap never reads "100%", and an uncapped meter says so."""
    used, cap = float(used or 0), float(cap or 0)
    if not cap:
        return "no cap"
    p = 100.0 * used / cap
    if 0 < p < 1:
        return "<1%"
    return f"{min(p, 99.0) if used < cap else p:.0f}%"


def _meters_report(day: date) -> dict:
    """The rail that absorbed the cost digest (§1.7): the same meters, from the same guard the
    vendors are charged against, stored with the day.

    ONLY A METER IN USE THIS CYCLE IS A LINE. The owner's AI read his review aloud on 2026-10-02 and it listed every
    vendor in the shipped config: "Instantly 0 of 0", "MyEmailVerifier 0 of 0", "Tomba 0 of 1000", eleven lines and
    one of them real (OSDev1's assignment, scope #1839). His 2026-09-29 ruling already covers it: "If a line doesn't
    have data, it should not be displayed." The page filtered them; the stored row, the email and MCP did not, so
    the rule lives here, once. A cap with no use is not news, and a buyer's box ships caps for vendors he never
    signed up for. The at-cap and near-cap checks need use, so nothing they say is lost."""
    from core import cost_digest, cost_guard
    at = datetime.combine(day, datetime.min.time(), tzinfo=tz()) + timedelta(hours=23, minutes=59)
    at = min(at, now_local())
    rows = [r for r in cost_digest.meters(at) if float(r.get("used") or 0) > 0]
    happened = [{"text": f"{vendor_name(r['vendor'])} {r['used']:,.0f} of {r['cap']:,.0f}" if r["cap"]
                 else f"{vendor_name(r['vendor'])} {r['used']:,.0f} used",
                 "value": meter_pct(r["used"], r["cap"])} for r in rows]
    watch = []
    for r in rows:
        if r["cap"] and r["used"] >= r["cap"]:
            watch.append({"text": f"{vendor_name(r['vendor'])} is AT CAP — calls are refused until the cycle resets",
                          "state": "fail", "vendor": vendor_name(r["vendor"]), "pct": round(float(r["pct"]))})
        elif r["pct"] >= cost_digest.WARN_PCT:
            watch.append({"text": f"{vendor_name(r['vendor'])} is at {r['pct']:.0f}% of its cap", "state": "warn",
                          "vendor": vendor_name(r["vendor"]), "pct": round(float(r["pct"]))})
    usd = None
    try:
        usd = {"spend": float(cost_guard.month_to_date_spend(at)), "ceiling": float(cost_guard.ceiling())}
    except Exception as e:  # noqa: BLE001 — the meters still land if the USD line cannot
        log.warning("report.usd_line_failed", error=type(e).__name__)
    return {"title": "Meters",
            "headline": {"value": f"${usd['spend']:.0f}" if usd else "—",
                         "label": f"of ${usd['ceiling']:.0f} this cycle" if usd else "USD unknown"},
            "happened": happened, "watch": watch,
            # THE TWO NUMBERS THE MORNING BRIEF WEIGHS (core/review_brief.py: spend over half the ceiling).
            "figures": ({"spend": {"value": round(usd["spend"], 2), "label": "Spent this cycle"},
                         "ceiling": {"value": round(usd["ceiling"], 2), "label": "Monthly ceiling"}} if usd else {})
            | ai_figures(day, at)}


# WHAT THE BOX'S AI DID, BY TASK (plan #1857 H7): Meters showed vendors and dollars, and a box thinking on the
# buyer's own subscription spends no dollars, so it showed nothing of where the AI went. Counts, never cost.
_AI_TASKS = {"inbox_draft": "Replies drafted", "brief": "Today's brief", "ask": "Questions answered",
             "review": "Morning Review", "router": "AI checks", "written_seed": "Articles planned"}


def ai_figures(day: date, at: datetime) -> dict:
    """{"ai:<task>": {"value": calls that day, "label", "cycle": calls this cycle}}, from the spend ledger."""
    from zoneinfo import ZoneInfo
    try:
        from core import cost_guard
        lo = datetime.combine(day, datetime.min.time(), tzinfo=tz())
        utc = ZoneInfo("UTC")
        lo_u, hi_u = lo.astimezone(utc).isoformat(), (lo + timedelta(days=1)).astimezone(utc).isoformat()
        cyc_u = min(cost_guard._cycle_start(at), lo).astimezone(utc).isoformat()
        with state.connect() as c:
            rows = c.execute("SELECT task, SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS today, COUNT(*) AS cycle "
                             "FROM spend_ledger WHERE ts >= ? AND ts < ? AND task IS NOT NULL GROUP BY task",
                             (lo_u, cyc_u, hi_u)).fetchall()
    except Exception as e:                               # noqa: BLE001 — the meters still land without it
        log.warning("report.ai_uses_unreadable", error=type(e).__name__)
        return {}
    out = {}
    for r in sorted(rows, key=lambda r: (-int(r["today"] or 0), str(r["task"]))):
        task = str(r["task"])
        out[f"ai:{task}"] = {"value": int(r["today"] or 0), "cycle": int(r["cycle"] or 0),
                             "label": _AI_TASKS.get(task) or task.replace("_", " ").capitalize()}
    return out


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


def _stored_number(rep) -> int | float | None:
    """A stored row's headline as a number, or None when it is not one.

    A HEADLINE NOBODY GAVE IS NOT A ZERO. `_normalize` fills value 0 and label "" for a reporter that gives no
    headline. Until 2026-10-02 the website reporter gave none while saying "179 visits from people this week", so
    every stored day reads 0. Counted as a zero, the first real headline would read "+185 vs the day before" and its
    chart would leap from nothing. So an unlabelled headline on a row that had something to say, or that could not
    report, is no number. A quiet row (nothing happened, no label) keeps its zero: that is the AEO Machine's day
    with nothing published, and a real zero."""
    if not isinstance(rep, dict):
        return None
    h = rep.get("headline")
    v = h.get("value") if isinstance(h, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None                             # bool is an int in Python; it is not a headline
    if not str(h.get("label") or "").strip() and (rep.get("happened") or rep.get("error")):
        return None
    return v


def _previous_headline(c, day: date, machine: str):
    row = c.execute("SELECT report_json FROM daily_reports WHERE day = ? AND machine = ?",
                    ((day - timedelta(days=1)).isoformat(), machine)).fetchone()
    if not row:
        return None
    try:
        return _stored_number(json.loads(row["report_json"]))
    except ValueError:
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
                raw = r["fn"](day)
                rep = _normalize(machine, r["title"], raw)
                # A REPORTER THAT SAYS ITS OWN CHANGE, EVEN "NONE", IS NOT SECOND-GUESSED. The website says none when a
                # site has no week ending the day before; filled from yesterday's row, that site's whole week would
                # read as growth.
                said = isinstance(raw, dict) and isinstance(raw.get("headline"), dict) and "delta" in raw["headline"]
                head = rep["headline"]
                if not said and head.get("delta") is None and isinstance(head["value"], (int, float)):
                    prev = _previous_headline(c, day, machine)
                    head["delta"] = (head["value"] - prev) if prev is not None else None
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


def late(machine: str, day: date) -> bool:
    """A machine's numbers for a closed day arrived late: re-report that one row, while the day's review hasn't been
    started. Returns True when the row was rewritten. Never raises.

    THE DAY CLOSES AT MIDNIGHT, BUT SOME NUMBERS DON'T EXIST UNTIL MORNING. Measured on the owner's box 2026-10-02:
    the website sync for yesterday runs at 06:00 (the day must be over in PostHog), hours after `close_open_days`
    froze yesterday's rows, so the Morning Review never carried a website line. Search Console lags by days. The
    review is built once, from these rows (core/review_brief.py), so until it is started they can still become
    true; once it has been, the day is what was sent and stays so. Only the named machine's row is touched.
    """
    try:
        r = REPORTERS.get(machine)
        if not r or machine == METERS:
            return False
        from core import review_brief
        d = day.isoformat()
        with state.connect() as c:
            if c.execute("SELECT 1 FROM daily_reports WHERE day = ? AND machine = ?",
                         (d, review_brief.BRIEF)).fetchone():
                return False                       # the review for that day is built or being built: it stays
            rep = _normalize(machine, r["title"], r["fn"](day))
            blob = json.dumps(rep, sort_keys=True)
            c.execute("INSERT INTO daily_reports (day, machine, report_json, written_at, final) VALUES (?,?,?,?,1) "
                      "ON CONFLICT(day, machine) DO UPDATE SET report_json = excluded.report_json, "
                      "written_at = excluded.written_at", (d, machine, blob, state._now()))
        log.info("report.late_numbers", machine=machine, day=d)
        return True
    except Exception as e:                         # noqa: BLE001 — late numbers must never break a sync
        log.warning("report.late_failed", machine=machine, error=type(e).__name__)
        return False


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
            # THE STORED BRIEF IS NOT A MACHINE (core/review_brief.py): every reader of the day's segments
            # gets the machines only; the brief is read by name.
            rows = c.execute("SELECT * FROM daily_reports WHERE day = ? AND machine != 'brief'", (d,)).fetchall()
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
            v = _stored_number(json.loads(r["report_json"]))
        except ValueError:
            continue
        if v is None:
            continue
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


_BLANK = frozenset({"", "—", "–", "-", "n/a", "none"})


def has_value(v) -> bool:
    """IS THERE ANYTHING TO SHOW? A LINE WITHOUT ONE IS NOT DRAWN.

    Owner, 2026-09-29, looking at this page rendered on a box with nothing in it yet: *"That's
    exactly what we do not want!!! what a depressing looking screen. If nothing exists, we don't
    wanna have zeros."* and *"If a line doesn't have data, it should not be displayed."* The page
    drew ten meters at "0 of N", a headline reading "None", zeros in red, and "Nothing." under
    every machine.

    So None, blank, a dash, and zero in any spelling ("0", "0.0", "$0", "$0.00", "0%") are
    nothing. A sentence with a number in it ("3 of 50 used") is something. One rule, used by the
    page, the Base Machine dashboard, the email and this module's message, so none of them can
    disagree about what counts."""
    if v is None or isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float)):
        return v != 0
    t = str(v).strip()
    if t.lower() in _BLANK:
        return False
    bare = re.sub(r"[\s$€£%,.+]", "", t)
    if bare and set(bare) <= {"0"}:
        return False
    # "0 of 20", "$0 of $90", "0% used" — a count that starts at nothing is still nothing. A time
    # such as "00:30" is not caught: the zero must be followed by a space, a % or the end.
    return not re.match(r"^[$€£]?0(?:[.,]0+)?(?:\s|%|$)", t)


def _slack_escape(t) -> str:
    """Slack's three control characters, and nothing else: anything typed into the box reads as words."""
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _planned_names() -> list[str]:
    try:
        from core import business_context
        return [str(p.get("what") or "").strip().lower() for p in business_context.get().get("coming") or []
                if str(p.get("what") or "").strip()]
    except Exception:                                    # noqa: BLE001 — no context: nothing planned to hide
        return []


def _names_a_plan(item: dict, planned: list[str]) -> bool:
    text = f"{item.get('title') or ''} {item.get('why') or ''}".lower()
    return any(p in text for p in planned)


def slack_text(b: dict) -> str:
    """The stored brief as a Slack message: the same words as the email and the app page (core/review_email.py,
    core/dash/review.py), short. The date, the day's quote, the good news, then Worth your time today, Already
    moving and Ideas to try, numbered, each item on one line. No buttons, no markers, no zeros."""
    from core import review_email
    link = str(b.get("link") or "")
    base = link.split("/app/review")[0]
    lines = [f"*{_slack_escape(b.get('date_label'))}*", f"> _{_slack_escape(b.get('quote'))}_"]
    if b.get("good_news"):
        lines += ["", _slack_escape(b["good_news"])]
    if review_email.numbers_line(b):
        lines += ["", _slack_escape(review_email.numbers_line(b))]
    # PLANS NEVER GO TO SLACK (OSDev1 on #1964): a channel is read by more people than the owner, and a planned offer
    # is the owner's until its month. So "What's coming" stays on the page and in the owner's email, and an idea that
    # names a planned offer stays there too.
    planned = _planned_names()
    for key, heading in review_email.SECTIONS:
        if key == "coming":
            continue
        items = [it for it in b.get(key) or [] if str(it.get("title") or "").strip()
                 and not (key == "ideas" and _names_a_plan(it, planned))]
        if not items:
            continue
        lines += ["", f"*{heading}*"]
        for n, it in enumerate(items, 1):
            title, machine = _slack_escape(it["title"]), _slack_escape(it.get("machine"))
            if key == "moving" and machine:
                title = f"{machine}: {title}"          # what a machine did reads machine first, as on the page
            # A LINK SLACK CAN OPEN, OR NONE: a whole web address with nothing in it that could break the markup.
            href = review_email._href(str(it.get("href") or ""), base)
            if href.startswith(("https://", "http://")) and not re.search(r"[\s<>|]", href):
                title = f"<{href}|{title}>"
            why = _slack_escape(it.get("why"))
            lines.append(f"{n:02d}  {title}" + (f" — {why}" if why else ""))
    if b.get("ideas_from") == "ai" and b.get("ideas"):
        lines += ["", "_The ideas come from your box's AI, based only on yesterday's numbers._"]
    whole = link.startswith(("https://", "http://")) and not re.search(r"[\s<>|]", link)
    lines += ["", f"<{link}|Open the full review>" if whole else "The full review is in your box's app."]
    return "\n".join(lines).rstrip()


def render(day: date, now: datetime | None = None) -> str:
    """THE MORNING SLACK MESSAGE, FROM THE SAME STORED BRIEF AS THE EMAIL AND THE PAGE.

    OSDev1, 2026-10-02, assigning it: this still sent the old text, with "!!" markers, and repeated every standing
    item every morning, against the owner's ruling for the review: *"Light and optimistic ... if they're stale
    information in there that they can't instantly change then we don't continue to harass and annoy them every
    day."* So it reads `review_brief.ensure(day)`, the brief `run` builds once and stores, and the no-repeat rule
    that decides the email's list decides this one. "" on a morning with nothing to say: nothing is sent, as for the
    email. A brief that can't be built still tells him the review is there, in one line, never the old shape.

    `day` is the day the message is ABOUT (yesterday, closed)."""
    now = now or now_local()
    try:
        from core import review_brief
        b = review_brief.ensure(day, now)
    except Exception as e:  # noqa: BLE001 — the morning still gets its link
        log.warning("report.slack_brief_failed", about=day.isoformat(), error=f"{type(e).__name__}: {str(e)[:160]}")
        return f"Your Morning Review is ready.\n<{page_url(day)}|Open the review>"
    if b.get("empty"):
        return ""
    text = slack_text(b)
    if len(text) > SLACK_LIMIT:
        # chat.postMessage REFUSES over 4000 characters, so an unbounded message is not a long message, it is NO
        # message, on the morning it had the most to say. Cut at a line boundary and keep the link, which is the
        # whole point of the last line.
        lines = text.split("\n")
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
    # THE MORNING'S BRIEF IS BUILT AND STORED HERE, WHATEVER THE CHANNEL (OSDev1's review of #1779): only the
    # email used to build it, so a box with just the app never remembered what it had shown, and "152 scripts
    # waiting" came back every morning. Once per day: `ensure` returns the stored one after the first time.
    try:
        from core import review_brief
        review_brief.ensure(yday, now)
    except Exception as e:  # noqa: BLE001 — a brief that can't be built never costs the DM or the push
        log.warning("report.brief_failed", about=yday.isoformat(), error=f"{type(e).__name__}: {str(e)[:160]}")
    out = {"status": "send_failed", "about": yday.isoformat()}
    if want_dm:
        msg = render(yday, now)
        if not msg:
            # NOTHING TO SAY, NOTHING SENT, as for the email: done for the morning, so it isn't retried every hour.
            st["sent"] = mark
            out["dm"] = "quiet"
            log.info("report.dm_quiet", day=t.isoformat(), about=yday.isoformat())
        elif send(msg):
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
    if "sent" in (out.get("dm"), out.get("email"), out.get("app")) or out.get("dm") == "quiet":
        out["status"] = "sent"
    return out


def _email(to: str, yday: date, now: datetime, t: date, send_email=None) -> str:
    """The email half of `run`. -> "sent" | "failed:<Kind>". Never raises: a mail failure must not
    cost the DM, its marker or the worker. The idempotency key is per day and recipient, so a
    retry after a timeout that DID deliver cannot deliver the same morning twice."""
    try:
        from core import review_email
        e = review_email.build(yday, now)
        if e.get("skip"):
            # NOTHING TO SAY, NOTHING SENT (owner-approved, docs/SCOPE_MORNING_REVIEW_V2.md decision 4). Marked
            # done for the morning, so it isn't retried every hour.
            log.info("report.email_quiet", day=t.isoformat(), about=yday.isoformat())
            return "sent"
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
    # TODAY'S BRIEF IS WRITTEN HERE, AHEAD (core/brief.py, #1839): from the rows just stored, so `core.brief` never
    # waits on an AI. It rewrites only when the numbers moved, and its AI at most hourly. Never raises.
    from core import brief
    out["brief"] = brief.refresh().get("status")
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
