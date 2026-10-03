"""QueueInterface keeps the job backend swappable (spec invariant).

Default implementation is SQLite via core.state. Swap to Postgres later by
implementing the same four methods — callers never change.
"""
import json

from core import state


class QueueInterface:
    def enqueue(self, **kwargs):
        raise NotImplementedError

    def claim_next(self):
        raise NotImplementedError

    def complete(self, job_id, result):
        raise NotImplementedError

    def fail(self, job_id, error):
        raise NotImplementedError


class SQLiteQueue(QueueInterface):
    def enqueue(self, *, idempotency_key, **kwargs):
        """Returns (job_dict, created: bool). Idempotent on idempotency_key.

        Raises ValueError for an intent no machine on this box answers (refused_intent), so the caller's own error
        names it, never a job that is queued only to fail."""
        why = refused_intent(str(kwargs.get("intent") or ""))
        if why:
            raise ValueError(why)
        return state.create_job(idempotency_key=idempotency_key, **kwargs)

    def claim_next(self):
        return state.claim_next_job()

    def complete(self, job_id, result):
        state.update_job(job_id, status="done", result=json.dumps(result or {}))

    def fail(self, job_id, error):
        state.update_job(job_id, status="failed", error=str(error)[:1000])


INTENTS_NS, INTENTS_KEY = "core", "intents"


def modules_now() -> list[str]:
    """The machine modules this box would load now: config `modules:` and every discovered pack's module."""
    from core.config import get_config
    mods = [str(p) for p in (get_config().get("modules") or [])]
    try:
        from core import packs
        mods += [str(m["module"]) for m in packs.discover() if m.get("module")]
    except Exception:                                    # noqa: BLE001 — a pack that won't read is the worker's to log
        pass
    return sorted(mods)


def refused_intent(intent: str) -> str:
    """"" when this box can take a job for `intent`, else the sentence why not (plan #1857 H6).

    THE WORKER IS THE ONLY PROCESS THAT KNOWS. Handlers register when the worker imports its modules, and the web
    process that also enqueues never does (the 09-18 two-process gotcha), so the worker publishes what it answers
    when it starts (worker.publish_intents). Until it has, nothing is refused. A machine installed since then (the
    box's modules differ from the ones it published with) is accepted too: its job waits for the restart.
    """
    if not intent:
        return ""
    try:
        from core import box_settings
        pub = box_settings.get(INTENTS_NS, INTENTS_KEY) or {}
    except Exception:                                    # noqa: BLE001 — no settings table yet: refuse nothing
        return ""
    if not pub or intent in (pub.get("intents") or ()) or pub.get("modules") != modules_now():
        return ""
    return f"no machine on this box answers '{intent}'"


queue: QueueInterface = SQLiteQueue()
