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
import os
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

TEXT = {"name": 120, "area": 300, "hours": 300, "customers": 300}
LISTS_MAX = 50
# YOUR BUSINESS, IN YOUR OWN WORDS. Owner, 2026-10-08: "we also want the owners to be able to paste in a big paragraph
# of text which takes precedence over everything else ... one big open field would probably be the best rather than a
# bunch of little entries", and yes to it driving the website's articles too and to the screen saying "Anything here
# may be said to a customer." Kept as `description`, the field it grew out of, so every box keeps what it has. It is
# read FIRST by everything the box writes (core/brain.knowledge_context, the Inbox's drafter), and where anything else
# the box knows says otherwise, it wins.
WORDS_MAX = 5000
SUGGESTED_DESCRIPTION_MAX = 600                          # a home page's own short description, as the light scan reads it
# SMART FROM DAY ONE, NEVER THE OWNER'S WORDS BY MISTAKE: the text the box last wrote into the field itself, from the
# website read. While the field still holds exactly that, it is the box's first draft: the screen says so, the next
# weekly read may rewrite it, and nothing treats it as the owner's. The moment a person changes it, it is theirs.
WORDS_DRAFT = "_words_draft"

# WHAT STILL LIVES IN A MACHINE'S OWN SETTINGS, read through until C5 moves the machine onto the context.
LEGACY = {"website": ("seo", "site_url"), "competitors": ("seo", "competitors")}

DEFAULTS: dict = {
    "website": "", "name": "", "industry": "", "area": "", "hours": "", "description": "",
    "socials": [], "sells": [], "customers": "", "customer_kinds": [], "coming": [], "push": [],
    "goals": [], "workflows": [], "stage": "", "team_size": "", "competitors": [],
    "profile": [], "suggested": {}, "ctas": "",
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


def clean_words(value) -> str:
    """The owner's own words as they pasted them: line breaks kept (a pasted list stays a list), spaces inside a line
    collapsed, at most one blank line in a row. Raises ValueError, in a sentence a person can act on, past WORDS_MAX:
    cutting the end off what someone pasted would lose the part they wrote last, silently."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(" ".join(line.split()) for line in text.split("\n"))).strip()
    if len(text) > WORDS_MAX:
        raise ValueError(f"That is {len(text):,} characters, and your own words hold up to {WORDS_MAX:,}. Shorten it "
                         "and save again.")
    return text


def _clean(field: str, value):
    if field == "website":
        return clean_website(value)
    if field == "description":
        return clean_words(value)
    if field == "ctas":
        return clean_ctas(value)
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
    if filled.get("description"):                        # the site's own words, not the owner's: a first draft
        box_settings.put(NS, WORDS_DRAFT, filled["description"], set_by=str(by)[:80])
    return filled


# ── your business, in your own words ──────────────────────────────────────────────────────────────────────────────
def own_words() -> str:
    """The owner's own words about the business, or "" when there are none: the field is empty, or it still holds the
    first draft the box wrote from the website (which says nothing the website read doesn't already carry, and is not
    theirs to be held to). What every writer on the box reads first. Never raises."""
    try:
        text = str(get().get("description") or "")
        return "" if not text or text == box_settings.get(NS, WORDS_DRAFT, default=None) else text
    except Exception:                                    # noqa: BLE001 — a description never costs a reply
        return ""


def words() -> dict:
    """For a screen or a tool: {"text", "from": "you" | "your website" | "", "max"}. Never raises."""
    try:
        text = str(get().get("description") or "")
    except Exception:                                    # noqa: BLE001
        text = ""
    mine = own_words()
    return {"text": text, "from": "you" if mine else ("your website" if text else ""), "max": WORDS_MAX}


def set_words(text, *, by: str) -> str:
    """A person's own words, made theirs even when they match the box's draft word for word (they chose it). An empty
    text goes back to the first draft from the website read. -> the words as stored. Raises ValueError."""
    v = put("description", text, by=by)
    box_settings.put(NS, WORDS_DRAFT, None, set_by=str(by)[:80])
    if not v:
        draft_words()
    return v


# ── CTAs: how an email reply ends ─────────────────────────────────────────────────────────────────────────────────
# Owner, 2026-10-09, after a long description sent every draft's closing line somewhere different: "Your business should
# just stay there and could just be a brain dump to be used for general business context in other purposes ... it
# should be another field called CTAs ... these will be placed at the end of email drafts to drive traffic and Leeds.
# If you don't want a CTA leave this box empty." And how they are used: "give three examples and then instruct the box
# to slightly [change] them each time according to what it appears would work best in each given situation". So one
# field, one CTA a line, at most three, each kept short enough to close an email. The Inbox's drafter reads them
# (marketing/customer_voice/drafter/draft.py, CTA_CLOSE); nothing else does, and empty is the box exactly as it was.
CTAS_MAX = 3
CTA_CHARS = 400


def clean_ctas(value) -> str:
    """The owner's CTAs, one a line: spaces inside a line collapsed, empty lines dropped. Raises ValueError, in a
    sentence a person can act on, past CTAS_MAX lines or CTA_CHARS in a line: cutting what someone wrote would change
    what every email says, silently."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in (" ".join(raw.split()) for raw in text.split("\n")) if ln]
    if len(lines) > CTAS_MAX:
        raise ValueError(f"That is {len(lines)} CTAs. Write up to {CTAS_MAX}, one per line.")
    for n, ln in enumerate(lines, 1):
        if len(ln) > CTA_CHARS:
            raise ValueError(f"CTA {n} is {len(ln):,} characters. Keep each one under {CTA_CHARS}, so it still reads "
                             "as the end of an email.")
    return "\n".join(lines)


def ctas() -> list[str]:
    """The owner's CTAs, in their order, or [] when there are none. Never raises."""
    try:
        return [ln for ln in str(get().get("ctas") or "").split("\n") if ln.strip()]
    except Exception:                                    # noqa: BLE001 — a CTA never costs a reply
        return []


def set_ctas(text, *, by: str) -> str:
    """Store the owner's CTAs (empty clears them). -> the text as stored. Raises ValueError."""
    return put("ctas", text, by=by)


def _words_from_site(profile: list[dict]) -> str:
    """The first draft: the home page's own short description, then what the site says under each heading, word for
    word. Only quoted words, so it can say nothing the business didn't. "" when the site gave nothing."""
    head = str((get().get("suggested") or {}).get("description") or "").strip()
    parts = [head] if head else []
    for field, heading, _ in PROFILE_FIELDS:
        lines = [x["line"] for x in profile if x.get("field") == field and x.get("line")]
        if lines:
            parts.append(heading + "\n" + "\n".join(f"- {line}" for line in lines))
    out = ""
    for part in parts:                                   # whole sections only, within the field's size
        if len(out) + len(part) + 2 > WORDS_MAX:
            break
        out = (out + "\n\n" + part) if out else part
    return out


def draft_words(profile: list[dict] | None = None) -> bool:
    """SMART FROM DAY ONE: until the owner writes their own, the field holds a first draft from the website read, so a
    new owner starts from something true, never an empty box. Rewritten after each read while it is still the box's;
    never once a person has changed it. -> True when it wrote. Never raises."""
    try:
        cur = str(get().get("description") or "")
        if cur and cur != box_settings.get(NS, WORDS_DRAFT, default=None):
            return False                                 # a person's words: never touched
        text = _words_from_site(get().get("profile") or [] if profile is None else profile)
        if not text or text == cur:
            return False
        box_settings.put(NS, "description", text, set_by="website read")
        box_settings.put(NS, WORDS_DRAFT, text, set_by="website read")
        log.info("business.words_drafted", chars=len(text))
        return True
    except Exception as e:                               # noqa: BLE001 — the draft is a nicety, the read stands
        log.warning("business.words_draft_failed", error=type(e).__name__)
        return False


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
        "description": " ".join(_html.unescape(desc).split())[:SUGGESTED_DESCRIPTION_MAX],
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


# ── C3: the full scan ─────────────────────────────────────────────────────────────────────────────────────────────
# Once a website is known (typed, confirmed, or the AEO Machine's), the box reads it: the home page and up to five
# pages likeliest to hold facts (core/site_reader.py, no model), then ONE reasoning call that answers with sentence
# numbers only. Each profile line is a sentence from the business's own site, word for word, with its page. A field
# the site doesn't state stays empty. The profile is stored here and written to my/knowledge/business-profile.md,
# which every call that is not isolated already carries (core/brain.py, _with_knowledge): the Morning Review's ideas,
# the AEO writer and the Inbox's drafter among them. Plans ("What's coming") are never in it: they are the owner's,
# and the Inbox drafter writes to customers.
PROFILE_FILE = "business-profile.md"
FULL_SCAN_TRIES = 3
PROFILE_PER_FIELD = 6
PROFILE_FIELDS = (                                       # (field, heading, what the model looks for)
    ("sells", "What you sell", "what it sells: products, services, treatments, classes, packages"),
    ("prices", "What it costs", "what something costs"),
    ("customers", "Who it's for", "who its customers are"),
    ("area", "Where", "where it is, or the area it serves"),
    ("hours", "When you're open", "when it is open"),
    ("different", "What makes you different", "what makes it different: experience, credentials, guarantees, "
                                                "policies"),
)
# WHICH INDUSTRY, BY THE SAME METHOD (Morning Review V2 step 1, docs/PLAN_MORNING_REVIEW_ADVISOR.md Input A): one word
# from the fixed list and the number of the sentence that shows it. It only ever becomes a question on the screen.
SCAN_INDUSTRIES = tuple(i for i in INDUSTRIES if i != "other")
SCAN_SYSTEM = (
    "You read numbered sentences from a business's own website and say which sentences state each of these about "
    "the business: " + "; ".join(f"{k} ({what})" for k, _, what in PROFILE_FIELDS) + ". Skip slogans, opinions, "
    "testimonials, calls to action, cookie and legal notices, and anything about other companies. Also say which "
    "one of these the business is: " + ", ".join(SCAN_INDUSTRIES) + ", with the number of the sentence that shows "
    "it. Answer with JSON only: {" + ", ".join(f'"{k}": [sentence numbers]' for k, _, _ in PROFILE_FIELDS)
    + ', "industry": {"pick": "one of the list", "because": sentence number} or null}, at most '
    f"{PROFILE_PER_FIELD} per field, most useful first, an empty list when the site doesn't say, and null for the "
    "industry when no sentence shows it. Never write a sentence of your own.")

# REBUILT ONCE A WEEK (V2 step 1: "rebuilt once a week, never daily"), so a new price or a new service reaches the
# review; a re-read that fails keeps last week's lines.
REBUILD_DAYS = 7
# WHAT THE OWNER SAID IS WRONG ("the box drafts them and you fix what's wrong"): a struck line goes at once and no
# later read brings it back. Kept as the line's words, folded, so a re-read of the same sentence is recognised.
STRUCK = "_struck"
STRUCK_MAX = 500
# THE INDUSTRY THE SITE SHOWS, as a question: {"industry", "line", "source"}, and the picks a person said no to.
INDUSTRY_HINT = "_industry_hint"
INDUSTRY_NO = "_industry_no"


def _picks(raw: str, numbered: list) -> list[dict]:
    """The model's answer -> profile lines. Anything that isn't a valid sentence number is dropped, never used as
    text, so a line can only ever be a sentence from the site."""
    try:
        got = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except (ValueError, AttributeError, TypeError):
        return []
    if not isinstance(got, dict):
        return []
    out, used = [], set()
    for field, _, _ in PROFILE_FIELDS:
        n = 0
        for p in got.get(field) or []:
            try:
                i = int(p)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(numbered) and (field, i) not in used and n < PROFILE_PER_FIELD:
                used.add((field, i))
                n += 1
                out.append({"line": numbered[i - 1][0], "source": numbered[i - 1][1], "field": field})
    return out


def _fold(line: str) -> str:
    return " ".join(str(line or "").lower().split())


def _struck_rows() -> list[dict]:
    """[{"fold", "line", "source", "field"}], oldest first: what was struck, whole, so it can be put back."""
    try:
        rows = box_settings.get(NS, STRUCK, default=[]) or []
    except Exception:                                    # noqa: BLE001
        return []
    return [r if isinstance(r, dict) else {"fold": str(r)} for r in rows]


def struck() -> set:
    """The lines the owner said are wrong, folded. Never raises."""
    return {str(r.get("fold") or "") for r in _struck_rows()} - {""}


def last_struck() -> dict:
    """The line struck most recently, for the screen's "Put it back"; {} when none."""
    rows = _struck_rows()
    return rows[-1] if rows and rows[-1].get("line") else {}


def _write_profile_file(profile: list[dict]) -> None:
    try:
        from core import brain
        site = website()
        brain.write_knowledge(PROFILE_FILE, profile_text(profile, site, _now()[:10]) if profile else
                              f"The owner removed every line the box read from {site}.", made_from="your own website")
    except Exception as e:                               # noqa: BLE001 — the screen is right either way
        log.warning("business.profile_file_failed", error=type(e).__name__)


def strike(line: str, *, by: str) -> bool:
    """The owner says a profile line is wrong: it leaves the profile and the knowledge file now, and no later read of
    the site brings it back. -> whether a line went."""
    f = _fold(line)
    prof = get().get("profile") or []
    gone = [p for p in prof if _fold(p.get("line")) == f]
    keep = [p for p in prof if _fold(p.get("line")) != f]
    if not f or not gone:
        return False
    rows = [r for r in _struck_rows() if r.get("fold") != f] + [{"fold": f, **{k: gone[0].get(k) for k in
                                                                                ("line", "source", "field")}}]
    box_settings.put(NS, STRUCK, rows[-STRUCK_MAX:], set_by=str(by)[:80])
    put("profile", keep, by=by)
    _write_profile_file(keep)
    draft_words(keep)                                    # a line struck as wrong leaves the first draft too
    log.info("business.line_struck", by=str(by)[:40])
    return True


def unstrike(line: str, *, by: str) -> bool:
    """"Put it back": a struck line returns to the profile now, where it was, and later reads keep it. -> whether one
    came back."""
    f = _fold(line)
    rows = _struck_rows()
    back = next((r for r in rows if r.get("fold") == f and r.get("line") and r.get("source")), None)
    if not back:
        return False
    box_settings.put(NS, STRUCK, [r for r in rows if r.get("fold") != f], set_by=str(by)[:80])
    prof = get().get("profile") or []
    if f not in {_fold(p.get("line")) for p in prof}:
        prof = put("profile", prof + [{k: back[k] for k in ("line", "source", "field")}], by=by)
    _write_profile_file(prof)
    draft_words(prof)
    log.info("business.line_put_back", by=str(by)[:40])
    return True


def _industry_pick(raw: str, numbered: list) -> dict:
    """The model's industry -> {"industry", "line", "source"}, or {} when it is not one of the list or its sentence
    number is not a sentence."""
    try:
        got = json.loads(raw[raw.index("{"):raw.rindex("}") + 1]).get("industry")
        pick, i = str(got.get("pick") or "").strip().lower(), int(got.get("because"))
    except (ValueError, AttributeError, TypeError):
        return {}
    if pick not in SCAN_INDUSTRIES or not 1 <= i <= len(numbered):
        return {}
    return {"industry": pick, "line": numbered[i - 1][0], "source": numbered[i - 1][1]}


def industry_hint() -> dict:
    """The industry the site shows, while the owner has none set and has not said no to it; else {}."""
    try:
        h = box_settings.get(NS, INDUSTRY_HINT, default={}) or {}
        no = set(box_settings.get(NS, INDUSTRY_NO, default=[]) or [])
    except Exception:                                    # noqa: BLE001
        return {}
    if not isinstance(h, dict) or not h.get("industry") or get().get("industry") or h["industry"] in no:
        return {}
    return h


def answer_industry(yes: bool, *, by: str) -> str:
    """A person's answer to "Is this a <industry> business?". Yes fills the industry (only if still empty); no means
    that pick is never asked again. -> the industry now stored, or ""."""
    h = industry_hint()
    if not h:
        return get().get("industry") or ""
    box_settings.put(NS, INDUSTRY_HINT, {}, set_by=str(by)[:80])
    if yes:
        return put("industry", h["industry"], by=by)
    no = set(box_settings.get(NS, INDUSTRY_NO, default=[]) or [])
    box_settings.put(NS, INDUSTRY_NO, sorted(no | {h["industry"]}), set_by=str(by)[:80])
    return ""


def profile_text(profile: list[dict], site: str, day: str) -> str:
    """The profile as my/knowledge/business-profile.md: a heading per field, each line with its page."""
    parts = ["# Your business, from your own website", "",
             f"Read from {site} on {day}. Every line below is a sentence from the business's own site, word for "
             "word, with the page it came from."]
    for field, heading, _ in PROFILE_FIELDS:
        lines = [x for x in profile if x.get("field") == field]
        if lines:
            parts += ["", f"## {heading}"] + [f"- {x['line']} ({x['source']})" for x in lines]
    return "\n".join(parts)


def full_scan(site: str | None = None, *, think=None, fetch=None) -> dict:
    """C3. Read the business's website and keep what it says, quoted. -> {"ok", "why", "lines", "wait"?, "final"?}.
    `wait` means nothing was tried (no AI yet): no try is spent. `final` means the site was read and says nothing
    the box could quote: trying again won't change that. `think` and `fetch` are for tests. Never raises."""
    try:
        site = str(site or website() or "")
        if not site:
            return {"ok": False, "why": "There is no website yet.", "lines": 0, "wait": True}
        if think is None:
            if os.environ.get("AIOS_HERMETIC_TEST"):
                return {"ok": False, "why": "No AI in a test.", "lines": 0, "wait": True}
            from core import brain
            ready, why = brain.can_think()
            if not ready:
                return {"ok": False, "why": f"The box can't think yet: {why}", "lines": 0, "wait": True}
            think = brain.think
        from core import site_reader
        got = site_reader.numbered(site, fetch=fetch)
        numbered = got["sentences"]
        if got["status"] == "no_answer":
            return {"ok": False, "why": f"Your website, {site}, didn't answer. The box tries again later.", "lines": 0}
        if not numbered:
            return {"ok": False, "final": True, "lines": 0,
                    "why": "Your website has no sentences the box could read. It may be built only with images or "
                           "scripts."}
        listing = "\n".join(f"{i}. {s}" for i, (s, _) in enumerate(numbered, 1))
        raw = think(task="business.full_scan", system=SCAN_SYSTEM, max_tokens=500, timeout=120, isolated=True,
                    prompt=f"Website: {site}\n\nSentences, numbered:\n{listing}\n\nWhich numbers state each?",
                    job_id=f"business-scan:{bare_host(site)}")
        gone = struck()
        profile = put("profile", [p for p in _picks(raw, numbered) if _fold(p["line"]) not in gone], by="full scan")
        hint = _industry_pick(raw, numbered)
        if hint and not get().get("industry"):
            box_settings.put(NS, INDUSTRY_HINT, hint, set_by="full scan")
        from core import brain
        if not profile:                                  # nothing old stays behind as if the site still said it
            brain.write_knowledge(PROFILE_FILE, f"The box read {site} on {_now()[:10]} and found nothing it could "
                                                "quote about the business.", made_from="your own website")
            return {"ok": False, "final": True, "lines": 0,
                    "why": "The box read your website and found no sentence about the business it could quote."}
        brain.write_knowledge(PROFILE_FILE, profile_text(profile, site, _now()[:10]), made_from="your own website")
        draft_words(profile)
        log.info("business.full_scanned", site=bare_host(site), pages=len(got["pages"]), lines=len(profile))
        return {"ok": True, "why": "", "lines": len(profile)}
    except Exception as e:                               # noqa: BLE001 — a scan that fails says so, never raises
        log.warning("business.full_scan_failed", error=f"{type(e).__name__}: {e}"[:200])
        return {"ok": False, "why": "The box couldn't read the website just now.", "lines": 0}


def full_scan_if_needed() -> dict:
    """The worker's periodic: read a website once it is known, and again only when it changes. At most
    FULL_SCAN_TRIES tries per website; a try counts only when the box could think."""
    site = website()
    if not site:
        return {"status": "no_website"}
    last = box_settings.get(NS, "_full_scan", default={}) or {}
    same = isinstance(last, dict) and last.get("site") == site
    # A NEW WEEK IS A NEW READ (REBUILD_DAYS), with its own tries: a site that changed is read again, and one that
    # could not be read last week is tried again this week.
    week_old = same and _days_old(last.get("at")) >= REBUILD_DAYS
    if same and last.get("status") == "done" and not week_old:
        return {"status": "done"}
    tries = int(last.get("tries") or 0) if same and not week_old else 0
    if tries >= FULL_SCAN_TRIES:
        return {"status": "gave_up"}
    out = full_scan(site)
    if out.get("wait"):
        return {"status": "waiting", "why": out.get("why", "")}
    status = "done" if out.get("ok") or out.get("final") else "none"
    box_settings.put(NS, "_full_scan", {"site": site, "tries": tries + 1, "status": status, "at": _now(),
                                        "lines": out.get("lines", 0), "why": out.get("why", "")}, set_by="full scan")
    return {"status": status, "why": out.get("why", "")}


def _days_old(at) -> float:
    """How many days ago an ISO stamp was; a missing or unreadable stamp is old."""
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except (TypeError, ValueError):
        return float("inf")


def full_scan_state() -> dict:
    """For the screen: {"site", "status", "at", "lines", "why"} of the last full scan, or {}."""
    last = box_settings.get(NS, "_full_scan", default={}) or {}
    return last if isinstance(last, dict) and last.get("site") == website() else {}


try:
    from core.worker import register_periodic
    register_periodic(scan_if_needed, interval_s=1800, name="business_light_scan")
    register_periodic(full_scan_if_needed, interval_s=1800, name="business_full_scan")
except Exception as e:  # noqa: BLE001 — importable without a worker (dispatch, tests, scripts)
    log.debug("business.no_worker", error=type(e).__name__)
