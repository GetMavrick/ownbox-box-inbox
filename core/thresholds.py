"""The editorial numbers, read from one file by both the gate and the prompt.

THE RULE THIS SERVES (owner): **when you disagree with the gate, you edit the file — you do not
argue with the gate.** `editorial-thresholds.md` at the repo root is that file.

WHY A FIFTH COPY WOULD HAVE BEEN WORSE THAN NONE. The proposal that started this was to move the
gate's body-length bound into a config file. OSDev1 checked and found the bound was already
500-2000 in three of the four places it is written down — and that the 800 the owner actually hit
came from the fourth: the PROMPT's tier menu, whose first entry read "~500-800 … say it and
stop". A model reading that anchors there. So a file feeding only the gate would have let him
change a 2000 that was already 2000, watch the next piece come back at 800, and be right back to
arguing with a machine that was never the thing in control.

So this feeds BOTH readers, and neither restates a value:

    editorial-thresholds.md
            ├─→ scripts/preflight_written.py   the gate    — refuses what breaks a bound
            └─→ marketing/content_machine/written/daily_seed.py  the prompt — states the bounds and the tiers

STDLIB ONLY, AND THAT IS A HARD REQUIREMENT, not a preference. `preflight_written.py` is
deliberately dependency-free — it runs standalone against the live base and its `--self-test`
touches no network, which is exactly the ad-hoc situation where a missing PyYAML would hurt. It
cannot `import yaml` and it cannot reach `core.config`, which does. Hence a ~15-line parser here
instead of a schema, and hence this module importing nothing from `core`.

NOT CACHED, DELIBERATELY. `get_config()` is `lru_cache(maxsize=1)`, so anything routed through it
needs a restart to see an edit — which reintroduces the deploy loop this exists to remove. This
re-reads per call. It is a few hundred bytes next to a sweep that already makes network calls,
and "change the number, next sweep uses it" IS the feature.

THREE RULES FOR A MISSING THING, in descending order of how much they matter:

  1. A missing FILE is not a crash. Fall back to the values shipped below so a clone still runs.
     A file that declares no keys at all counts as missing — see `load()`.
  2. A missing KEY is not an inferred key. Return None, say so, and SKIP that one check. Never
     guess a number — a guessed bound is a bound nobody agreed to that still refuses work. This
     is why `load()` does not merge over the fallback: a merge would make deleting a line a
     no-op, and a rule you cannot delete is a rule you have to argue with.
  3. Every message names the key and the file, so the writer can go straight to the line.
"""
from __future__ import annotations

import os

# The path is absolute-from-this-file so it resolves the same from a sweep on the box, from
# `python3 scripts/preflight_written.py` in any directory, and from a test.
PATH = os.environ.get(
    "AIOS_THRESHOLDS_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "editorial-thresholds.md"))

# Which keys are numbers and which are comma-separated lists. A key absent from BOTH is returned
# as its raw string — `body_tiers` is the one that matters, and it is prose carrying numbers.
_INTS = frozenset((
    "body_min_words", "body_max_words", "title_max_chars", "meta_min_chars", "meta_max_chars",
    "slug_min_words", "slug_max_words", "h2_min", "h2_max", "headline_max_chars",
    # The spoken script. Separate keys rather than reusing the body bounds because they measure
    # a different artifact — 100-160 words is a fail for an article and correct for a reel.
    "script_min_words", "script_max_words",
    # X's own hard cap on the caption that carries a published Article.
    "x_caption_max_chars"))
_LISTS = frozenset(("banned_words", "banned_openers",
                    "script_banned_cta", "script_burned_hooks",
                    "script_trade_nouns", "script_hook_assumptions", "script_spoken_words"))
# The one free-text key. It is prose carrying numbers — editorial judgement expressed as a menu —
# which is why it is not a scalar and why it lives here rather than in code.
_STRINGS = frozenset(("body_tiers",))
KNOWN = _INTS | _LISTS | _STRINGS

# THE ONE COPY OF THE OLD VALUES, and it exists only so a clone with no file still runs (rule 1).
# It is not a second source of truth: nothing reads it while the file is present, and the
# acceptance test greps both readers to prove no threshold literal survives outside this table.
FALLBACK: dict = {
    "body_min_words": 500, "body_max_words": 2000,
    "body_tiers": ("500-800 one sharp claim | 900-1400 an argument with structure "
                   "| 1500-2000 a guide"),
    "title_max_chars": 60, "meta_min_chars": 150, "meta_max_chars": 160,
    "slug_min_words": 3, "slug_max_words": 6,
    "h2_min": 3, "h2_max": 8, "headline_max_chars": 70,
    "banned_words": [
        "leverage", "unlock", "revolutionize", "game-changer", "cutting-edge", "synergy",
        "deep dive", "thought leadership", "value proposition", "optimize", "streamline",
        "robust", "best-in-class", "next-level", "holistic", "utilize", "journey",
        "transformation", "transform", "elevate", "empower", "authentic", "lean into",
        "intentional", "aligned", "abundance", "showing up", "really", "very", "truly",
        "literally", "basically", "actually", "amazing", "incredible", "mind-blowing",
        "absolutely", "totally"],
    "banned_openers": [
        "in today", "many people", "as a ", "let's talk about", "today i want",
        "i want to tell you about", "have you ever wondered", "did you know", "listen up",
        "here's the thing"],
    # The spoken script. `comment` is deliberately NOT a banned CTA — the lead-magnet funnel
    # depends on the keyword being said out loud in the reel, so gating it would refuse the one
    # call to action that belongs there. See the file for the note the owner can act on.
    # 45-140 and not 100-160: the latter was fitted to the finished SCRIPT this branch
    # replaces, and `editorial-thresholds.md` records it refusing 18 of 20 real seeds. The
    # caption key is purely additive — a different artifact with a different bound.
    "script_min_words": 45, "script_max_words": 140,
    "x_caption_max_chars": 256,
    "script_banned_cta": [
        "book a call", "dm me", "link in bio", "follow for more", "sign up", "swipe up",
        "click the link", "try me", "buy now", "get started today", "book a demo",
        "schedule a call"],
    "script_trade_nouns": [
        "plumber", "plumbers", "roofer", "roofers", "hvac", "contractor", "contractors",
        "technician", "technicians", "homeowner", "homeowners", "dispatch", "water heater",
        "crawl space", "electrician", "electricians", "landscaper", "landscapers"],
    "script_hook_assumptions": [
        "your client", "your clients", "your team", "your techs", "your employees",
        "your crew", "your staff", "your revenue"],
    "script_spoken_words": [
        "injected", "diff", "interface", "adoption", "allocation", "non-billable", "median",
        "qualify", "open-weights", "deploy", "enforce", "pipeline", "constraint",
        "utilization", "iterate", "leverage", "optimize", "dependency", "capacity"],
    "script_burned_hooks": [
        "you know that feeling when", "pov:", "here's a story about",
        "three things nobody tells you", "the #1 reason why",
        "this one thing changed everything", "let me tell you about", "trust me on this",
        "hot take:"],
}


def _parse(text: str) -> dict:
    """`key: value` lines out of Markdown. Everything else is ignored, on purpose.

    That tolerance is what lets the owner keep his reasoning in the same file as his numbers —
    headings, prose, a note to himself about why 160 and not 200. A stricter format would be a
    file he does not open, and a file he does not open cannot be the thing he edits instead of
    arguing.

    A line inside a fenced block is skipped: the file explains itself with examples, and an
    example must never become a setting.
    """
    out: dict = {}
    fenced = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().strip("`*_"), val.strip()
        # ONLY A KNOWN KEY IS A SETTING. An allowlist rather than a shape test, because the file
        # is Markdown the owner writes prose in and prose contains colons — "Note: I picked 160
        # because Google truncates there" is a sentence, and a parser that reads it as a setting
        # named `Note` is one he cannot safely explain himself in. It also means a typo'd key is
        # simply absent, which rule 2 already handles by skipping that check and saying so.
        if key not in KNOWN or not val:
            continue
        if key in _INTS:
            try:
                out[key] = int(val)
            except ValueError:
                continue                          # a typo'd number is a MISSING key, not a zero
        elif key in _LISTS:
            out[key] = [v.strip() for v in val.split(",") if v.strip()]
        else:                                     # _STRINGS
            out[key] = val
    return out


def load(path: str | None = None) -> dict:
    """Every threshold the file declares. Never raises, never caches.

    NOT MERGED OVER THE FALLBACK, and that is the whole of rule 2. Merging was the first cut and
    it is wrong: it makes deleting a line do nothing. The owner strikes `body_max_words`, means
    "stop enforcing a ceiling", and a merge quietly reinstates the shipped 2000 — which is the
    machine winning an argument, the exact failure this file exists to end. A key absent from a
    file that declares other keys is a DELETION, and a deletion skips that one check.

    A file that declares NOTHING is treated as no file, not as ten deletions. Zero bytes is what
    a failed save looks like, and "every rule off" must not be reachable by accident — only by
    striking lines one at a time, which is a thing you can only do on purpose.
    """
    p = path or PATH
    try:
        with open(p, encoding="utf-8") as fh:
            found = _parse(fh.read())
    except OSError:
        return dict(FALLBACK)
    return found or dict(FALLBACK)


def get(key: str, path: str | None = None):
    """One threshold, or None if neither the file nor the fallback has it.

    None is the signal to SKIP a check (rule 2). It is deliberately not a default — a guessed
    bound is a bound nobody agreed to that still refuses somebody's work.
    """
    return load(path).get(key)


def where(key: str, value=None) -> str:
    """The phrase every message ends with: ``over `body_max_words: 2000` (editorial-thresholds.md)``

    Rule 3, in one place. A writer who reads a rejection should be able to go straight to the
    line that caused it, and a message that names only the number sends them hunting.
    """
    shown = f"{key}: {value}" if value is not None else key
    return f"`{shown}` ({os.path.basename(PATH)})"
