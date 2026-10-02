"""The outreach pull (docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md §5 step 3): Instantly's campaigns, read hourly by the worker.

ONE CAMPAIGN AT A TIME, THROUGH THE WORKER (OSDev1's review of the TAKING; the website sync's shape, jobs.py). A pull
is a chain of jobs: the first reads the campaign list and every campaign's week in one call each, then queues the
first campaign; each campaign's job reads its days and its week's steps and queues the next before writing its own
result. So whatever else waits for the worker, an inbox draft say, waits behind one campaign at most.

THE SAME 28-DAY BACKFILL AS THE WEBSITES (sync.py): a campaign seen for the first time is read for its last 28 days,
one that was missed for a while for up to a week, and every pull reads yesterday again, and today so far.

A REFUSAL IS AN OUTCOME, NEVER A RETRY LOOP (OSDev1). A key Instantly refuses stops the pull and waits for a new key
on the card; a 429 stops it and waits an hour, said on the card; the month's request limit stops it until the next
cycle. A campaign whose answer could not be read is said and the chain goes on to the next.

WHAT IS RUNNING IS READ FROM THE QUEUE ITSELF (jobs.running), as the website card's is.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core import box_secrets, box_settings
from core.logging import get_logger

from . import instantly, jobs, outreach_store, settings

log = get_logger(__name__)

PULL, CHECK = "outreach_pull", "outreach_check"
PENDING_SECRET = "INSTANTLY_API_KEY_OUTREACH_PENDING"
RUN, LAST_CHECK = "pull_run", "last_check"
CHECK_SLOW = "Instantly asked the box to slow down. Press Check and save again in a few minutes."
BACKFILL_DAYS = 28
CATCH_UP_DAYS = 7
EVERY_MIN = 55                 # the hourly tick, with room for a tick that comes a little early
MAX_CAMPAIGNS = 50             # a workspace with more is read for its busiest fifty
_LIVE = {1, 4, -1, -2}         # active, running follow-ups, sending accounts unhealthy, paused for bounces


def _now() -> datetime:
    return datetime.now(ZoneInfo("UTC"))


def _iso(t: datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz())
    except Exception:                                    # noqa: BLE001 — an unknown zone is UTC
        return ZoneInfo("UTC")


def today() -> date:
    return _now().astimezone(_tz()).date()


def bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """The buyer's days [first, last] as two UTC instants: from first's local midnight to the millisecond before the
    local midnight after `last`. Instantly reads a date alone as UTC midnight, which would shift a Pacific day by
    seven hours."""
    tz, utc = _tz(), ZoneInfo("UTC")
    start = datetime.combine(first, time.min, tz).astimezone(utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tz).astimezone(utc) - timedelta(milliseconds=1)
    return start, end


def week_of(end_day: date) -> tuple[date, date]:
    """The seven days ending `end_day`, as the Morning Review's week is (seam.week)."""
    return end_day - timedelta(days=6), end_day


def _days(campaign_id: str, upto: date) -> list[date]:
    """The days to read for a campaign, oldest first: 28 for a new one, the gap (up to a week) for one missed a while,
    and always yesterday again and today so far."""
    through = outreach_store.pulled_through(campaign_id)
    if through is None:
        first = upto - timedelta(days=BACKFILL_DAYS - 1)
    else:
        first = max(min(through, upto - timedelta(days=1)), upto - timedelta(days=CATCH_UP_DAYS - 1))
    return [first + timedelta(days=i) for i in range((upto - first).days + 1)]


# ── the run ────────────────────────────────────────────────────────────────────────────────────────────────────

def pull_run() -> dict:
    r = box_settings.get(settings.OUT_NS, RUN)
    return r if isinstance(r, dict) else {}


def _save_run(r: dict) -> None:
    box_settings.put(settings.OUT_NS, RUN, r, set_by="outreach")


def backing_off() -> str:
    """While Instantly's 429 back-off lasts, the time it ends on the buyer's clock ("10:41"); "" otherwise."""
    try:
        until = datetime.fromisoformat(str(settings.pull_state().get("backoff_until") or ""))
    except ValueError:
        return ""
    return f"{until.astimezone(_tz()):%H:%M}" if _now() < until else ""


def _enqueue(intent: str, key: str, payload: dict) -> None:
    from core.queue import queue
    queue.enqueue(idempotency_key=key, intent=intent, agent_name="outreach", raw_text=json.dumps(payload))


def start_pull(why: str = "now") -> tuple[bool, str]:
    """Queue a pull: the list first, then one campaign at a time. Refused while one is queued or running."""
    if jobs.running(PULL):
        return False, "A pull is already running. Its result shows here when it finishes."
    if settings.instantly() is None:
        return False, "Connect Instantly first, below; the pull reads from it."
    wait = backing_off()
    if wait:
        return False, f"Instantly asked the box to slow down. Pull now works again at {wait}."
    run = uuid.uuid4().hex[:10]
    _save_run({"run": run, "why": why, "started": _iso(), "campaigns": [], "names": {}, "done": {}})
    _enqueue(PULL, f"{PULL}:{run}:list", {"run": run})
    return True, "Pulling now. The numbers show here when it finishes, and you can leave this page."


def _queue_next(run: str, campaign_id: str) -> None:
    r = pull_run()
    if r.get("run") != run:
        return                                           # an older run; a newer one has replaced it
    ids = list(dict.fromkeys(r.get("campaigns") or []))
    at = ids.index(campaign_id) + 1 if campaign_id in ids else len(ids)
    if at < len(ids):
        _enqueue(PULL, f"{PULL}:{run}:{ids[at]}", {"run": run, "campaign": ids[at]})


def _finish(run: str, campaign_id: str, outcome: str) -> None:
    """One campaign's outcome ("ok" or the sentence). The last one writes the pull state the card reads."""
    r = pull_run()
    if r.get("run") != run:
        return
    done = dict(r.get("done") or {})
    done[campaign_id] = outcome
    r["done"] = done
    _save_run(r)
    if set(done) >= set(r.get("campaigns") or []):
        _end(run, next((v for v in done.values() if v != "ok"), ""))


def _end(run: str, error: str, *, refused: instantly.Refused | None = None) -> None:
    r = pull_run()
    if r.get("run") != run:
        return
    r["ended"] = _iso()
    _save_run(r)
    upd = {"error": error, "last_run": _iso()}
    if not error:
        # A CLEAN RUN CLEARS WHAT A REFUSAL SET (OSDev1's review): after a 401, a pull that then succeeds must not leave
        # the card saying "Stopped" and the hourly pass waiting for a key that already works.
        upd.update(through=today().isoformat(), key_refused=False, backoff_until="")
    if refused is not None and refused.slow:
        upd["backoff_until"] = _iso(_now() + timedelta(hours=1))
    if refused is not None and refused.key:
        upd["key_refused"] = True
    settings.set_pull_state(**upd)
    if not error:
        outreach_store.prune()


def learn_step_base(steps: dict[str, dict]) -> dict[str, dict]:
    """STEP NUMBERING IS LEARNED, NOT ASSUMED (OSDev1). Instantly's docs count A/B variants from 0 and say nothing of
    steps. A workspace whose answers ever name a step "0" counts from 0, and the seam adds one so the first email is
    step 1, as a person says it; until then the steps are shown as Instantly sends them. A sequence's first email
    goes to every new lead, so a backfill names step 0 at once wherever it exists."""
    if "0" in steps and settings.pull_state().get("step_base") != 0:
        settings.set_pull_state(step_base=0)
    return steps


def _stops_the_pull(e: instantly.Refused) -> bool:
    """A refused key, a 429 and the month's limit end the whole pull: the next campaign would hear the same."""
    return e.key or e.slow or str(e) == instantly.CAPPED


def _list(run: str, conn: instantly.Conn) -> dict:
    upto = today()
    yesterday = upto - timedelta(days=1)
    try:
        found = instantly.campaigns(conn)
        outreach_store.write_campaigns(found)
        weeks = {}
        for end in (yesterday, yesterday - timedelta(days=7)):          # this week and the one before, as weeks
            s, e = week_of(end)
            weeks[end] = instantly.analytics(conn, *bounds(s, e))
            outreach_store.write_week_totals(s, e, weeks[end])
    except instantly.Refused as e:
        log.warning("outreach.list_refused", why=str(e)[:160])
        _end(run, str(e), refused=e)
        return {"error": str(e)[:200]}
    this = weeks[yesterday]
    busy = lambda c: sum(int(v or 0) for v in (this.get(c["id"]) or {}).values())   # noqa: E731
    # WHICH CAMPAIGNS ARE READ: one still sending, one with anything this week, and one not yet backfilled. A
    # campaign that finished months ago costs nothing an hour.
    wanted = [c for c in found if c.get("status") != 0 and (
        c.get("status") in _LIVE or busy(c) or outreach_store.pulled_through(c["id"]) is None)]
    wanted.sort(key=lambda c: -busy(c))
    ids = [c["id"] for c in wanted[:MAX_CAMPAIGNS]]
    r = pull_run()
    if r.get("run") != run:
        return {"campaigns": 0}
    r["campaigns"], r["names"] = ids, {c["id"]: c.get("name") or "" for c in wanted[:MAX_CAMPAIGNS]}
    _save_run(r)
    if ids:
        _enqueue(PULL, f"{PULL}:{run}:{ids[0]}", {"run": run, "campaign": ids[0]})
    else:
        _end(run, "")
    return {"campaigns": len(ids)}


def _campaign(run: str, conn: instantly.Conn, campaign_id: str) -> dict:
    upto = today()
    try:
        for d in _days(campaign_id, upto):
            outreach_store.write_step_day(campaign_id, d,
                                          learn_step_base(instantly.steps(conn, campaign_id, *bounds(d, d))))
        s, e = week_of(upto - timedelta(days=1))
        outreach_store.write_week_steps(campaign_id, s, e,
                                        learn_step_base(instantly.steps(conn, campaign_id, *bounds(s, e))))
    except instantly.Refused as e:
        log.warning("outreach.campaign_refused", campaign=campaign_id, why=str(e)[:160])
        if _stops_the_pull(e):
            _end(run, str(e), refused=e)
        else:
            _queue_next(run, campaign_id)
            _finish(run, campaign_id, str(e))
        return {"campaign": campaign_id, "error": str(e)[:200]}
    outreach_store.set_pulled_through(campaign_id, upto)
    _queue_next(run, campaign_id)
    _finish(run, campaign_id, "ok")
    return {"campaign": campaign_id}


def do_pull(job: dict) -> dict:
    p = json.loads(job.get("raw_text") or "{}")
    run, campaign_id = str(p.get("run") or ""), str(p.get("campaign") or "")
    if pull_run().get("run") != run:
        return {"skipped": "an older pull"}
    conn = settings.instantly()
    if conn is None:
        _end(run, "Connect Instantly first; the pull reads from it.")
        return {"skipped": "no key"}
    return _campaign(run, conn, campaign_id) if campaign_id else _list(run, conn)


def pull_failed(job: dict, error: Exception) -> None:
    """A pull job the worker gave up on still ends its part, so the card never says "Pulling…" for ever, and one
    campaign's failure never ends the run."""
    try:
        p = json.loads(job.get("raw_text") or "{}")
        run, campaign_id = str(p.get("run") or ""), str(p.get("campaign") or "")
        said = "The pull stopped before it finished. Press Pull now to try again."
        if campaign_id:
            _queue_next(run, campaign_id)
            _finish(run, campaign_id, said)
        else:
            _end(run, said)
    except Exception as e:                               # noqa: BLE001 — a failure hook never compounds a failure
        log.warning("outreach.pull_hook_failed", why=type(e).__name__)


def tick() -> None:
    """The worker's hourly pass: start a pull when the last one is an hour old. Never with a key Instantly refused,
    never inside a back-off, never while one runs. Never raises."""
    try:
        if settings.instantly() is None or jobs.running(PULL):
            return
        st = settings.pull_state()
        if st.get("key_refused"):
            return                                       # waits for a new key on the card
        now = _now()
        for k, gap in (("backoff_until", timedelta(0)), ("last_run", timedelta(minutes=EVERY_MIN))):
            try:
                if st.get(k) and now < datetime.fromisoformat(st[k]) + gap:
                    return
            except ValueError:
                pass
        start_pull("hourly")
    except Exception as e:                               # noqa: BLE001 — the worker's tick must not die here
        log.warning("outreach.tick_failed", why=f"{type(e).__name__}: {str(e)[:120]}")


# ── Check and save ─────────────────────────────────────────────────────────────────────────────────────────────

def start_check(typed_key: str) -> tuple[bool, str]:
    """Queue the one real read. The typed key waits under its pending name, never in the job's text."""
    if jobs.running(CHECK):
        return False, "A check is already running. Its result shows here when it finishes."
    box_secrets.put(PENDING_SECRET, typed_key, user_id="outreach")
    _enqueue(CHECK, f"{CHECK}:{uuid.uuid4().hex[:10]}", {})
    return True, "Checking Instantly now. The result shows here in a minute, and you can leave this page."


def last_check() -> dict:
    r = box_settings.get(settings.OUT_NS, LAST_CHECK)
    return r if isinstance(r, dict) else {}


def _said_check(ok: bool, said: str) -> None:
    box_settings.put(settings.OUT_NS, LAST_CHECK, {"ok": ok, "said": said, "at": _iso()}, set_by="outreach")


def do_check(job: dict) -> dict:
    """Read with the typed key what the pull reads: the campaign list, yesterday's numbers, and one campaign's steps.
    Saved only when Instantly answers all three, so a key that can see the campaigns but not their numbers is refused
    here, naming the scope it lacks, rather than at the first pull. Then the first pull starts."""
    key = box_secrets.get(PENDING_SECRET)
    try:
        if not key:
            _said_check(False, f"Paste your Instantly API key. It needs the {instantly.SCOPE} scope.")
            return {"ok": False}
        try:
            conn = instantly.Conn(key=key)
            found = instantly.campaigns(conn)
            yesterday = bounds(today() - timedelta(days=1), today() - timedelta(days=1))
            instantly.analytics(conn, *yesterday)
            if found:
                instantly.steps(conn, found[0]["id"], *yesterday)
        except instantly.Refused as e:
            # NOTHING RETRIES A CHECK (OSDev1's review): a 429 here is said as what to do, not as the pull's "in an hour".
            _said_check(False, (CHECK_SLOW if e.slow else str(e)) + " Nothing was saved.")
            return {"ok": False}
        box_secrets.put(settings.OUT_SECRET, key, user_id="outreach")
        settings.set_pull_state(key_refused=False, backoff_until="", error="")
        live = sum(1 for c in found if c.get("status") in _LIVE)
        n = len(found)
        _said_check(True, (f"Connected: {n:,} campaign{'s' if n != 1 else ''} in Instantly"
                           + (f", {live:,} sending." if live else ".")) if n else
                    "Connected. There are no campaigns in Instantly yet.")
    finally:
        box_secrets.clear(PENDING_SECRET)
    start_pull("connected")
    return {"ok": True, "campaigns": n}


def check_failed(job: dict, error: Exception) -> None:
    try:
        box_secrets.clear(PENDING_SECRET)
        _said_check(False, "The check stopped before it finished, and nothing was saved. Press Check and save to "
                           "try again.")
    except Exception as e:                               # noqa: BLE001
        log.warning("outreach.check_hook_failed", why=type(e).__name__)


def register() -> None:
    from core.worker import register as _register
    _register(PULL, do_pull, on_failure=pull_failed)
    _register(CHECK, do_check, on_failure=check_failed)
