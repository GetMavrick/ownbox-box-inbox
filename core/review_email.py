"""The Morning Review as an email: the day's quote, then what's worth your time, what's moving, and ideas.

Rebuilt 2026-10-01 (docs/SCOPE_MORNING_REVIEW_V2.md, owner: "Ok go"). His words: "Light and optimistic. That's
what we want." and "a short motivational quote for every day of the year ... That should be the headline", and
"please get rid of those nasty black buttons". So the mail draws the stored brief (core/review_brief.py), the same
words the app page shows, on a light page with quiet links and no zeros. A morning with nothing to say sends
nothing.

DELIVERY is unchanged: the box's own transport (core.box_mail), an Idempotency-Key per day and recipient, and no
unsubscribe footer, tracking or marketing copy, because it is a message from a box to its own owner.
"""
from __future__ import annotations

import html as _html

from core import box_mail, report
from core.logging import get_logger

log = get_logger(__name__)

SENDER_NAME = "Morning Review"

# The mockup's palette (docs/mockups/morning-review-v2.html), inline because mail clients drop <style>.
_INK, _SOFT, _GREY, _HAIR, _BG, _WASH = "#2e2c27", "#6b6a63", "#b4b3a8", "#e4e3dc", "#fcfcfb", "#f9f9f7"
_SERIF = "'Iowan Old Style','Palatino Linotype',Georgia,serif"
_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
SECTIONS = (("worth", "Worth your time today"), ("moving", "Already moving"), ("ideas", "Ideas to try"))


def build(day, now=None) -> dict:
    """The email as data: the stored brief for `day` (built the first time), with a subject. `skip` is True on a
    morning with nothing to say, and then nothing is sent."""
    from core import review_brief
    b = review_brief.ensure(day, now)
    return {**b, "subject": f"Your Morning Review: {b['quote']}", "skip": bool(b.get("empty"))}


def text(e: dict) -> str:
    lines = [e["date_label"], "", e["quote"], ""]
    if e.get("good_news"):
        lines += [e["good_news"], ""]
    for key, heading in SECTIONS:
        items = e.get(key) or []
        if not items:
            continue
        lines.append(heading.upper())
        for n, it in enumerate(items, 1):
            lines.append(f"  {n:02d}  {it['title']}")
            if it.get("why"):
                lines.append(f"      {it['why']}")
            if it.get("machine"):
                lines.append(f"      {it['machine']}")
        lines.append("")
    lines.append(f"The full review: {e['link']}")
    return "\n".join(lines)


def _href(h: str, base: str) -> str:
    if not h:
        return ""
    return h if h.startswith("http") else base.rstrip("/") + "/" + h.lstrip("/")


def html(e: dict) -> str:
    esc = _html.escape
    base = e["link"].split("/app/review")[0]
    out = [f'<div style="background:{_BG};color:{_INK};font-family:{_SANS};font-size:16px;line-height:1.6">'
           f'<div style="background:{_WASH};border-bottom:1px solid {_HAIR};padding:28px 20px 24px">'
           f'<div style="max-width:600px;margin:0 auto">'
           f'<div style="font-size:13px;letter-spacing:.05em;color:{_SOFT}">{esc(e["date_label"])}</div>'
           f'<div style="font-family:{_SERIF};font-size:27px;line-height:1.25;margin:12px 0 0">{esc(e["quote"])}</div>'
           f'<div style="border-top:1px solid {_SOFT};margin:22px 0 0;width:100%"></div>']
    if e.get("good_news"):
        out.append(f'<p style="color:{_SOFT};margin:14px 0 0">{esc(e["good_news"])}</p>')
    out.append('</div></div><div style="max-width:600px;margin:0 auto;padding:8px 20px 40px">')
    for key, heading in SECTIONS:
        items = e.get(key) or []
        if not items:
            continue
        out.append(f'<p style="margin:30px 0 4px;font-size:12px;font-weight:600;letter-spacing:.14em;'
                   f'text-transform:uppercase;color:{_SOFT}">{esc(heading)}</p>'
                   '<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse">')
        for n, it in enumerate(items, 1):
            title = esc(it["title"])
            href = _href(it.get("href") or "", base)
            if href:
                title = (f'<a href="{esc(href, quote=True)}" style="color:{_INK};text-decoration:none;'
                         f'border-bottom:1px solid {_HAIR}">{title}</a>')
            why = f'<div style="color:{_SOFT};margin-top:4px">{esc(it["why"])}</div>' if it.get("why") else ""
            who = (f'<div style="color:{_GREY};font-size:13px;margin-top:2px">{esc(it["machine"])}</div>'
                   if it.get("machine") else "")
            out.append(f'<tr><td style="width:30px;vertical-align:top;padding:14px 0;color:{_GREY};font-size:13px">'
                       f'{n:02d}</td><td style="padding:14px 0"><div style="font-weight:600">{title}</div>{why}{who}'
                       '</td></tr>')
        out.append("</table>")
    if e.get("ideas_from") == "ai":
        out.append(f'<p style="color:{_GREY};font-size:13px;margin:28px 0 0">The ideas come from your box\'s AI, '
                   'based only on yesterday\'s numbers.</p>')
    out.append(f'<p style="margin:24px 0 0"><a href="{esc(e["link"], quote=True)}" style="color:{_INK}">'
               'Open the full review</a></p></div></div>')
    return "".join(out)


def from_address() -> str:
    """Kept as this module's own name because callers and tests use it; the answer comes from
    `box_mail`, so the review email and a box notification can never disagree about who the box
    is."""
    return box_mail.from_address()


def send(to: str, subject: str, text_body: str, html_body: str, *, idem_key: str) -> str:
    """One email through Resend. -> the Resend message id. Raises; the caller decides what a failure
    costs. Metered before the call, recorded after it, exactly once per message id.

    THE TRANSPORT MOVED TO `core.box_mail`, unchanged, when the inbox needed to tell the owner his
    box had messages waiting. Two senders, one door: the alternative was a second copy of the
    Resend call, the meter and the ledger note, and a second place to fix them.

    THIS FUNCTION'S CONTRACT IS UNCHANGED — same arguments, same return, same exceptions, same
    `morning_review` ledger note — and this module's suite is what proves the move was faithful.
    """
    return box_mail.send(to, subject, text_body, html_body, idem_key=idem_key,
                         sender_name=SENDER_NAME, note="morning_review")
