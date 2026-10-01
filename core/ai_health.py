"""Did this box's AI actually answer? When it last did, when it last failed and why, and a one-question test.

Owner, 2026-10-01: *"It says it's connected, but I can't tell if it's using inference."* Connected means a credential
is stored; it never meant a request went through. So every real reasoning call records its outcome here
(`core.brain.think` and `core.brain.run_agent`), at most once a minute per outcome, and Settings → AI Account shows
the last success, the last failure and a Test button that asks one tiny question through the same path drafts and
machines use. The check-in carries the last success's time, so OSDev1 can see it without asking.

NEVER RAISES INTO A REASONING CALL: recording an outcome must never be what breaks one.
NEVER STORES A PROMPT OR AN ANSWER: when, what kind of call, which backend, and for a failure a short scrubbed reason.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

NS = "ai_health"
EVERY_S = 60                      # one write per outcome per minute per process: drafting comes in bursts
TEST_TASK = "router"              # the cheapest tier in config `models`
TEST_PROMPT = "Reply with exactly one word: ready"
_last: dict = {}


def _iso(t: float) -> str:
    # MILLISECONDS: a success and a failure in the same second must still say which came last.
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds")


def note(ok: bool, how: str, why: str = "", *, force: bool = False) -> None:
    """Record that a reasoning call answered (ok) or failed (with why). Never raises."""
    try:
        key = "last_ok" if ok else "last_fail"
        now = time.time()
        if not force and now - _last.get(key, 0.0) < EVERY_S:
            return
        _last[key] = now
        from core import box_settings, brain
        rec = {"at": _iso(now), "how": str(how or "")[:40], "backend": brain._backend()}
        if not ok:
            from core.logging import scrub_secrets
            rec["why"] = scrub_secrets(" ".join(str(why or "").split()))[:200]
        box_settings.put(NS, key, rec, set_by="ai_health")
    except Exception:                                   # noqa: BLE001 — see the module docstring
        pass


def state() -> dict:
    """{"last_ok": {...} | None, "last_fail": {...} | None}. Never raises."""
    try:
        from core import box_settings
        ok, fail = box_settings.get(NS, "last_ok"), box_settings.get(NS, "last_fail")
    except Exception:                                   # noqa: BLE001
        ok, fail = None, None
    return {"last_ok": ok if isinstance(ok, dict) else None, "last_fail": fail if isinstance(fail, dict) else None}


TEST_GAP_S = 30


def last_test() -> dict | None:
    try:
        from core import box_settings
        got = box_settings.get(NS, "last_test")
    except Exception:                                   # noqa: BLE001
        got = None
    return got if isinstance(got, dict) else None


def _keep_test(result: dict) -> dict:
    try:
        from core import box_settings
        box_settings.put(NS, "last_test", dict(result, at=_iso(time.time())), set_by="ai_health")
    except Exception:                                   # noqa: BLE001
        pass
    return result


def test() -> dict:
    """Ask the box's AI one tiny question, through the path drafts use. -> {"ok", "answer", "seconds", "why"}.
    Kept as `last_test`, so the page shows it after a reload. At most one every 30 seconds: each one is a real
    request on the owner's plan."""
    prev = last_test() or {}
    try:
        if time.time() - datetime.fromisoformat(str(prev.get("at"))).timestamp() < TEST_GAP_S:
            return {"ok": bool(prev.get("ok")), "answer": prev.get("answer", ""), "seconds": prev.get("seconds", 0),
                    "why": prev.get("why", ""), "repeat": True}
    except (TypeError, ValueError):
        pass
    return _keep_test(_ask())


def _ask() -> dict:
    from core import brain
    from core.logging import scrub_secrets
    t0 = time.monotonic()
    try:
        text = brain.think(TEST_TASK, TEST_PROMPT, max_tokens=8, timeout=90, isolated=True)
    except Exception as e:                              # noqa: BLE001 — the owner reads exactly what went wrong
        why = scrub_secrets(" ".join(f"{type(e).__name__}: {e}".split()))[:300]
        note(False, "test", why, force=True)
        return {"ok": False, "answer": "", "seconds": round(time.monotonic() - t0, 1), "why": why}
    answer = " ".join(str(text or "").split())[:60]
    if answer:
        note(True, "test", force=True)
    else:
        note(False, "test", "it answered with nothing", force=True)
    return {"ok": bool(answer), "answer": answer, "seconds": round(time.monotonic() - t0, 1),
            "why": "" if answer else "it answered with nothing"}
