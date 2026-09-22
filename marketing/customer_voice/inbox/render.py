"""A message body, made readable, WITHOUT rendering anybody's HTML.

WHAT A BUYER WAS ACTUALLY SEEING (found by OSDev5, 2026-09-22): `email_channel._body_text()`
keeps `text/plain` and throws the `text/html` part away at ingest, so the thread shows the
vendor's plain-text FALLBACK — the part nobody at the sending company ever looks at. It arrives
with `<https://…>` link syntax, tables exploded one value per line, and stacks of blank lines,
and `.msg .b` is `white-space:pre-wrap`, so every one of those blank lines became dead space on
the screen. Gmail shows the HTML part. We never kept it.

WHY THIS IS NOT "RENDER THE HTML INSTEAD", which is the obvious fix and the wrong one today:

  · There is no sanitiser installed, and hand-rolling one is a textbook XSS hole shipped to every
    box. OSDev5 stopped there, correctly.
  · A sanitiser alone would not make it safe anyway. A remote image in a customer's email is a
    read-receipt that tells the sender when the buyer opened it, and sender CSS can reach out and
    restyle the page around the bubble. Safe rendering means a sandboxed `<iframe srcdoc>` with a
    restrictive CSP and images blocked until asked for — a real feature, not a same-day fix.
  · `requirements.lock` is hash-pinned and generated on the box platform, so a new dependency is
    a lock regeneration that ships to every machine we have sold.

So this does the honest, dependency-free thing: take the text we already store and present it the
way a person wrote it, rather than the way a mailer's fallback generator emitted it.

ESCAPE FIRST, THEN LINKIFY. Every function here works on ALREADY-ESCAPED text, so there is no
path by which a sender's angle bracket becomes a tag. The only HTML this module emits is its own
`<a>` and `<br>`, built from matched URL characters.
"""
from __future__ import annotations

import html
import re

# A URL as it survives escaping: `&` has already become `&amp;`, so the pattern must accept it.
# Trailing punctuation is left OUT of the link — a sentence ending "see https://x.com/a." should
# not produce a link with the full stop inside it.
_URL = re.compile(r"""(?P<url>https?://[^\s<>"']+?)(?P<tail>[.,;:!?)\]]*)(?=\s|$)""")


def _unclosed(url: str, tail: str) -> tuple[str, str]:
    """Give back a bracket the URL actually opened.

    THE TAIL RULE IS RIGHT FOR A SENTENCE AND WRONG FOR WIKIPEDIA. Stripping every trailing
    `)` turns `https://en.wikipedia.org/wiki/X_(Y)` into an href ending `X_(Y` — a link that
    404s, which is worse than no link, while `(see https://x.com/a)` genuinely wants the
    bracket left outside. The difference is whether the URL opened the bracket itself, so
    that is what is counted rather than guessed. Measured on both before and after.
    """
    while tail.startswith(")") and url.count("(") > url.count(")"):
        url, tail = url + ")", tail[1:]
    while tail.startswith("]") and url.count("[") > url.count("]"):
        url, tail = url + "]", tail[1:]
    return url, tail

# `<https://…>` is RFC 3986's "angle-bracket delimited" form and mailers emit it constantly. After
# escaping it reads `&lt;https://…&gt;`, and showing a buyer those literal brackets is noise.
_ANGLED = re.compile(r"&lt;(?P<url>https?://[^\s<>\"']+?)&gt;")

_MAX_RUN = 1          # blank lines kept from a run of them
_MAX_LINK_TEXT = 48   # a link's visible text, before the middle is elided


def _shorten(url: str) -> str:
    """A long tracking URL, shown short. The href keeps every character."""
    if len(url) <= _MAX_LINK_TEXT:
        return url
    return url[: _MAX_LINK_TEXT - 12] + "…" + url[-8:]


def _link(url: str) -> str:
    """One anchor. `url` is already escaped, so it is safe in both the href and the text.

    `noopener noreferrer` because the target is a stranger's link, and `nofollow` because this is
    somebody's private mail and we are not passing it any standing.
    """
    return (f'<a href="{url}" target="_blank" rel="noopener noreferrer nofollow">'
            f'{_shorten(url)}</a>')


def collapse_blank_lines(text: str, keep: int = _MAX_RUN) -> str:
    """Runs of empty lines reduced to `keep`. The shape of the message survives; the gaps do not.

    NOT `strip()` ON THE WHOLE THING AND NOT A REFLOW. A mailer's fallback uses single newlines
    meaningfully — one address line per line, one table cell per line — so joining them would
    destroy the only structure the fallback has. Only the RUNS go.
    """
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blanks = 0
    for ln in lines:
        if ln:
            blanks = 0
            out.append(ln)
            continue
        blanks += 1
        if blanks <= keep and out:          # never open with blank lines
            out.append("")
    while out and not out[-1]:              # nor close with them
        out.pop()
    return "\n".join(out)


def readable(text: str) -> str:
    """One message body as safe HTML: escaped, de-gapped, and with its links made real.

    THE ORDER IS THE SECURITY PROPERTY. `html.escape` runs on the raw text before anything else
    looks at it, so from that point on the string contains no live markup and every later step is
    operating on inert characters. A regex that assembled HTML from UNESCAPED input would be the
    hole this module exists to avoid.
    """
    # AN EMPTY `<>` IS WHERE A LINK WITH NO TEXT USED TO BE — the generator wrote the href into
    # the HTML part and had nothing to put in the plain one. It reaches the buyer as
    # "fundercrm.com <>", which reads as a rendering fault rather than as the debris it is, and
    # it appeared twice in the screenshot the owner sent. `_ANGLED` cannot catch it: there is no
    # URL between the brackets for it to match. Dropped before escaping, while it is still `<>`.
    escaped = html.escape(collapse_blank_lines((text or "").replace("<>", "")), quote=True)
    escaped = _ANGLED.sub(lambda m: _link(m.group("url")), escaped)
    def _one(m: "re.Match[str]") -> str:
        url, tail = _unclosed(m.group("url"), m.group("tail"))
        return _link(url) + tail

    escaped = _URL.sub(_one, escaped)
    return escaped.replace("\n", "<br>")
