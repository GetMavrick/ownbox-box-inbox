"""Smoke test the kernel spine WITHOUT spending a cent or hitting the network.

Exercises state + queue + idempotency + the spend ledger using a throwaway DB.
Run: python scripts/smoke_test.py   (expects to print PASS)
"""
import os
import sys
import tempfile

# Make `python scripts/smoke_test.py` work standalone (no pip install -e . needed).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point at a throwaway DB before importing anything that reads settings.
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "smoke.db")

from core import cost_guard, state          # noqa: E402
from core.queue import queue                # noqa: E402


def main() -> None:
    state.init_db()

    # 1) Idempotency: same key enqueued twice -> one job, second flagged duplicate.
    job1, created1 = queue.enqueue(idempotency_key="k-123", agent_name="mavrick",
                                   intent="reel", raw_text="make a reel about X")
    job2, created2 = queue.enqueue(idempotency_key="k-123", agent_name="mavrick",
                                   intent="reel", raw_text="make a reel about X")
    assert created1 is True, "first enqueue should create"
    assert created2 is False, "duplicate key must NOT create a second job"
    assert job1["id"] == job2["id"], "duplicate must return the same job_id"

    # 2) Claim -> complete moves the job through its lifecycle.
    claimed = queue.claim_next()
    assert claimed and claimed["id"] == job1["id"], "should claim the queued job"
    assert claimed["status"] == "running"
    queue.complete(job1["id"], {"ok": True})
    assert state.get_job(job1["id"])["status"] == "done"

    # 3) Spend ledger accrues and is queryable by the cost guard.
    state.record_spend(task="score", model="claude-haiku-4-5-20251001",
                       cost_usd=0.0123, input_tokens=1000, output_tokens=200)
    mtd = cost_guard.month_to_date_spend()
    assert mtd >= 0.0123, f"ledger should reflect spend, got {mtd}"
    assert cost_guard.ceiling() == 90.0, "ceiling should load from config"

    print(f"PASS — idempotency, queue lifecycle, and spend ledger all work. "
          f"(MTD ${mtd:.4f} / ${cost_guard.ceiling():.0f})")


if __name__ == "__main__":
    main()
