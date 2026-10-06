"""The Customer Voice segment of the Morning Review — the first of the three, by the owner's order.

Reads the local database and nothing else: no network, no model, no person. The rails that need
an account are absent until they exist, and the two that do not report real numbers from the
first poll — which is what stops a new box's first morning reading as a broken one.

FOUR STATES, RENDERED (docs/PLAN_CUSTOMER_VOICE.md §1.10). A rail he does not own is absent
entirely; one he owns but has not connected says so and is `connect`, never `fail`. Getting that
backwards is how a dashboard turns an activation step into a support ticket.
"""
import re
from datetime import date, datetime, timedelta, timezone

from core import state
from core.report import register_reporter, window

from . import rails
from .inbox import store as inbox_store
from .competitors import roster as competitors
from .seo import health

MACHINE = "customer_voice"
# OWNER, 2026-09-13: "There is no such thing as Customer Voice. It is either Unified Inbox or Ownbox."
# The buyer-facing name. The package and box type keep their internal name; this string is what
# the morning page, the phone app and the box docs show.
TITLE = "Inbox Machine"

# What each rail is called on the page, and what it says when it is owned but not yet connected.
# The connect line NAMES THE ACCOUNT, because "not connected" is not an instruction anybody can
# act on and "connect your Google Business Profile" is.
# A CONNECT LINE MUST NAME THE REASON IT IS ACTUALLY WAITING. The first version gave each rail
# ONE hint, so a box with `site_url` already set was told to "point customer_voice.site_url at
# your website" — an instruction to do a thing that was already done. MEASURED on the live box
# 2026-09-09, minutes after Stage 1 deployed. Telling someone to fix what they have fixed is how
# a dashboard teaches them to stop reading it, which is the same failure as a false red light.
# NO CONFIGURATION KEY EVER REACHES THIS PAGE. Three of these hints used to name one: `Site` and
# `Speed` both read "point customer_voice.site_url at your website" and `Competitors` read "name
# them in customer_voice.competitors.roster". A buyer met the first two on his FIRST screen after
# paying (measured 2026-09-15 by claiming an exported box and rendering it), under a heading that
# says "Worth watching" — so the product's opening move was to tell him to worry about something
# he cannot act on, in a language he does not speak, naming a YAML path he will never see.
#
# The rule, stated once here because this is where it broke: no config key, YAML path, env var or
# module name appears on a screen a customer can reach. These hints now say what the box needs in
# the same voice as the three that were always right ("connect your Google Business Profile").
#
# They deliberately do NOT name a place to go and do it. There is no field for a website address
# in the app today (`/inbox/settings` carries appearance, install and refresh), and sending a
# buyer to a screen that cannot help him is the same defect wearing politer words. When the field
# exists, these gain "in Settings" and not before.
LABEL = {
    # TWO RAILS, ONE ADDRESS, AND THEY MUST NOT SAY THE SAME SENTENCE. These two carried copy
    # that was byte-identical, so a buyer on day one read the same words twice under different
    # labels and could only conclude the screen was repeating itself. Found by rendering a box
    # exported with nothing connected and reading it, not by reading the dict.
    #
    # EACH ROW STILL NAMES THE MISSING FACT ON ITS OWN. My first fix made the second row read
    # "the same address, and ..." — which only parses while the first row is directly above it,
    # and these rows appear independently (a box can own `pagespeed` and not `uptime`).
    # test_customer_voice caught it, and its rule is the older scar: a hint owes the reader the
    # missing fact in his own words, never a config key. So both lead with the same ask, which is
    # the truth — one address, two different things watched — and differ where the eye lands.
    "uptime": ("Site", "tell Ownbox your website address and it will watch that it stays up"),
    "pagespeed": ("Speed", "tell Ownbox your website address and it will watch how fast it loads"),
    "reviews": ("Reviews", "connect your Google Business Profile"),
    "comments": ("Comments", "connect your Facebook and Instagram accounts"),
    "seo": ("Search", "connect Search Console and Analytics"),
    "competitors": ("Competitors", "name the businesses you want watched"),
}


def _n(c, sql: str, args: tuple = ()) -> int:
    return int(c.execute(sql, args).fetchone()[0] or 0)


def _space_name() -> str:
    """The Space this box's morning page reports for.

    THE BOX'S OWN, NOT "ALL OF THEM". Every count below is scoped to one Space because the store
    refuses an all-Spaces read on purpose (the tenant boundary), and summing across tenants on a
    page is how one client's numbers end up in another's morning. A sold box has exactly one
    Space, so this is the whole answer there; on the owner's multi-Space box it is the first,
    which is the same scope the rest of this segment's machines already use.

    NEVER RAISES. A reporter that threw would take the whole morning page down over a count."""
    try:
        from core import spaces
        return str((spaces.all_spaces() or [{}])[0].get("name") or spaces.DEFAULT)
    except Exception:                            # noqa: BLE001 — a page is worth more than a count
        return "default"


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
        # NOTHING IS WRONG AND NOTHING IS MISSING — it simply hasn't run yet, so there's no line at
        # all (owner, 2026-09-29: "If a line doesn't have data, it should not be displayed"). It was
        # "waiting for its first check", a line with nothing in it.
        if rail in rails.NO_AUTH and health.configured():
            return None
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


RECENT_DAYS = 30


def _iso(s: str) -> str:
    """A stored stamp in the one form the cutoff compares with: aware, UTC, ISO. "" when it can't be read."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


ANSWER_FIRST = 3
SAID_CHARS = 60                  # the words in `why`, the one line Slack-free text surfaces carry
SAID_CARD = 140                  # the words on the review's card for that person

# WHAT THEY WANT, READ FROM THEIR OWN WORDS (owner, 2026-10-06: "use some intelligence and inference to make that a good
# section ... speed to lead is where the money is at"). Plain patterns on the message the box already holds, no model:
# the review must never hand a customer's words to an AI (core/review_advisor.py), and a reason the owner can see is a
# reason the owner can check. Each is (the words on the card, the words in a sentence, its weight, the pattern).
_WANTS = (
    ("Needs help soon", "needs help soon", 4,
     re.compile(r"\b(urgent|asap|a\.s\.a\.p|emergency|right away|as soon as|leak(?:ing|s)?|flood(?:ed|ing)?|burst|"
                r"no (?:heat|hot water|power|ac|a/c|air)|not working|stopped working|broke(?:n)?)\b", re.I)),
    ("Wants to book", "wants to book", 3,
     re.compile(r"\b(book(?:ing)?|appointments?|appt|schedule|availab(?:le|ility)|openings?|slots?|reserve|"
                r"consult(?:ation)?|sign(?:ing)? up|join|membership|trial|fit me in|come (?:in|out|by)|"
                r"do you have (?:any(?:thing)?|time|room|space)|open (?:on |this |next )?"
                r"(?:mon|tue|wed|thu|fri|sat|sun)\w*)\b", re.I)),
    ("Asking about price", "asking about price", 3,
     re.compile(r"(\b(price[sd]?|pricing|cost[s]?|how much|rates?|fees?|quote|estimate|deal|special|discount|"
                r"package|promo(?:tion)?)\b|\$\s?\d|\d+\s?% off)", re.I)),
)
_RESCHEDULE = re.compile(r"\b(re-?schedule|move my|change my|cancel)\b", re.I)


def wants(said: str) -> list[tuple[str, str, int]]:
    """What a message asks for, as (card words, sentence words, weight), strongest first. Moving or cancelling a
    booking is an existing customer's errand, said as such, never "wants to book"."""
    said = str(said or "")
    out = []
    for card, phrase, weight, pat in _WANTS:
        if pat.search(said):
            if card == "Wants to book" and _RESCHEDULE.search(said):
                card, phrase, weight = "About their booking", "about their booking", 2
            out.append((card, phrase, weight))
    return sorted(out, key=lambda w: -w[2])


def _fresh(hours: float) -> int:
    """SPEED TO LEAD: a person who wrote an hour ago is the easiest one to win; after a week most have gone elsewhere
    (the Morning Review research, 2026-10-06: a reply within the hour made a lead about 7x likelier to qualify, HBR 2011)."""
    return 3 if hours <= 1 else 2 if hours <= 24 else 1 if hours <= 72 else 0 if hours <= 168 else -2


def _hours(iso: str) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 3600)
    except (TypeError, ValueError):
        return 1e9


# MAIL FROM PROVIDERS ANYONE CAN USE: their domain is never "the box's own", or a gmail.com mailbox would hide every
# customer who writes from gmail.com.
_FREE_MAIL = frozenset({"gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com",
                        "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
                        "protonmail.com", "gmx.com", "zoho.com", "fastmail.com"})
_ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_SENDER_KEY = re.compile(r"from|reply_to|sender|operator|email", re.I)


def own_senders() -> tuple[set, set]:
    """The box's own addresses, and the domains it sends from (OSDev1, 2026-10-05: "Skip the box's own addresses and
    domains"): the mailbox it reads, its people's sign-ins, and every address its settings send or reply as. The
    onboarding notice that headed the owner's own list on 10-05 came from a sending subdomain the mailbox check never
    knew. Never raises."""
    import os
    addrs: set = set()

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and _SENDER_KEY.search(key):
            addrs.update(a.lower() for a in _ADDRESS.findall(node))
    try:
        from .drafter import who_wrote
        addrs |= who_wrote.our_addresses()
    except Exception:                            # noqa: BLE001
        pass
    try:
        with state.connect() as c:
            addrs |= {str(r[0]).strip().lower() for r in c.execute("SELECT email FROM users").fetchall() if r[0]}
    except Exception:                            # noqa: BLE001
        pass
    try:
        from core.config import get_config
        walk(get_config())
    except Exception:                            # noqa: BLE001
        pass
    walk({k: v for k, v in os.environ.items()})
    # A placeholder in a settings file (you@example.com) must never make every example address "ours".
    domains = {d for d in (a.rsplit("@", 1)[1] for a in addrs if "@" in a)
               if d not in _FREE_MAIL and not d.startswith("example.") and d != "example"}
    return addrs, domains


def _is_ours(sender: str, addrs: set, domains: set) -> bool:
    found = _ADDRESS.findall(str(sender or ""))
    if not found:
        return False
    addr = found[-1].lower()
    dom = addr.rsplit("@", 1)[1]
    return addr in addrs or any(dom == d or dom.endswith("." + d) for d in domains)


def real_people(space: str, rows: list[dict]) -> list[dict]:
    """The waiting rows that are a person who wants an answer from this business (OSDev1, 2026-10-05, after 10-05.2's
    three names were the owner's own onboarding notice, a cold sales pitch and a funding pitch). Leaves out:
      * the box itself: its own addresses and the domains it sends from (`own_senders`);
      * a sender never judged (`automated` NULL) that the shared classifier marks a machine on its address alone;
      * what the box already judged needs no person: a cold pitch, or a newest message judged to need no reply.
    Marks the rest `first_time` when nobody has ever answered on the thread. Never raises; a failed check keeps a row,
    because hiding a customer is the expensive mistake."""
    if not rows:
        return []
    addrs, domains = own_senders()
    try:
        from .drafter import store as drafts
        judged = drafts.judged_not_for_a_person(space)
    except Exception:                            # noqa: BLE001
        judged = set()
    try:                                         # A REPLY THE BOX ALREADY WROTE, still waiting to be sent
        ready = {str(d.get("zcid") or "") for d in drafts.waiting(space, limit=1000)
                 if str(d.get("body") or "") != drafts.NO_REPLY_BODY}
    except Exception:                            # noqa: BLE001
        ready = set()
    ids = [str(r.get("zernio_conversation_id") or "") for r in rows]
    facts: dict = {}
    try:
        with state.connect() as c:
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                for f in c.execute(
                        "SELECT k.zernio_conversation_id AS z, "
                        "  (SELECT m.sent_by FROM inbox_messages m WHERE m.space = k.space "
                        "     AND m.zernio_conversation_id = k.zernio_conversation_id AND m.direction = 'in' "
                        "     ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS sender, "
                        "  EXISTS(SELECT 1 FROM inbox_messages m WHERE m.space = k.space "
                        "     AND m.zernio_conversation_id = k.zernio_conversation_id AND m.direction = 'out') AS answered, "
                        "  (SELECT COUNT(*) FROM inbox_messages m WHERE m.space = k.space "
                        "     AND m.zernio_conversation_id = k.zernio_conversation_id AND m.direction = 'in' "
                        "     AND m.created_at > COALESCE((SELECT MAX(o.created_at) FROM inbox_messages o "
                        "         WHERE o.space = k.space AND o.zernio_conversation_id = k.zernio_conversation_id "
                        "           AND o.direction = 'out'), '')) AS unanswered "
                        "  FROM inbox_conversations k WHERE k.space = ? AND k.zernio_conversation_id IN (%s)"
                        % ",".join("?" * len(part)), (space, *part)).fetchall():
                    facts[f["z"]] = dict(f)
    except Exception:                            # noqa: BLE001
        facts = {}
    try:
        from .drafter import who_wrote
    except Exception:                            # noqa: BLE001
        who_wrote = None
    out = []
    for r in rows:
        z = str(r.get("zernio_conversation_id") or "")
        f = facts.get(z, {})
        found = _ADDRESS.findall(str(f.get("sender") or ""))
        sender = found[-1].lower() if found else str(f.get("sender") or "")   # the classifier reads a bare address
        email = str(r.get("platform") or "") == "email"
        if z in judged:
            continue
        if email and _is_ours(sender, addrs, domains):
            continue
        if email and r.get("automated") is None and who_wrote is not None:
            try:
                if who_wrote.level(sender, {}, ours=addrs, strict=False):
                    continue
            except Exception:                    # noqa: BLE001
                pass
        out.append({**r, "first_time": not f.get("answered"), "unanswered": int(f.get("unanswered") or 0),
                    "ready": z in ready})
    return out


def _ranked(r: dict) -> tuple[int, list[tuple[str, str]]]:
    """(how much answering this person first is worth, the reasons, strongest first, as (card, sentence) words)."""
    reasons = [(c, p, w) for c, p, w in wants(r.get("preview"))]
    if r.get("ad_meta_id"):
        reasons.append(("From your ad", "from your ad", 3))
    if r.get("first_time"):
        reasons.append(("New", "first message", 2))
    n = int(r.get("unanswered") or 0)
    if n >= 2:
        times = "twice" if n == 2 else f"{n} times"
        reasons.append((f"Wrote {times}", f"wrote {times}", 1))
    reasons.sort(key=lambda x: -x[2])
    score = sum(w for _, _, w in reasons) + _fresh(_hours(r.get("last_inbound_at")))
    return score, [(c, p) for c, p, _ in reasons]


def answer_first(rows: list[dict], cut: str) -> list[dict]:
    """WHO TO ANSWER FIRST, BY NAME: the three people most worth a reply this morning, from the last 30 days, each with
    why, how long they have waited, what they asked, and a link to the thread.

    THE ORDER IS SPEED TO LEAD (owner, 2026-10-06: "use some intelligence and inference to make that a good section
    ... speed to lead is where the money is at. So put like two or three people on that list."). Each person scores on
    what their own words ask for (`wants`: help soon, a booking, a price), an ad that brought them, writing for the
    first time, writing again while unanswered, and how fresh it is (`_fresh`: an hour-old message is the easiest
    one to win; a week-old one has mostly gone elsewhere). Highest first, newest first on a tie. Until 10-06 the order
    was ad, then first-time writers, then the rest (OSDev1, 10-05), which put a "thanks!" ahead of a booking.

    "72 waiting" is a number; "Dana wants to book, from your ad, 2h ago" is a reply sent before breakfast. Read from
    rows already on this box, and no model reads their words. It goes to the review page and the owner's email, never
    to Slack (core/report.py), where more than the owner can read a customer's words."""
    from urllib.parse import quote
    from .inbox import channels
    keep = [r for r in rows if str(r.get("last_inbound_at") or "") and _iso(r["last_inbound_at"]) >= cut]
    keep.sort(key=lambda r: _iso(r["last_inbound_at"]), reverse=True)
    scored = [(_ranked(r), r) for r in keep]
    scored.sort(key=lambda x: -x[0][0])                  # stable: newest first among equals
    out = []
    for (_, reasons), r in scored[:ANSWER_FIRST]:
        said = " ".join(str(r.get("preview") or "").split())
        card = said if len(said) <= SAID_CARD else said[:SAID_CARD - 1].rsplit(" ", 1)[0] + "…"
        short = said if len(said) <= SAID_CHARS else said[:SAID_CHARS - 1].rsplit(" ", 1)[0] + "…"
        waited = _waited(r["last_inbound_at"])
        bits = [f"Waiting {waited}"] + [p for _, p in reasons]
        platform = str(r.get("platform") or "")
        out.append({"text": str(r.get("participant") or "").strip() or "Someone",
                    "why": " · ".join(bits) + (f": “{short}”" if short else ""),
                    "href": "/inbox/inbox/" + quote(str(r.get("zernio_conversation_id") or ""), safe=""),
                    # THE CARD'S PARTS, for the review page and the owner's email to draw on their own.
                    "said": card, "waited": waited, "reasons": [c for c, _ in reasons][:3],
                    "ready": bool(r.get("ready")), "channel": channels.name(platform, fallback="") if platform else "",
                    "of": len(keep)})
    return out


# THE NUMBERS THAT MATTER (Morning Review V2 step 2, docs/PLAN_MORNING_REVIEW_ADVISOR.md Input B): how fast replies
# went out, where new conversations came from, and when people write. Arithmetic on the box's own rows, no model, each
# a figure the page draws alone and the review's AI is handed as a fact.
PACE_DAYS = 7
BUSY_DAYS = 30
BUSY_MIN = 20                    # fewer messages than this in a month says nothing about a busiest day or hour
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _span(seconds: float) -> str:
    """A wait, as a person says it: "4m", "2h 5m", "1d 3h"."""
    m = int(round(seconds / 60))
    if m < 60:
        return f"{max(m, 1)}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _hour(h: int) -> str:
    return "12am" if h == 0 else f"{h}am" if h < 12 else "12pm" if h == 12 else f"{h - 12}pm"


def pace_figures(space: str, day: date) -> dict:
    """The day's reply speed, the week's, where the week's new conversations came from, and the month's busiest day
    and hour of the week, as report figures. Empty when there is nothing to say. Never raises."""
    from core.report import tz
    figures: dict = {}
    lo, hi = window(day)
    week_lo = window(day - timedelta(days=PACE_DAYS - 1))[0]
    day_s = inbox_store.reply_seconds(space, lo, hi)
    if day_s:
        figures["reply_time_day"] = {"value": _span(_median(day_s)), "label": "typical time to answer that day"}
    week_s = inbox_store.reply_seconds(space, week_lo, hi)
    if len(week_s) >= 3:
        figures["reply_time_week"] = {"value": _span(_median(week_s)), "label": "typical time to answer, last 7 days"}
    new = inbox_store.new_conversations(space, week_lo, hi)
    if new["all"]:
        figures["new_week"] = {"value": new["all"], "label": "new conversations, last 7 days"}
    if new["from_ads"]:
        figures["from_ads_week"] = {"value": new["from_ads"], "label": "of them from your ads"}
    stamps = inbox_store.arrivals(space, window(day - timedelta(days=BUSY_DAYS - 1))[0])
    if len(stamps) >= BUSY_MIN:
        days, hours, zone = {}, {}, tz()
        for s in stamps:
            try:
                t = datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(zone)
            except ValueError:
                continue
            days[t.weekday()] = days.get(t.weekday(), 0) + 1
            hours[t.hour] = hours.get(t.hour, 0) + 1
        if days:
            d = max(days, key=lambda k: (days[k], -k))
            figures["busiest_day"] = {"value": _DAYS[d], "label": "busiest day for messages, last 30 days"}
        if hours:
            h = max(hours, key=lambda k: (hours[k], -k))
            figures["busiest_hour"] = {"value": _hour(h), "label": "busiest hour for messages, last 30 days"}
    return figures


def _waited(iso: str) -> str:
    """How long ago, in the fewest characters a person reads at a glance: 21m, 3h, 2d."""
    from datetime import datetime, timezone
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        s = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}m" if s < 3600 else (f"{s // 3600}h" if s < 172800 else f"{s // 86400}d")


def report(day: date, space: str | None = None) -> dict:
    """The morning page for `day`, for `space` — or for this box's own Space when none is named.

    THE ARGUMENT EXISTS BECAUSE THE SCREEN AND THE MAIL ARE NOT ALWAYS ABOUT THE SAME TENANT,
    which was a live bug found on 2026-09-18 while building Today's greeting. `_space_name()`
    answers "the FIRST Space", and `app._space()` answers "the Space this request is in". On a
    sold box there is one Space and they agree. On the owner's multi-Space box they do not — so
    `/inbox/` was rendering one tenant's inbox figures directly above another tenant's
    conversation list, and the greeting built on those figures would have said "4 unread" about
    people who were not in the list underneath it.
    ONE PRODUCER IS STILL THE RULE. This is the same function, the same predicates and the same
    window whoever asks; only the tenant it is asked about moves. The 8am send calls it with no
    space, exactly as `register_reporter` always has, so nothing about the mail changes.
    THE MAIL'S OWN SCOPE IS STILL AN OPEN QUESTION on a multi-Space box — reporting only the
    first Space is a choice nobody has revisited — and it is a product question, not a bug to
    quietly redefine here.
    """
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

    # ── the inbox ────────────────────────────────────────────────────────────────────────
    # THE PRODUCT'S OWN SEGMENT, AND IT WAS NOT HERE. This page reported uptime, PageSpeed and
    # competitors on a box whose headline is a unified inbox — measured 2026-09-16, and flagged by
    # OSDev0 at 02:30 as one of the live homepage's overstated claims ("morning page: zero inbox
    # data"). A morning review of a messaging product that never mentions the messages is the
    # dashboard equivalent of the empty reply box: it works, and it does not do the thing.
    #
    # IT IS NOT A RAIL, AND THAT IS WHY IT IS UNCONDITIONAL. `rails` are optional things a
    # business may or may not have — a website to watch, competitors to name. The inbox is what
    # the box IS. Gating it behind `owned` would hide it on every box, since no rail is named
    # `inbox` and none should be.
    #
    # SILENT ON A BOX WITH NO CONVERSATIONS. A new box, or one polled before its first message,
    # adds nothing here rather than a row of zeroes — the same rule as a rail nobody owns. Zeroes
    # on the first morning read as a broken machine, which is exactly what the module header says
    # this page exists not to do.
    inbox_space = space or _space_name()
    counts = inbox_store.day_counts(inbox_space, lo, hi)
    # THIRTY DAYS (owner, 2026-10-04: "skip threads older than 30 days"). A thread nobody has written on in a month is
    # not someone waiting on a reply this morning; on a mailbox with years in it, counting those made "waiting" a
    # number nobody could act on and "oldest waiting" read 319 days. The inbox screen still lists every one.
    waiting = inbox_store.awaiting_reply(inbox_space, within_days=RECENT_DAYS)
    # UNREAD IS PRODUCED HERE BECAUSE THE SCREEN IS NOT ALLOWED TO COMPUTE IT. `r_today` adds no
    # figure of its own on purpose — one producer is the only way the 8am message and the screen
    # can never disagree — and the greeting it now opens with needs this number. It is the same
    # count the dot on the Inbox tab uses, read through the same predicate, so the phone's mark
    # and the morning's sentence cannot drift apart either.
    #
    # WRAPPED, BECAUSE A REPORT MUST NOT DIE FOR A GREETING. A box mid-migration can be missing
    # `read_at`; this segment is worth more than the number, so an unreadable count is 0 and the
    # rest of the report still writes.
    try:
        unread = inbox_store.unread_conversations(inbox_space)
    except Exception:                            # noqa: BLE001 — a figure, never the report
        unread = 0
    # UNREAD JOINS THE GUARD. A message that arrived yesterday and has not been opened is the
    # exact case this segment exists for, and on a quiet morning `counts["inbound"]` is 0 — so
    # keying only on today's traffic would hide the one thing he needs to see.
    if counts["inbound"] or waiting or counts["new_people"] or unread:
        if counts["inbound"]:
            happened.append({"text": "messages came in", "value": counts["inbound"]})
        if counts["new_people"]:
            happened.append({"text": "people wrote for the first time", "value": counts["new_people"]})
        if counts["drafts"]:
            happened.append({"text": "replies written for you", "value": counts["drafts"]})
        figures["inbox_waiting"] = {"value": waiting, "label": "waiting on you"}
        try:
            figures.update(pace_figures(inbox_space, day))
        except Exception:                        # noqa: BLE001 — a figure, never the report
            pass
        if unread:
            figures["inbox_unread"] = {"value": unread, "label": "not opened yet"}
        if counts["inbound"]:
            figures["inbox_today"] = {"value": counts["inbound"], "label": "messages today"}
        if waiting:
            # THE SECOND INSTRUCTION ON THIS PAGE, and it belongs beside the first. A customer who
            # wrote and has not been answered is the same shape of problem as a front door that
            # did not open, and it is the one thing on this segment a person can act on in a
            # minute. `awaiting_reply` is about DIRECTION, not a clock, so an old one counts —
            # being ignored for a week is worse than being ignored since breakfast, not resolved.
            noun = "conversation is" if waiting == 1 else "conversations are"
            # `person`: people waiting on him, so the Morning Review says it again whenever it grows.
            needs_you.append({"text": f"{waiting} {noun} waiting on your reply",
                              "href": "/inbox/inbox", "key": "waiting", "value": waiting, "person": True})
            watch.append({"text": f"Inbox — {waiting} waiting on you", "state": rails.WARN})
        elif counts["inbound"]:
            # ANSWERED, SAID PLAINLY. The good state has to be visible or the segment only ever
            # appears when something is wrong, and a page that only nags is a page nobody opens.
            watch.append({"text": "Inbox — everyone has been answered", "state": rails.OK})

    # Every rail he owns gets a line when it is waiting on him or failing. A rail he does not own
    # is absent — no line, no zero, no nag.
    # WHAT THE BOX HAS READY, AND HOW LONG THE OLDEST HAS WAITED (owner, 2026-09-29, his target
    # dashboard). Both are facts the inbox already holds: the drafts the Drafts screen lists, and
    # the conversations the list calls waiting. Each is said only when it is true right now.
    try:
        from marketing.customer_voice.drafter import store as _drafts
        ready = _drafts.waiting_count(inbox_space)
    except Exception:                            # noqa: BLE001 — a figure, never the report
        ready = 0
    if ready:
        figures["inbox_drafts"] = {"value": ready, "label": "drafts ready to send"}
        noun = "reply is" if ready == 1 else "replies are"
        needs_you.append({"text": f"{ready} {noun} written and ready to send",
                          "href": "/inbox/waiting"})
    first: list[dict] = []
    if waiting:
        try:
            rows = inbox_store.list_conversations(inbox_space, limit=500, waiting=True)
            cut = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat()
            stamps = sorted(s for s in (str(r.get("last_inbound_at") or "") for r in rows) if s and _iso(s) >= cut)
            age = _waited(stamps[0]) if stamps else ""
            first = answer_first(real_people(inbox_space, rows), cut)
        except Exception:                        # noqa: BLE001 — a figure, never the report
            age, first = "", []
        if age:
            figures["inbox_oldest"] = {"value": age, "label": "oldest waiting"}
    for rail in rails.ALL:
        st = rails.state_of(rail)
        if st in (rails.CONNECT, rails.FAIL):
            line = _rail_line(rail, st)
            if line and not any(line["text"].split(" — ")[0] == w["text"].split(" — ")[0] for w in watch):
                watch.append(line)

    headline = ps_score if ps_score is not None else (checks - down)
    label = "PageSpeed score" if ps_score is not None else "good checks today"
    if not owned:
        # OWNING NO RAIL IS NOW THE SHIPPED DEFAULT, NOT AN UNFINISHED BOX. Until 2026-09-22 a box
        # shipped with `rails_owned: [uptime, pagespeed]`, so an empty set meant the buyer had
        # actively cleared it and this branch asked him to say what his business has. The owner
        # retired that whole rail — *"the whole speed and site thing is not something we're going
        # to have"* — so every box now lands here, and both halves of what this branch used to do
        # became wrong on the same day:
        #
        #   · THE PROMPT BECAME A NAG ABOUT A FEATURE WE REMOVED. "say which of these this
        #     business has" asks a buyer to opt into watching a website, which is the one thing
        #     this change exists to stop asking. Dropped.
        #
        #   · THE HEADLINE BECAME A ZERO ABOUT NOTHING. `(0, "rails set up")` was a fair summary
        #     while rails were the product; on a quiet box it now renders "0 rails set up" on the
        #     dashboard — a figure counting a thing that is gone. Measured by rendering the report
        #     on a fresh box, not by reading this line.
        #
        # SO A QUIET BOX SAYS NOTHING AT ALL, which is this file's own rule rather than a new one:
        # `core/dash/home.py:_segments` skips a segment whose headline carries no LABEL, and the
        # module header above says zeroes on the first morning read as a broken machine. The inbox
        # still speaks the moment it has anything to say.
        headline, label = (waiting, "waiting on you") if counts["inbound"] or waiting else (None, "")

    return {"title": TITLE,
            "headline": {"value": headline, "label": label, "better": "less", "week": "last"},   # waiting: fewer is better
            "needs_you": needs_you, "happened": happened, "watch": watch,
            "figures": figures, "notes": [], "answer_first": first}


register_reporter(MACHINE, TITLE, report)
