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
from urllib.parse import urlsplit

from core import site_reader
from core.logging import get_logger
# The reader moved to core for the business's full scan (#1957 C3); these names stay so nothing that reads them moves.
from core.site_reader import MAX_PAGES, PAGE_BYTES, MAX_SENTENCES, read, sentences  # noqa: F401
from core.site_reader import pages_to_read as _pages_to_read  # noqa: F401

log = get_logger(__name__)

MAX_FACTS = 20
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
SYSTEM = ("You pick sentences from a business's own website that state concrete facts about the business: what it "
          "offers, what things cost, who it is for, where and when it is open, how it works, its policies, its "
          "credentials and experience. Skip opinions, slogans, testimonials, calls to action, cookie and legal "
          "notices, and anything about other companies. Answer with JSON only: {\"picks\": [sentence numbers]}, "
          f"at most {MAX_FACTS}, most useful first. Never write a sentence of your own.")


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
    site = str(site_url or "").strip()
    if not site.startswith(("https://", "http://")):
        return {"facts": [], "why": "There is no website address on AEO Settings yet."}
    got = site_reader.numbered(site, fetch=fetch)
    pages, numbered = got["pages"], got["sentences"]          # numbered: (sentence, page)
    if got["status"] == "no_answer":
        return {"facts": [], "why": f"Your website, {site}, didn't answer, so nothing was drafted. Try again later."}
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
