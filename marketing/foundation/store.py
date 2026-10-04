"""The store: a site's day written once, replaced on a re-sync, read by the seam. Local DB only."""
from __future__ import annotations

import json
from datetime import date, timedelta

from core import state


def write_day(site: str, day: date, *, traffic: tuple[int, int, int], conversions: list[tuple[str, int, int]],
              pages: list[tuple[str, int]], utms: list[tuple[str, int]],
              referrers: list[tuple[str, int, list[tuple[str, int]]]],
              left_out: dict[str, int] | None = None, unchecked: int = 0,
              page_conversions: list[tuple[str, int]] | None = None) -> None:
    """Replace everything stored for (site, day) in one transaction, so a reader never sees half a day. `left_out` is
    the visits visitors.py left out, by reason; `unchecked` the kept visits whose address couldn't be checked;
    `page_conversions` the visits that read an article and converted, per article (posthog.article_conversions)."""
    d = day.isoformat()
    pv, visitors, sessions = (int(x or 0) for x in traffic)
    converted = sum(int(s or 0) for _, _, s in conversions)
    rows = ([(site, d, "conversion", name, int(s or 0), str(int(n or 0))) for name, n, s in conversions]
            + [(site, d, "page", path, int(n or 0), None) for path, n in pages]
            + [(site, d, "utm", src, int(n or 0), None) for src, n in utms]
            + [(site, d, "referrer", ref, int(v or 0), json.dumps(pg)) for ref, v, pg in referrers]
            + [(site, d, "left_out", why, int(n or 0), None) for why, n in (left_out or {}).items() if n]
            + ([(site, d, "unchecked", "visits", int(unchecked), None)] if unchecked else [])
            + [(site, d, "page_conversion", path, int(n or 0), None) for path, n in (page_conversions or []) if n])
    with state.connect() as c:
        c.execute("DELETE FROM web_site_day_items WHERE site = ? AND day = ?", (site, d))
        c.executemany("INSERT OR REPLACE INTO web_site_day_items (site, day, kind, name, n, detail) "
                      "VALUES (?, ?, ?, ?, ?, ?)", rows)
        c.execute("INSERT OR REPLACE INTO web_site_days (site, day, pageviews, visitors, sessions, converted, synced_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (site, d, pv, visitors, sessions, converted, state._now()))


def write_search(site: str, day: date, clicks: int, impressions: int, position: float | None) -> None:
    with state.connect() as c:
        c.execute("INSERT OR REPLACE INTO web_site_day_search (site, day, clicks, impressions, position, synced_at) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (site, day.isoformat(), int(clicks or 0), int(impressions or 0),
                                                 position, state._now()))


def day_row(site: str, day: date) -> dict | None:
    with state.connect() as c:
        r = c.execute("SELECT * FROM web_site_days WHERE site = ? AND day = ?", (site, day.isoformat())).fetchone()
    return dict(r) if r else None


def items(site: str, day: date, kind: str) -> list[dict]:
    with state.connect() as c:
        rows = c.execute("SELECT name, n, detail FROM web_site_day_items WHERE site = ? AND day = ? AND kind = ? "
                         "ORDER BY n DESC, name", (site, day.isoformat(), kind)).fetchall()
    return [dict(r) for r in rows]


def days(site: str, start: date, end: date) -> list[dict]:
    """The stored days in [start, end], oldest first."""
    with state.connect() as c:
        rows = c.execute("SELECT * FROM web_site_days WHERE site = ? AND day >= ? AND day <= ? ORDER BY day",
                         (site, start.isoformat(), end.isoformat())).fetchall()
    return [dict(r) for r in rows]


def items_between(site: str, start: date, end: date, kind: str) -> list[tuple[str, int]]:
    """Names summed over the days in [start, end], largest first."""
    with state.connect() as c:
        rows = c.execute("SELECT name, SUM(n) n FROM web_site_day_items WHERE site = ? AND day >= ? AND day <= ? "
                         "AND kind = ? GROUP BY name ORDER BY n DESC, name",
                         (site, start.isoformat(), end.isoformat(), kind)).fetchall()
    return [(r["name"], int(r["n"] or 0)) for r in rows]


def rows_between(site: str, start: date, end: date, kind: str) -> list[tuple[str, int, str]]:
    """Each day's rows of `kind` in [start, end], unsummed: (name, n, detail). A referrer's detail is the pages its
    visits viewed that day, which a sum across days cannot keep."""
    with state.connect() as c:
        rows = c.execute("SELECT name, n, detail FROM web_site_day_items WHERE site = ? AND day >= ? AND day <= ? "
                         "AND kind = ? ORDER BY day, name", (site, start.isoformat(), end.isoformat(), kind)).fetchall()
    return [(r["name"], int(r["n"] or 0), r["detail"] or "") for r in rows]


def search_between(site: str, start: date, end: date) -> dict:
    with state.connect() as c:
        r = c.execute("SELECT SUM(clicks) c, SUM(impressions) i, AVG(position) p, COUNT(*) n FROM web_site_day_search "
                      "WHERE site = ? AND day >= ? AND day <= ?", (site, start.isoformat(), end.isoformat())).fetchone()
    return {"clicks": int(r["c"] or 0), "impressions": int(r["i"] or 0),
            "position": round(float(r["p"]), 1) if r["p"] is not None else None, "days": int(r["n"] or 0)}


def stored_sites() -> list[str]:
    with state.connect() as c:
        return [r["site"] for r in c.execute("SELECT DISTINCT site FROM web_site_days ORDER BY site").fetchall()]


def newest_day(site: str) -> date | None:
    with state.connect() as c:
        r = c.execute("SELECT MAX(day) d FROM web_site_days WHERE site = ?", (site,)).fetchone()
    return date.fromisoformat(r["d"]) if r and r["d"] else None


def prune(keep_days: int = 400) -> None:
    """The store keeps 400 days (plan §2); older rows go, every table alike."""
    cutoff = (date.today() - timedelta(days=int(keep_days))).isoformat()
    with state.connect() as c:
        for t in ("web_site_days", "web_site_day_items", "web_site_day_search"):
            c.execute(f"DELETE FROM {t} WHERE day < ?", (cutoff,))
