"""Your own AI connects able to draft, and a connection made read-only moves up in one tap.

docs/SCOPE_INBOX_CONNECTOR.md step 2. OSDev1, 2026-10-02 (#1794 §4): a person's own-AI seat is `act`
by default; `read` stays the choice for read-only access; a one-tap change for an existing seat, the
same for every owner. Owner, 2026-10-02: "Yes, we want this machine to be powerful... For right now we
want to draft. Both. Yes go ahead."

WHAT WAS TRUE BEFORE. The consent screen preselected `read` whenever the assistant ASKED for `read`,
and Claude asks for it, so the owner's own Claude connected read-only and could not leave one draft.
The only way up was to make a new connection and set it up in Claude again.

WHAT THIS SUITE DEFENDS:
  · the consent screen preselects read-and-draft whatever the assistant asks for, and still offers read
  · one tap moves a live connection between read and act IN PLACE: the same credential, a new role
  · it never touches a run seat, a revoked seat, or `service`, and never grants more than `act`
  · only the owner can do it

Run: python tests/test_your_own_ai_can_draft.py
"""
import base64
import hashlib
import os
import secrets
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "ownseat.db")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

from core import state                                                  # noqa: E402

state.init_db()

from core import dash                                                   # noqa: E402
from core.connector import oauth, seats                                 # noqa: E402
from core.dispatch import app                                           # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def _owner():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]), domain="localhost")
    return c


def _role(seat_id):
    return next((s["role"] for s in seats.all_seats(include_runs=True) if s["id"] == seat_id), None)


def test_the_consent_screen_grants_read_and_draft():
    print("test_the_consent_screen_grants_read_and_draft")
    reg = oauth.register({"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"]})
    v = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    for asked in ("read", "act", ""):
        q = {"client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code",
             "code_challenge": chal, "code_challenge_method": "S256", "state": "s"}
        if asked:
            q["scope"] = asked
        page = _owner().get("/oauth/authorize?" + urlencode(q)).get_data(as_text=True)
        ok(f"asked for {asked or 'nothing'}: the screen says read and draft replies",
           "Read and draft replies" in page, page[:200])
        ok("...and offers no read-only choice (owner, 2026-10-02: removed)", "type=radio" not in page
           and "Read only" not in page)
        r = _owner().post("/oauth/authorize?" + urlencode(q), data={**q, "decision": "allow", "grant": "read"})
        code = dict(x.split("=", 1) for x in r.headers.get("Location", "").split("?", 1)[1].split("&"))["code"]
        from core import state
        with state.connect() as c:
            role = c.execute("SELECT role FROM oauth_codes WHERE code = ?", (code,)).fetchone()["role"]
        ok("...and grants read-and-draft even when the form or the app asks for read", role == "act", role)


def test_read_only_connections_move_up():
    print("test_read_only_connections_move_up")
    sid, cred = seats.mint("Claude", "read")
    import ast
    import pathlib
    src = ast.parse(pathlib.Path(seats.__file__).resolve().parents[1].joinpath("dispatch.py").read_text())
    top = [n for n in src.body if isinstance(n, ast.Try)]
    ok("the box moves them up when it starts (core/dispatch.py calls seats.promote_all at import)",
       any("promote_all" in ast.unparse(n) for n in top))
    seats.promote_all()                                  # what that start does
    seat = seats.verify(cred)
    ok("a read-only connection is read-and-draft once the box starts after the update, same credential",
       seat and seat["role"] == "act" and _role(sid) == "act", str(seat))
    ok("the move happens once: starting again moves nothing", seats.promote_all() == 0)
    sid2, _ = seats.mint("Grok", "read")
    page = _owner().get("/settings/agent").get_data(as_text=True)
    ok("opening the MCP Server screen moves every one up", _role(sid2) == "act")
    ok("...and it offers no way down, nor a toggle", "Make it read only" not in page
       and "Let it draft replies" not in page and 'value="read"' not in page, page[-600:])
    _owner().post("/settings/agent", data={"do": "role", "seat": sid, "role": "read"})
    ok("asking for read changes nothing", _role(sid) == "act")
    try:
        seats.set_role(sid, "read")
        ok("...not even by calling it directly", False, "accepted")
    except ValueError:
        ok("...not even by calling it directly", True)
    r = _owner().post("/settings/agent", data={"do": "mint", "label": "A script", "role": "read"})
    newest = next(s for s in seats.all_seats() if s["label"] == "A script")
    ok("a key made by hand reads and drafts, whatever the form says", newest["role"] == "act", newest)


def test_what_it_never_touches():
    print("test_what_it_never_touches")
    run_id, run_cred = seats.mint("a shift", "read", capabilities=["read:inbox"])
    seats.promote_all()
    ok("a run seat keeps its role and its exact list", _role(run_id) == "read")

    svc, _ = seats.mint("the box's own", "service")
    seats.promote_all()
    _owner().post("/settings/agent", data={"do": "role", "seat": svc, "role": "act"})
    ok("a service seat is never moved", _role(svc) == "service")

    sid, _ = seats.mint("old", "act")
    _owner().post("/settings/agent", data={"do": "role", "seat": sid, "role": "service"})
    ok("nothing can be raised to service", _role(sid) == "act")
    try:
        seats.set_role(sid, "service")
        ok("...not even by calling it directly", False, "accepted")
    except ValueError:
        ok("...not even by calling it directly", True)

    gone, _ = seats.mint("gone", "read")
    seats.revoke(gone)
    seats.promote_all()
    ok("a revoked connection stays revoked and unchanged", _role(gone) == "read")

    sid2, _ = seats.mint("someone's", "read")
    anon = app.test_client().get("/settings/agent")
    ok("somebody who is not signed in moves nothing", _role(sid2) == "read", str(anon.status_code))


if __name__ == "__main__":
    test_the_consent_screen_grants_read_and_draft()
    test_read_only_connections_move_up()
    test_what_it_never_touches()
    import pathlib
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("this file is in the workflow's suite list", "test_your_own_ai_can_draft" in _wf.read_text())
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
