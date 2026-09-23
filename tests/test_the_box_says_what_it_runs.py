"""A buyer can see what release this box runs, and that it is keeping itself current.

We sell a box that keeps getting better and a buyer could not see it happening: three sections on
the dashboard, and not one said what release the machine ran or that anything was arriving. A
product that improves invisibly is, to the person paying for it, a product that does not improve.

WHAT THIS SUITE REFUSES TO ACCEPT, and every one of them is a lie a status page can tell:

  1. "OUT OF DATE" TO A BOX THAT IS AN HOUR OLD. Never-checked is its own state. Telling somebody
     who did nothing wrong that they are behind is how a status page loses its reader.
  2. A GREEN TICK BUILT ON A STOPPED CLOCK. The timer runs twice a day with spread, so the worst
     honest gap is ~15 hours. Past 30 the box is not checking, and repeating that check's verdict
     as fact is worse than saying nothing.
  3. "UP TO DATE" WHEN EVERY RELEASE WAS REFUSED. That is the one state that is genuinely
     somebody's problem — a newer release exists and could not be trusted — and it must not
     render as health.
  4. A PAGE THAT 500s WHEN THE UPDATE PATH IS BROKEN. This screen exists to answer in exactly the
     state where everything else has failed.
  5. A CHOSEN RELEASE READ AS AN INSTALLED ONE. `selected` means a tag was picked; whether it
     LANDED is a different question, answered by comparing it with the installed marker.

Run: python tests/test_the_box_says_what_it_runs.py
"""
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
_T = tempfile.mkdtemp()
os.environ["AIOS_DB_PATH"] = _T + "/updates.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# The log and the repo this suite owns outright — never the machine it runs on, which on a dev box
# would make the result depend on whose laptop ran it.
_LOG = pathlib.Path(_T) / "updates.jsonl"
os.environ["AIOS_UPDATES_LOG"] = str(_LOG)
os.environ["AIOS_REPO"] = _T + "/not-a-repo"
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import state  # noqa: E402

state.init_db()

from core import box_updates as U  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def when(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def log(*entries):
    _LOG.write_text("".join(json.dumps(e) + "\n" for e in entries))


def state_with(release, *entries):
    log(*entries)
    U._installed = lambda: release          # the marker/HEAD read, stubbed: this tree is not a box
    return U.state()


_real_installed = U._installed


# ── a box nobody has updated yet ──────────────────────────────────────────────────────────
print("\n— a box that was set up an hour ago —")

_LOG.unlink(missing_ok=True)
U._installed = lambda: "release/2026.09.23.1"
st = U.state()
ok("NEVER CHECKED IS ITS OWN STATE, not 'out of date'", st["state"] == "never_checked", str(st))
ok("...and `ok` is withheld rather than guessed either way", st["ok"] is None, str(st.get("ok")))
ok("...and it says when the first check will happen", "twice a day" in st["said"], st["said"])
ok("the release is still shown — that fact does not depend on the log",
   st["release"] == "release/2026.09.23.1", str(st.get("release")))


# ── the ordinary, healthy box ─────────────────────────────────────────────────────────────
print("\n— a box that checked this morning and is current —")

st = state_with("release/2026.09.23.1",
                {"at": when(6), "status": "up_to_date", "current": "release/2026.09.23.1"})
ok("up to date reads as up to date", st["state"] == "current" and st["ok"] is True, str(st))
ok("...and the check's age is carried for the screen", 5 * 3600 < st["checked_s_ago"] < 7 * 3600,
   str(st.get("checked_s_ago")))

st = state_with("release/2026.09.23.2",
                {"at": when(2), "status": "selected", "selected": "release/2026.09.23.2"})
ok("a SELECTED release that matches what is installed means the update LANDED",
   st["state"] == "current" and st["ok"] is True, str(st))

st = state_with("release/2026.09.23.1",
                {"at": when(0.2), "status": "selected", "selected": "release/2026.09.23.2"})
ok("a SELECTED release that does NOT match is still arriving, not installed",
   st["state"] == "updating" and st["ok"] is None, str(st))
ok("...and it names the release and says there is nothing to do",
   "2026.09.23.2" in st["said"] and "Nothing for you to do" in st["said"], st["said"])


# ── the stopped clock ─────────────────────────────────────────────────────────────────────
print("\n— a box whose timer has stopped —")

st = state_with("release/2026.09.23.1",
                {"at": when(40), "status": "up_to_date"})
ok("A GREEN TICK IS WITHHELD when the last check is two windows old",
   st["stale"] is True and st["ok"] is None, str(st))
ok("...and the page says the verdict may no longer be true, and why",
   "may no longer be true" in st["said"] and "twice-daily check" in st["said"], st["said"])
st = state_with("release/2026.09.23.1", {"at": when(14), "status": "up_to_date"})
ok("...while a check inside the honest window (timer + spread) is still trusted",
   st["stale"] is False and st["ok"] is True, str(st))


# ── the two states that are somebody's problem ────────────────────────────────────────────
print("\n— what must never render as health —")

st = state_with("release/2026.09.23.1",
                {"at": when(1), "status": "all_refused",
                 "refused": [{"tag": "release/2026.09.24.1", "reason": "bad_signature"}]})
ok("EVERY RELEASE REFUSED is a failure, not 'up to date'",
   st["state"] == "refused" and st["ok"] is False, str(st))
ok("...and it says the box did the SAFE thing, so nobody panics",
   "safe outcome" in st["said"], st["said"])
ok("...and it does not leak the verification detail to a buyer",
   "bad_signature" not in st["said"], st["said"])

st = state_with("release/2026.09.23.1", {"at": when(1), "status": "cannot_run",
                                         "detail": "fetch failed: Permission denied (publickey)"})
ok("a check that could not run is a failure", st["state"] == "cannot_check" and st["ok"] is False)
ok("...and it reassures that nothing changed", "nothing has changed" in st["said"], st["said"])
ok("...and no key or path detail reaches the buyer",
   "publickey" not in st["said"], st["said"])

st = state_with("release/2026.09.23.1",
                {"at": when(3), "status": "rolled_back", "from": "release/2026.09.23.9",
                 "to": "release/2026.09.23.1"})
ok("an update that was undone says so", st["state"] == "rolled_back" and st["ok"] is False, str(st))

# A WORD THIS VERSION DOES NOT KNOW must not become a tick. An older page reading a newer box's
# log is the ordinary case during a rollout, not an exotic one.
st = state_with("release/2026.09.23.1", {"at": when(1), "status": "something_invented_later"})
ok("an unrecognised status is UNKNOWN, never healthy", st["state"] == "unknown" and st["ok"] is None)


# ── it answers when everything around it is broken ────────────────────────────────────────
print("\n— the state this page exists for —")

_LOG.write_text("{not json at all\n")
U._installed = lambda: "release/2026.09.23.1"
st = U.state()
ok("a corrupt log does not break the page", st["state"] == "never_checked", str(st))
_LOG.write_text(json.dumps({"at": when(1), "status": "up_to_date"}) + "\n{truncated\n")
st = U.state()
ok("...and a truncated FINAL line still finds the newest good entry",
   st["state"] == "current", str(st))

U._installed = lambda: None
st = U.state()
ok("a box that cannot name its release says so and withholds the tick",
   st["release"] is None and st["ok"] is None, str(st))
ok("...and says it first, because it changes how everything else reads",
   st["said"].startswith("This box cannot tell"), st["said"])
U._installed = _real_installed


# ── the screen, over HTTP ─────────────────────────────────────────────────────────────────
print("\n— the screen a buyer opens —")

log({"at": when(5), "status": "up_to_date"})
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(
    state.add_user("sam@acme.co", name="Sam", role="member")["id"]))

r = owner.get("/settings/updates")
page = r.get_data(as_text=True)
ok("the page answers the owner", r.status_code == 200, str(r.status_code))
ok("...and carries the one sentence about how updates arrive — WITH MANAGED (owner, 2026-09-23)",
   "Updates come with Ownbox Managed, and they arrive on their own" in page)
ok("...and says the settings and data are never touched", "never touched" in page)
ok("...and tells them nothing needs pressing", "needs pressing" in page)

# NOT OWNER-ONLY, deliberately: it publishes a version string and nothing else, and a door that
# refuses you reads as a broken box.
ok("a member may see it too", member.get("/settings/updates").status_code == 200)
ok("...and is offered it from System Settings",
   "/settings/updates" in member.get("/settings").get_data(as_text=True))
ok("the owner is offered it as well",
   "/settings/updates" in owner.get("/settings").get_data(as_text=True))

anon = app.test_client()
r = anon.get("/settings/updates")
ok("a stranger is sent to the login", r.status_code in (302, 303)
   and "/dash/login" in (r.headers.get("Location") or ""), str(r.status_code))

# THE SCREEN MUST ANSWER WHEN THE READER ITSELF THROWS. This page's whole job is to be readable on
# a box where the update path is broken.
_boom = U.state
U.state = lambda: (_ for _ in ()).throw(RuntimeError("no /var/lib/aios"))
r = owner.get("/settings/updates")
ok("a reader that raises still renders a page, not a 500", r.status_code == 200, str(r.status_code))
ok("...and it says nothing has changed rather than going silent",
   "nothing has changed" in r.get_data(as_text=True))
U.state = _boom

print("\nALL UPDATE-SURFACE CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
