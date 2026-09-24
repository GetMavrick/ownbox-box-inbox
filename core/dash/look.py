"""The box's look: one stylesheet and two fonts, served by the Base Machine to every screen.

WHY THIS EXISTS. Owner, 2026-09-24: *"We need a lightweight design system that will be appealing to
every single client who gets one of these boxes"* — and no Tailwind, no build step. Until now the
box had six stylesheets that never shared a value: 91 colours, 26 font sizes, 15 weights, 21 radii
(counted, docs/SCOPE_DESIGN_LANGUAGE.md §1). The owner's order the same night, relayed by OSDev1 on
the wall: the box wears the ownbox.io design language, *"served on the box as ONE plain
stylesheet, no build step, so the investor sees one product going from site to box."* The values
are OSDev0's measurements of the live site (docs/BOX_DESIGN_REFERENCE.md).

WHAT IS HERE.
  `static/box.css`    the tokens, the plain-element base and the components, in three cascade
                      layers. Hand-written CSS; what is in the file is what the browser gets.
  `static/fonts/`     Inter (variable, weight + optical size, the site's face) and Geist Mono (for
                      what a person types or copies), latin subsets, OFL 1.1, licences beside them.
                      GEIST MONO IS THE OWNER'S PICK, 2026-09-24, from three rendered at 390px: *"I
                      was just going to tell you geist mono."* It replaces the system monospace
                      stack, which he did not like (*"I do not like the Mono font that we have
                      chosen there. Need a replacement"*).
  `static/icon.svg`   the Ownbox mark, the same file as sites/ownbox/app/icon.svg: an ink tile with a
                      cream square in it. It is the box's icon everywhere a browser or a home screen
                      shows one — see "THE MARK" below.
  `/ui/box.css`       the stylesheet, versioned by content hash so a new release is fetched once
                      and an unchanged one is never fetched twice.
  `/ui/font/<k>.woff2` exactly two keys, never a path.
  `/ui/icon.svg`, `/favicon.ico`, `/apple-touch-icon.png`   the mark, for a tab and a home screen.
  `/ui`               every component on one page, for whoever is checking a screen against it.

SERVED FROM THE BOX, NEVER A CDN — the same reason `marketing/customer_voice/fonts/README.md` gives:
a single-tenant box installed to a home screen must draw its own text without asking a third party,
and must not tell one every time it is opened.

A SCREEN ADOPTS IT with `head_tags()` in its `<head>`, then uses the tokens and components and
nothing of its own for colour, type size, font, radius or shadow.
"""
from __future__ import annotations

import functools
import hashlib
import html as _html
import pathlib
import struct
import zlib

from flask import Response, request

from core.dash import blueprint, require_session
from core.logging import get_logger

log = get_logger(__name__)

_DIR = pathlib.Path(__file__).resolve().parent / "static"
_CSS = _DIR / "box.css"

# THE NAME IS A KEY, NEVER A PATH: `name` arrives from the URL and is only looked up here, so
# `../` is not a traversal, it is a miss.
_FONTS = {"sans": "inter-latin-var.woff2", "mono": "geist-mono-latin-var.woff2"}


def _css_bytes() -> bytes:
    try:
        return _CSS.read_bytes()
    except OSError as e:                              # a missing file costs the look, never the page
        log.warning("look.css_unreadable", error=type(e).__name__)
        return b""


def version() -> str:
    """A short content hash of the stylesheet. Changes exactly when the file does."""
    return hashlib.sha256(_css_bytes()).hexdigest()[:12]


def head_tags() -> str:
    """What a screen puts in its <head> to wear the box's look. Preloads the text face, so the
    first paint is already Inter rather than a fallback that swaps a frame later.

    THE ICON RIDES HERE TOO, so every screen that wears the look also wears the mark: the tab, and
    the home screen when a page is added to one. The .ico is for browsers that do not read SVG
    icons; ownbox.io declares the same three links."""
    return ('<link rel="preload" href="/ui/font/sans.woff2" as="font" type="font/woff2" crossorigin>'
            f'<link rel="stylesheet" href="/ui/box.css?v={version()}">'
            '<link rel="icon" href="/favicon.ico" sizes="32x32">'
            '<link rel="icon" href="/ui/icon.svg" type="image/svg+xml">'
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png">')


@blueprint.get("/ui/box.css")
def ui_css():
    """The stylesheet. Public: the sign-in screen wears it before anyone has signed in.

    IMMUTABLE ONLY WHEN ASKED FOR BY HASH. `head_tags()` always asks with `?v=<hash>`, so a cached
    copy can never outlive its content; a bare request gets a short cache instead of a year.
    """
    body = _css_bytes()
    if not body:
        return ("", 404)
    asked = request.args.get("v", "")
    cache = ("public, max-age=31536000, immutable" if asked and asked == version()
             else "public, max-age=300")
    return Response(body, mimetype="text/css", headers={"Cache-Control": cache})


@blueprint.get("/ui/font/<name>.woff2")
def ui_font(name: str):
    """One of exactly two files, or a 404. Both OFL 1.1; the licences sit beside them."""
    fn = _FONTS.get(str(name or "").strip().lower())
    if not fn:
        return ("", 404)
    try:
        blob = (_DIR / "fonts" / fn).read_bytes()
    except OSError as e:
        log.warning("look.font_unreadable", font=fn, error=type(e).__name__)
        return ("", 404)
    # THIRTY DAYS, NOT IMMUTABLE: these URLs carry no hash, so a release that ships a new file
    # serves it at the same address and an installed app must be able to pick it up.
    return Response(blob, mimetype="font/woff2",
                    headers={"Cache-Control": "public, max-age=2592000"})


# ── THE MARK ────────────────────────────────────────────────────────────────────────────────────
#
# OWNER, 2026-09-24, looking at a home screen with the installed Unified Inbox next to the Ownbox
# icon: *"Right now it has just a blue O, but I want the actual."* The blue O was the inbox's own
# placeholder, a ring drawn in marketing/customer_voice/app.py and labelled a placeholder there
# from the day it shipped. The box now has one icon, and it is ownbox.io's.
#
# ONE MARK, THE SITE'S. static/icon.svg is sites/ownbox/app/icon.svg byte for byte: a 240-unit ink
# tile with corner radius 64, and a cream square 120 across at (60, 60) with corner radius 30. The
# constants below are that geometry as fractions of the side, and
# tests/test_the_box_wears_the_ownbox_mark.py reads the SVG to hold them to it.
#
# DRAWN, NOT SHIPPED AS PNG FILES, for the reason app.py gave for the ring: Pillow is not a
# dependency of this box, and a PNG is little enough format to write by hand. Drawing from the
# same numbers also means a size cannot drift from the SVG, because there is no second picture.
#
# TWO SHAPES OF THE SAME MARK.
#   A home screen (`tile=False`): the ink runs to the edge, because iOS and Android cut their own
#   corners. An icon that rounds its own gets them cut twice, and iOS paints anything transparent
#   black. The cream square is half the side and its corners are rounded, so no part of it is more
#   than 30% of the side from the centre: inside the 40% circle a maskable icon is never cropped to.
#   A tab (`tile=True`): the tile keeps its own rounded corners, transparent outside, as on the site.
_INK = (17, 17, 17)            # --ink, the tile
_GROUND = (246, 244, 239)      # --ground, the cream square
_TILE_R = 64 / 240             # the tile's corner radius, as a fraction of the side
_SQUARE = 120 / 240            # the cream square's side
_SQUARE_R = 30 / 240           # ...and its corner radius
_ICON_CACHE = "public, max-age=86400"


def _outside(px: float, py: float, half: float, r: float) -> float:
    """How far a point (measured from the centre) is outside a rounded square; negative inside."""
    qx, qy = abs(px) - half + r, abs(py) - half + r
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    return (ox * ox + oy * oy) ** 0.5 + min(max(qx, qy), 0.0) - r


def _cover(d: float) -> float:
    """How much of a pixel a shape covers, from the distance at the pixel's centre. This is the
    anti-aliasing: an edge pixel is part ink and part cream rather than a staircase."""
    return 0.0 if d >= 0.5 else 1.0 if d <= -0.5 else 0.5 - d


@functools.lru_cache(maxsize=8)
def mark_png(size: int, tile: bool = False) -> bytes:
    """The mark as a `size`-pixel square PNG. RGB for a home screen, RGBA with rounded corners for a tab.

    CACHED, because every route that serves it is public: a stranger fetching the 512 in a loop
    must cost a dictionary lookup, not a quarter of a second of this box's one CPU.
    """
    c = size / 2
    sq_half, sq_r, tile_r = size * _SQUARE / 2, size * _SQUARE_R, size * _TILE_R
    # The only rows and columns the cream square reaches. Everything else is ink, which is most of
    # a home-screen icon and is copied rather than computed.
    lo, hi = max(0, int(c - sq_half) - 1), min(size, int(c + sq_half) + 2)
    ink = bytes(_INK)
    raw = bytearray()
    for y in range(size):
        raw.append(0)                                  # filter type 0 (None) for each scanline
        if not tile and not lo <= y < hi:
            raw += ink * size
            continue
        py = y + 0.5 - c
        for x in range(size):
            px = x + 0.5 - c
            if lo <= x < hi and lo <= y < hi:
                k = _cover(_outside(px, py, sq_half, sq_r))
                raw += bytes(round(i + (g - i) * k) for i, g in zip(_INK, _GROUND))
            else:
                raw += ink
            if tile:
                raw.append(round(255 * _cover(_outside(px, py, c, tile_r))))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6 if tile else 2, 0, 0, 0)   # 8-bit RGBA / RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


@functools.lru_cache(maxsize=1)
def favicon_ico() -> bytes:
    """16, 32 and 48 pixels in one .ico, each stored as a PNG, which every current browser reads."""
    images = [(s, mark_png(s, tile=True)) for s in (16, 32, 48)]
    head = struct.pack("<HHH", 0, 1, len(images))                 # reserved, type 1 = icon, count
    offset = len(head) + 16 * len(images)
    entries, blobs = b"", b""
    for s, png in images:
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return head + entries + blobs


# ALL THREE ARE PUBLIC. A browser asks for a tab icon on the sign-in page, before anyone has signed
# in, and a home screen fetches its icon on its own terms. None of them carries anything but the
# mark. The two root addresses are also where a browser looks on its own when a page names
# no icon, which covers the screens not yet on head_tags().
@blueprint.get("/favicon.ico")
def favicon():
    return Response(favicon_ico(), mimetype="image/x-icon", headers={"Cache-Control": _ICON_CACHE})


@blueprint.get("/apple-touch-icon.png")
@blueprint.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    """180 pixels, the size an iPhone asks for."""
    return Response(mark_png(180), mimetype="image/png", headers={"Cache-Control": _ICON_CACHE})


@blueprint.get("/ui/icon.svg")
def ui_icon():
    try:
        blob = (_DIR / "icon.svg").read_bytes()
    except OSError as e:                              # the .ico beside it still answers
        log.warning("look.icon_unreadable", error=type(e).__name__)
        return ("", 404)
    return Response(blob, mimetype="image/svg+xml", headers={"Cache-Control": _ICON_CACHE})


_CHEV = ('<svg class="ui-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>')


def _specimen() -> str:
    row = (lambda label, value, sub="": f'<a class="ui-row" href="#"><span class="ui-row-label">{label}'
           + (f"<small>{sub}</small>" if sub else "") + f'</span><span class="ui-row-value">{value}</span>'
           + _CHEV + "</a>")
    return f"""
<div class="ui-page">
  <div class="ui-head"><div class="ui-eyebrow">Box design language</div>
    <h1>Every building block</h1>
    <p class="ui-lede">What each screen on this box is made of. Colours, type and shape come only from here.</p></div>

  <section class="ui-card"><h2>Status</h2>
    <p class="ui-status"><span class="ui-dot ok"></span><b>Connected.</b> Mail arrives every minute.</p>
    <p class="ui-status"><span class="ui-dot warn"></span><b>Waiting.</b> The first check runs within a minute.</p>
    <p class="ui-status"><span class="ui-dot bad"></span><b>Needs you.</b> The password was refused.</p>
    <p><span class="ui-chip"><span class="ui-dot ok"></span>Running</span>
       <span class="ui-chip"><span class="ui-dot new"></span>New</span> <span class="ui-chip">Owner only</span></p></section>

  <section class="ui-card"><h2>Rows</h2><p>The whole row is the tap target.</p>
    <div class="ui-rows">{row("Your AI account", "Set up")}{row("Your mobile app", "Install")}
      {row("Your inbox", "Connected", "you@yourcompany.com")}</div></section>

  <section class="ui-card"><h2>A form</h2>
    <div class="ui-field"><label for="s-email">Email address<input id="s-email" type="email" placeholder="you@yourcompany.com"></label>
      <p class="ui-hint">The address customers write to.</p></div>
    <div class="ui-field"><label for="s-pw">Password<input id="s-pw" type="password" value="short"></label>
      <p class="ui-error">Use at least 12 characters. What you typed is still here.</p></div>
    <label class="ui-check"><input type="checkbox"> Keep me signed in on this device</label>
    <div class="ui-actions"><button type="button">Save</button>
      <button type="button" class="ui-ghost">Cancel</button></div></section>

  <section class="ui-card"><h2>Steps and things to copy</h2>
    <ol class="ui-steps"><li>Open a terminal on your own computer.</li>
      <li>Paste this and press Enter.</li><li>Copy what it prints into the box below.</li></ol>
    <div class="ui-copy">cat ~/.ssh/id_ed25519.pub</div>
    <p>Build it in <code>my/machines/</code> with a <code>machine.yaml</code>. <a href="#">Read the guide</a></p></section>

  <div class="ui-notice">One thing first: this is the only notice on a screen, and it sits at the top.</div>

  <section class="ui-card"><h2>Type</h2>
    <p class="ui-eyebrow">Eyebrow</p><h1>Page title</h1><p class="ui-muted">Body text, 17px, for reading.</p>
    <p class="ui-small">Small print, 13px, for what is true but secondary.</p>
    <p class="ui-empty">Nothing here yet. The first report appears within fifteen minutes.</p>
    <div class="ui-actions"><a class="ui-btn ui-compact ui-ghost" href="#">Small action</a>
      <button type="button" class="ui-danger">Remove</button></div></section>
</div>"""


@blueprint.get("/ui")
def ui_specimen():
    """Every component on one page. Signed-in only: it is a reference for the people running the
    box, not a page a stranger should find."""
    gate = require_session()
    if gate is not None:
        return gate
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{_html.escape("Box design language")}</title>{head_tags()}</head>'
            f"<body>{_specimen()}</body></html>")
