"""Settings → Data Sources → Website analytics: the one place to connect a business's website numbers.

docs/PLAN_ANALYTICS_FOUNDATION_PHASE1.md §5 (owner-approved scope, 2026-10-01). The sites, PostHog (where it is,
the project, a personal key that can only read), Search Console (its existing button), what counts as a
conversion on each site, and the last sync, with "Sync now". Registered with core/source_cards.py when the
foundation is imported, so core names no department and a box without this foundation shows no card.

EVERYTHING IS ON THE CARD, nothing behind a click (OSDev1's assignment), and every error names its fix. It reads
`settings`, writes the `website` namespace and the POSTHOG_API_KEY_WEB secret, and never touches the AEO
Machine's own fields, which OSDev6 removes once AEO reads the store.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from core import box_secrets, box_settings, source_cards

from . import jobs, posthog, settings, visitors

KEY = "website"
CLOUD = {"us": "https://us.posthog.com", "eu": "https://eu.posthog.com"}
_REGIONS = (("us", "PostHog Cloud, US"), ("eu", "PostHog Cloud, EU"), ("own", "My own PostHog"))
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_EVENT = re.compile(r"^[A-Za-z0-9_$.:-]{1,80}$")
_PROJECT = re.compile(r"^[0-9]{1,12}$")
MAX_SITES = 10


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


# ── reading what is set ────────────────────────────────────────────────────────────────────────────────────────

def _own(key: str, default=None):
    return box_settings.get(settings.NS, key, default=default)


def _region_of(host: str) -> str:
    h = str(host or "").rstrip("/")
    return next((k for k, v in CLOUD.items() if v == h), "own" if h else "us")


def _when(iso: str) -> str:
    """"Oct 2, 06:04" on the buyer's clock, or ""."""
    try:
        d = datetime.fromisoformat(str(iso)).astimezone(ZoneInfo(settings.tz()))
    except (ValueError, TypeError, KeyError):
        return ""
    return f"{d:%b} {d.day}, {d:%H:%M}"


def _day(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso))
    except (ValueError, TypeError):
        return ""
    return f"{d:%b} {d.day}"


def _search_console() -> str:
    try:
        from core.vendors import google_search_console as gsc
        st = gsc.status()
    except Exception:                                    # noqa: BLE001 — a status, never a failure
        st = {}
    if st.get("connected"):
        who = f" as {_esc(st['account'])}" if st.get("account") else ""
        site = f", reading {_esc(st['property'])}" if st.get("property") else ", no site picked yet"
        return f'Connected{who}{site}. <a href="/settings/aeo/google">Search Console settings &rarr;</a>'
    return 'Not connected. <a href="/settings/aeo/google">Connect Google Search Console &rarr;</a>'


def _last_sync() -> str:
    st = settings.sync_state()
    if st.get("error"):
        return f"Stopped at {_esc(_when(st.get('last_run')))}: {_esc(st['error'])}"
    if st.get("note") == "no_posthog":
        return "Waiting for PostHog: connect it below and the first sync starts."
    if st.get("last_run"):
        synced = st.get("synced") or {}
        through = f", through {_esc(_day(st.get('upto')))}" if st.get("upto") else ""
        what = (" (" + ", ".join(f"{_esc(s)}: {int(n)} day{'s' if int(n) != 1 else ''}"
                                  for s, n in synced.items()) + ")") if synced else ""
        return f"{_esc(_when(st['last_run']))}{through}{what}."
    return "Not yet. The first sync runs after 06:00, or press Sync now."


def _day_row() -> str:
    """THE NUMBERS WHERE THEY WERE CONNECTED (OSDev1, after #1815): the last synced day, one line per site, from the
    seam the Morning Review reads, in its words: "ownbox.io: 412 visits, 38 from AI answers, 3 conversions
    (Checkout click 2, Booking click 1)." Nothing that is zero is said, a site with no visits has no line, and with
    no numbers at all there is no row (owner, 2026-09-29: "If a line doesn't have data, it should not be
    displayed")."""
    from . import seam, sync
    try:
        d = date.fromisoformat(str(settings.sync_state().get("upto") or ""))
    except ValueError:
        return ""
    lines, no_addresses = [], False
    for site in seam.sites():
        got = seam.day(site, d)
        totals = (got or {}).get("totals") or {}
        people, visits = int(totals.get("visitors") or 0), int(totals.get("sessions") or 0)
        if not people:
            continue
        # PEOPLE ONLY, AND SAID (OSDev1's ruling, after #1822): one day's distinct people, then what was left out.
        left = visitors.say(got.get("left_out") or [])
        parts = [f"{site}: {people:,} {'person' if people == 1 else 'people'} visited"
                 + (f" ({left} left out)" if left else "")]
        # A POSTHOG THAT KEEPS NO ADDRESSES can't have its scanners told apart: every visit counts as a person, said
        # once below, on this card only (OSDev1's ruling), never on the review.
        no_addresses = no_addresses or (visits > 0 and int(got.get("unchecked") or 0) >= visits)
        ai = int((got.get("by_source") or {}).get("ai") or 0)
        if ai:
            parts.append(f"{ai:,} visit{'s' if ai != 1 else ''} from AI answers")
        converted = int(got["totals"].get("converted") or 0)
        if converted:
            named = ", ".join(f"{name} {n:,}" for name, n, _ in (got.get("conversions") or [])[:2] if n)
            parts.append(f"{converted:,} conversion{'s' if converted != 1 else ''}" + (f" ({named})" if named else ""))
        lines.append(_esc(", ".join(parts) + "."))
    if not lines:
        return ""
    label = "Yesterday" if d == sync.yesterday() else f"{d:%b} {d.day}"
    note = ('<p class="quiet">Your PostHog does not keep visitors\' addresses, so mail scanners can\'t be told apart '
            'from people, and every visit is counted as a person.</p>' if no_addresses else "")
    return f'<dt>{label}</dt><dd>{"<br>".join(lines)}{note}</dd>'


def _own_events(site: str) -> list[dict]:
    own = _own("conversions") or {}
    items = own.get(site) if isinstance(own, dict) else None
    return [i for i in (items or []) if isinstance(i, dict) and i.get("name") and i.get("event")]


# ── drawing ────────────────────────────────────────────────────────────────────────────────────────────────────

def render(note=None) -> str:
    sites = settings.sites()
    source = settings.posthog_source()
    conn = settings.posthog()
    host, project = str(_own("posthog_host") or (conn.host if conn else "")), str(
        _own("posthog_project") or (conn.project if conn else ""))
    region = _region_of(host)
    own_key = box_secrets.is_set(settings.SECRET)
    ph = ("Connected, project " + _esc(project) + (" (" + dict(_REGIONS)[region] + ")" if region != "own" else
                                                  " at " + _esc(host)) + "." if source == "website" else
          "Using the AEO Machine's PostHog connection. Enter yours here to make this the one place."
          if source == "aeo" else "Not connected yet.")
    said = ""
    if note:
        ok, text = note
        said = f'<p class="{"ok" if ok else "stale"}" role="status">{_esc(text)}</p>'
    # WHILE THE WORKER HAS IT, SAY SO, AND OFFER NO SECOND PRESS (jobs.py): the queue is what says it is running.
    syncing, checking_jobs = jobs.running(jobs.SYNC), jobs.running(jobs.CHECK)
    checking = (f"Checking PostHog, started {_esc(_when(checking_jobs[0]['created_at']))}." if checking_jobs else "")
    facts = ('<dl class="ui-facts">'
             f'<dt>Websites</dt><dd>{" &middot; ".join(_esc(s) for s in sites) or "None yet. Add them below."}</dd>'
             f'<dt>PostHog</dt><dd>{checking or ph}</dd>'
             f'<dt>Search Console</dt><dd>{_search_console()}</dd>'
             f'<dt>Last sync</dt><dd>{_syncing(syncing) if syncing else _last_sync()}</dd>'
             + _day_row() + '</dl>'
             '<div class="ui-acts">' + (_busy("Syncing") if syncing else _post("sync", "Sync now")) + '</div>')
    shown = chr(10).join(_own("sites") or sites)
    # WHERE THE LIST CAME FROM, said on the box (OSDev1, first setup): the box found these in PostHog until a person
    # types their own, and a list saved as it was shown stays found (handle "sites"), so it keeps updating itself.
    found_note = ('<p class="quiet">Found in your PostHog. Type your own list to choose.</p>'
                  if settings.sites_source() == "found" else "")
    sites_form = (_form("sites",
                        '<label for="wa-sites">Your websites, each on its own row</label>'
                        + found_note +
                        f'<textarea id="wa-sites" name="sites" rows="3" autocapitalize="off" spellcheck="false" '
                        f'placeholder="ownbox.io">{_esc(shown)}</textarea>'
                        f'<input type="hidden" name="shown" value="{_esc(shown)}">'
                        '<p class="quiet">As each appears in the address bar. ownbox.io and www.ownbox.io are '
                        'one website, counted once.</p>', "Save websites"))
    radios = "".join(f'<label class="consent"><input type="radio" name="region" value="{k}"'
                     f'{" checked" if k == region else ""}> {_esc(lab)}</label>' for k, lab in _REGIONS)
    ph_form = _form("posthog",
                    f'<fieldset class="wa-f"><legend>Where your PostHog is</legend>{radios}</fieldset>'
                    '<label for="wa-host">Your PostHog address (leave blank if you do not self host)</label>'
                    f'<input id="wa-host" name="own_host" type="url" placeholder="https://posthog.example.com" '
                    f'value="{_esc(host if region == "own" else "")}" autocapitalize="off" spellcheck="false">'
                    '<label for="wa-project">Project ID</label>'
                    f'<input id="wa-project" name="project" inputmode="numeric" value="{_esc(project)}" '
                    'placeholder="12345">'
                    '<p class="quiet">In PostHog, open Settings, then Project. The ID is a number.</p>'
                    f'<label for="wa-key">Personal API key, with the {posthog.SCOPE} scope</label>'
                    '<input id="wa-key" name="key" type="password" autocomplete="off" '
                    f'placeholder="{"Saved. Paste a new one to replace it." if own_key else "phx_..."}">'
                    f'<p class="quiet">In PostHog: your account settings, then Personal API keys. Give it only '
                    f'{posthog.SCOPE}, and this project. It stays on this box.</p>', "Check and save",
                    busy="Checking" if checking_jobs else "")
    lc = jobs.last_check()
    if lc.get("said") and not checking_jobs:
        ph_form = (f'<p class="{"ok" if lc.get("ok") else "stale"}">Last check, {_esc(_when(lc.get("at")))}: '
                   f'{_esc(lc["said"])}</p>' + ph_form)
    conv = []
    for site in sites:
        mine = _own_events(site)
        rows = "".join(
            f'<li>{_esc(i["name"])} <span class="quiet">({_esc(i["event"])})</span> '
            + _post("conv_remove", "Remove", extra={"site": site, "i": str(n)}) + '</li>'
            for n, i in enumerate(mine))
        conv.append(f'<p><b>{_esc(site)}</b>: ' + (f'</p><ul class="wa-ev">{rows}</ul>' if rows else
                                                    'only the ones every site has.</p>'))
    conv_form = ""
    if sites:
        pick = ("".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in sites))
        conv_form = _form("conv_add",
                          (f'<label for="wa-site">Website</label><select id="wa-site" name="site">{pick}</select>'
                           if len(sites) > 1 else f'<input type="hidden" name="site" value="{_esc(sites[0])}">')
                          + '<label for="wa-name">What it is called</label>'
                          '<input id="wa-name" name="name" maxlength="60" placeholder="Intake form sent">'
                          '<label for="wa-event">Its PostHog event name</label>'
                          '<input id="wa-event" name="event" maxlength="80" autocapitalize="off" spellcheck="false" '
                          'placeholder="intake_submitted">', "Add this conversion")
    return ('<div class="card" id="website"><h2>Website analytics</h2>'
            '<p class="sub">Your websites\' visits, where they came from and what they led to, read each morning '
            'from your own PostHog and Search Console. Your Morning Review and your coworkers read them from '
            'here.</p>' + said + facts
            + '<h3 class="wa-h">Your websites</h3>' + sites_form
            + '<h3 class="wa-h">PostHog</h3>' + ph_form
            + '<h3 class="wa-h">What counts as a conversion</h3>'
            '<p class="quiet">On every site: a click through to a Stripe checkout, a click on a Google booking '
            'page, and a contact form sent. Add each site\'s own by its PostHog event name.</p>'
            + "".join(conv) + conv_form + _CSS + '</div>')


_CSS = ("<style>.wa-h{margin:22px 0 4px;font-size:calc(16 * var(--px, 1px))}"
        ".wa-f{border:0;margin:0;padding:0}.wa-f legend{font-weight:600;margin:10px 0 0}"
        ".wa-ev{margin:4px 0 8px;padding-left:20px}.wa-ev li{margin:4px 0}"
        ".wa-ev form{display:inline}.wa-ev button{width:auto;min-height:36px;margin:0 0 0 8px;padding:4px 14px;"
        "font-size:calc(14 * var(--px, 1px))}</style>")


def _hidden(do: str, extra: dict | None = None) -> str:
    return (f'<input type="hidden" name="card" value="{KEY}"><input type="hidden" name="do" value="{_esc(do)}">'
            + "".join(f'<input type="hidden" name="{_esc(k)}" value="{_esc(v)}">' for k, v in (extra or {}).items()))


def _post(do: str, label: str, *, extra: dict | None = None) -> str:
    # EVERY BUTTON HERE IS A GHOST: the page's one ink pill is Connect, for an app (core/dash/sources.py).
    return (f'<form method="post" action="/settings/sources#website">{_hidden(do, extra)}'
            f'<button class="ghost" type="submit">{_esc(label)}</button></form>')


def _form(do: str, fields: str, label: str, *, busy: str = "") -> str:
    return (f'<form method="post" action="/settings/sources#website">{_hidden(do)}{fields}'
            + (_busy(busy) if busy else f'<button class="ghost" type="submit">{_esc(label)}</button>') + '</form>')


def _busy(doing: str) -> str:
    """In place of the button while the worker has it: what is happening, and a way to look again."""
    return (f'<p class="quiet wa-busy">{_esc(doing)}&hellip; <a href="/settings/sources#website">Refresh</a> '
            'to see the result.</p>')


def _syncing(js: list[dict]) -> str:
    """"Syncing, started 09:41: ownbox.io done, brian-macdonald.com waiting." from the run and its queued jobs."""
    run = jobs.sync_run()
    done = run.get("done") or {}
    now_on = ""
    for j in js:
        try:
            if j.get("status") == "running":
                now_on = str(json.loads(j.get("raw_text") or "{}").get("site") or "")
        except ValueError:
            pass
    # ONE SITE AT A TIME (jobs.start_sync): the run's own list says which are done, which is being synced, and which
    # wait their turn; only one of them is ever in the queue.
    bits = []
    for s in run.get("sites") or []:
        if s in done:
            bits.append(f"{_esc(s)} {'done' if done[s].get('outcome') == 'ok' else 'stopped'}")
        else:
            bits.append(f"{_esc(s)} {'syncing' if s == now_on else 'waiting'}")
    return (f"Syncing, started {_esc(_when(run.get('started') or js[0]['created_at']))}"
            + (": " + ", ".join(bits) if bits else "") + ".")


# ── acting ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _clean_site(raw: str) -> str:
    """"https://www.Ownbox.io/pricing" -> "www.ownbox.io". "" when it is not a website address."""
    t = str(raw or "").strip().lower()
    if not t:
        return ""
    if "://" not in t:
        t = "https://" + t
    try:
        h = (urlsplit(t).hostname or "").strip(".")
    except ValueError:
        return ""
    return h if _HOST.match(h) and "." in h else ""


def _api_host(region: str, typed: str) -> str:
    """The PostHog API address from the choice, or raise ValueError with the fix. The AEO Machine's rule."""
    if region in CLOUD:
        return CLOUD[region]
    v = str(typed or "").strip()
    try:
        parts = urlsplit(v)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise ValueError("Type your PostHog address, like https://posthog.example.com.") from None
    if parts.scheme != "https" or "." not in host or not _HOST.match(host) or parts.path.strip("/") or parts.query:
        raise ValueError("Type your PostHog address, like https://posthog.example.com, or choose US or EU.")
    if host.endswith(".i.posthog.com"):
        raise ValueError("That is PostHog's address for sending events. Choose US or EU instead.")
    return f"https://{host}" + (f":{port}" if port else "")


def handle(do: str, form, by: str) -> tuple[bool, str]:
    if do == "sites":
        typed = [x for x in re.split(r"[\s,]+", str(form.get("sites") or "")) if x]
        clean = [_clean_site(x) for x in typed]
        bad = [t for t, c in zip(typed, clean) if not c]
        if bad:
            return False, f"“{bad[0][:60]}” is not a website address. Type it like ownbox.io."
        out = list(dict.fromkeys(clean))
        was = [c for c in (_clean_site(x) for x in re.split(r"[\s,]+", str(form.get("shown") or "")) if x) if c]
        if settings.sites_source() == "found" and out == list(dict.fromkeys(was)):
            return True, "Nothing changed. The box keeps finding your websites in your PostHog."
        if len(out) > MAX_SITES:
            return False, f"Up to {MAX_SITES} websites. Remove one, then save."
        box_settings.put(settings.NS, "sites", out, set_by=by)
        if not out:
            # AN EMPTY LIST HANDS THE CHOICE BACK TO THE BOX (settings.sites): it watches what it finds in PostHog and
            # the AEO Machine's site, so the sentence says which, never "none" while some are watched.
            now = settings.sites()
            return True, ("Saved. The box picks the websites itself from your PostHog: " + ", ".join(now) + "."
                          if now else "Saved. No websites are watched yet; the box adds them when PostHog sees visits.")
        return True, "Saved: " + ", ".join(out) + "."
    if do == "posthog":
        return _posthog(form, by)
    if do == "conv_add":
        site, name, event = (str(form.get(k) or "").strip() for k in ("site", "name", "event"))
        if site not in settings.sites():
            return False, "Pick one of your websites."
        if not name or len(name) > 60:
            return False, "Give it a name a person would say, like Intake form sent."
        if not _EVENT.match(event):
            return False, ("Type the event's name exactly as PostHog shows it, like intake_submitted: letters, "
                           "digits and _ $ . : - only.")
        own = _own("conversions") or {}
        own = dict(own) if isinstance(own, dict) else {}
        mine = [i for i in _own_events(site) if i["event"] != event] + [{"name": name, "event": event}]
        own[site] = mine
        box_settings.put(settings.NS, "conversions", own, set_by=by)
        return True, f"Counting {name} on {site} from the next sync."
    if do == "conv_remove":
        site = str(form.get("site") or "")
        try:
            n = int(form.get("i"))
        except (TypeError, ValueError):
            n = -1
        mine = _own_events(site)
        if not 0 <= n < len(mine):
            return False, "That one was already gone."
        gone = mine.pop(n)
        own = _own("conversions") or {}
        own = dict(own) if isinstance(own, dict) else {}
        own[site] = mine
        box_settings.put(settings.NS, "conversions", own, set_by=by)
        return True, f"No longer counting {gone['name']} on {site}."
    if do == "sync":
        if settings.posthog() is None:
            return False, "Connect PostHog first, below; the sync reads from it."
        if not settings.sites():
            return False, "Add your websites first, below."
        return jobs.start_sync()
    return False, "That did not work, and nothing was changed."


def _posthog(form, by: str) -> tuple[bool, str]:
    # NO SITE NEEDED FIRST (OSDev1, for a new buyer's first setup): with none typed or found, the check asks PostHog
    # which sites it sees (jobs.do_check).
    try:
        host = _api_host(str(form.get("region") or "us"), str(form.get("own_host") or ""))
    except ValueError as e:
        return False, str(e)
    project = str(form.get("project") or "").strip()
    if not _PROJECT.match(project):
        return False, "The project ID is a number. In PostHog, open Settings, then Project."
    key = str(form.get("key") or "").strip()
    if not key:
        if not box_secrets.is_set(settings.SECRET):
            return False, f"Paste your PostHog personal API key. It needs the {posthog.SCOPE} scope."
    elif any(ch.isspace() for ch in key):
        return False, "That key has a space in it, or came in two pieces. Copy it again and paste it on its own."
    # THE QUERIES RUN IN THE WORKER (jobs.py): saved only if PostHog answers, said on this card when done.
    return jobs.start_check(host, project, key)


source_cards.register(KEY, title="Website analytics", render=render, handle=handle, order=10)
