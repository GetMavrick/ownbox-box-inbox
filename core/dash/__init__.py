import re
import os
"""core.dash — the two-page shell every machine's GUI is built on.

Extracted VERBATIM from marketing/content_machine/reel/dashboard.py (Phase 1 of
docs/MACHINE_GUI_PLAN.md). Nothing here is new: it is the ~200 generic lines that
were already carrying the reel dashboard, lifted so the Lead Machine — and every
machine after it — gets the same shell instead of a second copy that drifts.

WHAT BELONGS HERE: sessions, the login, the CSS, the page frame, and the small
formatters every listing needs. WHAT NEVER DOES: anything that knows what a row
IS. No reels, no leads, no vendors — tests/test_dash_boundary.py fails the build
if this package imports marketing/.

THE LOGIN IS MOUNTED ONCE, BY THE KERNEL. core/dispatch.py registers this
blueprint directly rather than through config `web_modules:`, because it is core,
not a department. That is also what makes a box with two machines show ONE login
at one /dash (MACHINE_PACKS_PLAN D1b) instead of two hosts racing to claim the
same route.
"""
import hmac
import time
import html
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import Blueprint, make_response, redirect, request

from core import claim as _claim
from core import state
from core.config import get_config, settings
from core.logging import get_logger

log = get_logger(__name__)
blueprint = Blueprint("core_dash", __name__)

COOKIE = "aios_session"
SESSION_DAYS = 30


# ── sessions ─────────────────────────────────────────────────────────────────

# EVERY SESSION BELONGS TO SOMEBODY. There is no such thing as a session with no person, and
# NULL is emphatically NOT the owner — that reading fails OPEN, because any future path that
# forgets to set `user_id`, or any row written without it, would become the owner of the whole
# box. Migration 47 makes the assignment ONCE: it creates the owner's row and backfills every
# session that predates it, so the owner's 30-day cookie keeps working (2026-09-07, "I don't
# ever wanna be locked out of these machines") because it points at a real row — not because a
# missing value is being read generously on every request forever.


def new_session(user_id: str) -> str:
    """Mint a session for a REAL, ACTIVE person. There is no other kind.

    Refuses rather than writing a session that resolves to nobody. A row like that cannot
    authenticate anything (`session_user` would refuse it), so minting one only produces the
    worst kind of bug report: signed in, and nothing works, with no error anywhere.
    """
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("a session must belong to a user — see migration 47")
    who = state.get_user(uid)
    if not who or not who.get("active"):
        raise ValueError(f"no active user {uid!r} to mint a session for")
    sid = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with state.connect() as c:
        c.execute("INSERT INTO sessions (id, created_at, expires_at, user_id) VALUES (?,?,?,?)",
                  (sid, state._now(), exp, uid))
    return sid


def session_user(req) -> dict | None:
    """WHO this request is, or None if it is nobody. The question every gate should be asking.

    Returns a user dict — `role` and `active` are the two fields the gates read. A session whose
    user was REVOKED returns None, which is what makes `active = 0` take effect on the very next
    request rather than whenever a 30-day cookie happens to expire.

    FAILS CLOSED, always and in every direction: no cookie, no row, an expired row, a row whose
    `user_id` is NULL, one pointing at a user who does not exist, one pointing at a user who has
    been revoked, or a database too old to have the column — every one of them is nobody.

    There is deliberately NO owner-shaped fallback here. An earlier cut of this function read a
    NULL `user_id` as the owner so that sessions predating migration 47 kept working; that is a
    fail-OPEN rule living in the request path forever, and OSDev1 was right to refuse it. The
    same outcome is now reached once, in the migration, by giving those sessions a real owner to
    point at. A schema older than 47 cannot arise while serving: `core.state.init_db` runs at
    boot, executes SCHEMA and then every migration up to SCHEMA_VERSION, before a request is
    answered.
    """
    try:
        sid = req.cookies.get(COOKIE, "")
    except Exception:                            # noqa: BLE001 — no request, no cookies, nobody
        return None
    if not sid:
        return None
    now = datetime.now(timezone.utc).isoformat()
    try:
        with state.connect() as c:
            row = c.execute("SELECT expires_at, user_id FROM sessions WHERE id = ?",
                            (sid,)).fetchone()
            if not row or not (row["expires_at"] > now):
                return None
            uid = row["user_id"]
            if not uid:
                # A session with no person. Refused, never promoted to the owner.
                return None
            u = c.execute("SELECT id, email, name, role, active FROM users WHERE id = ?",
                          (uid,)).fetchone()
    except Exception:                            # noqa: BLE001 — no table, no column, no session
        return None
    if not u or not u["active"]:
        return None
    return {"id": u["id"], "email": u["email"], "name": u["name"],
            "role": u["role"], "active": 1}


def session_ok(req) -> bool:
    """Is SOMEBODY signed in here — the owner, or an employee who has not been revoked?

    Deliberately unchanged in meaning for every one of its callers except in one direction: a
    revoked person is no longer somebody. Narrowing it here rather than at each call site is what
    makes revocation take effect on `/dash/leads/*` and the reel dashboard too, neither of which
    is one of the four sites this change was written for and both of which repeat their own
    per-route guard (see docs/AIOS_GUI_WEB_APP_UPGRADES.md §5 debt 1).
    """
    return session_user(req) is not None


def require_session():
    """Bounce to the login, REMEMBERING where he was trying to go.

    Without the `next`, every bounce landed him on the operator dashboard after signing in — so
    the app sent him to a password box and the password box sent him somewhere else. The path is
    re-validated on the way out of the form by `safe_next`; carrying it here is only a
    convenience and grants nothing.
    """
    if not session_ok(request):
        here = safe_next(request.full_path.rstrip("?") if request.full_path else request.path)
        return redirect("/dash/login?next=" + quote(here, safe="/") if here
                        else "/dash/login")
    return None



# ── layout (Tailwind values from the saved pages, translated to plain CSS) ────

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;background:#15181C;color:#E8E9EA;display:flex;flex-direction:column;
font:14px/1.5 "Inter Tight","Inter",-apple-system,system-ui,sans-serif}
a{text-decoration:none;color:inherit}
header{border-bottom:1px solid rgba(255,255,255,.1);padding:16px 24px;display:flex;
align-items:center;gap:16px}
.crumb{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:500}
.crumb .b1{color:rgba(255,255,255,.4)} .crumb .b1:hover{opacity:.8}
.crumb .sep{color:rgba(255,255,255,.2)}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:12px}
.btn-new{padding:8px 16px;background:rgba(255,255,255,.1);color:#fff;font-size:14px;
font-weight:500;border-radius:8px;border:1px solid rgba(255,255,255,.1);cursor:pointer;
display:inline-block;font-family:inherit}
.btn-new:hover{background:rgba(255,255,255,.15)}
main{flex:1;width:100%;margin:0 auto;padding:40px 24px;display:flex;
flex-direction:column;gap:32px}
main.wide{max-width:896px} main.narrow{max-width:672px}
h1{font-size:30px;font-weight:700;color:#fff}
.projid{color:rgba(255,255,255,.4);font-size:14px;margin-top:4px;
font-family:ui-monospace,Menlo,monospace}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:24px}
.stat{display:flex;flex-direction:column;gap:4px}
.lbl{font-size:10px;font-family:ui-monospace,Menlo,monospace;
color:rgba(255,255,255,.4);text-transform:uppercase;letter-spacing:.1em}
.stat .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;color:#fff}
.stat .v.green{color:#4ade80}
hr{border:0;border-top:1px solid rgba(255,255,255,.1)}
.chips{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.chip{padding:6px 14px;border-radius:8px;font-size:14px;font-weight:500;
color:rgba(255,255,255,.5);display:inline-block}
.chip:hover{color:rgba(255,255,255,.7);background:rgba(255,255,255,.05)}
.chip.on{background:rgba(255,255,255,.15);color:#fff}
.chip .n{margin-left:6px;font-size:12px;font-family:ui-monospace,Menlo,monospace;
color:rgba(255,255,255,.3)}
.chip.on .n{color:rgba(255,255,255,.6)}
.rows{display:flex;flex-direction:column}
.rowa{display:flex;align-items:center;gap:16px;padding:16px;margin:0 -16px;
border-radius:8px;border-bottom:1px solid rgba(255,255,255,.05)}
.rowa:hover{background:rgba(255,255,255,.03)}
.icbox{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;
justify-content:center;font-size:14px;flex:none}
.icbox.green{background:rgba(20,83,45,.4);color:#4ade80}
.icbox.blue{background:rgba(30,58,138,.4);color:#60a5fa;font-weight:700}
.icbox.red{background:rgba(127,29,29,.4);color:#f87171}
.icbox.amber{background:rgba(120,53,15,.4);color:#fcd34d}
.icbox.gray{background:rgba(255,255,255,.06);color:rgba(255,255,255,.4)}
.rowa .mid{flex:1;min-width:0}
.rowa .t{color:#fff;font-size:14px;font-weight:500;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.rowa .m{color:rgba(255,255,255,.4);font-size:12px;margin-top:2px;
font-family:ui-monospace,Menlo,monospace}
.pill{font-size:11px;padding:4px 10px;border-radius:9999px;flex:none;
font-family:ui-monospace,Menlo,monospace}
.pill.green{background:rgba(20,83,45,.4);color:#86efac}
.pill.blue{background:rgba(30,58,138,.4);color:#93c5fd}
.pill.red{background:rgba(127,29,29,.4);color:#fca5a5}
.pill.amber{background:rgba(120,53,15,.4);color:#fcd34d}
.pill.gray{background:rgba(255,255,255,.06);color:rgba(255,255,255,.5)}
.dl{flex:none;color:rgba(255,255,255,.3);font-size:14px;padding:0 4px}
.dl:hover{color:#5865F2}
section .val{color:rgba(255,255,255,.8);font-size:14px}
section .val.strong{color:#fff;font-weight:500}
.lblrow{display:flex;align-items:center;justify-content:space-between;
margin-bottom:8px}
.lbl.mb{display:block;margin-bottom:8px}
.words{font-size:10px;font-family:ui-monospace,Menlo,monospace;
color:rgba(255,255,255,.3)}
.scriptbox{background:rgba(255,255,255,.05);border-radius:8px;padding:12px 16px;
color:rgba(255,255,255,.8);font-size:14px;line-height:1.625;white-space:pre-wrap}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:24px}
.vgroup{display:flex;flex-direction:column;gap:12px}
.vwrap{border-radius:8px;overflow:hidden;background:#000}
video{width:100%;max-height:480px;border-radius:8px;display:block;background:#000}
.dlink{display:inline-flex;align-items:center;gap:6px;color:#5865F2;font-size:14px;
font-weight:500}
.dlink:hover{opacity:.85}
.okhead{color:#86efac;font-weight:600;font-size:14px}
.failhead{color:#fca5a5;font-weight:600;font-size:14px}
.stack8{display:flex;flex-direction:column;gap:32px}
select,input[type=text],input[type=password],textarea{background:rgba(255,255,255,.05);
color:#E8E9EA;border:1px solid rgba(255,255,255,.1);border-radius:8px;
padding:10px 12px;font:inherit;width:100%}
textarea{min-height:160px;line-height:1.625}
.formrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.btn-primary{padding:8px 16px;background:#5865F2;color:#fff;font-size:14px;
font-weight:500;border-radius:8px;border:0;cursor:pointer;font-family:inherit}
.btn-primary:hover{opacity:.9}
footer{border-top:1px solid rgba(255,255,255,.05);padding:16px 24px;text-align:center}
footer p{font-size:12px;color:rgba(255,255,255,.2)}
select,input[type=text]{width:100%}
/* Phones (the review link opens from Slack on a phone by design): stack the
   two-column grids — side-by-side at 380px crushes selects to one letter. */
@media (max-width:720px){.grid2{grid-template-columns:1fr}}
"""


def brand() -> str:
    """The name on every screen, from ONE config value. These screens get filmed for the shop and
    for buyer handovers, so the mark has to change in one edit; it was hardcoded in ten places
    across two files.

    ON A SOLD BOX THE NAME IS THE BUYER'S. An explicit `dash.brand` still wins — an operator who set
    one meant it — but where config is silent a PROVISIONED box answers with the company it was built
    for, not with ours. Rendering an exported box showed the operator's name in the inbox header twice
    a page (OSDev5, 2026-09-16), on a machine whose whole pitch is that the customer owns it.

    THE OLD FALLBACK WAS A SECOND BUG WAITING. It printed "Mavrick", a mark the owner ruled off this
    app entirely — so a box with no config and no provision.json advertised the wrong company. Now the
    last resort is the product's own name, which is true on any box in any state.
    """
    try:
        configured = str((get_config().get("dash") or {}).get("brand") or "").strip()
    except Exception:                       # noqa: BLE001 — unreadable config is simply no brand
        configured = ""
    if configured:
        return configured
    try:
        bought_for = _claim.provisioned_buyer()
    except Exception:                       # noqa: BLE001 — a box with no provision.json has no buyer
        bought_for = ""
    return bought_for or "Ownbox"


def scope() -> list[str] | None:
    """Which recipes this request is looking at, read from the host — lane E of
    docs/MACHINE_AUTOPILOT_PLAN.md (owner, 2026-09-05: one demo box, many subdomains).

    `dentists.nlvl.co` → the packs whose manifest `industry` is "dentists" → their slugs, and every
    read on the page is filtered to rows those recipes found. A bare host, an IP, localhost, or a
    first label no pack claims is the WHOLE box, exactly as today — so a sold box at acme.nlvl.co
    (a client's name, never an industry) is unchanged, and nothing here can hide a row from the
    person who owns the box; it only chooses which rows a demo address shows. Read-only, no SQL:
    the stores take the result as `campaign=`.
    """
    label = host_label()
    if not label:
        return None
    try:
        from core import packs
        slugs = [m["slug"] for m in packs.discover() if str(m.get("industry", "")).lower() == label]
    except Exception:
        slugs = []
    # A machine that is CODE, not a pack, has no industry to match — the hiring-signal machine
    # (job: leads) and Practice Finder (npi: leads, and a page slug that differs from its manifest).
    # `dash.labels` names them: label → the source_ref prefixes its rows carry. The stores take a
    # prefix (an entry ending in ":") beside a campaign name in the same `campaign=` list.
    scoped = slugs + label_sources().get(label, [])
    return scoped or None


def label_sources() -> dict[str, list[str]]:
    """`{label: [source_ref prefix, ...]}` from config `dash.labels` — the subdomains a machine
    that is not a pack answers on (owner, 2026-09-05: "mini apps built for every machine"). A
    prefix is `<scheme>:` — `job:` for the hiring signal, `npi:` for Practice Finder — and every
    lead whose source_ref starts with it belongs to that app. Read from config, never from a
    machine (tests/test_dash_boundary.py); malformed entries are dropped, not raised, because a
    typo in an overlay must not take the box's dashboards down."""
    out: dict[str, list[str]] = {}
    try:
        from core.config import get_config
        raw = (get_config().get("dash") or {}).get("labels") or {}
        for label, prefixes in (raw.items() if isinstance(raw, dict) else []):
            label = str(label).strip().lower()
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", label):
                continue
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            keep = [str(p).strip().lower() for p in (prefixes or []) if isinstance(p, str)]
            keep = [p for p in keep if re.fullmatch(r"[a-z][a-z0-9_-]*:", p)]
            if keep:
                out[label] = keep
    except Exception:
        pass
    return out


def host_label() -> str | None:
    """The first label of the request host — `dentists` from `dentists.nlvl.co` — or None for a
    bare host, an IP, localhost, or a reserved first label. This is the one host read every scope
    shares: `scope()` maps it through the packs (lead rows), the reel board maps it through the
    Spaces (content rows). One reader, so the two scopes can never disagree about the host."""
    try:
        from flask import request as _req
        host = (_req.host or "").split(":")[0].strip().lower()
    except Exception:
        return None
    labels = host.split(".")
    if len(labels) < 3 or labels[0] in ("www", "dash", "app") or all(x.isdigit() for x in labels):
        return None
    return labels[0]


APP_TOKEN_COOKIE = "aios_app_k"          # the name machine_app already sets after a ?k= hit


def viewer_is_owner(req=None) -> bool:
    """Is this viewer the person who owns the box — a signed-in session, or the app token?

    THE SAME TWO CREDENTIALS `machine_app.unlocked()` accepts, and for the same reason: the
    session is the one the owner actually holds, and the token is for a link he chose to send.
    Lifted into core so a CORE page can ask the question without importing a machine.

    Order matters. The SESSION IS CHECKED FIRST so that a missing, rotated or mistyped app token
    can never shut out someone who is already signed in — the owner's standing rule is that he is
    never locked out of his own machines.

    FAILS CLOSED: no request context, no session table, a blank configured token, or a blank
    supplied one all return False, and the comparison is constant-time so the token cannot be
    guessed a character at a time.
    """
    try:
        from flask import request as _r
        req = req if req is not None else _r
    except Exception:                            # noqa: BLE001 — no context at all: not the owner
        return False
    try:
        # THE USER, NOT MERELY THE SESSION. Before per-person login this asked `session_ok`, which
        # answered "does this request carry any valid session" — so the day a receptionist could
        # sign in, she would have been the owner of the whole box here: the money numbers on core
        # pages, and through `machine_app.unlocked` every lead's real name, email and phone.
        # A SESSION WITH NO PERSON ON IT IS NOBODY, never the owner — `session_user` refuses a
        # NULL `user_id` outright. An earlier draft of this comment promised the opposite
        # ("still resolves to the owner, so nobody is logged out") and OSDev1 refused it: a
        # request-path default is fail-OPEN, and migration 47 backfills every real session to
        # the owner row anyway, so nothing was logged out by removing it.
        u = session_user(req)
        if u and u.get("role") == "owner":
            return True
    except Exception:                            # noqa: BLE001 — no session table, no session
        pass
    # READ THE CONFIG PER CALL, not the name this module bound at import. `machine_app.unlocked`
    # does exactly this, and test_the_two_credential_checks_can_never_disagree compares the two:
    # with the module-level name, a test (or a box) that swaps `core.config.get_config` moved one
    # of them and not the other, and the answer to "is this the owner" differed by which function
    # you asked. Caught by that test the moment the two were collapsed into one.
    from core.config import get_config as _cfg_now
    want = str((_cfg_now().get("dash") or {}).get("app_token") or "").strip()
    if not want:
        return False
    try:
        got = (req.args.get("k") or req.cookies.get(APP_TOKEN_COOKIE) or "").strip()
    except Exception:                            # noqa: BLE001
        return False
    return bool(got) and hmac.compare_digest(got, want)



def public_labels() -> set:
    """Labels whose app answers WITHOUT a login — config `dash.public_labels`, default EMPTY.

    Owner, 2026-09-06, asked whether these pages should be public and answered: "Yes, public."

    IT IS A LIST OF LABELS, NOT A SWITCH, and that is the whole design. One box answers for every
    machine's subdomain, so a boolean would have opened every machine on it at once — including
    ones nobody had looked at. Naming the label makes each one a deliberate act, and a sold box
    that names none stays shut, which is the only safe default for a file that ships to clones.

    THE BOX'S OWN NAME IS NEVER IN HERE. A bare host has no label, and "" is not a label, so
    /dash and a bare host keep their login no matter what this says.

    MOVED HERE from marketing/lead_machine/machine_app.py (2026-09-09) because the Morning Review
    is a core page that must answer under the same rule as the app it sits beside, and core
    imports no machine. machine_app re-exports it; its gate is unchanged.

    WHAT MAKES THIS SAFE IS NOT THIS FUNCTION. It is that no page can render a contact value:
    masking happens in the view model above every page, and the Morning Review carries no
    person at all (tests/test_morning_review.py). Without that this would be a leak with a
    config key.
    """
    try:
        raw = (get_config().get("dash") or {}).get("public_labels") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(x).strip().lower() for x in raw if str(x).strip()}
    except Exception:                            # noqa: BLE001 — unreadable config keeps the login
        return set()


def space_labels() -> dict[str, str]:
    """`{label: space name}` for every configured Space that carries `label:` — read straight
    from config, because core imports no machine (tests/test_dash_boundary.py). A Space is a
    brand on this box (owner 2026-09-05: one droplet, many subdomains), and its label is the
    subdomain it answers on; the reel package normalises the same key, and one test holds both
    readers to one answer. A block with no `airtable_base` is not a Space to the reel package
    either, so it is not an address here."""
    out: dict[str, str] = {}
    try:
        from core.config import get_config
        for s in (get_config().get("spaces") or []):
            label = str((s or {}).get("label") or "").strip().lower()
            if label and s.get("name") and s.get("airtable_base"):
                out[label] = str(s["name"])
    except Exception:
        pass
    return out


def scope_label(slugs: list[str] | None) -> str:
    """'dentists' → 'Dentists', from the first pack in scope; '' when unscoped."""
    if not slugs:
        return ""
    try:
        from core import packs
        for m in packs.discover():
            if m["slug"] == slugs[0]:
                return str(m.get("industry", "")).replace("-", " ").strip().capitalize()
    except Exception:
        pass
    label = host_label()                 # a dash.labels address: the label itself is the name
    if label and label in label_sources():
        return label.replace("-", " ").strip().capitalize()
    return ""


def _footer(product: str) -> str:
    """The one host-specific byte in the frame. Reel passes "Reel Machine" and gets
    back exactly the string it printed before this extraction; a host that passes
    nothing gets the plain mark rather than another machine's name."""
    return f"Powered by {brand()} · {product}" if product else f"Powered by {brand()}"



def _home_href() -> str:
    """Where the brand crumb goes. On a Lead-only buyer box nothing serves /dash — the reel board
    is a Content Machine page — so a crumb hardcoded to /dash was a link to a 404 on the client's
    first screen. Ask the app what it serves, the same way the login redirect does."""
    try:
        from flask import current_app
        rules = {str(r.rule) for r in current_app.url_map.iter_rules()}
        if "/dash/home" in rules:
            return "/dash/home"
        if "/dash" in rules:
            return "/dash"
    except Exception:
        pass
    return "/dash/home"


def privileged() -> bool:
    """Does this viewer hold a credential the box issued? ONE NAME FOR THE OTHER — this is
    `viewer_is_owner` and nothing else, kept as a name because callers read better with it.

    THE TWO WERE WRITTEN WITHIN AN HOUR OF EACH OTHER, in #1012 and #1013, by two people fixing
    the same leak from different sides — and they were the same logic twice: session, then app
    token, constant-time, failing closed. `machine_app.unlocked` is the third. Two copies of a
    credential check is how one of them quietly stops matching the others, which is precisely
    what test_the_two_credential_checks_can_never_disagree exists to catch; the cheaper answer
    is to have one implementation and let the names be names.

    WHAT THE NAMES MEAN, and it is worth keeping both. `viewer_is_owner` is asked by a GATE —
    may this page answer at all. `privileged` is asked by a RENDERER — may this page show the
    box's own numbers. Same credential, two questions, and a reader should be able to tell which
    one a call site is asking.
    """
    return viewer_is_owner()

def page(crumb_tail: str, main_cls: str, body: str,
         hdr_right: str = "", title: str | None = None,
         refresh: int | None = None, product: str = "") -> str:
    title = title if title is not None else brand()
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<header><div class="crumb"><a class="b1" href="{_home_href()}">{html.escape(brand())}</a>
<span class="sep">/</span><span>{crumb_tail}</span></div>
<div class="hdr-right">{hdr_right}</div></header>
<main class="{main_cls}">{body}</main>
<footer><p>{_footer(product)}</p></footer></body></html>"""



# ── masking — THE ONE FORM, for every machine's page ────────────────────────────────────
# Moved here from marketing/lead_machine/machine_app.py (2026-09-09, docs/PLAN_MORNING_REVIEW.md
# §1.10) because a CORE page — the Morning Review, and Customer Voice's review authors after it —
# renders named people and must not import a machine to obscure them. The family moves TOGETHER:
# `_obscure` is the primitive both maskers share, and splitting it across modules is exactly how
# #893 happened (mask_person "fixed" beside the primitive while the surname still leaked).
# machine_app re-exports all three so nothing that imported them there changes.

_DOTS = "•" * 4                                  # a FIXED width, never one dot per hidden letter


def _obscure(s: str) -> str:
    """THE ONE MASKING FORM, used by both maskers. Owner, 2026-09-07: "using some sort of formula
    where we show the first and the last letter with obscured middle letters".

    `Whitfield` → `W••••d`. Fixed-width middle, not one dot per character: proportional dots look
    more real and quietly publish the length, which is a free guess for anyone reading the
    screen over a shoulder.

    EVERY VALUE OBSCURES TO THE SAME WIDTH, short ones included. Returning a one- or two-character
    token whole publishes the entire value, and the differing width then announces "this local
    part is one or two characters" to anyone comparing rows. A single-character value simply has
    the same first and last letter; it obscures like everything else.
    """
    s = str(s or "").strip()
    return f"{s[0]}{_DOTS}{s[-1]}" if s else ""


def mask_email(value: str) -> str:
    """`dana@harborridge.com` → `d••••a@harborridge.com`. The DOMAIN stays, because the domain is
    the company and the company is the point; the person is not ours to show.

    Owner, 2026-09-06: "It would be nice to have a page of the leads and have the email addresses
    obscured for the demo. That's gonna be the wow moment if we can deliver a prospecting
    engine." An obscured address proves the machine HAS one. A blank cell proves nothing, and a
    real one is somebody's inbox on a shared screen.
    """
    v = str(value or "").strip()
    if "@" not in v:
        return ""
    local, _, domain = v.partition("@")
    if not local or not domain:
        return ""
    return f"{_obscure(local)}@{domain}"


def mask_person(name: str) -> str:
    """`Dana Whitfield` → `D••••a W••••d`. EVERY part is obscured, the surname included.

    THIS USED TO KEEP THE SURNAME — `D. Whitfield` — on the argument that a buyer should be able
    to verify the filing. That was wrong and it shipped. A surname next to the company and the
    city on the same row is not masked, it is a search query, and these are private individuals
    who signed a federal form, not customers who opted into anything.

    Owner, 2026-09-07, on the form: "we show the first and the last letter with obscured middle
    letters". The token is what reveals a real name (see machine_app.unlocked). Nothing else does.
    """
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    return " ".join(_obscure(p) for p in parts) if parts else ""


def age(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return "—"
    s = max(s, 0)
    for div, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= div:
            return f"{s / div:.0f}{unit}"
    return f"{s:.0f}s"


def fmt_dur(seconds: float | None) -> str | None:
    if not seconds:
        return None
    s = int(round(seconds))
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def options(items: list[dict], selected: str | None) -> str:
    out = []
    for it in items:
        sel = " selected" if it.get("id") == (selected or "") else ""
        out.append(f'<option value="{html.escape(str(it.get("id", "")))}"{sel}>'
                   f'{html.escape(str(it.get("name") or it.get("id", "")))}</option>')
    return "".join(out) or '<option value="">(configure reel.avatars/voices)</option>'



# ── the login does not answer unlimited guesses ──────────────────────────────────────
# MEASURED 2026-09-06 against the live box: twelve wrong passwords answered in 2.2 seconds, no
# backoff, no ceiling. compare_digest makes each guess constant-time, which defeats a timing
# oracle and nothing else. Ten boxes are about to sit on public subdomains.
#
# Shape, deliberately: PER-IP and SHORT. A global counter would let one stranger lock the owner
# out of his own box — a worse outcome than the guessing. NOTHING SLEEPS: a sleeping request
# thread is itself the denial of service on a two-worker box, so we refuse immediately with 429
# and Retry-After. Memory only — an attempt must never cost a disk write.
_FREE_ATTEMPTS = 5              # answered normally; a fat-fingered owner never meets the wall
_MAX_BLOCK_S = 300              # the ceiling on one address's wait
_MAX_TRACKED = 2048             # bounded, so rotating addresses cannot grow this
_fails: dict = {}


def _client_ip() -> str:
    """Caddy is the only ingress on this box (port 8000 refuses anything else), so its
    X-Forwarded-For is trustworthy here in a way it would not be on a directly exposed port."""
    fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return fwd or (request.remote_addr or "?")


def _block_seconds(ip: str) -> int:
    _, until = _fails.get(ip, (0, 0.0))
    left = until - time.time()
    return int(left) + 1 if left > 0 else 0


def _note_failure(ip: str) -> None:
    n = _fails.get(ip, (0, 0.0))[0] + 1
    wait = 0 if n <= _FREE_ATTEMPTS else min(2 ** (n - _FREE_ATTEMPTS), _MAX_BLOCK_S)
    if ip not in _fails and len(_fails) >= _MAX_TRACKED:
        for k in sorted(_fails, key=lambda k: _fails[k][1])[: _MAX_TRACKED // 4]:
            _fails.pop(k, None)
    _fails[ip] = (n, time.time() + wait)


def _clear_failures(ip: str) -> None:
    _fails.pop(ip, None)


# WHERE A PERSON LANDS WHEN THEY HAVE JUST SIGNED IN AND NAMED NO DESTINATION.
#
# MEASURED, NOT ASSUMED, AND THE MEASUREMENT IS THE WHOLE POINT. A customer_voice box ships
# NEITHER `/dash/home` (lead_machine.dash) NOR `/dash` (content_machine.reel.dashboard):
# `export_box.sh:53` sets MODULES to customer_voice alone and `:302` filters web_modules to it.
# Both login and claim fell back to `/dash`, so on the box we are actually selling, a $499 buyer
# set their password and landed on a 404. Found by OSDev4, confirmed against an exported tree —
# 29 routes, `/voice/` present, both dashboards absent.
#
# WHICH PAGES EXIST IS A PER-BOX FACT, NEVER A CONSTANT. That is why this reads the LIVE url_map
# instead of a list of paths we believe in: the same binary ships as four different products, and
# any hard-coded landing is right for one of them and a 404 for the rest.
#
# THE ORDER PUTS THE DASHBOARDS FIRST, and that is a correction to my own first attempt rather
# than the order OSDev1 sketched on the wall. He asked for `/voice/` first; I built that, and CI
# answered with `test_client_home::test_login_lands_on_the_home` — an existing suite that asserts
# a box WITH a client home lands a bare login on it. That is not a preference I get to overrule
# from inside a 404 fix: it is a product decision someone already made and wrote a guard for.
#
# Dashboards-first satisfies both and costs nothing:
#   inbox box   — ships neither dashboard, falls through to /voice/   (the 404 is fixed)
#   lead box    — has /dash/home, lands there                          (contract preserved)
#   everything  — has /dash/home, lands there                          (owner's box unchanged)
#
# THE INBOX OUTRANKS THE REEL DASHBOARD in the fall-through, which is OSDev1's order and not my
# first one. I had `/dash` second. On a box with the reel operator dashboard but no client home,
# that lands a BUYER on an operator screen; his order lands them on the inbox, which is the
# machine they bought. `/dash` stays last as the thing you reach only when nothing better exists.
#
# The defect was never "we land on the wrong page on a box that has one". It was "we land on a
# page this box does not serve". Only the fall-through had to change.
# THE ORDER IS THE RULING, SO THE REASONS LIVE HERE.
#
# `/voice/` IS NOT THE INBOX. The app's tabs are Today · Inbox · Settings, and `/voice/` is
# TODAY — the site uptime and pagespeed report. Until 2026-09-15 a SOLD inbox box therefore
# landed its buyer, thirty seconds after paying for a unified inbox, on a website-monitoring
# page reading "— good checks today / 0 checks on your site" plus a hint naming a YAML key.
# Measured by claiming an exported box and rendering what came back, not inferred from the
# route name — which is the mistake the previous order encoded (OSDev4's own #1172 ruling said
# "the buyer lands on /voice/", meaning the inbox, and /voice/ is not it).
#
# `/voice/inbox` goes FIRST, and only ahead of `/voice/`. The empty inbox already says the right
# thing — "No conversations yet. The first person who messages you appears here" — which tells a
# buyer nothing is wrong and what happens next, where dashes and zeroes tell him the box is
# broken in the minute he bought it.
#
# THE OWNER'S BOX IS DELIBERATELY UNTOUCHED. His ruling of 2026-09-09 about what he sees first is
# carried by `/dash/home`, which stays at the head of this tuple and which his everything-box
# serves; a box holding /dash/home never reaches the two entries below it. This changes exactly
# one shape: a box that has an inbox and no client home — which is the product we sell.
# /voice/* MOVED TO /inbox/* ON 2026-09-17. The owner: "No redirect, please. Full cut over" — /voice
# belongs to the AI receptionist machine, so no login may land there again.
#
# `/dashboard` SITS SECOND, AND THAT PLACE IS THE WHOLE DECISION (2026-09-17).
#
# Owner, 2026-09-17, quoted verbatim by OSDev1 from his own session: *"They should go to
# /dashboard and then they should see some helpful information and can click settings to set
# things up."* And to me, on the front door: */ must redirect to landing()… it follows /dashboard
# automatically the moment that lands.* That second sentence is only TRUE if `/dashboard` is in
# this tuple, which is why it is added here rather than left for later.
#
# SECOND AND NOT FIRST, because the two rulings are about two different people and both stand.
# `/dash/home` is served by the LEAD MACHINE (marketing/lead_machine/dash.py) — measured, not
# assumed — so it exists on his everything-box and on no inbox box we sell. Putting `/dashboard`
# behind it therefore changes nothing he sees and everything a buyer sees: his 2026-09-09 ruling
# keeps its screen, and a buyer stops landing on the INBOX — whatever that path is called this
# week — and lands on the dashboard the 2026-09-17 ruling names. Ahead of it would have overturned
# a ruling to satisfy a ruling.
#
# THE ENTRY BELOW IT IS NAMED BY ROLE, NOT BY PATH, because this comment was first written saying
# `/voice/inbox` and that path stopped existing the same morning — OSDev1's cut renamed it while
# this branch was open. A test in this stack made the identical mistake and would have raised a
# ValueError on his replay. Prose that names a neighbour ages exactly as badly as an assertion
# that does.
#
# AND IT IS ONE PLACE, NOT TWO. The front door (`/`) resolves through `landing()`, so this tuple
# is the only thing that decides where a box opens. A redirect in `deploy/Caddyfile` would be a
# second answer to the same question, living in a file no test reads.
_LANDINGS = ("/dash/home", "/dashboard", "/inbox/inbox", "/inbox/", "/dash")


@blueprint.get("/")
def bare_domain():
    """THE BOX'S OWN ADDRESS, typed bare — the first thing a buyer does after the claim email.

    It was a 404. OSDev4 walked the onboarding as customer #1 on a fresh box (2026-09-17): the claim
    and the login both redirect to `landing()`, which is fine, but nothing answered `/` and the
    Caddyfile is a plain reverse_proxy, so `acme.ownbox.app` — typed, bookmarked, or tapped from a
    note — showed a blank Not Found on the one site he now owns. Two lines: not signed in goes to the
    login, which lands him afterwards; signed in goes straight to where he would have landed.

    A REDIRECT TO `landing()`, NEVER A HARDCODED PAGE. The landing order is decided once, in
    `_LANDINGS`, and a second opinion here is how `/` would start disagreeing with the login.
    """
    if not session_ok(request):
        return redirect("/dash/login")
    return redirect(landing())


def landing() -> str:
    """The first of `_LANDINGS` this box actually serves; never a path that 404s."""
    from flask import current_app
    have = {str(r) for r in current_app.url_map.iter_rules()}
    for path in _LANDINGS:
        if path in have:
            return path
    # A box serving NONE of them has shipped no UI at all, which is a build fault rather than a
    # routing one. The login page is the only page guaranteed to exist here (this blueprint owns
    # it) and it renders rather than redirecting, so this terminates instead of looping — and the
    # error line is how anyone finds out, because a person would otherwise just see a login form
    # again and assume their password failed.
    log.error("dash.no_landing_route", candidates=list(_LANDINGS))
    return "/dash/login"


# ── the login, for every host on the box ─────────────────────────────────────

def safe_next(value: str) -> str:
    """A path on THIS box to return to after signing in, or "" for the default landing.

    AN UNVALIDATED RETURN PATH IS AN OPEN REDIRECT, and this form answers anybody — the same
    trap `machine_app._safe_back` exists for, on the login page this time, where a stranger
    handing someone a `/dash/login?next=…` link is the whole attack. So: one leading slash and
    nothing that can leave this site. `//evil.com` is a protocol-relative URL a browser treats as
    another host, and a backslash is read as a slash by some of them, so both are refused rather
    than escaped. Anything not plainly a local path is dropped, never repaired.
    """
    v = str(value or "").strip()
    # THE CLASS, NOT THREE MEMBERS OF IT. My first version excluded backslash, newline and
    # carriage return — and tab is the third character in that class, so `/<TAB>/evil.com` passed
    # every clause: it starts with "/", not "//", carries no backslash and no "://". `.strip()`
    # only takes whitespace off the ENDS, so an internal tab survives intact into a Location
    # header. Per the WHATWG URL spec a browser then removes tab, LF and CR from ANYWHERE in a
    # URL before parsing, so what the person's browser actually resolves is `//evil.com` —
    # protocol-relative, another origin, which is the exact attack this function exists to stop.
    # Found by OSDev1, 2026-09-12, by running this function rather than reading it.
    #
    # The eight bad vectors in my test were all sound and not one held a control character: a
    # list cannot refuse what nobody thought to list. So the rule is now the CLASS — every C0
    # control and DEL — and the next member nobody thinks of is covered without another round.
    # A path a person is being returned to needs none of them.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in v):
        return ""
    if (v.startswith("/") and not v.startswith("//")
            and "\\" not in v and "://" not in v):
        return v
    return ""


@blueprint.get("/dash/login")
def login_form():
    # WHERE HE WAS, CARRIED THROUGH THE LOGIN. Owner, 2026-09-11: "when I enter my password, I
    # can use the app completely." Before this, signing in always landed on the operator
    # dashboard — so from the app he typed his password, arrived somewhere else entirely, and
    # had to find his own way back, on a phone. A door that opens into a different room is the
    # same half-working control the app has spent days removing.
    nxt = safe_next(request.args.get("next", ""))
    hidden = (f'<input type="hidden" name="next" value="{html.escape(nxt)}">' if nxt else "")
    body = f"""
<section style="max-width:380px">
  <label class="lbl mb">Sign in</label>
  <form method="post" action="/dash/login">
    {hidden}<input type="email" name="email" placeholder="email" autocomplete="username" autofocus
           style="margin-bottom:10px">
    <input type="password" name="token" placeholder="password" autocomplete="current-password"
           style="margin-bottom:10px">
    <button class="btn-primary" type="submit">Sign in</button>
  </form>
</section>"""
    return page("Login", "narrow", body, title=f"{brand()} · Login")


# ── first login: the box hands itself over, once (core/claim.py) ─────────────────
#
# NO `require_session()` ON EITHER OF THESE, and that is the whole point rather than an
# oversight: nobody can sign in to a box that has never been claimed, so a claim page behind
# the session gate is a door locked from the inside. It is safe precisely because it can be
# used ONCE — `box_claim.id` is `CHECK (id = 1)`, so the second claim fails inside SQLite.

# THE FRONT DOOR WEARS THE PRODUCT, NOT THE OPERATOR DASHBOARD.
#
# WHAT WAS WRONG, seen only by rendering it: /claim came through `page()`, which is the operator
# shell — dark, a breadcrumb reading "<operator> / Set up your box", a purple button, and
# "Powered by <operator>" in the footer. That is the FIRST SCREEN A PAYING CUSTOMER EVER SEES on
# the box they just bought, and it looked like an internal admin tool with somebody else's name
# on it. The words were already right by then (§1) and the page still said the wrong thing.
#
# SELF-CONTAINED, AND THAT IS NOT LAZINESS. /claim is a CORE route: it ships on every box, and a
# Lead box has no `marketing/customer_voice` to borrow a shell from. Importing one machine's app
# into a core door would also invert the dependency — core would depend on a department. So the
# tokens are declared here.
#
# WHERE THE TOKENS COME FROM, because a copied hex with no lineage is how a design language
# drifts: they are the INBOX's, which are Kinso's, taken under the owner's ruling (2026-09-15)
# to start from the competitor's design exactly and put our own touches on it as a later pass.
# Kinso is the reference for tokens and design language — owner, 2026-09-15 — and OUR MARKETING
# SITE IS NOT: it is being re-skinned, so anything matched to it today is matched to something
# that is about to change. The buyer meets this screen and then the inbox; those two agreeing is
# the whole point, and the site is not in that path.
#
# LIGHT ONLY, DELIBERATELY. Every other screen in the product carries a dark toggle; this one is
# seen once, before any preference exists to read, and a front door that guesses wrong in a dark
# room is worse than one that simply looks like itself. It still paints its own background, so it
# never borrows a host ground.
_CLAIM_CSS = """
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:#eff2f4;color:#1a1d1f;min-height:100dvh;display:flex;align-items:center;
  justify-content:center;padding:24px 20px;
  font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{width:100%;max-width:420px}
.card{background:#fff;border-radius:18px;padding:26px 24px 24px;
  box-shadow:0 1px 2px rgba(16,24,32,.05),0 8px 24px -12px rgba(16,24,32,.18)}
h1{font-size:25px;line-height:1.2;font-weight:640;letter-spacing:-.015em;margin:0 0 8px;
  text-wrap:balance}
p{margin:0 0 16px;color:#6e7478}
p.tight{margin-bottom:18px}
label{display:block;font-size:13px;font-weight:560;color:#6e7478;margin:0 0 6px}
input{width:100%;font:inherit;font-size:16px;padding:12px 14px;border:1px solid #e5eaed;
  border-radius:12px;background:#fff;color:#1a1d1f;margin-bottom:14px}
input:focus{outline:2px solid rgba(224,93,56,.45);outline-offset:1px;border-color:#e05d38}
button{width:100%;font:inherit;font-size:16px;font-weight:600;padding:13px 16px;border:0;
  border-radius:999px;background:#e05d38;color:#fff;cursor:pointer;min-height:48px}
button:hover{background:#cf5330}
button:focus-visible{outline:2px solid #1a1d1f;outline-offset:2px}
.small{font-size:13.5px;color:#9ba1a6;margin:16px 0 0}
.bad{color:#c0392b;background:#fdecea;border-radius:10px;padding:10px 12px;margin:0 0 14px;
  font-size:14.5px}
a{color:#e05d38}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def _claim_page(body: str, code: int = 200):
    """The claim screens, in their own shell. `_CLAIM_CSS` above says why it is not `page()`."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,'
            f'viewport-fit=cover">'
            f'<meta name="theme-color" content="#eff2f4">'
            f'<meta name="robots" content="noindex,nofollow">'
            f'<title>Set up your box</title><style>{_CLAIM_CSS}</style></head>'
            f'<body><div class="wrap"><div class="card">{body}</div></div></body></html>'), code


def _claim_states():
    """(already claimed?, is this box even sellable?) — read once per render."""
    return _claim.claimed() is not None, _claim.provisioned_order() is not None


# EVERY WORD ON THESE SCREENS COMES FROM docs/COPY_INBOX_FIRST_RUN.md §1, and that is a rule
# rather than a courtesy. This is the first screen a paying customer ever sees on their own box,
# the copy was written and approved for it, and the version that shipped first was written before
# that doc existed — so it cut the ownership line §1.1 marks do-not-cut and opened with exactly
# the "invalid code" accusation §1.2 forbids by name. Changing these strings means changing the
# doc first. `tests/test_claim_copy.py` holds them to it.
_CLAIM_REFUSALS = {
    # §1.2 — never "invalid code": it tells the buyer nothing they can act on and reads as an
    # accusation at the moment they are least sure of themselves.
    "bad_code": ("That link did not work.",
                 "Check the link in your welcome email — it needs to be the whole thing, including "
                 "the part after the question mark. If you have lost the email, reply to it and "
                 "we will send the link again."),
    # Not in §1: the buyer of a box that was never sold. Kept separate because the answer ("sign
    # in with your dashboard password") is useless to a buyer and exactly right for the owner.
    "not_sellable": ("There is nothing to claim here.",
                     "This box was not set up by a purchase. Sign in with your dashboard "
                     "password."),
}

# THE FALLBACK EXISTS SO THAT A KIND WITH NO COPY BEHIND IT CANNOT REACH A SCREEN. A missing entry
# used to be a KeyError — a 500 on the first page a buyer opens — and the near-miss beside it was
# worse: the recoverable path rendered `str(exception)`, whose fallback IS the kind, so the literal
# string `bad_email` was printed in red to a paying customer (§2.8, measured 2026-09-15 by posting
# a malformed address to the built screens, not by reading them). Words for an unknown kind are
# therefore generic but TRUE: `claim_box` raises having changed nothing, so the link really does
# still work. The event is logged at error level, because loud in the logs and calm on the screen
# is the right pair — the opposite pair is what shipped.
_UNKNOWN_REFUSAL = ("We could not set this box up.",
                    "Nothing has changed and your link still works. Try it again, and if it "
                    "happens twice reply to your welcome email — we will sort it out.")

# §2.7 AND §2.8 — THE KINDS A BUYER RECOVERS FROM WITHOUT LEAVING THE FORM. These are one sentence
# rendered under the field, not an (h1, body) page: the buyer keeps his code, his address and his
# place. The two tables are separate on purpose, because a kind belongs to exactly one of them and
# a single table would have to be read to know which.
_CLAIM_PROBLEMS = {
    # §2.8. The second sentence is the only place a buyer is ever told what the address is FOR;
    # without it a person who reads the field as a mailing-list signup types a throwaway and
    # locks himself out of the box he just bought.
    "bad_email": "That does not look like an email address. This is the address you will sign "
                 "in with.",
    # §2.7's words come from `password_problem()`, which builds them from MIN_PASSWORD so the
    # screen cannot disagree with the box about the number. This entry is what renders if that
    # rule ever returns nothing — it is a backstop, not the copy.
    "bad_password": "That password will not work. Choose a longer one.",
}


# AND THE ONE KIND THAT CARRIES NO WORDS OF ITS OWN, declared rather than hardcoded in the view.
# `already_claimed` does not render a message: it REDIRECTS to `GET /claim`, where §1.4's whole
# screen — both paths and a button — is already built. An (h1, body) pair cannot hold that screen,
# so forcing it into the table above would flatten the copy to fit the table. Written here as data
# because the suite reads all three of these and requires every kind `core/claim.py` raises to
# appear in exactly one; a kind that is in none of them has nothing to say to a buyer.
_CLAIM_REDIRECTS = {"already_claimed": "/claim"}


def _claim_problem(e: "_claim.ClaimRefused") -> str:
    """The sentence shown under the field — from the copy table, never from the exception.

    `bad_password` is the SINGLE kind whose words come from the raised detail, and only because
    `password_problem()` is written to return customer-ready sentences. Even there the detail is
    used only when it is genuinely a detail: `ClaimRefused.__init__` falls back to the kind when
    no detail is given, and printing that fallback is precisely the §2.8 defect.
    """
    detail = str(e)
    if e.kind == "bad_password" and detail and detail != e.kind:
        return detail
    return _CLAIM_PROBLEMS[e.kind]


@blueprint.get("/claim")
def claim_form():
    done, sellable = _claim_states()
    if done:
        # §1.4, AND BOTH PATHS ALWAYS — a dead end here is a support ticket at best. It also says
        # NOTHING about the code: a claimed box answers a stranger with the right code exactly as
        # it answers one with the wrong code, because guessing is the only thing left to do here.
        return _claim_page(
            '<h1>This box has already been claimed.</h1>'
            '<p>Someone created the login for this box. If that was you, sign in below. If it '
            'was not, reply to your welcome email now and we will help.</p>'
            '<a href="/dash/login"><button type="button">Sign in</button></a>')
    if not sellable:
        h1, body = _CLAIM_REFUSALS["not_sellable"]
        return _claim_page(f'<h1>{html.escape(h1)}</h1><p>{html.escape(body)}</p>')
    # THE CODE IS CARRIED IN A HIDDEN FIELD, NOT RE-READ FROM THE QUERY ON SUBMIT, so the
    # address bar is the only place it ever appears and a mistyped POST cannot half-work.
    code = html.escape(str(request.args.get("c", ""))[:220])
    return _claim_page(_claim_form_body(code))


def _claim_form_body(code: str, *, email: str = "", problem: str = "") -> str:
    """§1.1's screen, optionally carrying a recoverable refusal (§2.7).

    ONE FUNCTION FOR BOTH, because §2.7 requires the buyer to STAY ON THE PAGE: "a short password
    is a typo, not a failed claim; a box that spends its single-use code on one is a support
    ticket on the day of the sale." Sending them to a message and a Try-again link means retyping
    everything, and the earlier version of this screen did exactly that.

    THE PASSWORD IS NEVER CARRIED BACK — "not in the field, not in the message, not in a log."
    The address is, because it is theirs and retyping it is friction with nothing bought for it.
    """
    # §1.1 NAMES THE BOX BACK TO THEM. "This one is yours — <hostname>" is the cheapest proof
    # available that they are on their own machine and not on a page that could belong to anyone;
    # a box with no hostname on disk simply drops the clause rather than printing an empty dash.
    host = _claim.provisioned_host()
    whose = (f"This one is yours — {html.escape(host)}. Create the first login and it is "
             "locked to you." if host else
             "Create the first login and it is locked to you.")
    # §2.7: RENDER THE SENTENCE THE RULE RETURNS, do not write one. `password_problem()` builds it
    # from MIN_PASSWORD, so the screen cannot disagree with the box about the number — which is
    # the failure §2.7 says it nearly shipped, specifying 10 in prose against a rule of 12. No h1,
    # no strength meter, no "for your security": §2.7 asks for the string and nothing around it.
    note = f'<p class="bad">{html.escape(problem)}</p>' if problem else ""
    body = f"""
  <h1>Your box is ready.</h1>
  <p class="tight">{whose}</p>
  {note}
  <form method="post" action="/claim">
    <input type="hidden" name="c" value="{code}">
    <label for="claim-email">Your email address</label>
    <input id="claim-email" type="email" name="email" autocomplete="username"
           value="{html.escape(email)}" required>
    <label for="claim-pw">Choose a password</label>
    <input id="claim-pw" type="password" name="password"
           autocomplete="new-password" required>
    <button type="submit">Create my login</button>
  </form>
  <p class="small">This link works once. After you use it nobody else can claim this box
     — including us.</p>"""
    return body


@blueprint.post("/claim")
def claim_submit():
    # THE SAME PER-IP THROTTLE THE LOGIN USES, on purpose and shared with it. Both answer the
    # question "is this stranger guessing a credential for this box", and an attacker who
    # exhausts one would otherwise simply move to the other. It is per-IP and short for the
    # reason written at `_note_failure`: a global counter would let one stranger lock the buyer
    # out of the box he just paid for, which is worse than the guessing.
    ip = _client_ip()
    wait = _block_seconds(ip)
    if wait:
        # §1.3 — THE THROTTLE IS A FEATURE, SO SAY SO. Named as protection rather than reported as
        # a fault, it stops reading like the box is broken at the moment the buyer is most anxious.
        log.warning("claim.throttled", ip=ip, wait_s=wait)
        resp = make_response(_claim_page(
            '<h1>Too many tries.</h1>'
            '<p>Wait a minute, then try again. This is here so nobody can guess '
            'their way into your box.</p>', 429))
        resp.headers["Retry-After"] = str(wait)
        return resp
    try:
        owner = _claim.claim_box(code=request.form.get("c", ""),
                                 email=request.form.get("email", ""),
                                 password=request.form.get("password", ""),
                                 ip=ip, user_agent=request.headers.get("User-Agent", ""))
    except _claim.ClaimRefused as e:
        # EVERY refusal counts against the throttle, including a too-short password — an attempt
        # is an attempt, and a state that were free would be the cheapest way to probe this box.
        _note_failure(ip)
        log.warning("claim.refused", ip=ip, kind=e.kind)
        if e.kind in _CLAIM_REDIRECTS:
            return redirect(_CLAIM_REDIRECTS[e.kind])   # §1.4 renders it: both paths and a button
        if e.kind in _CLAIM_PROBLEMS:
            # §2.7 AND §2.8 — A TYPO, NOT A FAILED CLAIM. They hold the code, the code is still
            # valid (`claim_box` raised having changed nothing), so the form comes back with
            # their address and a sentence from the copy table. Never the password, and never
            # the exception's own text: this line used to read `problem=str(e)`, which for
            # `bad_email` printed the kind itself.
            return _claim_page(_claim_form_body(
                request.form.get("c", ""), email=request.form.get("email", ""),
                problem=_claim_problem(e)), 400)
        if e.kind not in _CLAIM_REFUSALS:
            log.error("claim.refusal_has_no_copy", kind=e.kind)
        h1, body = _CLAIM_REFUSALS.get(e.kind, _UNKNOWN_REFUSAL)
        return _claim_page(
            f'<h1>{html.escape(h1)}</h1><p>{html.escape(body)}</p>'
            '<p><a href="/claim">Try again</a></p>', 400)
    _clear_failures(ip)
    # STRAIGHT IN. He has just proved he holds the order and chosen his password; sending him to
    # a login form to type it again is a door that opens onto another door.
    resp = make_response(redirect(landing()))
    resp.set_cookie(COOKIE, new_session(owner["id"]), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


@blueprint.post("/dash/login")
def login():
    # THE DASH PASSWORD, WHICH IS NOT THE COMMAND CREDENTIAL (#748). settings.dash_token
    # falls back to the dispatch bearer, so a box that has never heard of DASH_TOKEN behaves
    # exactly as before. Set DASH_TOKEN and the bearer STOPS working here — that is the
    # point, not a side effect: a dash password that also drives /dispatch cannot be shown
    # to a VA, a partner or a client.
    ip = _client_ip()
    wait = _block_seconds(ip)
    if wait:
        # Refused BEFORE the comparison — a throttle that still checks the guess is a counter,
        # not a throttle.
        log.warning("dash.login_throttled", ip=ip, wait_s=wait)
        resp = make_response(page("Login", "narrow",
                                  f'<p class="val">Too many attempts. Try again in {wait}s.</p>',
                                  title=f"{brand()} · Login"), 429)
        resp.headers["Retry-After"] = str(wait)
        return resp
    token = settings.dash_token
    provided = request.form.get("token", "")
    # TWO CREDENTIALS, AND THE SECOND ONLY EVER ADDS A WAY IN. A box the buyer has claimed
    # (`/claim`, core/claim.py) carries a password HE chose, hashed; DASH_TOKEN keeps working
    # exactly as it always has, because the owner's standing rule (2026-09-07) is "I don't ever
    # wanna be locked out of these machines" and a delivery flow is the worst possible place to
    # start taking doors away. On an unclaimed box `password_ok` is False before it looks at
    # anything, so every existing box behaves identically.
    #
    # DASH_TOKEN IS TRIED FIRST because it is a compare_digest against a string already in
    # memory, while the claimed password is a deliberately expensive scrypt. A wrong guess
    # therefore costs one hash, not zero — which is the cost that makes guessing unattractive —
    # but the owner's own correct token costs none.
    who = state.owner_user() if bool(token) and hmac.compare_digest(provided, token) else None
    # A PERSON WITH THEIR OWN PASSWORD (docs/DESIGN_PER_PERSON_LOGIN.md). Email and password, checked
    # against that person's row. AN UNKNOWN ADDRESS COSTS THE SAME HASH AND GETS THE SAME ANSWER as a
    # wrong password: a form that answers faster, or differently, for addresses nobody holds is a
    # list of who works at the business.
    email = str(request.form.get("email", "") or "").strip().lower()
    if who is None and email:
        found = state.password_hash_for(email)
        if found and _claim.verify_password(provided, found[1]):
            who = found[0]
        elif not found:
            _claim.verify_password(provided, _dummy_hash())
    if who is None and not email and _claim.password_ok(provided):
        who = state.owner_user()          # a claimed box's owner typing only his own password
    if who is None:
        _note_failure(ip)
        log.warning("dash.login_failed", ip=ip)
        return page("Login", "narrow",
                     '<p class="val">That email and password do not match. <a class="dlink" '
                     'href="/dash/login">Try again</a></p>',
                     title=f"{brand()} · Login"), 401
    _clear_failures(ip)
    # Land on the client home when this box has one, else the operator page. Until the
    # home existed, a LEAD-ONLY box logged in straight into a 404: /dash belongs to the
    # reel dashboard, which that box does not ship. Checked against the live url_map
    # rather than assumed, because which pages exist is a per-box fact, not a constant.
    # BACK WHERE HE CAME FROM when the form carried a place, validated by `safe_next` — an
    # unvalidated one here would be an open redirect on the one page that answers everybody.
    # Falls through to the normal landing when there is none, so every existing caller is
    # unchanged.
    where = safe_next(request.form.get("next", "")) or landing()
    resp = make_response(redirect(where))
    # THE TOKEN LOGIN IS THE OWNER'S, and it mints HIS session — the same row migration 47
    # created and backfilled every older session to. Owner, 2026-09-07: "I don't ever wanna be
    # locked out of these machines"; §4.4: DASH_TOKEN keeps working, unchanged, forever.
    resp.set_cookie(COOKIE, new_session(who["id"]), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    log.info("auth.signed_in", user=who["id"], role=who.get("role"), ip=ip)
    return resp


_DUMMY = {}


def _dummy_hash() -> str:
    """A real scrypt hash of nothing anyone holds, so an unknown address costs what a known one does."""
    if "h" not in _DUMMY:
        _DUMMY["h"] = _claim.hash_password(secrets.token_urlsafe(24))
    return _DUMMY["h"]


# ── the people who may sign in: the owner invites, revokes and restores (DESIGN_PER_PERSON_LOGIN) ──
#
# OWNER ONLY, decided by the session's own row and nothing else. A member who could open this page
# could invite a friend; the app token (`viewer_is_owner`) does not count here either, because a
# link shared to read a report must never be a way to add a person to the box.
def _owner_session():
    who = session_user(request)
    return who if who and who.get("role") == "owner" else None


def _people_page(body: str, code: int = 200):
    return page("People", "narrow", body, title=f"{brand()} · People"), code


def _people_list(notice: str = "") -> str:
    rows = []
    for u in state.list_users():
        status = ("owner" if u.get("role") == "owner" else
                  "removed" if not u.get("active") else
                  "signed up" if u.get("has_password") else "invited, not joined yet")
        uid = html.escape(str(u["id"]))
        actions = ""
        if u.get("role") != "owner":
            if u.get("active"):
                actions = (f'<form method="post" action="/dash/people/{uid}/invite" style="display:inline">'
                           f'<button class="btn" type="submit">New link</button></form> '
                           f'<form method="post" action="/dash/people/{uid}/remove" style="display:inline">'
                           f'<button class="btn" type="submit">Remove</button></form>')
            else:
                actions = (f'<form method="post" action="/dash/people/{uid}/restore" style="display:inline">'
                           f'<button class="btn" type="submit">Restore</button></form>')
        rows.append(f'<tr><td>{html.escape(str(u.get("email") or ""))}</td>'
                    f'<td>{html.escape(status)}</td><td>{actions}</td></tr>')
    cap = state.max_users()
    seats = f"{state.count_active_users()} of {cap} people" if cap else f"{state.count_active_users()} people"
    return f"""{notice}
<section style="max-width:560px">
  <label class="lbl mb">People who can sign in · {html.escape(seats)}</label>
  <table>{''.join(rows)}</table>
  <label class="lbl mb" style="margin-top:18px">Invite someone</label>
  <form method="post" action="/dash/people/invite">
    <input type="email" name="email" placeholder="their email" required style="margin-bottom:10px">
    <button class="btn-primary" type="submit">Create invite link</button>
  </form>
  <p class="val">You send the link yourself. It works once, for {state.INVITE_DAYS} days, and they choose their own password.</p>
</section>"""


# ── Managed: what the buyer's card is about to do, and how to stop it ────────────────────────────
#
# Managed is a separate product bought in the same cart as the box (owner, 2026-09-15): a free three
# months that then RENEWS AUTOMATICALLY. A subscription that renews itself owes its buyer two things —
# to be told before the charge, and to be cancellable without asking us — and the second is the law
# (FTC click-to-cancel), not a nicety.
#
# THE CANCEL LINK IS STRIPE'S OWN PORTAL, on purpose. Minting a portal session would put a Stripe
# credential in the path, and this page runs on a droplet the CUSTOMER has root on — so the box holds
# no key, talks to no billing service of ours, and simply points at the page Stripe already runs. It
# costs the buyer one email round-trip and costs us no secret on a machine we do not control.
#
# WITH NOTHING CONFIGURED IT SAYS SO. An unset portal URL prints the fallback instead of a dead link:
# a cancel button that does nothing is worse than a sentence telling you who to write to.


def _managed_page(body: str, code: int = 200):
    return page("Managed", "narrow", body, title=f"{brand()} · Managed"), code


def _managed_body() -> str:
    until = _claim.provisioned_managed_until()
    if not until:
        # Every box that nobody bought Managed for, which is most of them. Say it plainly and stop.
        return ('<label class="lbl mb">This box does not have the Managed service.</label>'
                '<p class="val">Nothing here renews and nothing is charged. The box is yours either way.</p>')
    try:
        day = datetime.fromisoformat(until).strftime("%-d %B %Y")
    except ValueError:
        day = until                          # an unparseable date is still better shown than swallowed
    portal = (settings.managed_portal_url or "").strip()
    # STRIPE IS THE SOURCE OF TRUTH, AND THIS PAGE SAYS SO. The date came from the cart at build time and
    # never changes — so on a box whose owner has already cancelled, an unqualified "it renews" would be a
    # lie told by us to the person who cancelled. Until the box can read subscription state (it cannot: that
    # needs a credential this machine must never hold), the honest move is to name what was BOUGHT and point
    # at who knows what is true now.
    how = (f'<p><a class="btn-primary" href="{html.escape(portal)}" rel="noopener">Manage or cancel Managed</a></p>'
           '<p class="val">That is Stripe, where the subscription lives and where its current state is. '
           'They will email you a link to sign in. If you have already cancelled, Stripe will say so.</p>'
           if portal.startswith("https://") else
           '<p class="val">To cancel, reply to your welcome email and we will stop it the same day.</p>')
    return ('<label class="lbl mb">Managed is on.</label>'
            f'<p class="val">You bought Managed with this box, with three months free to '
            f'<strong>{html.escape(day)}</strong>. Unless you cancel, it renews after that.</p>'
            f'{how}'
            '<p class="val">Cancelling Managed does not touch the box. It keeps running, it keeps your data, '
            'and it stays yours — you would simply be running it yourself.</p>')


@blueprint.get("/dash/managed")
def managed():
    if not session_ok(request):
        return redirect("/dash/login?next=/dash/managed")
    if not _owner_session():
        # Billing is the owner's, but a colleague asking must not meet a blank 403 with no explanation.
        return _managed_page('<p class="val">Only the box owner can see or change the Managed service.</p>', 403)
    return _managed_page(_managed_body())


def _invite_notice(email: str, token: str) -> str:
    link = f"{request.host_url.rstrip('/')}/join?t={quote(token, safe='')}"
    return (f'<p class="val">Invite link for {html.escape(email)} — shown once, copy it now:<br>'
            f'<code style="word-break:break-all">{html.escape(link)}</code></p>')


@blueprint.get("/dash/people")
def people():
    if not session_ok(request):
        return redirect("/dash/login?next=/dash/people")
    if not _owner_session():
        return _people_page('<p class="val">Only the box owner manages who can sign in.</p>', 403)
    return _people_page(_people_list())


@blueprint.post("/dash/people/invite")
def people_invite():
    owner = _owner_session()
    if not owner:
        return _people_page('<p class="val">Only the box owner manages who can sign in.</p>', 403)
    email = str(request.form.get("email", "") or "").strip().lower()
    try:
        person = state.add_user(email, role="member")
    except state.SeatsFull:
        return _people_page(_people_list(f'<p class="val">This box is set up for {state.max_users()} people. '
                                         'Remove someone first.</p>'), 409)
    except state.SharedPasswordRefused as e:
        return _people_page(_people_list(f'<p class="val">{html.escape(str(e))}</p>'), 409)
    except ValueError:
        return _people_page(_people_list('<p class="val">Enter the email address of the person to invite.</p>'), 400)
    if person.get("role") == "owner":
        return _people_page(_people_list('<p class="val">That is the owner\'s own address.</p>'), 400)
    token = state.create_invite(person["id"], created_by=owner["id"])
    log.info("auth.invite_created", user=person["id"], by=owner["id"])
    return _people_page(_people_list(_invite_notice(person["email"], token)))


@blueprint.post("/dash/people/<uid>/<action>")
def people_action(uid: str, action: str):
    owner = _owner_session()
    if not owner:
        return _people_page('<p class="val">Only the box owner manages who can sign in.</p>', 403)
    person = state.get_user(uid)
    if not person or person.get("role") == "owner" or action not in ("invite", "remove", "restore"):
        return _people_page(_people_list('<p class="val">That person is not on this box.</p>'), 404)
    if action == "remove":
        state.set_user_active(uid, False)        # every session they hold stops at its next request
        log.info("auth.revoked", user=uid, by=owner["id"])
        return _people_page(_people_list(f'<p class="val">{html.escape(person["email"])} can no longer sign in.</p>'))
    if action == "restore":
        try:
            state.add_user(person["email"], role="member")   # reactivation goes through the seat check
        except state.SeatsFull:
            return _people_page(_people_list(f'<p class="val">This box is set up for {state.max_users()} people. '
                                             'Remove someone first.</p>'), 409)
        log.info("auth.restored", user=uid, by=owner["id"])
        return _people_page(_people_list(f'<p class="val">{html.escape(person["email"])} can sign in again.</p>'))
    if not person.get("active"):
        return _people_page(_people_list('<p class="val">Restore that person before sending a new link.</p>'), 409)
    token = state.create_invite(uid, created_by=owner["id"])
    log.info("auth.invite_created", user=uid, by=owner["id"], reissued=True)
    return _people_page(_people_list(_invite_notice(person["email"], token)))


# ── joining from an invite link: the invited person chooses their own password ────────────────
_JOIN_REFUSED = ('<p class="val">This invite link can\'t be used. It may have been used already, or it '
                 'expired. Ask the person who invited you for a new one.</p>')


def _join_page(body: str, code: int = 200):
    return page("Join", "narrow", body, title=f"{brand()} · Join"), code


def _join_form(token: str, email: str, problem: str = "") -> str:
    note = f'<p class="val">{html.escape(problem)}</p>' if problem else ""
    return f"""{note}
<section style="max-width:380px">
  <label class="lbl mb">Choose your password</label>
  <p class="val">You'll sign in as {html.escape(email)}.</p>
  <form method="post" action="/join">
    <input type="hidden" name="t" value="{html.escape(token)}">
    <input type="password" name="password" placeholder="a password ({_claim.MIN_PASSWORD}+ characters)"
           autocomplete="new-password" required style="margin-bottom:10px">
    <button class="btn-primary" type="submit">Join</button>
  </form>
</section>"""


@blueprint.get("/join")
def join_form():
    token = str(request.args.get("t", "") or "")[:200]
    person = state.invite_user(token)
    if not person:
        return _join_page(_JOIN_REFUSED, 404)
    return _join_page(_join_form(token, person["email"]))


@blueprint.post("/join")
def join_submit():
    ip = _client_ip()
    wait = _block_seconds(ip)
    if wait:
        resp = make_response(_join_page(f'<p class="val">Too many attempts. Try again in {wait}s.</p>', 429))
        resp.headers["Retry-After"] = str(wait)
        return resp
    token = str(request.form.get("t", "") or "")[:200]
    person = state.invite_user(token)
    if not person:
        _note_failure(ip)
        log.warning("auth.invite_refused", ip=ip)
        return _join_page(_JOIN_REFUSED, 404)
    password = request.form.get("password", "")
    problem = _claim.password_problem(password, email=person["email"])
    if problem:
        # A TYPO, NOT A GUESS: the link is valid, so a short password is not counted against the throttle.
        return _join_page(_join_form(token, person["email"], problem), 400)
    joined = state.redeem_invite(token, _claim.hash_password(password))
    if not joined:
        _note_failure(ip)
        return _join_page(_JOIN_REFUSED, 404)
    _clear_failures(ip)
    log.info("auth.invite_used", user=joined["id"], ip=ip)
    resp = make_response(redirect(landing()))
    resp.set_cookie(COOKIE, new_session(joined["id"]), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


# ── the demo zone: Caddy asks before it issues a certificate ─────────────────────────────────
# One box answers every <industry>.<zone> (lane E). A wildcard certificate would need a DNS token
# on the box; on-demand TLS needs none — Caddy asks THIS endpoint per hostname and issues only on
# a 200. So the ask is the gate: the box's own address, a label some pack claims as its
# industry, or a label a Space carries (a content brand — spaces: … label:), under the
# configured zone. Everything else is a 403 and never gets a certificate
# (Let's Encrypt rate limits are finite; a scanner probing random labels must not spend them).
def demo_zone() -> str:
    try:
        from core.config import get_config
        return str((get_config().get("dash") or {}).get("demo_zone") or "").strip().lower().strip(".")
    except Exception:
        return ""


def _own_host() -> str:
    base = os.environ.get("DASHBOARD_BASE_URL", "")
    return base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].strip().lower()


def ask_tls(domain: str) -> bool:
    """True only for a hostname this box should hold a certificate for."""
    host = (domain or "").strip().lower().split(":", 1)[0].rstrip(".")
    if not host or "/" in host or any(c.isspace() for c in host):
        return False
    if host == _own_host() and host:
        return True
    zone = demo_zone()
    if not zone or not host.endswith("." + zone):
        return False
    label = host[: -len(zone) - 1]
    if not label or "." in label or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", label):
        return False
    if label in space_labels():          # a Space's public label — a content brand on this box
        return True
    if label in label_sources():         # a machine that is code, not a pack — dash.labels names it
        return True
    try:
        from core import packs
        return any(str(m.get("industry", "")).lower() == label for m in packs.discover())
    except Exception:
        return False


@blueprint.get("/tls/ask")
def tls_ask():
    """Caddy's on_demand_tls `ask` — 200 issues, 403 does not. No body a scanner can learn from."""
    from flask import request as _req
    # ONLY CADDY ASKS. Caddy calls this on the loopback with no proxy headers; anything that came
    # through Caddy from the public carries X-Forwarded-For and is refused before the domain is read.
    if _req.headers.get("X-Forwarded-For") or _req.headers.get("X-Forwarded-Host"):
        return ("no", 403)
    return ("ok", 200) if ask_tls(_req.args.get("domain", "")) else ("no", 403)


# THE MORNING REVIEW PAGE mounts on this blueprint (core/dash/review.py) so it ships on every box
# through the kernel's one login-free mount, not through `web_modules:`. Imported last: it needs
# `blueprint` and `page` above. It imports no department — test_dash_boundary checks.
from core.dash import review as _review  # noqa: E402,F401

# THE BUYER'S HOME mounts the same way and for the same reason — `/dashboard` ships on every box
# through the kernel's one mount, not through `web_modules:`. It must be imported HERE rather than
# by whoever happens to need it: a blueprint stops accepting routes the moment it is registered on
# an app, so a page imported later is a silent 404 in production and an AssertionError in a test.
from core.dash import home as _home  # noqa: E402,F401
