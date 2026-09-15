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
        """Returns (job_dict, created: bool). Idempotent on idempotency_key."""
        return state.create_job(idempotency_key=idempotency_key, **kwargs)

    def claim_next(self):
        return state.claim_next_job()

    def complete(self, job_id, result):
        state.update_job(job_id, status="done", result=json.dumps(result or {}))

    def fail(self, job_id, error):
        state.update_job(job_id, status="failed", error=str(error)[:1000])


queue: QueueInterface = SQLiteQueue()
