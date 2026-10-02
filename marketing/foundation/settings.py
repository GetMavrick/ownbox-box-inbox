"""One place to connect website analytics: Settings → Data Sources → Website analytics (plan §5).

Its own keys first; until a person has filled them in, the AEO Machine's current PostHog values and key are read
in their place, so nothing breaks before, during or after the move (and nothing is copied: a one-shot copy splits
data when an update rolls back). The AEO Machine removes its own fields once it reads the store.
"""
from __future__ import annotations

import re

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
    # "Booking click", not the CRO script's "Book a call": "call" is a word the box keeps for the AI receptionist
    # (core/review_brief._RESERVED), and a conversion's name is printed on the Morning Review. Same condition.
    ("Booking click", "event = '$autocapture' and elements_chain_href like '%calendar.app.google%'"),
    ("Contact form sent", "event = '$autocapture' and properties.$event_type = 'submit' "
                          "and properties.$pathname like '/contact%'"),
)


def _own(key: str, default=None):
    return box_settings.get(NS, key, default=default)


def sites() -> list[str]:
    """The hosts this box watches, as PostHog records them (www. kept: PostHog's $host is exact).

    The sites a person typed on the card, when there are any. Otherwise the ones the box found in this PostHog
    project (`found()`, refreshed by the sync), then the AEO Machine's site: SETUP FIXES ITSELF (owner, 2026-10-01),
    so a box whose PostHog already holds two sites watches both without anyone typing them.
    """
    own = _own("sites") or []
    out = [str(h).strip().lower() for h in own if isinstance(h, str) and _HOST.match(str(h).strip().lower())]
    if out:
        return out
    out = [h for h in found() if _HOST.match(h)]
    aeo = str(box_settings.get(_AEO_NS, "host") or "").strip().lower()
    if _HOST.match(aeo) and aeo not in out:
        out.insert(0, aeo)
    return out


def sites_source() -> str:
    """"yours" when a person typed the list, "found" when the box found it, "" for none."""
    if [h for h in (_own("sites") or []) if isinstance(h, str) and _HOST.match(h.strip().lower())]:
        return "yours"
    return "found" if sites() else ""


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
