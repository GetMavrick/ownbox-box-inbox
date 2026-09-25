"""The SEO machine's two screens: Topics (what to write) and Settings (where, and what it may say).

MOUNTED THROUGH CONFIG `web_modules:`, like every machine's pages, so core never imports this file.
It draws with core's `chrome()` — the same frame, rail and controls as System Settings — rather than
a look of its own, because a machine that brings its own stylesheet is a second design to keep in
step with the first.

EVERY SETTING IS WRITTEN THROUGH `settings.py`'S NAMES, into `core.box_settings` under machine
`seo`, and read back through `settings.get()` — the one accessor the publisher and the writer read.
There is no second settings module and no key name that exists only here (OSDev6, 2026-09-25). The
Sanity token is the one exception, and only because it is a credential: it goes to `box_secrets` as
`SANITY_API_TOKEN_OWNBOX`, the name the publisher reads, and is never shown again.

OWNER ONLY TO CHANGE, ANYONE SIGNED IN TO READ. This screen decides what gets published on the
business's own website under its name, which is the same kind of decision as the inbox's
connections, and those are the owner's (owner, 2026-09-24: "Owner only"). Extending that ruling to
this machine is a proposal, not yet owner-approved.

DARK UNTIL SET UP. Until the box has a website, a Sanity project and a token, Topics says what is
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

from . import plan, settings

log = get_logger(__name__)

blueprint = Blueprint("seo_machine_app", __name__)

HOME = "/seo"
TOPICS = "/seo/topics"
SETTINGS = "/seo/settings"
GOOGLE = "/settings/seo/google"          # core's own screen (core/dash/google_search.py)

# THE PUBLISHER READS THIS NAME. It is the owner's (OSDev6, 2026-09-25: "Token name stays the
# owner's SANITY_API_TOKEN_OWNBOX"), and tests/test_seo_machine_shell.py holds the two together.
TOKEN = "SANITY_API_TOKEN_OWNBOX"

# A MAGNIFIER, drawn as one path like every other rail icon (core/shell.py draws exactly one).
_ICON = "M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13ZM15.5 15.5 20 20"

# THE SECTION IS `/seo`, NOT ITS FIRST SCREEN. `core.shell` gives a top-level section every page
# under its own address and none outside it, so a section at /seo/topics would leave /seo/settings
# with no menu and no breadcrumb. /seo itself only redirects to Topics.
shell.register_section(
    "seo", order=20, machine="seo_machine", title="SEO", href=HOME, icon=_ICON,
    items=[
        {"key": "topics", "label": "Topics", "href": TOPICS},
        {"key": "settings", "label": "Settings", "href": SETTINGS},
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
    if not s.get("project_id"):
        out.append("your Sanity project ID")
    if not box_secrets.get(TOKEN):
        out.append("your Sanity API token")
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
    ("project_id", "Sanity project ID", "text", "abc123xy",
     "In Sanity, open your project. The ID is under its name."),
    ("dataset", "Sanity dataset", "text", "production",
     "Leave it as production unless your site reads a different one."),
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

_PROJECT_RE = re.compile(r"^[a-z0-9]{4,32}$")
_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
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


def _save(form, *, user_id: str) -> None:
    """Check everything first, then write. A form that fails half way writes nothing."""
    writes: dict = {}
    site, host = _site(form.get("site_url") or "")
    writes["site_url"], writes["host"] = site, host
    project = (form.get("project_id") or "").strip().lower()
    if project and not _PROJECT_RE.match(project):
        raise _Refused("A Sanity project ID is letters and numbers only, like abc123xy.")
    writes["project_id"] = project
    dataset = (form.get("dataset") or "").strip().lower()
    if dataset and not _DATASET_RE.match(dataset):
        raise _Refused("A dataset name is lowercase letters, numbers, dashes and underscores.")
    writes["dataset"] = dataset
    key = (form.get("indexnow_key") or "").strip()
    if key and not _INDEXNOW_RE.match(key):
        raise _Refused("An IndexNow key is 8 to 128 letters, numbers and dashes.")
    writes["indexnow_key"] = key
    for name, _label, _help in _LISTS:
        writes[name] = _lines(form.get(name) or "")
    token = (form.get("token") or "").strip()
    if token:
        if any(ch.isspace() for ch in token) or len(token) < 20:
            raise _Refused("That does not look like a Sanity token. Copy it again from Sanity.")

    # EMPTY MEANS "BACK TO THE DEFAULT", so an empty field clears the box's row rather than pinning
    # "" over a default that may change (core/box_settings.py `clear`).
    for name, value in writes.items():
        if name not in settings.DEFAULTS:
            raise RuntimeError(f"{name} is not one of settings.py's keys")
        if value in ("", []):
            box_settings.clear(settings.MACHINE, name)
        else:
            box_settings.put(settings.MACHINE, name, value, set_by=user_id)
    if token:
        box_secrets.put(TOKEN, token, user_id=user_id)
    log.info("seo.settings_saved", user=user_id, token_changed=bool(token))


# ── the screens ───────────────────────────────────────────────────────────────────────────────

_SAID = {
    "saved": ("Saved", "The SEO machine uses these from its next article.", True),
    "added": ("Topic added", "It is in the plan below.", True),
    "now": ("Next up", "Writing starts within a minute or so. It takes a few minutes, then this "
                       "page shows whether it went live.", True),
    "gone": ("That topic cannot go now", "It is already being written or is live.", False),
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
    return ('<div class="card"><h2>Not set up yet</h2>'
            f'<p>Before it can publish, the SEO machine needs {_esc(_and(need))}.</p>'
            f'<div class="foot"><a href="{SETTINGS}">Open SEO settings &rarr;</a></div></div>')


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


def _topic_row(row: dict, *, can_go: bool) -> str:
    q = f'<p class="sub">{_esc(row["question"])}</p>' if row.get("question") else ""
    btn = ""
    if can_go and row.get("status") in ("planned", "refused", "failed") and not (
            row.get("status") == "planned" and row.get("requested_at")):
        label = "Write and publish now" if row["status"] == "planned" else "Try again now"
        btn = _post(TOPICS, "now", label, cls="ghost",
                    extra=f'<input type="hidden" name="id" value="{int(row["id"])}">')
    return (f'<div class="card"><h2>{_esc(row["topic"])}</h2>{q}'
            f'<p>{_status(row)}</p>{btn}</div>')


def _week_full() -> bool:
    """Has this box published its weekly number in the last seven days?"""
    from datetime import timedelta, timezone
    try:
        cap = int(settings.get().get("weekly_cap") or 0)
    except (TypeError, ValueError):
        cap = 0
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return plan.published_since(since) >= cap


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
    title, lede = "Topics", "What the SEO machine writes about, and what became of each article."
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
            return redirect(f"{TOPICS}?said={'now' if ok else 'gone'}", code=303)
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
    elif ready:
        cap = settings.get().get("weekly_cap") or 0
        full = (' This week\'s are out. The next goes live once the oldest of them is a week '
                'old.') if _week_full() else ""
        body += ('<div class="card"><p>Up to '
                 f'{_esc(cap)} articles a week are written from this list, oldest first, and each '
                 f'is checked against your settings before it goes live.{_esc(full)}</p></div>')
    if owner:
        body += ('<div class="card"><h2>Add a topic</h2>'
                 f'<form method="post" action="{TOPICS}"><input type="hidden" name="do" value="add">'
                 '<label for="topic">Topic</label>'
                 f'<input id="topic" name="topic" maxlength="{plan.TOPIC_MAX}" required '
                 f'placeholder="How often to service a boiler" value="{_esc(typed[0])}">'
                 '<label for="question">The question it answers (optional)</label>'
                 f'<input id="question" name="question" maxlength="{plan.QUESTION_MAX}" '
                 f'placeholder="How often should I service my boiler?" value="{_esc(typed[1])}">'
                 '<button type="submit">Add topic</button></form></div>')
    else:
        body += ('<div class="card"><p>Only the owner of this box can add topics or publish.'
                 '</p></div>')
    rows = plan.rows()
    if rows:
        body += "".join(_topic_row(r, can_go=owner and ready and writer) for r in rows)
    else:
        body += ('<div class="card"><h2>No topics yet</h2><p>Each topic becomes one article that '
                 'answers a question your customers search for.</p></div>')
    return _page(TOPICS, title, lede, body)


def _settings_form(typed=None) -> str:
    """The owner's form. `typed` is a refused POST, so a mistake does not wipe what was entered."""
    s = dict(settings.get())
    if typed is not None:
        for name, *_rest in _FIELDS:
            s[name] = typed.get(name) or ""
        for name, *_rest in _LISTS:
            s[name] = (typed.get(name) or "").splitlines()
    token_set = box_secrets.is_set(TOKEN)
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
    out.append('<div class="card"><h2>Where articles are published</h2>'
               '<p class="sub">Your website reads its articles from Sanity. The SEO machine writes '
               'them there.</p>' + field(*f["project_id"]) + field(*f["dataset"])
               + '<label for="token">Sanity API token</label>'
               '<input id="token" name="token" type="password" autocomplete="off" '
               f'placeholder="{"Saved. Leave blank to keep it" if token_set else "sk..."}">'
               '<p class="sub">In Sanity, open API, then Tokens, and add one with Editor '
               'access. It is stored on this box and never shown again.</p></div>')
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
            ("Sanity project", s.get("project_id") or "Not set"),
            ("Sanity token", "Saved" if box_secrets.is_set(TOKEN) else "Not set")]
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
    lede = "Where articles are published, and what the writer may and may not say."
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
