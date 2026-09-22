"""The HTTP transport. One blueprint under /api/, which core/dispatch.py's gate already shuts.

MOUNTED AS CORE, directly, the way the dash shell is — not through `web_modules:`. So no shipped
box needs a config edit for the door to exist, and there is no box where somebody has to remember
to turn the connector on before it can refuse people properly. It is shut by the seat gate until
somebody deliberately mints a seat.

THIS FILE HOLDS NO POLICY. Authentication happened in the gate; authorisation, validation, the
audit row and the not_configured state all live in tools.call(), because the MCP adapter is a
second transport over the same functions and anything decided here would have to be decided twice.
If the MCP adapter ever needs a function to change shape, the shape was wrong — that is the design
check, and it is why both adapters land against one definition.
"""
from flask import Blueprint, abort, g, jsonify, redirect, request
from html import escape
from urllib.parse import quote

from core.connector import manifest, tools
from core.logging import get_logger

log = get_logger(__name__)

blueprint = Blueprint("connector", __name__)


def _seat() -> dict:
    """The seat the gate already verified. Never re-derived, and NEVER invented.

    A transport that re-reads the credential is a transport that can disagree with the gate about
    who is calling, and the gate is the one with the audit trail.

    NO FALLBACK SEAT — this refuses instead, and that is the whole point of the function. The
    first version returned `{"id": "unknown", "role": "read"}` when `g.seat` was missing, which
    FABRICATED A VALID READ SEAT: a read seat may call every Tier 1 tool, so a missing identity
    became a working one. Unreachable today, because `_auth_gate` covers the whole /api/ prefix —
    and latent exactly until somebody mounts a connector route outside that prefix or narrows it,
    at which point read access is granted silently and no test goes red.

    It is also the inverse of step 1's stated principle, "a door with no lock fitted yet stays
    closed", in the file that exists to serve that door. And it poisoned the ledger: a row
    attributed to `unknown` is not an answer to the only question an audit trail is ever asked.

    A missing `g.seat` is an invariant violation, not a default. Caught by OSDev1 reviewing #1101.
    """
    seat = getattr(g, "seat", None)
    if not seat:
        # The gate is the only thing that may create a seat. If it did not run, nothing here may
        # proceed on a guess.
        log.error("connector.seat_missing_on_request", path=request.path)
        abort(401)
    return seat


@blueprint.get("/api/v1/manifest")
def get_manifest():
    """What this box can answer. Capability, never data — safe for the lowest role.

    A THIN SHELL OVER THE REGISTRY, not a second way to run the same tool. The first version
    called `manifest.build()` directly and hand-wrote its own audit row, which gave one tool two
    execution paths with different authorisation and different auditing — the exact drift the
    registry exists to prevent, reintroduced one file below it. This module's own docstring says
    it holds no policy because anything decided here would have to be decided twice; audit is
    policy. Caught by OSDev1 reviewing #1101.
    """
    payload, status = tools.call(manifest.TOOL, None, _seat())
    # THROUGH the registry for authorisation and audit, but the GET returns the manifest ITSELF
    # rather than call()'s {"tool", "result"} envelope. This is a discovery document fetched at
    # its own URL: a client asking "what is this box" should get the box, not a wrapper it has to
    # unwrap. POST /api/v1/call with tool=manifest still returns the envelope, because there the
    # envelope IS the contract — same function, same audit row, each transport in its own shape.
    if status == 200 and "result" in payload:
        return jsonify(payload["result"]), status
    return jsonify(payload), status


@blueprint.get("/api/v1/tools")
def list_tools():
    """The tool list alone, for a client that does not want the whole manifest.

    Also through `call()`. Enumerating a box's capabilities is precisely the probe a ledger
    should show, and the first version wrote no row at all.
    """
    payload, status = tools.call(manifest.TOOL, None, _seat())
    if status != 200 or "result" not in payload:
        return jsonify(payload), status
    return jsonify({"tools": payload["result"]["tools"]}), status


@blueprint.post("/api/v1/call")
def post_call():
    """Invoke one named tool with typed arguments. Never free text — that is the whole design."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "bad_body", "message": "send a JSON object"}), 400
    name = body.get("tool")
    if not name or not isinstance(name, str):
        return jsonify({"error": "no_tool",
                        "message": "name the tool to call; GET /api/v1/manifest lists them"}), 400
    payload, status = tools.call(name, body.get("args"), _seat())
    return jsonify(payload), status


# ── THE OAUTH FRONT DOOR ─────────────────────────────────────────────────────────────────────
# DELIBERATELY UNGATED, all of it except /authorize. These paths ARE the bootstrap: a client that
# has no credential reads them to learn how to get one, so requiring a credential to read them
# would be a door whose key is behind the door. `core/dispatch._needs_a_seat` covers "/api/" by
# prefix and "/mcp" exactly, so nothing here is caught by it — that is by design and the suite
# asserts it.
#
# /authorize IS THE EXCEPTION and is owner-only: it is the one place a human grants access to
# their own inbox, and a consent screen anybody could drive is not consent.
def _root() -> str:
    """This box's own origin, ALWAYS https for a real host.

    OAUTH REFUSES A NON-HTTPS ISSUER, and `request.host_url` cannot be trusted to know: Caddy
    terminates TLS and proxies to gunicorn over plain HTTP, so Flask sees `http://` unless a
    forwarded-proto header is honoured. Measured on a live box 2026-09-22 — the metadata
    advertised `http://ownbox-oauth.ownbox.app` and a client would have rejected the issuer or,
    worse, downgraded to it.
    #
    Loopback keeps http so a developer can drive the flow locally; everything else is https,
    because every sold box is.
    """
    raw = str(request.host_url or "").rstrip("/")
    host = request.host or ""
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return raw
    return "https://" + host


@blueprint.get("/.well-known/oauth-protected-resource")
@blueprint.get("/.well-known/oauth-protected-resource/mcp")
def oauth_protected_resource():
    """RFC 9728. Two paths because clients differ on whether the resource path is appended."""
    from core.connector import oauth
    return jsonify(oauth.protected_resource_metadata(_root()))


@blueprint.get("/.well-known/oauth-authorization-server")
@blueprint.get("/.well-known/oauth-authorization-server/mcp")
def oauth_authorization_server():
    from core.connector import oauth
    return jsonify(oauth.authorization_server_metadata(_root()))


@blueprint.post("/oauth/register")
def oauth_register():
    """RFC 7591. Registering grants NOTHING — it returns a name the client must carry through a
    flow a human approves. Refusing it would simply mean no connector can ever reach this box."""
    from core.connector import oauth
    try:
        out = oauth.register(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"error": "invalid_client_metadata", "error_description": str(e)}), 400
    return jsonify(out), 201


@blueprint.post("/oauth/token")
def oauth_token():
    """The code, redeemed for a seat credential. Errors use OAuth's own vocabulary, not ours —
    a client parses `error`, and inventing words here makes it report a generic failure."""
    from core.connector import oauth
    f = request.form or {}
    if str(f.get("grant_type") or "") != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400
    try:
        out = oauth.exchange(code=str(f.get("code") or ""),
                             client_id=str(f.get("client_id") or ""),
                             redirect_uri=str(f.get("redirect_uri") or ""),
                             verifier=str(f.get("code_verifier") or ""))
    except ValueError as e:
        # NEVER SAYS WHICH CHECK FAILED. Distinguishing "no such code" from "wrong verifier" is an
        # oracle for probing, and the client can act on neither.
        return jsonify({"error": str(e) or "invalid_grant"}), 400
    return jsonify(out)


_CONSENT = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Connect {name}</title>
<style>
 :root{{color-scheme:light}}
 body{{margin:0;background:#f2f3f5;color:#15171a;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
 main{{max-width:34rem;margin:0 auto;padding:2.5rem 1rem}}
 .card{{background:#fff;border-radius:16px;padding:1.6rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 h1{{font-size:1.35rem;margin:0 0 .6rem;text-wrap:balance}}
 p{{margin:.55rem 0;color:#41464d}}
 ul{{margin:.6rem 0;padding-left:1.15rem;color:#41464d}} li{{margin:.25rem 0}}
 .who{{font-weight:600;color:#15171a}}
 form{{margin-top:1.3rem;display:flex;gap:.6rem;flex-wrap:wrap}}
 button{{font:inherit;border:0;border-radius:10px;padding:.7rem 1.15rem;cursor:pointer}}
 .yes{{background:#b8442a;color:#fff}} .no{{background:#e6e8ea;color:#15171a}}
 .q{{color:#6b7178;font-size:.9rem;margin-top:1.1rem}}
 label{{display:block;border:1px solid #dfe2e5;border-radius:12px;padding:.8rem .9rem;margin:.5rem 0;cursor:pointer}}
 label:has(input:checked){{border-color:#b8442a;background:#fdf6f4}}
 label b{{display:block}} label span{{color:#6b7178;font-size:.92rem}}
</style><main><div class=card>
<h1>Let {name} into this box?</h1>
<p><span class=who>{name}</span> is asking to connect to <span class=who>{host}</span>.</p>
<p>Choose what it may do:</p>
{perms}
<p class=q>It can never send a message as your business. You can take this back at any time on
the AI coworkers screen, and nothing else about your box changes.</p>
<form method=post>{hidden}
  <button class=yes name=decision value=allow type=submit>Allow</button>
  <button class=no name=decision value=deny type=submit>Cancel</button>
</form></div></main></html>"""


@blueprint.route("/oauth/authorize", methods=["GET", "POST"])
def oauth_authorize():
    """The one screen where a person grants an assistant access to their own inbox.

    OWNER-ONLY, AND VALIDATED BEFORE IT DRAWS. Every refusable thing about the request — unknown
    client, unregistered redirect, missing or non-S256 challenge — is checked before a consent
    screen exists, because a screen rendered for an unvalidated redirect is how a grant ends up
    at somebody else's URL.

    IT NAMES WHAT IT IS GRANTING IN WORDS. "scope: act" means nothing to the person pressing the
    button; "read your conversations" and "leave a draft reply waiting" do.
    """
    from urllib.parse import urlencode

    from core import dash
    from core.connector import oauth

    q = request.values
    try:
        cl = oauth.check_authorize(client_id=str(q.get("client_id") or ""),
                                   redirect_uri=str(q.get("redirect_uri") or ""),
                                   code_challenge=str(q.get("code_challenge") or ""),
                                   method=str(q.get("code_challenge_method") or ""))
    except ValueError as e:
        # REFUSED HERE, NEVER REDIRECTED. If the redirect_uri itself is what failed, bouncing the
        # error to it would be doing the exact thing the check exists to prevent.
        return jsonify({"error": "invalid_request", "error_description": str(e)}), 400

    scope = (str(q.get("scope") or "read").strip().lower().split() or ["read"])[0]
    if scope not in oauth.ROLES:
        scope = "read"

    try:
        who = dash.session_user(request) or {}
    except Exception:                                    # noqa: BLE001
        who = {}
    if (who.get("role") or "") != "owner":
        # NOT A 403 WITH NOTHING BEHIND IT. Somebody arriving here is mid-flow in another app;
        # sending them to sign in and back is the only path that ends with them connected.
        nxt = "/oauth/authorize?" + urlencode({k: v for k, v in q.items(True)})
        return redirect("/dash/login?next=" + quote(nxt, safe=""), code=303)

    if request.method == "POST":
        if str(request.form.get("decision") or "") != "allow":
            # A refusal IS an answer, and OAuth has a word for it.
            sep = "&" if "?" in cl and False else ("&" if "?" in str(q.get("redirect_uri")) else "?")
            back = str(q.get("redirect_uri")) + sep + urlencode(
                {"error": "access_denied", "state": str(q.get("state") or "")})
            return redirect(back, code=303)
        granted = str(request.form.get("grant") or scope).strip().lower()
        if granted not in oauth.ROLES:
            granted = "read"
        code = oauth.issue_code(client_id=cl["client_id"],
                                redirect_uri=str(q.get("redirect_uri")),
                                code_challenge=str(q.get("code_challenge")),
                                role=granted, label=cl["name"], user_id=who.get("id"))
        sep = "&" if "?" in str(q.get("redirect_uri")) else "?"
        back = str(q.get("redirect_uri")) + sep + urlencode(
            {"code": code, "state": str(q.get("state") or "")})
        return redirect(back, code=303)

    # THE OWNER CHOOSES, NOT THE CLIENT. The first version took `scope` from the query string,
    # so an assistant that asked for nothing got `read` — which is why Claude connected with
    # seven tools and no `draft_reply` on 2026-09-22, and the owner could not tell why. A client
    # asking for less than it needs is a support ticket; a client asking for MORE than the owner
    # wants is worse. The request is a hint; this screen is the decision.
    # DEFAULTS TO READ-AND-DRAFT (owner, 2026-09-22): "people don't have to send their drafts
    # but they're always gonna want to give this permission." A draft is not a send — the box
    # cannot send from a connector seat at all — so the cautious default was caution about
    # nothing, and it cost the owner a connection with no draft_reply and no way to see why.
    # An assistant that explicitly asks for `read` still gets exactly that.
    pre = "read" if scope == "read" and str(q.get("scope") or "").strip() else "act"
    perms = "".join(
        f'<label><input type=radio name=grant value="{v}"{" checked" if v == pre else ""}>'
        f'<b>{t}</b><span>{w}</span></label>'
        for v, t, w in (
            ("read", "Read only",
             "It can read your conversations and your morning report. It cannot write anything."),
            ("act", "Read and draft replies",
             "Everything above, plus it can leave a suggested reply waiting on the screen. "
             "It still cannot send — you press send, or you do not.")))
    hidden = "".join(
        f'<input type=hidden name="{escape(k)}" value="{escape(str(v))}">'
        for k, v in q.items(True) if k not in ("decision", "grant"))
    return _CONSENT.format(name=escape(cl["name"]), host=escape(request.host),
                           perms="".join(perms), hidden=hidden), 200
