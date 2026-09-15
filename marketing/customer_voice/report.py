"""The Customer Voice segment of the Morning Review — the first of the three, by the owner's order.

Reads the local database and nothing else: no network, no model, no person. The rails that need
an account are absent until they exist, and the two that do not report real numbers from the
first poll — which is what stops a new box's first morning reading as a broken one.

FOUR STATES, RENDERED (docs/PLAN_CUSTOMER_VOICE.md §1.10). A rail he does not own is absent
entirely; one he owns but has not connected says so and is `connect`, never `fail`. Getting that
backwards is how a dashboard turns an activation step into a support ticket.
"""
from datetime import date

from core import state
from core.report import register_reporter, window

from . import rails
from .competitors import roster as competitors
from .seo import health

MACHINE = "customer_voice"
# OWNER, 2026-09-13: "There is no such thing as Customer Voice. It is either Unified Inbox or Ownbox."
# The buyer-facing name. The package and box type keep their internal name; this string is what
# the morning page, the phone app and the box docs show.
TITLE = "Unified Inbox"

# What each rail is called on the page, and what it says when it is owned but not yet connected.
# The connect line NAMES THE ACCOUNT, because "not connected" is not an instruction anybody can
# act on and "connect your Google Business Profile" is.
# A CONNECT LINE MUST NAME THE REASON IT IS ACTUALLY WAITING. The first version gave each rail
# ONE hint, so a box with `site_url` already set was told to "point customer_voice.site_url at
# your website" — an instruction to do a thing that was already done. MEASURED on the live box
# 2026-09-09, minutes after Stage 1 deployed. Telling someone to fix what they have fixed is how
# a dashboard teaches them to stop reading it, which is the same failure as a false red light.
LABEL = {
    "uptime": ("Site", "point customer_voice.site_url at your website"),
    "pagespeed": ("Speed", "point customer_voice.site_url at your website"),
    "reviews": ("Reviews", "connect your Google Business Profile"),
    "comments": ("Comments", "connect your Facebook and Instagram accounts"),
    "seo": ("Search", "connect Search Console and Analytics"),
    "competitors": ("Competitors", "name them in customer_voice.competitors.roster"),
}


def _n(c, sql: str, args: tuple = ()) -> int:
    return int(c.execute(sql, args).fetchone()[0] or 0)


def _rail_line(rail: str, st: str) -> dict | None:
    """One `watch` entry, or None when the rail renders nothing at all."""
    name, connect_hint = LABEL.get(rail, (rail.title(), "connect it"))
    if st is None:
        return None
    if st == rails.CONNECT:
        # A rail that HAS run and hit something he can fix explains itself in its own words —
        # "PageSpeed's free anonymous quota is used up" beats a generic "connect it".
        said = rails.explain(rail)
        if said:
            return {"text": f"{name} — {said}", "state": rails.CONNECT}
        # NOTHING IS WRONG AND NOTHING IS MISSING — it simply has not run yet. Say that, rather
        # than repeating a setup instruction the box can see has already been carried out.
        if rail in rails.NO_AUTH and health.configured():
            return {"text": f"{name} — waiting for its first check", "state": rails.CONNECT}
        # NOT A FAILURE. Owner, 2026-09-09: an owned-but-unconnected rail says "connect your
        # Google Business Profile". Same pixel as a red line, opposite outcome — one reads as a
        # broken machine, the other as the next step.
        return {"text": f"{name} — {connect_hint}", "state": rails.CONNECT}
    if st == rails.FAIL:
        h = rails.health(rail)
        since = (h.get("last_ok_at") or "")[:10]
        return {"text": f"{name} — we could not reach it" + (f" since {since}" if since else " yet"),
                "state": rails.FAIL}
    return None                                     # ok rails speak through their numbers below


def report(day: date) -> dict:
    lo, hi = window(day)
    owned = rails.declared()
    figures: dict = {}
    happened: list = []
    watch: list = []
    needs_you: list = []

    with state.connect() as c:
        checks = _n(c, "SELECT COUNT(*) FROM voice_observations WHERE source = 'uptime' "
                       "AND recorded_at >= ? AND recorded_at < ?", (lo, hi))
        down = _n(c, "SELECT COUNT(*) FROM voice_observations WHERE source = 'uptime' "
                     "AND recorded_at >= ? AND recorded_at < ? AND payload LIKE '%\"up\": false%'", (lo, hi))
        slow = _n(c, "SELECT COUNT(*) FROM voice_observations WHERE source = 'uptime' "
                     "AND recorded_at >= ? AND recorded_at < ? AND payload LIKE '%\"slow\": true%'", (lo, hi))
        row = c.execute("SELECT AVG(value) a FROM voice_observations WHERE source = 'uptime' "
                        "AND recorded_at >= ? AND recorded_at < ?", (lo, hi)).fetchone()
        avg_ms = round(float(row["a"])) if row and row["a"] is not None else None
        ps = c.execute("SELECT value FROM voice_metrics WHERE source = 'pagespeed' "
                       "AND metric = 'performance' AND day <= ? ORDER BY day DESC LIMIT 1",
                       (day.isoformat(),)).fetchone()
        ps_score = round(float(ps["value"])) if ps else None
        lcp = c.execute("SELECT value FROM voice_metrics WHERE source = 'pagespeed' "
                        "AND metric = 'lcp_ms' AND day <= ? ORDER BY day DESC LIMIT 1",
                        (day.isoformat(),)).fetchone()
        lcp_ms = round(float(lcp["value"])) if lcp and lcp["value"] else None

    if "uptime" in owned:
        up_pct = (100.0 * (checks - down) / checks) if checks else None
        happened.append({"text": "checks on your site", "value": checks})
        happened.append({"text": "times it did not answer", "value": down})
        figures["uptime"] = {"value": (f"{up_pct:.1f}%" if up_pct is not None else "—"),
                             "label": "of checks answered today"}
        if avg_ms is not None:
            figures["response_ms"] = {"value": avg_ms, "label": "milliseconds to answer, average"}
        if down:
            # THE ONLY THING ON THIS SEGMENT THAT IS AN INSTRUCTION. A site that did not answer
            # is not a metric to read at leisure; it is the front door, shut, while people knock.
            needs_you.append({"text": f"Your site did not answer {down} "
                                      f"{'check' if down == 1 else 'checks'} today", "href": "/app/"})
            watch.append({"text": f"Site — {down} failed {'check' if down == 1 else 'checks'} today",
                          "state": rails.FAIL})
        elif slow:
            watch.append({"text": f"Site — answered slowly {slow} "
                                  f"{'time' if slow == 1 else 'times'} today", "state": rails.WARN})
        elif checks:
            watch.append({"text": f"Site — answering, {avg_ms}ms average", "state": rails.OK})

    if "pagespeed" in owned and ps_score is not None:
        happened.append({"text": "PageSpeed score", "value": ps_score})
        figures["pagespeed"] = {"value": ps_score, "label": "PageSpeed performance, out of 100"}
        if lcp_ms:
            figures["lcp_ms"] = {"value": lcp_ms, "label": "milliseconds to the largest paint"}
        # GOOGLE'S OWN BANDS, not ours: 90+ is good, 50-89 needs improvement, under 50 is poor.
        watch.append({"text": f"Speed — PageSpeed {ps_score} of 100",
                      "state": rails.OK if ps_score >= 90 else
                      (rails.WARN if ps_score >= 50 else rails.FAIL)})

    if "competitors" in owned:
        rows = competitors.standings()
        mine = next((r for r in rows if r["is_self"]), None)
        # RANKED, not merely rated: a listing with too few reviews is shown but does not take a
        # position, because a 5.0 from two people is not a place on a table.
        rated = [r for r in rows if r.get("ranked")]
        thin = [r for r in rows if r.get("rating") is not None and not r.get("ranked")]
        if mine and mine.get("rating") is not None and len(rated) > 1:
            # WHERE HE STANDS, which is the whole reason this rail is worth reading. Position is
            # computed over the rows that actually have a rating — a competitor Places could not
            # read is not silently counted as last.
            place = [r["slug"] for r in rated].index(mine["slug"]) + 1
            best = rated[0]
            figures["rank"] = {"value": f"{place} of {len(rated)}", "label": "by rating, among the ones you named"}
            figures["my_rating"] = {"value": mine["rating"], "label": f"your rating, {int(mine['reviews_total'] or 0)} reviews"}
            if best["slug"] != mine["slug"]:
                figures["best_rival"] = {"value": best["rating"],
                                         "label": f"best of theirs — {best['label'][:40]}"}
            happened.append({"text": "competitors read", "value": len(rated) - 1})
            watch.append({"text": f"Competitors — you are {place} of {len(rated)} by rating",
                          "state": rails.OK if place == 1 else rails.WARN})
        elif rated or thin:
            figures["watched"] = {"value": len(rated) + len(thin), "label": "listings being watched"}
        if thin:
            watch.append({"text": f"Competitors — {len(thin)} shown but not ranked, "
                                  f"under {competitors.min_reviews()} reviews", "state": rails.OK})

    # Every rail he owns gets a line when it is waiting on him or failing. A rail he does not own
    # is absent — no line, no zero, no nag.
    for rail in rails.ALL:
        st = rails.state_of(rail)
        if st in (rails.CONNECT, rails.FAIL):
            line = _rail_line(rail, st)
            if line and not any(line["text"].split(" — ")[0] == w["text"].split(" — ")[0] for w in watch):
                watch.append(line)

    headline = ps_score if ps_score is not None else (checks - down)
    label = "PageSpeed score" if ps_score is not None else "good checks today"
    if not owned:
        # A BOX THAT OWNS NO RAIL IS NOT A BROKEN ONE. It renders as a machine waiting to be
        # told what this business has, which is a setup step and reads like one.
        headline, label = 0, "rails set up"
        watch = [{"text": "Nothing set up yet — say which of these this business has",
                  "state": rails.CONNECT}]

    return {"title": TITLE,
            "headline": {"value": headline, "label": label},
            "needs_you": needs_you, "happened": happened, "watch": watch,
            "figures": figures, "notes": []}


register_reporter(MACHINE, TITLE, report)
