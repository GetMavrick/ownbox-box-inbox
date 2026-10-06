"""One place to connect website analytics: Settings → Data Sources → Website analytics (plan §5).

Its own keys first; until a person has filled them in, the AEO Machine's current PostHog values and key are read
in their place, so nothing breaks before, during or after the move (and nothing is copied: a one-shot copy splits
data when an update rolls back). The AEO Machine removes its own fields once it reads the store.
"""
from __future__ import annotations

import re
from datetime import date

from core import box_secrets, box_settings

from .posthog import Conn

NS = "website"                       # box_settings namespace; storage name for keeps
SECRET = "POSTHOG_API_KEY_WEB"       # the business's PostHog personal key, this foundation's own home
_AEO_NS, _AEO_SECRET = "seo", "POSTHOG_API_KEY_SEO"   # the AEO Machine's, read until the move is done

_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_EVENT = re.compile(r"^[A-Za-z0-9_$.:-]{1,80}$")
_PROJECT = re.compile(r"^[0-9]{1,12}$")

# THE CONVERSIONS EVERY SITE HAS, in scripts/cro_daily_report.py's words and conditions (plan §3: the same
# queries, so the numbers agree). A site's own events (an intake form, a product click) are added per site on
# the Data Sources card, by event name.
DEFAULT_CONVERSIONS: tuple[tuple[str, str], ...] = (
    ("Checkout click", "event = '$autocapture' and (elements_chain_href like '%buy.stripe.com%' "
                       "or elements_chain_href like '%checkout.stripe.com%')"),
    # "Booking click": the CRO script's "Book a call", same condition.
    ("Booking click", "event = '$autocapture' and elements_chain_href like '%calendar.app.google%'"),
    ("Contact form sent", "event = '$autocapture' and properties.$event_type = 'submit' "
                          "and properties.$pathname like '/contact%'"),
)


def _own(key: str, default=None):
    return box_settings.get(NS, key, default=default)


def business_site() -> str:
    """THE BUSINESS'S OWN WEBSITE, from Your business (core/business_context.py; #1957 C5), as a host, "" for none.
    ONE HOME (owner, 10-04: "Add-on machines should draw from it instead of gathering their own"): the foundation
    keeps no copy of it. Its own list (`sites` below) holds only the other websites it also counts."""
    try:
        from urllib.parse import urlsplit

        from core import business_context
        host = (urlsplit(business_context.website()).hostname or "").lower()
    except Exception:                                    # noqa: BLE001 — no business website is a quiet answer
        return ""
    return host if _HOST.match(host) else ""


def _typed() -> list[str]:
    own = _own("sites") or []
    return [str(h).strip().lower() for h in own if isinstance(h, str) and _HOST.match(str(h).strip().lower())]


def _found_list() -> list[str]:
    """The sites the box found in this PostHog project (`found()`, refreshed by the sync), then the AEO Machine's
    site: SETUP FIXES ITSELF (owner, 2026-10-01), so a box whose PostHog already holds two sites watches both."""
    out = [h for h in found() if _HOST.match(h)]
    aeo = str(box_settings.get(_AEO_NS, "host") or "").strip().lower()
    if _HOST.match(aeo):
        out.insert(0, aeo)
    return out


def sites() -> list[str]:
    """The hosts this box watches, as PostHog records them (www. kept: PostHog's $host is exact).

    The business's website first, from Your business (`business_site`). Then the other websites a person typed on
    the card, when there are any; otherwise the ones the box found (`_found_list`).
    """
    rest, mine = _typed() or _found_list(), business_site()
    return _one_each(([_spelled(mine, rest)] if mine else []) + rest)


def _spelled(mine: str, rest: list[str]) -> str:
    """THE BUSINESS'S WEBSITE IN THE SPELLING THE STORE KNOWS (www. or not). PostHog's $host is exact and the store
    keys each day by it, so the twin wins wherever it is known: the typed or found list, what the box found in
    PostHog, then the hosts with stored days (the newest first). OSDev1's review of #1974: the card's save drops the
    twin from the typed list, so a search of that list alone lost the spelling after the first save, and every day
    kept under it went unread."""
    bare = mine.removeprefix("www.")
    twins = [h for h in rest + found() if h.removeprefix("www.") == bare]
    if twins:
        return twins[0]
    try:
        from . import store
        kept = [h for h in store.stored_sites() if h.removeprefix("www.") == bare]
    except Exception:                                    # noqa: BLE001 — no store: the spelling as given
        kept = []
    if len(kept) > 1:
        kept.sort(key=lambda h: store.newest_day(h) or date.min, reverse=True)
    return kept[0] if kept else mine


def _one_each(hosts: list[str]) -> list[str]:
    """ONE SITE, ONE ENTRY (OSDev1's review of #1826): a host and its www twin are one site (posthog.host_is matches
    both), so a list holding both spellings would give two lines each carrying the whole site's numbers. The first
    spelling is kept, as typed."""
    out: list[str] = []
    for h in hosts:
        bare = h[4:] if h.startswith("www.") else h
        if not any((o[4:] if o.startswith("www.") else o) == bare for o in out):
            out.append(h)
    return out


def sites_source() -> str:
    """Where the other websites come from: "yours" when a person typed them, "found" when the box found them, ""
    for none. The business's own website is Your business's, never a source here."""
    if _typed():
        return "yours"
    return "found" if _found_list() else ""


def others(hosts: list[str]) -> list[str]:
    """`hosts` without the business's own website or its www twin: what the card's list may hold."""
    own = business_site().removeprefix("www.")
    return [h for h in hosts if not own or h.removeprefix("www.") != own]


FOUND_KEY = "sites_found"           # {"hosts": [...], "at": iso}: what the sync last found in PostHog


def found() -> list[str]:
    f = _own(FOUND_KEY)
    hosts = f.get("hosts") if isinstance(f, dict) else None
    return [str(h).strip().lower() for h in (hosts or []) if isinstance(h, str)]


def found_at() -> str:
    f = _own(FOUND_KEY)
    return str(f.get("at") or "") if isinstance(f, dict) else ""


def set_found(hosts: list[str], at: str) -> None:
    """What the box found in PostHog. THE TIME IS KEPT ONLY WHEN SOMETHING WAS FOUND (OSDev1's review of #1815): an
    empty find stamped it, so the box did not look again for a week, right after telling a new buyer to add the
    snippet. With nothing found it looks again at the next sync, and the card's own check looks at once."""
    box_settings.put(NS, FOUND_KEY, {"hosts": list(hosts), "at": at if hosts else ""}, set_by="website")


def posthog() -> Conn | None:
    """The PostHog connection in force, or None when nobody has connected one."""
    host, project = str(_own("posthog_host") or "").strip(), str(_own("posthog_project") or "").strip()
    if host and _PROJECT.match(project) and box_secrets.is_set(SECRET):
        return Conn(host=host, project=project, key=box_secrets.get(SECRET) or "")
    host = str(box_settings.get(_AEO_NS, "posthog_host") or "").strip()
    project = str(box_settings.get(_AEO_NS, "posthog_project") or "").strip()
    if host and _PROJECT.match(project) and box_secrets.is_set(_AEO_SECRET):
        return Conn(host=host, project=project, key=box_secrets.get(_AEO_SECRET) or "")
    return None


def posthog_source() -> str:
    """"website" when the foundation's own values are in use, "aeo" when the AEO Machine's are, "" for none."""
    if _own("posthog_host") and box_secrets.is_set(SECRET):
        return "website"
    return "aeo" if posthog() else ""


def conversions(site: str) -> list[tuple[str, str]]:
    """The defaults, then this site's own events from settings: {site: [{"name": ..., "event": ...}, ...]}."""
    out = list(DEFAULT_CONVERSIONS)
    own = _own("conversions") or {}
    for item in (own.get(site) or []) if isinstance(own, dict) else []:
        if not isinstance(item, dict):
            continue
        name, event = str(item.get("name") or "").strip(), str(item.get("event") or "").strip()
        if name and _EVENT.match(event) and len(name) <= 60:
            out.append((name, "event = '" + event.replace("'", "") + "'"))
    return out


def tz() -> str:
    from core import notify
    return notify.buyer_timezone() or "UTC"


def sync_state() -> dict:
    s = _own("sync_state")
    return s if isinstance(s, dict) else {}


def set_sync_state(**kw) -> None:
    box_settings.put(NS, "sync_state", {**sync_state(), **kw}, set_by="website")


# ── outreach: Instantly, read only (docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md §5 step 3) ───────────────────────────────

OUT_NS = "outreach"                              # box_settings namespace; storage name for keeps
OUT_SECRET = "INSTANTLY_API_KEY_OUTREACH"        # the workspace's Instantly API v2 key, read scopes only


def instantly():
    """The Instantly connection in force, or None. ONE PLACE PER SETTING (OSDev1's A3): the key the owner pasted on the
    Outreach card, and nothing else. The INSTANTLY_API_KEY in a box's .env belongs to the Lead Machine's own client
    (marketing/lead_machine/instantly_client.py) and is never read here."""
    from .instantly import Conn
    key = box_secrets.get(OUT_SECRET) if box_secrets.is_set(OUT_SECRET) else ""
    return Conn(key=key) if key else None


def pull_state() -> dict:
    s = box_settings.get(OUT_NS, "pull_state")
    return s if isinstance(s, dict) else {}


def set_pull_state(**kw) -> None:
    box_settings.put(OUT_NS, "pull_state", {**pull_state(), **kw}, set_by="outreach")
