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


def test_the_consent_screen_starts_at_draft():
    print("test_the_consent_screen_starts_at_draft")
    reg = oauth.register({"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"]})
    v = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    for asked in ("read", "act", ""):
        q = {"client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code",
             "code_challenge": chal, "code_challenge_method": "S256", "state": "s"}
        if asked:
            q["scope"] = asked
        page = _owner().get("/oauth/authorize?" + urlencode(q)).get_data(as_text=True)
        ok(f"asked for {asked or 'nothing'}: read-and-draft is preselected",
           'value="act" checked' in page, page[:200])
        ok("...and read only is still offered", 'value="read"' in page and 'value="read" checked' not in page)


def test_one_tap_moves_a_connection_in_place():
    print("test_one_tap_moves_a_connection_in_place")
    sid, cred = seats.mint("Claude", "read")
    page = _owner().get("/settings/agent").get_data(as_text=True)
    ok("a read-only connection offers the tap", "Let it draft replies" in page, page[-600:])

    r = _owner().post("/settings/agent", data={"do": "role", "seat": sid, "role": "act"})
    ok("the tap answers with the screen again", r.status_code == 303, str(r.status_code))
    seat = seats.verify(cred)
    ok("the SAME credential now holds act: nothing to reconnect", seat and seat["role"] == "act", str(seat))
    page = _owner().get("/settings/agent").get_data(as_text=True)
    ok("...and the screen offers the way back", "Make it read only" in page)

    _owner().post("/settings/agent", data={"do": "role", "seat": sid, "role": "read"})
    ok("one tap back down, same credential", (seats.verify(cred) or {}).get("role") == "read")


def test_what_it_never_touches():
    print("test_what_it_never_touches")
    run_id, _ = seats.mint("a shift", "act", capabilities=["read:inbox"])
    _owner().post("/settings/agent", data={"do": "role", "seat": run_id, "role": "read"})
    ok("a run seat keeps its role and its list", _role(run_id) == "act")

    svc, _ = seats.mint("the box's own", "service")
    _owner().post("/settings/agent", data={"do": "role", "seat": svc, "role": "act"})
    ok("a service seat is never moved", _role(svc) == "service")

    sid, _ = seats.mint("old", "read")
    _owner().post("/settings/agent", data={"do": "role", "seat": sid, "role": "service"})
    ok("nothing can be raised to service", _role(sid) == "read")
    try:
        seats.set_role(sid, "service")
        ok("...not even by calling it directly", False, "accepted")
    except ValueError:
        ok("...not even by calling it directly", True)

    seats.revoke(sid)
    ok("a revoked connection stays revoked and unchanged", seats.set_role(sid, "act") is False
       and _role(sid) == "read")

    sid2, _ = seats.mint("someone's", "read")
    anon = app.test_client().post("/settings/agent", data={"do": "role", "seat": sid2, "role": "act"})
    ok("somebody who is not signed in changes nothing", _role(sid2) == "read", str(anon.status_code))


if __name__ == "__main__":
    test_the_consent_screen_starts_at_draft()
    test_one_tap_moves_a_connection_in_place()
    test_what_it_never_touches()
    import pathlib
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("this file is in the workflow's suite list", "test_your_own_ai_can_draft" in _wf.read_text())
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
