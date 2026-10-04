"""The box's own set-up doors — the three the inbox was only ever holding for it.

WHY THESE MOVED, IN THE OWNER'S WORDS. 2026-09-22, after connecting his AI account from System
Settings and being thrown into the inbox's wizard to finish: *"we need to find tune our wizards.
Step four and five should not be in this wizard any longer"*, and earlier the same session,
*"Step three and step four should be a core setting. The LLM and the phone."* (his words; the
step is named "mobile app" everywhere a buyer reads it — see the naming rule below)

THE SCREEN HALF OF THAT WAS A TWO-LINE CHANGE AND IT WAS REVERTED, correctly, by OSDev5: the
machine's set-up screen is the box's set-up CONTRACT made visible, so it cannot show a subset of
it (`docs/SCOPE_BOX_STEPS_NEED_CORE_DOORS.md`). The contract had to be split, and a box step
cannot leave the machine's wizard while the wizard is the only place it can be finished. All
three were: the AI account at a sign-in route the inbox served, the AI coworkers at the inbox's
own screen, and the mobile app at NO DOOR AT ALL — its instructions were prose on the wizard and its
two endpoints were the inbox's.

So this file is the other half. It is option C of that scope doc, chosen by OSDev1: the doors
move into core rather than core learning to point at a machine's. Nothing here is the inbox's
business and none of it ever was — `core.brain` is what an AI account feeds, `core.connector` is
what a coworker seat opens, and the app on somebody's mobile is theirs rather than a product's. A Lead box
with no inbox on it needs all three and, before today, could reach none of them.

WHAT DID NOT MOVE, AND WHY IT COULD NOT. The permission prompt stays where the service worker is.
`navigator.serviceWorker.ready` resolves against the registration whose scope covers THE PAGE YOU
ARE ON, and this box's worker is registered at a machine's path with a scope to match. Called from
`/settings` that promise never settles — no error, no rejection, a button that spins forever. The
two ENDPOINTS are core's now (`core.push.CLIENT_JS` fetches them from wherever it is loaded); the
asking waits for a root-scoped worker, which is a separate change with its own blast radius.
"""

# NO WORD HERE MAY COLLIDE WITH A VOICE PRODUCT. Owner, 2026-09-22: *"It's a mobile app with
# notifications"*, and *"don't use any terms that will collide with a voice/phone product"* — the
# AI receptionist machine is coming to `/voice`, and a buyer who reads "your phone" on a settings
# screen will reasonably think it is about calls. So: **mobile app**, **install**, **notify** /
# **notification**. Never phone (except naming the device in install steps — "On iPhone"), never
# ring, call, dial, line, voice or answer. `tests/test_no_voice_words_on_the_app.py` enforces it.

import html as _html
import json
import os
import re
import subprocess

from flask import jsonify, redirect, request

from core.dash import blueprint
from core import setup_errors
from core.dash.home import chrome
from core.logging import get_logger

log = get_logger(__name__)


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def _admit(*, owner_only: bool):
    """The same gate the rest of the drawer uses, never a second copy of it.

    `core.dash.review._admit` is where this question is answered for every core screen. Mirroring
    a gate rather than calling it is how `/app/review` shipped open on five public labels in
    September; there is one of these on the box and this is it.
    """
    from core.dash import review as _review
    return _review._admit(owner_only=owner_only)


def _who() -> dict:
    from core import dash
    try:
        return dash.session_user(request) or {}
    except Exception:                                # noqa: BLE001 — an unreadable session decides
        return {}                                    # only whose name an audit line carries


def _is_owner() -> bool:
    return (_who().get("role") or "") == "owner"


def _step(key: str) -> dict:
    """One entry of the box's contract, live. `{}` if this box does not carry it.

    A BOX MID-SELF-UPDATE LEGITIMATELY CARRIES FEWER, so every caller here draws a sentence rather
    than a traceback when a step is missing.
    """
    from core import box_secrets
    try:
        for e in box_secrets.setup_state():
            if str(e.get("key")) == key:
                return e
    except Exception:                                # noqa: BLE001 — a settings screen never 500s
        return {}
    return {}


def _back() -> str:
    """Every one of these screens ends where it was opened from. Owner's acceptance, in OSDev5's
    words: connecting the AI account from System Settings leaves you on System Settings."""
    return '<div class="foot"><a href="/settings">&larr; Settings</a></div>'


# ── the AI account ───────────────────────────────────────────────────────────────────────────────

# THE ONE LINE THAT SAYS WHAT CONNECTING NEEDS. Read by its test, so the words live once.
AI_NEEDS = "Needs a Claude or ChatGPT subscription, or an API key."


@blueprint.route("/settings/ai", methods=["GET", "POST"])
def box_ai():
    """Sign in to Claude, or paste a key. Owner, 2026-09-18: "There's no key. It's a login."

    TWO WAYS IN, ONE STEP. The button runs `claude setup-token` on the box and hands back a link;
    the field below takes an Anthropic API key or a subscription token for anybody who already has
    one. `put_ai_credential` reads the prefix and routes, so a buyer never has to tell a form which
    of the two they are holding.

    THE BOX NEVER SEES A PASSWORD. It sees a short code that is useless to anyone else and is spent
    the moment it is used.

    OWNER-ONLY, AND THE ROW ON /settings KNOWS THAT WITHOUT ASKING — the step declares
    `owner_only`, so a member is never offered this door and then refused at it.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import box_secrets, claude_login

    who = _who()
    if (who.get("role") or "") != "owner":
        return chrome("/settings/ai", title="AI Account",
                      lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can connect an AI '
                           'account: it bills their subscription and every machine on the box '
                           'drafts through it.</p></div>' + _back()), 403

    note, url = "", ""
    if request.method == "POST":
        action = str(request.form.get("do") or "")
        try:
            if action == "start":
                # THE TICK TRAVELS WITH THE LOGIN IT BELONGS TO. See `claude_login.start`.
                url = claude_login.start(consented=bool(request.form.get(
                    str(_step("anthropic").get("consent_field") or "subscription_consent"))))
            elif action == "code":
                claude_login.finish(str(request.form.get("code") or ""), user_id=who.get("id"))
                # CHECKED THE MOMENT IT'S SAVED (owner, 2026-10-01: a sign-in the page called connected was refused
                # by Anthropic on every draft). One tiny question; the page then says working, or exactly why not.
                from core import ai_health
                ai_health.test(fresh=True)
                return redirect("/settings/ai?tested=1", code=303)
            elif action == "cancel":
                claude_login.cancel()
                return redirect("/settings/ai", code=303)
            elif action == "test":
                # ONE TINY REAL QUESTION, THROUGH THE PATH DRAFTS USE (core/ai_health.py). Owner, 2026-10-01: "It
                # says it's connected, but I can't tell if it's using inference." The answer is kept, so the
                # page shows it after the redirect.
                from core import ai_health
                ai_health.test()
                return redirect("/settings/ai?tested=1", code=303)
            elif action == "key":
                # ONE FIELD, EITHER CREDENTIAL — the router decides, not the buyer and not a radio
                # button. An sk-ant-oat… token is stored as a subscription and selects the
                # claude_code backend; an API key takes the verified path it always did.
                box_secrets.put_ai_credential(
                    str(request.form.get("key") or ""),
                    consented=bool(request.form.get(
                        str(_step("anthropic").get("consent_field") or "subscription_consent"))),
                    user_id=who.get("id"))
                from core import ai_health                         # checked the moment it's saved, as above
                ai_health.test(fresh=True)
                return redirect("/settings/ai?tested=1", code=303)
        except claude_login.LoginError as e:
            # THE SENTENCE IS THE PRODUCT HERE. `claude_login` raises only things a person can act
            # on and never quotes the CLI's transcript at them — the transcript carried the
            # code_challenge.
            note = str(e)
        except box_secrets.SecretRejected as e:
            note = str(e)

    # COMING BACK IS THE NORMAL CASE, NOT AN EDGE ONE. Signing in happens in another tab, and
    # somebody who reloads this one was being offered a Connect button for a login already
    # running — which replaced the login whose code they were holding.
    if not url:
        try:
            url = claude_login.pending_url()
        except Exception:                            # noqa: BLE001 — no pending login is an answer
            url = ""

    e = _step("anthropic")
    picked = _picked(e)

    # CONNECTED COMES FIRST, AND SAYS WHICH ACCOUNT. System Settings reads "Your AI account:
    # Connected", and this page, one tap later, drew a fresh Connect screen with the terms tick and
    # a paste field, as if nothing were set up: it never asked. It now asks the same reader the
    # row does, says what the drafts are written with, and folds the ways to connect under "Use a
    # different account". Not while a sign-in is running, after a refusal, or when the buyer has
    # picked another model: then they are mid-change and the cards are the page.
    try:
        ai = box_secrets.anthropic_state()
    except Exception:                                # noqa: BLE001 — a status read never 500s this page
        ai = {}
    settled = (ai.get("status") == "connected" and not url and not note
               and request.method == "GET" and not request.args.get("model"))

    def page(parts: list, lede: str):
        if settled:
            # NO AI IS NAMED (owner, 2026-10-03: "Please adjust to not mention which LLM. We're also going to add
            # Gemini and Grok very soon."): a subscription or a key, on the buyer's own account.
            kind = ("AI subscription" if ai.get("provider") == "chatgpt" or box_secrets.claude_oauth_token()
                    else "AI key")
            parts = (['<div class="card"><h2>Connected</h2>'
                      f'<p>Your drafts are written with your {_esc(kind)}, on your own account '
                      'and your own bill.</p>' + _ai_health_lines() + '</div>',
                      '<details class="fold ai-other"><summary>Use a different account</summary>']
                     + parts + ['</details>', _back()])
            lede = "What writes your drafts, and how to change it."
        else:
            parts = parts + [_back()]
        return chrome("/settings/ai", title="AI Account", lede=lede,
                      body="".join(parts)), 200

    body = ['<div class="card"><p>' + _esc(e.get("why") or
            "This is what writes your replies, on your own account and your own bill.")
            # WHAT YOU NEED, BEFORE ANY CHOICE. A buyer with none of the three read the whole page
            # and learned only that we could do it for them (walk #3, 2026-09-23). One line, at
            # the top, whichever model is picked below.
            + '</p><p class="quiet">' + _esc(AI_NEEDS) + '</p></div>', _picker(e, picked)]
    if note:
        body.append(setup_errors.card("ai", note))       # names the fix, links the page (plan #1857 launch bar 1)

    # A MODEL THIS BOX CANNOT DRAFT WITH GETS THE WHOLE SCREEN TO ITSELF AND NO FIELD ON IT.
    # Owner, 2026-09-22: *"the page is gonna have to be completely different according to which
    # drop-down is chosen."* It is — and the half that must not vary is that nothing here will
    # take a credential it cannot use.
    if not picked.get("available"):
        body.append(_preview(picked, _models(e)))
        return page(body, f"What {picked.get('name')} would look like on this box.")

    # A LIVE MODEL CONNECTED SOMEWHERE ELSE GETS ITS OWN DOOR, AND NONE OF CLAUDE'S CARDS.
    # This is the half of the owner's ruling that was missing while ChatGPT was still greyed out:
    # picking it changed the heading and then drew the Claude sign-in underneath, which is the
    # screen lying about what the button in front of you does. Every card below this line is
    # Anthropic's — the link, the code field, the sk-ant… form — so a model that connects
    # elsewhere returns before any of them is built.
    if (picked.get("connect_href") or "/settings/ai") != "/settings/ai":
        body.append(_door(picked))
        return page(body, f"Sign in with {picked.get('name')}.")

    if url:
        body.append(
            '<div class="card">'
            # WALK #4: SAY WHERE YOU GO AND HOW YOU COME BACK. Claude's sign-in shows a code to
            # paste rather than returning to the box, so the round trip is the buyer's to make;
            # the page's job is to make it obvious, and to still be here when they return (it
            # keeps the login running across a reload, see "COMING BACK" above).
            '<p><b>1.</b> Sign in to Claude and approve access. Claude opens in a new tab; this '
            'page stays here and waits for you.</p>'
            f'<p style="margin:12px 0"><a href="{_esc(url)}" target="_blank" '
            'rel="noopener noreferrer">Sign in to Claude &rarr;</a></p>'
            # THE LINK IS ENOUGH (#1483, finding 5). The raw URL is about 400 characters of query
            # string, and printed under the button it was the loudest thing on the screen. It stays
            # one tap away, folded, for the phone whose app will not open links in a browser.
            '<details class="quiet"><summary>Link not opening? Copy it instead</summary>'
            f'<p style="word-break:break-all;margin:8px 0 0">{_esc(url)}</p></details>'
            '<p><b>2.</b> Claude shows you a short code. Copy it, come back to this tab and '
            'paste it here.</p>'
            '<p class="quiet">Using the box as an app on your mobile? Claude opens in your '
            'browser. When you have the code, switch back to the app: it keeps your place.</p>'
            '<form method="post" action="/settings/ai">'
            '<input type="hidden" name="do" value="code">'
            '<input name="code" autocomplete="off" spellcheck="false" '
            'aria-label="Paste the code from Claude" placeholder="Paste the code from Claude">'
            '<button type="submit">Finish</button></form>'
            '<form method="post" action="/settings/ai" style="margin-top:10px">'
            '<input type="hidden" name="do" value="cancel">'
            '<button class="ghost" type="submit">Cancel</button></form>'
            '</div>')
    else:
        # THE LINK IS NOT DRAWN BEFORE IT EXISTS. Starting the login takes a few seconds and can
        # fail; a button saying "Sign in to Claude" that leads nowhere is the dead end this whole
        # screen was written to remove.
        body.append(
            '<div class="card"><h2>Use your Claude subscription</h2>'
            f'<p>{_esc(e.get("action_why") or "")}</p>'
            '<form method="post" action="/settings/ai">'
            '<input type="hidden" name="do" value="start">'
            # WHERE THE OWNER PUT IT, AND WHERE IT WAS ALWAYS ABOUT. The sentence says "use my
            # SUBSCRIPTION with this box"; it sat in the paste-a-key card, which is the one place
            # on this screen a subscription is not what you are connecting.
            + _consent(e) +
            '<button type="submit">Connect</button></form>'
            # SAID ONCE. The line above already says the box never sees the password; this one
            # used to say it again, two sentences apart, in the same card.
            # WHAT HAPPENS NEXT, BEFORE IT HAPPENS (walk #4). A buyer who knows they will leave
            # and come back does not read the new tab as the box breaking.
            '<p class="quiet" style="margin-top:12px">Next: Claude opens in a new tab. You sign '
            'in there and Claude shows a short code, which you paste back on this page. It takes '
            'about a minute.</p></div>')

    # THE SECOND DOOR, under the first, only when the step declares one (a box mid-update may not).
    if e.get("alt_action_href"):
        body.append(
            '<div class="card"><h2>Or use your ChatGPT subscription</h2>'
            f'<p>{_esc(e.get("alt_action_why") or "")}</p>'
            f'<p><a href="{_esc(e.get("alt_action_href"))}">'
            f'{_esc(e.get("alt_action_label") or "Sign in to ChatGPT")} &rarr;</a></p></div>')
    body.append(_key_form(e))
    return page(body, "What writes your drafts, on your own account.")


@blueprint.route("/settings/chatgpt", methods=["GET", "POST"])
def box_chatgpt():
    """Sign in to ChatGPT from the box: a link and a one-time code, entered on the buyer's OWN
    device. Owner, 2026-09-21: "We need the Claude login. And then we're gonna need the ChatGPT
    login right behind it."

    THE MIRROR IMAGE OF /settings/ai. There, the person brings a code back to the box; here, the
    box hands them a code to take to ChatGPT, and the CLI on the box waits for ChatGPT to say it
    was entered. So this page has no field at all: it shows the link and the code, and checks
    itself every few seconds until the sign-in is done. Nothing secret crosses this page.
    """
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome("/settings/ai", title="AI Account",
                      body='<div class="card"><p>Only the owner of this box can connect an AI '
                           'account.</p></div>' + _back()), 403
    from core import codex_login
    who = _who()
    note, live = "", {}
    if request.method == "POST":
        action = str(request.form.get("do") or "")
        try:
            if action == "start":
                # THE TICK TRAVELS WITH THE SIGN-IN IT IS ABOUT. See `codex_login.start`.
                live = codex_login.start(
                    consented=bool(request.form.get(
                        str(_step("anthropic").get("consent_field") or "subscription_consent"))),
                    user_id=who.get("id"))
            elif action == "cancel":
                codex_login.cancel()
                return redirect("/settings/ai", code=303)
            elif action == "disconnect":
                codex_login.disconnect(user_id=who.get("id"))
                return redirect("/settings", code=303)
        except codex_login.LoginError as e:
            note = str(e)
    if not live and not note:                        # a failed start says why; no stale session
        live = codex_login.pending()
    st = str(live.get("status") or "")
    if st == "done":
        codex_login.cancel()                             # the session directory has done its job
        return redirect("/settings", code=303)
    if st == "error" and not note:
        note = str(live.get("error") or "")
        live = {}
    body = ['<div class="card"><p>Sign in to ChatGPT on your mobile or computer and this box will '
            'draft on your own subscription. There is no key, and the box never sees your '
            'password.</p></div>']
    if note:
        body.append(setup_errors.card("ai", note))
    refresh = ""
    if live.get("url") and live.get("code"):
        body.append(
            '<div class="card">'
            '<p><b>1.</b> Open this link on any device and sign in to ChatGPT.</p>'
            f'<p style="margin:12px 0"><a href="{_esc(live["url"])}" target="_blank" '
            'rel="noopener noreferrer">Open ChatGPT &rarr;</a></p>'
            f'<p class="quiet" style="word-break:break-all">{_esc(live["url"])}</p>'
            '<p><b>2.</b> Enter this one-time code when it asks.</p>'
            '<p style="font:600 calc(28 * var(--px, 1px))/1.2 ui-monospace,Menlo,monospace;letter-spacing:0.08em;'
            f'margin:10px 0 16px;user-select:all">{_esc(live["code"])}</p>'
            '<p class="quiet">The code lasts fifteen minutes. This page checks itself every few '
            'seconds and takes you back to settings when the sign-in is done.</p>'
            '<form method="post" action="/settings/chatgpt" style="margin-top:12px">'
            '<input type="hidden" name="do" value="cancel">'
            '<button class="ghost" type="submit">Cancel</button></form></div>')
        refresh = '<meta http-equiv="refresh" content="5">'
    else:
        # THE SAME TICK THE CLAUDE CARD CARRIES, BECAUSE IT IS THE SAME DECISION. Signing in here
        # runs a box on a consumer subscription exactly as signing in there does, so the gate
        # counsel asked for belongs on both screens or on neither — and until 2026-09-22 it was on
        # one. Both vendors' terms are linked beside it for the reason the owner gave on
        # 2026-09-18: what we owe somebody standing here is the INFORMATION, at the moment they
        # are deciding. Nothing is refused if it is left unticked.
        e = _step("anthropic")
        links = _terms_links(e)
        body.append(
            '<div class="card"><h2>Use your ChatGPT subscription</h2>'
            + (f'<p class="quiet">{_esc(e.get("terms_warning") or "")}</p>'
               if e.get("terms_warning") else "")
            + (f'<p class="quiet">{links}</p>' if links else "")
            + '<form method="post" action="/settings/chatgpt">'
            '<input type="hidden" name="do" value="start">'
            + _consent(e) +
            '<button type="submit">Connect</button></form>'
            '<p class="quiet" style="margin-top:12px">Not working? Turn on device code sign-in '
            'in ChatGPT under Settings &rarr; Security (a work account needs an admin to allow '
            'it), then press Connect again.</p></div>')
    body.append(_back())
    page = chrome("/settings/ai", title="AI Account",
                  lede="Sign in with ChatGPT — a one-time code, no key.", body="".join(body))
    if refresh and "</head>" in page:
        page = page.replace("</head>", refresh + "</head>", 1)
    return page, 200


def _local_when(iso) -> str:
    """An ISO time as the owner reads it, in their own timezone ("Oct 1, 2:58 PM")."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        from core import notify
        d = datetime.fromisoformat(str(iso)).astimezone(ZoneInfo(notify.buyer_timezone()))
    except Exception:                                # noqa: BLE001 — a time that can't be read isn't shown
        return ""
    return f"{d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


_HOW = {"test": "a test", "coworker": "a coworker's shift", "router": "a quick check"}


def _ai_health_lines() -> str:
    """WHETHER THE AI REALLY ANSWERS, not only whether it's connected (core/ai_health.py): the last answer, a
    failure newer than it with its reason, the latest test, and the button to test it now."""
    from core import ai_health, box_secrets
    st, t = ai_health.state(), ai_health.last_test()
    ok, fail = st.get("last_ok") or {}, st.get("last_fail") or {}
    out = []
    # WHICH TOKEN IS IN USE (owner, 2026-10-01): a newer sign-in always wins; a token only in the box's settings file
    # is named, so a stale one there can never hide behind "Connected" again.
    try:
        from core import brain
        # The Claude token's source, said only while the box thinks on Claude: on ChatGPT it isn't the one in use.
        source = box_secrets.claude_oauth_source() if brain._backend() == "claude_code" else ""
    except Exception:                                # noqa: BLE001
        source = ""
    if source == "sign-in":
        out.append('<p class="quiet">Using the token from your sign-in on this page.</p>')
    elif source == "settings file":
        out.append('<p class="quiet">Using a token from the box\'s settings file. Sign in below to replace it.</p>')
    if ok.get("at"):
        out.append(f'<p class="quiet">Last answered: {_esc(_local_when(ok["at"]))}, '
                   f'{_esc(_HOW.get(ok.get("how"), "writing for a machine"))}.</p>')
    else:
        out.append('<p class="quiet">It hasn\'t answered anything yet. Test it below.</p>')
    if fail.get("at") and str(fail.get("at")) > str(ok.get("at") or ""):
        out.append(f'<p class="quiet">Last failed: {_esc(_local_when(fail["at"]))}: {_esc(fail.get("why"))}</p>')
    if request.args.get("tested") and t:
        said = (f'It answered "{_esc(t.get("answer"))}" in {_esc(t.get("seconds"))} seconds. Your AI is working.'
                if t.get("ok") else f'It did not answer: {_esc(t.get("why"))} Sign in again under Use a different '
                                    'account below, then press Test it now.')
        out.append(f'<p><b>{said}</b></p>')
    out.append('<form method="post" action="/settings/ai" style="margin-top:12px"><input type="hidden" '
               'name="do" value="test"><button class="ghost" type="submit">Test it now</button></form>')
    return "".join(out)


def _models(e: dict) -> tuple:
    """The four, from the contract. `()` on a box carrying an older one, and every caller copes."""
    return tuple((e.get("choose") or {}).get("options") or ())


def _picked(e: dict) -> dict:
    """WHICH MODEL THIS SCREEN IS ABOUT. `?model=` if it names one of ours, else the live one.

    A QUERY STRING IS AN UNTRUSTED STRING and it is matched against the contract rather than
    interpolated: `?model=<script>` selects nothing and the page draws Claude.
    """
    want = str(request.args.get("model") or "")
    opts = _models(e)
    return (next((o for o in opts if o.get("id") == want), None)
            or next((o for o in opts if o.get("available")), None)
            or (opts[0] if opts else {}))


def _picker(e: dict, picked: dict) -> str:
    """The dropdown the owner asked to have back.

    IT WAS NEVER DELETED — it was declared and then orphaned. `_AI_STEP["choose"]` has carried all
    four models the whole time, and the inbox's wizard drew it; when the step moved to core
    (#1418) it arrived at a screen with no renderer for a picker, so it silently stopped being on
    anybody's screen. Owner, 2026-09-22: *"he removed this drop-down… We need to put that back."*

    ALL FOUR ARE SELECTABLE HERE, WHICH IS THE DIFFERENCE FROM THE MACHINE'S COPY. The machine
    greyed the unavailable ones out, because there the picker sat directly above a field that
    would have taken a key. Here choosing one changes the page: a model the box can draft with
    gets its sign-in, and one it cannot gets an explanation and nothing to fill in. So selecting
    it costs a buyer nothing and answers the question they actually have — what is this going to
    involve. The refusal to take a key the box cannot draft with lives in the panel, not in a
    disabled attribute.
    """
    opts = _models(e)
    if not opts:
        return ""
    choice = e.get("choose") or {}
    out = []
    for o in opts:
        sel = " selected" if o.get("id") == picked.get("id") else ""
        tail = "" if o.get("available") else " — not yet"
        out.append(f'<option value="{_esc(o.get("id"))}"{sel}>'
                   f'{_esc(o.get("name"))}{tail}</option>')
    return ('<div class="card"><form method="get" action="/settings/ai">'
            f'<label for="ai-model">{_esc(choice.get("label") or "")}</label>'
            f'<select id="ai-model" name="model" onchange="this.form.submit()">'
            + "".join(out) + '</select>'
            # NO SCRIPT, NO DEAD END. `onchange` is the nicety; the button is what makes the
            # control work for somebody whose browser ran none of it.
            '<noscript><button class="ghost" type="submit">Show</button></noscript>'
            f'<p class="quiet" style="margin:10px 0 0">{_esc(choice.get("note") or "")}</p>'
            '</form></div>')


def _terms_links(e: dict) -> str:
    """Both vendors' terms, one click away. `TERMS_LINKS` is (label, url) PAIRS, not dicts —
    read off the contract rather than assumed, because the first draft of this assumed dicts and
    500'd the screen."""
    return " · ".join(
        f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">{_esc(label)}</a>'
        for label, url in (e.get("terms_links") or ()))


def _consent(e: dict) -> str:
    """The tick, wherever the card that needs it puts it. Never `required` — see `box_secrets`."""
    if not e.get("consent_field"):
        return ""
    return (f'<label class="consent"><input type="checkbox" '
            f'name="{_esc(e.get("consent_field"))}">'
            f'<span>{_esc(e.get("consent_label"))}</span></label>')


def _door(m: dict) -> str:
    """A LIVE MODEL WHOSE SIGN-IN LIVES ON ANOTHER SCREEN — the same three steps, then one button.

    THE BUTTON DOES THE THING, IT DOES NOT NAVIGATE TO IT. It posts straight to that screen's own
    `do=start`, so pressing Connect here starts the sign-in and lands on the page showing the link
    and the code. A link that merely arrives at another Connect button asks a buyer to press the
    same word twice, which reads like the first press failed.
    """
    name = _esc(m.get("name"))
    href = _esc(m.get("connect_href"))
    steps = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span>{_esc(s)}</span></div>'
                    for i, s in enumerate(m.get("steps") or (), 1))
    return ('<div class="card">'
            f'<h2>Use your {name} subscription</h2>'
            f'<p>{_esc(m.get("lede") or "")}</p>'
            + (f'<p class="quiet">What it costs: {_esc(m.get("billing"))}</p>'
               if m.get("billing") else "")
            + steps
            + f'<form method="post" action="{href}">'
            '<input type="hidden" name="do" value="start">'
            f'<button type="submit">Connect {name}</button></form>'
            + (f'<p class="quiet" style="margin-top:12px">{_esc(m.get("gotcha"))}</p>'
               if m.get("gotcha") else "")
            + '</div>')


def _preview(m: dict, opts: tuple = ()) -> str:
    """WHAT CONNECTING THIS ONE WILL LOOK LIKE — and, plainly, that it does not work yet.

    THIS PANEL EXISTS BECAUSE THE FOUR ERRANDS ARE GENUINELY DIFFERENT, which is the thing a
    logo-and-a-key-field screen hides. Only Claude can be driven by the consumer subscription
    somebody already pays for; ChatGPT, Gemini and Grok each need a developer account with its
    own billing, and two of the three are a surprise to anyone holding a $20 subscription. That
    is worth a buyer's twenty minutes and it costs us a paragraph.

    THERE IS NO FIELD ON THIS PANEL AND THAT IS THE POINT. A box that accepted a key it cannot
    draft with would be the fake feature the owner named on 2026-09-21.
    """
    name = _esc(m.get("name"))
    steps = "".join(f'<div class="row"><span class="n">{i}</span>'
                    f'<span>{_esc(t)}</span></div>'
                    for i, t in enumerate(m.get("steps") or (), 1))
    # WHICH ONES DO WORK, COUNTED RATHER THAN NAMED. This sentence said "Pick Claude above" and
    # went stale the day ChatGPT went live — a buyer holding a ChatGPT subscription was told to
    # go and find a Claude one. It now reads the same table the picker draws from, so the day a
    # third model lands there is no copy anywhere that has to be remembered.
    live = [str(o.get("name")) for o in (opts or ()) if o.get("available")]
    others = (" or ".join((", ".join(live[:-1]), live[-1])) if len(live) > 1
              else (live[0] if live else ""))
    label, href = (m.get("key_from") or ("", ""))
    sub = ('This is one you can drive with a subscription you already pay for.'
           if m.get("subscription") else
           'A consumer subscription does not cover this one — the key is a separate account.')
    return ('<div class="card">'
            f'<h2>What {name} would look like</h2>'
            f'<p class="sub">{_esc(sub)}</p>'
            f'<p>{_esc(m.get("lede") or "")}</p>'
            + (f'<p class="quiet">What it costs: {_esc(m.get("billing"))}</p>'
               if m.get("billing") else "")
            + (f'<p class="quiet">Where the key comes from: '
               f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer">'
               f'{_esc(label)}</a></p>' if href else "")
            + steps
            + '</div>'
            '<div class="card"><h2>Not yet</h2>'
            f'<p>This box cannot draft on {name} today, so it will not take a key for it — a key '
            'that sits here and never writes a reply is worse than no key at all. '
            + (f'Pick {_esc(others)} above to connect something that works now. ' if others
               else '')
            + f'{name} will appear here as a button the day the box can really use it.'
            '</p></div>')


def _key_form(e: dict) -> str:
    """The fallback for somebody who already holds a key, with the terms beside it.

    THE WARNING, THE LINKS AND THE TICK ARE THE STEP'S, NOT THIS FILE'S. Owner, 2026-09-18: *"we're
    going above and beyond by putting a link to Anthropic terms and to OpenAI terms right there on
    the page so they can review the terms of their subscription in particular."* Every word of it
    is declared in `box_secrets`, so the machine's screen and this one cannot drift apart — and the
    tick is never `required`, because the owner's ruling is that a box refuses nobody: *"We are not
    policing people's usage of their own property."*
    """
    if not e.get("fields"):
        return ""
    f = dict((e.get("fields") or [{}])[0])
    # `TERMS_LINKS` IS A TUPLE OF (label, url) PAIRS, not dicts. Read off the contract rather
    # than assumed — the first draft of this line assumed dicts and 500'd the screen.
    links = _terms_links(e)
    # THE TICK IS NOT HERE ANY MORE, ON THE OWNER'S INSTRUCTION, AND THE WARNING STAYS. It reads
    # "use my SUBSCRIPTION with this box", and this is the one card on the screen where what you
    # are connecting is not a subscription — a pasted `sk-ant-oat…` is the expert's back door and
    # an `sk-ant-api…` key carries no such condition at all. The INFORMATION is what we owe
    # somebody standing here, so both vendors' terms stay linked, one click away, at the moment
    # they are deciding (owner, 2026-09-18). Nothing is refused either way, and so this path
    # records no tick rather than inventing one.
    return ('<div class="card"><h2>Or paste a key</h2>'
            f'<p class="quiet">{_esc(e.get("terms_warning") or "")}</p>'
            + (f'<p class="quiet">{links}</p>' if links else "")
            + '<form method="post" action="/settings/ai">'
            '<input type="hidden" name="do" value="key">'
            f'<label for="ai-key">{_esc(f.get("label") or "Key")}</label>'
            f'<input id="ai-key" name="key" type="{_esc(f.get("type") or "password")}" '
            f'autocomplete="off" spellcheck="false" '
            f'placeholder="{_esc(f.get("placeholder") or "")}">'
            # THE FALLBACK IS NOT THE PRIMARY. The Connect or Finish above is the thing to do next;
            # this Save is for the few who already hold a key, so it wears the outline (walk drift,
            # OSDev0, 2026-09-24: /settings/ai drew three ink pills).
            + '<button class="ghost" type="submit">Save</button></form>'
            + (f'<p class="quiet" style="margin-bottom:0">{_esc(e.get("terms_note"))}</p>'
               if e.get("terms_note") else "")
            + '</div>')


# ── the mobile app ────────────────────────────────────────────────────────────────────────────────────

# THE INSTRUCTIONS COME FROM THE CONTRACT, NOT FROM THIS FILE. `_MOBILE_STEP["platforms"]` carries
# them, because TWO screens render these words — this page and the sheet a colleague is handed —
# and a second copy laid out for paper is a second copy that drifts. The one that drifts is the
# printed one: nobody re-reads a page they already pinned to a wall.
def home_screen_apps() -> list[dict]:
    """Every app this box can put on a home screen: the Base Machine first, then each add-on.

    AN APP IS WHATEVER SERVES A WEB MANIFEST, read off the box's own routes. Core never names a
    machine's address (tests/test_core_boundary.py), so it asks the router which manifests exist
    and reads each one the way a browser would. A box sold with two machines shows three icons,
    and a Lead box shows what a Lead box has, with no list here to keep in step.
    """
    from flask import current_app
    import json
    found = []
    for rule in current_app.url_map.iter_rules():
        if not rule.rule.endswith("manifest.webmanifest") or "GET" not in (rule.methods or ()):
            continue
        try:
            resp = current_app.view_functions[rule.endpoint]()
            m = json.loads(resp.get_data(as_text=True) if hasattr(resp, "get_data") else resp)
        except Exception as e:                   # noqa: BLE001 — one broken app hides only itself
            log.warning("box_settings.manifest_unreadable", rule=rule.rule, error=type(e).__name__)
            continue
        icons = [i for i in (m.get("icons") or ()) if str(i.get("src") or "").startswith("/")]
        found.append({"name": str(m.get("short_name") or m.get("name") or "").strip(),
                      "start": str(m.get("start_url") or "/"),
                      "icon": str(icons[0]["src"]) if icons else "",
                      "base": rule.rule.startswith("/ui/")})
    found = [a for a in found if a["name"] and a["icon"]]
    return sorted(found, key=lambda a: (not a["base"], a["name"].lower()))


def _apps_card() -> str:
    """The home screen this page is about to give you, drawn from the apps the box really has."""
    apps = home_screen_apps()
    if not apps:
        return ""
    tiles = "".join(f'<a href="{_esc(a["start"])}"><img src="{_esc(a["icon"])}" alt="" '
                    f'width="64" height="64"><span>{_esc(a["name"])}</span></a>' for a in apps)
    many = len(apps) > 1
    return ('<div class="card"><h2>On your Home Screen</h2>'
            + ('<p class="sub">The Base Machine and each add-on machine are apps of their own. '
               'Add each one: tap it here to open it, then follow the steps below.</p>' if many else
               '<p class="sub">Tap it here to open it, then follow the steps below.</p>')
            + f'<div class="apps">{tiles}</div></div>')


def icon_card() -> str:
    """OWNER ONLY: the client's icon, for the menu, every header and every home-screen app on this box.

    ON THE SYSTEM SETTINGS MAIN SCREEN (owner, 2026-09-30), not on Mobile App where it began: "this
    icon is not just used for the mobile app, but it's used inside the dashboard". Mobile App keeps
    a link to it, in the place it left. The check and the re-encoding live in core/client_icon.py;
    this is the form.
    """
    if not _is_owner():
        return ""
    from flask import request as _rq
    from core import client_icon
    from core.dash import look
    got = client_icon.current()
    said = ""
    if _rq.args.get("icon_error"):
        said = f'<p class="stale">{_esc(client_icon.said(_rq.args["icon_error"]))}</p>'
    elif _rq.args.get("icon") == "saved":
        said = '<p class="ok">Saved. Every screen now wears it, and every app added from now on.</p>'
    elif _rq.args.get("icon") == "removed":
        said = '<p class="ok">Back to the Ownbox mark.</p>'
    remove = ('<form method="post" action="/settings/icon"><input type="hidden" name="do" '
              'value="remove"><button class="ghost" type="submit">Use the Ownbox mark again</button></form>'
              if got else "")
    # THE TWO PLACES IT IS SEEN, side by side: the circle that opens the menu, and the tile on a
    # home screen, which is the box's own icon address — what a phone will really be given.
    tile = f'/ui/icon-192.png{look.icon_version()}'
    return ('<div class="card" id="icon"><h2>Your icon</h2>'
            '<p class="sub">It opens the menu on every screen, heads the menu, and is the icon of '
            'every app on this box on a home screen. A square PNG or JPEG, at least 192 pixels on '
            'a side. One with its own background fills the space; one with a clear background '
            'sits on white.</p>'
            '<div class="iconsee">'
            f'<figure>{look.header_mark()}<figcaption>Menu</figcaption></figure>'
            f'<figure><img class="tile" src="{_esc(tile)}" alt="" width="64" height="64">'
            '<figcaption>Home screen</figcaption></figure></div>'
            + ('' if got else '<p class="quiet">The Ownbox mark, until you upload yours.</p>')
            + said +
            '<form method="post" action="/settings/icon" enctype="multipart/form-data">'
            '<label for="icon-file">Choose a PNG or JPEG</label>'
            '<input id="icon-file" type="file" name="icon" accept="image/png,image/jpeg" required>'
            '<button class="ghost" type="submit">Upload icon</button></form>'
            + remove +
            '<p class="quiet">An iPhone keeps the icon an app had when it was added. To see the new one '
            'there, remove the app from the home screen and add it again.</p></div>')


def _icon_link_card() -> str:
    """Where the icon card used to be on Mobile App: a way to it, for the owner who comes looking."""
    if not _is_owner():
        return ""
    return ('<div class="card"><h2>Your icon</h2>'
            '<p class="sub">The icon on these apps is set in System Settings, with the menu and '
            'every screen.</p>'
            '<div class="foot"><a href="/settings#icon">Change your icon &rarr;</a></div></div>')


@blueprint.post("/settings/icon")
def box_icon():
    """Save, or remove, the client's icon. Owner only, like the card that sends it.

    THE ONLY ROUTE ON THE BOX THAT TAKES MORE THAN 256 KB. `core/dispatch.py` caps every request
    there, deliberately, and a logo exported from a design tool is often larger — so this one route
    raises its own limit to the icon's, before anything reads the body. Nothing else moves."""
    refuse = _admit(owner_only=True)
    if refuse is not None:
        return refuse
    from flask import request as _rq
    from werkzeug.exceptions import RequestEntityTooLarge
    from core import client_icon
    _rq.max_content_length = client_icon.MAX_BYTES + 64 * 1024
    back = "/settings"
    try:
        if _rq.form.get("do") == "remove":
            client_icon.remove()
            return redirect(f"{back}?icon=removed#icon", code=303)
        f = _rq.files.get("icon")
        data = f.read(client_icon.MAX_BYTES + 1) if f else b""
        client_icon.save(data, by=(_who() or {}).get("id"))
    except RequestEntityTooLarge:
        return redirect(f"{back}?icon_error=large#icon", code=303)
    except client_icon.Refused as e:
        return redirect(f"{back}?icon_error={e.code}#icon", code=303)
    return redirect(f"{back}?icon=saved#icon", code=303)


def _platform_cards() -> str:
    """iPhone on the left, Android on the right — because whoever prints this does not know which
    device the next person has. Drawn from the step; this file names no platform of its own."""
    out = []
    for pl in (_step("mobile").get("platforms") or ()):
        items = "".join(f'<li style="margin:6px 0">{_esc(t)}</li>'
                        for t in (pl.get("steps") or ()))
        out.append(f'<div style="flex:1 1 260px;min-width:260px">'
                   f'<h3 style="margin:0 0 8px">{_esc(pl.get("title"))}</h3>'
                   f'<ol style="padding-left:20px;margin:0">{items}</ol>'
                   + (f'<p class="quiet" style="margin:8px 0 0">{_esc(pl.get("note"))}</p>'
                      if pl.get("note") else "")
                   + '</div>')
    if not out:
        # A BOX MID-SELF-UPDATE LEGITIMATELY CARRIES AN OLDER CONTRACT. Say so rather than print
        # an empty box on a sheet somebody is about to hand to a colleague.
        return ('<p class="quiet">This box is running an older set of instructions. Add it to '
                'your Home Screen from your browser\'s share or menu button.</p>')
    return '<div style="display:flex;flex-wrap:wrap;gap:28px">' + "".join(out) + '</div>'


# THE OLD ADDRESS STILL OPENS, and it is not politeness — it is the defect this whole area spent
# the night removing. `/settings/phone` shipped in #1418, the owner was handed it, typed it, and
# it is the address a printed handout may already carry. A rename that 404s the one address he was
# just given is the same dead end in a new hat.
#
# 308, NOT 302: the method and body are preserved, and browsers cache it — so a bookmark heals
# itself rather than asking again forever. Remove this pair once no handout in the world can carry
# the old address; it costs four lines until then.
@blueprint.route("/settings/phone")
def box_phone_moved():
    return redirect("/settings/mobile", code=308)


@blueprint.route("/settings/phone/print")
def box_phone_print_moved():
    return redirect("/settings/mobile/print", code=308)


def _email_line() -> str:
    """What email does alongside the app, said only as far as it is true on THIS box.

    WALK #7 (OSDev4, docs/JOURNEY_WALK_2026-09-23.md): this page said "Email keeps arriving either
    way" on a box that could not send email at all. A sold box ships with no way to send it until
    its owner adds one (/settings/email, #1473), so the promise was false on every new box. Now it
    reads the box: with email set up, the app is the faster of two ways; without, it is the way,
    and the owner is shown where to add the other.
    """
    try:
        from core import box_mail
        sending = box_mail.is_configured()
    except Exception:                            # noqa: BLE001 — unknown is not "yes"
        sending = False
    if sending:
        return ('<p class="quiet">Your Morning Review and inbox alerts also arrive by email. This '
                'is the faster way to hear about them, never the only way.</p>')
    return ('<p class="quiet">This box has no email set up yet, so for now the app is how it '
            'reaches you.'
            + (' <a href="/settings/email">Set up email &rarr;</a>' if _is_owner() else '')
            + '</p>')


@blueprint.route("/settings/mobile")
def box_mobile():
    """How to install this box as an app so it can notify you. It has never had a screen of its own.

    ITS OWN PAGE, AND NOT A CARD. Owner, 2026-09-20: "It should be given its own page and a
    required step in the onboarding. A nice printable page that people can hand to their
    colleagues." The reason a card cannot do this job is that THE PERSON WHO NEEDS IT IS OFTEN NOT
    THE PERSON WHO BOUGHT THE BOX — a box seats three, and whoever is watching the inbox at 8 AM is
    frequently not the owner and is not standing next to them when they set it up. A card in
    somebody else's settings cannot be handed to a receptionist. A page at a stable address can.

    NOT OWNER-ONLY: the app on somebody's own mobile is theirs. A member working the inbox all day needs the
    notification more than the owner does, and subscriptions are keyed to whoever is signed in.

    INSTALLING IS HERE; ASKING IS NOT, and that is the owner's other ruling the same day — ask
    "after the buyer has seen their first real message, never on first load." Adding the box to a
    Home Screen is free and reversible, and on iPhone nothing can notify without it, so it belongs in
    set-up. An iOS denial is close to permanent, so the prompt waits for a moment that has earned
    it. This page fires nothing.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import push

    e = _step("mobile")
    ok, why = push.available()
    body = []
    # THE ORDER A PERSON NEEDS IT IN (walk, 2026-09-24): anything wrong first, then where this box
    # stands, then how to install. It used to open with a card repeating the page's own lede and
    # close on a card that began "One thing first" — the first thing, drawn last.
    if not ok:
        # AN HONEST EMPTY ANSWER BEATS FOUR STEPS THAT CANNOT SUCCEED. `why` is a sentence written
        # for this screen, never an exception class (#1483 finding 8) — the class is in the log.
        body.append('<div class="card notice"><p>One thing first: this box cannot send '
                    f'notifications yet — {_esc(why)}. The steps below still work and are worth '
                    'doing. The box tries again every time it updates; if this is still here after '
                    f'an update, email {setup_errors.SUPPORT}.</p></div>')

    # THE DEVICE IN YOUR HAND, FIRST (walk #9). The card below is the box as a whole; this one is
    # the answer a person who just installed is looking for.
    body.append(device_card())

    # THE STATE, SAID AS NARROWLY AS THE SERVER CAN HONESTLY SAY IT. A subscription row proves SOME
    # device on this box is set up; it can never prove the one in your hand is, because the same
    # person reading this on a laptop has a mobile the box cannot see. So it reports the box, and
    # says out loud that the box is what it is reporting.
    detail = str(e.get("detail") or "").strip()
    body.append('<div class="card"><h2>Where this box stands</h2>'
                + (f'<p>{_esc(detail)}.</p>' if detail else
                   '<p>No device on this box has notifications switched on yet.</p>')
                + '<p class="quiet">This is what the box can see across everybody who uses it. '
                  'The card above is about the device you are holding.</p></div>')

    body.append(_apps_card())
    body.append(_icon_link_card())
    body.append('<div class="card">' + _platform_cards() + '</div>')

    # WHEN THE ASKING HAPPENS, said plainly, because a set-up step that ends with nothing switched
    # on reads as a step that failed. It did not: the box is waiting on purpose.
    body.append('<div class="card"><h2>Turning them on</h2>'
                '<p>The box asks you once, on the screen where you read your messages, the first '
                'time something real arrives. It waits on purpose: a mobile only lets you answer '
                'that question once, and saying no is hard to undo.</p>'
                + _email_line() + '</div>')

    body.append('<div class="card"><h2>For somebody else on this box</h2>'
                '<p>Whoever watches the inbox is often not whoever bought the box. This page '
                'prints onto one sheet you can hand over or leave by the till — it carries no '
                'password and nothing private, just these instructions and the address.</p>'
                '<p><a href="/settings/mobile/print">Print this for a colleague &rarr;</a></p>'
                '</div>')
    body.append(_back())
    return chrome("/settings/mobile", title="Mobile App",
                  lede="Install the box as an app and it can notify you when a customer writes.",
                  body="".join(body)), 200


# AN ADDRESS THAT CANNOT WORK FROM ANOTHER DEVICE MUST NEVER REACH PAPER.
#
# THIS IS A MEASURED FAILURE, NOT A PRECAUTION (2026-09-22). The owner opened a sheet rendered
# from a box reached over `localhost`, followed its last instruction on his mobile, and Safari said
# it could not connect to the server. `localhost` on a mobile IS that device. He had already
# installed the real box on his Home Screen; the sheet sent him somewhere that does not exist.
#
# WHAT MAKES IT WORSE THAN A BROKEN LINK IS THE PAPER. A dead link on a screen is a back button.
# A dead address on a sheet pinned to a wall is read by somebody who was not there when it was
# printed, cannot tell whether they typed it wrong, and has nobody to ask — which is the precise
# person this whole page exists for.
#
# A LAN ADDRESS IS ALLOWED ON PURPOSE. 192.168.x / 10.x is how a receptionist on the shop's own
# wifi reaches a box that is not on the public internet; that genuinely works and refusing it
# would be us deciding how somebody may run their own box. Only the addresses that can NEVER
# resolve to this box from another device are refused.
_UNPRINTABLE_HOSTS = ("localhost", "127.", "0.0.0.0", "[::1]", "::1", "169.254.")


def _handout_address(root: str) -> str:
    """The address to print, or "" when this box is being viewed at one that cannot travel."""
    host = root.split("//", 1)[-1].split("/", 1)[0].split("@")[-1].lower()
    bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    for bad in _UNPRINTABLE_HOSTS:
        if bare == bad.rstrip(".") or bare.startswith(bad) or bare.endswith(".localhost"):
            return ""
    return f"{root}/settings/mobile"


@blueprint.route("/settings/mobile/print")
def box_mobile_print():
    """The same instructions, laid out for paper, for the seat that is not reading this screen.

    A SEPARATE ROUTE RATHER THAN A `@media print` BLOCK ON THE PAGE ABOVE, which is what I planned
    and is the wrong shape here: the page above renders through `chrome()`, so printing it would
    put the rail, the breadcrumb and the box's whole navigation on a sheet meant to be handed to
    somebody who has never seen the product. This route shares the INSTRUCTIONS with it — one
    `_platform_cards()` for both — and nothing else, so the two cannot drift in the way that
    matters while the paper stays paper.

    NOT OWNER-ONLY, for the same reason as the page it belongs to, and then some: this is the one
    screen in the product whose whole purpose is somebody who does not own the box.

    NOTHING PRIVATE IS ON IT, AND THAT IS LOAD-BEARING RATHER THAN INCIDENTAL. It is meant to be
    left on a counter. It carries the business's own name, the address of this page, and
    instructions — no credential, no token, no customer, no message, no number. A test asserts it,
    because the day somebody adds "and here is your key" to this page is the day a key goes on a
    noticeboard.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import dash as _dash

    name = _dash.brand()
    root = str(request.host_url or "").rstrip("/")
    # THE ADDRESS IS THE HANDOVER, so it is set in type somebody can read across a counter and
    # type without a second look. A QR would be better and is deliberately not here — see the
    # note in the PR: this box carries no QR encoder, requirements.lock is hash-pinned per box, and
    # a hand-rolled one that does not scan is worse on paper than an address that does.
    where = _handout_address(root)
    # SAY WHY, AND SAY WHAT TO DO. A sheet that silently drops its own address is a sheet somebody
    # prints, hands over, and only then discovers is useless.
    address_block = (
        f'<p class="addr">{_esc(where)}</p>' if where else
        '<p class="addr">— open this page at the box\'s own web address before printing —</p>'
        '<p class="quiet">This sheet was opened at an address that only works on the computer it '
        'was opened from, so there is nothing here a colleague could type. Open the box at the '
        'web address you normally use, come back to this page, and print it again.</p>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_esc(name)} — notifications on your mobile</title>"
            "<style>"
            # NO DARK MODE, NO THEME TOKENS. Paper is white and the screen preview should look
            # like the sheet that comes out of the printer, so this page defines its own colours
            # rather than inheriting a palette that inverts.
            "  body{background:#fff;color:#111;font:16px/1.55 -apple-system,BlinkMacSystemFont,"
            "'Segoe UI',Helvetica,Arial,sans-serif;margin:0;padding:32px 28px;max-width:760px}"
            "  h1{font-size:26px;margin:0 0 4px} h2{font-size:18px;margin:26px 0 6px}"
            "  h3{font-size:15px;margin:0 0 8px}"
            "  .quiet{color:#555;font-size:13.5px}"
            "  .addr{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:19px;"
            "        border:1.5px solid #111;border-radius:8px;padding:12px 14px;display:inline-block;"
            "        margin:8px 0;word-break:break-all}"
            "  .noprint{margin:26px 0 0}"
            # ONE PAGE, PORTRAIT, NO CHROME. `@page` trims the browser's default margin so the
            # sheet is not two pages with four lines on the second.
            "  @media print{ .noprint{display:none} body{padding:0;max-width:none;font-size:12.5pt}"
            "    @page{margin:14mm} h1{font-size:20pt} h2{font-size:13pt} }"
            "</style></head><body>"
            f"<h1>{_esc(name)}</h1>"
            "<p>This box can tell your mobile the moment a customer writes to the business — a "
            "notification that opens straight into the messages, not into somebody else's app.</p>"
            "<h2>Install it on your mobile</h2>"
            + _platform_cards() +
            "<h2>Then open this address on that device</h2>"
            + address_block +
            "<p class=\"quiet\">Sign in with your own account. The box asks whether to send you "
            "notifications the first time a real message arrives — it waits until then on purpose, "
            "because a mobile only lets you answer that question once.</p>"
            "<p class=\"quiet\">There is no password on this sheet and nothing private. If you "
            "do not have an account on this box yet, ask whoever set it up to invite you.</p>"
            "<div class=\"noprint\"><button onclick=\"window.print()\">Print this page</button>"
            " &nbsp; <a href=\"/settings/mobile\">&larr; Back</a></div>"
            "</body></html>"), 200


@blueprint.route("/settings/push/key", methods=["GET"])
def box_push_key():
    """The box's own VAPID public key, for `applicationServerKey`.

    An empty key is an answer, not a failure: a box whose release predates the crypto dependency
    cannot mint one, and the screen needs to say so rather than offer a button that cannot work.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        # A FETCH CANNOT FOLLOW A LOGIN REDIRECT USEFULLY — it would read the login page as JSON
        # and report a parse error as the reason notifications failed.
        return jsonify({"key": "", "available": False, "why": "sign in first"}), 403
    from core import push
    ok, why = push.available()
    return jsonify({"key": push.public_key(), "available": ok, "why": why})


@blueprint.route("/settings/push/subscribe", methods=["POST"])
def box_push_subscribe():
    """A browser hands over the endpoint its push service issued. We store it against the person.

    NOT OWNER-ONLY, for the reason above: every seat on this box gets their own device notified, and
    `subscriptions_for` never crosses users.

    THE BODY IS A SUBSCRIPTION, NOT A MESSAGE. An endpoint and two public key halves, all issued
    by the browser; nothing a customer wrote passes through here.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return jsonify({"ok": False, "error": "sign in first"}), 403
    from core import push
    who = _who()
    if not who.get("id"):
        return jsonify({"ok": False, "error": "sign in first"}), 403
    body = request.get_json(silent=True) or {}
    keys = body.get("keys") or {}
    try:
        fresh = push.save_subscription(user_id=str(who["id"]),
                                       endpoint=str(body.get("endpoint") or ""),
                                       p256dh=str(keys.get("p256dh") or ""),
                                       auth=str(keys.get("auth") or ""))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "new": fresh})


@blueprint.route("/settings/push/mine", methods=["POST"])
def box_push_mine():
    """Is the endpoint this device holds stored against the person asking? (walk #9)

    ONE BIT, AND ONLY ABOUT YOURSELF. It answers for the signed-in person's own rows and nothing
    else, so it cannot be used to learn anything about anybody else's devices.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return jsonify({"mine": False}), 403
    from core import push
    who = _who()
    body = request.get_json(silent=True) or {}
    return jsonify({"mine": push.is_mine(str(who.get("id") or ""), str(body.get("endpoint") or ""))})


def device_card() -> str:
    """The one sentence about the device in your hand, filled in by push.DEVICE_JS (walk #9).

    Without JavaScript it says what it cannot know, rather than leaving a blank card."""
    from core import push
    return ('<div class="card"><h2>On this device</h2>'
            '<p id="ownbox-device" data-state="unknown" aria-live="polite">Open this page with '
            'JavaScript on, from the box\'s Home Screen icon, to see whether this device is '
            'connected.</p></div>'
            # DEVICE_JS ONLY. The notification client carries the permission prompt, and this page
            # never loads anything that could fire it (owner, 2026-09-20).
            f'<script>{push.DEVICE_JS}</script>')


# ── the AI coworkers ─────────────────────────────────────────────────────────────────────────────

# THE WORDS HERE ARE A PERMISSION LIST A BUYER READS BEFORE HANDING OVER A KEY, so they have to
# name everything the role can see. `read` gained the box's own health and `act` gained its
# SPEND (core/box_tools.py), and a role described as "read your conversations" while it can also
# read the billing figure is a consent screen that misleads. Whenever _ROLE_CAPABILITIES grows,
# this text grows with it.
# ONE PERMISSION (owner, 2026-10-02): every AI connection reads and drafts. Read only is gone as a choice, and a
# connection made read-only before is moved up the next time this screen opens or it is used (core.connector.seats).
_ROLE_CHOICES = (
    ("act", "Read and draft replies",
     "It can read your conversations, your morning report, whether the box is running, what the box has spent "
     "against its monthly ceiling, and the apps you connected on Data Sources, and it can leave a suggested reply "
     "waiting on the screen. It still cannot send - you press send, or you do not."),
)


def _seat_rows(seats_list: list) -> str:
    """The seats that exist, so revoking is possible without remembering what you made.

    A REVOKED SEAT IS STILL LISTED. It is the audit trail: "this assistant had access between these
    dates" is a question a buyer will eventually be asked by somebody else.
    """
    if not seats_list:
        return ('<div class="card"><p class="quiet">Nothing is connected to this box yet.</p>'
                '</div>')
    out = ['<div class="card">']
    for s in seats_list:
        label = _esc(s.get("label"))
        role = str(s.get("role") or "")
        human = next((t for r, t, _ in _ROLE_CHOICES if r == role), "Read and draft replies" if role == "read"
                     else role)
        if s.get("revoked_at"):
            out.append(f'<div class="row"><b style="flex:1;min-width:0">{label}</b>'
                       f'<span class="quiet">Revoked. It can no longer reach this box.</span>'
                       f'</div>')
        else:
            change = ""                          # nothing to switch: every connection reads and drafts
            out.append(f'<div class="row"><b style="flex:1;min-width:0">{label}</b>'
                       f'<span class="quiet">{_esc(human)}</span>{change}'
                       f'<a href="/settings/agent?revoke={_esc(s.get("id"))}">Revoke</a></div>')
    out.append('</div>')
    return "".join(out)


def _seat_form(note: str = "", *, name: str = "", quiet: bool = False) -> str:
    # ONE PERMISSION, SAID, NOT CHOSEN (owner, 2026-10-02: Read only removed). It names everything the key can do.
    fid = "seat-label-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else "seat-label"   # ids unique
    opts = "".join(f'<input type="hidden" name="role" value="{r}"><p><b>{t}.</b> {w}</p>'
                   for r, t, w in _ROLE_CHOICES)
    return (note +
            '<form method="post" action="/settings/agent" class="card">'
            '<input type="hidden" name="do" value="mint">'
            f'<label for="{fid}">What should it be called?</label>'
            '<p class="quiet">A name you will recognise later, so you know what you are '
            'revoking.</p>'
            f'<input id="{fid}" name="label" maxlength="60" placeholder="My AI app" value="{_esc(name)}" '
            'required>'
            '<p><b>What it can do</b></p>' + opts +
            # ONE PRIMARY PER SCREEN (tests/test_every_settings_screen_has_one_primary.py): the second key form is quiet.
            f'<button type="submit"{' class="ghost"' if quiet else ""}>Make the connection key</button></form>')


def _copy(value: str, label: str) -> str:
    """One press to copy. Where the browser can't, the value beside it is plain text to select."""
    return (f'<button type="button" class="ghost" data-copy="{_esc(value)}" onclick="var b=this;'
            'navigator.clipboard&amp;&amp;navigator.clipboard.writeText(b.dataset.copy).then(function()'
            "{b.textContent='Copied'})\">" + _esc(label) + '</button>')


# A CONNECTION KEY, NEVER AN "API KEY" IN OUR OWN WORDS (owner, 2026-10-02: "nobody wants to enter an API key because
# that's really scary because it could get really expensive"; OSDev1's copy). Said wherever a key is made or shown.
KEY_WORDS = ("This connection key is made by your box. It costs nothing. It isn't a key to any AI account: it only "
             "lets your AI reach this box, and you can delete it here any time.")


def _seat_credential(label: str, credential: str, url: str) -> str:
    """Shown ONCE. `seats.mint` never stores the secret, so there is no second chance by design.

    THE PAGE SAYS SO BEFORE THE STRING, not after it. Somebody who scrolls past a key and closes
    the tab has lost it, and the only repair is revoking a seat they never used and making another.
    """
    return ('<div class="card"><h2>Copy this now.</h2>'
            f'<p class="quiet">This is the only time <b>{_esc(label)}</b>\'s connection key will ever be '
            'shown. The box keeps a one-way hash of it and nothing else, so if you lose it, revoke '
            'this connection and make another — there is no way to look it up.</p>'
            f'<p class="addr">{_esc(credential)}</p>' + _copy(credential, "Copy key") +
            f'<p>{_esc(KEY_WORDS)}</p></div>'
            '<div class="card"><h2>Where it goes</h2>'
            '<p><b>Address:</b></p>'
            f'<p class="addr">{_esc(url)}</p>' + _copy(url, "Copy address") +
            '<p><b>In ChatGPT</b>, in the same New custom plugin form: set <b>Authentication</b> to '
            '<b>Access token / API key</b> (ChatGPT\'s words) and paste the key there.</p>'
            '<p class="quiet">Any other AI app or agent: give it the address and the key.</p></div>')


@blueprint.route("/settings/agent", methods=["GET", "POST"])
def box_agent():
    """Connect an AI coworker to this box, or take its access away.

    EVERYTHING THIS CALLS IS CORE AND ALWAYS WAS — `core.connector.seats.mint`, `.revoke`,
    `.all_seats`, and `box_secrets.AGENT_CLIENTS`. The screen was in a machine because that is
    where the screens were, not because the seat belongs to one.

    OWNER-ONLY: the credential it mints reads every message on the box.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import box_secrets
    from core.connector import seats

    if not _is_owner():
        return chrome("/settings/agent", title="MCP Server",
                      lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can connect an AI '
                           'coworker, because the connection can read every message on it.</p>'
                           '</div>' + _back()), 403

    note = ""
    revoke = (request.args.get("revoke") or "").strip()
    if revoke:
        seats.revoke(revoke)
        return redirect("/settings/agent", code=303)

    if request.method == "POST" and str(request.form.get("do") or "") == "role":
        to = str(request.form.get("role") or "").strip()
        if to in seats.SETTABLE:
            seats.set_role(str(request.form.get("seat") or "").strip(), to)
        return redirect("/settings/agent", code=303)

    if request.method == "POST" and str(request.form.get("do") or "") == "mint":
        label = str(request.form.get("label") or "").strip()[:60]
        role = "act"                                     # every AI connection reads and drafts (owner, 2026-10-02)
        try:
            _sid, credential = seats.mint(label, role)
        except ValueError as e:
            note = setup_errors.card("mcp", str(e), head="That key was not made")
        else:
            root = str(request.host_url or "").rstrip("/")
            # NEVER A REDIRECT AND NEVER A QUERY STRING. The credential is rendered into this one
            # response and then it is gone: a redirect would put it in a URL, and gunicorn logs
            # raw query stnotifies.
            return chrome("/settings/agent", title="MCP Server",
                          lede="Copy the key now — it is shown once.",
                          # ONE ADDRESS, THE SHORT ONE — carried across from #1417 (OSDev1),
                          # which landed on main while this screen was being moved into core.
                          # This screen printed `/api/v1/mcp` while the screen that links to it
                          # printed `/mcp`; both work, same handler and same gate, but a buyer
                          # shown two addresses for one thing reasonably concludes one is wrong,
                          # and that doubt arrives while they are pasting a secret. The owner hit
                          # exactly that on 2026-09-22. Resolving this file's merge by taking my
                          # side alone would have silently reverted his fix.
                          body=_seat_credential(label, credential, f"{root}/mcp")
                               + '<div class="foot"><a href="/settings/agent">'
                                 '&larr; MCP Server</a></div>'), 200

    root = str(request.host_url or "").rstrip("/")
    addr = f"{root}/mcp"
    # THE ADDRESS FIRST, THEN HOW TO ADD IT TO EACH AI (owner, 2026-10-02: "standard base machines should be able to
    # have a menu choice called MCP server and they should be able to clearly see the address of the MCP server on
    # how to add it to their favorite chat bo[t]", approved from a preview). The two kinds of coworker are said on
    # Coworkers, the Pro page; this page is the box's own door for the AI a person already uses, on every box.
    #
    # THEN A GUIDE NOBODY HAS TO ASK ABOUT (owner, 2026-10-03: "our customers don't have the ability to ask you
    # questions. So if they can't figure that shit out they're gonna fucking be pissed"; OSDev1's assignment): ChatGPT
    # and Claude step by step with the exact field values, signing in first and the connection key as the labelled
    # fallback, and every error a buyer meets with its fix. An AI is named only where the buyer is in that app.
    copy_addr = _copy(addr, "Copy address")
    body = ('<div class="card"><h2>Your box\'s MCP address</h2>'
            # THERE IS NO KEY TO COPY (owner, 2026-09-22: "we just enter the MCP server and it authorizes"): the
            # assistant is sent here to sign in, since the OAuth front door (#1419).
            '<p class="quiet">Paste this into your AI. It sends you back here to sign in and approve, so there is '
            'no key to copy.</p>'
            # A PLACE TO BREAK AT EACH SLASH, so a long address wraps between its parts on a mobile screen rather
            # than mid-word ("…/mc" then "p"). <wbr> copies as nothing.
            f'<p class="addr">{_esc(root).replace("/", "/<wbr>")}/<wbr>mcp</p>' + copy_addr + '</div>'
            + '<div class="card guide" id="chatgpt"><h2>Connect ChatGPT</h2><ol style="padding-left:20px;margin:0">'
              '<li>On <b>chatgpt.com</b>, in a web browser, open <b>Settings</b>, then <b>Plugins</b>, and choose '
              '<b>New custom plugin</b>.</li>'
              '<li><b>Name:</b> Ownbox</li>'
              f'<li><b>Server URL:</b> <div class="addr">{_esc(addr)}</div>{copy_addr}</li>'
              '<li><b>Authentication:</b> OAuth</li>'
              '<li>Tick the box that says you understand the risk, then press <b>Create</b>.</li>'
              '<li>ChatGPT sends you to this box to sign in. Press <b>Allow</b>, and you are back in ChatGPT, '
              'connected.</li></ol>'
              '<h3>If it doesn\'t work</h3><ul style="padding-left:20px;margin:0">'
              # MEASURED BY THE OWNER, 2026-10-03 (OSDev1): on some ChatGPT screens, choosing OAuth shows this message
              # with Create greyed out, and an underlined "Continue in ChatGPT" link beneath it.
              '<li><b>\u201cOAuth setup is unavailable in this environment\u201d</b>: if Create is greyed out under '
              'that message, click the underlined <b>Continue in ChatGPT</b> link. It opens ChatGPT, where you finish '
              'the same steps and Create works. Or add it on chatgpt.com in a web browser.</li>'
              '<li><b>No Plugins, or no New custom plugin</b>: ChatGPT isn\'t offering custom plugins yet. Your '
              'workspace admin turns on <b>Developer mode</b>, in Settings, Apps, Advanced.</li>'
              '<li><b>It won\'t sign in</b>: use a connection key instead, just below. It works on every plan.</li>'
              '</ul>'
              f'<details id="key"{" open" if note else ""}><summary>Use a connection key instead</summary>'
              '<p>In the same form, set <b>Authentication</b> to <b>Access token / API key</b> (ChatGPT\'s words), '
              'and paste a connection key from your box there. Make it here; it is shown once.</p>'
              f'<p class="quiet">{_esc(KEY_WORDS)}</p>'
            + _seat_form(note, name="ChatGPT")
            + '</details></div>'
            + '<div class="card guide" id="claude"><h2>Connect Claude</h2><ol style="padding-left:20px;margin:0">'
              '<li>In Claude, open <b>Settings</b>, then <b>Connectors</b>, and choose '
              '<b>Add custom connector</b>.</li>'
              '<li><b>Name:</b> Ownbox</li>'
              f'<li><b>Remote MCP server URL:</b> <div class="addr">{_esc(addr)}</div>{copy_addr}</li>'
              '<li>Press <b>Add</b>, then <b>Connect</b>. Claude sends you to this box to sign in. Press '
              '<b>Allow</b>.</li></ol>'
              '<h3>If it doesn\'t work</h3><ul style="padding-left:20px;margin:0">'
              '<li><b>No Add custom connector</b>: on a Claude Team or Enterprise plan, an owner of the '
              'organization adds it first, in the organization\'s settings under Connectors.</li>'
              '<li><b>It couldn\'t connect</b>: check the whole address was pasted, ending in /mcp, then press '
              'Connect again.</li>'
              '</ul></div>')
    others = [c for c in getattr(box_secrets, "AGENT_CLIENTS", ()) if c.get("id") not in ("chatgpt", "claude")]
    body += ('<div class="card"><h2>Another AI app</h2>'
             + "".join(f'<div class="row"><b style="flex:1;min-width:0">{_esc(c["name"])}</b>'
                       f'<span class="quiet">{_esc(c["how"])}</span></div>' for c in others)
             + '<ol style="padding-left:20px;margin:12px 0 0">'
             + "".join(f'<li style="margin:6px 0">{_esc(t)}</li>' for t in getattr(box_secrets, "AGENT_STEPS", ()))
             + '</ol></div>'
             + '<div class="card"><p>You choose what each AI may do, and you can take it back at any moment.</p>'
               '<p class="quiet">Nothing that connects here can send a message as your business. The most it can do '
               'is leave a reply waiting on the screen for you.</p></div>'
             + f'<details style="margin-top:18px"{" open" if note else ""}><summary style="cursor:pointer">'
               'Make a connection key for a script or another agent</summary>'
               f'<p class="quiet" style="margin:10px 0">For anything that can\'t sign in. {_esc(KEY_WORDS)} It is '
               'shown once, so keep it somewhere safe.</p>'
             + _seat_form(note, quiet=True)
             + '</details>'
             + _seat_rows((seats.promote_all(), seats.all_seats())[1])
             + _back())
    return chrome("/settings/agent", title="MCP Server",
                  lede="Use your box from the AI you already use: Claude, ChatGPT, Gemini or Grok.",
                  body=body), 200


# ── the buyer's own way in ───────────────────────────────────────────────────────────────────────

def _key_rows(keys: list) -> str:
    """The keys that can open this box, with the fingerprint the owner can check on their laptop.

    A LIST OF KEYS WITH NO FINGERPRINTS IS NOT A SECURITY SCREEN. "You have two keys" is not
    something anybody can act on; `ssh-keygen -lf ~/.ssh/id_ed25519.pub` prints this exact string,
    so an owner can tell their own key from one they do not recognise and remove the second.
    """
    if not keys:
        return ('<div class="card"><p>No key of yours is on this box yet, so SSH will refuse you. '
                'Add one below and you are in.</p></div>')
    out = ['<div class="card"><h2>Keys that can open this box</h2>']
    for k in keys:
        label = _esc(k.get("comment") or "no name")
        out.append(f'<div class="row"><b style="flex:1;min-width:0">{label}</b>'
                   f'<span class="quiet">{_esc(k.get("type"))}</span></div>'
                   f'<p class="addr">{_esc(k.get("fingerprint"))}</p>'
                   f'<div class="foot"><a href="/settings/access?remove={_esc(k.get("fingerprint"))}">'
                   f'Remove this key</a></div>')
    return "".join(out) + "</div>"


def _connect_card(where: dict, has_keys: bool, host_fp: str) -> str:
    """The literal command, the name in it, where that name leads, and what a refusal means.

    DONE-WHEN, set by OSDev1 on 2026-09-23: a person who has never seen this box can connect using
    ONLY what this page renders — no email, no docs, no asking us. So every branch below prints
    something typeable, and the one case with nothing honest to print says how to make it appear.
    """
    cmd = where.get("command") or ""
    host, ips, box_ip = where.get("host") or "", where.get("ips") or [], where.get("box_ip") or ""
    out = ['<div class="card"><h2>Where you connect</h2>']
    if not cmd:
        out.append('<p>This box was opened at an address only this computer can reach, and it has '
                   'not been told its public one, so there is no command to print yet. Open the '
                   'dashboard at the web address you normally use and come back to this page — the '
                   'command appears here.</p></div>')
        return "".join(out)
    if not has_keys:
        out.append('<p><b>Add your key above first \u2014 this command cannot work yet.</b> No key '
                   'of yours is on this box, so SSH will refuse you until one is. Once the key is '
                   'in, come back to this command.</p>')
    out.append('<p class="quiet">On a Mac, open Terminal (in Applications, then Utilities). On '
               'Windows 10 or 11, open PowerShell. On Linux, open your terminal. Then type this and '
               'press enter:</p>'
               f'<p class="addr" style="font-size:calc(17 * var(--px, 1px))">{_esc(cmd)}</p>'
               '<p class="quiet">Tap or click it once to select all of it.</p>')
    ip_cmd = f"ssh root@{box_ip}" if box_ip else ""
    pts = where.get("points_here")
    if pts is True:
        out.append(f'<p class="quiet">{_esc(host)} leads to {_esc(box_ip)}, which is this box. If '
                   f'the name ever stops working, <b>{_esc(ip_cmd)}</b> reaches the same place.</p>')
    elif pts is False:
        out.append(f'<p><b>{_esc(host)} leads to {_esc(", ".join(ips))}, which is not this box.</b> '
                   f'This box is at {_esc(box_ip)}, so the command above uses that until the name '
                   'is pointed here.</p>')
    elif ips:
        out.append(f'<p class="quiet">{_esc(host)} leads to {_esc(", ".join(ips))}.</p>')
    elif host and box_ip:
        out.append(f'<p class="quiet">{_esc(host)} did not lead anywhere when this page looked it '
                   'up, so the command above uses this box\'s own address instead.</p>')
    elif host:
        out.append('<p class="quiet">This box could not look its own name up just now. If your '
                   'computer says it cannot resolve the name, wait a few minutes and try again.</p>')
    first = ('<p class="quiet">The first time, your computer asks whether you are sure you want to '
             'continue connecting. Type <b>yes</b> and press enter.')
    if host_fp:
        first += (' The fingerprint it shows should be exactly this — if it is different, stop, '
                  'because you have reached some other machine:</p>'
                  f'<p class="addr">{_esc(host_fp)}</p>')
    else:
        first += '</p>'
    out.append(first)
    out.append('<details><summary>If it refuses you</summary>'
               '<p><b>Permission denied (publickey)</b> — this box does not hold the key your '
               'computer offered. Either none has been added yet, or a different one was. On your '
               'computer run <b>ssh-keygen -lf ~/.ssh/id_ed25519.pub</b> and compare what it prints '
               'with the fingerprints under Keys that can open this box.</p>'
               '<p><b>Connection refused</b>, or it waits and then times out — your computer did not '
               'reach this box at all, so no key was checked. Some office and public wifi networks '
               'block SSH; try again from another network.</p>')
    if ip_cmd and cmd != ip_cmd:
        out.append('<p><b>Could not resolve hostname</b> — the name has not reached your network '
                   f'yet. Use <b>{_esc(ip_cmd)}</b> instead.</p>')
    return "".join(out) + "</details></div>"


@blueprint.route("/settings/access", methods=["GET", "POST"])
def box_access_screen(move_note: str = "", checkin_note: str = ""):
    """Add the owner's own SSH key to this box, or take one away. And, at the foot, the danger zone.

    THE PRODUCT PROMISES FULL ACCESS AND THE BOX SHIPPED WITH NONE. The only key ever placed on a
    droplet is ours, and first boot deletes it the moment bootstrap succeeds — deliberately, so
    nobody here keeps standing access to a customer's machine. That left the buyer locked out of
    the server they own. This is the other half, and it is done ON THE BOX so it needs no
    DigitalOcean account, no provisioner change, and works the same after they move the box to a
    Mac mini.

    OWNER-ONLY, and not by a margin. A key here is root on the machine — strictly more than the
    dashboard itself grants — so this is the one screen in the drawer where the gate matters most.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import box_access

    if not _is_owner():
        return chrome("/settings/access", title="Server Access",
                      lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can add a key to it, '
                           'because a key here is full control of the machine — more than this '
                           'dashboard gives anyone.</p></div>' + _back()), 403

    note = ""
    remove = (request.args.get("remove") or "").strip()
    if remove:
        try:
            text, gone = box_access.remove(box_access.read(), remove)
            if gone:
                box_access.write(text)
        except OSError:
            # A BOX WHOSE KEY FILE CANNOT BE READ IS NOT A BOX THAT SHOULD 500. Say so and leave
            # the file alone; an owner locked out by a failed write has no second way in.
            pass
        return redirect("/settings/access", code=303)

    # ONLY THIS PAGE'S OWN FORM ADDS A KEY. The danger zone's two forms post elsewhere and are drawn
    # back onto this page, so a POST that arrives here from them is not a key to read.
    if request.method == "POST" and request.path == "/settings/access":
        try:
            text, fp = box_access.add(box_access.read(), request.form.get("key") or "")
            box_access.write(text)
            note = (f'<div class="card"><h2>That key is in.</h2><p class="quiet">Its fingerprint '
                    f'is below — check it against your own machine before you rely on it.</p>'
                    f'<p class="addr">{_esc(fp)}</p></div>')
        except box_access.KeyRefused as e:
            # THE REFUSAL IS THE TEACHING. Every message from box_access names what to do instead,
            # so it is shown verbatim rather than replaced with "invalid key".
            note = setup_errors.card("access", str(e), head="That was not stored.")
        except OSError as e:                             # noqa: BLE001
            note = setup_errors.card("access", f"Nothing was changed ({type(e).__name__}). Try again in a minute; "
                                     f"if it happens again, email {setup_errors.SUPPORT}.",
                                     head="The box could not write the file.")

    try:
        keys = box_access.listed(box_access.read())
    except OSError:
        keys = []
        note += setup_errors.card("access", "This box cannot read its key file right now, so the list below may be "
                                  f"incomplete. Nothing has been changed. Reload in a minute; if it stays, email "
                                  f"{setup_errors.SUPPORT}.", head="The key list could not be read")

    form = (
        '<div class="card"><h2>Add your key</h2>'
        '<p>Three steps, about two minutes, and you only do this once. You do not need to know '
        'what any of it means \u2014 just follow it in order.</p>'

        '<h3>Step 1 &mdash; Open a terminal on your own computer</h3>'
        '<p class="quiet">Not on this page \u2014 on the computer in front of you.</p>'
        '<ul>'
        '<li><b>On a Mac:</b> hold <b>Command</b> and press the <b>spacebar</b>, type '
        '<b>Terminal</b>, press <b>Enter</b>.</li>'
        '<li><b>On Windows 10 or 11:</b> click <b>Start</b>, type <b>PowerShell</b>, press '
        '<b>Enter</b>.</li>'
        '<li><b>On Linux:</b> open <b>Terminal</b> from your applications.</li>'
        '</ul>'
        '<p class="quiet">A window opens with a blinking cursor. Everything below gets typed '
        'into that window, one at a time, pressing Enter after each.</p>'

        '<h3>Step 2 &mdash; Get your key</h3>'
        '<p>Type this and press Enter:</p>'
        '<p class="addr">cat ~/.ssh/id_ed25519.pub</p>'
        '<p>A long row of text starting with <b>ssh-ed25519</b> appears. That is your key: select '
        'all of it and copy it.</p>'
        # THE STEPS STAY OPEN AND THE EXCEPTIONS FOLD. The page was seven mobile screens because
        # every "if it says..." sat in line with the one thing most people will see. The words are
        # unchanged; they sit one tap away, under the question a person would actually ask.
        '<details><summary>It says "No such file", or asks to overwrite</summary>'
        '<ul>'
        '<li><b>If it says "No such file or directory"</b> \u2014 you do not have a key yet. Type '
        '<b>ssh-keygen -t ed25519</b> and press Enter. It asks three questions: press <b>Enter</b> '
        'at each one without typing anything. Then run the <b>cat</b> command above again, and copy '
        'all of what it prints.</li>'
        '<li><b>If it asks to overwrite an existing key</b> \u2014 type <b>n</b> and press Enter. You '
        'already have one; run the <b>cat</b> command above to see it.</li>'
        '</ul></details>'
        '<details><summary>Is it safe to paste?</summary>'
        '<p class="quiet">What you copy is the <b>public</b> half. It is safe to share and it '
        'is the only half you ever paste anywhere. The file without <b>.pub</b> on the end is your '
        '<b>private key</b> and must <b>never leave your computer</b> \u2014 not to us, not to '
        'anyone. Nobody legitimate will ever ask you for it.</p></details>'

        '<h3>Step 3 &mdash; Paste it below, then connect</h3>'
        '<p class="quiet">Paste it into the box below and press <b>Add this key</b>. Then go '
        'back to your terminal window and type the connect command shown further down this page.</p>'
        '<details><summary>What you will see when you connect</summary>'
        '<ul>'
        '<li><b>If it asks "The authenticity of host ... can\u2019t be established"</b> and whether '
        'you are sure \u2014 that is normal, not an error. Type <b>yes</b> and press Enter. It only '
        'asks the first time.</li>'
        '<li><b>You are in</b> when the prompt starts with <b>root@</b>. To leave, type <b>exit</b> '
        'and press Enter.</li>'
        '<li><b>If it still says "Permission denied (publickey)"</b> \u2014 the key did not save. '
        'Check it appears in the list on this page, and that you pasted all of it including '
        'the <b>ssh-ed25519</b> at the front.</li>'
        '</ul></details>'
        '<form method="post" action="/settings/access">'
        '<label for="pubkey">Your public key</label>'
        '<textarea id="pubkey" name="key" rows="4" required '
        'placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5... you@your-computer"></textarea>'
        '<button type="submit">Add this key</button></form></div>')

    where = box_access.where_to_connect(os.environ.get("DASHBOARD_BASE_URL", ""),
                                        str(request.host_url or ""))
    # THE ORDER IS THE INSTRUCTION, AND I HAD IT BACKWARDS. My own note here read "the connection
    # comes first, a stranger needs to see where the key takes them before being asked for one",
    # and OSDev4 built exactly that. Then the owner claimed a real box, ran the command at the top
    # of the page, and got `Permission denied (publickey)` — because on a box with no key of yours
    # the first thing this page offers is the one thing that cannot work yet.
    #
    # So the page leads with whatever the reader can actually DO. No key: the paste box first, and
    # the command below it saying plainly it will not work until the key is in. Key already added:
    # the command first, because that is the only reason they came back.
    #
    # THE CHROME PATH AND TITLE ARE #1470's, the ordering is #1469's, and both are wanted: the page
    # now sits under the Server access sub-menu AND leads with the doable step.
    has = bool(keys)
    connect = _connect_card(where, has, box_access.host_key_fingerprint())
    # SAID ONCE: with a command showing, the connect card already reports the empty state.
    rows = _key_rows(keys) if keys or not where.get("command") else ""
    return chrome("/settings/access", title="Server Access",
                  lede=("Add your key, then the machine is yours from your own terminal."
                        if not has else
                        "Put your own key on this box, and the machine is yours from your own terminal."),
                  body=(note + (connect + rows + form if has else form + connect + rows)
                        + danger_zone(move_note, checkin_note) + _back())), 200


# ── the danger zone ──────────────────────────────────────────────────────────────────────────────
# Owner, 2026-09-30: "we should actually have a danger zone area, where turning things off will
# disconnect their support and things like that and we don't want just anyone to be able to do
# this" — "an Admin or just the account owner". It sits at the foot of Server Access, the one page
# that is already the owner's alone (a member is refused it whole), and it is where a control goes
# when using it makes this box harder for us to help with. Moving the box was a page of its own
# and a link on System Settings; the owner: "We don't want this to be stumbled upon too much."
def danger_zone(move_note: str = "", checkin_note: str = "") -> str:
    """The owner's switches that cut this box loose from us, last on Server Access.

    GITHUB'S SHAPE, which the owner sent (2026-09-30, "it should have a warning like github does
    it"): the title in red, one red-edged box, a row per action — what it is and what it costs on
    the left, a red button on the right. The button only OPENS the row: a "Read this first" warning
    says what will happen, and nothing happens until the owner ticks that they understand and
    presses the second button. No script — a <details> per row, so it works on every phone."""
    if not _is_owner():
        return ""
    return ('<section class="danger" id="danger"><h2>Danger zone</h2>'
            '<p class="sub">Only the owner of this box sees these. Each one makes it harder for us '
            'to support you.</p>'
            f'<div class="dz">{_checkin_row(checkin_note)}{_move_row(move_note)}</div></section>')


def _dz_row(key: str, title: str, said: str, button: str, inside: str, note: str = "") -> str:
    """One row that opens: its button reads Cancel while it is open, and a result opens it."""
    return (f'<details class="dzrow" id="{key}"{" open" if note else ""}><summary>'
            f'<span class="what"><b>{_esc(title)}</b><span>{said}</span></span>'
            f'<span class="dzbtn"><span class="go">{_esc(button)}</span><span class="no">Cancel</span></span>'
            f'</summary><div class="dzbody">{note}{inside}</div></details>')


def _dz_plain(key: str, title: str, said: str, inside: str = "", note: str = "") -> str:
    """A row with nothing dangerous to press: what stands, and the safe way back if there is one."""
    return (f'<div class="dzrow" id="{key}"><span class="what"><b>{_esc(title)}</b>'
            f'<span>{said}</span></span>{note}{inside}</div>')


def _warn(text: str) -> str:
    return f'<div class="ui-notice bad" role="note"><b>Read this first.</b> {text}</div>'


# ── what release this box runs ───────────────────────────────────────────────────────────────────

def _ago(seconds) -> str:
    """"14 hours ago" from a number of seconds. Vague on purpose, and it stops at days.

    A buyer does not need the minute, they need to know whether it was recent — and the units stop
    at days because a box that has not checked in weeks has a problem no wording can soften.
    """
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "at an unknown time"
    if s < 0:
        return "at a time this box reads as the future"
    if s < 3600:
        m = max(1, s // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if s < 86400:
        h = s // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = s // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


@blueprint.route("/settings/updates")
def box_updates_screen():
    """What release this box is on, when it last looked, and how updates arrive.

    WE SELL A BOX THAT KEEPS GETTING BETTER and a buyer could not see it happening: three sections
    on the dashboard, and not one said what release the machine ran or that anything was arriving.
    A product that improves invisibly is, to the person paying for it, a product that does not
    improve. Owner, 2026-09-23, made this demo-critical.

    NO UPDATE BUTTON, DELIBERATELY. The box updates itself twice a day, so an update button would be
    a second way to do a thing that already happens — and a half-finished manual update on a
    customer's box is a far worse outcome than waiting twelve hours. The one button here is the way
    BACK when the box's own files have been edited (#1472 R3): it saves the changes under my/ and
    restores the files, so the box's own twice-daily check can install again.

    NOT OWNER-ONLY. Somebody who works in this box every day should be able to see whether it is
    current. This page publishes a version string and nothing else: no money, no keys, no customer
    data. The screens beside it gate on ownership because they hand out access or spend; this one
    does neither, and hiding it would be the dead end the walk suite already caught once.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    return _updates_page()


def _updates_page(note: str = ""):
    from core import box_updates

    try:
        st = box_updates.state()
    except Exception as e:                               # noqa: BLE001 — a settings screen never 500s
        st = {"release": None, "ok": None, "checked_at": None, "checked_s_ago": None,
              "said": f"This box could not read its own update state ({type(e).__name__}). It is "
                      f"still running the release it has; nothing has changed. Reload in a minute; if it "
                      f"stays, email {setup_errors.SUPPORT}."}

    release = st.get("release")
    resume_card = ('<div class="card"><h2>Managed</h2><p>Updates come with Ownbox Managed. Resume it '
                   'and this box starts receiving them again at its next check.</p>'
                   '<p><a href="/dash/managed">Resume Managed &rarr;</a></p></div>'
                   if st.get("state") == "no_managed" and _is_owner() else "")
    edited_card = _edited_card(st.get("changed") or [], _is_owner()) if st.get("state") == "edited" else ""
    # THE TAG, PLAINLY, AND NOT DRESSED UP AS A VERSION NUMBER. It is the string a buyer would
    # quote to us and the one our release list is keyed by. A prettier invented name here would
    # mean the thing they read and the thing we look up are two different strings.
    body = ('<div class="card"><h2>This box</h2>'
            + (f'<p class="quiet">Release</p><p class="addr">{_esc(release)}</p>'
               if release else '<p>This box cannot tell which release it is on.</p>')
            + f'<p>{_esc(st.get("said"))}</p>'
            + (f'<p class="quiet">Last checked {_esc(_ago(st.get("checked_s_ago")))}.</p>'
               if st.get("checked_at") else "")
            + '</div>'
            + resume_card + edited_card + _checkin_card(_is_owner()) +
            '<div class="card"><h2>How updates arrive</h2>'
            f'<p>{_esc(box_updates.HOW_UPDATES_ARRIVE)}</p>'
            + ('' if edited_card else
               '<p class="quiet">Nothing here needs pressing. The box does this on its own.</p>')
            + '</div>')
    return chrome("/settings/updates", title="Updates",
                  lede="What this box is running, and how it stays current.",
                  body=note + body + _back()), 200


_SHOWN_FILES = 20


def _checkin_card(owner: bool) -> str:
    """The box's check-in with Ownbox (docs/PLAN_NO_GHOST_BOXES.md P1): what it says, the exact last
    message, and, for the owner, the switch.

    THE MESSAGE ITSELF IS THE OWNER'S TO SEE. It is harmless, but it names the box's order, so it is
    shown to the owner only. Everybody else sees what it is for and whether it is on.
    SWITCHING OFF IS BEHIND A TICK, like putting files back: it is the one control here that makes this
    box harder for us to help, so it is never the easiest thing on the screen to hit by accident.
    """
    from core import checkin
    on, sent = checkin.enabled(), checkin.last()
    # EVERY FIELD THE CHECK-IN CARRIES IS NAMED HERE (docs/PLAN_TIER_INTEGRITY.md rule 4), in the same
    # change that adds it: the plan, and the people count with its limit, joined in step 2.
    what = ("This box tells Ownbox it is running, " + checkin.EVERY + ": which release it is on, "
            "whether its last update worked, which of its own health checks are failing, by name, its "
            "plan and the features it switches on, and how many people can sign in against its limit "
            "(a count, never who). "
            "Your messages, contacts, leads, settings and keys are never part of it. If it goes quiet, "
            "we notice and get in touch.")
    out = ['<div class="card"><h2>Check-in with Ownbox</h2>', f'<p>{_esc(what)}</p>']
    if not on:
        out.append('<p><b>Switched off.</b> Ownbox is told once that you turned it off, and nothing is '
                   'sent after that. We can no longer see whether this box is healthy.</p>')
    if sent.get("at"):
        told = "delivered" if sent.get("sent") else f'not delivered ({_esc(sent.get("result") or "")})'
        out.append(f'<p class="quiet">Last sent {_esc(_ago(_seconds_since(sent.get("at"))))} to '
                   f'{_esc(_host_of(sent.get("to") or ""))}: {told}.</p>')
        if owner and sent.get("payload"):
            out.append('<details><summary>Exactly what was sent</summary><pre class="addr" '
                       'style="white-space:pre-wrap;word-break:break-all">'
                       + _esc(json.dumps(sent["payload"], indent=1, sort_keys=True)) + '</pre></details>')
    elif on:
        out.append('<p class="quiet">Nothing sent yet. The first check-in goes out within a few minutes '
                   'of the box starting.</p>')
    # THE SWITCH IS IN THE DANGER ZONE (owner, 2026-09-30): switching this off is how a box goes
    # dark to us, which is what that zone is for. This card still says what it is and whether it is on.
    if owner:
        out.append('<p class="quiet">The switch is in the <a href="/settings/access#danger">danger '
                   'zone</a>, at the foot of Server Access.</p>')
    else:
        out.append('<p class="quiet">The owner of this box can switch this off.</p>')
    return "".join(out) + "</div>"


def _checkin_row(note: str = "") -> str:
    """The check-in's switch, in the danger zone: the control that makes this box hardest for us to
    help, so switching off takes the row opened, the warning read, a tick, and a second press.
    Switching back on is safe, so it is one press."""
    from core import checkin
    if not checkin.enabled():
        return _dz_plain("checkin", "Check-in with Ownbox is off",
                         "We can no longer see whether this box is healthy.",
                         '<form method="post" action="/settings/updates/checkin">'
                         '<input type="hidden" name="to" value="on">'
                         '<button type="submit">Switch back on</button></form>', note)
    inside = (_warn("This box stops telling Ownbox it is running. If it stops working, nobody here "
                    "will know until you tell us. Ownbox is told once that you switched it off, and "
                    "nothing is sent after that. You can switch it back on here at any time.")
              + '<p class="quiet"><a href="/settings/updates">Exactly what it sends &rarr;</a></p>'
              '<form method="post" action="/settings/updates/checkin">'
              '<input type="hidden" name="to" value="off">'
              '<label class="consent"><input type="checkbox" name="confirm" value="yes" required> '
              'Stop telling Ownbox this box is running</label>'
              '<button type="submit" class="danger">Switch off</button></form>')
    return _dz_row("checkin", "Stop checking in with Ownbox",
                   "We would no longer see whether this box is healthy, or know to reach you when it "
                   "goes quiet.", "Switch off check-in", inside, note)


def _seconds_since(iso) -> float | None:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - t).total_seconds())
    except (TypeError, ValueError):
        return None


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or url


def _edited_card(files: list, owner: bool) -> str:
    """Which of the box's own files are changed, and — for the owner — the way back.

    THE WAY BACK IS BEHIND A TICK, not a bare button: it restores files somebody may be working on,
    so it must never be the easiest thing on the screen to hit by accident. What it does is said
    before it is done — the changes are saved to my/ first, and nothing is deleted.
    """
    shown = "".join(f'<p class="addr">{_esc(f)}</p>' for f in files[:_SHOWN_FILES])
    more = (f'<p class="quiet">…and {len(files) - _SHOWN_FILES} more.</p>'
            if len(files) > _SHOWN_FILES else "")
    out = ['<div class="card"><h2>Changed on this box</h2>'
           '<p class="quiet">These came with the box and have been edited on it. While they are, '
           'the box keeps the release it has rather than install over somebody\'s work.</p>',
           shown, more]
    if owner:
        out.append('<form method="post" action="/settings/updates/put-back">'
                   '<label class="consent"><input type="checkbox" name="confirm" value="yes" '
                   'required> Save these changes to my/put-aside, then put the files back as they '
                   'came</label>'
                   '<button type="submit" style="margin-top:14px">Put them back</button></form>'
                   '<p class="quiet">Your changes are saved first, never deleted.</p>'
                   '<p class="quiet">Build your own work into a machine in my/ instead, and '
                   'updates keep arriving.</p>')
    else:
        out.append('<p class="quiet">The owner of this box can put them back from this page.</p>')
    return "".join(out) + "</div>"


def _put_back_note(res: dict) -> str:
    """The result, drawn straight onto the page the POST returns — never carried in a URL, where
    anybody could write a link that makes this box say something it did not."""
    tone = "" if res.get("ok") else ' style="border-color:var(--danger)"'
    return (f'<div class="card"{tone}><h2>{"Put back" if res.get("ok") else "Not finished"}</h2>'
            f'<p>{_esc(res.get("said") or "")}</p></div>')


@blueprint.route("/settings/updates/put-back", methods=["POST"])
def box_updates_put_back():
    """Save the changes to the box's own files under my/, then restore them. Owner-only.

    OWNER-ONLY ON THE POST, not just the page: it rewrites files on the machine. The tick is checked
    here too, so a form posted without it does nothing.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome("/settings/updates", title="Updates", lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can put its files '
                           'back.</p></div>' + _back()), 403
    from core import box_updates
    if request.form.get("confirm") != "yes":
        res = {"ok": False, "said": "Nothing was changed — tick the box to confirm first."}
    else:
        res = box_updates.put_back(by=str(_who().get("id") or ""))
    return _updates_page(_put_back_note(res))


@blueprint.route("/settings/updates/checkin", methods=["POST"])
def box_checkin_switch():
    """Switch the box's check-in off or back on. Owner-only, on the POST as well as the page.

    Either way one check-in goes out now: switching off sends the single "switched off" message (so
    Ownbox knows this box chose quiet rather than went dark), switching on sends a fresh one.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if not _is_owner():
        return chrome("/settings/updates", title="Updates", lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can switch its check-in '
                           'off or on.</p></div>' + _back()), 403
    from core import checkin
    to = request.form.get("to")
    # DRAWN BACK ONTO THE DANGER ZONE, where the switch is, and never carried in a URL.
    if to == "off" and request.form.get("confirm") != "yes":
        return box_access_screen(checkin_note='<p class="stale">Nothing was changed — tick the box '
                                              'to confirm first.</p>')
    if to not in ("on", "off"):
        return box_access_screen(checkin_note='<p class="stale">Nothing was changed.</p>')
    checkin.set_enabled(to == "on", by=str(_who().get("id") or ""))
    _start_checkin()
    said = ("Switched off. Ownbox is being told once, and nothing is sent after that." if to == "off"
            else "Switched back on. A check-in is on its way now.")
    return box_access_screen(checkin_note=f'<p class="ok">{_esc(said)}</p>')


def _start_checkin() -> None:
    """Start one check-in now, as its own unit, without waiting for it. Nothing here can fail the page."""
    if os.environ.get("AIOS_HERMETIC_TEST"):
        return
    try:
        subprocess.Popen(["systemctl", "start", "--no-block", "aios-checkin.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        pass


# ── take this box to your own DigitalOcean account ──────────────────────────────────────────────────

# One sentence per state, in one place. The provisioner writes the state; this is what a buyer reads.
_MOVE_SAID = {
    "requested": ("Waiting to start",
                  "Your request is in. Within a few minutes we will start making the copy."),
    "imaging": ("Making the copy",
                "A full copy of this box is being made. This box pauses its work while that "
                "happens — usually a few minutes — and then carries on by itself. If you are "
                "reading this on the NEW server in your own account, this is the copy: press "
                "Start on the dashboard to switch it on."),
    "sent": ("Sent to your account",
             "The copy is on its way to your DigitalOcean account. Next: open the email "
             "DigitalOcean sends you and accept it. Then in DigitalOcean go to Backups & Snapshots, "
             "then Snapshots, and create a server from it. The copy starts paused so it cannot work "
             "your inbox at the same time as this one — press Start on its dashboard when you are "
             "ready."),
    "failed": ("That did not work",
               "The copy could not be made or sent. Nothing about this box has changed and it is "
               "running as before. You can try again below."),
}


@blueprint.route("/settings/move", methods=["GET", "POST"])
def box_move_screen():
    """Take this box to the owner's own DigitalOcean account. Owner, 2026-09-23: a button, now.

    NO LONGER A PAGE (owner, 2026-09-30): the card lives in the danger zone at the foot of Server
    Access, and this address is where its form posts. A GET — an old link, a bookmark — lands on
    the card.

    WHAT IT CAN AND CANNOT DO, said on the screen because a buyer will assume otherwise: DigitalOcean
    cannot move a running server, or its IP address, between accounts. It moves a full COPY — an
    image — to the email of the receiving account. So the button makes that copy and sends it; the
    buyer builds a server from it in their own account. Everything on the box comes with it.

    THIS SCREEN ONLY RECORDS THE REQUEST. The box holds no DigitalOcean key and could not image
    itself if it tried; the provisioner, which does, reads the request and does the work
    (core/box_move.py). That split is the reason a stolen box cannot be used to copy anybody else's.

    OWNER-ONLY, on the card AND the post. A copy of the box carries every conversation on it, and
    choosing where that goes is the owner's decision alone.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    if request.method != "POST":
        return redirect("/settings/access#move", code=303)
    if not _is_owner():
        return box_access_screen()                       # the owner's page refuses a member, 403
    from core import box_move

    note = ""
    do = str(request.form.get("do") or "")
    who = (_who().get("email") or _who().get("id") or None)
    if do == "cancel":
        note = ('<p class="ok">Cancelled. Nothing was copied.</p>' if box_move.cancel(by=who) else
                '<p class="stale">That can no longer be cancelled — the copy has already been '
                'started.</p>')
    elif do == "request":
        if request.form.get("confirm") != "yes":
            note = ('<p class="stale">Tick the box to confirm you want a copy of this box sent to '
                    'that account.</p>')
        else:
            try:
                box_move.request(request.form.get("email") or "", by=who)
            except box_move.MoveRefused as e:
                note = f'<p class="stale"><b>Not started.</b> {_esc(e)}</p>'
    return box_access_screen(move_note=note)


def _move_row(note: str = "") -> str:
    """Where a move stands, or how to start one: a row of the danger zone."""
    from core import box_move
    now = box_move.current()
    title = "Move your box to your own DigitalOcean account"
    if not now:
        return _dz_row("move", title, "A full copy of everything on it goes to an account you name, "
                       "and we have no access to the server it becomes.", "Move this box",
                       _move_warning() + _move_explainer() + _move_form(), note)
    state, (head, said) = now.get("state"), _MOVE_SAID.get(now.get("state"), ("In progress", ""))
    where = (f'<p class="quiet">Sending to</p><p class="addr">{_esc(now.get("email"))}</p>'
             + (f'<p class="quiet">{_esc(now.get("detail"))}</p>' if now.get("detail") else ""))
    if state in ("sent", "failed"):
        return _dz_row("move", title, f'<b>{_esc(head)}.</b> {_esc(said)}', "Send another copy",
                       where + _move_warning() + _move_form(), note)
    cancel = ('<form method="post" action="/settings/move"><input type="hidden" name="do" value="cancel">'
              '<button type="submit">Cancel — do not copy this box</button></form>'
              if state == "requested" else "")
    return _dz_plain("move", title, f'<b>{_esc(head)}.</b> {_esc(said)}', where + cancel, note)


def _move_warning() -> str:
    return _warn("A copy of every conversation, contact and setting on this box goes to the "
                 "DigitalOcean account you name. Once the copy has started it cannot be stopped. The "
                 "server it becomes is yours alone: we cannot see it, update it or help with it.")


def _move_explainer() -> str:
    return ('<h3>What happens</h3>'
            '<p>We make a full copy of this box — everything on it — and send it to the '
            'DigitalOcean account you name. You accept it there and create a server from it. '
            'That server is yours, on your own bill, and we have no access to it.</p>'
            '<p class="quiet">DigitalOcean does not let a server keep its numeric address when it '
            'changes accounts, so the new one gets a new address. The web address you use '
            'stays the same once it is pointed at the new server.</p>'
            '<p class="quiet">You need a DigitalOcean account first. Creating one is free at '
            'digitalocean.com.</p>')


def _move_form() -> str:
    # A DESTRUCTIVE-LOOKING ACTION IS NEVER THE EASIEST THING TO HIT BY ACCIDENT (mobile-first ruling).
    # This is not destructive — the original keeps running — but it sends a copy of every
    # conversation to another account, so it takes a typed address AND a tick, not one tap.
    return ('<form method="post" action="/settings/move">'
            '<input type="hidden" name="do" value="request">'
            '<label for="do-email">Your DigitalOcean account email</label>'
            '<input id="do-email" name="email" type="email" autocomplete="email" required '
            'placeholder="you@yourcompany.com">'
            '<label class="consent"><input type="checkbox" name="confirm" value="yes"> Send a full copy of this '
            'box, and everything on it, to that account</label>'
            '<button type="submit" class="danger">Send a copy to my account</button></form>')


# THE PROVISIONER'S TWO REQUESTS, behind the /deploy prefix so `_auth_gate` holds them to the per-box
# deploy token and nothing else. See `_deploy_authorized` in core/dispatch.py for what that token
# may now do and why.
@blueprint.get("/deploy/move-request")
def deploy_move_request():
    """What the provisioner reads: a pending move and its address, or nothing."""
    from core import box_move
    return jsonify({"move": box_move.pending()}), 200


@blueprint.post("/deploy/updates-plan")
def deploy_updates_plan():
    """The provisioner telling this box whether updates come with its plan (#1472 R9).

    Behind the per-box deploy token, like the move. It stores "on" or "off" and a date, and nothing else;
    an unknown value is refused, never stored. It EXPLAINS the Updates screen — what actually stops
    updates is the box's key removed on Ownbox's side, so a forged "on" here buys nothing."""
    from core import box_updates
    body = request.get_json(silent=True) or {}
    if "updates" not in body and "ends" not in body:
        return jsonify({"error": "bad_plan", "message": "nothing to store"}), 400
    try:
        if "updates" in body:
            box_updates.set_plan(str(body.get("updates") or ""), str(body.get("until") or ""))
        if "ends" in body:                       # #1857 D9c: when a cancelled Managed ends, "" when it is not
            box_updates.set_managed_ends(str(body.get("ends") or ""))
    except ValueError as e:
        return jsonify({"error": "bad_plan", "message": str(e)}), 400
    return jsonify({"ok": True}), 200


@blueprint.post("/deploy/plan")
def deploy_plan():
    """Ownbox telling this box what its plan is (docs/SCOPE_TIERS.md §2.2): {"seq", "tier", "add"}.

    Behind the per-box deploy token, like the two above. An unknown tier or feature is refused and
    never stored. A seq that is not newer than the one held is refused, so a delayed older message
    never undoes a newer one. EVERY answer carries the plan the box now holds: that reply is how
    the provisioner knows its message landed, including when a retry arrives after the first did."""
    from core import tiers
    try:
        return jsonify({"ok": True, "plan": tiers.set_plan(request.get_json(silent=True) or {})}), 200
    except tiers.PlanRefused as e:
        return jsonify({"error": "stale_seq" if e.stale else "bad_plan", "message": str(e),
                        "plan": tiers.current()}), (409 if e.stale else 400)
@blueprint.post("/deploy/upgrade-status")
def deploy_upgrade_status():
    """The provisioner saying where this box's upgrade to Pro is (docs/SCOPE_UPGRADE_TO_PRO.md).

    A stage from a fixed list and a short detail, nothing else. An unknown stage is refused."""
    from core import upgrade
    body = request.get_json(silent=True) or {}
    try:
        upgrade.set_status(str(body.get("stage") or ""), str(body.get("detail") or ""))
    except ValueError as e:
        return jsonify({"error": "bad_stage", "message": str(e)}), 400
    return jsonify({"ok": True}), 200


@blueprint.post("/deploy/prepare-restart")
def deploy_prepare_restart():
    """The provisioner asking the box to get ready to be shut down and resized.

    200 once the box holds the update lock; 202 with the reason while a coworker works or an update
    installs (the provisioner asks again on its next run); 409 when no upgrade is under way."""
    from core import upgrade
    got = upgrade.prepare_restart()
    if got.get("ready"):
        return jsonify(got), 200
    return jsonify(got), (409 if got.get("refused") else 202)


@blueprint.post("/deploy/move-status")
def deploy_move_status():
    """The provisioner reporting progress. An unknown state is refused, never stored."""
    from core import box_move
    body = request.get_json(silent=True) or {}
    try:
        box_move.set_status(str(body.get("state") or ""), str(body.get("detail") or ""))
    except ValueError as e:
        return jsonify({"error": "bad_state", "message": str(e)}), 400
    return jsonify({"ok": True}), 200


# THE THREE DOORS ABOVE ARE WHAT `core/dash/home.py` OFFERS, and it learns them from the step data
# rather than from this file: `_AI_STEP["action_href"]`, `_PHONE_STEP["action_href"]` and
# `_AGENT_STEP["link"]["url"]` all name paths served here. Keeping the stnotifies in the contract is
# what lets one screen render a step it has never heard of.
_DOORS = ("/settings/ai", "/settings/mobile", "/settings/agent", "/settings/access",
          "/settings/updates", "/settings/email", "/settings/sources")
# The handout hangs off the mobile-app door rather than being one of its own: it is not a
# step a buyer finishes, it is a sheet they hand to somebody else.
_HANDOUT = "/settings/mobile/print"


def registered_doors() -> tuple:
    """The core paths this module serves, for a test to check the contract still points at them."""
    return _DOORS

