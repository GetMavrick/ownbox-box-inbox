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

from flask import jsonify, redirect, request

from core.dash import blueprint
from core.dash.home import chrome


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
        return chrome("/settings", title="Your AI account",
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
                return redirect("/settings", code=303)
            elif action == "cancel":
                claude_login.cancel()
                return redirect("/settings/ai", code=303)
            elif action == "key":
                # ONE FIELD, EITHER CREDENTIAL — the router decides, not the buyer and not a radio
                # button. An sk-ant-oat… token is stored as a subscription and selects the
                # claude_code backend; an API key takes the verified path it always did.
                box_secrets.put_ai_credential(
                    str(request.form.get("key") or ""),
                    consented=bool(request.form.get(
                        str(_step("anthropic").get("consent_field") or "subscription_consent"))),
                    user_id=who.get("id"))
                return redirect("/settings", code=303)
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
    body = ['<div class="card"><p>' + _esc(e.get("why") or
            "This is what writes your replies, on your own account and your own bill.")
            + '</p></div>', _picker(e, picked)]
    if note:
        body.append(f'<div class="card"><p>{_esc(note)}</p></div>')

    # A MODEL THIS BOX CANNOT DRAFT WITH GETS THE WHOLE SCREEN TO ITSELF AND NO FIELD ON IT.
    # Owner, 2026-09-22: *"the page is gonna have to be completely different according to which
    # drop-down is chosen."* It is — and the half that must not vary is that nothing here will
    # take a credential it cannot use.
    if not picked.get("available"):
        body.append(_preview(picked, _models(e)))
        body.append(_back())
        return chrome("/settings", title="Your AI account",
                      lede=f"What {picked.get('name')} would look like on this box.",
                      body="".join(body)), 200

    # A LIVE MODEL CONNECTED SOMEWHERE ELSE GETS ITS OWN DOOR, AND NONE OF CLAUDE'S CARDS.
    # This is the half of the owner's ruling that was missing while ChatGPT was still greyed out:
    # picking it changed the heading and then drew the Claude sign-in underneath, which is the
    # screen lying about what the button in front of you does. Every card below this line is
    # Anthropic's — the link, the code field, the sk-ant… form — so a model that connects
    # elsewhere returns before any of them is built.
    if (picked.get("connect_href") or "/settings/ai") != "/settings/ai":
        body.append(_door(picked))
        body.append(_back())
        return chrome("/settings", title="Your AI account",
                      lede=f"Sign in with {picked.get('name')}.",
                      body="".join(body)), 200

    if url:
        body.append(
            '<div class="card">'
            '<p><b>1.</b> Open this link and sign in to Claude, then approve access.</p>'
            f'<p style="margin:12px 0"><a href="{_esc(url)}" target="_blank" '
            'rel="noopener noreferrer">Sign in to Claude &rarr;</a></p>'
            f'<p class="quiet" style="word-break:break-all">{_esc(url)}</p>'
            '<p><b>2.</b> Claude will show you a short code. Paste it here.</p>'
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
            '<p class="quiet" style="margin-top:12px">This box never sees your password. You sign '
            'in at claude.com and paste back a short code.</p></div>')

    # THE SECOND DOOR, under the first, only when the step declares one (a box mid-update may not).
    if e.get("alt_action_href"):
        body.append(
            '<div class="card"><h2>Or use your ChatGPT subscription</h2>'
            f'<p>{_esc(e.get("alt_action_why") or "")}</p>'
            f'<p><a href="{_esc(e.get("alt_action_href"))}">'
            f'{_esc(e.get("alt_action_label") or "Sign in to ChatGPT")} &rarr;</a></p></div>')
    body.append(_key_form(e))
    body.append(_back())
    return chrome("/settings", title="Your AI account",
                  lede="What writes your drafts, on your own account.",
                  body="".join(body)), 200


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
        return chrome("/settings", title="Your AI account",
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
    body = ['<div class="card"><p>Sign in to ChatGPT on your phone or computer and this box will '
            'draft on your own subscription. There is no key, and the box never sees your '
            'password.</p></div>']
    if note:
        body.append(f'<div class="card"><p>{_esc(note)}</p></div>')
    refresh = ""
    if live.get("url") and live.get("code"):
        body.append(
            '<div class="card">'
            '<p><b>1.</b> Open this link on any device and sign in to ChatGPT.</p>'
            f'<p style="margin:12px 0"><a href="{_esc(live["url"])}" target="_blank" '
            'rel="noopener noreferrer">Open ChatGPT &rarr;</a></p>'
            f'<p class="quiet" style="word-break:break-all">{_esc(live["url"])}</p>'
            '<p><b>2.</b> Enter this one-time code when it asks.</p>'
            '<p style="font:600 28px/1.2 ui-monospace,Menlo,monospace;letter-spacing:0.08em;'
            f'margin:10px 0 16px;user-select:all">{_esc(live["code"])}</p>'
            '<p class="quiet">The code lasts fifteen minutes. This page checks itself every few '
            'seconds and takes you back to settings when the sign-in is done.</p>'
            '<form method="post" action="/settings/chatgpt" style="margin-top:12px">'
            '<input type="hidden" name="do" value="cancel">'
            '<button type="submit">Cancel</button></form></div>')
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
    page = chrome("/settings", title="Your AI account",
                  lede="Sign in with ChatGPT — a one-time code, no key.", body="".join(body))
    if refresh and "</head>" in page:
        page = page.replace("</head>", refresh + "</head>", 1)
    return page, 200


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
            '<noscript><button type="submit">Show</button></noscript>'
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
            + '<button type="submit">Save</button></form>'
            + (f'<p class="quiet" style="margin-bottom:0">{_esc(e.get("terms_note"))}</p>'
               if e.get("terms_note") else "")
            + '</div>')


# ── the mobile app ────────────────────────────────────────────────────────────────────────────────────

# THE INSTRUCTIONS COME FROM THE CONTRACT, NOT FROM THIS FILE. `_MOBILE_STEP["platforms"]` carries
# them, because TWO screens render these words — this page and the sheet a colleague is handed —
# and a second copy laid out for paper is a second copy that drifts. The one that drifts is the
# printed one: nobody re-reads a page they already pinned to a wall.
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
    body = ['<div class="card"><p>' + _esc(e.get("why") or "") + '</p></div>',
            '<div class="card">' + _platform_cards() + '</div>']

    # THE STATE, SAID AS NARROWLY AS THE SERVER CAN HONESTLY SAY IT. A subscription row proves SOME
    # device on this box is set up; it can never prove the one in your hand is, because the same
    # person reading this on a laptop has a mobile the box cannot see. So it reports the box, and
    # says out loud that the box is what it is reporting.
    detail = str(e.get("detail") or "").strip()
    body.append('<div class="card"><p><b>Where this box stands</b></p>'
                + (f'<p>{_esc(detail)}.</p>' if detail else
                   '<p>No device on this box has notifications switched on yet.</p>')
                + '<p class="quiet">This is what the box can see across everybody who uses it. It '
                  'cannot tell whether the device you are holding is one of them — open the box '
                  'from its Home Screen icon and it will know.</p></div>')

    # WHEN THE ASKING HAPPENS, said plainly, because a set-up step that ends with nothing switched
    # on reads as a step that failed. It did not: the box is waiting on purpose.
    body.append('<div class="card"><p><b>Turning them on</b></p>'
                '<p>The box asks you once, on the screen where you read your messages, the first '
                'time something real arrives. It waits on purpose: a mobile only lets you answer '
                'that question once, and saying no is hard to undo.</p>'
                '<p class="quiet">Email keeps arriving either way. This is the faster way to hear '
                'about it, never the only way.</p></div>')

    if not ok:
        # AN HONEST EMPTY ANSWER BEATS FOUR STEPS THAT CANNOT SUCCEED. A box whose release predates
        # the crypto dependency cannot mint a push identity at all.
        body.append('<div class="card"><p>One thing first: this box cannot send notifications yet '
                    f'— {_esc(why)}. The steps above still work and are worth doing; the box will '
                    'be able to notify you once it updates itself.</p></div>')

    body.append('<div class="card"><p><b>For somebody else on this box</b></p>'
                '<p>Whoever watches the inbox is often not whoever bought the box. This page '
                'prints onto one sheet you can hand over or leave by the till — it carries no '
                'password and nothing private, just these instructions and the address.</p>'
                '<p><a href="/settings/mobile/print">Print this for a colleague &rarr;</a></p>'
                '</div>')
    body.append(_back())
    return chrome("/settings", title="Your mobile app",
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


# ── the AI coworkers ─────────────────────────────────────────────────────────────────────────────

# THE WORDS HERE ARE A PERMISSION LIST A BUYER READS BEFORE HANDING OVER A KEY, so they have to
# name everything the role can see. `read` gained the box's own health and `act` gained its
# SPEND (core/box_tools.py), and a role described as "read your conversations" while it can also
# read the billing figure is a consent screen that misleads. Whenever _ROLE_CAPABILITIES grows,
# this text grows with it.
_ROLE_CHOICES = (
    ("read", "Read only",
     "It can read your conversations, your morning report, and whether the box is running. It "
     "cannot write anything, and it cannot see what the box is spending."),
    ("act", "Read and draft replies",
     "Everything above, plus what the box has spent against its monthly ceiling, and it can "
     "leave a suggested reply waiting on the screen. It still cannot send - you press send, or "
     "you do not."),
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
        human = next((t for r, t, _ in _ROLE_CHOICES if r == role), role)
        if s.get("revoked_at"):
            out.append(f'<div class="row"><b style="flex:1;min-width:0">{label}</b>'
                       f'<span class="quiet">Revoked. It can no longer reach this box.</span>'
                       f'</div>')
        else:
            out.append(f'<div class="row"><b style="flex:1;min-width:0">{label}</b>'
                       f'<span class="quiet">{_esc(human)}</span>'
                       f'<a href="/settings/agent?revoke={_esc(s.get("id"))}">Revoke</a></div>')
    out.append('</div>')
    return "".join(out)


def _seat_form(note: str = "") -> str:
    opts = "".join(
        f'<label style="display:block;margin:8px 0"><input type="radio" name="role" '
        # READ-AND-DRAFT BY DEFAULT — #1419 (OSDev1), carried across. Owner, 2026-09-22: "we're
        # always going to want to read and draft." A draft is not a send; nothing reachable from
        # a seat can send, so defaulting to `read` protected nobody and cost him a connection
        # with no draft_reply. The choice stays; only the preselected answer moves.
        f'value="{r}"{" checked" if r == "act" else ""}> <b>{t}</b><br>'
        f'<span class="quiet" style="margin-left:22px">{w}</span></label>'
        for r, t, w in _ROLE_CHOICES)
    return (note +
            '<form method="post" action="/settings/agent" class="card">'
            '<input type="hidden" name="do" value="mint">'
            '<label for="seat-label">What should it be called?</label>'
            '<p class="quiet">A name you will recognise later, so you know what you are '
            'revoking.</p>'
            '<input id="seat-label" name="label" maxlength="60" placeholder="Grok on X" required>'
            '<p><b>What may it do?</b></p>' + opts +
            '<button type="submit">Create the connection</button></form>')


def _seat_credential(label: str, credential: str, url: str) -> str:
    """Shown ONCE. `seats.mint` never stores the secret, so there is no second chance by design.

    THE PAGE SAYS SO BEFORE THE STRING, not after it. Somebody who scrolls past a key and closes
    the tab has lost it, and the only repair is revoking a seat they never used and making another.
    """
    return ('<div class="card"><h2>Copy this now.</h2>'
            f'<p class="quiet">This is the only time <b>{_esc(label)}</b>\'s key will ever be '
            'shown. The box keeps a one-way hash of it and nothing else, so if you lose it, revoke '
            'this connection and make another — there is no way to look it up.</p>'
            f'<p class="addr">{_esc(credential)}'
            '</p></div>'
            '<div class="card"><p><b>Address</b> — give your assistant this and that key. It '
            'speaks MCP.</p>'
            f'<p class="addr">{_esc(url)}</p>'
            '</div>')


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
        return chrome("/settings", title="AI coworkers",
                      lede="This one is the owner's.",
                      body='<div class="card"><p>Only the owner of this box can connect an AI '
                           'coworker, because the connection can read every message on it.</p>'
                           '</div>' + _back()), 403

    note = ""
    revoke = (request.args.get("revoke") or "").strip()
    if revoke:
        seats.revoke(revoke)
        return redirect("/settings/agent", code=303)

    if request.method == "POST" and str(request.form.get("do") or "") == "mint":
        label = str(request.form.get("label") or "").strip()[:60]
        role = str(request.form.get("role") or "act").strip()
        if role not in [r for r, _, _ in _ROLE_CHOICES]:
            role = "read"
        try:
            _sid, credential = seats.mint(label, role)
        except ValueError as e:
            note = f'<p class="quiet">{_esc(e)}</p>'
        else:
            root = str(request.host_url or "").rstrip("/")
            # NEVER A REDIRECT AND NEVER A QUERY STRING. The credential is rendered into this one
            # response and then it is gone: a redirect would put it in a URL, and gunicorn logs
            # raw query stnotifies.
            return chrome("/settings", title="AI coworkers",
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
                                 '&larr; AI coworkers</a></div>'), 200

    root = str(request.host_url or "").rstrip("/")
    clients = "".join(f'<div class="row"><b style="flex:1;min-width:0">{_esc(c["name"])}</b>'
                      f'<span class="quiet">{_esc(c["how"])}</span></div>'
                      for c in getattr(box_secrets, "AGENT_CLIENTS", ()))
    body = ('<div class="card"><p>Your box can be read by an assistant you already pay for — '
            'Claude, ChatGPT, Grok — so you can ask it about your customers where you already '
            'work. You give it a key, you choose what it may do, and you can take that key away '
            'at any moment.</p>'
            '<p class="quiet">Nothing you connect here can send a message as your business. The '
            'most a coworker can do is leave a reply waiting on the screen for you.</p></div>'
            # THERE IS NO KEY TO COPY, and this sentence said there was. Owner, 2026-09-22, after
            # connecting Claude himself: "we're gonna have totally different instructions where we
            # just enter the MCP server and it authorizes." Since the OAuth front door (#1419) that
            # is what happens — the assistant is sent here to sign in — and a line telling a buyer
            # to fetch a key first sends them to the hard path we stopped needing. The form below
            # still exists for an assistant that cannot sign in; it is a fallback, not the route.
            '<div class="card"><p><b>This box\'s address</b> — paste this into whichever '
            'assistant you use. It will send you here to sign in; there is no key to copy.</p>'
            f'<p class="addr">{_esc(root)}/mcp'
            '</p></div>'
            # EVERY ONE OF THESE IS LIVE. They connect TO the box over MCP; the box never calls
            # them and holds nothing of theirs, which is why this list needs nothing greyed out.
            + (f'<div class="card"><p><b>Works with</b></p>{clients}</div>' if clients else "")
            # THE STEPS BEFORE THE FORM, AND THE FORM DEMOTED — #1419 (OSDev1), carried across
            # while this screen moved into core. Every assistant worth naming signs in now
            # (core/connector/oauth.py), and the owner's words after doing it were "people will
            # probably just choose sign in". A screen that opens with a secret to copy teaches the
            # harder path first. The three steps are core's own words (`box_secrets.AGENT_STEPS`).
            + '<div class="card"><ol style="padding-left:20px;margin:0">'
            + "".join(f'<li style="margin:6px 0">{_esc(t)}</li>'
                      for t in getattr(box_secrets, "AGENT_STEPS", ()))
            + '</ol></div>'
            + '<details style="margin-top:18px"><summary style="cursor:pointer">'
              'Or make a key by hand</summary>'
              '<p class="quiet" style="margin:10px 0">For a script, or an assistant that cannot '
              'sign in. The key is shown once and you keep it safe yourself — signing in '
              'above is easier and revoking it is the same button.</p>'
            + _seat_form(note)
            + '</details>'
            + _seat_rows(seats.all_seats())
            + _back())
    return chrome("/settings", title="AI coworkers",
                  lede="Let an assistant you already pay for read this box.",
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


@blueprint.route("/settings/access", methods=["GET", "POST"])
def box_access_screen():
    """Add the owner's own SSH key to this box, or take one away.

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
        return chrome("/settings", title="Your way in",
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

    if request.method == "POST":
        try:
            text, fp = box_access.add(box_access.read(), request.form.get("key") or "")
            box_access.write(text)
            note = (f'<div class="card"><h2>That key is in.</h2><p class="quiet">Its fingerprint '
                    f'is below — check it against your own machine before you rely on it.</p>'
                    f'<p class="addr">{_esc(fp)}</p></div>')
        except box_access.KeyRefused as e:
            # THE REFUSAL IS THE TEACHING. Every message from box_access names what to do instead,
            # so it is shown verbatim rather than replaced with "invalid key".
            note = f'<div class="card"><h2>That was not stored.</h2><p>{_esc(e)}</p></div>'
        except OSError as e:                             # noqa: BLE001
            note = (f'<div class="card"><h2>The box could not write the file.</h2>'
                    f'<p class="quiet">{_esc(type(e).__name__)} — nothing was changed.</p></div>')

    try:
        keys = box_access.listed(box_access.read())
    except OSError:
        keys = []
        note += ('<div class="card"><p>This box cannot read its key file right now, so the list '
                 'below may be incomplete. Nothing has been changed.</p></div>')

    form = (
        '<div class="card"><h2>Add your key</h2>'
        '<p class="quiet">On your own computer, open a terminal and run '
        '<b>cat ~/.ssh/id_ed25519.pub</b>. If it says no such file, run '
        '<b>ssh-keygen -t ed25519</b> first and press enter at every question, then run the first '
        'command again. Paste the whole line it prints — it begins with ssh-ed25519 and it is '
        'safe to share. The file WITHOUT .pub on the end is your private key and must never leave '
        'your computer.</p>'
        '<form method="post" action="/settings/access">'
        '<label for="pubkey">Your public key</label>'
        '<textarea id="pubkey" name="key" rows="4" required '
        'placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5... you@your-computer"></textarea>'
        '<button type="submit">Add this key</button></form></div>')

    return chrome("/settings", title="Your way in",
                  lede="Put your own key on this box, and the machine is yours at the command line.",
                  body=note + _key_rows(keys) + form + _back()), 200


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
        return f"{max(1, s // 60)} minutes ago"
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

    READ-ONLY, DELIBERATELY, and it does not need a button. The box updates itself twice a day, so
    an update button would be a second way to do a thing that already happens — and a half-finished
    manual update on a customer's box is a far worse outcome than waiting twelve hours.

    NOT OWNER-ONLY. Somebody who works in this box every day should be able to see whether it is
    current. This page publishes a version string and nothing else: no money, no keys, no customer
    data. The screens beside it gate on ownership because they hand out access or spend; this one
    does neither, and hiding it would be the dead end the walk suite already caught once.
    """
    refuse = _admit(owner_only=False)
    if refuse is not None:
        return refuse
    from core import box_updates

    try:
        st = box_updates.state()
    except Exception as e:                               # noqa: BLE001 — a settings screen never 500s
        st = {"release": None, "ok": None, "checked_at": None, "checked_s_ago": None,
              "said": f"This box could not read its own update state ({type(e).__name__}). It is "
                      f"still running the release it has; nothing has changed."}

    release = st.get("release")
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
            '<div class="card"><h2>How updates arrive</h2>'
            f'<p>{_esc(box_updates.HOW_UPDATES_ARRIVE)}</p>'
            '<p class="quiet">Nothing here needs pressing. The box does this on its own.</p>'
            '</div>')
    return chrome("/settings", title="Updates",
                  lede="What this box is running, and how it stays current.",
                  body=body + _back()), 200


# THE THREE DOORS ABOVE ARE WHAT `core/dash/home.py` OFFERS, and it learns them from the step data
# rather than from this file: `_AI_STEP["action_href"]`, `_PHONE_STEP["action_href"]` and
# `_AGENT_STEP["link"]["url"]` all name paths served here. Keeping the stnotifies in the contract is
# what lets one screen render a step it has never heard of.
_DOORS = ("/settings/ai", "/settings/mobile", "/settings/agent", "/settings/access",
          "/settings/updates")
# The handout hangs off the mobile-app door rather than being one of its own: it is not a
# step a buyer finishes, it is a sheet they hand to somebody else.
_HANDOUT = "/settings/mobile/print"


def registered_doors() -> tuple:
    """The core paths this module serves, for a test to check the contract still points at them."""
    return _DOORS

