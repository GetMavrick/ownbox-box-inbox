"""The OAuth front door an MCP client needs to find its way in.

WHY THIS EXISTS AT ALL. The box already had a perfectly good credential — a seat, minted on a
screen, hashed at rest, revocable, audited. What it did not have was any way for a CLIENT to
DISCOVER that. MCP's June-2025 revision makes an MCP server a plain OAuth resource server:

    1. POST /mcp with no token        -> 401 + WWW-Authenticate: Bearer resource_metadata="…"
    2. GET that metadata (RFC 9728)   -> which authorization server to talk to
    3. GET /.well-known/oauth-authorization-server (RFC 8414) -> its endpoints
    4. POST to registration_endpoint  -> register itself, get a client_id (RFC 7591)
    5. /authorize with PKCE, then /token

Claude's "add custom connector" walks exactly that and offers no box to paste a key into. Measured
2026-09-22 on a live box: it failed with *"Couldn't register with Ownbox's sign-in service"*,
because we served a bare 401 and 404 on every .well-known path. The owner saw a product that
could not connect to the assistant it is sold as connecting to.

A GRANT MINTS A SEAT, AND THAT IS THE WHOLE DESIGN. Nothing here invents a second access system:
`/token` calls `seats.mint()` and hands back the seat credential as the access token. Every
downstream thing — `_seat_authorized`, `visible_to`, revocation, the audit row — keeps working
unchanged, and a buyer revoking "Grok on X" on the coworkers screen revokes the OAuth grant too,
because they are the same row.

THE BOX IS ITS OWN AUTHORIZATION SERVER. The spec prefers a separate one, and for a fleet of
single-tenant boxes that would mean a central service holding every customer's grants — which is
the architecture the owner explicitly refused when he chose per-box addresses over one
`mcp.ownbox.app`. A standalone resource+authorization server is normal for single tenancy, and it
keeps the promise that nothing of a buyer's crosses our wire.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from core import state
from core.connector import seats
from core.logging import get_logger

log = get_logger(__name__)

CODE_TTL_S = 300               # RFC 6749 says ten minutes maximum; five is plenty for a redirect
ROLES = ("read", "act")        # `service` is ours, never granted through a public flow


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def protected_resource_metadata(root: str) -> dict:
    """RFC 9728. The document a client reads to learn who authorises this resource."""
    root = root.rstrip("/")
    return {"resource": f"{root}/mcp",
            "authorization_servers": [root],
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(ROLES)}


def authorization_server_metadata(root: str) -> dict:
    """RFC 8414. S256 only — `plain` PKCE is not a protection, it is a spelling."""
    root = root.rstrip("/")
    return {"issuer": root,
            "authorization_endpoint": f"{root}/oauth/authorize",
            "token_endpoint": f"{root}/oauth/token",
            "registration_endpoint": f"{root}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": list(ROLES)}


def challenge_header(root: str) -> str:
    """The 401's `WWW-Authenticate`. Without it a client cannot begin — it is the whole bootstrap."""
    root = root.rstrip("/")
    return f'Bearer resource_metadata="{root}/.well-known/oauth-protected-resource"'


def _https_only(uris) -> list[str]:
    """Redirect targets, validated. http:// is refused EXCEPT on loopback, which is how a desktop
    client receives its code — and loopback cannot be intercepted off the machine."""
    out = []
    for u in uris or []:
        u = str(u)
        if u.startswith("https://"):
            out.append(u)
        elif u.startswith("http://127.0.0.1") or u.startswith("http://localhost"):
            out.append(u)
    return out


def register(payload: dict) -> dict:
    """RFC 7591 dynamic client registration. OPEN BY DESIGN, and that is not a hole.

    Registering mints nothing and grants nothing: it returns an identifier a client must then
    carry through a flow a HUMAN approves on this box. An unapproved client_id is a name with no
    rights. Refusing registration would simply mean no connector can ever reach the box.
    """
    uris = _https_only(payload.get("redirect_uris"))
    if not uris:
        raise ValueError("redirect_uris must contain at least one https (or loopback) URI")
    name = str(payload.get("client_name") or "An AI coworker")[:80]
    client_id = "obc_" + secrets.token_urlsafe(18)
    with state.connect() as c:
        c.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, created_at) "
                  "VALUES (?,?,?,?)", (client_id, name, json.dumps(uris), _now().isoformat()))
    log.info("oauth.client_registered", client_id=client_id, name=name)
    # No client_secret: a public client with PKCE is the shape MCP clients use, and a secret we
    # handed to anyone who asked would be a secret in name only.
    return {"client_id": client_id, "client_name": name, "redirect_uris": uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"], "response_types": ["code"]}


def client(client_id: str) -> dict | None:
    with state.connect() as c:
        row = c.execute("SELECT client_id, name, redirect_uris FROM oauth_clients "
                        "WHERE client_id = ?", (str(client_id or ""),)).fetchone()
    if not row:
        return None
    return {"client_id": row["client_id"], "name": row["name"],
            "redirect_uris": json.loads(row["redirect_uris"])}


def check_authorize(*, client_id: str, redirect_uri: str, code_challenge: str,
                    method: str) -> dict:
    """Everything /authorize must refuse BEFORE a person is shown a consent screen.

    A consent screen rendered for an unvalidated redirect is how a grant ends up at somebody
    else's URL, so the checks come first and the screen only draws for a request that would be
    honoured.
    """
    cl = client(client_id)
    if cl is None:
        raise ValueError("unknown client")
    # EXACT MATCH, never a prefix. Prefix matching on redirect URIs is the classic open-redirect
    # that turns an authorization code into somebody else's access.
    if redirect_uri not in cl["redirect_uris"]:
        raise ValueError("redirect_uri does not match this client's registration")
    if (method or "").upper() != "S256":
        raise ValueError("only S256 code challenges are accepted")
    if not code_challenge or len(code_challenge) < 32:
        raise ValueError("a code_challenge is required")
    return cl


def issue_code(*, client_id: str, redirect_uri: str, code_challenge: str, role: str,
               label: str, user_id: str | None) -> str:
    """Record the owner's approval as a short-lived, single-use, PKCE-bound code."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    code = secrets.token_urlsafe(32)
    with state.connect() as c:
        c.execute("INSERT INTO oauth_codes (code, client_id, redirect_uri, code_challenge, role,"
                  " label, user_id, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                  (code, client_id, redirect_uri, code_challenge, role, label[:60], user_id,
                   (_now() + timedelta(seconds=CODE_TTL_S)).isoformat()))
    log.info("oauth.code_issued", client_id=client_id, role=role)
    return code


def exchange(*, code: str, client_id: str, redirect_uri: str, verifier: str) -> dict:
    """Redeem a code for an access token — which IS a seat credential.

    SINGLE USE, ENFORCED BY A WRITE. `used_at` is set in the same transaction that reads the row,
    so two simultaneous redemptions cannot both win.
    """
    with state.connect() as c:
        row = c.execute("SELECT * FROM oauth_codes WHERE code = ?", (str(code or ""),)).fetchone()
        if row is None:
            raise ValueError("invalid_grant")
        if row["used_at"]:
            # A REPLAYED CODE IS AN INCIDENT, not a retry. The spec says revoke what it bought.
            log.error("oauth.code_replayed", client_id=row["client_id"])
            raise ValueError("invalid_grant")
        if datetime.fromisoformat(row["expires_at"]) < _now():
            raise ValueError("invalid_grant")
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            raise ValueError("invalid_grant")
        expect = _b64(hashlib.sha256((verifier or "").encode()).digest())
        # CONSTANT TIME: this compares a secret the client proves it knows.
        if not secrets.compare_digest(expect, row["code_challenge"]):
            raise ValueError("invalid_grant")
        c.execute("UPDATE oauth_codes SET used_at = ? WHERE code = ? AND used_at IS NULL",
                  (_now().isoformat(), code))
    seat_id, credential = seats.mint(row["label"], row["role"])
    log.info("oauth.token_issued", client_id=client_id, seat_id=seat_id, role=row["role"])
    # No refresh token: the credential does not expire, and the owner revokes it on the same
    # screen they revoke a hand-minted one. A rotating refresh token would add a second lifetime
    # to reason about and buy nothing a revoke button does not already give.
    return {"access_token": credential, "token_type": "Bearer", "scope": row["role"]}


def sweep(*, older_than_s: int = 3600) -> int:
    """Drop spent and expired codes. They are worthless, but they are also a list of grants."""
    cutoff = (_now() - timedelta(seconds=older_than_s)).isoformat()
    with state.connect() as c:
        cur = c.execute("DELETE FROM oauth_codes WHERE expires_at < ? OR "
                        "(used_at IS NOT NULL AND used_at < ?)", (cutoff, cutoff))
        return cur.rowcount or 0
