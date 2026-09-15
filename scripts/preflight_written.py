#!/usr/bin/env python3
"""Preflight for a WRITTEN row — refuse to arm anything that would publish wrong.

WHY THIS EXISTS. On 2026-07-31 the WRITTEN machine shipped its first real day and every
defect that reached production had the same shape: a rule that lived only in a document,
and a human or an agent who did not happen to check it that minute.

  - A guide armed without knowing line 1 IS the title took a 77-character URL that could
    not be taken back.
  - A markdown table would have published as a paragraph of literal `| |---|` pipes; it was
    caught because someone thought to run the converter first, not because anything made them.
  - A 162-character meta description was caught by an assertion that happened to be written.

Prose cannot enforce anything. This can. Every check below is a defect that actually
occurred or that the converter/API demonstrably produces, and the arm is the point of no
return — the slug in particular is immutable the instant it is live.

USAGE
    python3 scripts/preflight_written.py --row recXXXXXXXX     # live, fetches Airtable
    python3 scripts/preflight_written.py --all                 # every unpublished row
    python3 scripts/preflight_written.py --self-test           # no network

Exit 0 = safe to arm. Exit 1 = do not arm; every failure is printed with the fix.
No network is touched unless a row is actually fetched, so the rule engine is testable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import importlib.util as _ilu

# THE THRESHOLDS, LOADED BY PATH, NOT BY PACKAGE. `core.thresholds` is stdlib-only on purpose,
# but `import core.thresholds` executes `core/__init__.py`, and this script's whole point is
# that it runs standalone with no dependencies — an ad-hoc operator run is exactly when a
# missing PyYAML hurts. Loading the one file directly keeps a single parser without inheriting
# the package's imports.
_TH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "thresholds.py")
_spec = _ilu.spec_from_file_location("_aios_thresholds", _TH_PATH)
thresholds = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(thresholds)

BASE = os.environ.get("AIRTABLE_WRITTEN_BASE", "appREPLACEME")

# The two `Type` options, read from env exactly as core.config does. This script is deliberately
# dependency-free (it runs standalone against the live base), so it cannot import settings — but
# it must not hard-code a value the owner renames either. `Brief` -> `Article` on 2026-08-05 is
# the third such rename this week.
TYPE_GUIDE = os.environ.get("AIRTABLE_TYPE_GUIDE", "Guide")
TYPE_ARTICLE = os.environ.get("AIRTABLE_TYPE_ARTICLE", "Article")
TABLE = os.environ.get("AIRTABLE_WRITTEN_TABLE", "tblREPLACEME")

CATEGORIES = ("AI Content Creation", "AI Employees", "Founder Marketing",
              "Behind the Build", "Signal vs Noise", "Biohacking")

# Per-post ceilings. A chained column is split on `---` and EACH beat is measured; the
# cascade sends them as separate posts, so a single over-long beat fails the whole chain.
# X/Twitter: 25,000 on Premium (the owner subscribed 2026-08-11). The 280 cap is what forced
# one-idea-per-tweet and produced a feed of fragments; the gate must stop enforcing a limit
# the account no longer has. Set `AIRTABLE_X_BEAT_CAP=280` on a clone without Premium.
_X_CAP = int(os.environ.get("AIRTABLE_X_BEAT_CAP", "25000"))
CHAINED = {"Thread": _X_CAP, os.environ.get("AIRTABLE_X_COLUMN", "X / Twitter"): _X_CAP,
           "Threads Post": 500, "Bluesky": 300}
SINGLE = {"LinkedIn": 3000}   # LinkedIn hard cap; the PROMPT targets 900-1600 for reach

# The never-says list, mechanical half only — a regex can catch a banned word, and nothing else
# here. Voice and the value gate still need a reader: brand-brief-default.md carries the
# former, content-thesis.md's pre-delivery checklist the latter.
# Both lists live in `editorial-thresholds.md` now — they are taste, and the owner is the one
# who gets to hold a different opinion about whether "journey" is banned. Read per call, so an
# edit lands on the next sweep instead of the next deploy.
def banned_words() -> tuple[str, ...]:
    return tuple(thresholds.get("banned_words") or ())


def banned_openers() -> tuple[str, ...]:
    return tuple(thresholds.get("banned_openers") or ())


def slugify(title: str) -> str:
    """Byte-identical to sanity_client.slugify — the slug this row WILL get."""
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return s or "guide"


def beats(text: str) -> list[str]:
    out, cur = [], []
    for ln in (text or "").splitlines():
        if ln.strip() == "---":
            out.append("\n".join(cur)); cur = []
        else:
            cur.append(ln)
    out.append("\n".join(cur))
    return [b.strip() for b in out if b.strip()]


_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")


def broken_pipe_table(text: str) -> bool:
    """A pipe table the converter will NOT recognise — pipe rows with no `|---|` separator.

    THE BAN IS GONE, and this replaces it (2026-08-11). Until #450 the converter had no table
    support and flattened pipes into a paragraph of literal `| |---|` on the live page, so
    preflight refused any table at all. #450 gave it real tables, which made this rule wrong in
    the other direction: it blocked exactly the thing that now works, and OSDev4 hit it the same
    afternoon he was asked to start using tables.

    What is still worth catching is the shape the converter legitimately declines. It requires a
    separator line under the header — that requirement is what stops a paragraph beginning with
    a pipe from being eaten as a table — so pipe rows WITHOUT one publish as literal text. That
    is a real defect on a real page, and it is invisible until someone reads the article.
    """
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if not re.match(r"^\s*\|.*\|?\s*$", ln.strip()) or "|" not in ln:
            continue
        if i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            return False                          # a real table: header + separator
        # a pipe row that is not itself a separator and has no separator under it
        if not _TABLE_SEP.match(ln.strip()) and i + 1 < len(lines) \
                and re.match(r"^\s*\|.*\|?\s*$", lines[i + 1].strip()):
            return True
    return False


def unclosed_fence(text: str) -> bool:
    return (text or "").count("```") % 2 != 0


def banned_hits(text: str) -> list[str]:
    low = f" {(text or '').lower()} "
    return sorted({w for w in banned_words() if re.search(rf"\b{re.escape(w)}\b", low)})


class Report:
    def __init__(self, label: str):
        self.label, self.errors, self.warnings = label, [], []
        self.skipped = False

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        state = "SKIP" if self.skipped else ("PASS" if self.ok else "FAIL")
        head = f"{state}  {self.label}"
        lines = [head]
        lines += [f"    ERROR  {e}" for e in self.errors]
        lines += [f"    warn   {w}" for w in self.warnings]
        return "\n".join(lines)


def check_spokes(f: dict, r: Report) -> None:
    for col, cap in CHAINED.items():
        t = (f.get(col) or "").strip()
        if not t:
            continue
        for i, b in enumerate(beats(t), 1):
            if len(b) > cap:
                r.err(f"{col} post {i} is {len(b)} chars, cap {cap} — the network rejects it "
                      f"and the whole chain fails")
        if "http" in t:
            r.err(f"{col} contains a raw URL — the cascade owns the link home; "
                  f"use the {{link}} token or leave it out")
    for col, cap in SINGLE.items():
        t = (f.get(col) or "").strip()
        if t and len(t) > cap:
            r.err(f"{col} is {len(t)} chars, cap {cap}")
        if t and "http" in t:
            r.err(f"{col} contains a raw URL — the cascade owns the link home")
        if t and any(ln.strip() == "---" for ln in t.splitlines()):
            r.warn("LinkedIn contains a `---` line. LinkedIn is NOT chained, so it publishes "
                   "as a literal horizontal rule. Intended?")
    for col in list(CHAINED) + list(SINGLE):
        t = f.get(col) or ""
        if t and (hits := banned_hits(t)):
            r.err(f"{col} uses banned words: {hits}")


# The banner that separates the owner's seed from material staged for the human writer.
# Kept in sync with daily_seed.WRITER_MATERIAL_MARK — asserted equal in test_written_preflight,
# because preflight must stay importable with no repo package installed.
WRITER_MATERIAL_MARK = "==== WRITER MATERIAL"


def _staging_leaked(f: dict, r: Report) -> None:
    """The staging block must never reach the PAGE.

    Two different holes, one banner. `daily_seed._owner_seed` stops the block reaching the
    DRAFTER (it was being handed over labelled "STANDING INSTRUCTION FROM THE OWNER"). This
    stops it reaching the READER — a writer who copies the seed wholesale into Body, or a
    drafter running on a box that has not taken that fix yet. Gate 1 publishes the moment it
    passes, so the first human to see a leaked "==== WRITER MATERIAL ====" banner would be
    someone reading the live article.

    Errors, never warns. A banner in the body is not a judgement call.
    """
    for field in ("Body", "Lead Magnet Content"):
        if WRITER_MATERIAL_MARK in (f.get(field) or ""):
            r.err(f"{field} contains the {WRITER_MATERIAL_MARK} banner — staged notes to the "
                  f"writer are in the text that publishes. Cut everything from the banner down; "
                  f"it belongs in Seed, not on the page.")


# `![alt](url)` on its own line — the only image form the converter turns into a rendered
# figure (sanity_client._IMAGE_RE). Kept in step with it; a mid-paragraph image is literal text
# and is deliberately not matched here either.
_IMG_RE = re.compile(r"^!\[([^\]]*)\]\((\S+?)\)$", re.M)


def _images(f: dict, r: Report) -> None:
    """Every image in the body resolves, and the page does not contradict itself.

    LIVE-FOUND on row 34 (2026-08-10), the pillar of the Beach Chair series, with preflight
    reporting OK: the body carried THREE image lines where the piece has one hero — the same
    CDN url twice under two different alts ("one lead, six machines" and "three bosses"), plus
    a third pointing at a repo path that was never committed because the file went to the CDN
    instead. All three would have shipped: a duplicated hero, a caption contradicting the
    article four paragraphs away, and a broken image.

    None of that is a taste call, which is why it errors:

      - a RELATIVE path only resolves if the file is committed to the site, and this pipeline
        hosts images on the CDN. A relative src is a broken image far more often than not, so
        it must be stated deliberately, not typed by accident.
      - the same url under two DIFFERENT NON-EMPTY alts: `portableText.tsx` renders alt as a
        VISIBLE figcaption, so the page argues with itself in print.

    Repeating an image is NOT flagged, and an EMPTY alt on the repeat is the correct way to
    do it: the hero carries the caption, a later callback shows the same picture without
    printing the caption twice. Row 34 does exactly that — hero up top, the same shot again
    at "That's a Monday." Flagging a legitimate shape is how a gate teaches people to ignore
    it.
    """
    seen: dict[str, set] = {}
    for alt, url in _IMG_RE.findall(f.get("Body") or ""):
        if not url.startswith(("http://", "https://")):
            r.err(f"image {url!r} is a relative path — this pipeline serves images from the "
                  f"CDN, and a relative src only resolves if the file is committed to the "
                  f"site. Use the full https:// url, or commit the file first.")
        if alt.strip():
            seen.setdefault(url, set()).add(alt.strip())
    for url, alts in seen.items():
        if len(alts) > 1:
            r.err(f"image {url!r} carries {len(alts)} different captions "
                  f"({' | '.join(sorted(a[:40] for a in alts))}) — alt renders as a VISIBLE "
                  f"caption, so this publishes a page that contradicts itself. Caption it "
                  f"once, and leave the alt empty where the image repeats.")


def _duplicate_headline(f: dict, r: Report) -> None:
    """A body that opens with its own H1, next to a Title that is already the headline.

    THE TITLE IS THE TITLE AND THE BODY IS THE BODY (owner, 2026-08-10). `publish_row` used to
    reconcile the two by silently lifting line 1 out of the body — which ate the opening
    sentence of six published pieces, because the helper it used cannot tell a heading from a
    hook. Nothing is removed silently any more, so the one case that removal existed for has
    to be refused here instead: a leading `# Heading` beside a Title publishes the headline
    twice.

    Only a MARKDOWN HEADING, only on the first non-empty line, and only when Title is set. A
    first line of ordinary prose is the hook, and is precisely what must survive.
    """
    title = (f.get("Title") or "").strip()
    if not title:
        return                      # no Title → line 1 legitimately becomes the headline
    for ln in ((f.get("Body") or "") or (f.get("Lead Magnet Content") or "")).splitlines():
        if not ln.strip():
            continue
        if re.match(r"^#{1,6}\s+\S", ln.strip()):
            r.err(f"the body opens with a heading ({ln.strip()[:60]!r}) and Title is already "
                  f"{title!r} — the page would print the headline twice. The Title field is "
                  f"the headline; delete the heading line from the body.")
        return                      # only the first non-empty line matters


def check_brief(f: dict, r: Report, others: list[dict] | None = None) -> None:
    _staging_leaked(f, r)
    _images(f, r)
    _duplicate_headline(f, r)
    # ONE READ PER CHECK, so a sweep sees one consistent set of numbers even if the owner saves
    # the file mid-run. `None` for a key means the file dropped it — that check is SKIPPED and
    # says so, rather than inventing a bound nobody agreed to.
    th = thresholds.load()

    title = (f.get("Title") or "").strip()
    meta = (f.get("Meta Description") or "").strip()
    slug = (f.get("Slug") or "").strip()
    body = f.get("Body") or ""
    kw = (f.get("SEO Keyword") or "").strip()
    cat = (f.get("Category") or "").strip()

    if not title:
        r.err("Title is empty")
    elif th.get("title_max_chars") is None:
        r.warn("title length unchecked — no " + thresholds.where("title_max_chars"))

    elif len(title) > th["title_max_chars"]:
        r.err(f"Title is {len(title)} chars, over "
              + thresholds.where("title_max_chars", th["title_max_chars"]))
    if not meta:
        r.err("Meta Description is empty — this IS the search-result text")
    elif th.get("meta_min_chars") is None or th.get("meta_max_chars") is None:
        r.warn("meta length unchecked — no " + thresholds.where("meta_min_chars/meta_max_chars"))
    elif not (th["meta_min_chars"] <= len(meta) <= th["meta_max_chars"]):
        r.err(f"Meta Description is {len(meta)} chars, must be 150-160")
    if cat not in CATEGORIES:
        r.err(f"Category {cat!r} is not one of the six: {', '.join(CATEGORIES)}")
    if not kw:
        r.err("SEO Keyword is empty — nothing was ledger-checked")

    if not slug:
        r.err("Slug is empty")
    else:
        if slug != slugify(slug):
            r.err(f"Slug {slug!r} is not URL-clean; it would become {slugify(slug)!r}")
        words = [w for w in slug.split("-") if w]
        if th.get("slug_min_words") is None or th.get("slug_max_words") is None:
            r.warn("slug length unchecked — no " + thresholds.where("slug_min_words/slug_max_words"))
        elif not (th["slug_min_words"] <= len(words) <= th["slug_max_words"]):
            r.err(f"Slug has {len(words)} words, outside "
                  + thresholds.where("slug_min_words/slug_max_words",
                                     f'{th["slug_min_words"]}-{th["slug_max_words"]}')
                  + " — and it is IMMUTABLE once live")
        if re.search(r"\b(19|20)\d{2}\b", slug):
            r.err(f"Slug {slug!r} contains a year — dated slugs rank for nothing")

    # LENGTH: 500-2000 words, and THE MODEL PICKS WITHIN IT (owner, 2026-08-06). "Some of them
    # could be short like 500 words. But I don't wanna put any limit on it because some of my
    # guides should be 2000 words. So can we ask the Model to decide what would be most
    # appropriate for the topic?"
    #
    # The old gate was 500-800, and it was the binding constraint on the whole machine — not a
    # style note but a hard `err`, so a good 1,400-word piece was HELD and never reached the
    # site. Everything the machine produced was short because nothing longer could get out.
    #
    # This band is deliberately WIDE and the prompt, not the gate, decides where a given piece
    # lands. A gate is the wrong instrument for an editorial judgement: it can only say no, and
    # it says no identically to a tight argument that finished at 600 words and to a padded one
    # that stopped at 499. So the gate keeps only the two bounds that are real — nothing under
    # 500 is a piece, nothing over 2000 was planned — and the prompt carries the judgement.
    #
    # THE NUMBER LIVES IN ONE PLACE NOW (editorial-thresholds.md). This comment used to warn
    # that changing it here was "necessary but NOT sufficient" because three other statements of
    # it had to be edited to agree — which is precisely the defect that let a piece come back at
    # 800 words while every gate said 2000. The gate and the prompt both read the file; neither
    # restates a bound.
    #
    # `max_tokens` on draft()/repair() is the one number that is NOT taste and stays in code:
    # 2000 words does not fit in 4000 tokens, so a ceiling below the bound truncates a body
    # mid-sentence instead of rejecting it. It is a machine limit, not an opinion.
    words = len(body.split())
    if not body.strip():
        r.err("Body is empty")
    elif th.get("body_min_words") is None or th.get("body_max_words") is None:
        r.warn("body length unchecked — no " + thresholds.where("body_min_words/body_max_words"))
    elif not (th["body_min_words"] <= words <= th["body_max_words"]):
        r.err(f"Body is {words} words, outside "
              + thresholds.where("body_min_words/body_max_words",
                                 f'{th["body_min_words"]}-{th["body_max_words"]}'))
    h2 = len(re.findall(r"^##\s+\S", body, re.M))
    if th.get("h2_min") is None or th.get("h2_max") is None:
        r.warn("H2 count unchecked — no " + thresholds.where("h2_min/h2_max"))
    elif h2 < th["h2_min"]:
        r.err(f"Body has {h2} H2 headings, under "
              + thresholds.where("h2_min", th["h2_min"])
              + " — the ON THIS PAGE rail is built from them and renders empty without them")
    elif h2 > 8:
        # Scaled with the word count. At 3-5 this warned on every long piece, and a warning
        # that fires on every piece is one nobody reads.
        r.warn(f"Body has {h2} H2 headings; 3-8 is the house shape")
    openers = banned_openers()
    if body.strip() and openers and body.lstrip().lower().startswith(openers):
        r.err("Body opens with a banned opener")
    if hits := banned_hits(body):
        r.err(f"Body uses banned words: {hits}")

    check_markdown(body, "Body", r)
    check_spokes(f, r)
    check_collisions(slug, kw, others, r)


def check_guide(f: dict, r: Report) -> None:
    _staging_leaked(f, r)          # the three Beach Chair guides publish through this lane
    _duplicate_headline(f, r)      # one process: the Title field is the headline here too
    # THE TEXT MOVED AND THIS DID NOT FOLLOW IT, WHICH KILLED THE WHOLE GUIDE PREFLIGHT.
    #
    # `Lead Magnet Content` is a RETIRED column — `schema_guard` says so in as many words, and
    # `brief_intake` records copying it into `Body` on the guide rows that still held text. So
    # this read empty on every guide, errored, and RETURNED — taking the Keyword check, the H1
    # shape rules and the slug rules down with it. Measured 2026-08-14: all seven guides on the
    # board carry 6,524-13,531 chars in `Body` and zero in `Lead Magnet Content`, including the
    # two that are already Posted and live. A field that is empty on rows which published
    # successfully is not a blocker; it is a column nobody writes any more.
    #
    # The owner said it plainly — "we don't use that field" — and he was right while I was
    # quoting its own error back at him as evidence a row was broken.
    #
    # `Body` first, the retired column only as a fallback for a legacy row that still holds its
    # text there, so nothing that used to pass starts failing.
    content = (f.get("Body") or "").strip() or (f.get("Lead Magnet Content") or "").strip()
    if not content:
        r.err("the guide has no text — `Body` is empty")
        return
    if not (f.get("Keyword") or "").strip():
        r.err("Keyword is empty — the guide lane's trigger filter requires it")

    # LINE 1 IS ONLY THE TITLE WHEN NOTHING ELSE IS (changed 2026-08-05, one publish lane).
    #
    # These rules were written when the guide lane genuinely ignored `Title`: it lifted line 1
    # out of the body, made it the H1, and built the slug from it. Both halves of that are now
    # false. `publish_row` takes `Title` when set and falls back to the leading heading only
    # when it is blank, and it takes `Slug` — or, for anything already live, the slug on the
    # published document, which no column can override.
    #
    # A published guide therefore CANNOT change its slug, and reporting an unfixable slug as an
    # ERROR made row 7 permanently red for a URL it already has and physically cannot move.
    # A gate that is permanently red on a healthy row is a gate people stop reading, which is
    # the same pathology that let two contentless days go unnoticed this week.
    published = bool((f.get("Lead Magnet URL") or "").strip())
    title = (f.get("Title") or "").strip()

    line1 = ""
    for ln in content.splitlines():
        if ln.strip():
            line1 = re.sub(r"^#{1,6}\s*", "", ln.strip())[:80]
            break

    headline = title or line1
    if title:
        r.warn(f"H1 comes from the Title field: {title!r} (line 1 is body copy)")
    else:
        r.warn(f"no Title set, so line 1 becomes the H1: {line1!r}")

    # Headline shape applies to whichever field actually becomes the H1 — always fixable,
    # because a republish rewrites the title in place while keeping the URL.
    if len(headline) > 70:
        r.err(f"the H1 is {len(headline)} chars; it is a headline, not an opening sentence")
    if headline.endswith((".", "!", "?")) and len(headline.split()) > 4:
        r.err(f"the H1 ends like a sentence, not a title: {headline!r}")

    # Slug: an error only while it can still be changed.
    slug = (f.get("Slug") or "").strip() or slugify(headline)
    n = len([w for w in slug.split("-") if w])
    # Always name the URL it will take — the one thing about a guide that cannot be undone.
    r.warn(f"/guides/{slug}" + (" — already live; the slug is fixed and cannot be changed"
                                if published else ""))
    if n > 6 and not published:
        r.err(f"the slug is {n} words ({slug!r}) and CANNOT be changed after publish. "
              f"Set a short `Slug`, or give it a tighter title.")

    check_markdown(content, "Lead Magnet Content", r)


def check_markdown(text: str, field: str, r: Report) -> None:
    if broken_pipe_table(text):
        r.err(f"{field} has pipe rows with no `|---|` separator line under the header. The "
              f"converter needs that line to recognise a table — without it the rows publish "
              f"as literal pipes. Add it, or drop the pipes.")
    if unclosed_fence(text):
        r.err(f"{field} has an unclosed ``` fence")
    if re.search(r"^\s{2,}[-*]\s", text, re.M):
        r.err(f"{field} has a NESTED list. Lists are level-1 only; nesting is dropped.")
    if re.search(r"^####", text, re.M):
        r.warn(f"{field} uses h4+; only two heading levels exist (## -> H2, ###+ -> H3)")


def check_collisions(slug: str, kw: str, others: list[dict] | None, r: Report) -> None:
    if not others:
        return
    for o in others:
        of = o.get("fields", {})
        if slug and (of.get("Slug") or "").strip() == slug:
            r.err(f"Slug {slug!r} collides with row {o.get('id')}")
        if kw and (of.get("SEO Keyword") or "").strip().lower() == kw.lower():
            r.err(f"SEO Keyword {kw!r} already used by row {o.get('id')} — two of our own "
                  f"briefs chasing one query is worse than either ranking alone")


# ── the truth gate ───────────────────────────────────────────────────────────────────
# RETIRED FACTUAL CLAIMS. Not taste, so deliberately NOT in `editorial-thresholds.md` with the
# banned words — those are opinions the owner is entitled to change his mind about, while these
# are two numbers that are simply no longer true. A retired claim that reaches the page is a
# correctness bug wearing an editorial hat.
#
# WHY THIS IS CODE AND NOT A PROMPT LINE (standing gotcha, wall §6: "a check specified in a DOC
# is not a check"). Both facts were already written down — the ruling dates below are when the
# owner made them — and seeds kept repeating the retired versions anyway, because nothing
# measured it. The drafter prompt now states both, which lowers the odds; this is what makes
# the odds irrelevant.
_RETIRED_CLAIMS = (
    # Owner ruling 2026-08-15. The number is 50 sign-ups in the first 30 days.
    (re.compile(r"\b(?:100|a\s+hundred|one\s+hundred)\s+(?:new\s+)?"
                r"(?:sign[-\s]?ups?|signups?|subscribers?|users?|customers?)\b", re.I),
     "the retired '100 sign-ups' claim — the number is 50 in the first 30 days "
     "(owner ruling 2026-08-15)"),
    # Owner ruling 2026-08-23. Mavrick is live in under 60 seconds; "three minutes" was invented.
    (re.compile(r"\b(?:three|3)\s*[-\s]?\s*min(?:ute)?s?\b", re.I),
     "the invented 'three minutes' setup figure — Mavrick is live in UNDER 60 SECONDS, "
     "answering as if he already works for your company (owner ruling 2026-08-23)"),
)

# Every field a reader can end up seeing. The register applies to "Seed, Body, and captions
# alike", so the gate reads the same surface — a retired number is no less wrong in a LinkedIn
# post than in the article it points at.
_CLAIM_FIELDS = ("Seed", "Angle", "Title", "Meta Description", "Body",
                 "Lead Magnet Content", "LinkedIn", "Threads Post", "Bluesky",
                 os.environ.get("AIRTABLE_X_COLUMN", "X / Twitter"), "Thread", "Caption")


def check_retired_claims(f: dict, r: Report) -> None:
    """FAIL a row that restates a fact the owner has retired."""
    for field in _CLAIM_FIELDS:
        text = f.get(field)
        if not isinstance(text, str) or not text.strip():
            continue
        for rx, why in _RETIRED_CLAIMS:
            m = rx.search(text)
            if m:
                r.err(f"{field}: {m.group(0)!r} is {why}")


def check_row(rec: dict, others: list[dict] | None = None) -> Report:
    f = rec.get("fields", {}) or {}
    label = f"{rec.get('id','(local)')}  {f.get('Type','?')}  {(f.get('Title') or '—')[:44]}"
    r = Report(label)
    t = (f.get("Type") or "").strip()
    # An UNTOUCHED row is not-started, not broken. Brian adds blank rows as placeholders, and
    # failing them would train everyone to ignore this output — which is the whole way a
    # check like this dies. Only judge rows somebody has actually written into.
    if not t and not any((f.get(k) or "").strip()
                         for k in ("Seed", "Body", "Lead Magnet Content", "Title")):
        r.skipped = True
        return r
    # Runs for EVERY type, before the type-specific rules: a retired fact is wrong on a guide,
    # an article, and anything added later, and it should not depend on Type being set right.
    check_retired_claims(f, r)
    if t == TYPE_GUIDE:
        check_guide(f, r)
    elif t == TYPE_ARTICLE:
        check_brief(f, r, others)
    else:
        r.err(f"Type {t!r} is neither {TYPE_ARTICLE} nor {TYPE_GUIDE} — preflight does not know "
              f"its rules. Set Type before filling anything else.")
    return r


# ── live mode ────────────────────────────────────────────────────────────────────────
def fetch(row_id: str | None) -> tuple[list[dict], list[dict]]:
    key = os.environ.get("AIRTABLE_API_KEY")
    if not key:
        sys.exit("AIRTABLE_API_KEY is not set")
    req = urllib.request.Request(
        f"https://api.airtable.com/v0/{BASE}/{TABLE}?pageSize=100",
        headers={"Authorization": f"Bearer {key}"})
    recs = json.load(urllib.request.urlopen(req))["records"]
    if row_id:
        target = [x for x in recs if x["id"] == row_id]
        if not target:
            sys.exit(f"row {row_id} not found in {TABLE}")
    else:
        target = [x for x in recs
                  if (x["fields"].get("Status") or "") not in ("Posted", "Archived")]
    return target, recs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--row", help="one Airtable record id")
    g.add_argument("--all", action="store_true", help="every row not Posted/Archived")
    g.add_argument("--self-test", action="store_true", help="rule engine only, no network")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    target, everything = fetch(a.row)
    if not target:
        print("nothing to check — no unpublished rows")
        return 0
    failed = skipped = 0
    for rec in target:
        others = [x for x in everything if x["id"] != rec["id"]]
        rep = check_row(rec, others)
        print(rep.render())
        if rep.skipped:
            skipped += 1
        elif not rep.ok:
            failed += 1
    judged = len(target) - skipped
    print()
    tail = f" ({skipped} untouched, skipped)" if skipped else ""
    print(f"{judged - failed}/{judged} rows safe to arm{tail}" if not failed
          else f"{failed}/{judged} rows MUST NOT be armed{tail}")
    return 1 if failed else 0


# ── self-test: every rule must catch the defect it exists for ────────────────────────
def self_test() -> int:
    fails = []

    def ok(label, cond):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        if not cond:
            fails.append(label)

    # ~1385 words — inside the 500-2000 gate and not near either edge, so an unrelated
    # rule change never fails here for a length reason nobody was testing.
    good_body = ("Opening line that reframes the thing.\n\n"
                 + "\n\n".join(f"## Question number {i}?\n\n" + "word " * 460
                               for i in range(1, 4)))
    base = {"Type": TYPE_ARTICLE, "Title": "A Good Title Under Sixty Chars",
            "Meta Description": "x" * 155, "Slug": "a-good-clean-slug",
            "SEO Keyword": "a good keyword", "Category": "AI Employees",
            "Body": good_body, "Thread": "one post", "LinkedIn": "a normal post"}

    def rep(**over):
        f = dict(base); f.update(over)
        return check_row({"id": "recTEST", "fields": f})

    ok("a clean brief passes", rep().ok)
    ok("title over 60 fails", not rep(Title="x" * 61).ok)
    ok("meta 162 fails (the real 2026-07-31 near-miss)", not rep(**{"Meta Description": "x" * 162}).ok)
    ok("meta 149 fails", not rep(**{"Meta Description": "x" * 149}).ok)
    ok("meta 150 passes", rep(**{"Meta Description": "x" * 150}).ok)
    ok("2-word slug fails", not rep(Slug="too-short").ok)
    ok("7-word slug fails", not rep(Slug="a-b-c-d-e-f-g").ok)
    ok("dated slug fails", not rep(Slug="the-2026-recap-post").ok)
    ok("unclean slug fails", not rep(Slug="Not Clean Slug").ok)
    ok("bad category fails", not rep(Category="Made Up").ok)
    ok("short body fails", not rep(Body="## H\n\n" + "word " * 100).ok)
    # 700 + 700 clears the word gate, so this can only fail on the heading count — the thing
    # it claims to test. At 300 + 300 it was failing on length and would have gone on
    # "passing" even if the H2 rule were deleted.
    ok("body with 2 H2s fails", not rep(Body="## One?\n\n" + "word " * 700 + "\n\n## Two?\n\n" + "word " * 700).ok)
    ok("a well-formed MARKDOWN TABLE now passes (#450 gave the converter real tables)",
       rep(Body=good_body + "\n\n| a | b |\n|---|---|\n| 1 | 2 |\n").ok)
    ok("pipe rows with NO separator still fail — those publish as literal pipes",
       not rep(Body=good_body + "\n\n| a | b |\n| 1 | 2 |\n").ok)
    ok("unclosed fence fails", not rep(Body=good_body + "\n\n```mermaid\nflowchart TD\n").ok)
    ok("nested list fails", not rep(Body=good_body + "\n\n- top\n  - nested\n").ok)
    ok("banned word in body fails", not rep(Body=good_body.replace("Opening", "We leverage")).ok)
    ok("over-cap tweet fails", not rep(Thread="x" * (_X_CAP + 1)).ok)
    ok("over-cap beat inside a chain fails",
       not rep(Thread="fine\n\n---\n\n" + "x" * (_X_CAP + 1)).ok)
    ok("a long Premium beat PASSES — 280 is not the limit any more",
       rep(Thread="x" * 1200).ok)
    ok("bluesky over 300 fails", not rep(Bluesky="x" * 301).ok)
    ok("raw URL in a spoke fails", not rep(Thread="read it https://x.co/a").ok)
    ok("{link} token is allowed", rep(Thread="read it: {link}").ok)
    # TABLES ARE ALLOWED NOW (#450). This rule used to refuse every one of them, which blocked
    # OSDev4 the same afternoon he was asked to start using them.
    good_tbl = good_body + "\n\n| Tier | Multiple |\n|---|---|\n| Owner-operator | 4-7x |\n"
    ok("a well-formed markdown table PASSES", rep(Body=good_tbl).ok)
    bad_tbl = good_body + "\n\n| Tier | Multiple |\n| Owner-operator | 4-7x |\n"
    ok("pipe rows with NO separator still fail — they publish as literal pipes",
       not rep(Body=bad_tbl).ok)
    ok("LinkedIn `---` warns but passes",
       rep(LinkedIn="a\n\n---\n\nb").ok and rep(LinkedIn="a\n\n---\n\nb").warnings)

    # the guide lane
    gbase = {"Type": "Guide", "Keyword": "ENGINE",
             "Lead Magnet Content": "The AI Content Engine\n\nA real opening paragraph here."}

    def grep_(**over):
        f = dict(gbase); f.update(over)
        return check_row({"id": "recG", "fields": f})

    ok("a guide with a real title line passes", grep_().ok)
    ok("THE 2026-07-31 DEFECT: prose line 1 fails", not grep_(**{
        "Lead Magnet Content":
            "A reel dies in 72 hours. A post on ground you own is still being found in 2028.\n\n"
            "Body follows."}).ok)
    ok("empty keyword fails", not grep_(Keyword="").ok)
    ok("a guide may carry a table too", grep_(**{
        "Lead Magnet Content": "Good Title\n\nA real opening paragraph here.\n\n"
                               "| a | b |\n|---|---|\n| 1 | 2 |\n"}).ok)
    ok("guide always reports the slug it will take",
       any("/guides/" in w for w in grep_().warnings))

    # collisions
    other = [{"id": "recOTHER", "fields": {"Slug": "a-good-clean-slug",
                                           "SEO Keyword": "a good keyword"}}]
    r = check_row({"id": "recTEST", "fields": dict(base)}, other)
    ok("slug collision fails", any("collides" in e for e in r.errors))
    ok("keyword collision fails", any("chasing one query" in e for e in r.errors))

    ok("unknown Type fails", not check_row({"id": "r", "fields": {"Type": "Tweet"}}).ok)

    print(f"\n{'ALL PREFLIGHT SELF-TESTS PASS' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
