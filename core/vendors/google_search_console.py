"""Google Search Console — one button, then pick your site.

OWNER, 2026-09-25: "Our clients will never be able to click through and figure all that shit out…
we need to add a button to the settings page that has them login into their Google account and
choose a property/website as their primary." So a buyer never sees Google Cloud: they press
Connect, sign in on Google's own page, and choose one of the sites their account already has.

WHY THE CODE IS REDEEMED THROUGH OWNBOX. Every box signs in through ONE shared Ownbox Google app,
and a Google web client needs its client secret to redeem a code or refresh a token. The box repo is
public, so that secret can never ship on a box, and review C3 keeps it off the website too. Google
sends the buyer back to www.ownbox.io/connect/google, a relay that holds nothing and bounces the code
to this box. The box then redeems it at orders.ownbox.app/google (provisioner/google_handoff.py),
which adds the secret, checks the caller is a box we built by its own deploy token, and passes
Google's answer back unchanged. PKCE keeps a code that leaked on the way useless to anyone but the
box that started the sign-in, because only that box holds the verifier.

READ-ONLY. The only Search Console scope asked for is webmasters.readonly: the box can read what a
site ranks for and cannot change anything in the buyer's Google account. `openid email` rides along
so the page can say which Google account is connected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse

from core import box_secrets, net
from core.logging import get_logger

log = get_logger(__name__)

# The shared Ownbox app. A client id is public by design (it is in every sign-in URL); the env
# override exists for tests and for the day the app moves to its own Google project.
CLIENT_ID = "1061493361396-2orhl8rcbdqn159rn2p2du68gvbrqad7.apps.googleusercontent.com"
RELAY = "https://www.ownbox.io"                 # where Google sends the buyer back (no secret)
BROKER = "https://orders.ownbox.app/google"      # where the code is redeemed (holds the secret)
AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE = "https://oauth2.googleapis.com/revoke"
SITES = "https://www.googleapis.com/webmasters/v3/sites"
# What a site was searched for, per property. The property goes in URL-encoded, whole, so both a
# URL-prefix property (https://example.com/) and a domain property (sc-domain:example.com) work.
SEARCH_ANALYTICS = SITES + "/{site}/searchAnalytics/query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPES = ("openid", "email", SCOPE)

# The hand-off relays only to a box under ownbox.app, so a sign-in can only start from there.
BOX_HOST = re.compile(r"^[a-z0-9-]+\.ownbox\.app$")
PENDING_TTL_S = 600
# Levels at which Search Console will answer queries for a site. `siteUnverifiedUser` cannot.
_READABLE = ("siteOwner", "siteFullUser", "siteRestrictedUser")

REFRESH = "google_sc_refresh_token"
ACCOUNT = "google_sc_account"
PROPERTY = "google_sc_property"
PENDING = "google_sc_pending"

_cached: dict = {}


class Refused(ValueError):
    """The step did not happen. `key` names why, for the page; the message is for a log line."""

    def __init__(self, key: str, detail: str = ""):
        self.key = key
        super().__init__(f"{key}: {detail}" if detail else key)


def client_id() -> str:
    return os.environ.get("OWNBOX_GOOGLE_CLIENT_ID") or CLIENT_ID


def broker() -> str:
    return (os.environ.get("OWNBOX_GOOGLE_BROKER") or BROKER).rstrip("/")


def redirect_uri() -> str:
    return (os.environ.get("OWNBOX_GOOGLE_RELAY") or RELAY).rstrip("/") + "/connect/google"


def _box_headers(host: str) -> dict:
    """Who is asking: this box's name and its own narrow deploy token (see core/dispatch.py)."""
    from core.config import settings                   # read at call time, never bound at import
    return {"X-Ownbox-Host": str(host or ""),
            "Authorization": f"Bearer {getattr(settings, 'deploy_token', '') or ''}"}


def _own_host() -> str:
    from core import claim
    return claim.provisioned_host()


def host_ok(host: str) -> bool:
    return bool(BOX_HOST.match(str(host or "").strip().lower()))


def status() -> dict:
    return {"connected": box_secrets.is_set(REFRESH), "account": box_secrets.get(ACCOUNT),
            "property": box_secrets.get(PROPERTY)}


def begin(host: str, *, user_id: str | None = None, now: float | None = None) -> str:
    """The Google sign-in URL for this box. Remembers one pending sign-in; a newer one replaces it."""
    host = str(host or "").strip().lower()
    if not host_ok(host):
        raise Refused("wrong_host", host[:80])
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    nonce = secrets.token_urlsafe(24)                    # base64url: never contains the "." we split on
    box_secrets.put(PENDING, json.dumps({"nonce": nonce, "verifier": verifier, "host": host,
                                         "at": int(now if now is not None else time.time())},
                                        separators=(",", ":")), user_id=user_id)
    q = {"client_id": client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
         "include_granted_scopes": "true", "code_challenge": challenge,
         "code_challenge_method": "S256", "state": f"{nonce}.{host}"}
    return AUTHORIZE + "?" + urllib.parse.urlencode(q)


def _json(body: str) -> dict:
    try:
        out = json.loads(body or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _email_from(id_token: str) -> str:
    """The account's address, for display only. It arrived straight from Google through our own
    hand-off over TLS, so it is not re-verified here; nothing is authorised on it."""
    try:
        part = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return str(claims.get("email") or "")
    except Exception:                                    # noqa: BLE001 — no address is still connected
        return ""


def finish(code: str, state: str, *, user_id: str | None = None, now: float | None = None) -> str:
    """Redeem Google's code for this box. Returns the connected account's address."""
    raw = box_secrets.get(PENDING)
    pending = _json(raw)
    nonce = str(state or "").split(".", 1)[0]
    if not pending or not nonce or not hmac.compare_digest(nonce, str(pending.get("nonce") or "")):
        raise Refused("not_ours")
    box_secrets.clear(PENDING, user_id=user_id)          # single use, before anything can fail
    if (now if now is not None else time.time()) - int(pending.get("at") or 0) > PENDING_TTL_S:
        raise Refused("too_slow")
    try:
        code_status, body = net.post_public(broker() + "/token",
                                            json={"code": str(code or ""),
                                                  "code_verifier": pending["verifier"]},
                                            headers=_box_headers(pending.get("host") or ""))
    except net.PostRefused as e:
        raise Refused("handoff_down", str(e)) from None
    got = _json(body)
    if code_status == 503:
        raise Refused("not_switched_on")
    if code_status == 401:
        raise Refused("not_a_sold_box")
    if code_status != 200 or not got.get("refresh_token"):
        raise Refused("google_said_no", f"HTTP {code_status} {str(got.get('error') or '')[:60]}")
    if SCOPE not in str(got.get("scope") or "").split():
        # GOOGLE LETS A PERSON UNTICK A PERMISSION on its consent page, and then still sends a code.
        raise Refused("no_search_console")
    email = _email_from(str(got.get("id_token") or ""))
    if box_secrets.get(ACCOUNT) != email:
        box_secrets.clear(PROPERTY, user_id=user_id)     # another account's site is not this one's
    box_secrets.put(REFRESH, str(got["refresh_token"]), user_id=user_id)
    if email:
        box_secrets.put(ACCOUNT, email, user_id=user_id)
    _cached.clear()
    log.info("google_sc.connected", user=user_id)
    return email


def _access_token(now: float | None = None) -> str:
    t = now if now is not None else time.time()
    if _cached.get("token") and _cached.get("until", 0) > t:
        return _cached["token"]
    refresh = box_secrets.get(REFRESH)
    if not refresh:
        raise Refused("not_connected")
    try:
        code_status, body = net.post_public(broker() + "/refresh", json={"refresh_token": refresh},
                                            headers=_box_headers(_own_host()))
    except net.PostRefused as e:
        raise Refused("handoff_down", str(e)) from None
    got = _json(body)
    if code_status != 200 or not got.get("access_token"):
        # invalid_grant: the buyer removed Ownbox from their Google account, or Google expired it.
        raise Refused("signed_out" if got.get("error") == "invalid_grant" else "google_said_no",
                      f"HTTP {code_status}")
    _cached.update(token=str(got["access_token"]),
                   until=t + max(60, int(got.get("expires_in") or 3600)) - 60)
    return _cached["token"]


def sites() -> list[dict]:
    """The sites this Google account can read in Search Console, as [{url, level}], sorted."""
    code_status, body = net.get_public(SITES, headers={"Authorization": f"Bearer {_access_token()}"})
    if code_status != 200:
        raise Refused("google_said_no", f"sites HTTP {code_status}")
    rows = _json(body).get("siteEntry") or []
    out = [{"url": str(r.get("siteUrl") or ""), "level": str(r.get("permissionLevel") or "")}
           for r in rows if isinstance(r, dict) and r.get("permissionLevel") in _READABLE]
    return sorted((r for r in out if r["url"]), key=lambda r: r["url"])


def search_analytics(start: str, end: str, dimensions=("query",), limit: int = 250) -> list[dict]:
    """What the chosen site was searched for between two dates (YYYY-MM-DD, inclusive).

    Rows as Google gives them: [{"keys": [...one per dimension], "clicks", "impressions", "ctr",
    "position"}], most clicks first. Read-only, like everything here. Raises Refused: `not_connected`
    or `no_property` when there is nothing to read yet, `google_down` or `google_said_no` otherwise.
    A machine that shows these rows decides how to say each one; this module only fetches them.
    """
    site = box_secrets.get(PROPERTY)
    if not site:
        raise Refused("no_property")
    url = SEARCH_ANALYTICS.format(site=urllib.parse.quote(site, safe=""))
    body = {"startDate": str(start), "endDate": str(end), "dimensions": list(dimensions),
            "rowLimit": max(1, min(int(limit), 25000))}
    try:
        code_status, text = net.post_public(
            url, json=body, headers={"Authorization": f"Bearer {_access_token()}"})
    except net.PostRefused as e:
        raise Refused("google_down", str(e)) from None
    if code_status != 200:
        raise Refused("google_said_no", f"searchAnalytics HTTP {code_status}")
    rows = _json(text).get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def choose(site_url: str, *, user_id: str | None = None) -> str:
    """Make one of this account's own sites the box's primary. Anything else is refused."""
    want = str(site_url or "").strip()
    if want not in {r["url"] for r in sites()}:
        raise Refused("not_a_site")
    box_secrets.put(PROPERTY, want, user_id=user_id)
    return want


def disconnect(*, user_id: str | None = None) -> None:
    """Forget the connection on this box, and ask Google to revoke it (best effort)."""
    refresh = box_secrets.get(REFRESH)
    if refresh:
        try:
            net.post_public(REVOKE, data={"token": refresh})
        except net.PostRefused:
            pass                                         # forgotten here is what the buyer asked for
    for name in (REFRESH, ACCOUNT, PROPERTY, PENDING):
        box_secrets.clear(name, user_id=user_id)
    _cached.clear()
