"""Waiting for you: what an AI coworker asked to do that changes something, held until a person says yes.

docs/SCOPE_CONNECTIONS_MCP_FIRST.md, phase 1 (owner-approved 2026-10-01): a shift can use a connected app "with
approvals and receipts". A coworker never changes anything in an app on its own. It PROPOSES; the proposal waits
on the Dashboard and the owner's phone; the owner approves or declines; only an approval runs it, once.

A CORE PRIMITIVE, NOT A CONNECTIONS FEATURE. Whatever runs an approved proposal registers its KIND here
(`register_kind`), so the next thing that must ask first (a send, a post) uses the same queue, the same screen
and the same receipt. Core names no machine: a kind is a string its owner chose.

THE RULES, AND WHERE EACH IS KEPT:
  * nothing runs without a person: `decide()` is the only path to a kind's `run`, and it needs `by`.
  * once: waiting -> approved is one UPDATE that only a waiting, unexpired row survives, so two taps (two
    devices, a double click) run it once.
  * a retried proposal is one row: the same kind and detail while one is waiting returns that one.
  * a week to decide: after that it is expired, and expired never runs.
  * what will run is shown exactly: `detail` is stored once and is what runs; nothing is rebuilt at approval.

Never raises for a bad id or a decided row; it says so.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from core import state
from core.logging import get_logger

log = get_logger(__name__)

DAYS = 7
PAGE = "/approvals"
_KINDS: dict[str, dict] = {}
# WHO REGISTERS EACH KIND, so a process that never imported it (the web process deciding what a worker-side
# coworker proposed) can still run an approval. A kind's own module registers it at import; this only imports it.
_PROVIDERS = {"app_action": "core.connections.gateway", "box_pause": "core.box_tools"}
# THE LOCK SCREEN NAMES NO APP AND NO MACHINE (core/machine_breaks.py keeps the same rule): the page it opens does.
PUSH_TITLE = "Ownbox"
PUSH_BODY = "A coworker is waiting for your OK. Tap to review."


# WHO IS DECIDING, while an approved proposal runs. `run(detail)` takes only what was proposed, so a kind that
# records who changed something (a setting's `set_by`, a stop marker) asks `decider()` instead of writing a
# word like "approval" where a person belongs (OSDev1's review of #1803, 2026-10-02).
_DECIDER: contextvars.ContextVar[str] = contextvars.ContextVar("approvals_decider", default="")


def decider() -> str:
    """The person whose approval is running right now, or "" outside one."""
    return _DECIDER.get()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def register_kind(kind: str, *, run) -> None:
    """`run(detail) -> {"ok": bool, "text": str}` carries out an approved proposal of this kind. Called once per
    approval, after a person said yes."""
    if not callable(run):
        raise ValueError(f"approval kind {kind!r} needs run=")
    _KINDS[kind] = {"run": run}


def _row(r) -> dict:
    d = dict(r)
    for k in ("detail", "result"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except ValueError:
            d[k] = None
    return d


def propose(kind: str, *, machine: str, title: str, detail: dict, seat_id: str = "") -> dict:
    """Hold this until a person decides. -> the approval row. The same kind and detail while one is waiting
    returns that one (a model retries), and the owner's phone is told once."""
    if kind not in _KINDS:
        raise ValueError(f"no approval kind {kind!r} is registered")
    body = json.dumps(detail, sort_keys=True, default=str)
    if len(body) > 16_000:
        raise ValueError("this proposal is too large to show a person")
    fp = hashlib.sha256(f"{kind}\n{body}".encode()).hexdigest()
    now = _now()
    with state.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        same = c.execute("SELECT * FROM approvals WHERE fingerprint=? AND status='waiting' AND expires_at > ?",
                         (fp, _iso(now))).fetchone()
        if same:
            return _row(same) | {"repeat": True}
        aid = "ap_" + uuid.uuid4().hex[:20]
        c.execute("INSERT INTO approvals (id, kind, machine, title, detail, fingerprint, proposed_by, status, "
                  "created_at, expires_at) VALUES (?,?,?,?,?,?,?,'waiting',?,?)",
                  (aid, kind, machine, str(title)[:120], body, fp, str(seat_id or "")[:80], _iso(now),
                   _iso(now + timedelta(days=DAYS))))
        row = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    log.info("approvals.proposed", id=aid, kind=kind, machine=machine, seat=seat_id)
    _tell_phone()
    return _row(row)


def _tell_phone() -> None:
    try:
        from core import push
        owner = state.owner_user()
        for sub in push.subscriptions_for(owner["id"]):
            push.send(sub, title=PUSH_TITLE, body=PUSH_BODY, navigate=PAGE)
    except Exception as e:                       # noqa: BLE001 — the Dashboard still shows it
        log.warning("approvals.notify_failed", error=f"{type(e).__name__}: {e}"[:200])


def _expire(c) -> None:
    c.execute("UPDATE approvals SET status='expired' WHERE status='waiting' AND expires_at <= ?", (_iso(_now()),))


def waiting() -> list[dict]:
    """What is waiting for a person, oldest first."""
    with state.connect() as c:
        _expire(c)
        return [_row(r) for r in c.execute("SELECT * FROM approvals WHERE status='waiting' ORDER BY created_at")]


def recent(limit: int = 20) -> list[dict]:
    """What was decided, newest first."""
    with state.connect() as c:
        _expire(c)
        return [_row(r) for r in c.execute("SELECT * FROM approvals WHERE status != 'waiting' "
                                           "ORDER BY COALESCE(decided_at, expires_at) DESC LIMIT ?", (int(limit),))]


def get(aid: str) -> dict | None:
    with state.connect() as c:
        r = c.execute("SELECT * FROM approvals WHERE id=?", (str(aid or ""),)).fetchone()
    return _row(r) if r else None


def decide(aid: str, approve: bool, *, by: str) -> dict:
    """A person's answer. Approve runs it, once; decline never does. -> {"ok", "status", "text"}."""
    if not by:
        return {"ok": False, "status": "", "text": "Only a person can decide."}
    now = _iso(_now())
    with state.connect() as c:
        _expire(c)
        cur = c.execute("UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='waiting' "
                        "AND expires_at > ?", ("approved" if approve else "declined", now, str(by)[:80],
                                               str(aid or ""), now))
        row = c.execute("SELECT * FROM approvals WHERE id=?", (str(aid or ""),)).fetchone()
    if row is None:
        return {"ok": False, "status": "", "text": "There's nothing waiting with that id."}
    if cur.rowcount != 1:
        said = {"done": "already done", "failed": "already tried, and it failed", "declined": "already declined",
                "expired": "expired: it waited a week", "approved": "already approved"}.get(row["status"],
                                                                                       row["status"])
        return {"ok": False, "status": row["status"], "text": f"Nothing to do: it was {said}."}
    if not approve:
        log.info("approvals.declined", id=aid, by=by)
        return {"ok": True, "status": "declined", "text": "Declined. Nothing was changed."}
    a = _row(row)
    if a["kind"] not in _KINDS and a["kind"] in _PROVIDERS:
        import importlib
        importlib.import_module(_PROVIDERS[a["kind"]])
    kind = _KINDS.get(a["kind"])
    token = _DECIDER.set(str(by)[:80])
    try:
        out = kind["run"](a["detail"]) if kind else {"ok": False, "text": "This box can no longer run it."}
    except Exception as e:                       # noqa: BLE001 — the owner reads what happened
        log.error("approvals.run_failed", id=aid, kind=a["kind"], error=f"{type(e).__name__}: {e}"[:200])
        out = {"ok": False, "text": "It couldn't be done. Nothing reports having changed."}
    finally:
        _DECIDER.reset(token)
    status = "done" if out.get("ok") else "failed"
    with state.connect() as c:
        c.execute("UPDATE approvals SET status=?, result=? WHERE id=?",
                  (status, json.dumps({"text": str(out.get("text") or "")[:4000]}), aid))
    log.info("approvals.ran", id=aid, kind=a["kind"], status=status, by=by)
    return {"ok": status == "done", "status": status, "text": str(out.get("text") or "")[:4000]}
