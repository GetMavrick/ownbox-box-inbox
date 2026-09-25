"""The SEO machine's screens: Topics (what to write), Data sources (Sanity and Airtable) and Settings
(the website, and what the writer may say).

MOUNTED THROUGH CONFIG `web_modules:`, like every machine's pages, so core never imports this file.
It draws with core's `chrome()` — the same frame, rail and controls as System Settings — rather than
a look of its own, because a machine that brings its own stylesheet is a second design to keep in
step with the first.

EVERY SETTING IS WRITTEN THROUGH `settings.py`'S NAMES, into `core.box_settings` under machine
`seo`, and read back through `settings.get()` — the one accessor the publisher and the writer read.
There is no second settings module and no key name that exists only here (OSDev6, 2026-09-25). The
two credentials are the exception, only because they are credentials: the Sanity token and the
Airtable key go to `box_secrets` under the names `sources.py` defines, and are never shown again.

ONE HOME PER SETTING. The Sanity project, dataset and token live on Data sources > Sanity and
nowhere else; SEO Settings no longer carries them (owner, 2026-09-25, via OSDev1: "The Sanity fields
move OFF SEO Settings onto it"). A connection is saved only after `sources.py` has proved it.

OWNER ONLY TO CHANGE, ANYONE SIGNED IN TO READ. This screen decides what gets published on the
business's own website under its name, which is the same kind of decision as the inbox's
connections, and those are the owner's (owner, 2026-09-24: "Owner only"). Extending that ruling to
this machine is a proposal, not yet owner-approved.

DARK UNTIL SET UP. Until the box has a website and a proved Sanity connection, Topics says what is
missing and offers no button that could not work.

NO `get_config()` ANYWHERE IN THIS PACKAGE. A buyer cannot edit YAML, and a sold box's config does
not even carry an `seo` section (tests/test_seo_settings.py measures both).
"""
from __future__ import annotations

import html as _html
import importlib.util
import re
from datetime import datetime
from urllib.parse import urlsplit

from flask import Blueprint, redirect, request

from core import box_secrets, box_settings, dash, shell
from core.logging import get_logger

from . import plan, posthog, settings, sources

log = get_logger(__name__)

blueprint = Blueprint("seo_machine_app", __name__)

HOME = "/seo"
TOPICS = "/seo/topics"
SETTINGS = "/seo/settings"
SOURCES = "/seo/sources"
SANITY = "/seo/sources/sanity"
AIRTABLE = "/seo/sources/airtable"
POSTHOG = "/seo/sources/posthog"
PERFORMANCE = "/seo/performance"
GOOGLE = "/settings/seo/google"          # core's own screen (core/dash/google_search.py)

# THE PUBLISHER READS THIS NAME. It is the owner's (OSDev6, 2026-09-25: "Token name stays the
# owner's SANITY_API_TOKEN_OWNBOX"), and tests/test_seo_machine_shell.py holds the two together.
TOKEN = sources.SANITY_TOKEN

# A MAGNIFIER, drawn as one path like every other rail icon (core/shell.py draws exactly one).
_ICON = "M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13ZM15.5 15.5 20 20"

# THE SECTION IS `/seo`, NOT ITS FIRST SCREEN. `core.shell` gives a top-level section every page
# under its own address and none outside it, so a section at /seo/topics would leave /seo/settings
# with no menu and no breadcrumb. /seo itself only redirects to Topics.
shell.register_section(
    "seo", order=20, machine="seo_machine", title="SEO", href=HOME, icon=_ICON,
    # ONLY ROWS THAT WORK TODAY (OSDev1, 2026-09-25, on the owner's delegation: "a basic working
    # system by today"). Today, Search and AI answers join when their sources are live
    # (docs/SCOPE_SEO_APP.md); an empty row would be a menu promising a page that says nothing.
    # Articles keeps the /seo/topics address its forms, links and tests already use. Data sources
    # is OSDev6's (#1572): it adds its row here with the menu and screens behind it.
    items=[
        {"key": "topics", "label": "Articles", "href": TOPICS},
        # How the website is doing, from PostHog (owner, 2026-09-25: "Customers are going to demand
        # current performance numbers immediately"). A working row even before PostHog is
        # connected: the screen says how to connect it, in one tap.
        {"key": "performance", "label": "Performance", "href": PERFORMANCE},
        {"key": "sources", "label": "Data sources", "href": SOURCES},
        {"key": "settings", "label": "Settings", "href": SETTINGS},
    ])

# DATA SOURCES IS A MENU INSIDE SEO (owner, 2026-09-25: "a data sources menu item that has a sub menu
# with things that are connected"), nested the way the Unified Inbox's Settings is, so its back
# arrow returns to SEO and nothing is added to the top-level menu.
shell.register_section(
    "seo_sources", order=10, machine="seo_machine", title="Data sources", href=SOURCES,
    parent="seo", icon=_ICON,
    items=[
        {"key": "overview", "label": "Overview", "href": SOURCES},
        {"key": "sanity", "label": "Sanity", "href": SANITY},
        {"key": "airtable", "label": "Airtable", "href": AIRTABLE},
        {"key": "posthog", "label": "PostHog", "href": POSTHOG},
        # CORE'S SCREEN, THE OWNER'S ALONE (OSDev1, 2026-09-25: "Data sources > Google opens
        # /settings/seo/google"). Owner-only, so a member is never shown a row they are refused at.
        {"key": "google", "label": "Google Search Console", "href": GOOGLE, "owner_only": True},
    ])


# ── who is looking ────────────────────────────────────────────────────────────────────────────

def _admit():
    """Core's one gate for a signed-in screen. Never a copy of it (see core/dash/box_settings.py)."""
    from core.dash import review as _review
    return _review._admit(owner_only=False)


def _who() -> dict:
    try:
        return dash.session_user(request) or {}
    except Exception:                                # noqa: BLE001 — unreadable: not the owner
        return {}


def _is_owner() -> bool:
    return (_who().get("role") or "") == "owner"


def _owner_refusal(path: str, title: str):
    body = ('<div class="card"><p>Only the owner of this box can change what the SEO machine '
            'writes and where it publishes.</p></div>')
    return _page(path, title, "This one is the owner's.", body), 403


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def _page(path: str, title: str, lede: str, body: str) -> str:
    from core.dash.home import chrome
    return chrome(path, title=title, lede=lede, body=body)


# ── what this box has been given ──────────────────────────────────────────────────────────────

def missing() -> list[str]:
    """What the box still needs before it can publish, in the words the screen uses. [] = ready."""
    s = settings.get()
    out = []
    if not s.get("site_url"):
        out.append("your website's address")
    if not sources.sanity_state()["connected"]:
        out.append("a Sanity connection")
    return out


def writer_installed() -> bool:
    """Is the job that writes and publishes on this box? Until it is, no button promises it.

    Asked of the file, not of the worker's handler table: the web process never imports the worker's
    modules, so that table is empty here on every box.
    """
    try:
        return importlib.util.find_spec(f"{__package__}.job") is not None
    except Exception:                                # noqa: BLE001
        return False


# ── the settings screen's fields ──────────────────────────────────────────────────────────────
# (key in settings.DEFAULTS, label, input type, placeholder, help)
_FIELDS = (
    ("site_url", "Your website", "url", "https://example.com",
     "Articles go live at this address, under /articles."),
    ("indexnow_key", "IndexNow key", "text", "",
     "Optional. Lets Bing and others know the minute an article is live. Your website serves "
     "the same key."),
)
# (key, label, help). One item per row of the text box on the screen, a list in the store.
_LISTS = (
    ("facts", "Facts the writer may state",
     "Put each on its own row. The writer states nothing about your business that is not here."),
    ("allowed_numbers", "Numbers it may use",
     "Each on its own row, such as your prices. An article with any other number is held back."),
    ("never_words", "Words it must never use", "Each on its own row. Whole words."),
    ("never_phrases", "Phrases it must never use", "Each on its own row. Matched anywhere in the text."),
    ("competitors", "Competitors it must never name", "Each on its own row."),
)
_LIST_MAX, _ITEM_MAX = 200, 200
# ARTICLES A WEEK, folded in from OSDev4's #1565 (OSDev1, 2026-09-25). The number is the owner's: the
# screen only bounds it. 0 is a real answer, "stop publishing", and the job honours it by finding
# no room this week.
_CAP_MAX = 21

_INDEXNOW_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")


class _Refused(ValueError):
    pass


def _site(value: str) -> tuple[str, str]:
    """(site_url, host) from what was typed. A bare domain is taken as https."""
    v = value.strip()
    if not v:
        return "", ""
    if "://" not in v:
        v = "https://" + v
    parts = urlsplit(v)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "." not in host or parts.query or parts.fragment:
        raise _Refused("Type your website's address, like https://example.com.")
    path = parts.path.rstrip("/")
    return f"https://{host}{path}", host


def _lines(text: str) -> list[str]:
    seen, out = set(), []
    for raw in str(text or "").splitlines():
        item = " ".join(raw.split())
        if not item or item.lower() in seen:
            continue
        if len(item) > _ITEM_MAX:
            raise _Refused(f"One entry is {len(item)} characters. Keep each under {_ITEM_MAX}.")
        seen.add(item.lower())
        out.append(item)
    if len(out) > _LIST_MAX:
        raise _Refused(f"A list can hold {_LIST_MAX} entries. That one has {len(out)}.")
    return out


def _cap(value: str):
    """The weekly number from the form: "" (back to the default) or an int in range."""
    v = str(value or "").strip()
    if not v:
        return ""
    if not v.isdecimal() or not v.isascii() or int(v) > _CAP_MAX:
        raise _Refused(f"Articles a week is a whole number from 0 to {_CAP_MAX}.")
    return int(v)


def _save(form, *, user_id: str) -> None:
    """Check everything first, then write. A form that fails half way writes nothing."""
    writes: dict = {}
    site, host = _site(form.get("site_url") or "")
    writes["site_url"], writes["host"] = site, host
    key = (form.get("indexnow_key") or "").strip()
    if key and not _INDEXNOW_RE.match(key):
        raise _Refused("An IndexNow key is 8 to 128 letters, numbers and dashes.")
    writes["indexnow_key"] = key
    for name, _label, _help in _LISTS:
        writes[name] = _lines(form.get(name) or "")
    writes["weekly_cap"] = _cap(form.get("weekly_cap"))

    # EMPTY MEANS "BACK TO THE DEFAULT", so an empty field clears the box's row rather than pinning
    # "" over a default that may change (core/box_settings.py `clear`).
    for name, value in writes.items():
        if name not in settings.DEFAULTS:
            raise RuntimeError(f"{name} is not one of settings.py's keys")
        if value in ("", []):
            box_settings.clear(settings.MACHINE, name)
        else:
            box_settings.put(settings.MACHINE, name, value, set_by=user_id)
    log.info("seo.settings_saved", user=user_id)


# ── the screens ───────────────────────────────────────────────────────────────────────────────

_SAID = {
    "saved": ("Saved", "The SEO machine uses these from its next article.", True),
    "added": ("Topic added", "It is in the plan below.", True),
    "now": ("Next up", "Writing starts within a minute or so. It takes a few minutes, then this "
                       "page shows whether it went live.", True),
    "full": ("First in line", "This week's articles are already out, so it goes live once the "
                              "oldest of them is a week old.", True),
    "paused": ("First in line", "Publishing is paused: articles a week is set to 0 in SEO "
                                "settings.", True),
    "gone": ("That topic cannot go now", "It is already being written or is live.", False),
    "connected": ("Connected", "Checked and saved. The SEO machine uses it from its next article.",
                  True),
    # PostHog feeds Performance, not the articles, so its own sentence (OSDev1's review of #1584).
    "posthog_connected": ("Connected", "Checked and saved. Your numbers are on Performance.", True),
}


def _note(key: str, detail: str = "") -> str:
    head, text, good = _SAID.get(key, ("Not saved", detail, False))
    if detail:
        text = detail
    tone = "" if good else ' style="border-color:var(--danger)"'
    return f'<div class="card"{tone}><h2>{_esc(head)}</h2><p>{_esc(text)}</p></div>'


def _post(action: str, do: str, label: str, *, cls: str = "", extra: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return (f'<form method="post" action="{action}"><input type="hidden" name="do" value="{do}">'
            f'{extra}<button{klass} type="submit">{_esc(label)}</button></form>')


def _setup_card() -> str:
    need = missing()
    if not need:
        return ""
    # EACH MISSING PIECE LINKS TO ITS ONE HOME, so the card never sends the owner to a screen
    # that does not have the field they were told is missing.
    links = []
    if not sources.sanity_state()["connected"]:
        links.append(f'<a href="{SANITY}">Connect Sanity &rarr;</a>')
    if not settings.get().get("site_url"):
        links.append(f'<a href="{SETTINGS}">Open SEO settings &rarr;</a>')
    return ('<div class="card"><h2>Not set up yet</h2>'
            f'<p>Before it can publish, the SEO machine needs {_esc(_and(need))}.</p>'
            f'<div class="foot">{"".join(links)}</div></div>')


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _when(iso: str) -> str:
    """"25 Sep 2026" — the box's date style, not the database's."""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return ""


def _status(row: dict) -> str:
    st = row.get("status")
    if st == "published":
        url = row.get("url") or ""
        link = (f' <a href="{_esc(url)}" target="_blank" rel="noopener">Read it &rarr;</a>'
                if url.startswith("https://") else "")
        return f"<b>Live</b> since {_esc(_when(row.get('published_at')))}.{link}"
    if st == "writing":
        return "<b>Being written now.</b>"
    if st == "refused":
        return f"<b>Held back.</b> {_esc(row.get('refusal'))}"
    if st == "failed":
        return f"<b>Did not publish.</b> {_esc(row.get('refusal'))}"
    if row.get("requested_at"):
        return "<b>Next up.</b>"
    return "Planned."


# THE LIST IS AS DENSE AS THE INBOX'S (owner, 2026-09-25: "much tighter spacing where there's twice
# as much information on the page as far as titles and First couple lines of each post. Similar to
# the inbox."). One row per article: a dot, the title, a one-word state, two lines of what it
# answers, and one line of detail with the one thing to do. No card per row.
DENSE_CSS = """<style>
.ar-list{padding:4px 16px}
.ar{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;padding:10px 0;align-items:start;
border-bottom:1px solid var(--hairline)}
.ar:last-child{border-bottom:0}
.ar-d{grid-row:1/4;width:9px;height:9px;border-radius:99px;margin-top:7px;background:var(--faint)}
.ar-d.ok{background:var(--ok)}.ar-d.warn{background:var(--warn)}.ar-d.bad{background:var(--bad)}
.ar-d.ink{background:var(--ink)}
.ar-t{grid-row:1;grid-column:2;font:600 15.5px/1.3 var(--sans);min-width:0;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ar-w{grid-row:1;grid-column:3;font-size:13px;color:var(--ink-3);white-space:nowrap;padding-top:2px}
.card .ar-p{grid-row:2;grid-column:2/4;margin:0;font:400 15px/1.4 var(--sans);color:var(--ink-2);
max-height:2.8em;overflow:hidden}
.ar-s{grid-row:3;grid-column:2/4;display:flex;flex-wrap:wrap;align-items:center;gap:0 14px;
font-size:14px;color:var(--ink-3)}
.ar-s b{color:var(--ink-2);font-weight:600}
.ar-s form{display:inline}
button.ar-go{display:inline-flex;align-items:center;width:auto;min-height:44px;margin:0;padding:0;
border:0;background:none;color:var(--link);font-size:14px;font-weight:600}
button.ar-go:hover{background:none;text-decoration:underline}
.ar-cap{margin:-8px 2px 16px;font-size:14px}
.ar-add details{margin:6px 0 0}
</style>"""

_STATE = {"published": ("ok", "Live"), "writing": ("ink", "Writing"), "refused": ("warn", "Held"),
          "failed": ("bad", "Failed")}


def _topic_row(row: dict, *, can_go: bool) -> str:
    st = row.get("status")
    tone, word = _STATE.get(st, ("ink", "Next") if row.get("requested_at") else ("", "Planned"))
    q = f'<p class="ar-p">{_esc(row["question"])}</p>' if row.get("question") else ""
    btn = ""
    if can_go and st in ("planned", "refused", "failed") and not (
            st == "planned" and row.get("requested_at")):
        label = "Write and publish now" if st == "planned" else "Try again now"
        btn = _post(TOPICS, "now", label, cls="ghost ar-go",
                    extra=f'<input type="hidden" name="id" value="{int(row["id"])}">')
    # THE DETAIL ROW ONLY SAYS WHAT THE STATE WORD CANNOT: when it went live and where, or why it
    # was held. "Planned." under "Planned" is a row's worth of nothing.
    detail = f"<span>{_status(row)}</span>" if st in ("published", "refused", "failed") else ""
    return (f'<div class="ar"><span class="ar-d {tone}"></span>'
            f'<span class="ar-t">{_esc(row["topic"])}</span><span class="ar-w">{word}</span>{q}'
            + (f'<div class="ar-s">{detail}{btn}</div>' if detail or btn else "") + '</div>')


def weekly_cap() -> int:
    try:
        return max(0, int(settings.get().get("weekly_cap") or 0))
    except (TypeError, ValueError):
        return 0


def _week_full() -> bool:
    """Has this box published its weekly number in the last seven days? (Always, at 0.)"""
    from datetime import timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return plan.published_since(since) >= weekly_cap()


@blueprint.route(HOME, methods=["GET"])
def seo_home():
    refuse = _admit()
    if refuse is not None:
        return refuse
    return redirect(TOPICS, code=303)


@blueprint.route(TOPICS, methods=["GET", "POST"])
def seo_topics():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title, lede = "Articles", "What the SEO machine writes, and what became of each one."
    if request.method == "POST":
        if not _is_owner():
            return _owner_refusal(TOPICS, title)
        uid = str(_who().get("id") or "")
        do = (request.form.get("do") or "").strip()
        if do == "add":
            try:
                plan.add(request.form.get("topic") or "", request.form.get("question") or "",
                         user_id=uid)
            except plan.Rejected as e:
                return _topics_page(title, lede, note=_note("", str(e)),
                                    typed=(request.form.get("topic") or "",
                                           request.form.get("question") or "")), 400
            return redirect(f"{TOPICS}?said=added", code=303)
        if do == "now":
            if missing() or not writer_installed():
                return redirect(TOPICS, code=303)
            try:
                # THE CLICK ONLY MARKS THE ROW. The loop takes asked-for rows first, within about a
                # minute (OSDev6, 2026-09-25). Writing never runs inside this request: it can take a
                # minute, and the web server kills a request at 120 seconds, mid-write.
                ok = plan.request_now(int(request.form.get("id") or 0)) is not None
            except ValueError:
                ok = False
            # STILL MARKED WHEN THE WEEK IS FULL, so it goes first once there is room. What changes
            # is the sentence: "within a minute" would be false (OSDev1, 2026-09-25).
            said = "gone" if not ok else ("paused" if weekly_cap() == 0
                                          else "full" if _week_full() else "now")
            return redirect(f"{TOPICS}?said={said}", code=303)
        return redirect(TOPICS, code=303)
    said = request.args.get("said") or ""
    return _topics_page(title, lede, note=_note(said) if said in _SAID else ""), 200


def _topics_page(title: str, lede: str, *, note: str = "", typed=("", "")) -> str:
    owner = _is_owner()
    ready = not missing()
    writer = writer_installed()
    body = note + _setup_card()
    if ready and not writer:
        body += ('<div class="card"><h2>Writing is not switched on yet</h2>'
                 '<p>Your topics are saved. Articles start once this box\'s writer is '
                 'switched on in an update.</p></div>')
    # THE LIST HAS THE FIRST SCREEN, the inbox's order: articles, then how many a week, then the
    # compact add form. The form comes first only when there is nothing to list yet, or when a
    # refused add comes back with what was typed, so the fix is where the eye already is.
    rows = plan.rows()
    if rows:
        listed = ('<div class="card ar-list">'
                  + "".join(_topic_row(r, can_go=owner and ready and writer) for r in rows)
                  + '</div>')
    else:
        listed = ('<div class="card"><h2>No topics yet</h2><p>Each topic becomes one article '
                  'that answers a question your customers search for.</p></div>')
    if ready and writer and weekly_cap() == 0:
        listed += ('<p class="quiet ar-cap">Publishing is paused: articles a week is set to 0. '
                   f'Change it in <a href="{SETTINGS}">SEO settings</a> to start again.</p>')
    elif ready and writer:
        cap = weekly_cap()
        full = (' This week\'s are out. The next goes live once the oldest of them is a week '
                'old.') if _week_full() else ""
        listed += ('<p class="quiet ar-cap">Up to '
                   f'{_esc(cap)} a week are written from this list, oldest first, and each '
                   f'is checked against your settings before it goes live.{_esc(full)}</p>')
    if owner:
        # The question is optional and folds away, and opens again when a refused form comes
        # back with one typed in it.
        form = ('<div class="card"><form class="ar-add" method="post" action="' + TOPICS + '">'
                '<input type="hidden" name="do" value="add">'
                '<label for="topic" style="margin-top:0">Add a topic</label>'
                f'<input id="topic" name="topic" maxlength="{plan.TOPIC_MAX}" required '
                f'placeholder="Microneedling aftercare" value="{_esc(typed[0])}">'
                f'<details{" open" if typed[1] else ""}><summary>Add the question it answers'
                '</summary><label for="question">The question it answers</label>'
                f'<input id="question" name="question" maxlength="{plan.QUESTION_MAX}" '
                f'placeholder="What should I do after microneedling?" value="{_esc(typed[1])}">'
                '</details><button type="submit">Add topic</button></form></div>')
    else:
        form = ('<div class="card"><p>Only the owner of this box can add topics or publish.'
                '</p></div>')
    body += (form + listed) if (not rows or any(typed)) else (listed + form)
    return _page(TOPICS, title, lede, DENSE_CSS + body)


def _settings_form(typed=None) -> str:
    """The owner's form. `typed` is a refused POST, so a mistake does not wipe what was entered."""
    s = dict(settings.get())
    if typed is not None:
        for name, *_rest in _FIELDS:
            s[name] = typed.get(name) or ""
        for name, *_rest in _LISTS:
            s[name] = (typed.get(name) or "").splitlines()
        s["weekly_cap"] = typed.get("weekly_cap") or ""
    out = [f'<form method="post" action="{SETTINGS}">']

    def field(name, label, kind, ph, help_):
        return (f'<label for="{name}">{_esc(label)}</label>'
                f'<input id="{name}" name="{name}" type="{kind}" value="{_esc(s.get(name) or "")}"'
                f' placeholder="{_esc(ph)}" autocapitalize="off" autocorrect="off" '
                f'spellcheck="false"><p class="sub">{_esc(help_)}</p>')

    def area(name, label, help_):
        text = "\n".join(str(x) for x in (s.get(name) or []))
        return (f'<label for="{name}">{_esc(label)}</label>'
                f'<textarea id="{name}" name="{name}" rows="4">{_esc(text)}</textarea>'
                f'<p class="sub">{_esc(help_)}</p>')

    f = {k: (k, lab, kind, ph, h) for k, lab, kind, ph, h in _FIELDS}
    out.append('<div class="card">' + field(*f["site_url"]) + '</div>')
    out.append('<div class="card"><h2>Sanity and Airtable</h2>'
               '<p class="sub">Where articles are planned and where they are published are '
               'connected under Data sources.</p>'
               f'<div class="foot"><a href="{SOURCES}">Open Data sources &rarr;</a></div></div>')
    out.append('<div class="card"><h2>How often</h2><label for="weekly_cap">Articles a week</label>'
               f'<input id="weekly_cap" name="weekly_cap" type="number" inputmode="numeric" min="0" '
               f'max="{_CAP_MAX}" step="1" value="{_esc(s.get("weekly_cap"))}">'
               '<p class="sub">Up to this many go live in any seven days, oldest topic first. '
               '0 pauses publishing.</p></div>')
    out.append('<div class="card"><h2>Search engines</h2>' + field(*f["indexnow_key"])
               + f'<div class="foot"><a href="{GOOGLE}">Google Search Console &rarr;</a></div>'
               '</div>')
    out.append('<div class="card"><h2>What the writer may say</h2>'
               + "".join(area(k, lab, h) for k, lab, h in _LISTS[:2]) + '</div>')
    out.append('<div class="card"><h2>What it must never say</h2>'
               + "".join(area(k, lab, h) for k, lab, h in _LISTS[2:])
               + '<button type="submit">Save</button></div></form>')
    return "".join(out)


def _settings_readonly() -> str:
    s = settings.get()
    rows = [("Website", s.get("site_url") or "Not set"),
            ("Sanity", "Connected" if sources.sanity_state()["connected"] else "Not connected"),
            ("Airtable", "Connected" if sources.airtable_state()["connected"] else "Not connected"),
            ("Articles a week", str(weekly_cap()))]
    rows += [(lab, f"{len(s.get(k) or [])} entries") for k, lab, _h in _LISTS]
    items = "".join(f"<p><b>{_esc(a)}:</b> {_esc(b)}</p>" for a, b in rows)
    return (f'<div class="card">{items}<p class="sub">Only the owner of this box can change '
            'these.</p></div>')


@blueprint.route(SETTINGS, methods=["GET", "POST"])
def seo_settings():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title = "SEO settings"
    lede = "Your website, and what the writer may and may not say."
    if request.method == "POST":
        if not _is_owner():
            return _owner_refusal(SETTINGS, title)
        try:
            _save(request.form, user_id=str(_who().get("id") or ""))
        except (_Refused, box_secrets.SecretRejected) as e:
            return _page(SETTINGS, title, lede, _note("", str(e)) + _settings_form(request.form)), 400
        return redirect(f"{SETTINGS}?said=saved", code=303)
    said = request.args.get("said") or ""
    body = _note(said) if said == "saved" else ""
    body += _setup_card()
    body += _settings_form() if _is_owner() else _settings_readonly()
    return _page(SETTINGS, title, lede, body), 200


# ── data sources: Sanity and Airtable ─────────────────────────────────────────────────────────
# BOTH ARE REQUIRED (owner, 2026-09-25, see sources.py). Each screen proves its connection with the
# vendor before it saves a thing, and says in one sentence what to fix when the proof fails.

_TOKEN_MIN = 20


def _secret(form_value: str, saved_name: str, what: str) -> tuple[str, bool]:
    """(the credential to check, whether it was typed now). Blank keeps the saved one, so an owner
    changing the dataset does not have to find the token again."""
    typed = (form_value or "").strip()
    if typed:
        if any(ch.isspace() for ch in typed) or len(typed) < _TOKEN_MIN:
            raise _Refused(f"That does not look like {what}. Copy it again and paste it on its own.")
        return typed, True
    saved = box_secrets.get(saved_name) or ""
    if not saved:
        raise _Refused(f"Paste {what}.")
    return saved, False


def _secret_input(name: str, label: str, saved: bool, placeholder: str) -> str:
    hint = "Saved. Leave blank to keep it" if saved else placeholder
    return (f'<label for="{name}">{_esc(label)}</label>'
            f'<input id="{name}" name="{name}" type="password" autocomplete="off" '
            f'autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="{_esc(hint)}">')


def _text_input(name: str, label: str, value: str, placeholder: str, *, kind: str = "text") -> str:
    return (f'<label for="{name}">{_esc(label)}</label>'
            f'<input id="{name}" name="{name}" type="{kind}" value="{_esc(value)}" '
            f'placeholder="{_esc(placeholder)}" autocapitalize="off" autocorrect="off" '
            'spellcheck="false">')


def _steps(*items: str) -> str:
    return "<ol>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ol>"


def _source_card(title: str, what: str, state_text: str, connected: bool, href: str, *,
                 required: bool = True) -> str:
    # Only a REQUIRED source that is missing is drawn as a problem. PostHog is recommended, not
    # required, so an unconnected PostHog is an invitation, not a red card.
    tone = "" if connected or not required else ' style="border-color:var(--danger)"'
    # A MEMBER IS OFFERED ONLY WHAT THEY CAN DO: the page reads, it does not connect, for them.
    act = ("Change" if connected else "Connect") if _is_owner() else "See"
    return (f'<div class="card"{tone}><h2>{_esc(title)}</h2><p>{_esc(what)}</p>'
            f'<p><b>{_esc(state_text)}</b></p>'
            f'<div class="foot"><a href="{href}">{act} {_esc(title)} &rarr;</a></div></div>')


@blueprint.route(SOURCES, methods=["GET"])
def seo_sources():
    refuse = _admit()
    if refuse is not None:
        return refuse
    san, air = sources.sanity_state(), sources.airtable_state()
    body = ""
    if not (san["connected"] and air["connected"]):
        body += ('<div class="card"><p>The SEO machine needs both: Airtable, where you plan your '
                 'articles, and Sanity, where your website reads them.</p></div>')
    body += _source_card(
        "Airtable", "Where you plan your articles.",
        "Connected." if air["connected"] else "Not connected.", air["connected"], AIRTABLE)
    body += _source_card(
        "Sanity", "Where your website reads your articles. The SEO machine publishes them here.",
        (f"Connected to project {san['project']}, dataset {san['dataset']}."
         if san["connected"] else "Not connected."), san["connected"], SANITY)
    # Recommended, not required, so after the two the machine cannot run without.
    ph = posthog.state()
    body += _source_card(
        "PostHog", "Recommended. How your website is doing: visitors, article views, and visits "
        "from AI answer engines.",
        "Connected." if ph["connected"] else "Not connected.", ph["connected"], POSTHOG,
        required=False)
    # CORE'S GOOGLE SCREEN IS THE OWNER'S ALONE, so a member is not shown a door they are refused at
    # (tests/test_a_buyer_can_walk_every_screen.py walks every link as a member).
    if _is_owner():
        body += ('<div class="card"><h2>Google Search Console</h2>'
                 '<p>How your articles do in Google search.</p>'
                 f'<div class="foot"><a href="{GOOGLE}">Open Google Search Console &rarr;</a></div>'
                 '</div>')
    return _page(SOURCES, "Data sources",
                 "Where your articles are planned and published, and how they are doing.", body), 200


def _shaped(value, pattern) -> str:
    """What was typed, ONLY IF it has the field's shape, else "".

    A REFUSED FORM NEVER ECHOES A VALUE THAT FAILED ITS SHAPE (OSDev1's review of #1572). The usual
    reason a Project ID or a table address fails is a token pasted into the wrong box, and a 400
    page that re-renders it puts the secret on screen and in the browser's history.
    """
    v = str(value or "").strip().lower()
    return v if pattern.match(v) else ""


def _sanity_form(typed=None) -> str:
    st = sources.sanity_state()
    if typed is not None:
        project = _shaped(typed.get("project_id"), sources.PROJECT_RE)
        dataset = _shaped(typed.get("dataset"), sources.DATASET_RE) or "production"
    else:
        project, dataset = st["project"], st["dataset"] or "production"
    return (f'<form method="post" action="{SANITY}"><div class="card">'
            + _text_input("project_id", "Project ID", project, "abc123xy")
            + '<p class="sub">In sanity.io/manage, open the project your website reads. The ID is '
              'under its name.</p>'
            + _text_input("dataset", "Dataset", dataset, "production")
            + '<p class="sub">Leave it as production unless your website reads a different one.</p>'
            + _secret_input("token", "API token", st["token_saved"], "sk...")
            + '<button type="submit">Check and save</button></div></form>'
            '<div class="card"><h2>Making the token</h2>'
            + _steps("Open sanity.io/manage and choose the project your website reads.",
                     "Open API, then Tokens, and choose Add API token.",
                     "Name it after this box, choose Editor, and save.",
                     "Copy the token and paste it above. Sanity shows it only once.")
            + '<p class="sub">It needs Editor, not Viewer: the SEO machine writes your articles. '
              'The token is stored on this box and never shown again.</p></div>')


@blueprint.route(SANITY, methods=["GET", "POST"])
def seo_sanity():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title, lede = "Sanity", "Where your website reads your articles. The SEO machine publishes them here."
    if request.method == "POST":
        if not _is_owner():
            return _owner_refusal(SANITY, title)
        form = request.form
        try:
            project = (form.get("project_id") or "").strip().lower()
            if not sources.PROJECT_RE.match(project):
                raise _Refused("A Sanity project ID is letters and numbers only, like abc123xy.")
            dataset = (form.get("dataset") or "").strip().lower() or "production"
            if not sources.DATASET_RE.match(dataset):
                raise _Refused("A dataset name is lowercase letters, numbers, dashes and "
                               "underscores.")
            token, typed = _secret(form.get("token"), TOKEN, "a Sanity token")
            problem = sources.check_sanity(project, dataset, token)
            if problem:
                raise _Refused(problem)
            uid = str(_who().get("id") or "")
            box_settings.put(settings.MACHINE, "project_id", project, set_by=uid)
            box_settings.put(settings.MACHINE, "dataset", dataset, set_by=uid)
            if typed:
                box_secrets.put(TOKEN, token, user_id=uid)
        except (_Refused, box_secrets.SecretRejected) as e:
            log.info("seo.sanity_refused", reason=str(e)[:120])
            return _page(SANITY, title, lede, _note("", str(e)) + _sanity_form(form)), 400
        log.info("seo.sanity_connected", user=uid, project=project, dataset=dataset,
                 token_changed=typed)
        return redirect(f"{SANITY}?said=connected", code=303)
    said = request.args.get("said") or ""
    body = _note(said) if said == "connected" else ""
    if _is_owner():
        body += _sanity_form()
    else:
        st = sources.sanity_state()
        body += ('<div class="card"><p><b>'
                 + _esc(f"Connected to project {st['project']}, dataset {st['dataset']}."
                        if st["connected"] else "Not connected.")
                 + '</b></p><p class="sub">Only the owner of this box can change this.</p></div>')
    return _page(SANITY, title, lede, body), 200


def _airtable_form(typed=None) -> str:
    st = sources.airtable_state()
    url = st["url"]
    if typed is not None:
        # Rebuilt from its parts, so only a real table address is ever shown back (see _shaped).
        try:
            url = sources.table_url(*sources.parse_table_url(typed.get("table_url") or ""))
        except sources.BadTableUrl:
            url = ""
    template = (f'<div class="foot"><a href="{_esc(sources.TEMPLATE_URL)}">Copy our template '
                '&rarr;</a></div>') if sources.TEMPLATE_URL else ""
    fields = ", ".join(sources.TEMPLATE_FIELDS)
    return (f'<form method="post" action="{AIRTABLE}"><div class="card">'
            + _text_input("table_url", "Table address", url,
                          "https://airtable.com/app.../tbl.../viw...", kind="url")
            + '<p class="sub">Open the table in Airtable, and the view you want the SEO machine to '
              'follow, then copy the address from your browser.</p>'
            + _secret_input("api_key", "Personal access token", st["key_saved"], "pat...")
            + '<button type="submit">Check and save</button></div></form>'
            '<div class="card"><h2>Making the token</h2>'
            + _steps("In Airtable, open Builder hub, then Personal access tokens, and choose "
                     "Create token.",
                     "Name it after this box.",
                     "Add three scopes: " + ", ".join(sources.SCOPES) + ".",
                     "Under Access, add only the base that holds your content plan.",
                     "Create it, copy the token and paste it above.")
            + '<p class="sub">The token is stored on this box and never shown again.</p></div>'
            f'<div class="card"><h2>Your table</h2><p>It needs these fields, with exactly these '
            f'names: {_esc(fields)}.</p>{template}</div>')


@blueprint.route(AIRTABLE, methods=["GET", "POST"])
def seo_airtable():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title, lede = "Airtable", "Where you plan your articles. The SEO machine works from this table."
    if request.method == "POST":
        if not _is_owner():
            return _owner_refusal(AIRTABLE, title)
        form = request.form
        try:
            try:
                base, table, view = sources.parse_table_url(form.get("table_url") or "")
            except sources.BadTableUrl as e:
                raise _Refused(str(e)) from None
            key, typed = _secret(form.get("api_key"), sources.AIRTABLE_KEY,
                                 "an Airtable personal access token")
            problem = sources.check_airtable(key, base, table, view)
            if problem:
                raise _Refused(problem)
            uid = str(_who().get("id") or "")
            box_settings.put(settings.MACHINE, "airtable_base", base, set_by=uid)
            box_settings.put(settings.MACHINE, "airtable_table", table, set_by=uid)
            if view:
                box_settings.put(settings.MACHINE, "airtable_view", view, set_by=uid)
            else:
                box_settings.clear(settings.MACHINE, "airtable_view")
            if typed:
                box_secrets.put(sources.AIRTABLE_KEY, key, user_id=uid)
        except (_Refused, box_secrets.SecretRejected) as e:
            log.info("seo.airtable_refused", reason=str(e)[:120])
            return _page(AIRTABLE, title, lede, _note("", str(e)) + _airtable_form(form)), 400
        log.info("seo.airtable_connected", user=uid, base=base, table=table, key_changed=typed)
        return redirect(f"{AIRTABLE}?said=connected", code=303)
    said = request.args.get("said") or ""
    body = _note(said) if said == "connected" else ""
    if _is_owner():
        body += _airtable_form()
    else:
        st = sources.airtable_state()
        body += ('<div class="card"><p><b>'
                 + ("Connected." if st["connected"] else "Not connected.")
                 + '</b></p><p class="sub">Only the owner of this box can change this.</p></div>')
    return _page(AIRTABLE, title, lede, body), 200


# ── PostHog, and the Performance screen it feeds ────────────────────────────────────────────────
# Owner, 2026-09-25: "post hog is going to be the next data source we connect ... we should suggest
# post hog" and "Customers are going to demand current performance numbers immediately."

_REGIONS = (("us", "PostHog Cloud, US"), ("eu", "PostHog Cloud, EU"), ("own", "My own PostHog"))


def _posthog_form(typed=None) -> str:
    st = posthog.state()
    host = st["host"]
    region = next((k for k, v in posthog.CLOUD.items() if v == host), "own" if host else "us")
    own = "" if region != "own" else host
    project = st["project"]
    if typed is not None:
        region = typed.get("region") if typed.get("region") in dict(_REGIONS) else "us"
        # An address that failed is never shown back (see _shaped), only one that passed.
        try:
            own = posthog.api_host(typed.get("own_host") or "") if region == "own" else ""
        except posthog.BadHost:
            own = ""
        p = str(typed.get("project") or "").strip()
        project = p if posthog.PROJECT_RE.match(p) else ""
    radios = "".join(
        f'<label class="ph-r"><input type="radio" name="region" value="{k}"'
        f'{" checked" if k == region else ""}> {_esc(lab)}</label>' for k, lab in _REGIONS)
    return (f'<form method="post" action="{POSTHOG}"><div class="card">'
            f'<fieldset class="ph-f"><legend>Where your PostHog is</legend>{radios}</fieldset>'
            + _text_input("own_host", "Your PostHog address (own PostHog only)", own,
                          "https://posthog.example.com", kind="url")
            + _text_input("project", "Project ID", project, "12345")
            + '<p class="sub">In PostHog, open Settings, then Project. The ID is a number.</p>'
            + _secret_input("api_key", "Personal API key", st["key_saved"], "phx_...")
            + '<button type="submit">Check and save</button></div></form>'
            '<div class="card"><h2>Making the key</h2>'
            + _steps("In PostHog, open your account settings, then Personal API keys, and choose "
                     "Create personal API key.",
                     "Name it after this box.",
                     f"Give it one scope: {posthog.SCOPE}. Nothing else is needed.",
                     "Give it access to the project your website sends to, and create it.",
                     "Copy the key and paste it above. PostHog shows it only once.")
            + '<p class="sub">With that one scope, the key can read your numbers and change '
              'nothing. It is stored on this box and never shown again.</p></div>'
            '<div class="card"><h2>Your website</h2><p>PostHog counts what its snippet sees, so '
            'the snippet has to be on every page of your website, including your articles.</p></div>')


_PH_CSS = """<style>
.ph-f{border:0;margin:0 0 8px;padding:0}.ph-f legend{font-weight:600;margin-bottom:6px}
.ph-r{display:flex;align-items:center;gap:10px;min-height:48px;margin:0;font-weight:400}
.ph-r input{width:22px;height:22px;margin:0}
.pf-g{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 16px}
.pf-t{background:var(--card);border:1px solid var(--hairline);border-radius:16px;padding:14px}
.pf-k{font-size:13px;color:var(--ink-3)}.pf-n{font-size:26px;font-weight:700;margin:4px 0}
.pf-c{font-size:13px;color:var(--ink-3)}.pf-c.up{color:var(--ok)}.pf-c.down{color:var(--bad)}
.card .pf-l{list-style:none;margin:0;padding:0}.pf-l li{display:flex;justify-content:space-between;gap:10px;
padding:10px 0;border-bottom:1px solid var(--hairline);font-size:15px}.pf-l li:last-child{border-bottom:0}
.pf-l span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pf-l b{white-space:nowrap}
@media (min-width:760px){.pf-g{grid-template-columns:repeat(4,1fr)}}
</style>"""


@blueprint.route(POSTHOG, methods=["GET", "POST"])
def seo_posthog():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title, lede = "PostHog", "Recommended. How your website is doing, read from your own PostHog."
    if request.method == "POST":
        if not _is_owner():
            return _owner_refusal(POSTHOG, title)
        form = request.form
        try:
            region = form.get("region") or "us"
            try:
                host = posthog.api_host(form.get("own_host") if region == "own" else region)
            except posthog.BadHost as e:
                raise _Refused(str(e)) from None
            project = str(form.get("project") or "").strip()
            if not posthog.PROJECT_RE.match(project):
                raise _Refused("A PostHog project ID is a number, like 12345.")
            key, typed = _secret(form.get("api_key"), posthog.KEY, "a PostHog personal API key")
            # THE SAVED KEY ONLY EVER GOES TO THE POSTHOG IT WAS SAVED FOR (OSDev1's review, HIGH).
            # Without this, a blank key field and a new address would send the saved key to that
            # address on the check, and on every Performance view after it.
            if not typed and host != posthog.state()["host"]:
                raise _Refused("Paste the key again. A saved key is only ever sent to the PostHog "
                               "it was saved for.")
            problem, views = posthog.check(host, project, key)
            if problem:
                raise _Refused(problem)
            uid = str(_who().get("id") or "")
            box_settings.put(settings.MACHINE, "posthog_host", host, set_by=uid)
            box_settings.put(settings.MACHINE, "posthog_project", project, set_by=uid)
            if typed:
                box_secrets.put(posthog.KEY, key, user_id=uid)
            posthog.forget()
        except (_Refused, box_secrets.SecretRejected) as e:
            log.info("seo.posthog_refused", reason=str(e)[:120])
            return _page(POSTHOG, title, lede,
                         _PH_CSS + _note("", str(e)) + _posthog_form(form)), 400
        log.info("seo.posthog_connected", user=uid, host=host, project=project, views_30d=views)
        return redirect(f"{POSTHOG}?said={'posthog_connected' if views else 'no_views'}", code=303)
    said = request.args.get("said") or ""
    if said == "no_views":
        body = ('<div class="card" style="border-color:var(--warn)"><h2>Connected, with no page '
                'views yet</h2><p>PostHog has no page views from your website in the last 30 days. '
                'Check that the PostHog snippet is on your website.</p></div>')
    else:
        body = _note(said) if said == "posthog_connected" else ""
    if _is_owner():
        body += _posthog_form()
    else:
        body += ('<div class="card"><p><b>'
                 + ("Connected." if posthog.state()["connected"] else "Not connected.")
                 + '</b></p><p class="sub">Only the owner of this box can change this.</p></div>')
    return _page(POSTHOG, title, lede, _PH_CSS + body), 200


def _change(pct) -> str:
    # NO WEEK BEFORE, NO CHANGE (OSDev1's review): a percentage on nothing is made up, and
    # "new this week" on a tile reading 0 is worse.
    if pct is None:
        return ""
    cls = "up" if pct > 0 else "down" if pct < 0 else ""
    arrow = "&uarr; " if pct > 0 else "&darr; " if pct < 0 else ""
    return f'<div class="pf-c {cls}">{arrow}{abs(int(pct))}% vs last week</div>'


def _tile(label: str, value, pct) -> str:
    return (f'<div class="pf-t"><div class="pf-k">{_esc(label)}</div>'
            f'<div class="pf-n">{_esc(f"{int(value):,}")}</div>{_change(pct)}</div>')


def _list(title: str, rows, empty: str) -> str:
    if not rows:
        return f'<div class="card"><h2>{_esc(title)}</h2><p>{_esc(empty)}</p></div>'
    items = "".join(f'<li><span>{_esc(a)}</span><b>{_esc(f"{int(b):,}")}</b></li>' for a, b in rows)
    return f'<div class="card"><h2>{_esc(title)}</h2><ul class="pf-l">{items}</ul></div>'


@blueprint.route(PERFORMANCE, methods=["GET"])
def seo_performance():
    refuse = _admit()
    if refuse is not None:
        return refuse
    title = "Performance"
    lede = "How your website did over the last 7 days, against the 7 before."
    p = posthog.performance()
    if not p.get("ok"):
        if p.get("why") == "not_connected":
            link = (f'<div class="foot"><a href="{POSTHOG}">Connect PostHog &rarr;</a></div>'
                    if _is_owner() else '<p class="sub">The owner of this box can connect it.</p>')
            body = ('<div class="card"><h2>Connect PostHog to see your numbers</h2><p>Visitors, '
                    'article views, visits from AI answer engines, and the sites that send them. '
                    f'From your own PostHog, the minute it is connected.</p>{link}</div>')
        else:
            body = ('<div class="card" style="border-color:var(--warn)"><h2>No numbers right now'
                    f'</h2><p>{_esc(p.get("why"))}</p></div>')
        return _page(PERFORMANCE, title, lede, body), 200
    site = f" for {p['site']}" if p.get("site") else ""
    if p.get("empty"):
        body = ('<div class="card" style="border-color:var(--warn)"><h2>No visits recorded yet</h2>'
                '<p>PostHog is connected, and has no page views from your website in the last 14 '
                'days. Check that the PostHog snippet is on every page of your website.</p></div>'
                f'<p class="quiet ar-cap">From PostHog{_esc(site)}.</p>')
        return _page(PERFORMANCE, title, lede, _PH_CSS + body), 200
    body = ('<div class="pf-g">'
            + _tile("Visitors", p["visitors"], p["visitors_change"])
            + _tile("Page views", p["views"], p["views_change"])
            + _tile("Article views", p["article_views"], p["article_views_change"])
            + _tile("From AI answers", p["ai_visits"], p["ai_visits_change"])
            + '</div>'
            + _list("Most read articles", [(a.removeprefix("/articles/").strip("/") or a, b)
                                           for a, b in p["top_articles"]],
                    "No article views this week yet.")
            + _list("Where visitors came from", p["top_sources"],
                    "Every visit this week came direct, with no referring site.")
            + f'<p class="quiet ar-cap">From PostHog{_esc(site)}. Numbers refresh every '
              f'{posthog.CACHE_S // 60} minutes.</p>')
    return _page(PERFORMANCE, title, lede, _PH_CSS + body), 200
