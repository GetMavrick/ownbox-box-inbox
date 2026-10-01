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
    icons; ownbox.io declares the same three links.

    THE TAB WEARS THE CLIENT'S ICON once there is one (owner, 2026-09-30: the icon should be
    "pulling through to the icon throughout the app"). It is their box, open in their browser."""
    from core import client_icon as _ci
    got = _ci.current()
    tab = (f'<link rel="icon" href="/ui/client-icon.png?v={got["sha"]}" type="image/png">' if got else
           '<link rel="icon" href="/favicon.ico" sizes="32x32">'
           '<link rel="icon" href="/ui/icon.svg" type="image/svg+xml">')
    return ('<link rel="preload" href="/ui/font/sans.woff2" as="font" type="font/woff2" crossorigin>'
            f'<link rel="stylesheet" href="/ui/box.css?v={version()}">'
            + tab +
            f'<link rel="apple-touch-icon" href="/apple-touch-icon.png{icon_version()}">')


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
_GROUND_HEX = "#%02x%02x%02x" % _GROUND   # the same cream, for a manifest's colour fields
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
    """180 pixels, the size an iPhone asks for: the client's icon once uploaded, else the mark."""
    return Response(app_png(180), mimetype="image/png", headers={"Cache-Control": _icon_cache()})


# THE CLIENT'S ICON OR THE BOX'S MARK, ONE ANSWER FOR EVERY HOME SCREEN (owner, 2026-09-29: the upload
# appears "everywhere"). Every icon route on the box asks this — core's, and a machine's own, since the
# Unified Inbox draws its icons through it too — so no machine has to know an upload exists.
def app_png(size: int) -> bytes:
    from core import client_icon
    return client_icon.tile_png(size) or mark_png(size)


def icon_version() -> str:
    """"?v=<sha>" once a client icon is uploaded, else "": a new icon is a new address, so a phone that
    cached the old one fetches the new one rather than keeping it for a day."""
    from core import client_icon
    got = client_icon.current()
    return f"?v={got['sha']}" if got else ""


def _icon_cache() -> str:
    """Immutable when asked for by the current version, a day otherwise — the mark's old promise."""
    from flask import request as _rq
    v = icon_version()
    return ("public, max-age=31536000, immutable" if v and _rq.args.get("v") == v[3:]
            else "public, max-age=3600")


@blueprint.get("/ui/client-icon.png")
def ui_client_icon():
    """The uploaded icon itself, margins transparent, for a header to draw on its white disc. Public,
    like every icon: a sign-in screen and a home screen fetch it without a session."""
    from core import client_icon
    png = client_icon.master_png()
    if png is None:
        return ("", 404)
    return Response(png, mimetype="image/png", headers={"Cache-Control": _icon_cache()})


# THE BASE MACHINE IS AN APP OF ITS OWN. Owner, 2026-09-24, with a screenshot of two home-screen
# icons side by side, Ownbox and Unified Inbox: "put this on the Mobile app screen showing the PWA add
# to homescreen for the base machine and add on machines". Until now only the inbox declared a
# manifest, so adding the Base Machine offered the page title as its name and opened it in the
# browser. This is its manifest: the product's name on the icon, standalone like the inbox, and the
# same mark the tab and touch icon already use.
APP_NAME = "Ownbox"          # the product's own name: what an unsold box, with no buyer yet, is called


def base_name() -> str:
    """The Base Machine's name on a home screen: THE CLIENT'S (owner, 2026-09-29, "The client's
    name"), since it is their box and the header already says Base Machine once it is open. On an
    unsold box, with no buyer, `dash.brand()` answers with the product's own name."""
    try:
        from core.dash import brand
        return str(brand() or APP_NAME)
    except Exception:                   # noqa: BLE001 — a name, never a 500
        return APP_NAME
_APP_SIZES = (192, 512)


@blueprint.get("/ui/manifest.webmanifest")
def ui_manifest():
    """Public, as every manifest must be: a home screen fetches it without a session."""
    import json
    # `id` IS WHO THE APP IS, AND IT IS WHAT IT ALWAYS WAS: an app with no id is identified by its
    # start_url, which is "/". Saying so now means a later start_url change cannot orphan installs.
    m = {"id": "/", "name": base_name(), "short_name": base_name(), "start_url": "/", "scope": "/",
         "display": "standalone", "background_color": _GROUND_HEX, "theme_color": _GROUND_HEX,
         "icons": manifest_icons("/ui/icon-{n}.png")}
    return Response(json.dumps(m), mimetype="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})


def manifest_icons(pattern: str) -> list[dict]:
    """The two sizes every manifest names, at the app's own addresses, versioned by the client's icon.
    `any maskable`: the mark keeps its cream square inside the safe zone, and a client's icon is drawn
    into it (core/dash/client_icon.py), so one picture is right for both purposes."""
    v = icon_version()
    return [{"src": pattern.format(n=n) + v, "sizes": f"{n}x{n}", "type": "image/png",
             "purpose": "any maskable"} for n in _APP_SIZES]


@blueprint.get("/ui/icon-<int:size>.png")
def ui_icon_png(size: int):
    """Exactly the two sizes the manifest names, and a 404 for any other: a size is a key."""
    if size not in _APP_SIZES:
        return ("", 404)
    return Response(app_png(size), mimetype="image/png", headers={"Cache-Control": _icon_cache()})


# ONE HOME-SCREEN APP PER MACHINE, AND EVERY SCREEN OF THE MACHINE INSTALLS AS IT. Owner, 2026-09-29:
# "We want every screen of the unified inbox to bookmark the same way" and "All the screens on AEO
# machine need to reflect AEO machine." A machine that draws its screens through core's chrome()
# registers its name and manifest here once; chrome() finds the machine from the menu the page sits
# in (core.shell), so a core screen filed in a machine's menu wears that machine too, and core never
# names a machine. A machine that draws its own page (the Unified Inbox) carries its own tags.
_APPS: dict[str, dict] = {}


def register_app(machine: str, *, name: str, manifest: str) -> None:
    """`machine` is the shell section's `machine`; `name` is what the home screen and the tab say."""
    _APPS[machine] = {"name": name, "manifest": manifest}


def app_for(machine: str | None) -> dict | None:
    """The registered app for a machine, or None: its screens are the Base Machine's."""
    return _APPS.get(machine or "")


def client_icon(got: dict | None | bool = False) -> str:
    """Where the header's icon comes from: the CLIENT'S, once they have uploaded one, and until then
    the box's own mark. One function, so the upload (docs/SCOPE_MOBILE_APP_REDESIGN.md, the install
    phase) changes one line and every header follows. Owner, 2026-09-27: "we are going to keep the
    name of the client and give them the ability to upload their icon". `got` is `current()` when
    the caller has already asked, so a page asks the database once per mark, not twice."""
    if got is False:
        from core import client_icon as _ci
        got = _ci.current()
    return f"/ui/client-icon.png?v={got['sha']}" if got else "/ui/icon.svg"


def header_mark(shape: str = "") -> str:
    """The client's icon, for a header, the top of the menu, or a preview of either.

    AN ICON WITH ITS OWN GROUND FILLS ITS SHAPE, edge to edge (owner, 2026-09-30: "the icon should
    fill the whole space instead of shrinking down and having White"); a bare logo, and the box's
    own mark, sit on the white disc, which is what lets a dark logo read on the dark ground.
    `core.client_icon.fills` decides which an upload is. `shape` is an extra class: "av" for the
    rounded square at the top of the menu, where the letter tile used to be."""
    from core import client_icon as _ci
    got = _ci.current()
    cls = " ".join(c for c in ("ui-disc appmark", "fill" if got and got.get("fills") else "", shape) if c)
    return f'<span class="{cls}"><img src="{client_icon(got)}" alt=""></span>'


# THE MENU'S THREE LINES, small enough to sit on the icon's corner.
_MENU_BADGE = ('<span class="menubadge" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
               'stroke="currentColor" stroke-width="3" stroke-linecap="round">'
               '<path d="M5 7h14M5 12h14M5 17h14"/></svg></span>')


def menu_button() -> str:
    """THE CLIENT'S ICON IS THE MENU BUTTON, with a small three-line badge on its corner.

    Owner, 2026-09-29, choosing option B of three mocked side by side ("go with B, build it"): the
    icon the client uploaded opens the menu, the way tapping your own picture top-left opens the
    menu on X and LinkedIn, so it reads as "my box". The badge still says "menu" to someone new, and
    the separate three-line button went: one control where there were two.

    STILL THE SAME CONTROL. A <label> for the drawer's checkbox, named "Menu" for a screen reader, so
    the drawer, its scrim and its keyboard focus work exactly as they did. Every shell draws it
    from here, so the Base Machine's screens and every machine's cannot drift apart.
    """
    return ('<label class="ham" for="navtoggle" role="button" aria-label="Menu" '
            f'aria-controls="railnav">{header_mark()}{_MENU_BADGE}</label>')


def app_tags(app: dict | None = None) -> str:
    """What makes a screen installable, and under which name. Without `app`, the Base Machine's."""
    href, name = (app["manifest"], app["name"]) if app else ("/ui/manifest.webmanifest", base_name())
    # CAPABLE IS WHAT AN OLDER iPHONE READS before it opens a home-screen icon full screen; a newer one
    # reads the manifest's `display`. One line, and the failure it prevents is silent.
    return (f'<link rel="manifest" href="{_html.escape(href)}">'
            f'<meta name="apple-mobile-web-app-title" content="{_html.escape(name)}">'
            '<meta name="apple-mobile-web-app-capable" content="yes">')


def manifest_for(app: dict, *, start_url: str, scope: str) -> dict:
    """A machine's manifest: its own name, its own address space, the box's mark and colours. The
    mark is drawn to the edge with the cream square inside the safe zone, so it is `maskable` too."""
    return {"id": start_url, "name": app["name"], "short_name": app["name"], "start_url": start_url,
            "scope": scope, "display": "standalone", "background_color": _GROUND_HEX,
            "theme_color": _GROUND_HEX, "icons": manifest_icons("/ui/icon-{n}.png")}


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

  <section class="ui-card"><h2>Added in the redesign</h2>
    <p class="ui-eyebrow">A choice among a few: the chosen one is raised, never inked</p>
    <div class="ui-seg"><button type="button" aria-pressed="true">Light</button>
      <button type="button" aria-pressed="false">Dark</button>
      <button type="button" aria-pressed="false">Automatic</button></div>
    <p class="ui-eyebrow">Figures: a number over the words that say what it counts</p>
    <div class="ui-metrics"><div class="ui-metric"><b>4</b><span>drafts ready to send</span></div>
      <div class="ui-metric"><b>21m</b><span>oldest waiting</span></div></div>
    <p class="ui-eyebrow">A mark that is not ours, on its white disc in both themes</p>
    <p><span class="ui-disc"><img src="{client_icon()}" alt=""></span></p>
    <p class="ui-eyebrow">Status: green is good, amber is caution, red is stop, and always with a word</p>
    <p><span class="ui-chip"><span class="ui-dot ok"></span>Running</span>
      <span class="ui-chip"><span class="ui-dot warn"></span>Worth watching</span>
      <span class="ui-chip"><span class="ui-dot bad"></span>Needs you</span></p></section>
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
