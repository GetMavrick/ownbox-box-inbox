# AIOS `core/` — the kernel

The shared foundation every department plugs into. Built against the AIOS technical
spec (Commercial-Terms API cost model, poll-only ingress, single-tenant). No business
logic lives here.

## What's in it

| File | Role | Status |
|---|---|---|
| `config.py` | env + `aios.config.yaml` loader | real |
| `aios.config.yaml` | model tiering, `$90` ceiling, rate table | real (verify rates/models) |
| `state.py` | SQLite (WAL): spend ledger, jobs w/ unique idempotency_key, heartbeats, alerts | real |
| `cost_guard.py` | `$90` month-to-date governor, pinned to the billing cycle | real |
| `brain.py` | `think()` — the one reasoning gateway: Messages API + tiering + prompt caching | real |
| `queue.py` | `QueueInterface` (swappable) over SQLite | real |
| `dispatch.py` | the one inbound endpoint: bearer auth, idempotency, POST/GET | real |
| `worker.py` | claims jobs, routes by intent, runs module handlers | real (handlers come from modules) |
| `slack.py` | notifier (zero Claude); no-ops without a token | real |
| `watchdog.py` | health probes + transition alerts + spend line | real (zero-spend) |
| `scripts/` | `init_db`, `check_budget`, `smoke_test` | real |

## Get running

```bash
pip install -e .
cp .env.example .env          # fill ANTHROPIC_API_KEY + DISPATCH_BEARER_TOKEN at minimum
python scripts/smoke_test.py  # no cost, no network — verifies the spine
python tests/test_kernel.py # no cost — idempotency, atomic claim, budget-pause requeue
python scripts/init_db.py     # create the real DB
# /dispatch ingress — production: gunicorn behind a TLS proxy (Caddy/nginx), not `flask run`:
gunicorn -w 2 -b 127.0.0.1:8000 core.dispatch:app
python -m core.worker                     # the job worker (separate process)
python -m core.watchdog                   # one health pass (wire to a ~30-min systemd timer)
```

## How a department module plugs in

A module owns its parsing and logic, then registers a handler for its intent:

```python
# marketing/content_machine/reel/__init__.py
from core.worker import register

def handle(job: dict) -> dict:
    # job has: raw_text, agent_name, intent, slack_channel_id, id
    # parse raw_text here (AIOS owns parsing), run the pipeline, return a result dict
    ...
    return {"video_url": "...", "status": "ready"}

register("reel", handle)
```

The worker calls `handle(job)` for every `reel` job, records the result, and exposes
it via `GET /dispatch/<job_id>`. Reasoning inside the module must go through
`core.brain.think(task=..., prompt=...)` — never call an LLM SDK directly, so the cost
guard and ledger stay authoritative.

## Cost model (locked)

- `think()` uses the **Anthropic Messages API under Commercial Terms** (this deployment's
  own `ANTHROPIC_API_KEY`). Never a subscription/OAuth token.
- **Layer 1:** set a hard monthly spend limit on the key in the Anthropic console — and
  pin it to the same `billing_cycle_day`/`timezone` as `aios.config.yaml`.
- **Layer 2:** the `$90` `cost_guard` pauses reasoning when MTD spend hits the ceiling.
- **Layer 3:** Haiku for scoring/copy/routing, Sonnet only for rewriting, prompt caching
  on large reused context.

## Notes

- Flat modules now (`core/dispatch.py`, etc.); promote to packages as they grow.
- `state.py` and `queue.py` import with the standard library only — that's why
  `smoke_test.py` runs with no SDK and no network.
- Back up the SQLite file with **Litestream** to object storage (spec §5/§9).
