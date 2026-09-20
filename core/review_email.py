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

from core import box_mail, report
from core.logging import get_logger

log = get_logger(__name__)

# The owner's reading order, 2026-09-09 ("lead machine, content machine, and then customer voice
# machine"), confirmed for this email 2026-09-10. core.report.ORDER is the registry's list and the
# page's; it is not his reading order, so it is not reused here.
ORDER = ("lead_machine", "content_machine", "customer_voice")
SENDER_NAME = "Morning Review"
# THE TRANSPORT CONSTANTS MOVED WITH THE TRANSPORT (core.box_mail): the Resend URL, the
# User-Agent that Cloudflare requires, and the retryable status set. They are not duplicated here,
# because two copies of a retryable-status set is one of them being wrong later.
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
