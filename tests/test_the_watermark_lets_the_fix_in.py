"""Migration 52: the skip-marker is cleared once, so a fixed poller can reach conversations that exist.

WHAT IT IS FOR. `poller._sweep_channel` skips any conversation whose stored `last_activity` matches the
vendor's — the optimisation that keeps a quiet inbox cheap. Every conversation on the live box was
watermarked by the poller that recorded message directions wrongly, so #1334's fix would have been
invisible on all 72 of them: skipped before a message was re-read, and the owner's 8 AM mail counting
"who is waiting" from rows nobody could correct.

WHAT IT MUST NOT DO is clear `last_seen_msg_id`. That one is "which message have we already acted on";
clearing it offers every old inbound to the reply path as if it were new. The send window would hold
them, but a migration that relies on a second gate to undo its own damage is a bet, not a fix.

Run: python tests/test_the_watermark_lets_the_fix_in.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "wm.db")

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(label)


from core import state  # noqa: E402

state.init_db()

# THE TABLE IS THE MACHINE'S, NOT THE KERNEL'S — which is the whole reason step 52 is tagged
# `customer_voice`. A core-only database has no `inbox_state`, so this suite builds the shape the machine
# ships (the three columns the step and the poller's skip both read) rather than importing the machine,
# and therefore runs inside a Lead box too.
print("\n— the step itself —")
with state.connect() as c:
    c.execute("CREATE TABLE IF NOT EXISTS inbox_state ("
              "space TEXT NOT NULL, zernio_conversation_id TEXT NOT NULL, last_seen_msg_id TEXT, "
              "last_activity TEXT, updated_at TEXT, PRIMARY KEY (space, zernio_conversation_id))")
    c.execute("INSERT INTO inbox_state (space, zernio_conversation_id, last_seen_msg_id, last_activity, "
              "updated_at) VALUES (?,?,?,?,?)", ("acme", "z1", "msg_9", "2026-09-17T10:00:00Z", "now"))
    c.execute("INSERT INTO inbox_state (space, zernio_conversation_id, last_seen_msg_id, last_activity, "
              "updated_at) VALUES (?,?,?,?,?)", ("acme", "z2", "msg_4", "2026-09-16T08:00:00Z", "now"))
    state._migration_52(c)
    rows = {r["zernio_conversation_id"]: r for r in c.execute("SELECT * FROM inbox_state")}

ok("every conversation's skip-marker is cleared, so the next sweep re-reads it",
   all(r["last_activity"] is None for r in rows.values()),
   str({k: r["last_activity"] for k, r in rows.items()}))
ok("...and what the box has ALREADY acted on is untouched, so nothing is offered twice",
   rows["z1"]["last_seen_msg_id"] == "msg_9" and rows["z2"]["last_seen_msg_id"] == "msg_4")

print("\n— run it again (a migration must survive a replay) —")
with state.connect() as c:
    state._migration_52(c)
    again = [r["last_activity"] for r in c.execute("SELECT last_activity FROM inbox_state")]
ok("a second run is a no-op rather than an error", all(a is None for a in again))

print("\n— a box that never had an inbox —")
import sqlite3  # noqa: E402

bare = sqlite3.connect(os.path.join(_T, "bare.db"))
bare.row_factory = sqlite3.Row
try:
    state._migration_52(bare)
    ok("a Lead or Content box skips it instead of failing its boot", True)
except Exception as e:  # noqa: BLE001
    ok("a Lead or Content box skips it instead of failing its boot", False, f"{type(e).__name__}: {e}")

print("\n— the kernel knows whose it is —")
ok("it is tagged customer_voice, like every other step that touches that table",
   state._MIGRATION_OWNER.get(52) == "customer_voice", str(state._MIGRATION_OWNER.get(52)))
ok("...and the schema version moved with it", state.SCHEMA_VERSION >= 52, str(state.SCHEMA_VERSION))

src = (ROOT / "core" / "state.py").read_text()
body = src[src.index("def _migration_52"):src.index("def ", src.index("def _migration_52") + 10)]
ok("the step clears ONLY last_activity — last_seen_msg_id is never named in it",
   "last_activity" in body and "last_seen_msg_id" not in body.split('"""')[-1])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
