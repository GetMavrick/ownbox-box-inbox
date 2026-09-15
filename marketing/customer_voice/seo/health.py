"""The two rails that need no account: is the front door open, and is it fast.

STAGE 1 IS DELIBERATELY THE UNAUTHENTICATED HALF (docs/PLAN_CUSTOMER_VOICE.md §4). Reviews wait
on Google, comments wait on Meta, search waits on OAuth — all of them wait on somebody else's
queue. Uptime and PageSpeed wait on nothing, so this machine proves itself end to end on the day
it lands, on any box, with no key to find.

EVERY FETCH GOES THROUGH `core/net.py`. Not because these hosts are dangerous — the site is the
buyer's own and PageSpeed is Google — but because a machine that reaches the internet through
one door can be reasoned about, and one that reaches it eight ways cannot. Stage 0 built the
door and the AST scan that keeps everything inside it.

NO MODEL RUNS HERE. Every number is measured or read; nothing is judged. CLAUDE.md invariant 3.
"""
import json
import time
from datetime import datetime, timezone

from core import net, state
from core.logging import get_logger

from .. import rails

log = get_logger(__name__)

UPTIME, PAGESPEED = "uptime", "pagespeed"
_PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


# READ THE CONFIG PER CALL, never the name bound at import. `from core.config import get_config`
# at module scope freezes the function object, so a box overlay reloaded at runtime — or a test
# that swaps it — moves core.config and NOT this module. That is not hypothetical: the first
# version of this file did exactly that, and two of its own tests passed VACUOUSLY, agreeing with
# the real shipped config instead of the one they had set. The same defect cost an hour in
# core/dash the same morning.
def _cfg(section: str) -> dict:
    from core.config import get_config
    return dict((get_config().get("customer_voice") or {}).get(section) or {})


def site_url() -> str:
    """The address this business answers on. Empty is not a fault: it is a box nobody has
    finished setting up, and the rail says `connect` rather than reporting a failure."""
    from core.config import get_config
    return str((get_config().get("customer_voice") or {}).get("site_url") or "").strip()


def configured() -> bool:
    return bool(site_url())


def _observe(source: str, external_id: str, kind: str, value, payload: dict) -> None:
    """One row, once. `UNIQUE(source, external_id)` is what makes an overlapping poll idempotent
    rather than a triple count, so this INSERTs and lets the constraint do the deduplication."""
    with state.connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO voice_observations "
            "(id, source, external_id, observed_at, recorded_at, kind, value, payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"{source}:{external_id}", source, external_id, payload.get("at") or state._now(),
             state._now(), kind, value, json.dumps(payload, sort_keys=True)))


def check_uptime() -> dict:
    """Ask the site for its front page and record what came back.

    THREE OUTCOMES, AND "SLOW" IS ITS OWN. A site that answers in four seconds is not down, and
    calling it down would cry wolf until he stopped reading; but it is not fine either, because
    a visitor feels four seconds and leaves. So `slow_ms` is a separate state from a refusal.
    """
    url = site_url()
    if not url:
        return {"status": "unconfigured"}
    started = time.monotonic()
    body = ""
    err = None
    try:
        body = net.fetch_public(url, timeout=int(_cfg("uptime").get("timeout_s", 12)),
                                max_bytes=200_000)
    except Exception as e:                          # noqa: BLE001 — a probe never raises upward
        err = f"{type(e).__name__}"
    ms = (time.monotonic() - started) * 1000.0
    up = bool(body) and err is None
    slow = up and ms > float(_cfg("uptime").get("slow_ms", 2500))
    if not up and not err:
        # `fetch_public` answers "" for a refusal AND for a dead host, on purpose: to a caller
        # reading a stranger's page those are the same. Here they are not, so say what is known
        # rather than inventing a reason.
        err = "no response, or an address this box may not fetch"
    minute = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    _observe(UPTIME, minute, "check", round(ms, 1),
             {"up": up, "ms": round(ms, 1), "slow": slow, "url": url, "error": err})
    rails.record(UPTIME, error=err)
    if up:
        rails.mark_connected(UPTIME)
    log.info("voice.uptime", up=up, ms=round(ms), slow=slow)
    return {"status": "up" if up and not slow else ("slow" if slow else "down"),
            "ms": round(ms, 1), "error": err}


def check_pagespeed() -> dict:
    """Google's PageSpeed Insights score for the front page.

    A KEY IS OPTIONAL AND, IN PRACTICE, NEEDED. PSI answers without one from a pool shared by
    everyone who calls anonymously — and MEASURED on the first real call, 2026-09-09, that pool
    was already empty: `429 Quota exceeded for quota metric 'Queries' and limit 'Queries per
    day'`. So "Stage 1 needs no credential" is true of uptime and only half true of this rail.
    A free key gives the box a quota of its own; without one this reports `connect` most days,
    which is honest and actionable rather than a red light nobody can clear.
    """
    url = site_url()
    if not url:
        return {"status": "unconfigured"}
    cfg = _cfg("pagespeed")
    q = {"url": url, "strategy": str(cfg.get("strategy") or "mobile")}
    key = str(cfg.get("api_key") or "").strip()
    if key:
        q["key"] = key
    import urllib.parse
    target = f"{_PSI}?{urllib.parse.urlencode(q)}"
    err, score, lcp, quota = None, None, None, False
    try:
        # STATUS, NOT JUST A BODY. `fetch_public` answers "" for a refusal, a dead host and a
        # 429 alike — right for a guessed domain, wrong here. MEASURED: PSI answered 429 "Quota
        # exceeded ... Queries per day" and this rail said "answered without a performance
        # score", which sends someone to read the parser for a fault that was a quota.
        status, raw = net.get_public(target, timeout=60, max_bytes=2_000_000)
        data = json.loads(raw) if raw else {}
        if status == 429 or (data.get("error") or {}).get("code") == 429:
            # NOT A FAILURE, AND NOT OUR BUG. The unauthenticated PSI pool is shared across
            # everyone who calls without a key and it runs dry most days — measured empty on the
            # first real call. A key is free and raises the ceiling, so this is an ACTIONABLE
            # next step, which is a `connect` state and not a red light.
            quota = True
            err = ("PageSpeed's free anonymous quota is used up for today — set "
                   "customer_voice.pagespeed.api_key for a quota of your own")
        elif status and status >= 400:
            err = f"PageSpeed answered {status}"
        elif status == 0:
            err = "PageSpeed could not be reached"
        lh = (data.get("lighthouseResult") or {})
        cats = (lh.get("categories") or {}).get("performance") or {}
        if cats.get("score") is not None:
            score = round(float(cats["score"]) * 100)
        audits = lh.get("audits") or {}
        lcp_a = (audits.get("largest-contentful-paint") or {}).get("numericValue")
        if lcp_a is not None:
            lcp = round(float(lcp_a))
        if score is None and not err:
            err = "PageSpeed answered without a performance score"
    except ValueError:
        err = "PageSpeed answered with something that is not JSON"
    except Exception as e:                          # noqa: BLE001
        err = f"{type(e).__name__}"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if score is not None:
        _observe(PAGESPEED, day, "score", float(score),
                 {"score": score, "lcp_ms": lcp, "strategy": q["strategy"], "url": url})
        with state.connect() as c:
            # A SCORE FOR A DAY IS CORRECTED, NOT ACCUMULATED — run it twice and the later
            # reading is the true one, which is why this table overwrites by key.
            for metric, v in (("performance", float(score)), ("lcp_ms", float(lcp or 0))):
                c.execute(
                    "INSERT INTO voice_metrics (source, metric, day, value, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(source, metric, day) DO UPDATE SET value = excluded.value, "
                    "  updated_at = excluded.updated_at",
                    (PAGESPEED, metric, day, v, state._now()))
        rails.mark_connected(PAGESPEED)
    rails.record(PAGESPEED, error=err, actionable=quota)
    log.info("voice.pagespeed", score=score, lcp_ms=lcp, quota=quota, error=err)
    return {"status": "ok" if score is not None else ("quota" if quota else "failed"),
            "score": score, "lcp_ms": lcp, "quota": quota, "error": err}


def periodic_uptime() -> dict:
    if not rails.enabled() or not rails.owns(UPTIME):
        return {"status": "off"}
    return check_uptime()


def periodic_pagespeed() -> dict:
    if not rails.enabled() or not rails.owns(PAGESPEED):
        return {"status": "off"}
    return check_pagespeed()
