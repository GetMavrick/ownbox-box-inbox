"""The business context: what this business does, who its customers are, and how it plans to grow. One home, in core.

Owner, 2026-10-04: *"These people need to have a valuable machine on day one. Not a dumb box."* and *"The basic
business context should be part of the base machine. Add-on machines should draw from it instead of gathering their
own."* Scope: docs/SCOPE_BUSINESS_CONTEXT_AT_SIGNUP.md (#1957). This module is its C0 (the one home) and C1 (the light
scan); the "Your business" screen (C2), the full scan and quoted profile (C3) and the Welcome review (C4) build on it.

ONE HOME. Every field lives in box settings under `business`, written only through `put()` and read only through
`get()`. No machine keeps its own copy of the website, the name, the industry or the competitors
(tests/test_business_context.py holds it). A machine may keep a value about its own work only.

READ-THROUGH, NEVER COPIED (OSDev1's review of #1957, the #1595 lesson: "rename code, not storage"). Two values
already live in the AEO Machine's settings on boxes that have it: its website (`site_url`) and its competitors.
`get()` reads the context first and falls back to those keys; nothing copies them and nothing deletes them, so a
release that rolls back still reads what it wrote. LEGACY names each one, for C5 to retire.

THE LIGHT SCAN (C1). On the box itself, with no model and no vendor: the domain of the owner's sign-in email, unless
it is a personal mail service, and that site's home page only, read through `net.fetch_public` (public hosts only,
capped). What the page says about itself (its title and description, its business details in structured data, its
own links to social profiles) becomes a SUGGESTION under `suggested`, never a fact: the screen asks *"Is this your
website?"* and only a person's yes (`confirm_suggested`) fills the fields. Nothing about the buyer leaves their box.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from core import box_settings
from core.logging import get_logger

log = get_logger(__name__)

NS = "business"

# THE CHOICES, as the screen offers them. The owner's own four examples lead the industries (med spa, gym, e-commerce,
# consulting); the rest are the kinds of business we sell to. Strike or add here, and the screen follows.
INDUSTRIES = ("med spa", "gym and fitness", "e-commerce", "consulting", "dental", "salon and beauty",
              "home services", "legal", "restaurant", "retail", "agency", "other")
GOALS = ("more new customers", "more bookings", "answer people faster", "better reviews",
         "more repeat customers", "be found on Google and in AI answers", "save time on admin",
         "launch something new")
GOALS_MAX = 3
WORKFLOWS = ("customer messages", "booking and scheduling", "follow-up with customers", "reviews", "social posts",
             "website articles", "finding new leads", "email newsletters", "reporting", "admin and paperwork")
CUSTOMER_KINDS = ("locals", "families", "professionals", "businesses", "tourists", "online buyers", "other")
STAGES = ("just starting", "growing", "established")
TEAM_SIZES = ("just me", "2-5", "6-20", "more than 20")

TEXT = {"name": 120, "area": 300, "hours": 300, "description": 600, "customers": 300}
LISTS_MAX = 50

# WHAT STILL LIVES IN A MACHINE'S OWN SETTINGS, read through until C5 moves the machine onto the context.
LEGACY = {"website": ("seo", "site_url"), "competitors": ("seo", "competitors")}

DEFAULTS: dict = {
    "website": "", "name": "", "industry": "", "area": "", "hours": "", "description": "",
    "socials": [], "sells": [], "customers": "", "customer_kinds": [], "coming": [], "push": [],
    "goals": [], "workflows": [], "stage": "", "team_size": "", "competitors": [],
    "profile": [], "suggested": {},
}
FIELDS = tuple(DEFAULTS)

# PERSONAL MAIL SERVICES: a sign-in at one of these says nothing about the business's website, so it is never scanned.
PERSONAL_MAIL = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com", "outlook.com", "hotmail.com",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "zoho.com", "yandex.com", "mail.com", "hey.com", "fastmail.com", "tutanota.com",
    "comcast.net", "att.net", "verizon.net", "sbcglobal.net", "cox.net", "charter.net", "bellsouth.net",
    "earthlink.net", "qq.com", "163.com", "web.de", "orange.fr", "btinternet.com", "shaw.ca", "rogers.com",
})
SOCIAL_HOSTS = ("instagram.com", "facebook.com", "tiktok.com", "linkedin.com", "youtube.com", "x.com",
                "twitter.com", "pinterest.com", "yelp.com")
SCAN_TRIES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── reading ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _stored(field: str):
    return box_settings.get(NS, field, default=None)


def _legacy(field: str):
    where = LEGACY.get(field)
    if not where:
        return None
    v = box_settings.get(where[0], where[1], default=None)
    if field == "website":
        try:
            return clean_website(v) if v else None
        except ValueError:
            return None
    if field == "competitors":
        if isinstance(v, str):
            v = [x for x in (p.strip() for p in v.splitlines()) if x]
        return [str(x).strip()[:120] for x in (v or []) if str(x).strip()][:LISTS_MAX] or None
    return None


def get() -> dict:
    """Everything the box knows about the business, with the defaults filled. Never raises."""
    out = {}
    for field, default in DEFAULTS.items():
        try:
            v = _stored(field)
            if v in (None, "", [], {}):
                lv = _legacy(field)
                v = lv if lv not in (None, "", []) else v
        except Exception:                                # noqa: BLE001 — a section, never the answer
            v = None
        out[field] = v if v is not None else (list(default) if isinstance(default, list) else
                                              dict(default) if isinstance(default, dict) else default)
    return out


def website() -> str:
    """The business's website as `https://host`, or ""."""
    return str(get().get("website") or "")


# ── writing ───────────────────────────────────────────────────────────────────────────────────────────────────────
def clean_website(value) -> str:
    """`www.Example-MedSpa.com/about` -> `https://www.example-medspa.com`. Raises ValueError with a sentence."""
    v = str(value or "").strip()
    if not v:
        return ""
    if "://" not in v:
        v = "https://" + v
    try:
        parts = urlsplit(v)
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        scheme, host = "", ""
    if scheme not in ("http", "https") or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("That doesn't look like a website address. Try something like example.com.")
    return f"https://{host}"


def _one_of(value, choices: tuple, what: str) -> str:
    v = str(value or "").strip().lower()
    if v and v not in choices:
        raise ValueError(f"Choose one of the {what} on the list.")
    return v


def _some_of(value, choices: tuple, what: str, most: int | None = None) -> list:
    vals = [str(x).strip().lower() for x in (value or []) if str(x).strip()]
    bad = [x for x in vals if x not in choices]
    if bad:
        raise ValueError(f"Choose {what} from the list.")
    vals = list(dict.fromkeys(vals))
    if most and len(vals) > most:
        raise ValueError(f"Pick up to {most} {what}.")
    return vals


def _month(value) -> str:
    v = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
        raise ValueError("Give each plan a month, like 2027-03.")
    return v


def _clean(field: str, value):
    if field == "website":
        return clean_website(value)
    if field in TEXT:
        return " ".join(str(value or "").split())[:TEXT[field]]
    if field == "industry":
        return _one_of(value, INDUSTRIES, "industries")
    if field == "stage":
        return _one_of(value, STAGES, "stages")
    if field == "team_size":
        return _one_of(value, TEAM_SIZES, "team sizes")
    if field == "goals":
        return _some_of(value, GOALS, "goals", GOALS_MAX)
    if field == "workflows":
        return _some_of(value, WORKFLOWS, "jobs to hand over")
    if field == "customer_kinds":
        return _some_of(value, CUSTOMER_KINDS, "kinds of customer")
    if field == "socials":
        out = []
        for s in (value or [])[:LISTS_MAX]:
            u = str(s or "").strip()
            if u.startswith("https://") and _social_host(u):
                out.append(u[:300])
        return list(dict.fromkeys(out))
    if field == "competitors":
        return list(dict.fromkeys(" ".join(str(x).split())[:120] for x in (value or []) if str(x).strip()))[:LISTS_MAX]
    if field == "sells":
        out = []
        for item in (value or [])[:LISTS_MAX]:
            item = item if isinstance(item, dict) else {"name": item}
            name = " ".join(str(item.get("name") or "").split())[:120]
            if name:
                out.append({"name": name, "price": " ".join(str(item.get("price") or "").split())[:40]})
        return out
    if field == "push":
        sold = {s["name"].lower() for s in get()["sells"]}
        vals = [" ".join(str(x).split())[:120] for x in (value or []) if str(x).strip()]
        if any(v.lower() not in sold for v in vals):
            raise ValueError("Pick what to push from what you sell.")
        return list(dict.fromkeys(vals))[:LISTS_MAX]
    if field == "coming":
        out = []
        for item in (value or [])[:LISTS_MAX]:
            what = " ".join(str((item or {}).get("what") or "").split())[:160]
            if what:
                out.append({"what": what, "month": _month((item or {}).get("month"))})
        return out
    if field == "profile":
        out = []
        for item in (value or [])[:200]:
            line = " ".join(str((item or {}).get("line") or "").split())[:400]
            source = str((item or {}).get("source") or "").strip()[:300]
            if line and source:                          # every line is quoted from a page (C3); no source, no line
                out.append({"line": line, "source": source, "field": str((item or {}).get("field") or "")[:40]})
        return out
    if field == "suggested":
        return value if isinstance(value, dict) else {}
    raise ValueError(f"The box keeps no business field called {field!r}.")


def put(field: str, value, *, by: str):
    """Check and store one field. -> the value as stored. Raises ValueError with a sentence a person can act on."""
    if field not in DEFAULTS:
        raise ValueError(f"The box keeps no business field called {field!r}.")
    if not by:
        raise ValueError("Every change says who made it.")
    v = _clean(field, value)
    box_settings.put(NS, field, v, set_by=str(by)[:80])
    log.info("business.put", field=field, by=str(by)[:40])
    return v


def confirm_suggested(*, by: str) -> dict:
    """A person said yes to "Is this your website?": the scan's suggestion fills every field still empty. Never
    overwrites what someone already typed. -> the fields it filled."""
    s = get().get("suggested") or {}
    filled = {}
    cur = get()
    for field in ("website", "name", "area", "hours", "description", "socials"):
        if s.get(field) and not cur.get(field):
            try:
                filled[field] = put(field, s[field], by=by)
            except ValueError:
                continue
    return filled


def reject_suggested(*, by: str) -> None:
    """A person said "No, it isn't": the suggestion goes, and the scan never offers that site again. Clearing the
    suggestion alone would let the next periodic scan find the same page and ask the same question an hour later."""
    s = get().get("suggested") or {}
    dom = bare_host(s.get("website") or "")
    put("suggested", {}, by=by)
    if dom:
        no = list(box_settings.get(NS, "_rejected", default=[]) or [])
        box_settings.put(NS, "_rejected", sorted(set(no) | {dom}), set_by=str(by)[:80])


def bare_host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ── C1: the light scan ────────────────────────────────────────────────────────────────────────────────────────────
def _social_host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    host = host[2:] if host.startswith("m.") else host
    return next((h for h in SOCIAL_HOSTS if host == h), "")


class _Page(HTMLParser):
    """The parts of a home page that describe the business: title, descriptions, JSON-LD, and outbound links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.ld, self.links = "", {}, [], []
        self._in_title = self._in_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title, self._buf = True, []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("description", "og:description", "og:site_name", "og:title") and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self.title, self._in_title = "".join(self._buf).strip(), False
        elif tag == "script" and self._in_ld:
            self.ld.append("".join(self._buf))
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title or self._in_ld:
            self._buf.append(data)


def _ld_business(blobs: list[str]) -> dict:
    """The first schema.org object that describes an organization or local business, from the page's JSON-LD."""
    def walk(x):
        if isinstance(x, list):
            for y in x:
                yield from walk(y)
        elif isinstance(x, dict):
            yield x
            for y in (x.get("@graph") or []):
                yield from walk(y)
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for obj in walk(data):
            t = obj.get("@type")
            types = [t] if isinstance(t, str) else [str(x) for x in (t or [])]
            if any(x in ("Organization", "LocalBusiness", "Corporation") or x.endswith(("Business", "Store",
                   "Clinic", "Spa", "Salon", "Club", "Center", "Restaurant", "Practice", "Service"))
                   for x in types):
                return obj
    return {}


def _address(a) -> str:
    if isinstance(a, str):
        return a.strip()[:300]
    if isinstance(a, dict):
        bits = [a.get("streetAddress"), a.get("addressLocality"), a.get("addressRegion"), a.get("postalCode")]
        return ", ".join(str(b).strip() for b in bits if b and str(b).strip())[:300]
    return ""


def _hours(obj: dict) -> str:
    h = obj.get("openingHours")
    if isinstance(h, list):
        return "; ".join(str(x) for x in h)[:300]
    if isinstance(h, str):
        return h[:300]
    spec = obj.get("openingHoursSpecification")
    spec = spec if isinstance(spec, list) else [spec] if isinstance(spec, dict) else []
    out = []
    for s in spec:
        days = s.get("dayOfWeek")
        days = days if isinstance(days, list) else [days]
        names = ", ".join(str(d).rsplit("/", 1)[-1] for d in days if d)
        if names and s.get("opens") and s.get("closes"):
            out.append(f"{names} {s['opens']}-{s['closes']}")
    return "; ".join(out)[:300]


def _area(obj: dict) -> str:
    a = obj.get("areaServed")
    items = a if isinstance(a, list) else [a] if a else []
    names = [x if isinstance(x, str) else (x or {}).get("name") for x in items]
    return ", ".join(str(n).strip() for n in names if n)[:300]


def read_home_page(raw: str, url: str) -> dict:
    """What a home page says about its business, as a suggestion. Pure: no network. {} when it says nothing."""
    p = _Page()
    try:
        p.feed(raw or "")
    except Exception:                                    # noqa: BLE001 — a broken page says nothing, it never raises
        return {}
    biz = _ld_business(p.ld)
    name = str(biz.get("name") or p.meta.get("og:site_name") or "").strip()
    if not name and p.title:
        name = re.split(r"\s+[|\-–—:]\s+", p.title)[0].strip()
    desc = str(biz.get("description") or p.meta.get("description") or p.meta.get("og:description") or "").strip()
    socials = [str(s) for s in ([biz.get("sameAs")] if isinstance(biz.get("sameAs"), str)
                                else (biz.get("sameAs") or []))]
    socials += [h for h in p.links if h.startswith("https://")]
    socials = [s for s in dict.fromkeys(s.split("?")[0].rstrip("/") for s in socials)
               if _social_host(s) and "/share" not in s and "sharer" not in s][:10]
    found = {
        "website": url, "name": _html.unescape(name)[:TEXT["name"]],
        "description": " ".join(_html.unescape(desc).split())[:TEXT["description"]],
        "area": _area(biz) or _address(biz.get("address")), "hours": _hours(biz), "socials": socials,
    }
    return found if any(found[k] for k in ("name", "description", "area", "hours", "socials")) else {}


def email_domain(email: str) -> str:
    """The business domain of a sign-in address, or "" for a personal mail service or no address."""
    dom = str(email or "").strip().lower().rpartition("@")[2].strip(".")
    return "" if not dom or "." not in dom or dom in PERSONAL_MAIL else dom


def light_scan(email: str | None = None, *, fetch=None) -> dict:
    """C1. The owner's sign-in domain's home page, read for what it says about the business, kept as `suggested`.
    -> {"ok": bool, "why": sentence, "suggested": {...}}. No model, no vendor, nothing leaves the box. Never raises."""
    try:
        if email is None:
            from core import state
            email = (state.owner_user() or {}).get("email") or ""
        dom = email_domain(email)
        if not dom:
            return {"ok": False, "why": "The sign-in address is a personal one, so there is no website to read."}
        if dom in (box_settings.get(NS, "_rejected", default=[]) or []):
            return {"ok": False, "why": f"The owner said {dom} isn't their website."}
        if fetch is None:
            from core import net
            fetch = lambda u: net.fetch_public(u, timeout=10, max_bytes=800_000)  # noqa: E731
        for url in (f"https://{dom}", f"https://www.{dom}"):
            raw = fetch(url + "/")
            if raw:
                found = read_home_page(raw, url)
                if found:
                    found["from"], found["at"] = url, _now()
                    put("suggested", found, by="light scan")
                    return {"ok": True, "why": "", "suggested": found}
        return {"ok": False, "why": f"{dom} didn't answer with a page that describes the business."}
    except Exception as e:                               # noqa: BLE001 — a scan that fails says so, never raises
        log.warning("business.light_scan_failed", error=f"{type(e).__name__}: {e}"[:200])
        return {"ok": False, "why": "The box couldn't read the website just now."}


def scan_if_needed() -> dict:
    """The worker's periodic: scan once, when the box knows its owner's address and nothing about the website yet.
    At most SCAN_TRIES tries, ever; a website typed or confirmed stops it for good."""
    ctx = get()
    if ctx.get("website") or (ctx.get("suggested") or {}).get("website"):
        return {"status": "known"}
    tries = int(box_settings.get(NS, "_scan_tries", default=0) or 0)
    if tries >= SCAN_TRIES:
        return {"status": "gave_up"}
    # A TRY COUNTS ONLY WHEN THERE IS A WEBSITE TO TRY. Before the buyer claims the box its owner may have no
    # business address yet; counting those ticks would give up before the buyer ever arrived.
    from core import state
    if not email_domain((state.owner_user() or {}).get("email") or ""):
        return {"status": "no_business_email"}
    box_settings.put(NS, "_scan_tries", tries + 1, set_by="light scan")
    out = light_scan()
    return {"status": "found" if out.get("ok") else "none", "why": out.get("why", "")}


try:
    from core.worker import register_periodic
    register_periodic(scan_if_needed, interval_s=1800, name="business_light_scan")
except Exception as e:  # noqa: BLE001 — importable without a worker (dispatch, tests, scripts)
    log.debug("business.no_worker", error=type(e).__name__)
