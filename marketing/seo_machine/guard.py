"""The pre-publish guard — the only reviewer an article gets.

WHY THIS IS NOT A LINT. `OWNER 2026-09-25:` the machine will *"write and publish on its own"*, so
no human reads an article before it is live. `docs/OWNBOX_ARTICLES.md` is blunt about what that
means: *"Nobody reads an article before it is live, so the machine's pre-publish check is the only
reviewer"*, and OSDev1 ruled it **ships before auto-publish**. So this refuses; it never warns.

EVERY LIST IS THE BOX'S, AND NONE OF THEM IS OURS. This file shipped its first draft with our own
never-use words baked in — *phone, call, line, voice, plumbing* — and OSDev1 rejected it on the
golden-snapshot rule (`#1552`, 2026-09-25) with the case that settles it: **a plumber, a call
centre or a phone-repair shop could never publish an article about its own business.** Those words
are our box's *setting*; the guard is the *product*. So `never_words`, `never_phrases`,
`competitors` and `allowed_numbers` all arrive as arguments, there is no module-level list to
copy, and a box that supplies nothing refuses nothing but unsourced numbers.

Ownbox's own list — the receptionist vocabulary the owner reserved on 2026-09-22 — is therefore
DATA, entered on the box's SEO settings screen, not code in here.

NOTHING HERE REASONS. Deterministic work uses no Claude (`CLAUDE.md` non-negotiable 3): this is
regex and set membership, it runs on every draft, and it costs nothing.

HARDENED 2026-09-25 AFTER AN ADVERSARIAL REVIEW (OSDev1, on the owner's "please double check his
work"). The first version compared characters exactly, so the text a READER sees and the text the
guard read could differ and a forbidden term walked through: a non-breaking space, a curly
apostrophe or a soft hyphen inside it; "Call Rail" for CallRail; "agencies" for agency; "$499k" or
"499%" riding on an allowed 499. Every comparison below now runs on `_norm()` text, and each hole
has a planted-failure test in tests/test_seo_guard.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# ── what a reader sees, not the bytes that draw it ────────────────────────────────────────────────
# NFKC folds full-width digits, ligatures and NO-BREAK SPACES into their plain forms. The rest are
# characters that draw as nothing or as a plain quote or dash, and so must compare as one.
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_LOOKALIKE = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "‛": "'",
                            "“": '"', "”": '"', "‐": "-", "‑": "-",
                            "‒": "-", "–": "-", "—": "-", "−": "-"})


def _norm(text) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).translate(_INVISIBLE).translate(_LOOKALIKE)


# Between the words of a listed phrase or name: nothing, spaces, line breaks or hyphens. So
# "voice search", "voice-search", "voice\nsearch" and "voicesearch" are one phrase.
_GAP = r"[\s\-]*"
# Inside a CamelCase name, where the listed spelling runs words together: "Call Rail" is CallRail.
_CAMEL_GAP = r"[\s\-]?"

# The regular plurals a bare `+s` misses, and the irregular ones a buyer will not think to list.
_IRREGULAR = {"person": ("people",), "man": ("men",), "woman": ("women",), "child": ("children",),
              "mouse": ("mice",), "foot": ("feet",), "tooth": ("teeth",), "goose": ("geese",)}


def _forms(word: str) -> list[str]:
    w = word.lower()
    out = {w, w + "s", w + "es"}
    if len(w) > 1 and w.endswith("y") and w[-2] not in "aeiou":
        out.add(w[:-1] + "ies")
    if w.endswith("fe"):
        out.add(w[:-2] + "ves")
    elif w.endswith("f"):
        out.add(w[:-1] + "ves")
    out.update(_IRREGULAR.get(w, ()))
    return sorted(out, key=len, reverse=True)


def _term_re(term: str, *, plurals: bool, keep_case: bool = False) -> re.Pattern | None:
    """A matcher for one listed word, phrase or name, or None for an empty entry.

    EDGE PUNCTUATION IS PART OF A SHORT NAME AND OPTIONAL ON A LONG ONE. ".NET" without its dot is
    the word "net", and "C++" without its pluses is the letter "c", so a core under four letters
    keeps its punctuation. "Yahoo!" and "Acme Inc." are still themselves without it.

    `keep_case`: a capital letter the buyer typed must be a capital in the text. That is what lets
    a box list the company "Front" without refusing "in front of" or "the front end" (OSDev6's
    #1545 §5 finding, which whole-word matching alone could not clear).
    """
    t = _norm(term).strip()
    core = re.sub(r"^\W+|\W+$", "", t)
    if not core:
        return None
    if len(re.sub(r"\W", "", core)) >= 4:
        t = core
    words = [w for w in re.split(r"[\s\-]+", t) if w]
    pieces = []
    for i, w in enumerate(words):
        subs = [x for x in re.split(r"(?<=[a-z])(?=[A-Z])", w) if x]
        last_word = i == len(words) - 1
        esc = []
        for j, sub in enumerate(subs):
            if plurals and last_word and j == len(subs) - 1 and sub.isalpha():
                esc.append("(?:" + "|".join(re.escape(f) for f in _forms(sub)) + ")")
            elif keep_case and sub[:1].isupper():
                esc.append(re.escape(sub[0]) + "(?i:" + re.escape(sub[1:]) + ")" if len(sub) > 1
                           else re.escape(sub))
            else:
                esc.append("(?i:" + re.escape(sub) + ")")
        pieces.append(_CAMEL_GAP.join(esc))
    pat = _GAP.join(pieces)
    if re.match(r"\w", t):
        pat = r"(?<!\w)" + pat
    if re.search(r"\w$", t):
        pat = pat + r"(?!\w)"
    return re.compile(pat, 0 if keep_case else re.I)


# ── numbers ───────────────────────────────────────────────────────────────────────────────────────
# A number is its VALUE and its UNIT. "$499", "499" and "499.00" are one fact; "499%", "$499k" and
# "499x" are three others, and an allowed 499 must not vouch for them. Thousands separators are
# only commas in groups of three: "4,99" is not 499. A currency sign is ignored, as before.
_UNIT = {"%": "%", "k": "k", "m": "m", "mm": "m", "b": "b", "bn": "b", "x": "x",
         "hundred": "h", "thousand": "k", "million": "m", "billion": "b", "trillion": "t"}
_NUMBER_RE = re.compile(
    r"(?<![\d.,])\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(%|k|mm|m|bn|b|x)(?![a-z])|\s+(hundred|thousand|million|billion|trillion)\b)?", re.I)
# Spelled-out counts. One to ten are ordinary prose ("one of the reasons"); eleven and up, and any
# scale word, are almost always a count somebody would have to source.
_NUMBER_WORD_RE = re.compile(
    r"(?<![\d\w])(?:(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s\-](?:one|two|three|four|five|"
    r"six|seven|eight|nine))?(?:\s+(?:hundred|thousand|million|billion|trillion))?"
    r"|(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|a)\s+)?"
    r"(?:hundreds?|thousands?|millions?|billions?|trillions?|dozens?))(?!\w)", re.I)


def _number_key(value: str, unit: str | None, scale: str | None) -> tuple[str, str]:
    try:
        v = format(Decimal(value.replace(",", "")).normalize(), "f")
    except InvalidOperation:
        v = value
    u = (unit or scale or "").lower()
    return v, _UNIT.get(u, u)


def _allowed_keys(allowed_numbers) -> tuple[set, set]:
    """The box's fact list as number keys, and as spelled-out phrases."""
    keys, words = set(), set()
    for n in allowed_numbers or ():
        s = _norm(n).strip()
        m = _NUMBER_RE.search(s)
        if m:
            keys.add(_number_key(m.group(1), m.group(2), m.group(3)))
        elif s:
            words.add(re.sub(r"\s+", " ", s.lower()))
    return keys, words


@dataclass(frozen=True)
class Refusal:
    rule: str          # "never_word" | "never_phrase" | "competitor" | "unsourced_number"
    found: str         # the exact text that tripped it
    why: str           # what a person should do about it


class GuardRefused(Exception):
    """Raised instead of publishing. Carries every refusal, not just the first."""

    def __init__(self, refusals: list[Refusal]):
        self.refusals = refusals
        super().__init__("; ".join(f"{r.rule}: {r.found} — {r.why}" for r in refusals))


def check(text: str, *, never_words=(), never_phrases=(), allowed_numbers=(),
          competitors=(), word_competitors=(), markup: bool = False) -> list[Refusal]:
    """Every reason this text must not be published. Empty list means it may go.

    All four lists are PLAN-STORE ROWS, never constants (`docs/SEO_AEO_MACHINE_BUILD_SPEC.md`
    §2.4). A fresh box with all four empty refuses nothing but unsourced numbers, which is the
    proof that nothing of ours leaks into the product.

    - `never_words` and `never_phrases`: case-insensitive, across spaces, hyphens and line
      breaks, with their plurals (agency → agencies, person → people).
    - `competitors`: the same, without plurals (a company's plural is usually just an English
      word), and ALWAYS case-insensitive. #1562 first let every `[A-Z][a-z]+` name keep its
      capital, to spare "the front end" on a box listing Front; #1574 F-A proved that shape is
      most brand names (Salesforce, Zendesk, Intercom), so eight of our eleven seed rivals went
      live unseen in lowercase slugs and links. The guard cannot know which names are words.
    - `word_competitors`: the few rivals the BUYER marks as also being everyday words ("Front").
      In prose they match only with the capital the buyer typed. In `markup=True` text (slugs,
      URLs, code), which carries no case, they match case-insensitively: refusing a
      "front-end" slug is the safe side of a public address.
    - `allowed_numbers`: value and unit must both match: "$499" = "499" = "499.00", but not
      "499%", "$499k" or "499x". Spelled-out counts ("twelve thousand") need listing too.
    """
    t = _norm(text)
    out: list[Refusal] = []

    for rule, terms, plurals in (("never_word", never_words, True),
                                 ("never_phrase", never_phrases, True)):
        for term in terms or ():
            rx = _term_re(term, plurals=plurals)
            for m in (rx.finditer(t) if rx else ()):
                out.append(Refusal(rule, m.group(0), "on this box's never-use list"))

    for name, keep_case in ([(n, False) for n in competitors or ()]
                            + [(n, not markup) for n in word_competitors or ()]):
        rx = _term_re(name, plurals=False, keep_case=keep_case)
        if rx and rx.search(t):
            out.append(Refusal("competitor", str(name), "on this box's competitor list"))

    keys, words = _allowed_keys(allowed_numbers)
    for m in _NUMBER_RE.finditer(t):
        if _number_key(m.group(1), m.group(2), m.group(3)) not in keys:
            out.append(Refusal("unsourced_number", m.group(0),
                               "not in this box's price and fact list — a count we cannot "
                               "source does not render"))
    for m in _NUMBER_WORD_RE.finditer(t):
        if re.sub(r"\s+", " ", m.group(0).lower()) not in words:
            out.append(Refusal("unsourced_number", m.group(0),
                               "a spelled-out count not in this box's fact list"))
    return out


def assert_publishable(text: str, **lists) -> None:
    """Raise `GuardRefused` with EVERY reason at once, so one round of fixes clears a draft."""
    refusals = check(text, **lists)
    if refusals:
        raise GuardRefused(refusals)
