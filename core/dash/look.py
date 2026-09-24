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
  `/ui/box.css`       the stylesheet, versioned by content hash so a new release is fetched once
                      and an unchanged one is never fetched twice.
  `/ui/font/<k>.woff2` exactly two keys, never a path.
  `/ui`               every component on one page, for whoever is checking a screen against it.

SERVED FROM THE BOX, NEVER A CDN — the same reason `marketing/customer_voice/fonts/README.md` gives:
a single-tenant box installed to a home screen must draw its own text without asking a third party,
and must not tell one every time it is opened.

A SCREEN ADOPTS IT with `head_tags()` in its `<head>`, then uses the tokens and components and
nothing of its own for colour, type size, font, radius or shadow.
"""
from __future__ import annotations

import hashlib
import html as _html
import pathlib

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
    first paint is already Inter rather than a fallback that swaps a frame later."""
    return ('<link rel="preload" href="/ui/font/sans.woff2" as="font" type="font/woff2" crossorigin>'
            f'<link rel="stylesheet" href="/ui/box.css?v={version()}">')


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
