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
from email.header import decode_header, make_header

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
# AN EMAIL'S PICTURES LOAD BY THEMSELVES (owner, 2026-10-07: "I want to always show images. I don't think anyone ever
# doesn't want to show an image. Just make it automatic."), replacing the Show images button he chose earlier that day.
# From https only: never http, and nothing that runs. Loading one still tells its sender the email was opened, and when;
# no address goes with it (the frame's no-referrer), and the frame's sandbox runs no script whatever the policy says.
_FRAME_CSP = ("default-src 'none'; img-src data: https:; style-src 'unsafe-inline' data:; "
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


def text_of(body_html: str) -> str:
    """The words of an HTML part, on one line: scripts, styles and the head dropped, tags stripped, entities decoded.
    PLAIN TEXT, never markup: for a list's preview and the text under a frame (the new inbox screens, which show a
    mail that arrived with no text part as `<!doctype html>` otherwise)."""
    raw = str(body_html or "")
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub("[\u00ad\u034f\u200b-\u200f\u2060\ufeff]", "", html.unescape(text))
    return re.sub(r"\s+", " ", text).strip()


def looks_like_html(text: str) -> bool:
    """Is this stored "text" really an HTML source (a mail that arrived with no text part)?"""
    return bool(re.match(r"(?is)\s*<(!doctype|html|head|body|table|div|meta|style|center|!--|p[\s>])", str(text or "")))


def _estimate_px(body_html: str) -> int:
    """A generous guess at the rendered height. See the long note above for why it is a guess."""
    raw = str(body_html or "")
    text = text_of(raw)
    lines = -(-len(text) // _CHARS_PER_LINE) if text else 0        # ceil
    px = (lines * _PX_PER_LINE
          + len(_BLOCK.findall(raw)) * _PX_PER_BLOCK
          + len(_HEADING.findall(raw)) * _PX_PER_HEADING
          + len(_IMG.findall(no_dead_images(no_tracking_pixels(raw)))) * _PX_PER_IMG   # a picture taken out takes no room
          + 40)                                                    # the body's own padding
    px = int(px * 1.25)                                            # deliberately over, see above
    return max(_FRAME_MIN_PX, min(_FRAME_MAX_PX, px))


def has_markup(body_html: str) -> bool:
    """Is there a sender HTML part worth framing? Whitespace and `<html></html>` are not."""
    got = str(body_html or "").strip()
    if not got:
        return False
    return bool(re.search(r"(?is)<(p|div|table|ul|ol|h[1-6]|img|a|br|span|td)\b", got))


def frame_height(body_html: str) -> int:
    """The frame's height for this HTML part (the generous estimate above)."""
    return _estimate_px(body_html)


# NO LINK IN A FRAME MAY RUN SCRIPT (#2029, the measured frame). The new inbox screens measure an email's frame, which
# needs `allow-same-origin`: the sender's document then shares the box's origin. Nothing in it runs (no
# `allow-scripts`), but a `javascript:` link opened in a window of its own leaves the sandbox behind, so every URL
# attribute that names a scheme able to run script is taken out here, before the frame is built. The test is on the
# value as a browser reads it: entities decoded, and tab, newline and every other control or space character
# removed, which is how `jav&#x61;script:` and `java<TAB>script:` would otherwise get through.
_URL_ATTR = re.compile(r"""(?is)(?P<lead>[\s"'/])(?P<name>href|src|action|formaction|xlink:href|background|poster|data|srcdoc)"""
                       r"""\s*=\s*(?P<val>"[^"]*"|'[^']*'|[^\s"'=<>`]+)""")
_TAG = re.compile(r"""<[a-zA-Z][^\s/>]*(?:[^>"']|"[^"]*"|'[^']*')*>""")
_SCRIPT_SCHEME = re.compile(r"(?i)^(javascript|vbscript|livescript|data|blob|filesystem):")


def _runs_script(value: str) -> bool:
    """Would a browser read this attribute's value as a URL that runs script (or carries a document of its own)?"""
    v = html.unescape(value.strip("\"'"))
    v = re.sub(r"[\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u3000\ufeff]", "", v)
    if v.lower().startswith("data:image/") and not v.lower().startswith("data:image/svg"):
        return False                                             # an inline picture, which the frame's CSP allows
    return bool(_SCRIPT_SCHEME.match(v))


def _defang(tag: str) -> str:
    def one(m: re.Match) -> str:
        if m.group("name").lower() == "srcdoc" or _runs_script(m.group("val")):
            return f'{m.group("lead")}data-ownbox-removed="{html.escape(m.group("name").lower(), quote=True)}"'
        return m.group(0)
    return _URL_ATTR.sub(one, tag)


def no_script_links(body_html: str) -> str:
    """The sender's HTML with every URL attribute that could run script taken out (see the note above)."""
    return _TAG.sub(lambda m: _defang(m.group(0)), str(body_html or ""))


# EVERY LINK IN AN EMAIL OPENS OUTSIDE THE INBOX (owner, 2026-10-08, of a customer-portal email whose button showed
# "billing.stripe.com refused to connect" in the reading pane: "we also didn't think about when people click links
# inside emails ... Possibly just open a new browser tab? But Mobile is different because it's the native browser
# integration pop-up"). A link with no target opened INSIDE the frame, and a site that refuses to be framed (a bank, a
# billing portal, a sign-in page) drew its refusal there. So every <a> and <area> opens a new window, which a computer
# shows as a new tab and an installed app on a phone as the phone's own browser sheet; the frame's sandbox allows exactly
# that and no more (popups, escaping the sandbox). A sender's own target (_self, _top, a name) is replaced, and the frame
# also carries <base target="_blank"> for anything this misses. rel="noopener noreferrer": the page that opens can't
# reach back and move the reading pane, and is not told which box it came from. A link to a place in the same email
# (href="#...", a newsletter's contents) stays in the frame, pointed at about:srcdoc#...: a frame drawn from srcdoc
# reads a bare "#help" against the PAGE's address, so it loaded the Inbox itself inside the frame (measured in Brave,
# 10-08), while about:srcdoc#help scrolls the email to it.
_LINK_TAG = re.compile(r"""(?is)<(?:a|area)\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_HREF = re.compile(r"""(?is)[\s"'/]href\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+)""")
_TARGET_REL = re.compile(r"""(?is)(?P<lead>[\s"'/])(?:target|rel)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+)""")


def links_open_outside(body_html: str) -> str:
    """The sender's HTML with every link set to open in a window of its own (see the note above)."""
    def one(m: re.Match) -> str:
        tag = m.group(0)
        end = "/>" if tag.endswith("/>") else ">"
        inner = _TARGET_REL.sub(lambda x: x.group("lead") if x.group("lead") in "\"'" else " ", tag[: -len(end)])
        href = _HREF.search(inner)
        frag = html.unescape(href.group(1).strip("\"'")).strip() if href else ""
        if frag.startswith("#"):
            inner = (inner[: href.start(1)] + '"' + html.escape("about:srcdoc" + frag, quote=True) + '"'
                     + inner[href.end(1):])
            return inner.rstrip() + ' target="_self"' + end
        return inner.rstrip() + ' target="_blank" rel="noopener noreferrer"' + end
    return _LINK_TAG.sub(one, str(body_html or ""))


# NO EMAIL MOVES ITS OWN FRAME (OSDev1, on #2029). A `<meta http-equiv="refresh">` in the sender's HTML would send the
# frame somewhere else after it had drawn: another site in the reading pane, or a page of this box now that the frame
# shares its origin. The tag is dropped whole, read the way a browser reads it (any case, entities decoded, spaces
# around the value ignored). The box's own CSP meta comes before the sender's HTML and is never touched.
_META = re.compile(r"""(?is)<meta\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_EQUIV = re.compile(r"""(?is)[\s"'/]http-equiv\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+)""")


def no_refresh(body_html: str) -> str:
    """The sender's HTML with every `<meta http-equiv="refresh">` taken out (see the note above)."""
    def one(m: re.Match) -> str:
        equiv = _EQUIV.search(m.group(0))
        value = html.unescape(equiv.group(1).strip("\"'")).strip().lower() if equiv else ""
        return "" if value == "refresh" else m.group(0)
    return _META.sub(one, str(body_html or ""))


# NO EMAIL REPORTS ITS OWN OPENING (owner, 2026-10-07, once pictures loaded by themselves: "yes strip the tracking
# pixels too"). A sender's invisible picture tells them the email was opened, and when. So a picture nobody can see is
# taken out before the frame draws: 2px or less both ways (by its width and height attributes, or by its style, which
# wins as it does in a browser), or hidden by its style (display:none, visibility:hidden, opacity:0, a max-width or
# max-height of 0). A picture a person can see stays: a logo, a photo, a 1px-tall divider across the email. A pixel
# with no size and no hiding can't be told from a picture, so it stays too.
_IMG_TAG = re.compile(r"""(?is)<img\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_IMG_ATTR = re.compile(r"""(?is)[\s"'/](?P<name>[a-z][a-z0-9-]*)\s*=\s*(?P<value>"[^"]*"|'[^']*'|[^\s"'=<>`]+)""")
_PIXELS = re.compile(r"(?i)^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$")
PIXEL_PX = 2


def _px(value) -> float | None:
    """A length in pixels, or None when it isn't one (auto, a percentage, nothing)."""
    m = _PIXELS.match(str(value or ""))
    return float(m.group(1)) if m else None


def _img_attrs(tag: str) -> dict:
    """An <img> tag's attributes, names in lower case, values unescaped; the first of a repeated name wins."""
    attrs = {}
    for m in _IMG_ATTR.finditer(tag):
        attrs.setdefault(m.group("name").lower(), html.unescape(m.group("value").strip("\"'")))
    return attrs


def _invisible(tag: str) -> bool:
    attrs = _img_attrs(tag)
    style = {}
    for decl in attrs.get("style", "").split(";"):
        name, _, value = decl.partition(":")
        if value:
            style.setdefault(name.strip().lower(), value.lower().replace("!important", "").strip())
    if style.get("display") == "none" or style.get("visibility") == "hidden":
        return True
    if _px(style.get("opacity")) == 0 or 0 in (_px(style.get("max-width")), _px(style.get("max-height"))):
        return True
    w = _px(style["width"]) if "width" in style else _px(attrs.get("width"))
    h = _px(style["height"]) if "height" in style else _px(attrs.get("height"))
    return w is not None and h is not None and w <= PIXEL_PX and h <= PIXEL_PX


def no_tracking_pixels(body_html: str) -> str:
    """The sender's HTML with every picture nobody can see taken out (see the note above)."""
    return _IMG_TAG.sub(lambda m: "" if _invisible(m.group(0)) else m.group(0), str(body_html or ""))


# NO PICTURE THAT CAN NEVER LOAD (OSDev1's walk of release .11 on the owner's real mail, 2026-10-07: personal emails drew
# blank boxes and broken icons). An <img> with no source, an inline attachment (cid:, which the box doesn't serve), an
# http: picture (the frame's policy loads https only) or a bare path (on no site from inside the frame) is taken out
# before the frame draws. Its alt text stays in its place when it is real words ("Colliers International"), never a
# file name, a link, or a word like "image" that only says a picture was there.
_LOADS = re.compile(r"(?i)^(?:https:|//|data:image/)")
_FILE_NAME = re.compile(r"(?i)\.(?:png|jpe?g|gif|bmp|webp|svg|tiff?|ico|heic)$")
_TWO_LETTERS = re.compile(r"[^\W\d_]{2}")
_NOT_WORDS = {"image", "img", "picture", "pic", "photo", "logo", "icon", "spacer", "pixel", "banner", "graphic",
              "signature", "attachment", "inline image", "untitled", "blank"}


def _alt_words(alt: str) -> str:
    """The alt text when it is real words, else ""."""
    a = " ".join(str(alt or "").split())
    if (not _TWO_LETTERS.search(a) or _FILE_NAME.search(a) or "://" in a or ":" in a.split(" ", 1)[0]
            or a.lower().rstrip(" 0123456789_-") in _NOT_WORDS):
        return ""
    return a


def no_dead_images(body_html: str) -> str:
    """The sender's HTML with every picture that can never load replaced by its alt text, or by nothing (see above)."""
    def one(m: re.Match) -> str:
        attrs = _img_attrs(m.group(0))
        if _LOADS.match(attrs.get("src", "").strip()):
            return m.group(0)
        return html.escape(_alt_words(attrs.get("alt", "")), quote=False)
    return _IMG_TAG.sub(one, str(body_html or ""))


def csp_meta() -> str:
    """The frame's CSP, exactly as frame_doc writes it."""
    return f'<meta http-equiv="Content-Security-Policy" content="{_FRAME_CSP}">'


def frame_doc(body_html: str) -> str:
    """The document `safe_frame` puts in its sandboxed frame's `srcdoc`: the CSP, our base styling, then the sender's
    HTML. For a screen that builds the frame itself (the new inbox screens set it as a frame's `srcDoc`, which the
    browser never parses into the page); the frame's `sandbox` and `referrerpolicy` must be `safe_frame`'s."""
    return ('<!doctype html><html><head><meta charset="utf-8">'
            + csp_meta() +
            # NO ADDRESS LEAVES WITH A PICTURE OR A LINK: the frame's own requests send no referrer.
            '<meta name="referrer" content="no-referrer">'
            # AND EVERY LINK OPENS OUTSIDE, even one `links_open_outside` could not read: the first <base> wins.
            '<base target="_blank">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            # OUR OWN BASE STYLING, FIRST, so a message that styles nothing still reads like
            # something a person wrote rather than a 1996 default-serif wall. Anything the sender
            # sets afterwards wins, which is the right way round — it is their document.
            '<style>html{-webkit-text-size-adjust:100%}'
            'body{margin:0;padding:10px 12px;font:16px/1.4 -apple-system,BlinkMacSystemFont,'
            '"Segoe UI",Roboto,sans-serif;color:#111;background:#fff;word-break:break-word}'
            'img{max-width:100%;height:auto}table{max-width:100%}'
            'a{color:#0b57d0}</style></head><body>'
            + links_open_outside(no_script_links(no_refresh(no_dead_images(no_tracking_pixels(body_html)))))
            + '</body></html>')


def safe_frame(body_html: str, *, label: str = "Message") -> str:
    """The sender's HTML in a sandboxed frame, or "" when there is nothing to frame.

    EVERY BYTE OF `body_html` IS A STRANGER'S. It is never concatenated into our page as markup:
    it goes into the `srcdoc` ATTRIBUTE, escaped with `quote=True`, so `"` becomes `&quot;` and
    cannot close the attribute — the one and only way a sender could break out of the frame into
    our document. That escape is the whole trust boundary at this layer and it runs first.
    """
    if not has_markup(body_html):
        return ""
    doc = frame_doc(body_html)
    return (f'<iframe class="mail" title="{html.escape(str(label), quote=True)}" '
            f'sandbox="{_FRAME_SANDBOX}" referrerpolicy="no-referrer" loading="lazy" '
            f'style="height:{_estimate_px(body_html)}px" '
            f'srcdoc="{html.escape(doc, quote=True)}"></iframe>')


# A HEADER AS A PERSON READS IT (owner, 2026-10-09, of a LinkedIn alert whose subject showed as
# "=?UTF-8?Q?=E2=80=9Cdirector_or_manager_or_sen?= =?UTF-8?Q?ior_AI...": "what?"). Mail carries any header with a
# character outside plain ASCII as RFC 2047 encoded-words, and the sweep keeps headers exactly as they came
# (email_channel._kept_headers) so the details panel can stand behind them. This turns encoded-words into the words
# they are (curly quotes, "…", accents, any charset Python knows), joins the folded lines, and leaves everything else
# as it was: a plain subject, and the <address> in a From, come back unchanged. A header that won't decode is shown as
# it came, never as an error.
def readable_header(value) -> str:
    raw = str(value or "")
    if "=?" in raw:
        try:
            raw = str(make_header(decode_header(raw)))
        except Exception:                                # noqa: BLE001 — a bad header is not fatal
            pass
    return " ".join(raw.split())
