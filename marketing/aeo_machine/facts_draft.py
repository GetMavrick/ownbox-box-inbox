"""Draft a business's facts from its own website, so nobody has to know what to type (owner, 2026-10-04).

OWNER, 2026-10-04: "Where would a business owner put these facts in? I don't know where those would go… we're not
building for me." He typed 19 facts himself the night two articles went out with none; a med spa owner can't find the
page or know what to write. So the box reads the buyer's own website and drafts the facts and the numbers the writer
may use, and the owner approves them with one tap (OSDev1's 08:27 assignment).

NOTHING IS INVENTED, BY CONSTRUCTION, NOT BY ASKING NICELY. The pages are split into numbered sentences here, with no
model involved, and the model answers only with sentence NUMBERS. Every drafted fact is then one of those sentences,
word for word, with the page it came from. A model that tried to write a fact of its own would have nowhere to put
it: an answer that isn't a list of valid numbers is dropped.

ONE REASONING CALL PER DRAFT, through `brain.think` (CLAUDE.md non-negotiable 2), on the machine's own AI account key.
Reading the pages is deterministic and uses no model: `core.net.fetch_public`, public hosts only, capped.

WHAT IT DOES NOT DRAFT: competitors and the words never to use. A website names neither, and guessing a competitor is
exactly the kind of claim this machine exists not to make. Those stay the owner's (the Settings screen asks).
"""
from __future__ import annotations

import json as _json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from core import net
from core.logging import get_logger

log = get_logger(__name__)

MAX_PAGES = 6                 # the home page and the five most likely to hold facts
PAGE_BYTES = 1_000_000
MAX_SENTENCES = 220           # what one prompt carries
MAX_FACTS = 20
MIN_WORDS, MAX_WORDS = 5, 45
# Pages that usually hold facts, best first: what it sells, what it costs, who it is, how it works.
LIKELY = ("pricing", "price", "services", "service", "treatments", "menu", "about", "faq", "how-it-works",
          "team", "locations", "location", "contact", "hours", "plans", "products")
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head", "iframe", "nav"}   # a menu is links, not facts
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "td", "th", "tr",
          "dd", "dt", "blockquote", "header", "footer", "main", "summary", "details", "figcaption"}
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$\"“])")

SYSTEM = ("You pick sentences from a business's own website that state concrete facts about the business: what it "
          "offers, what things cost, who it is for, where and when it is open, how it works, its policies, its "
          "credentials and experience. Skip opinions, slogans, testimonials, calls to action, cookie and legal "
          "notices, and anything about other companies. Answer with JSON only: {\"picks\": [sentence numbers]}, "
          f"at most {MAX_FACTS}, most useful first. Never write a sentence of your own.")


class _Text(HTMLParser):
    """The words a person reads on the page, one block per line, and the page's links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self._skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def read(html: str) -> tuple[str, list[str]]:
    """(the page's readable text, its links). Never raises on a strange page."""
    p = _Text()
    try:
        p.feed(str(html or ""))
        p.close()
    except Exception:                                         # noqa: BLE001 — half a page is still a page
        pass
    lines = [" ".join(line.split()) for line in "".join(p.parts).split("\n")]
    return "\n".join(line for line in lines if line), p.links


def sentences(text: str) -> list[str]:
    """Each readable sentence, 5 to 45 words, in page order, each once."""
    out, seen = [], set()
    for line in str(text or "").split("\n"):
        for s in _SPLIT.split(line):
            s = s.strip()
            n = len(s.split())
            if MIN_WORDS <= n <= MAX_WORDS and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


def _pages_to_read(site: str, home_links: list[str]) -> list[str]:
    """The home page, then up to five pages on the same site, the likeliest to hold facts first."""
    host = (urlsplit(site).hostname or "").lower().removeprefix("www.")
    found = []
    for href in home_links:
        u = urljoin(site.rstrip("/") + "/", href.split("#")[0])
        parts = urlsplit(u)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").lower().removeprefix("www.") != host:
            continue
        clean = f"{parts.scheme}://{parts.hostname}{parts.path.rstrip('/') or '/'}"
        if clean not in found and clean.rstrip("/") != site.rstrip("/"):
            found.append(clean)

    def rank(u: str) -> tuple:
        path = urlsplit(u).path.lower()
        hit = next((i for i, w in enumerate(LIKELY) if w in path), len(LIKELY))
        return (hit, path.count("/"), path)

    return [site] + sorted(found, key=rank)[:MAX_PAGES - 1]


def numbers_in(facts: list[str]) -> list[str]:
    """The numbers the drafted facts use, as the writer's guard compares them: no $, no thousands commas."""
    out = []
    for f in facts:
        for m in _NUMBER.findall(f):
            n = m.lstrip("$").replace(",", "").rstrip(".")
            if n and n not in out:
                out.append(n)
    return out


def _think(**kw) -> str:
    """The machine's second reasoning call, and its only other one (tests/test_aeo_writer.py names both)."""
    from core import brain
    return brain.think(**kw)


def draft(site_url: str, *, think=None, fetch=None) -> dict:
    """{"facts": [{"text", "page"}], "numbers": [...], "pages": [...]} or {"facts": [], "why": a sentence}.

    `think` and `fetch` are for tests; the box uses brain.think and net.fetch_public."""
    fetch = fetch or (lambda u: net.fetch_public(u, max_bytes=PAGE_BYTES))
    site = str(site_url or "").strip()
    if not site.startswith(("https://", "http://")):
        return {"facts": [], "why": "There is no website address on AEO Settings yet."}
    home = fetch(site)
    if not home:
        return {"facts": [], "why": f"Your website, {site}, didn't answer, so nothing was drafted. Try again later."}
    text, links = read(home)
    pages, numbered = [], []                                   # numbered: (sentence, page)
    for url in _pages_to_read(site, links):
        page_text = text if url == site else read(fetch(url) or "")[0]
        got = sentences(page_text)
        if got:
            pages.append(url)
        for s in got:
            if len(numbered) < MAX_SENTENCES and all(s.lower() != x.lower() for x, _ in numbered):
                numbered.append((s, url))
    if not numbered:
        return {"facts": [], "why": "Your website has no sentences the box could read. It may be built only with "
                                    "images or scripts. Add your facts on AEO Settings instead."}
    listing = "\n".join(f"{i}. {s}" for i, (s, _) in enumerate(numbered, 1))
    raw = (think or _think)(task="aeo.facts_draft", system=SYSTEM, max_tokens=400, machine="seo",   # the AI-account key live boxes hold
                prompt=f"Website: {site}\n\nSentences, numbered:\n{listing}\n\nWhich numbers state facts?",
                job_id=f"aeo-facts:{urlsplit(site).hostname}")
    try:
        picks = _json.loads(raw[raw.index("{"):raw.rindex("}") + 1]).get("picks") or []
    except (ValueError, AttributeError):
        picks = []
    facts, used = [], set()
    for p in picks:
        try:
            i = int(p)
        except (TypeError, ValueError):
            continue                                           # not a sentence number: dropped, never used as text
        if 1 <= i <= len(numbered) and i not in used:
            used.add(i)
            facts.append({"text": numbered[i - 1][0], "page": numbered[i - 1][1]})
        if len(facts) >= MAX_FACTS:
            break
    log.info("aeo.facts_drafted", site=urlsplit(site).hostname, pages=len(pages), sentences=len(numbered),
             facts=len(facts))
    if not facts:
        return {"facts": [], "pages": pages,
                "why": "The box read your website and found no sentence that states a fact it could quote. Add your "
                       "facts on AEO Settings instead."}
    return {"facts": facts, "numbers": numbers_in([f["text"] for f in facts]), "pages": pages}
