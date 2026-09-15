"""One DM a day that says where every meter stands, and one the moment a meter crosses 80%.

Why this exists (2026-09-06): the Places cap of 200 requests was hit by a single machine's first
run, and the reel change multiplied renders per reel by four against a HeyGen cap of 100. Both
were discovered from a FAILED RUN. The governor already stops the spend; nobody was told it was
about to. The owner is cost-conscious and should read the meters at breakfast, not learn them
from a machine that stopped.

The numbers come from core.cost_guard — the same meter the vendors are charged against — so
this can never disagree with the thing that refuses a call. It sends nothing on a box with no
operator, and nothing twice: one digest per local day, one crossing alert per vendor per cycle.
"""
import json
import pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import cost_guard
from core.config import get_config, settings
from core.logging import get_logger

log = get_logger(__name__)
ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = "my/cost_digest.json"          # {"sent": "YYYY-MM-DD", "crossed": {"<cycle>:<vendor>": pct}}
WARN_PCT = 80


def _cfg() -> dict:
    return dict((get_config().get("cost") or {}).get("digest") or {})


def _tz():
    return ZoneInfo((get_config().get("cost") or {}).get("timezone", "UTC"))


def _state_path() -> pathlib.Path:
    return ROOT / STATE


def _read_state() -> dict:
    p = _state_path()
    try:
        d = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        d = {}
    d.setdefault("sent", ""); d.setdefault("crossed", {})
    return d


def _write_state(d: dict) -> None:
    p = _state_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, sort_keys=True))


def _cycle_bounds(now: datetime) -> tuple:
    start = cost_guard._cycle_start(now)
    nxt = (start.replace(day=1) + timedelta(days=32)).replace(day=start.day)
    return start, nxt


def meters(now: datetime | None = None) -> list[dict]:
    """Every metered vendor: used, cap, pct, burn per day, days to the cap at that burn."""
    now = now or datetime.now(_tz())
    start, nxt = _cycle_bounds(now)
    elapsed = max((now - start).total_seconds() / 86400.0, 1.0 / 24)
    left_in_cycle = max((nxt - now).total_seconds() / 86400.0, 0.0)
    out = []
    for v in sorted(cost_guard.metered_vendors()):
        cap = cost_guard.vendor_cap(v) or 0.0
        used = float(cost_guard.vendor_usage(v, now) or 0.0)
        pct = (100.0 * used / cap) if cap else 0.0
        burn = used / elapsed
        days_to_cap = ((cap - used) / burn) if (burn > 0 and used < cap) else None
        out.append({"vendor": v, "used": used, "cap": cap, "pct": pct, "burn": burn,
                    "days_to_cap": days_to_cap, "days_left": left_in_cycle,
                    "hits_cap_this_cycle": bool(days_to_cap is not None and days_to_cap < left_in_cycle) or used >= cap})
    return out


def render(now: datetime | None = None) -> str:
    now = now or datetime.now(_tz())
    start, nxt = _cycle_bounds(now)
    rows = meters(now)
    lines = [f"Meters, {now:%a %d %b} — cycle {start:%d %b} to {nxt:%d %b}"]
    for r in rows:
        if r["used"] >= r["cap"] and r["cap"]:
            tail = "AT CAP — calls are refused until the cycle resets"
        elif r["days_to_cap"] is not None:
            tail = f"~{r['days_to_cap']:.0f} days to cap at this pace" + (" — WILL HIT IT THIS CYCLE" if r["hits_cap_this_cycle"] else "")
        else:
            tail = "no use yet"
        flag = "!! " if r["pct"] >= WARN_PCT else "   "
        lines.append(f"{flag}{r['vendor']:<14} {r['used']:>7.0f} / {r['cap']:<6.0f} ({r['pct']:>3.0f}%)  {tail}")
    try:
        lines.append(f"USD this cycle: ${cost_guard.month_to_date_spend(now):.2f} of ${cost_guard.ceiling():.2f} ceiling")
    except Exception as e:  # noqa: BLE001 — the meters still go out if the USD line cannot
        log.warning("cost_digest.usd_line_failed", error=type(e).__name__)
    return "\n".join(lines)


def run(now: datetime | None = None, send=None) -> dict:
    """The periodic. Once a day after hour_local: the digest. Any tick: a crossing alert, once per vendor per cycle."""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return {"status": "off"}
    operator = getattr(settings, "operator_slack_user_id", "") or ""
    if not operator:
        return {"status": "no_operator"}
    if send is None:
        from core import slack
        send = lambda text: slack.send_dm(operator, text)     # noqa: E731
    now = now or datetime.now(_tz())
    st = _read_state(); out = {"status": "quiet", "digest": False, "alerts": []}
    start, _ = _cycle_bounds(now); cyc = start.strftime("%Y-%m-%d")
    for r in meters(now):
        key = f"{cyc}:{r['vendor']}"
        if r["pct"] >= WARN_PCT and key not in st["crossed"]:
            msg = (f"{r['vendor']} is at {r['pct']:.0f}% of its cap ({r['used']:.0f} of {r['cap']:.0f} this cycle)"
                   + (" and every further call is refused until the cycle resets." if r["used"] >= r["cap"]
                      else f" — about {r['days_to_cap']:.0f} days to the cap at this pace." if r["days_to_cap"] is not None else "."))
            if send(msg):
                st["crossed"][key] = round(r["pct"]); out["alerts"].append(r["vendor"])
                log.warning("cost_digest.crossing", vendor=r["vendor"], pct=round(r["pct"]))
    today = now.strftime("%Y-%m-%d")
    # THE MORNING REVIEW ABSORBED THE DAILY DIGEST (core/report.py, 2026-09-09): it renders these
    # meters as its bottom rail, so this standalone DM only goes out when `cost.digest.standalone`
    # says so — otherwise breakfast is two messages. The crossing alert above is untouched: it is a
    # transition, not a digest, and must not wait for breakfast.
    from core.config import as_bool
    if (as_bool(cfg.get("standalone"), False)
            and now.hour >= int(cfg.get("hour_local", 8)) and st.get("sent") != today):
        if send(render(now)):
            st["sent"] = today; out["digest"] = True; out["status"] = "sent"
            log.info("cost_digest.sent", day=today)
    if out["alerts"] and out["status"] == "quiet":
        out["status"] = "alerted"
    _write_state(st)
    return out


try:
    from core.worker import register_periodic
    register_periodic(run, interval_s=3600, name="cost_digest", beat="cost_digest")
except Exception as e:  # noqa: BLE001 — importable without a worker (tests, scripts)
    log.info("cost_digest.not_registered", why=type(e).__name__)
