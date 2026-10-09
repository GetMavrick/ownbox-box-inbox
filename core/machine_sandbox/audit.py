"""Every call a sandboxed machine made through its door: what, under which permission, and what happened (D18).

Kept in the box's own database (machine_audit), where no sandboxed machine can write, for 90 days, and shown on
the machine's page. A fetch records its HOST and the bytes each way, never the path or the body: a path can
carry a secret, and the point is to see what left the box, not to keep a second copy of it.

The same rows are the meter behind a machine's monthly AI count (`count`), and later the data-out meter that
pauses a machine on a spike (I7, step 4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import state

KEEP_DAYS = 90


def record(machine: str, call: str, *, ok: bool, version: str = "", permission: str = "", code: str = "",
           host: str = "", bytes_out: int = 0, bytes_in: int = 0) -> None:
    with state.connect() as c:
        c.execute("INSERT INTO machine_audit (at, machine, version, call, permission, ok, code, host, bytes_out, "
                  "bytes_in) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (state._now(), machine, str(version)[:40], str(call)[:40], str(permission)[:20], 1 if ok else 0,
                   str(code)[:40], str(host)[:253], int(bytes_out), int(bytes_in)))


def recent(machine: str, limit: int = 100) -> list[dict]:
    with state.connect() as c:
        rows = c.execute("SELECT * FROM machine_audit WHERE machine = ? ORDER BY id DESC LIMIT ?",
                         (machine, int(limit))).fetchall()
    return [dict(r) for r in rows]


def month_start(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def count(machine: str, call: str, *, since: str, ok_only: bool = True) -> int:
    with state.connect() as c:
        return int(c.execute("SELECT COUNT(*) FROM machine_audit WHERE machine = ? AND call = ? AND at >= ?"
                             + (" AND ok = 1" if ok_only else ""), (machine, call, since)).fetchone()[0])


def size(machine: str, call: str, *, since: str) -> int:
    """Characters out and back for one machine's successful calls since `since`: the meter behind its AI size."""
    with state.connect() as c:
        return int(c.execute("SELECT COALESCE(SUM(bytes_out + bytes_in), 0) FROM machine_audit WHERE machine = ? "
                             "AND call = ? AND at >= ? AND ok = 1", (machine, call, since)).fetchone()[0])


def totals(machine: str, *, since: str) -> dict:
    """What left and arrived since `since`: {"calls", "refused", "bytes_out", "bytes_in", "hosts": [...]}."""
    with state.connect() as c:
        row = c.execute("SELECT COUNT(*) AS calls, SUM(1 - ok) AS refused, COALESCE(SUM(bytes_out), 0) AS bytes_out, "
                        "COALESCE(SUM(bytes_in), 0) AS bytes_in FROM machine_audit WHERE machine = ? AND at >= ?",
                        (machine, since)).fetchone()
        hosts = [r[0] for r in c.execute("SELECT DISTINCT host FROM machine_audit WHERE machine = ? AND at >= ? "
                                         "AND host != '' AND ok = 1 ORDER BY host", (machine, since))]
    return {"calls": int(row["calls"] or 0), "refused": int(row["refused"] or 0), "bytes_out": int(row["bytes_out"]),
            "bytes_in": int(row["bytes_in"]), "hosts": hosts}


def prune(days: int = KEEP_DAYS) -> int:
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with state.connect() as c:
        return c.execute("DELETE FROM machine_audit WHERE at < ?", (cut,)).rowcount
