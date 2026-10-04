"""One AEO site per box (#1793 F5 item 1.2, owner 2026-10-02).

THE AEO SITE IS THE WEBSITE SAVED ON AEO SETTINGS, as one bare host: `https://www.ownbox.io/` is `ownbox.io`. The
machine writes for that site, so what it reads about searches must be that site's too. A box has one Search Console
property (core/vendors/google_search_console.py), chosen on Google's own screen, and nothing ever compared the two:
a box writing for ownbox.io whose Search Console was connected to brian-macdonald.com read brian-macdonald.com's
searches as its own, offered "Write an article on your top search" from them, and filed them as its customers'
common questions. Now the two are compared, and when they differ the machine says so in one sentence, with both
ways to fix it, everywhere it would have read the wrong site (aeo.status, aeo.sources, Data Sources, Performance),
and reads nothing from the other site.

MATCHING, the way Google's own properties work: a domain property (`sc-domain:ownbox.io`) covers the domain and
every subdomain; a URL-prefix property (`https://www.ownbox.io/`) covers its host. `www.` is never a different site.

UNKNOWN IS NOT WRONG. With no website saved, or no property chosen, there is nothing to compare, and the screens
already say what to do next; this module says nothing.
"""
from __future__ import annotations

from urllib.parse import urlsplit

DOMAIN = "sc-domain:"


def bare(value) -> str:
    """A website, address or property as one bare host, or "" when it names none.
    `https://www.Ownbox.io/articles` -> `ownbox.io`; `sc-domain:ownbox.io` -> `ownbox.io`."""
    v = str(value or "").strip().lower()
    if v.startswith(DOMAIN):
        v = v[len(DOMAIN):]
    if not v:
        return ""
    try:
        host = (urlsplit(v if "://" in v else "//" + v).hostname or "").rstrip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def site() -> str:
    """The site this machine writes for: AEO Settings' website, else its search-engine host. "" when neither is set."""
    from . import settings
    s = settings.get()
    return bare(s.get("site_url")) or bare(s.get("host"))


def covers(prop, host: str) -> bool:
    """Does this Search Console property report on `host`?"""
    p, h = str(prop or "").strip().lower(), bare(host)
    b = bare(p)
    if not b or not h:
        return False
    if p.startswith(DOMAIN):
        return h == b or h.endswith("." + b)
    return h == b


def mismatch(prop=None) -> str:
    """One sentence when Search Console reports on a different website than this machine writes for, else "".
    `prop` is the chosen property; read from the box when not given. Never raises."""
    try:
        if prop is None:
            from core.vendors import google_search_console as gsc
            prop = (gsc.status() or {}).get("property")
        host, other = site(), bare(prop)
        if not host or not other or covers(prop, host):
            return ""
    except Exception:                                    # noqa: BLE001 — unknown is not wrong
        return ""
    return (f"Search Console is connected to {other}, but this machine writes for {host}. "
            f"Connect the {host} property, or change the website on AEO Settings.")
