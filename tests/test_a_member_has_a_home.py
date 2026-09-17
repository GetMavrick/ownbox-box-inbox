"""A person with their own login can open the box's home — and nobody else gained anything.

Owner's $499 card sells "each with their own login". OSDev4 measured, on #1341, that a signed-in
MEMBER was bounced off `/dashboard` to `/dash/login` while the owner got 200, and reported the
measurement without a diagnosis. He was right, and the cause was mine:

  #1331 moved the front door onto `/dashboard`. That route reuses `review._admit()`, which ends
  `if not viewer_is_owner: redirect("/dash/login")` — OWNER-ONLY, because THE REVIEW publishes the
  meters, the monthly ceiling and the AT-CAP line. `/dashboard` publishes none of that. Reusing
  the function was right; inheriting its last line was not.

  Measured before the fix: member `/` -> `/dashboard` -> `/dash/login`, while `/dash/home` (the
  old head of `_LANDINGS`) returned 200 for the same member. Not an infinite loop — the login page
  renders — which is worse in one way: a person holding a valid session is shown a password form
  and concludes their password is broken.

WHAT THIS SUITE IS REALLY FOR is the other half. Widening an auth gate is how a leak ships, and
`/app/review` leaked exactly this way once already (2026-09-09: 200 with the meters to a stranger
on a demo host). So every assertion about what a member CAN now do is paired with one about what
nobody gained.

Run: python tests/test_a_member_has_a_home.py
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="member-home-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ["DASH_TOKEN"] = "the-owners-own-password"

from core import state                                      # noqa: E402

state.init_db()

from core import dash                                       # noqa: E402
from core.dispatch import app                               # noqa: E402

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _member():
    who = state.add_user("member@testco.example", name="A Member", role="member")
    c = app.test_client()
    c.set_cookie("aios_session", dash.new_session(who["id"]))
    return c


def _owner():
    c = app.test_client()
    c.post("/dash/login", data={"token": "the-owners-own-password"})
    return c


def _stranger():
    return app.test_client()


def test_a_member_lands_somewhere_they_can_actually_open():
    print("test_a_member_lands_somewhere_they_can_actually_open")
    # THE WHOLE DEFECT, END TO END, and asserted by FOLLOWING the redirect rather than reading it.
    # `landing()` returning a path proves nothing if that path turns the person around.
    c = _member()
    r = c.get("/")
    dest = r.headers.get("Location", "")
    ok("the front door redirects somewhere", r.status_code in (301, 302, 303), str(r.status_code))
    land = c.get(dest)
    ok(f"...and a member can open it ({dest} -> {land.status_code})", land.status_code == 200,
       f"{land.status_code} {land.headers.get('Location','')} — a valid session shown a login form")
    ok("...so the box does not send them back to the password box",
       not str(land.headers.get("Location", "")).endswith("/dash/login"))


def test_an_invited_teammate_lands_on_a_page_that_opens():
    """THE OTHER DOOR INTO THE SAME BUG, measured on main by OSDev1 and asked for by name:

        *"an invited member opens the /join link, picks a password, and gets 302 /dashboard ->
        302 /dash/login -> the login form. That is the first screen a new teammate ever sees."*

    `join_submit` ends `redirect(landing())` with the session cookie already set, so it walks into
    the bounce the same way signing in does — and this door is worse, because the person has never
    seen the box work and has nothing to compare it to. A test that only covered `/` would have
    gone green while the first impression stayed broken.

    THROUGH THE REAL DOORS, not a shortcut. The invite is created by POSTing the owner's own form
    and the token is read back out of the page he is shown, so this exercises `people_invite`,
    `_invite_notice`, `invite_user` and `redeem_invite` rather than a hand-minted row.
    """
    print("test_an_invited_teammate_lands_on_a_page_that_opens")
    import re as _re
    owner = _owner()
    r = owner.post("/dash/people/invite", data={"email": "newteammate@testco.example"})
    ok("the owner can invite", r.status_code in (200, 201), str(r.status_code))
    m = _re.search(r"/join\?t=([A-Za-z0-9_\-%.]+)", r.get_data(as_text=True))
    ok("...and is shown a link with a token", bool(m), r.get_data(as_text=True)[:200])
    if not m:
        return
    from urllib.parse import unquote
    token = unquote(m.group(1))

    joiner = app.test_client()
    ok("the join page opens for a real token",
       joiner.get(f"/join?t={m.group(1)}").status_code == 200)
    r = joiner.post("/join", data={"t": token, "password": "a-long-enough-password"})
    dest = r.headers.get("Location", "")
    ok("joining redirects somewhere", r.status_code in (301, 302, 303), str(r.status_code))
    first = joiner.get(dest)
    ok(f"...and the FIRST SCREEN a teammate sees opens ({dest} -> {first.status_code})",
       first.status_code == 200,
       f"{first.status_code} {first.headers.get('Location','')} — they set a password and were "
       f"asked for one again")
    ok("...rather than the login form they just came from",
       not str(first.headers.get("Location", "")).endswith("/dash/login"))


def test_the_owner_did_not_lose_anything():
    print("test_the_owner_did_not_lose_anything")
    ok("the owner still opens the dashboard", _owner().get("/dashboard").status_code == 200)
    ok("...and still opens the review", _owner().get("/app/review").status_code in (200, 404),
       str(_owner().get("/app/review").status_code))


# ── the half that matters more: nobody gained anything ──────────────────────────────────────────

def test_a_stranger_still_gets_nothing():
    print("test_a_stranger_still_gets_nothing")
    # NO SESSION, NO TOKEN. The 2026-09-09 leak was exactly this request returning 200.
    c = _stranger()
    r = c.get("/dashboard")
    ok("the dashboard refuses a stranger", r.status_code != 200, str(r.status_code))
    ok("...by sending them to sign in", str(r.headers.get("Location", "")).endswith("/dash/login"),
       r.headers.get("Location", ""))
    body = r.get_data(as_text=True)
    for word in ("conversations mirrored", "Waiting on you", "emails sent"):
        ok(f"...and the refusal carries no content ({word!r})", word not in body)


def test_the_money_page_is_still_owner_only():
    print("test_the_money_page_is_still_owner_only")
    # THE REASON THE OLD RULE EXISTED, kept exactly where it was. If this ever goes 200 for a
    # member, the flag has been applied to the wrong page and the meters are public again.
    r = _member().get("/app/review")
    ok("a member does NOT open the review", r.status_code != 200, str(r.status_code))
    ok("...and is sent to sign in", str(r.headers.get("Location", "")).endswith("/dash/login"),
       r.headers.get("Location", ""))


def test_a_session_belonging_to_nobody_is_still_nobody():
    print("test_a_session_belonging_to_nobody_is_still_nobody")
    # "SIGNED IN" MUST NOT MEAN "CARRIES A COOKIE THAT PARSES". `session_user` refuses a session
    # with a NULL user, and the new branch leans on that — so a forged or orphaned cookie must buy
    # nothing. Asserted with a value that was never minted.
    c = app.test_client()
    c.set_cookie("aios_session", "not-a-session-anybody-issued")
    r = c.get("/dashboard")
    ok("an unissued cookie opens nothing", r.status_code != 200, str(r.status_code))


def test_the_dashboard_still_carries_no_money():
    print("test_the_dashboard_still_carries_no_money")
    # THE PREMISE OF THE WHOLE CHANGE, asserted rather than remembered. `owner_only=False` is only
    # defensible while this page publishes no spend; the day a meter is added here, this goes red
    # and whoever added it has to decide the gate deliberately instead of inheriting this one.
    body = _owner().get("/dashboard").get_data(as_text=True)
    import html as _h
    import re as _re
    txt = _h.unescape(_re.sub(r"<[^>]+>", " ", _re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", body)))
    low = " ".join(txt.split()).lower()
    for word in ("$", "spend", "ceiling", "at cap", "usd"):
        ok(f"no {word!r} on the box's home", word not in low, low[:160])


def test_the_host_rules_are_not_duplicated():
    print("test_the_host_rules_are_not_duplicated")
    # ONE COPY OF "WHICH ADDRESSES ANSWER". The fix is a flag on the LAST line of `_admit`, not a
    # second gate in home.py — a second copy is how /app/review came to be guarded by a rule
    # written for a sales page in the first place.
    src = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "home.py").read_text()
    ok("the dashboard still calls the shared gate", "_review._admit(owner_only=False)" in src)
    ok("...and re-derives none of the host rules",
       "demo_zone" not in src and "label_sources" not in src and "space_labels" not in src)


def test_the_suite_is_named_in_ci():
    print("test_the_suite_is_named_in_ci")
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_a_member_has_a_home \\" in wf)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print()
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
