"""Instantly, read the one way: campaign analytics through its API v2 (docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md §5, step 3).

READ ONLY, NEVER A SEND (OSDev1's assignment). Every call here is a GET, to the campaign list or an analytics
endpoint. Nothing creates, changes or sends anything; tests/test_the_outreach_source.py fails if a call is anything
else, or goes anywhere but these three paths.

METERED, THOUGH INSTANTLY BILLS A FLAT PLAN (plan §2 rule 6; OSDev1's review of the TAKING). Each request passes
`cost_guard.check_vendor` before it goes and `state.record_vendor_usage` after it is answered, so every request is in
the spend reports (`vendors.instantly: metered: true`, no cap: the owner's ruling is no hard limits that get in the
way). A cap set later is honoured by the same check.

The key is the workspace's own API v2 key with read scopes only, held in box_secrets and never logged. A refusal
(401, 403, 429, the cap, no answer) raises `Refused` with the sentence the Data Sources card shows, never a retry.

UNVERIFIED UNTIL THE DEMO BOX'S FIRST RUN, as marketing/lead_machine/instantly_client.py says of its own call: the
shapes below are from Instantly's published OpenAPI (api.instantly.ai/openapi/api_v2.json, read 2026-10-02). An
answer of another shape raises UNREADABLE, never an empty week: no campaigns and an unread answer are different facts.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from core import cost_guard, net, state
from core.exceptions import BudgetExceeded

BASE = "https://api.instantly.ai"
VENDOR = "instantly"            # the meter's name, and `vendors.instantly` in the config
SCOPE = "campaigns:read"        # the narrowest read scope Instantly names for these endpoints

CAMPAIGNS = "/api/v2/campaigns"
ANALYTICS = "/api/v2/campaigns/analytics"
STEPS = "/api/v2/campaigns/analytics/steps"
PATHS = (CAMPAIGNS, ANALYTICS, STEPS)

BAD_KEY = "Instantly did not accept that key. Copy it again from Instantly: Settings, then Integrations, then API keys."
NO_SCOPE = (f"That key cannot read your campaigns. In Instantly, make a key with the {SCOPE} scope (read only), "
            "then paste it here.")
# A KEY THAT READS THE CAMPAIGNS BUT NOT THEIR NUMBERS (OSDev1): Instantly's docs name no scope for its analytics,
# so the card names the one Instantly's refusal names, and never asks for all:all or any write scope.
NO_NUMBERS = ("That key can read your campaigns but not their numbers. In Instantly, add the {scope} scope to the "
              "key (read only), then paste it here.")
NO_NUMBERS_UNNAMED = ("That key can read your campaigns but not their numbers. In Instantly, give the key read "
                      "access to campaign analytics too (read only, never all), then paste it here.")
_READ_SCOPE = re.compile(r"\b([a-z][a-z_-]*:read)\b")
SLOW_DOWN = "Instantly asked the box to slow down. It tries again in an hour."
CAPPED = "This month's limit for Instantly requests is reached, so the box stopped asking. It starts again next month."
UNREACHABLE = "Instantly did not answer. Try again in a minute."
UNREADABLE = "Instantly's answer could not be read. Try again in a minute."

STATUS = {0: "draft", 1: "active", 2: "paused", 3: "completed", 4: "running follow-ups",
          -99: "account suspended", -1: "sending accounts unhealthy", -2: "paused for bounces"}
_PAGES = 10                     # 10 x 100 campaigns: plenty, and bounded


class Refused(RuntimeError):
    """Instantly said no, or nothing. str(e) is the sentence for the card. `slow` is True for a 429, `key` for a key
    Instantly refused, so the pull can back off or wait for a new key instead of asking again every hour."""

    def __init__(self, said: str, *, slow: bool = False, key: bool = False):
        super().__init__(said)
        self.slow, self.key = slow, key


@dataclass(frozen=True)
class Conn:
    key: str                    # the API v2 key; in memory for the call, never logged
    base: str = BASE


def get(conn: Conn, path: str, params: dict | None = None):
    """One GET -> its JSON. Metered. Raises Refused."""
    if path not in PATHS:
        raise ValueError(f"not a read this adapter makes: {path}")
    try:
        cost_guard.check_vendor(VENDOR)
    except BudgetExceeded:
        raise Refused(CAPPED) from None
    q = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    status, body = net.get_public(f"{conn.base.rstrip('/')}{path}" + (f"?{q}" if q else ""),
                                  headers={"Authorization": f"Bearer {conn.key}", "Accept": "application/json"},
                                  timeout=30)
    if status:                  # it reached Instantly, so it counts, whatever it said
        state.record_vendor_usage(VENDOR, 1, idem_key=f"{VENDOR}:{uuid.uuid4().hex}", note=path)
    if status == 401:
        raise Refused(BAD_KEY, key=True)
    if status == 403:
        raise Refused(_no_scope(path, body), key=True)
    if status == 429:
        raise Refused(SLOW_DOWN, slow=True)
    if status == 0:
        raise Refused(UNREACHABLE)
    if status != 200:
        raise Refused(f"Instantly answered with an error ({status}). Try again in a minute.")
    try:
        return json.loads(body or "null")
    except ValueError:
        raise Refused(UNREADABLE) from None


def _no_scope(path: str, body: str) -> str:
    """The sentence for a 403: which read scope the key is missing, as Instantly's refusal names it."""
    if path == CAMPAIGNS:
        return NO_SCOPE
    named = [s for s in dict.fromkeys(_READ_SCOPE.findall(str(body or "").lower())) if not s.startswith("all:")]
    return NO_NUMBERS.format(scope=named[0]) if named else NO_NUMBERS_UNNAMED


def _n(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def campaigns(conn: Conn) -> list[dict]:
    """Every campaign: [{"id", "name", "status"}], paged."""
    out, after = [], None
    for _ in range(_PAGES):
        got = get(conn, CAMPAIGNS, {"limit": 100, "starting_after": after})
        if not isinstance(got, dict) or not isinstance(got.get("items"), list):
            raise Refused(UNREADABLE)
        for c in got["items"]:
            if isinstance(c, dict) and c.get("id"):
                out.append({"id": str(c["id"]), "name": str(c.get("name") or "")[:200],
                            "status": _n(c.get("status"))})
        after = got.get("next_starting_after")
        if not after or not got["items"]:
            break
    return out


def _range(start: datetime, end: datetime) -> dict:
    """Instantly reads a date alone as UTC midnight, so the buyer's day goes as its two instants instead."""
    return {"start_date": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "end_date": end.isoformat(timespec="milliseconds").replace("+00:00", "Z")}


def analytics(conn: Conn, start: datetime, end: datetime) -> dict[str, dict]:
    """Every campaign's totals for one range, in ONE call: {campaign id: {sent, contacted, opened, clicked, replied,
    bounced, unsubscribed, opportunities}}. The opens, clicks and replies are UNIQUE over the range, Instantly's own
    counting, which is why a week is asked for as a week and never summed from its days (OSDev1's review)."""
    got = get(conn, ANALYTICS, {**_range(start, end), "exclude_total_leads_count": "true"})
    if not isinstance(got, list):
        raise Refused(UNREADABLE)
    out = {}
    for r in got:
        if isinstance(r, dict) and r.get("campaign_id"):
            out[str(r["campaign_id"])] = {
                "sent": _n(r.get("emails_sent_count")), "contacted": _n(r.get("contacted_count")),
                "opened": _n(r.get("open_count_unique")), "clicked": _n(r.get("link_click_count_unique")),
                "replied": _n(r.get("reply_count_unique")), "bounced": _n(r.get("bounced_count")),
                "unsubscribed": _n(r.get("unsubscribed_count")), "opportunities": _n(r.get("total_opportunities"))}
    return out


def steps(conn: Conn, campaign_id: str, start: datetime, end: datetime) -> dict[str, dict]:
    """One campaign's steps for one range: {step: {sent, opened, clicked, replied, booked}}, its A/B variants added
    together. "booked" is Instantly's meetings_booked: its leads from that step whose status there is Meeting Booked.
    A step Instantly can't name is "". The step is kept as Instantly sends it; whether it counts from 0 or 1 is
    learned by the pull (outreach_sync.learn_step_base) and applied by the seam, so neither is assumed."""
    got = get(conn, STEPS, {"campaign_id": campaign_id, **_range(start, end), "include_opportunities_count": "true"})
    if not isinstance(got, list):
        raise Refused(UNREADABLE)
    out: dict[str, dict] = {}
    for r in got:
        if not isinstance(r, dict):
            continue
        s = out.setdefault("" if r.get("step") is None else str(r["step"])[:20],
                           {"sent": 0, "opened": 0, "clicked": 0, "replied": 0, "booked": 0})
        s["sent"] += _n(r.get("sent"))
        s["opened"] += _n(r.get("unique_opened"))
        s["clicked"] += _n(r.get("unique_clicks"))
        s["replied"] += _n(r.get("unique_replies"))
        s["booked"] += _n(r.get("meetings_booked"))
    return out
