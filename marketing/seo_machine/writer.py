"""Question in, article out. The ONLY part of this machine that reasons.

Everything else in `marketing/seo_machine/` is regex, HTTP and dict-building, and deliberately so
(`CLAUDE.md` non-negotiable 3). One `brain.think()` call per article, two if the first draft is
refused, and nothing else on the box spends a token on SEO.

THE REPAIR ROUND IS NOT POLITENESS, IT IS THE DIFFERENCE BETWEEN PUBLISHING AND NOT. Nobody reads
an article before it goes live, so a refusal with no repair is simply an article that never
exists. The guard's refusals are already written as "the exact text that tripped it" plus why, so
they hand back to the model almost verbatim. One round, then it stays refused and the row records
the reason for a person to read — a second repair is throwing money at a model that has already
shown it cannot satisfy the rule.

IT WRITES THE PROSE; IT DOES NOT CHOOSE THE SLUG. The slug is the article's identity — the thing
every link depends on and the key a re-publish patches by. A model that renames it on a re-run
orphans the old URL and creates a second article, silently. So the slug is derived from the title,
deterministically, by us.

NOTHING OF OUR BOX IS IN HERE. The facts it may state and the rules it must obey arrive as
arguments, from the box's settings screen (`docs/SEO_MACHINE_OWNBOX_SEED.md`). A box with no facts
entered gets an article that states none.
"""
from __future__ import annotations

import json
import re

from core import brain
from core.logging import get_logger
from marketing.seo_machine import guard, portable_text, publisher, settings

log = get_logger(__name__)

MAX_REPAIRS = 1
_MAX_TOKENS = 4000

_SYSTEM = """You write factual reference articles for one business's own website.

You are writing for two readers at once: a person searching for an answer, and an answer engine
deciding whether to quote you. Both want the same thing — the answer first, plainly, with nothing
to wade through.

Rules that are not style preferences:
- Open with a short answer of 40-60 words that stands alone. No "it", no "as above", no "this" —
  someone reading only that paragraph, with no page around it, must get a complete answer.
- State only facts from the list you are given. If a fact you want is not on the list, write
  around it. Never estimate, never round, never infer a number.
- Write small counts as words ("three steps", not "3 steps"). Put any sequence in a numbered
  Markdown list, so the digits are list markup rather than prose. Never put a date or a year in
  the text.
- Never name a competitor. Compare categories, not companies.
- Plain Markdown only: ##/### headings, -, 1., **bold**, `code`, [text](url), > quotes, tables.
- No exclamation marks. No marketing adjectives. Short sentences.

Return ONLY a JSON object, no commentary and no code fence:
{"title": "...", "short_answer": "...", "meta_description": "...", "body_markdown": "...",
 "faqs": [{"question": "...", "answer": "..."}]}
meta_description is at most 155 characters. faqs may be an empty list."""


def _prompt(question, *, facts=(), never_words=(), never_phrases=(), competitors=(),
            allowed_numbers=(), category=None, notes=None) -> str:
    parts = [f"Write the article that answers this question:\n\n{question}\n"]
    if category:
        parts.append(f"Category: {category}")
    if facts:
        parts.append("The ONLY facts you may state about this business:\n"
                     + "\n".join(f"- {f}" for f in facts))
    if allowed_numbers:
        parts.append("The ONLY numbers that may appear anywhere in your text:\n"
                     + ", ".join(str(n) for n in allowed_numbers)
                     + "\nAny other digit will be rejected. Write small counts as words and put "
                       "steps in a numbered list.")
    else:
        parts.append("NO numbers may appear anywhere in your text. Write counts as words.")
    banned = list(never_words) + list(never_phrases)
    if banned:
        parts.append("These words and phrases must not appear, in any form, including plurals "
                     "and inside longer words where noted:\n" + ", ".join(banned))
    if competitors:
        parts.append("Never name these companies:\n" + ", ".join(competitors))
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def _parse(raw: str) -> dict:
    """The model's JSON, however it chose to wrap it.

    A fenced block or a sentence of preamble is a formatting slip, not a failed article, and
    throwing the draft away over one costs a whole call. A response with no object in it at all
    is a real failure and raises, and `write()` spends its repair round on it.

    REVIEW ITEM 8 (OSDev1, #1557). This used to look for a fence ANYWHERE first, so a clean JSON
    reply whose `body_markdown` held a code block was cut down to that code block, failed to parse,
    and raised before the repair round could ask again. Now the reply is parsed as it came first;
    only a fence that WRAPS the whole reply is unwrapped; and the widest {...} is the last resort.
    """
    text = (raw or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    wrapped = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", text, re.S)
    if wrapped:
        try:
            return json.loads(wrapped.group(1))
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError as e:
            raise ValueError(f"the model's reply was not JSON: {text[:200]}") from e
    raise ValueError(f"the model's reply held no JSON object: {text[:200]}")


def _fields(drafted: dict, *, category=None, content_type=None) -> dict:
    title = (drafted.get("title") or "").strip()
    if not title:
        raise ValueError("the model returned no title, and title is required by the contract")
    body_md = drafted.get("body_markdown") or ""
    return {
        "title": title,
        "slug": portable_text.article_slug(title),      # OURS, never the model's — see the docstring
        "short_answer": (drafted.get("short_answer") or "").strip() or None,
        "meta_description": (drafted.get("meta_description") or "").strip() or None,
        "body_blocks": portable_text.portable_text(body_md) if body_md else None,
        "source_markdown": body_md or None,
        "faqs": [f for f in (drafted.get("faqs") or [])
                 if isinstance(f, dict) and f.get("question") and f.get("answer")] or None,
        "category": category,
        "content_type": content_type,
    }


def write(question: str, *, facts=None, lists=None, category=None, content_type=None,
          job_id=None) -> dict:
    """Draft one article and return the fields `publisher.publish()` takes.

    Raises `guard.GuardRefused` if the draft still breaks the box's rules after the repair round —
    the caller records that on the row, with `e.refusals` naming every reason.
    """
    # REVIEW ITEM 10 (OSDev1, #1557): a writer called without lists used to check against NOTHING,
    # so it would pass a draft the publisher then refused, paying for a publish that could not
    # happen. No lists means the box's lists, exactly as in the publisher. Facts likewise.
    lists = dict(settings.lists() if lists is None else lists)
    facts = tuple(settings.facts() if facts is None else facts)
    prompt = _prompt(question, facts=facts, category=category, **lists)
    notes = None

    for attempt in range(MAX_REPAIRS + 1):
        raw = brain.think(task="seo.article",
                          prompt=prompt if notes is None else prompt + "\n\n" + notes,
                          system=_SYSTEM, max_tokens=_MAX_TOKENS, job_id=job_id, machine="seo")
        try:
            fields = _fields(_parse(raw), category=category, content_type=content_type)
        except ValueError as e:
            # Unreadable, or no title. That is a draft to redo, not a reason to skip the repair
            # round, which is what it is for.
            log.warning("seo.draft_unreadable", question=question[:80], attempt=attempt + 1,
                        error=str(e)[:160])
            if attempt == MAX_REPAIRS:
                raise
            notes = ("Your previous reply could not be used: " + str(e)[:300] + "\nReturn the "
                     "whole article again as ONE JSON object and nothing else.")
            continue
        # The same reading of the article the publisher will do, prose AND markup (review item 2).
        refusals = publisher.refusals(lists=lists, **fields)
        if not refusals:
            log.info("seo.draft_ok", question=question[:80], attempt=attempt + 1,
                     slug=fields["slug"])
            return fields

        log.warning("seo.draft_refused", question=question[:80], attempt=attempt + 1,
                    rules=sorted({r.rule for r in refusals}))
        if attempt == MAX_REPAIRS:
            raise guard.GuardRefused(refusals)

        # Hand the refusals back almost as they came. They already name the exact text and why,
        # which is the whole reason the guard returns every reason at once instead of the first.
        notes = ("Your previous draft was rejected. Fix ALL of these and return the whole "
                 "article again as JSON:\n"
                 + "\n".join(f'- {r.rule}: "{r.found}" — {r.why}' for r in refusals))
