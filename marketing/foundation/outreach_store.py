"""The outreach store: Instantly's campaigns, their steps by day and their weeks, written by the pull
(outreach_sync.py) and read by the seam. Local DB only. Every row is keyed by Instantly's campaign id.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import state

_NUMS = ("sent", "contacted", "opened", "clicked", "replied", "bounced", "unsubscribed", "opportunities")
_STEP_NUMS = ("sent", "opened", "clicked", "replied", "booked")


def write_campaigns(rows: list[dict]) -> None:
    """Each campaign's label and status, refreshed. Its history and its `pulled_through` are kept: a rename is a
    new label on the same id, never a new campaign."""
    now = state._now()
    with state.connect() as c:
        c.executemany("INSERT INTO out_campaigns (campaign_id, name, status, synced_at) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT(campaign_id) DO UPDATE SET name = excluded.name, status = excluded.status, "
                      "synced_at = excluded.synced_at",
                      [(r["id"], r.get("name") or "", r.get("status"), now) for r in rows])


def campaigns() -> list[dict]:
    with state.connect() as c:
        return [dict(r) for r in c.execute("SELECT campaign_id, name, status, pulled_through FROM out_campaigns "
                                           "ORDER BY name, campaign_id").fetchall()]


def pulled_through(campaign_id: str) -> date | None:
    with state.connect() as c:
        r = c.execute("SELECT pulled_through FROM out_campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
    try:
        return date.fromisoformat(r["pulled_through"]) if r and r["pulled_through"] else None
    except ValueError:
        return None


def set_pulled_through(campaign_id: str, d: date) -> None:
    with state.connect() as c:
        c.execute("UPDATE out_campaigns SET pulled_through = ? WHERE campaign_id = ?", (d.isoformat(), campaign_id))


def write_step_day(campaign_id: str, day: date, steps: dict[str, dict]) -> None:
    """Replace one campaign's day in one transaction, so a reader never sees half of it."""
    d, now = day.isoformat(), state._now()
    with state.connect() as c:
        c.execute("DELETE FROM out_step_days WHERE campaign_id = ? AND day = ?", (campaign_id, d))
        c.executemany("INSERT INTO out_step_days (campaign_id, day, step, sent, opened, clicked, replied, booked, "
                      "synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [(campaign_id, d, step, *(int(v.get(k) or 0) for k in _STEP_NUMS), now)
                       for step, v in steps.items()])


def write_week_totals(start: date, end: date, totals: dict[str, dict]) -> None:
    """Every campaign's whole-week row (step ''). Its `booked` is its steps' sum, written by write_week_steps, and
    is left as it is here, so a reader between the two writes never sees a week's bookings drop to nothing."""
    s, e, now = start.isoformat(), end.isoformat(), state._now()
    cols = ", ".join(_NUMS)
    with state.connect() as c:
        c.executemany(f"INSERT INTO out_weeks (campaign_id, start_day, end_day, step, {cols}, synced_at) "
                      f"VALUES (?, ?, ?, '', {', '.join('?' * len(_NUMS))}, ?) "
                      "ON CONFLICT(campaign_id, start_day, end_day, step) DO UPDATE SET "
                      + ", ".join(f"{k} = excluded.{k}" for k in _NUMS) + ", synced_at = excluded.synced_at",
                      [(cid, s, e, *(int(v.get(k) or 0) for k in _NUMS), now) for cid, v in totals.items()])


def write_week_steps(campaign_id: str, start: date, end: date, steps: dict[str, dict]) -> None:
    """One campaign's steps for the week, replaced, and its whole-week `booked` set to their sum."""
    s, e, now = start.isoformat(), end.isoformat(), state._now()
    with state.connect() as c:
        c.execute("DELETE FROM out_weeks WHERE campaign_id = ? AND start_day = ? AND end_day = ? AND step != ''",
                  (campaign_id, s, e))
        c.executemany("INSERT INTO out_weeks (campaign_id, start_day, end_day, step, sent, opened, clicked, replied, "
                      "booked, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [(campaign_id, s, e, step, *(int(v.get(k) or 0) for k in _STEP_NUMS), now)
                       for step, v in steps.items() if step != ""])
        c.execute("UPDATE out_weeks SET booked = ? WHERE campaign_id = ? AND start_day = ? AND end_day = ? "
                  "AND step = ''", (sum(int(v.get("booked") or 0) for v in steps.values()), campaign_id, s, e))


def week_rows(start: date, end: date) -> list[dict]:
    with state.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM out_weeks WHERE start_day = ? AND end_day = ? "
                                           "ORDER BY campaign_id, step", (start.isoformat(), end.isoformat()))]


def day_rows(day: date) -> list[dict]:
    with state.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM out_step_days WHERE day = ? ORDER BY campaign_id, step",
                                           (day.isoformat(),))]


def prune(keep_days: int = 400) -> None:
    """Kept as long as the website store keeps its days (store.prune)."""
    cutoff = (date.today() - timedelta(days=int(keep_days))).isoformat()
    with state.connect() as c:
        c.execute("DELETE FROM out_step_days WHERE day < ?", (cutoff,))
        c.execute("DELETE FROM out_weeks WHERE end_day < ?", (cutoff,))
