"""Settings → Data Sources → Outreach: the one place to connect Instantly (docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md §5 step 3;
OSDev1's A3, the key on Data Sources, easy to undo).

Read only: the box reads campaigns and their numbers, and never sends from Instantly. The card shows the connection,
the campaigns, the last pull and the week's numbers by campaign, with Pull now and the key form. Registered with
core/source_cards.py when the foundation is imported, as the Website analytics card is (card.py), and drawn the same
way: everything on the card, every refusal names its fix, every button a ghost.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import source_cards

from . import instantly, jobs, outreach_sync, seam, settings
from .card import _CSS as _WA_CSS        # the foundation's card styles: each card is on its own page now

KEY = "outreach"
# SENDING FIRST, DRAFTS LAST: what the owner is asking of this row is what is out there now.
_ORDER = ("active", "running follow-ups", "sending accounts unhealthy", "paused for bounces", "account suspended",
          "paused", "completed", "draft")
_SAY = (("sent", "sent"), ("opened", "opened"), ("clicked", "clicked"), ("replied", "replied"),
        ("booked", "meeting{s} booked"))


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _when(iso: str) -> str:
    """"Oct 2, 06:04" on the buyer's clock, or ""."""
    try:
        d = datetime.fromisoformat(str(iso)).astimezone(ZoneInfo(settings.tz()))
    except (ValueError, TypeError, KeyError):
        return ""
    return f"{d:%b} {d.day}, {d:%H:%M}"


def _numbers(c: dict) -> str:
    """"412 sent, 180 opened, 6 clicked, 3 replied, 1 meeting booked." Nothing that is zero is said."""
    return ", ".join(f"{n:,} {word.format(s='s' if n != 1 else '')}"
                     for k, word in _SAY if (n := int(c.get(k) or 0)))


def _campaigns() -> str:
    cs = seam.campaigns()
    if not cs:
        return "None read yet."
    counts: dict[str, int] = {}
    for c in cs:
        word = instantly.STATUS.get(c.get("status"), "other")
        counts[word] = counts.get(word, 0) + 1
    order = list(_ORDER)
    return f"{len(cs):,}: " + ", ".join(f"{counts[w]:,} {w}" for w in sorted(
        counts, key=lambda w: order.index(w) if w in order else len(order)))


def _last_pull() -> str:
    st = settings.pull_state()
    if st.get("key_refused"):
        return f"Stopped at {_esc(_when(st.get('last_run')))}: {_esc(st.get('error'))} Paste a new key below."
    if st.get("error"):
        return f"Stopped at {_esc(_when(st.get('last_run')))}: {_esc(st['error'])}"
    if st.get("last_run"):
        return f"{_esc(_when(st['last_run']))}. The next one is within the hour."
    return "Not yet. The first pull starts when Instantly is connected."


def _pulling(js: list[dict]) -> str:
    """"Pulling, started 09:41: Spring founders done, Agency owners pulling, Clinics waiting." """
    run = outreach_sync.pull_run()
    done, names = run.get("done") or {}, run.get("names") or {}
    now_on = ""
    for j in js:
        try:
            if j.get("status") == "running":
                now_on = str(json.loads(j.get("raw_text") or "{}").get("campaign") or "")
        except ValueError:
            pass
    bits = []
    for cid in run.get("campaigns") or []:
        name = _esc(names.get(cid) or "A campaign")
        bits.append(f"{name} {'done' if done.get(cid) == 'ok' else 'stopped'}" if cid in done else
                    f"{name} {'pulling' if cid == now_on else 'waiting'}")
    return (f"Pulling, started {_esc(_when(run.get('started') or js[0]['created_at']))}"
            + (": " + ", ".join(bits) if bits else ": reading your campaigns") + ".")


def _week_row() -> str:
    """THE NUMBERS WHERE THEY WERE CONNECTED: the week ending yesterday, one entry per campaign with anything in it,
    in the same words as the website card. No campaign with nothing, and no row with no numbers (owner, 2026-09-29:
    "If a line doesn't have data, it should not be displayed")."""
    end = outreach_sync.today() - timedelta(days=1)
    wk = seam.outreach_week(end)
    lines = [_esc(f"{c['name'] or 'A campaign'}: {_numbers(c)}.") for c in (wk or {}).get("campaigns") or []
             if _numbers(c)]
    if not lines:
        return ""
    start = end - timedelta(days=6)
    return f'<dt>{start:%b} {start.day} to {end:%b} {end.day}</dt><dd>{"<br>".join(lines)}</dd>'


def _hidden(do: str) -> str:
    return (f'<input type="hidden" name="card" value="{KEY}"><input type="hidden" name="do" value="{_esc(do)}">')


def _busy(doing: str) -> str:
    return (f'<p class="quiet wa-busy">{_esc(doing)}&hellip; <a href="/settings/sources/outreach">Refresh</a> '
            'to see the result.</p>')


def render(note=None) -> str:
    connected = settings.instantly() is not None
    said = ""
    if note:
        ok, text = note
        said = f'<p class="{"ok" if ok else "stale"}" role="status">{_esc(text)}</p>'
    pulling, checking = jobs.running(outreach_sync.PULL), jobs.running(outreach_sync.CHECK)
    conn = (f"Checking Instantly, started {_esc(_when(checking[0]['created_at']))}." if checking else
            "Connected. The box only reads; it never sends from Instantly." if connected else "Not connected yet.")
    facts = ('<dl class="ui-facts">'
             f'<dt>Instantly</dt><dd>{conn}</dd>'
             f'<dt>Campaigns</dt><dd>{_esc(_campaigns())}</dd>'
             f'<dt>Last pull</dt><dd>{_pulling(pulling) if pulling else _last_pull()}</dd>'
             + _week_row() + '</dl>')
    acts = ""
    wait = outreach_sync.backing_off()
    if connected:
        # NO PULL NOW DURING A BACK-OFF (OSDev1's review): Instantly asked the box to wait, so the card says until when.
        acts = ('<div class="ui-acts">' + (_busy("Pulling") if pulling else
                f'<p class="quiet">Pull now works again at {_esc(wait)}, when Instantly lets the box ask.</p>'
                if wait else
                f'<form method="post" action="/settings/sources/outreach">{_hidden("pull")}'
                '<button class="ghost" type="submit">Pull now</button></form>') + '</div>')
    lc = outreach_sync.last_check()
    last = (f'<p class="{"ok" if lc.get("ok") else "stale"}">Last check, {_esc(_when(lc.get("at")))}: '
            f'{_esc(lc["said"])}</p>' if lc.get("said") and not checking else "")
    form = (last + f'<form method="post" action="/settings/sources/outreach">{_hidden("key")}'
            f'<label for="out-key">Instantly API key, with the {instantly.SCOPE} scope</label>'
            '<input id="out-key" name="key" type="password" autocomplete="off" '
            f'placeholder="{"Saved. Paste a new one to replace it." if connected else "Paste your key"}">'
            '<p class="quiet">In Instantly: Settings, then Integrations, then API keys. Create a key with only '
            f'{instantly.SCOPE}, so it can read and never send. It stays on this box.</p>'
            + (_busy("Checking") if checking else '<button class="ghost" type="submit">Check and save</button>')
            + '</form>')
    return ('<div class="card" id="outreach"><h2>Outreach</h2>'
            '<p class="sub">Your cold email campaigns in Instantly: what was sent, opened, clicked, replied to and '
            'booked, by campaign and by step, read every hour. Your coworkers read them from here.</p>'
            + said + facts + acts + '<h3 class="wa-h">Instantly</h3>' + form + _WA_CSS + '</div>')


def handle(do: str, form, by: str) -> tuple[bool, str]:
    if do == "pull":
        return outreach_sync.start_pull("now")
    if do == "key":
        key = str(form.get("key") or "").strip()
        if not key:
            return False, f"Paste your Instantly API key. It needs the {instantly.SCOPE} scope."
        if any(ch.isspace() for ch in key):
            return False, "That key has a space in it, or came in two pieces. Copy it again and paste it on its own."
        # THE READ RUNS IN THE WORKER (outreach_sync.do_check): saved only if Instantly answers.
        return outreach_sync.start_check(key)
    return False, "That did not work, and nothing was changed."


def summary() -> dict:
    """The card's one row in the Data Sources table (core/source_cards.py), in the card's own words."""
    connected = settings.instantly() is not None
    st, cs = settings.pull_state(), seam.campaigns()
    live = sum(1 for c in cs if c.get("status") in outreach_sync._LIVE)
    what = ("Instantly: " + f"{len(cs):,} campaign{'s' if len(cs) != 1 else ''}"
            + (f", {live:,} sending" if live else "")) if cs else "Instantly"
    stopped = st.get("error") or st.get("key_refused")
    status = ("Stopped" if stopped else "Connected") if connected else "Not connected"
    return {"what": what, "when": _when(st.get("last_run")) if connected else "", "status": status,
            "connected": connected}


source_cards.register(KEY, title="Outreach", render=render, handle=handle, order=20, summary=summary)
