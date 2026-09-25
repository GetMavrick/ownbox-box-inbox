"""Markdown → Sanity Portable Text. A VERBATIM PORT, and the copy is the point.

WHY THIS IS DUPLICATED, WHICH IS NORMALLY THE WRONG ANSWER. The identical converter already exists
in `marketing/content_machine/leadmagnet/sanity_client.py`, and importing it looked obviously
right until I measured what importing it does:

    >>> from marketing.content_machine.leadmagnet import sanity_client
    worker.registered_periodic  name=leadmagnet_capture         interval_s=20.0
    worker.registered_periodic  name=leadmagnet_keyword_intake  interval_s=300.0

Importing that module REGISTERS CONTENT MACHINE'S JOBS. A box that bought only the SEO machine
would start polling Airtable for lead magnets it does not own, every twenty seconds, forever. And
`export_box.sh` packages machines selectively, so on such a box the import may not even resolve.
An add-on machine has to stand on its own (`CLAUDE.md` non-negotiable 7).

SO IT IS COPIED, AND THE COPY IS MEASURED. Every symbol in the ported section is byte-for-byte
identical to its original, and `tests/test_seo_portable_text.py` compares them with
`inspect.getsource` on every run. Edit either copy and CI fails, naming the symbol that drifted.
That is the whole reason this is a port rather than a fork: the reserved-word list diverged
silently across two files and nobody noticed until it mattered (#1552), and this is the same shape
of risk with a detector attached.

DO NOT EDIT THE PORTED SECTION to fix an SEO bug. Fix it in the original, copy it here, and let
the drift test confirm they match. If the two ever genuinely need to differ, delete the drift test
in the same PR with a reason — do not let it rot.

Ported 2026-09-25 from sanity_client.py @ 56f01d25.
"""
from __future__ import annotations

import hashlib
import re

# ── VERBATIM PORT BEGINS — see the drift test before touching anything here ──────────────

def slugify(title: str) -> str:
    """Deterministic slug (our idempotency key). Lowercase, hyphenate, strip."""
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return s or "guide"


_FENCE_RE = re.compile(r"^```(\w+)?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
# An image ON ITS OWN LINE: ![alt](url). Line-level only, like every other block form here —
# an image buried mid-paragraph stays literal text, which is the converter's contract for
# anything it does not recognise (free-form text always publishes cleanly, never errors).
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\((\S+?)\)$")
# The fence language that makes images sit SIDE BY SIDE instead of stacking: ```row … ```.
# `_FENCE_RE` already captures it as a language, so this is a name check, not a second parser.
ROW_FENCE_LANG = "row"
# The `|---|---|` line under a table's header. A table is recognised ONLY when this is present,
# which is what keeps the converter's contract intact: a lone line that happens to start with a
# pipe is still a paragraph, exactly as it was before tables existed. Accepts the alignment
# colons GitHub allows (`:---`, `---:`, `:---:`) and the optional outer pipes.
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")
# Inline: `code`, **bold**, *italic*, [text](url). Bold is tried before italic so ** wins over *.
# Italic is *asterisks only* (underscores are left alone so file_names / snake_case never italicize).
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<bold>\*\*[^*]+?\*\*)"
    r"|(?P<italic>\*[^*\s][^*]*?\*)"
    r"|(?P<link>\[[^\]]+\]\([^)\s]+\))")


def _keygen():
    """A per-document key factory — Portable Text array items want a stable _key; a monotonic
    counter is unique within the doc and deterministic (testable)."""
    n = {"i": 0}
    def nxt() -> str:
        n["i"] += 1
        return f"k{n['i']}"
    return nxt


def _inline_spans(text: str, key) -> tuple[list, list]:
    """Parse inline Markdown in one line into portable-text spans + link markDefs. Recognizes
    `code`, **bold**, *italic*, [text](url); everything else is plain text. Non-nested (one mark
    per span) — enough for lead-magnet prose, and unknown syntax passes through as literal text."""
    spans, mark_defs, pos = [], [], 0

    def plain(s: str):
        if s:
            spans.append({"_type": "span", "_key": key(), "text": s, "marks": []})

    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            plain(text[pos:m.start()])
        kind, tok = m.lastgroup, m.group()
        if kind == "code":
            spans.append({"_type": "span", "_key": key(), "text": tok[1:-1], "marks": ["code"]})
        elif kind in ("bold", "italic"):
            # Recurse into the emphasized text so a nested link/code is parsed too, then STACK the
            # emphasis mark onto each inner span. Portable Text allows multiple marks per span, so
            # **[x](url)** becomes a span that is a link AND strong — not the literal "[x](url)"
            # that a flat (non-recursive) pass used to emit onto the live page.
            inner = tok[2:-2] if kind == "bold" else tok[1:-1]
            emph = "strong" if kind == "bold" else "em"
            inner_spans, inner_defs = _inline_spans(inner, key)
            mark_defs.extend(inner_defs)
            for sp in inner_spans:
                sp["marks"] = sp["marks"] + [emph]
            spans.extend(inner_spans)
        else:  # link
            lm = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", tok)
            mk = key()
            mark_defs.append({"_type": "link", "_key": mk, "href": lm.group(2)})
            spans.append({"_type": "span", "_key": key(), "text": lm.group(1), "marks": [mk]})
        pos = m.end()
    if pos < len(text):
        plain(text[pos:])
    if not spans:
        spans = [{"_type": "span", "_key": key(), "text": text, "marks": []}]
    return spans, mark_defs


def _text_block(text: str, style: str, key, list_item: str | None = None) -> dict:
    children, mark_defs = _inline_spans(text, key)
    block = {"_type": "block", "_key": key(), "style": style,
             "markDefs": mark_defs, "children": children}
    if list_item:
        block["listItem"], block["level"] = list_item, 1
    return block


def _table_cells(line: str) -> list[str]:
    """One `| a | b |` line → ["a", "b"]. Outer pipes optional, as in GitHub Markdown."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _image_row(code_lines: list[str], key) -> dict | None:
    """The inside of a ```row fence → one `imageRow` block, or None if it is not one.

    None means "this was not an image row after all", and the caller then emits the ordinary code
    block it would have emitted before this existed. That fallback is the whole safety story:

      EVERY LINE MUST BE AN IMAGE, or it is not a row. A fence with two images and one typo'd
      third does NOT publish two images and swallow the third — it publishes a code block showing
      all three lines verbatim, so the typo is visible on the page instead of vanishing from it.
      The converter's standing contract is that it never takes text away from the reader on a
      guess (see the table branch, which demands its `|---|` separator for the same reason).

    Blank lines are ignored so the markdown can breathe. An empty fence is None — a row of no
    images is nothing, and rendering nothing where the author wrote something hides the mistake.
    """
    imgs = []
    for raw in code_lines:
        s = raw.strip()
        if not s:
            continue
        m = _IMAGE_RE.match(s)
        if not m:
            return None
        # `alt` AND `caption` FROM THE SAME TEXT, because ImageRow.tsx keeps them separate:
        # `alt` is what a screen reader says, `caption` is what prints under the picture, and
        # only the second is optional. The single-image path (`imageUrl`) already passes the alt
        # as both, so a picture reads the same whether it is alone or in a row. Writing
        # `![](/img/x.png)` — empty alt — is therefore how you ask for a row with no captions.
        alt = m.group(1).strip()
        img = {"_type": "rowImage", "_key": key(), "url": m.group(2), "alt": alt}
        if alt:
            img["caption"] = alt
        imgs.append(img)
    if not imgs:
        return None
    return {"_type": "imageRow", "_key": key(), "images": imgs}


def portable_text(body: str) -> list:
    """Convert the owner's Markdown (Airtable 'Lead Magnet Content') into Sanity portable-text.
    Supported so authoring stays readable, never JSON:
      #/##/### → sub-headlines · ```lang fenced code``` → code/prompt blocks · > → featured quote
      - / * → bullet list · 1. → numbered list · **bold** *italic* `code` [text](url) inline
      | a | b | over |---|---| → a real table · ```mermaid → a diagram (rendered front-end side)
      ![alt](url) → an image · ```row of image lines ``` → those images SIDE BY SIDE.
    Anything unrecognized becomes a plain paragraph, so free-form text always publishes cleanly.

    TABLES AND DIAGRAMS EXIST BECAUSE THE SHAPE RECURS (OSDev4, 2026-08-11): "Comparison content
    wants a table, and the converter flattens markdown tables into literal pipes on the page.
    Row 5 is a cost comparison, row 19 has valuation multiples — both are naturally tabular and
    can't be." He was right that it recurs, and it was worse than cosmetic: a writer who reaches
    for the correct shape got punished with a wall of pipes, so the piece got written the wrong
    way round to avoid the bug. Fixing the converter is what lets the writing be what it wants."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    blocks, key, para, i, n = [], _keygen(), [], 0, len((body or "").replace("\r\n", "\n").split("\n"))

    def flush_para():
        if para:
            text = " ".join(s.strip() for s in para).strip()
            if text:
                blocks.append(_text_block(text, "normal", key))
            para.clear()

    while i < n:
        line = lines[i]
        fence = _FENCE_RE.match(line.strip())
        if fence:                                          # ```lang … ``` → a code/prompt block
            flush_para()
            lang, code_lines, i = fence.group(1), [], i + 1
            while i < n and not _FENCE_RE.match(lines[i].strip()):
                code_lines.append(lines[i]); i += 1
            i += 1                                         # skip the closing fence
            if lang == ROW_FENCE_LANG:                     # ```row … ``` → images side by side
                # WHY A FENCE AND NOT THREE BARE IMAGE LINES. Consecutive `![](…)` lines are
                # already valid Markdown that means three stacked images, and there is content on
                # the board relying on that. Inferring "side by side" from adjacency would
                # silently re-lay-out work nobody asked to change. The fence is the author saying
                # so out loud, and it costs one line to say.
                row = _image_row(code_lines, key)
                if row:
                    blocks.append(row)
                    continue
                # Not a row → fall through and publish the fence as the code block it looks like.
            # ```mermaid NEEDS NOTHING HERE. The fence's language rides through on the `code`
            # block and portableText.tsx already renders `language === "mermaid"` as a diagram
            # (DEV_SPEC B1). Emitting a distinct `mermaid` block type would land on a front end
            # with no handler for it and silently blank the diagram — the pipeline is already
            # whole, and the correct change to it is none.
            block = {"_type": "code", "_key": key(), "code": "\n".join(code_lines)}
            if lang:
                block["language"] = lang
            blocks.append(block)
            continue
        stripped = line.strip()
        if not stripped:
            flush_para(); i += 1; continue
        heading = _HEADING_RE.match(stripped)
        if heading:                                        # #/## → h2, ###+ → h3 (under the page h1)
            flush_para()
            style = "h2" if len(heading.group(1)) <= 2 else "h3"
            blocks.append(_text_block(heading.group(2).strip(), style, key)); i += 1; continue
        image = _IMAGE_RE.match(stripped)
        if image:                                          # ![alt](url) → an imageUrl block
            # A URL block, NOT a Sanity image asset. Assets need the upload API, another token
            # scope, and an asset ref per environment; a screenshot committed to the site's own
            # public/img/ has a stable URL on every deploy and survives a clone untouched. The
            # front end renders {_type:"imageUrl"} (portableText.tsx); Studio will show it as an
            # unknown block when editing that doc by hand, which is cosmetic and accepted.
            flush_para()
            blocks.append({"_type": "imageUrl", "_key": key(),
                           "url": image.group(2), "alt": image.group(1).strip()})
            i += 1; continue
        if (stripped.startswith("|") and i + 1 < n
                and _TABLE_SEP_RE.match(lines[i + 1].strip())):
            # | a | b |  over  |---|---|  → a real table. The separator line is REQUIRED, so a
            # paragraph that merely opens with a pipe is still a paragraph — the converter never
            # takes text away from the reader on a guess.
            flush_para()
            rows = [_table_cells(stripped)]
            i += 2                                         # header, then the separator
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_table_cells(lines[i])); i += 1
            # Pad ragged rows to the widest. A writer who drops a trailing pipe should get a
            # table with one empty cell, not a row that renders short and skews every column
            # after it — and Markdown itself is forgiving here, so the page should be too.
            width = max(len(r) for r in rows)
            blocks.append({"_type": "table", "_key": key(),
                           "rows": [{"_type": "tableRow", "_key": key(),
                                     "cells": r + [""] * (width - len(r))} for r in rows]})
            continue
        if stripped.startswith(">"):                       # > … → featured quote (joins wrapped lines)
            flush_para()
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            blocks.append(_text_block(" ".join(q.strip() for q in quote).strip(), "blockquote", key))
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            flush_para()
            blocks.append(_text_block(bullet.group(1).strip(), "normal", key, list_item="bullet"))
            i += 1; continue
        number = _NUMBER_RE.match(stripped)
        if number:
            flush_para()
            blocks.append(_text_block(number.group(1).strip(), "normal", key, list_item="number"))
            i += 1; continue
        para.append(line); i += 1
    flush_para()
    if not blocks:                                         # never publish an empty body
        blocks = [_text_block((body or "").strip() or " ", "normal", key)]
    return blocks


# ── VERBATIM PORT ENDS. Ours below, and the drift test ignores it. ───────────────────────

def article_slug(title: str) -> str:
    """`slugify` for articles, whose empty-title fallback must not be the word "guide".

    The ported `slugify` answers "guide" for a title that reduces to nothing (an emoji, say, or
    punctuation alone) — correct in the machine it came from, and wrong here: it would publish an
    ARTICLE at /articles/guide, and the second such title would silently PATCH the first, because
    the slug is the identity. Wrapped rather than edited so the port stays byte-identical and the
    drift test keeps covering it.

    REVIEW ITEM 6 (OSDev1, #1556): every title with no Latin letters came out as "article", so the
    second one overwrote the first, and a title only PARTLY in another script collided the same way
    ("AIとは" and "AIの使い方" were both "ai"). Whenever the slug drops letters the reader wrote, a
    short hash of the whole title is appended, so each distinct title gets its own address. The
    hash is of the title itself, so the same title always gets the same slug.
    """
    raw = (title or "").strip()
    s = slugify(raw)
    if s == "guide" and "guide" not in raw.lower():
        s = ""
    lost = any(ch.isalnum() and not ch.isascii() for ch in raw) or (not s and raw)
    if lost:
        digest = hashlib.sha1(" ".join(raw.lower().split()).encode("utf-8")).hexdigest()[:8]
        return f"{s}-{digest}" if s else f"article-{digest}"
    return s or "article"
