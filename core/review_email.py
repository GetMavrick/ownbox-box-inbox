"""The Morning Review as an email: one section per machine, in the owner's order.

Owner, 2026-09-10: "Wish it was an email with the 3 sections." Asked which three, he chose the
machines, in the order he set for the products page the same night (Lead, Content, Customer
Voice), sent to his own address, with the Slack message kept as well. The Slack message orders
by URGENCY on purpose (core/report.render: Needs you, Yesterday, Watch, Meters); this orders by
MACHINE on purpose. Neither is a bug in the other, and nobody should "fix" one to match.

EACH SECTION carries that machine's headline for yesterday, what needs him today, what happened
yesterday, and anything on its watch list that is not fine. After the three: one meters line,
because a vendor at its cap is the thing he most needs at breakfast and meters is not a machine,
then the link to the review page. The SUBJECT carries how many things need him, so the urgency
the machine ordering gives up is still the first thing he reads.

DELIVERY is Resend on the box's verified sending domain, through core.net's public POST (no
redirects, a capped response), metered against the `resend` cap. The caller passes an
Idempotency-Key per day and recipient, so a tick that retries after a timeout cannot send the
same morning twice. It is a message from the box to its own owner: no unsubscribe footer, no
tracking, no marketing copy.

THE ADDRESS IS PER BOX. Shipped config leaves `review.email_to` empty, which means no email;
the owner's address lives in my/settings.yaml, never in source.
"""
from __future__ import annotations

import html as _html
import json as _json

from core import cost_guard, net, report, state
from core.config import settings
from core.exceptions import RetryableError, VendorError
from core.logging import get_logger

log = get_logger(__name__)

# The owner's reading order, 2026-09-09 ("lead machine, content machine, and then customer voice
# machine"), confirmed for this email 2026-09-10. core.report.ORDER is the registry's list and the
# page's; it is not his reading order, so it is not reused here.
ORDER = ("lead_machine", "content_machine", "customer_voice")
SENDER_NAME = "Morning Review"
_URL = "https://api.resend.com/emails"
# RESEND SITS BEHIND CLOUDFLARE, and Cloudflare refuses urllib's default signature. Measured on the
# box 2026-09-10: core.net.post_public with no User-Agent got 403 "error code: 1010"; the identical
# call with one got Resend's own 401. core.net sets none by design (each caller names itself, as
# site_email does), so this caller must, or no morning email leaves the box at all.
_UA = "AIOS-MorningReview/1.0"
_TRANSIENT = {408, 429, 500, 502, 503, 504, 529}
_RANK = {"fail": 0, "warn": 1, "connect": 2}
_MARK = {"fail": "!!", "warn": "!", "connect": "→"}


def _fmt(v) -> str:
    return report._fmt_value(v)


def _headline(row: dict) -> str:
    h = row.get("headline") or {}
    delta = h.get("delta")
    dtxt = (f" ({'+' if delta > 0 else ''}{_fmt(delta)} vs the day before)"
            if isinstance(delta, (int, float)) and not isinstance(delta, bool) and delta else "")
    return f"{_fmt(h.get('value', 0))} {h.get('label', '')}{dtxt}".strip()


def _watch(row: dict | None) -> list:
    items = [(w.get("state"), w.get("text", "")) for w in (row or {}).get("watch") or []
             if w.get("state") != "ok" and w.get("text")]
    return sorted(items, key=lambda sw: _RANK.get(sw[0], 3))


def build(day, now=None) -> dict:
    """The email as data. `day` is the day it is ABOUT (yesterday, closed); needs and watch come
    from today's row, which report.run refreshes right before sending, the split render() uses."""
    now = now or report.now_local()
    yday = {r.get("machine"): r for r in report.read(day)}
    tday = {r.get("machine"): r for r in report.read(report.today(now))}
    machines, needs_total = [], 0
    for m in ORDER:
        y, t = yday.get(m), tday.get(m)
        if not (y or t):
            continue                          # a machine this box does not run gets no empty section
        needs = [n.get("text", "") for n in (t or {}).get("needs_you") or [] if n.get("text")]
        needs_total += len(needs)
        machines.append({
            "machine": m, "title": (y or t).get("title") or m, "needs": needs, "watch": _watch(t),
            "error": (y or {}).get("error"),
            "headline": _headline(y) if y and not y.get("error") else "",
            "happened": [(x.get("text", ""), x.get("value")) for x in (y or {}).get("happened") or []],
        })
    mrow = tday.get(report.METERS)
    meters = ({"headline": _headline(mrow), "watch": _watch(mrow)}
              if mrow and not mrow.get("error") else None)
    when = f"{now:%a %d %b}"
    if needs_total:
        subject = (f"Morning review, {when}: {needs_total} thing{'s' if needs_total != 1 else ''} "
                   f"need{'s' if needs_total == 1 else ''} you")
    else:
        subject = f"Morning review, {when}: nothing needs you"
    return {"subject": subject, "needs": needs_total, "machines": machines, "meters": meters,
            "link": report.page_url(day)}


def text(e: dict) -> str:
    lines = [e["subject"], ""]
    for s in e["machines"]:
        lines.append(s["title"].upper())
        if s["error"]:
            lines.append(f"  Could not report yesterday: {s['error']}")
        elif s["headline"]:
            lines.append(f"  Yesterday: {s['headline']}")
        if s["needs"]:
            lines.append("  Needs you")
            lines += [f"    • {n}" for n in s["needs"]]
        if s["happened"]:
            lines.append("  What happened")
            lines += [f"    · {t}" + (f": {_fmt(v)}" if v not in (None, "") else "") for t, v in s["happened"]]
        if s["watch"]:
            lines.append("  Watch")
            lines += [f"    {_MARK.get(st, '·')} {w}" for st, w in s["watch"]]
        lines.append("")
    if not e["machines"]:
        lines += ["No machine reported yesterday. The first full day lands tomorrow.", ""]
    if e["meters"]:
        lines.append(f"Meters: {e['meters']['headline']}")
        lines += [f"  {_MARK.get(st, '·')} {w}" for st, w in e["meters"]["watch"]]
        lines.append("")
    lines.append(f"The full review: {e['link']}")
    return "\n".join(lines)


def _list(heading: str, items: list, colour: str = "#1d2330") -> str:
    li = "".join(f'<li style="margin:2px 0">{i}</li>' for i in items)
    return (f'<p style="margin:10px 0 2px;font-size:12px;letter-spacing:.04em;text-transform:uppercase;'
            f'color:{colour}">{_html.escape(heading)}</p><ul style="margin:0;padding-left:20px">{li}</ul>')


def html(e: dict) -> str:
    esc = _html.escape
    out = ['<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1d2330;'
           'max-width:620px;font-size:15px;line-height:1.45">',
           f'<p style="margin:0 0 16px;color:#5b6475;font-size:13px">{esc(e["subject"])}</p>']
    for s in e["machines"]:
        out.append(f'<h2 style="font-size:18px;margin:24px 0 6px;padding-bottom:4px;'
                   f'border-bottom:1px solid #e3e6ec">{esc(s["title"])}</h2>')
        if s["error"]:
            out.append(f'<p style="margin:4px 0;color:#9b2c2c">Could not report yesterday: {esc(str(s["error"]))}</p>')
        elif s["headline"]:
            out.append(f'<p style="margin:4px 0"><strong>Yesterday:</strong> {esc(s["headline"])}</p>')
        if s["needs"]:
            out.append(_list("Needs you", [esc(n) for n in s["needs"]], "#9b2c2c"))
        if s["happened"]:
            out.append(_list("What happened", [esc(t) + (f": <strong>{esc(_fmt(v))}</strong>" if v not in (None, "") else "")
                                               for t, v in s["happened"]]))
        if s["watch"]:
            out.append(_list("Watch", [f"{esc(_MARK.get(st, '·'))} {esc(w)}" for st, w in s["watch"]], "#8a5a00"))
    if not e["machines"]:
        out.append("<p>No machine reported yesterday. The first full day lands tomorrow.</p>")
    if e["meters"]:
        out.append(f'<p style="margin:24px 0 2px"><strong>Meters:</strong> {esc(e["meters"]["headline"])}</p>')
        if e["meters"]["watch"]:
            out.append(_list("Vendors", [f"{esc(_MARK.get(st, '·'))} {esc(w)}" for st, w in e["meters"]["watch"]], "#8a5a00"))
    out.append(f'<p style="margin:24px 0 0"><a href="{esc(e["link"], quote=True)}">Open the full review</a></p></div>')
    return "".join(out)


def from_address() -> str:
    from core.config import get_config          # per call: config bound at import defeats a patch
    cfg = dict(get_config().get("review") or {})
    return str(cfg.get("email_from") or getattr(settings, "gtm_from_email", "") or "").strip()


def send(to: str, subject: str, text_body: str, html_body: str, *, idem_key: str) -> str:
    """One email through Resend. -> the Resend message id. Raises; the caller decides what a failure
    costs. Metered before the call, recorded after it, exactly once per message id."""
    key = (getattr(settings, "resend_api_key", "") or "").strip()
    if not key:
        raise VendorError("resend", "config", "RESEND_API_KEY is not set")
    sender = from_address()
    if not sender:
        raise VendorError("resend", "config", "no from-address: set review.email_from or GTM_FROM_EMAIL")
    to = (to or "").strip()
    if "@" not in to:
        raise VendorError("resend", "config", "review.email_to is not an address")
    cost_guard.check_vendor("resend", 1)
    payload = {"from": f"{SENDER_NAME} <{sender}>", "to": [to], "subject": subject,
               "text": text_body, "html": html_body}
    headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": idem_key, "User-Agent": _UA}
    try:
        status, body = net.post_public(_URL, json=payload, headers=headers, timeout=30)
    except net.PostRefused as e:
        raise RetryableError(f"resend transport: {str(e)[:160]}") from e
    if status in _TRANSIENT:
        raise RetryableError(f"resend HTTP {status}: {body[:160]}")
    if not 200 <= status < 300:
        raise VendorError("resend", status, body[:200])
    try:
        ref = (_json.loads(body) or {}).get("id")
    except ValueError as e:
        raise VendorError("resend", status, f"non-JSON: {e}") from e
    if not ref:
        raise VendorError("resend", "shape", f"no message id: {body[:160]}")
    state.record_vendor_usage("resend", 1, idem_key=f"resend:{ref}", note="morning_review")
    return ref
