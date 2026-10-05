"""Read a business's own website as numbered sentences, with no model (#1957 C3; OSDev1, 2026-10-04).

Moved to core from the AEO Machine's facts draft (#1946, OSDev6) so the business's full scan and the AEO facts draft
read a website the same way, and no machine keeps a reader of its own.

THE READER NEVER WRITES A WORD. It fetches the home page and up to five pages on the same site likeliest to hold facts,
keeps what a person reads on them, and splits that into sentences. Whoever then asks a model asks for sentence NUMBERS,
so every line that comes back is a sentence from the business's own site, word for word, with its page.

Public hosts only, capped (`core.net.fetch_public`). Never raises on a strange page.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from core import net

MAX_PAGES = 6                 # the home page and the five most likely to hold facts
PAGE_BYTES = 1_000_000
MAX_SENTENCES = 220           # what one prompt carries
MIN_WORDS, MAX_WORDS = 5, 45
# Pages that usually hold facts, best first: what it sells, what it costs, who it is, how it works.
LIKELY = ("pricing", "price", "services", "service", "treatments", "menu", "about", "faq", "how-it-works",
          "team", "locations", "location", "contact", "hours", "plans", "products")
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head", "iframe", "nav"}   # a menu is links, not facts
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "td", "th", "tr",
          "dd", "dt", "blockquote", "header", "footer", "main", "summary", "details", "figcaption"}
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$\"“])")

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


def pages_to_read(site: str, home_links: list[str]) -> list[str]:
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


def numbered(site: str, *, fetch=None, limit: int = MAX_SENTENCES) -> dict:
    """{"sentences": [(sentence, page)], "pages": [pages with a sentence], "status": "" | "no_answer" | "no_sentences"}.

    `fetch` is for tests; the box uses net.fetch_public. The caller words the status for its own screen."""
    fetch = fetch or (lambda u: net.fetch_public(u, max_bytes=PAGE_BYTES))
    home = fetch(site)
    if not home:
        return {"sentences": [], "pages": [], "status": "no_answer"}
    text, links = read(home)
    pages, out = [], []
    for url in pages_to_read(site, links):
        page_text = text if url == site else read(fetch(url) or "")[0]
        got = sentences(page_text)
        if got:
            pages.append(url)
        for s in got:
            if len(out) < limit and all(s.lower() != x.lower() for x, _ in out):
                out.append((s, url))
    return {"sentences": out, "pages": pages, "status": "" if out else "no_sentences"}
