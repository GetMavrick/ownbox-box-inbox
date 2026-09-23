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


# ── A SENDER'S OWN HTML, SAFELY ───────────────────────────────────────────────────────────────
# THE PROBLEM, RESTATED. A buyer was reading the plain-text fallback of mail that was written in
# HTML — the version nobody at the sending company looks at. The mail itself is now kept
# (`inbox_message_detail.body_html`), and this is what puts it on a screen.
#
# THE SECURITY BOUNDARY IS THE SANDBOX, NOT A SANITISER. That distinction is the whole design.
# A sanitiser is a denylist of everything anybody has thought of, maintained by someone else,
# shipped as a hash-pinned dependency to every box we have sold; it is useful and it is not a
# boundary. A `sandbox` iframe with neither `allow-scripts` nor `allow-same-origin` is one, and
# the browser enforces it:
#
#   no allow-scripts       sender JavaScript does not run. Not "is stripped" — does not run,
#                          including anything a sanitiser's denylist had never heard of.
#   no allow-same-origin   the frame is an OPAQUE origin. It cannot read our cookies, our
#                          session, our DOM, or reach `window.parent` even if script did run.
#   no allow-forms         a fake "sign in to continue" inside a message cannot post anywhere.
#   no allow-top-navigation
#                          a message cannot navigate the buyer's tab away from their own inbox.
#   allow-popups           is the ONE thing granted, so that a real link in a real email still
#   + allow-popups-to-escape-sandbox
#                          opens — in a new tab, itself unsandboxed. Without this pair every
#                          link in every email is dead, which is not a safe inbox, it is a
#                          broken one. The pair is what Gmail's own frame effectively allows.
#
# AND A CSP INSIDE THE FRAME, which is the second half and the one that stops the quiet attack.
# `default-src 'none'` means the document may fetch NOTHING: no script, no stylesheet, no font,
# no XHR, no frame of its own. `img-src data:` is the important line — a REMOTE image in a
# customer's email is a read receipt that tells the sender the moment the buyer opened it, and
# our buyers' mail is full of them. Blocked here at the network layer, by the browser, with no
# list of tracker domains to maintain and nothing to keep up to date.
#
# `style-src 'unsafe-inline'` LOOKS WRONG AND IS CORRECT. The sender's own styling is the point
# of rendering their HTML at all; "unsafe-inline" for STYLE is not script execution, and the
# frame is an opaque origin with no scripts, so there is nothing for CSS to exfiltrate to and
# nowhere for it to reach. Sender CSS restyling our page — the other half of the risk — is
# prevented by the frame boundary itself, not by this line.
#
# HEIGHT IS ESTIMATED, AND A SCROLLING FRAME WOULD HAVE BEEN THE EASY WRONG ANSWER. An iframe
# does not size to its content without script, and script is the one thing this frame must never
# get: the page cannot measure an opaque-origin document and the document cannot report its own
# height. The obvious answer is a fixed cap that scrolls inside — and a scrollable region inside
# a scrolling page is a scroll trap on a phone, which is a pattern this product is removing
# elsewhere, not adding here. Every buyer screen is mobile first (owner, 2026-09-22).
#
# SO THE FRAME DOES NOT SCROLL; THE PAGE DOES. Its height is guessed from the content and the
# guess is deliberately GENEROUS, because the two failure modes are not equal: overestimating
# leaves white space at the bottom of a card, and underestimating loses a paragraph of somebody's
# email with nothing on screen to say it is missing. Whitespace is a blemish; a silently truncated
# message is the same class of bug as the 2000-character body cap this week began with.
#
# THE OVERFLOW RULE IS A SAFETY NET, NOT THE DESIGN. `scrolling` is left at its default so that a
# message the estimate underserves is still reachable rather than clipped — an escape hatch that
# should almost never be used, not the mechanism.
_FRAME_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; "
              "font-src data:; form-action 'none'; base-uri 'none'")
_FRAME_SANDBOX = "allow-popups allow-popups-to-escape-sandbox"
# MEASURED AT 390px, 16px/1.4: about 45 characters to a line and 23px to a line of text. A block
# element (`p`, `li`, heading, `tr`) costs roughly another 14px in margins. An `img` is the one
# thing no estimate can reach — its height is in the file, which we never fetched — so each is
# allowed a conservative slab.
# CALIBRATED, NOT GUESSED. The first cut of these numbers came out of arithmetic and underserved
# a real newsletter by 14% — a paragraph of somebody's email below the fold of a frame that does
# not scroll. They were then measured against Chromium at 390px with this frame's own stylesheet
# (the owner's morning review, a four-section newsletter, a twenty-row table, a one-line reply),
# and tuned until every sample came out over. `scripts/` keeps no harness for it because the
# check is three lines of Playwright; what matters is that these are measurements.
_PX_PER_LINE, _CHARS_PER_LINE, _PX_PER_BLOCK, _PX_PER_IMG = 23, 38, 18, 120
# A HEADING IS NOT A PARAGRAPH. `h1`/`h2` render at 1.5–2em with their own margins, so counting
# them as ordinary blocks is most of where the newsletter estimate went short.
_PX_PER_HEADING = 30
_FRAME_MIN_PX, _FRAME_MAX_PX = 140, 6000
_BLOCK = re.compile(r"(?i)<(p|div|li|tr|br|blockquote|table)\b")
_HEADING = re.compile(r"(?i)<h[1-6]\b")
_IMG = re.compile(r"(?i)<img\b")


def _estimate_px(body_html: str) -> int:
    """A generous guess at the rendered height. See the long note above for why it is a guess."""
    raw = str(body_html or "")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    lines = -(-len(text) // _CHARS_PER_LINE) if text else 0        # ceil
    px = (lines * _PX_PER_LINE
          + len(_BLOCK.findall(raw)) * _PX_PER_BLOCK
          + len(_HEADING.findall(raw)) * _PX_PER_HEADING
          + len(_IMG.findall(raw)) * _PX_PER_IMG
          + 40)                                                    # the body's own padding
    px = int(px * 1.25)                                            # deliberately over, see above
    return max(_FRAME_MIN_PX, min(_FRAME_MAX_PX, px))


def has_markup(body_html: str) -> bool:
    """Is there a sender HTML part worth framing? Whitespace and `<html></html>` are not."""
    got = str(body_html or "").strip()
    if not got:
        return False
    return bool(re.search(r"(?is)<(p|div|table|ul|ol|h[1-6]|img|a|br|span|td)\b", got))


def safe_frame(body_html: str, *, label: str = "Message") -> str:
    """The sender's HTML in a sandboxed frame, or "" when there is nothing to frame.

    EVERY BYTE OF `body_html` IS A STRANGER'S. It is never concatenated into our page as markup:
    it goes into the `srcdoc` ATTRIBUTE, escaped with `quote=True`, so `"` becomes `&quot;` and
    cannot close the attribute — the one and only way a sender could break out of the frame into
    our document. That escape is the whole trust boundary at this layer and it runs first.
    """
    if not has_markup(body_html):
        return ""
    doc = ('<!doctype html><html><head><meta charset="utf-8">'
           f'<meta http-equiv="Content-Security-Policy" content="{_FRAME_CSP}">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           # OUR OWN BASE STYLING, FIRST, so a message that styles nothing still reads like
           # something a person wrote rather than a 1996 default-serif wall. Anything the sender
           # sets afterwards wins, which is the right way round — it is their document.
           '<style>html{-webkit-text-size-adjust:100%}'
           'body{margin:0;padding:10px 12px;font:16px/1.4 -apple-system,BlinkMacSystemFont,'
           '"Segoe UI",Roboto,sans-serif;color:#111;background:#fff;word-break:break-word}'
           'img{max-width:100%;height:auto}table{max-width:100%}'
           'a{color:#0b57d0}</style></head><body>'
           + str(body_html) + '</body></html>')
    return (f'<iframe class="mail" title="{html.escape(str(label), quote=True)}" '
            f'sandbox="{_FRAME_SANDBOX}" referrerpolicy="no-referrer" loading="lazy" '
            f'style="height:{_estimate_px(body_html)}px" '
            f'srcdoc="{html.escape(doc, quote=True)}"></iframe>')
