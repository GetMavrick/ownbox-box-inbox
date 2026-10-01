"""The CRO daily report: yesterday on ownbox.io and brian-macdonald.com, from PostHog.

Owner, 2026-10-01: OSDev5 CRO reports every morning at 8 AM Pacific on yesterday's traffic and
conversions, with advice for today. This prints the numbers; the advice is written from them.

Deterministic (no model): HogQL queries against the one PostHog project, read with the personal
API key in NEXT_PRIVATE_POSTHOG_KEY. "Yesterday" is the Pacific calendar day, the project's own
timezone. Usage: python scripts/cro_daily_report.py [YYYY-MM-DD]
"""
import json
import os
import sys
import urllib.request
from datetime import date, timedelta

PROJECT = 417536
API = f"https://us.posthog.com/api/projects/{PROJECT}/query/"
SITES = ("www.ownbox.io", "brian-macdonald.com")
DASHBOARD = "https://us.posthog.com/project/417536/dashboard/2159294"
# The conversion events and actions, per site (PostHog actions 372143-372149 mirror these).
CONVERSIONS = {
    "www.ownbox.io": [
        ("Checkout click", "event = '$autocapture' and (elements_chain_href like '%buy.stripe.com%' or elements_chain_href like '%checkout.stripe.com%')"),
        ("Book a call", "event = '$autocapture' and elements_chain_href like '%calendar.app.google%'"),
        ("Contact form sent", "event = '$autocapture' and properties.$event_type = 'submit' and properties.$pathname like '/contact%'"),
    ],
    "brian-macdonald.com": [
        ("Intake submitted", "event = 'build_submit'"),
        ("Buy click", "event = 'product_buy_click'"),
        ("Intro email click", "event = 'intro_email_click'"),
        ("CTA click", "event = 'cta_click'"),
    ],
}


def q(hogql: str) -> list:
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["NEXT_PRIVATE_POSTHOG_KEY"],
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r).get("results") or []


def day_filter(d: date) -> str:
    return f"toDate(toTimeZone(timestamp, 'America/Los_Angeles')) = toDate('{d.isoformat()}')"


def traffic(host: str, d: date) -> tuple:
    r = q(f"select count(), count(distinct person_id), count(distinct $session_id) from events "
          f"where event = '$pageview' and properties.$host = '{host}' and {day_filter(d)}")
    return tuple(r[0]) if r else (0, 0, 0)


def conversions(host: str, d: date) -> list:
    out = []
    for name, cond in CONVERSIONS[host]:
        r = q(f"select count(), count(distinct $session_id) from events "
              f"where properties.$host = '{host}' and ({cond}) and {day_filter(d)}")
        out.append((name, r[0][0] if r else 0, r[0][1] if r else 0))
    return out


def top(host: str, d: date, prop: str, n: int = 5) -> list:
    return q(f"select {prop} p, count(distinct $session_id) s from events where event = '$pageview' "
             f"and properties.$host = '{host}' and {day_filter(d)} group by p order by s desc limit {n}")


def referrers(host: str, d: date, n: int = 10) -> list:
    """Each referring site, its visits, and every page those visits viewed (owner, 2026-10-01: "a
    daily report of referring websites and which URLs they visited"). A visit is attributed to the
    site that referred its first page view, so later clicks inside the visit count under it too."""
    rows = q(f"""
        select ref, count(distinct sid) visits, groupArray((path, views)) pages from (
          select s.ref ref, e.$session_id sid, e.properties.$pathname path, count() views
          from events e
          join (select $session_id sid, argMin(properties.$referring_domain, timestamp) ref
                from events where event = '$pageview' and properties.$host = '{host}' and {day_filter(d)}
                group by sid) s on e.$session_id = s.sid
          where e.event = '$pageview' and e.properties.$host = '{host}' and {day_filter(d).replace('timestamp', 'e.timestamp')}
          group by ref, sid, path)
        group by ref order by visits desc limit {n}""")
    out = []
    for ref, visits, pages in rows:
        totals = {}
        for path, views in pages:
            totals[path] = totals.get(path, 0) + views
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:8]
        out.append((ref or "$direct", visits, ranked))
    return out


def main() -> None:
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    if d is None:
        today = q("select toDate(toTimeZone(now(), 'America/Los_Angeles'))")[0][0]
        d = date.fromisoformat(str(today)) - timedelta(days=1)
    prev, week = d - timedelta(days=1), d - timedelta(days=7)
    print(f"# CRO daily report — {d:%A %B %-d, %Y} (Pacific)\n\nDashboard: {DASHBOARD}\n")
    for host in SITES:
        pv, users, sessions = traffic(host, d)
        p_users, w_users = traffic(host, prev)[1], traffic(host, week)[1]
        print(f"## {host}\n")
        print(f"- Visitors {users} (day before {p_users}, same day last week {w_users}); "
              f"sessions {sessions}; page views {pv}")
        convs = conversions(host, d)
        total = sum(c[2] for c in convs)
        rate = f"{100 * total / sessions:.1f}%" if sessions else "n/a"
        print(f"- Conversions: {total} sessions converted, {rate} of sessions")
        for name, n, s in convs:
            print(f"  - {name}: {n}")
        for label, prop in (("UTM", "properties.utm_source"), ("Landing pages", "properties.$pathname")):
            rows = [r for r in top(host, d, prop) if r[0] not in (None, "")]
            print(f"- {label}: " + (", ".join(f"{r[0]} ({r[1]})" for r in rows) if rows else "none"))
        print("\n### Referring sites and the pages their visitors saw\n")
        refs = referrers(host, d)
        if not refs:
            print("No visits.")
        for ref, visits, pages in refs:
            print(f"- **{ref}**: {visits} visit{'s' if visits != 1 else ''}, saw " +
                  ", ".join(f"{p} ({n})" for p, n in pages))
        print()


if __name__ == "__main__":
    main()
