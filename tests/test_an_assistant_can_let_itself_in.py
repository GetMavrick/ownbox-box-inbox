"""The OAuth front door an MCP client needs — and every way in that must stay shut.

WHAT WAS TRUE BEFORE THIS (measured on a live box, 2026-09-22). The box had a good credential: a
seat, minted on a screen, hashed at rest, revocable, audited. It had no way for a CLIENT to
DISCOVER that. `/mcp` answered a bare 401 with no `WWW-Authenticate`, and every `.well-known`
path was a 404 — so Claude's "add custom connector" failed with *"Couldn't register with Ownbox's
sign-in service"* and never offered the owner anywhere to paste the key he was holding.

A connector UI does not paste bearer tokens. Claude's does not, ChatGPT's does not, Grok's does
not — they all walk MCP's authorization spec instead. So "connect any AI coworker", the thing the
$499 card sells, was true only for clients where you can hand-edit a config file.

WHAT THIS SUITE DEFENDS:
  · the bootstrap a client needs is REACHABLE WITHOUT a credential — it is the door's handle
  · the 401 carries `resource_metadata`, or discovery can never begin
  · a grant MINTS A SEAT, so revoke/audit/visibility keep working with no second access system
  · and the refusals: foreign redirects, near-miss redirects, `plain` PKCE, replayed codes,
    wrong verifiers — each of which turns an authorization code into somebody else's access

Run: python tests/test_an_assistant_can_let_itself_in.py
"""
import base64
import hashlib
import os
import secrets
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "oauth.db")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

from core import state                                                  # noqa: E402

state.init_db()

from core.connector import oauth, seats                                 # noqa: E402

_failed = 0
ROOT = "https://acme.ownbox.app"


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _chal(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def test_a_client_can_find_the_door():
    print("test_a_client_can_find_the_door")
    prm = oauth.protected_resource_metadata(ROOT)
    ok("the resource names itself (RFC 9728)", prm["resource"] == f"{ROOT}/mcp", str(prm))
    ok("...and points at an authorization server", prm["authorization_servers"] == [ROOT], str(prm))
    asm = oauth.authorization_server_metadata(ROOT)
    for k in ("issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint"):
        ok(f"the AS metadata carries {k}", bool(asm.get(k)), str(asm))
    # S256 ONLY. `plain` is not a weaker protection, it is the absence of one: the verifier and
    # the challenge are the same string, so anyone who sees the challenge can redeem the code.
    ok("only S256 challenges are advertised", asm["code_challenge_methods_supported"] == ["S256"],
       str(asm["code_challenge_methods_supported"]))
    ok("...and `service` is never offered to a public flow", "service" not in asm["scopes_supported"],
       str(asm["scopes_supported"]))
    # THE HEADER IS THE WHOLE BOOTSTRAP. Without it the 401 is a closed door with no handle.
    h = oauth.challenge_header(ROOT)
    ok("the 401 challenge points at the metadata",
       h.startswith("Bearer ") and "/.well-known/oauth-protected-resource" in h, h)


def test_registration_grants_nothing():
    print("test_registration_grants_nothing")
    reg = oauth.register({"client_name": "Grok on X", "redirect_uris": ["https://grok.com/cb"]})
    ok("a client is issued an id", reg["client_id"].startswith("obc_"), str(reg))
    # A PUBLIC CLIENT, DELIBERATELY. A secret handed to anyone who asks is a secret in name only;
    # PKCE is what actually binds the code to the client that requested it.
    ok("...and no client_secret is invented", "client_secret" not in reg, str(reg))
    ok("...and it holds no rights yet", seats.verify(reg["client_id"]) is None,
       "a client_id must not be usable as a credential")
    for bad, why in (({"redirect_uris": ["http://evil.test/cb"]}, "plain http"),
                     ({"redirect_uris": ["javascript:alert(1)"]}, "a javascript: uri"),
                     ({"redirect_uris": []}, "no redirect at all"),
                     ({}, "an empty body")):
        try:
            oauth.register(bad)
            ok(f"refuses {why}", False, "accepted")
        except ValueError:
            ok(f"refuses {why}", True)
    # LOOPBACK IS ALLOWED and must stay so — it is how a desktop client receives its code, and it
    # cannot be intercepted off the machine.
    lo = oauth.register({"client_name": "A desktop app",
                         "redirect_uris": ["http://127.0.0.1:53211/cb"]})
    ok("...but loopback http is allowed, for desktop clients",
       lo["redirect_uris"] == ["http://127.0.0.1:53211/cb"], str(lo))


def test_the_ways_in_that_must_stay_shut():
    print("test_the_ways_in_that_must_stay_shut")
    reg = oauth.register({"client_name": "Probe", "redirect_uris": ["https://grok.com/cb"]})
    cid, v = reg["client_id"], secrets.token_urlsafe(48)
    good = {"client_id": cid, "redirect_uri": "https://grok.com/cb",
            "code_challenge": _chal(v), "method": "S256"}
    try:
        oauth.check_authorize(**good)
        ok("a well-formed request is allowed through", True)
    except ValueError as e:
        ok("a well-formed request is allowed through", False, str(e))
    # EXACT MATCH, NEVER A PREFIX. Prefix matching on a redirect is the classic open redirect
    # that delivers somebody else's authorization code to an attacker.
    for kw, why in ((("redirect_uri", "https://grok.com/cb/../evil"), "a traversal near-miss"),
                    (("redirect_uri", "https://grok.com/cb.evil.test"), "a suffix near-miss"),
                    (("redirect_uri", "https://evil.test/cb"), "a foreign redirect"),
                    (("method", "plain"), "plain PKCE"),
                    (("code_challenge", "short"), "a too-short challenge"),
                    (("client_id", "obc_nosuchclient"), "an unknown client")):
        args = dict(good)
        args[kw[0]] = kw[1]
        try:
            oauth.check_authorize(**args)
            ok(f"refuses {why}", False, f"accepted {kw[1]!r}")
        except ValueError:
            ok(f"refuses {why}", True)


def test_a_grant_is_a_seat():
    print("test_a_grant_is_a_seat")
    reg = oauth.register({"client_name": "Grok on X", "redirect_uris": ["https://grok.com/cb"]})
    cid, v = reg["client_id"], secrets.token_urlsafe(48)
    code = oauth.issue_code(client_id=cid, redirect_uri="https://grok.com/cb",
                            code_challenge=_chal(v), role="act", label="Grok on X", user_id="u1")
    tok = oauth.exchange(code=code, client_id=cid, redirect_uri="https://grok.com/cb", verifier=v)
    ok("the code becomes a bearer token", tok["token_type"] == "Bearer", str(tok)[:60])
    # THE WHOLE DESIGN IN ONE ASSERTION: the token IS a seat, so nothing downstream had to change.
    seat = seats.verify(tok["access_token"])
    ok("...and the token IS a seat", seat is not None, "a second access system would have to be audited separately")
    ok("...with the role the owner approved", seat and seat["role"] == "act", str(seat))
    ok("...labelled so the audit trail reads like a name", seat and seat["label"] == "Grok on X")
    ok("...and the scope reported back matches", tok["scope"] == "act", str(tok))
    # ONE SCREEN REVOKES BOTH, because there is only one row.
    seats.revoke(seat["id"])
    ok("revoking on the coworkers screen kills the grant",
       seats.verify(tok["access_token"]) is None)


def test_a_code_is_worth_using_once():
    print("test_a_code_is_worth_using_once")
    reg = oauth.register({"client_name": "Probe", "redirect_uris": ["https://grok.com/cb"]})
    cid, v = reg["client_id"], secrets.token_urlsafe(48)

    def fresh(role="read"):
        return oauth.issue_code(client_id=cid, redirect_uri="https://grok.com/cb",
                                code_challenge=_chal(v), role=role, label="Probe", user_id="u1")

    code = fresh()
    oauth.exchange(code=code, client_id=cid, redirect_uri="https://grok.com/cb", verifier=v)
    for args, why in (
        ({"code": code, "client_id": cid, "redirect_uri": "https://grok.com/cb", "verifier": v},
         "a replayed code"),
        ({"code": fresh(), "client_id": cid, "redirect_uri": "https://grok.com/cb",
          "verifier": "not-the-verifier"}, "a wrong verifier"),
        ({"code": fresh(), "client_id": "obc_someoneelse",
          "redirect_uri": "https://grok.com/cb", "verifier": v}, "another client's redemption"),
        ({"code": fresh(), "client_id": cid, "redirect_uri": "https://evil.test/cb",
          "verifier": v}, "a swapped redirect at redemption"),
        ({"code": "not-a-code", "client_id": cid, "redirect_uri": "https://grok.com/cb",
          "verifier": v}, "an invented code"),
    ):
        try:
            oauth.exchange(**args)
            ok(f"refuses {why}", False, "accepted")
        except ValueError as e:
            # THE SAME WORD EVERY TIME. Distinguishing "no such code" from "wrong verifier" is an
            # oracle for probing, and a client can act on neither.
            ok(f"refuses {why}", str(e) == "invalid_grant", f"leaked which check failed: {e}")
    # `service` is ours. A public flow must never be able to ask for it.
    try:
        oauth.issue_code(client_id=cid, redirect_uri="https://grok.com/cb",
                         code_challenge=_chal(v), role="service", label="P", user_id="u1")
        ok("refuses a `service` grant", False, "accepted")
    except ValueError:
        ok("refuses a `service` grant", True)


def test_the_bootstrap_is_reachable_without_a_credential():
    print("test_the_bootstrap_is_reachable_without_a_credential")
    # A DOOR WHOSE KEY IS BEHIND THE DOOR. `_needs_a_seat` must cover /mcp and /api/ and NOT the
    # discovery paths, or a client can never learn how to authenticate.
    from core.dispatch import _needs_a_seat
    for path, want in (("/mcp", True), ("/api/v1/mcp", True),
                       ("/.well-known/oauth-protected-resource", False),
                       ("/.well-known/oauth-authorization-server", False),
                       ("/oauth/register", False), ("/oauth/token", False),
                       ("/oauth/authorize", False)):
        ok(f"{'gated' if want else 'open '}: {path}", _needs_a_seat(path) is want,
           "the bootstrap must be readable by something holding nothing")


if __name__ == "__main__":
    test_a_client_can_find_the_door()
    test_registration_grants_nothing()
    test_the_ways_in_that_must_stay_shut()
    test_a_grant_is_a_seat()
    test_a_code_is_worth_using_once()
    test_the_bootstrap_is_reachable_without_a_credential()

    print("\n— and this file cannot silently fall out of CI —")
    import pathlib
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("test_an_assistant_can_let_itself_in is in the workflow's suite list",
           "test_an_assistant_can_let_itself_in" in _wf.read_text())
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
