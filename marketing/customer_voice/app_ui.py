"""Zernio's inbox screens on the box (#1990 Phase 1: step 1.3, the thread; 1.4 the list), behind a switch.

Owner, 2026-10-06, through OSDev1's review: "The new screens go behind a per-box switch, the owner's own box first; the old
inbox stays default until a parity checklist of the owner's rulings passes on both." So `inbox_screens`
(core/labs.py) is off on every box until HQ turns it on for one, and while it is off nothing here draws.

THEIR SCREENS, OUR BOX. Step 1.1 built their list, thread and composer into static files
(marketing/customer_voice/static/inbox-ui/, scripts/inbox_ui_build.sh). This serves those files, behind the inbox's
own sign-in like every /inbox/ path, and draws the page that mounts them. Their screens then ask the box's adapter
routes (app_api.py, step 1.2) for everything; the box alone calls Zernio.
"""
from __future__ import annotations

import hashlib
import html
import json
import pathlib
import re

from flask import Response, abort

from core import labs
from core.logging import get_logger

from .app import blueprint

log = get_logger(__name__)

LABS = "inbox_screens"
STATIC = pathlib.Path(__file__).resolve().parent / "static" / "inbox-ui"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(js|css|txt)")
_TYPES = {"js": "application/javascript", "css": "text/css", "txt": "text/plain"}


def on() -> bool:
    """Are Zernio's screens this box's inbox? Off unless HQ switched them on for it (core/labs.py)."""
    return labs.on(LABS)


def _version(name: str) -> str:
    """A file's content, short: the address changes when the build does, so a mobile never runs yesterday's."""
    try:
        return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"


@blueprint.get("/inbox/ui/<name>")
def ui_file(name: str):
    """One built file, by its bare name. ONE FOLDER, NOTHING ELSE: the route takes no slash, the name must look like a
    built file, and a file that isn't there is a 404. Chunks are named by their content, so they keep for a year;
    the entries and the styles are asked again each time, and the page asks for them by version."""
    if not _NAME.fullmatch(name):
        abort(404)
    path = STATIC / name
    if not path.is_file():
        abort(404)
    cache = "public, max-age=31536000, immutable" if name.startswith("chunk-") else "no-cache"
    return Response(path.read_bytes(), mimetype=_TYPES[name.rsplit(".", 1)[1]], headers={"Cache-Control": cache})


@blueprint.get("/inbox/api/conversations/<path:zcid>/saved-replies")
def ui_saved_replies(zcid: str):
    """SAVED REPLIES IN THEIR COMPOSER (#1990 1.3): this conversation's saved replies, their fill-ins filled for this
    person ({first_name}) and the one replying ({my_name}), so what fills the composer is exactly what goes. Reads only:
    picking fills the composer, and the person reads it and presses Send (the owner's pick, decision 3)."""
    from flask import jsonify, request

    from core import dash

    from .app import _space
    from .inbox import snippets, store
    conv = store.get_conversation(_space(), zcid)
    if not conv:
        abort(404)
    try:
        rows = snippets.all_for(_space())
    except Exception:                            # noqa: BLE001 — a list that can't be read costs the list only
        rows = []
    me = (dash.session_user(request) or {}).get("name") or ""
    from .app import _is_owner
    return jsonify({"replies": [{"id": str(r["id"]), "name": str(r["title"]),
                                 "words": snippets.fill(r["body"], participant=conv.get("participant"), my_name=me)}
                                for r in rows],
                    # NONE SAVED YET: the owner is shown where to save some, as the old reply box shows him.
                    "canAdd": bool(_is_owner())})


@blueprint.get("/inbox/api/signature")
def ui_signature():
    """THE EMAIL SIGNATURE, SAID BEFORE SEND (the parity checklist; the old reply box's `_signed_note`). The send path
    adds it (inbox/signature.py), never the composer, so this only reads it: the words, or null when there is none."""
    from flask import jsonify

    from .app import _space
    from .inbox import signature
    try:
        sig = signature.get(_space())
    except Exception:                            # noqa: BLE001 — a note that can't be read is not drawn
        sig = None
    return jsonify({"signature": sig or None})


@blueprint.get("/inbox/api/conversations/<path:zcid>")
def ui_conversation(zcid: str):
    """ONE CONVERSATION, FOR A LINK THE LIST CAN'T ANSWER (#1990 1.4). Their screens find a selected conversation in the
    list they loaded, so a link to one in Done, Trash or Junk, or past the list's first page, sat on "Loading
    conversation" for good. Their page asks here when its list doesn't hold it: the list's own shape, its draft too."""
    from flask import jsonify

    from . import app_api
    conv, missing = app_api._conv_or_404(zcid)
    if missing:
        return missing
    ready = {str(d["zcid"]): {"id": str(d["id"]), "body": str(d.get("body") or "")} for d in app_api._waiting()
             if str(d.get("zcid")) == str(zcid)}
    # THE LIST'S OWN ROW FOR IT, so its label, whether it waits and its tags are what the list would say (the details
    # panel reads them); the stored row alone when the list's query cannot answer.
    from .app import _space
    from .inbox import store
    try:
        listed = store.list_conversations(_space(), limit=1, zcid=str(zcid))
    except Exception as e:                       # noqa: BLE001 — the stored row still opens the thread
        # NEVER SILENT (OSDev1, #2026): the panel would read the bare row's "No label / Answered" without a trace. The
        # exception's type only: its message can carry the query's values, which are the person's.
        log.warning("inbox.ui_conversation_unlisted", error=type(e).__name__)
        listed = []
    return jsonify({"data": app_api._conversation(listed[0] if listed else conv, ready)})


def thread_address(conv: dict) -> str:
    """Where a conversation opens in their inbox: selected in the address, as their own page selects it
    (?conversation=<account>:<id>), so the list, the thread and the way back are theirs."""
    from urllib.parse import quote
    from . import app_api
    c = app_api._conversation(conv)
    return "/inbox/inbox?conversation=" + quote(f"{c['accountId']}:{c['id']}", safe="")


# THE LISTS A SWIPE PUTS THINGS IN (#1990 1.4, OSDev4's #2001): Done, Trash and Junk, each the list's ?status=.
VIEWS = (("archived", "Done"), ("deleted", "Trash"), ("junk", "Junk"))


def view_of(arg: str | None) -> str:
    """Which list the address asks for: "" is the inbox, else one of VIEWS."""
    return arg if arg in {k for k, _ in VIEWS} else ""


def has_conversations(space: str) -> bool:
    """Is there anyone in this inbox at all? The notification offer waits for someone to be told about (#1966)."""
    from .inbox import store
    try:
        return bool(store.list_conversations(space, limit=1))
    except Exception:                            # noqa: BLE001 — an unreadable store offers nothing
        return False


# THE PILLS THAT NARROW THE INBOX, and can be on together (box-pills.tsx): Drafts, Unanswered, Prospects.
NARROWS = (("drafts", "Drafts"), ("waiting", "Unanswered"), ("from_ad", "Prospects"))


def drafts_ready(space: str) -> int:
    """How many conversations in the inbox have a reply waiting: the Drafts pill's number, counted with the same SQL
    its list uses (store.count_among over drafter.store.waiting), or -1 when the box cannot say."""
    from .drafter import store as _drafts
    from .inbox import store
    try:
        return store.count_among(space, [str(d["zcid"]) for d in _drafts.waiting(space, limit=1000)])
    except Exception:                            # noqa: BLE001 — a missing number is not a 500
        return -1


def pills(space: str, counts: dict, waiting: bool, from_ad: bool, view: str, drafts: bool = False) -> list[dict]:
    """THE BOX'S PILLS, FOR THEIR FILTER ROW (owner, 2026-10-09, on the phone's inbox: "these things should be
    consolidated right into the slider, horizontal menu, filtering menu"): Drafts, Unanswered and Prospects first, then
    their platform menu and sort, then Done, Trash and Junk ("Please move done and trash to the very end"). Patch 0010
    and ownbox/box-pills.tsx draw them.

    DRAFTS IS THE LINE THAT SAT ABOVE THE LIST ("61 drafts ready to send Should be Drafts pill"), now a filter with its
    number. ALL IS GONE ("All - Delete this one"): the pill that is on turns itself off.

    A PILL THAT CANNOT CHANGE THE SCREEN IS NOT OFFERED, the rule app._pills keeps: each shows when its list holds
    someone, or while it is on (answering the last one must not take the pill from under the thumb). In Done, Trash or
    Junk the narrowing pills lead back to the inbox."""
    from .inbox import store
    n = {"drafts": drafts_ready(space), "waiting": counts.get("waiting", 0), "from_ad": counts.get("from_ad", 0)}
    chosen = {"drafts": drafts, "waiting": waiting, "from_ad": from_ad}
    out = []
    for key, name in NARROWS:
        on = chosen[key] and not view
        if on or n[key] > 0:
            out.append({"key": key, "label": name, "on": on, **({"count": n[key]} if key == "drafts" and n[key] > 0
                                                                else {})})
    for key, name in VIEWS:
        on = view == key
        if not on:
            try:
                if not store.list_conversations(space, limit=1, view=key):
                    continue
            except Exception:                    # noqa: BLE001 — a list that can't be counted is not offered
                continue
        out.append({"key": key, "label": name, "on": on})
    return out


def inbox_body(pills: list[dict] | None = None) -> str:
    """THE LIST IS THEIRS (#1990 1.4), with the open thread beside it on a desktop, inside the inbox's own frame: the
    box's menu on a desktop, the bottom bar on a mobile. Selection is in the address (?conversation=account:id), so
    a link to a conversation opens it. The box's pills (`pills` above) ride on it, for their filter row."""
    css, js = f"/inbox/ui/inbox-ui.css?v={_version('inbox-ui.css')}", f"/inbox/ui/inbox.js?v={_version('inbox.js')}"
    return (f'<link rel="stylesheet" href="{css}">'
            # THE FRAME STOPS RESERVING A SCREEN OF ITS OWN: its layout box is a full screen tall below a header, which
            # left a page that scrolls by the header's height, and a focus scrolled it. The inbox sizes itself.
            '<style>#ib-inbox{min-height:320px;display:flex;flex-direction:column}.lay{min-height:0}'
            # THEIR SPACING STOPS GROWING WITH THE PHONE'S TEXT SIZE (owner, 2026-10-10: "Overall, there is just a ton of
            # white space"). Their every padding, gap and height is a multiple of --spacing, a quarter of the root's
            # size, and on an iPhone the root is the reader's text size (box.css), so a phone set to larger text got
            # 70px buttons and headers. Fixed at 4px, what it is at the default everywhere else; their text still
            # follows the reader.
            ':root{--spacing:4px}'
            # THE INBOX TAKES THE WINDOW ON A DESKTOP (owner, 2026-10-06: "widen the message"): the old list's 780px
            # column held one list; this holds the list and the conversation beside it, so the conversation was
            # squeezed under 400px. Up to 1600px, so a wide monitor does not stretch a line across a room.
            '@media (min-width:821px){.ib .wrap{max-width:1600px}}'
            # ONE ROW OF FILTERS THAT SCROLLS SIDEWAYS (owner, 2026-10-09: "the slider, horizontal menu, filtering
            # menu"): the box's pills, then their platform menu and sort (patch 0010), running to the card's edges
            # as it scrolls, its scrollbar hidden as a phone's would be.
            '#ib-inbox [data-ownbox-filters]{overflow-x:auto;scrollbar-width:none;margin:0 -12px;padding:2px 12px}'
            '#ib-inbox [data-ownbox-filters]::-webkit-scrollbar{display:none}'
            '#ib-inbox [data-ownbox-filters]>*{flex:none}'
            # AN EMAIL'S LINKS (render.readable's own <a>) look like links.
            '#ib-inbox .ownbox-mail a,#ib-inbox details a{color:var(--link);text-decoration:underline}'
            # ONE SEARCH AT EVERY WIDTH (owner, #1977): on a phone the bar's, so theirs in the list is not drawn; on a
            # desktop, where the box's bar hides, theirs (which asks the box, patch 0004).
            '@media (max-width:820px){#ib-inbox .ib-list-search{display:none}}'
            # THE LIST RUNS EDGE TO EDGE ON A PHONE (owner, 2026-10-09: "No padding on the left and right side please.
            # Right now there's padding that is wasted space"). Anything else on the page (the stopped note, the
            # notification offer, other machines' cards) keeps the page's gutter.
            '@media (max-width:820px){.wrap:has(>#ib-inbox){padding-left:0;padding-right:0}'
            '.wrap:has(>#ib-inbox)>:not(#ib-inbox){margin-left:16px;margin-right:16px}}'
            # A ROW'S ACTIONS FOR A SCREEN READER OR A KEYBOARD (list-actions.tsx): drawn nowhere until a keyboard
            # reaches them, then where the mouse's bar sits.
            '#ib-inbox .ib-acts{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;'
            'clip:rect(0,0,0,0);white-space:nowrap;border:0}'
            '#ib-inbox .ib-acts:focus-within{width:auto;height:auto;margin:0;overflow:visible;clip:auto;right:8px;'
            'top:50%;transform:translateY(-50%);display:flex;gap:4px;align-items:center;padding:4px;'
            'border:1px solid var(--chat-border);border-radius:10px;background:var(--chat-surface)}'
            '#ib-inbox .ib-acts button{display:flex;align-items:center;gap:4px;padding:0 10px;border-radius:8px;'
            'font-size:13px;color:var(--ink)}'
            '#ib-inbox .ib-acts select{border-radius:8px;padding:0 8px;background:transparent;color:var(--ink)}'
            # THEIR TOASTS (Moved to Done, Undo) IN THE BOX'S COLOURS, light and dark: theirs are always light.
            '#ib-inbox [data-sonner-toaster]{--normal-bg:var(--card);--normal-text:var(--ink);'
            '--normal-border:var(--card-edge)}'
            # INSIDE A CONVERSATION, ON A MOBILE, THE COMPOSER TAKES THE BOTTOM: the bottom bar and its room step aside.
            # 48px TARGETS AND 16px FIELDS ON A MOBILE (the owner's ruling, every inbox screen): their controls are
            # 32px and their small type, so a thumb gets the size the old screens give it, and a field never zooms.
            '@media (max-width:899px){#ib-inbox :is(button,[role=button],[role=combobox],select,textarea,'
            'input:not([type=hidden],[type=checkbox],[type=radio])){min-height:48px}'
            '#ib-inbox :is(button,[role=button]){min-width:48px}'
            # ...BUT THE DRAFT'S PILLS ARE 36px TO THE EYE (owner, 10-10: "much smaller round pill buttons"): an
            # invisible ring above and below each one keeps the thumb's 48px (ownbox/draft-card.tsx).
            '#ib-inbox [data-ownbox-draft] button{min-height:36px}#ib-inbox [data-ownbox-draft] p+button{min-height:0;min-width:0}'
            # ...AND THE FILTER ROW'S PILLS ARE 32px, Material 3's filter chip (owner, 10-10: "everything needs to be tight
            # on the screen"), with the same kind of ring: 8px above and below makes the thumb's 48px.
            '#ib-inbox [data-ownbox-filters] :is(a,button){min-height:32px;position:relative}'
            '#ib-inbox [data-ownbox-filters] :is(a,button)::after{content:"";position:absolute;inset:-8px 0}'
            '#ib-inbox :is(input,textarea,select){font-size:max(16px,1em)}'
            'html.ib-thread-open nav.tabs{display:none}'
            'html.ib-thread-open body{padding-bottom:0}html.ib-thread-open :is(#ownbox-notify,.ib-below)'
            '{display:none}}'
            # A MESSAGE GETS THE PHONE'S SCREEN (owner, 2026-10-07, with Gmail's app as the model: "only about 10% of
            # the screen is the actual message"). Inside a conversation on a phone: no search bar (the thread's own
            # header, its back arrow first, is the top of the screen), no gutter around the page, only a touch of room
            # around the messages, and an email edge to edge, without the card's border.
            '@media (max-width:820px){html.ib-thread-open .bar-find{display:none}'
            'html.ib-thread-open .wrap{padding-left:0;padding-right:0}'
            r'html.ib-thread-open #ib-inbox .max-w-\[900px\].space-y-3{padding:6px 4px}'
            r'html.ib-thread-open #ib-inbox .max-w-\[760px\]{max-width:none;border:0;border-radius:0;'
            'padding:10px 8px;box-shadow:none}'
            # WHAT HE WRITES GETS THE WIDTH: with the saved replies, emoji, attach and send buttons in its row, the
            # field was 125 of 351px and a reply wrapped into a narrow column. Empty, the row stays one line; with
            # words in it, the field takes the full width and the buttons sit on the line below.
            r'#ib-inbox div.items-end:has(> textarea[aria-label="Message"]:not(:placeholder-shown))'
            '{flex-wrap:wrap;justify-content:flex-end}'
            r'#ib-inbox textarea[aria-label="Message"]:not(:placeholder-shown){flex:1 1 100%}'
            # ...AND EMPTY, IT READS ON ONE LINE: four 48px buttons left the field 125px on Instagram and Facebook, so
            # "Type a message..." broke in two. The emoji button steps aside on a phone, as in Gmail's app: the
            # phone's own keyboard has every emoji.
            '#ib-inbox button[aria-label="Insert emoji"]{display:none}}'
            # THEIR COMPOSER'S FIELD IS THE COMPOSER, in dark too: its own dark tint drew a second box inside it.
            'html.dark #ib-inbox textarea{background-color:transparent}</style>'
            f'<div id="ib-inbox" data-pills="{html.escape(json.dumps(pills or []), quote=True)}"></div>'
            # THE INBOX FILLS WHAT THE FRAME LEAVES: below the header, above the bottom bar's room, measured, never
            # assumed (a notch, a larger text size), again on a resize and when a conversation opens or closes.
            # THEIR DARK FOLLOWS THE BOX'S (#1990 1.5): their `dark:` styles key on a .dark class, the box's choice is
            # data-theme on <html> (core/dash/theme.py), so one mirrors the other, now and when it changes.
            '<script>(function(){var root=document.documentElement,el=document.getElementById("ib-inbox");'
            'function dark(){root.classList.toggle("dark",root.getAttribute("data-theme")==="dark");}'
            'function fit(){var top=el.getBoundingClientRect().top+window.scrollY;'
            'var pb=parseFloat(getComputedStyle(document.body).paddingBottom)||0;'
            'el.style.height="calc(100dvh - "+(top+pb)+"px)";'
            # OTHER MACHINES' CARDS START BELOW THE SCREEN, not in the room kept above the bottom bar, where their top
            # edge showed under the list: below the conversations, reached by scrolling (core/panels.py).
            'var b=document.querySelector(".ib-below");if(b)b.style.marginTop=pb?pb+"px":"";}'
            'dark();fit();addEventListener("resize",fit);'
            # ...AND AGAIN WHEN ANYTHING ABOVE IT CHANGES SIZE: the notification offer appears once the page asks.
            'if(window.ResizeObserver){var ro=new ResizeObserver(fit),s=el.previousElementSibling;'
            'while(s){ro.observe(s);s=s.previousElementSibling;}}'
            'new MutationObserver(function(){dark();fit();}).observe(root,'
            '{attributes:true,attributeFilter:["class","data-theme"]});'
            '})();</script>'
            f'<script type="module">import {{ mountInbox }} from "{js}";'
            'mountInbox(document.getElementById("ib-inbox"));</script>')
