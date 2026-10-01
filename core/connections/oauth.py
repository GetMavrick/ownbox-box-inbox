"""Sign in to an app's MCP server on the app's own login page: MCP authorization (OAuth 2.1).

docs/SCOPE_CONNECTIONS_MCP_FIRST.md, phase 1 (owner-approved 2026-10-01): connect an app by its MCP address and
SIGN IN. Most apps' MCP servers take no pasted token; they send the person to their own login page. The box does
what an AI app does for a custom connector:

  1. FIND THE LOGIN. The app's 401 names its Protected Resource Metadata (RFC 9728), which names the authorization
     server, whose own metadata (RFC 8414, or OpenID's) names the endpoints. Older servers (spec 2025-03-26) keep
     that metadata at the MCP server's own origin, so that is tried last.
  2. REGISTER THIS BOX as a client (RFC 7591), with its own callback, https://<box>/settings/sources/callback.
  3. SEND THE OWNER to the app's login page with PKCE (S256), a single-use state, and the MCP server named as the
     `resource` (RFC 8707), so the token is good for that server only.
  4. ON THE WAY BACK, check the state, trade the code for tokens, and connect as usual: the app's tools are listed
     for real with the new token before anything is saved.
  5. KEEP IT FRESH. A token near its end is refreshed before a call, UNDER A LOCK: a refresh token is often single
     use, and two workers refreshing at once would sign the box out.

Every request goes through core/net's door (public https only; the box follows no redirect). Tokens live in
box_secrets with the connection, never in a log, a message or the page.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse

from core import box_secrets, net
from core.connections import client as _client
from core.logging import get_logger

log = get_logger(__name__)

PENDING = "app_conn_pending"           # box_secrets: the one sign-in under way
PENDING_TTL_S = 900
CALLBACK = "/settings/sources/callback"
REFRESH_EARLY_S = 120
_RESOURCE_META = re.compile(r'resource_metadata="([^"]+)"')
_SCOPE = re.compile(r'scope="([^"]*)"')


class SignInFailed(_client.ConnectionFailed):
    """Shown to the owner on Data Sources: what happened, and what to do."""


def _json(body) -> dict:
    try:
        got = json.loads(body or "")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def _public(url) -> bool:
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    return parts.scheme in _client.SCHEMES and bool(parts.hostname) and net.url_is_public(str(url), _client.RESOLVE)


def _get_json(url: str) -> dict:
    if not _public(url):
        return {}
    try:
        status, body = net.get_public(url, headers={"Accept": "application/json", "User-Agent": _client.USER_AGENT},
                                      resolve=_client.RESOLVE)
    except Exception:                                     # noqa: BLE001 — not there is "try the next place"
        return {}
    return _json(body) if status == 200 else {}


def _well_known(base: str, name: str) -> list[str]:
    """Where RFC 8414 and RFC 9728 put a document: inserted before the path first, then at the root."""
    p = urllib.parse.urlsplit(base)
    root, path = f"{p.scheme}://{p.netloc}", p.path.rstrip("/")
    return ([f"{root}/.well-known/{name}{path}"] if path else []) + [f"{root}/.well-known/{name}"]


def _server_meta_urls(issuer: str) -> list[str]:
    urls = _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration")
    if urllib.parse.urlsplit(issuer).path.rstrip("/"):
        urls.append(issuer.rstrip("/") + "/.well-known/openid-configuration")
    return list(dict.fromkeys(urls))


def same_resource(resource: str, url: str) -> bool:
    """Is the metadata about the server the owner typed? (RFC 9728 §3.3, OSDev4's review of #1751.)

    Scheme, host and port exactly; the path equal to the typed one, or a prefix of it at a "/" (a server's
    metadata may name its origin, as Asana's does). WITHOUT THIS a hostile address can name a real login
    and another service's resource: the owner signs in on a genuine page, and the box hands that service's
    token to the hostile address."""
    try:
        r, u = urllib.parse.urlsplit(str(resource or "")), urllib.parse.urlsplit(str(url or ""))
        rport, uport = r.port, u.port
    except ValueError:
        return False
    if not r.hostname or r.fragment or r.username or r.password:
        return False
    default = {"https": 443, "http": 80}
    if (r.scheme, r.hostname, rport or default.get(r.scheme)) != (u.scheme, u.hostname, uport or default.get(u.scheme)):
        return False
    rp, up = r.path.rstrip("/"), u.path.rstrip("/")
    return rp == "" or up == rp or up.startswith(rp + "/")


def discover(url: str, challenge: str = "") -> dict:
    """The app's login, as the box will use it: {resource, issuer, authorize, token, register, scope}."""
    url = _client.check_address(url)
    host = _client.host_of(url)
    found = _RESOURCE_META.search(challenge or "")
    resource_meta = {}
    for u in ([found.group(1)] if found else []) + _well_known(url, "oauth-protected-resource"):
        resource_meta = _get_json(u)
        if resource_meta.get("authorization_servers"):
            break
    said = _SCOPE.search(challenge or "")
    scope = said.group(1) if said else " ".join(str(s) for s in resource_meta.get("scopes_supported") or [])
    servers = resource_meta.get("authorization_servers") or []
    if servers and isinstance(servers, list):
        resource = str(resource_meta.get("resource") or "")
        if not same_resource(resource, url):
            raise SignInFailed(f"{host}'s sign-in details are about a different address "
                               f"({_client.host_of(resource) if resource else 'none given'}), so the box won't "
                               "sign in there. Check the address in the app's own MCP instructions.")
        issuer = str(servers[0])
    else:
        p = urllib.parse.urlsplit(url)                   # 2025-03-26: the MCP server's origin signs people in
        issuer, resource = f"{p.scheme}://{p.netloc}", url
    meta = {}
    for u in _server_meta_urls(issuer):
        meta = _get_json(u)
        if meta.get("authorization_endpoint") and meta.get("token_endpoint"):
            break
    if not (meta.get("authorization_endpoint") and meta.get("token_endpoint")):
        raise SignInFailed(f"{host} asks for a sign-in but doesn't say where. If the app offers a token, paste "
                           "that instead.")
    if meta.get("issuer") and str(meta["issuer"]).rstrip("/") != issuer.rstrip("/"):
        raise SignInFailed(f"{host}'s sign-in details don't match the server they came from, so the box won't use "
                           "them.")
    if "S256" not in (meta.get("code_challenge_methods_supported") or []):
        raise SignInFailed(f"{host}'s sign-in doesn't offer the protection the box requires (PKCE). If the app "
                           "offers a token, paste that instead.")
    if not meta.get("registration_endpoint"):
        raise SignInFailed(f"{host}'s sign-in needs the box registered by hand, which it can't do yet. If the app "
                           "offers a token, paste that instead.")
    ends = {k: str(meta[k]) for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint")}
    if not all(_public(v) for v in ends.values()):
        raise SignInFailed(f"{host}'s sign-in points somewhere the box can't safely go.")
    return {"resource": resource, "issuer": issuer, "authorize": ends["authorization_endpoint"],
            "token": ends["token_endpoint"], "register": ends["registration_endpoint"], "scope": scope[:500]}


def register(meta: dict, redirect: str) -> dict:
    """Register this box with the app's sign-in (RFC 7591). {client_id, client_secret}."""
    host = _client.host_of(meta["register"])
    try:
        status, body = net.post_public(meta["register"], json={
            "client_name": "Ownbox", "redirect_uris": [redirect], "grant_types": ["authorization_code",
                                                                                  "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"},
            headers={"Accept": "application/json", "User-Agent": _client.USER_AGENT}, resolve=_client.RESOLVE)
    except net.PostRefused:
        raise SignInFailed(f"Couldn't reach {host} to set up the sign-in.") from None
    got = _json(body)
    if status not in (200, 201) or not got.get("client_id"):
        raise SignInFailed(f"{host} wouldn't set up a sign-in for the box ({status}). Try again in a minute.")
    return {"client_id": str(got["client_id"])[:400], "client_secret": str(got.get("client_secret") or "")[:400]}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pack(d: dict) -> str:
    return _b64(json.dumps(d, separators=(",", ":")).encode())


def _unpack(s: str) -> dict:
    try:
        return _json(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode())
    except (ValueError, UnicodeDecodeError):
        return {}


def begin(name: str, url: str, *, box_host: str, challenge: str = "", user_id: str | None = None) -> str:
    """The app's login page for this box, with everything needed to come back. One sign-in at a time."""
    from core.connections import store
    name, _slug = store.check_new(name)
    meta = discover(url, challenge)
    redirect = f"https://{box_host}{CALLBACK}"
    reg = register(meta, redirect)
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(24)
    box_secrets.put(PENDING, _pack({"state": state, "verifier": verifier, "name": name, "url": url,
                                    "redirect": redirect, "meta": meta, "client": reg,
                                    "at": int(time.time())}), user_id=user_id)
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": redirect,
         "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
         "state": state, "resource": meta["resource"]}
    if meta["scope"]:
        q["scope"] = meta["scope"]
    log.info("connections.signin_started", host=_client.host_of(url))
    return meta["authorize"] + ("&" if "?" in meta["authorize"] else "?") + urllib.parse.urlencode(q)


def cancel(*, user_id: str | None = None) -> None:
    box_secrets.clear(PENDING, user_id=user_id)


def _tokens(conn: dict, form: dict) -> dict:
    """Ask the token endpoint. {token, refresh, expires_at}; SignInFailed in words when it says no."""
    host = _client.host_of(conn["token_endpoint"])
    form = dict(form, client_id=conn["client_id"], resource=conn["resource"])
    if conn.get("client_secret"):
        form["client_secret"] = conn["client_secret"]
    try:
        status, body = net.post_public(conn["token_endpoint"], data=form, resolve=_client.RESOLVE,
                                       headers={"Accept": "application/json", "User-Agent": _client.USER_AGENT})
    except net.PostRefused:
        raise SignInFailed(f"Couldn't reach {host} to finish the sign-in.") from None
    got = _json(body)
    kind = str(got.get("token_type") or "Bearer")
    if status != 200 or not got.get("access_token") or kind.lower() != "bearer":
        raise SignInFailed(f"{host} didn't accept the sign-in ({str(got.get('error') or status)[:40]}). Start "
                           "again from Data Sources.")
    try:
        lasts = int(got.get("expires_in") or 0)
    except (TypeError, ValueError):
        lasts = 0
    return {"token": str(got["access_token"]), "refresh": str(got.get("refresh_token") or ""),
            "expires_at": int(time.time()) + lasts if lasts > 0 else 0}


def finish(code: str, state: str, *, by: str, user_id: str | None = None) -> dict:
    """Back from the app's login page: check the state, get the tokens, connect. Returns the saved record."""
    from core.connections import store
    raw = box_secrets.get(PENDING)
    pending = _unpack(raw)
    if not pending or not state or not hmac.compare_digest(str(state), str(pending.get("state") or "")):
        raise SignInFailed("That sign-in wasn't started from this box, or was already used. Start again from "
                           "Data Sources.")
    # SINGLE USE, EVEN FOR TWO CALLBACKS AT ONCE (OSDev4, review of #1751): taken only if it still holds what
    # was read, in one statement, before anything can fail. The second of two gets the plain sentence.
    if not box_secrets.take(PENDING, raw, user_id=user_id):
        raise SignInFailed("That sign-in was already used. If the app isn't on Data Sources, start again.")
    if time.time() - int(pending.get("at") or 0) > PENDING_TTL_S:
        raise SignInFailed("That sign-in took too long. Start again from Data Sources.")
    meta, reg = pending["meta"], pending["client"]
    conn = {"url": pending["url"], "token_endpoint": meta["token"], "client_id": reg["client_id"],
            "client_secret": reg.get("client_secret") or "", "resource": meta["resource"]}
    conn.update(_tokens(conn, {"grant_type": "authorization_code", "code": str(code or ""),
                               "redirect_uri": pending["redirect"], "code_verifier": pending["verifier"]}))
    rec = store.add(pending["name"], pending["url"], conn=conn, by=by)
    log.info("connections.signed_in", app=rec["slug"], host=rec["host"])
    return rec


def _lock_path() -> str:
    from core.config import settings
    return os.path.join(os.path.dirname(os.path.abspath(settings.db_path)), ".connections.lock")


def token_for(slug: str, conn: dict, *, force: bool = False) -> str:
    """The token to send now. A signed-in connection near the end of its token is refreshed first, under a
    lock, re-reading inside it: the worker that waited adopts the token the other one just got."""
    from core.connections import store
    if not conn.get("refresh") or not conn.get("token_endpoint"):
        return str(conn.get("token") or "")
    fresh = lambda c: c.get("expires_at", 0) == 0 or c["expires_at"] - REFRESH_EARLY_S > time.time()  # noqa: E731
    if not force and fresh(conn):
        return str(conn.get("token") or "")
    seen = conn.get("token")
    with open(_lock_path(), "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            # DISCONNECTED WHILE WAITING (OSDev4, review of #1751): never refresh, and never write a credential
            # back for an app the owner removed.
            current = store.secret(slug)
            if not current:
                raise SignInFailed("This app was disconnected on Data Sources.")
            conn = current
            if (conn.get("token") != seen) or (not force and fresh(conn)):
                return str(conn.get("token") or "")          # another worker refreshed while this one waited
            new = _tokens(conn, {"grant_type": "refresh_token", "refresh_token": conn["refresh"]})
            conn.update(token=new["token"], refresh=new["refresh"] or conn["refresh"], expires_at=new["expires_at"])
            store.save_secret(slug, conn)
            log.info("connections.token_refreshed", app=slug)
            return conn["token"]
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
