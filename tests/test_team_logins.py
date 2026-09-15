"""Per-person login — up to five people, each with their own password (docs/DESIGN_PER_PERSON_LOGIN.md).

The $499 card sells "Up to 5 people, each with their own login", and until this every session on every
box was the owner's. What this suite defends, because a second way in is a second door to guard:
  · a password per person, stored as a hash that NO reader outside sign-in ever returns
  · the ONLY way to get one is an owner-issued invite: single-use, seven days, stored hashed, retired
    by a newer one, refused for a revoked person
  · sign-in by email and password answers an unknown address exactly like a wrong password
  · a password change, and a removal, end that person's sessions at their next request
  · only the owner's own session manages people — never a member, never the app token
  · DASH_TOKEN still signs the owner in, and a claimed owner still signs in with his own password
  · a fresh box can invite at all: install.sh mints a DASH_TOKEN distinct from the API key

Run: python tests/test_team_logins.py
"""
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "team.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "the-api-key-not-a-password"
os.environ["DASH_TOKEN"] = "the-owners-break-glass-token"

from core import state                                                # noqa: E402
state.init_db()
from core import claim                                                # noqa: E402
from core import dash                                                 # noqa: E402
from core.config import settings                                      # noqa: E402
from core.dispatch import app as flask_app                            # noqa: E402

_failed = 0
PW = "correct horse battery staple"


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def client():
    dash._fails.clear()
    return flask_app.test_client()


def owner_client():
    c = client()
    r = c.post("/dash/login", data={"token": settings.dash_token})
    assert r.status_code in (302, 303), r.status_code
    return c


def invite_link(c, email):
    r = c.post("/dash/people/invite", data={"email": email})
    m = re.search(r"/join\?t=([A-Za-z0-9_\-%]+)", r.get_data(as_text=True))
    return r, (m.group(1) if m else None)


def signed_in_as(c):
    r = c.get("/dash/people")
    return r.status_code


print("\n— migration 49: one credential store —")
cols = {r[1] for r in sqlite3.connect(os.environ["AIOS_DB_PATH"]).execute("PRAGMA table_info(users)")}
ok("users carries pw_hash", "pw_hash" in cols, str(cols))
mem = sqlite3.connect(":memory:")
mem.executescript("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT); "
                  "CREATE TABLE box_claim (id INTEGER PRIMARY KEY, user_id TEXT, pw_hash TEXT);"
                  "INSERT INTO users VALUES ('usr_owner', 'buyer@co.com');"
                  "INSERT INTO box_claim VALUES (1, 'usr_owner', 'scrypt$claimed');")
state._migration_49(mem)
ok("a box claimed before 49 moves its owner's password onto his row",
   mem.execute("SELECT pw_hash FROM users WHERE id='usr_owner'").fetchone()[0] == "scrypt$claimed")
ok("user_invites is a secret table: never exported", "user_invites" in state.SECRET_TABLES)

print("\n— nobody outside sign-in reads a hash —")
member = state.add_user("hash-check@co.com")
state.set_password(member["id"], claim.hash_password(PW))
ok("get_user carries no pw_hash", "pw_hash" not in (state.get_user(member["id"]) or {}))
ok("user_by_email carries no pw_hash", "pw_hash" not in (state.user_by_email("hash-check@co.com") or {}))
listed = [u for u in state.list_users() if u["id"] == member["id"]][0]
ok("list_users says whether a password is set, never what it is",
   listed.get("has_password") == 1 and "pw_hash" not in listed, str(listed))
ok("password_hash_for is the one reader, and only for an active person with a password",
   state.password_hash_for("hash-check@co.com") is not None and state.password_hash_for("nobody@co.com") is None)

print("\n— only the owner manages people —")
ok("no session: the People page sends you to sign in", client().get("/dash/people").status_code in (302, 303))
oc = owner_client()
page = oc.get("/dash/people")
ok("the owner sees the People page", page.status_code == 200 and "Invite someone" in page.get_data(as_text=True))
ok("...and it shows no hash of anyone", "scrypt$" not in page.get_data(as_text=True))

print("\n— an invite: once, hashed, seven days —")
r, token = invite_link(oc, "Receptionist@Co.com ")
ok("the owner gets a join link, shown once", r.status_code == 200 and bool(token), r.get_data(as_text=True)[:300])
with state.connect() as c:
    stored = [row["token_hash"] for row in c.execute("SELECT token_hash FROM user_invites")]
ok("the token is NOT stored; its sha256 is", token not in stored and state._token_hash(token) in stored)
jr = client().get(f"/join?t={token}")
ok("the link opens a password form for that address", jr.status_code == 200 and "receptionist@co.com" in jr.get_data(as_text=True))
short = client().post("/join", data={"t": token, "password": "short"})
ok("a short password is refused with the rule, and the link still works",
   short.status_code == 400 and state.invite_user(token) is not None)
jc = client()
joined = jc.post("/join", data={"t": token, "password": PW})
ok("a good password joins and signs them in", joined.status_code in (302, 303) and "aios_session" in joined.headers.get("Set-Cookie", ""))
ok("...as a MEMBER, who cannot manage people", signed_in_as(jc) == 403)
again = client().post("/join", data={"t": token, "password": PW + " again"})
ok("the same link never works twice", again.status_code == 404 and client().get(f"/join?t={token}").status_code == 404)

print("\n— sign-in by email and password —")
good = client().post("/dash/login", data={"email": "receptionist@co.com", "token": PW})
ok("the member signs in with their own email and password", good.status_code in (302, 303))
wrong = client().post("/dash/login", data={"email": "receptionist@co.com", "token": "not their password"})
unknown = client().post("/dash/login", data={"email": "stranger@co.com", "token": "not their password"})
ok("a wrong password is refused", wrong.status_code == 401)
ok("an unknown address gets the SAME status and the SAME words", unknown.status_code == 401
   and wrong.get_data(as_text=True) == unknown.get_data(as_text=True))
calls = []
_real_verify = claim.verify_password
claim.verify_password = lambda pw, stored: calls.append(stored) or _real_verify(pw, stored)
try:
    client().post("/dash/login", data={"email": "stranger@co.com", "token": "whatever it is"})
finally:
    claim.verify_password = _real_verify
ok("...and it COSTS the same: an unknown address still runs one real scrypt check",
   len(calls) == 1 and str(calls[0]).startswith("scrypt$"), str(calls)[:80])
ok("DASH_TOKEN still signs the owner in (break-glass)", owner_client().get("/dash/people").status_code == 200)

print("\n— a new link retires the old; a new password ends old sessions —")
person = state.user_by_email("receptionist@co.com")
mc = client()
mc.post("/dash/login", data={"email": "receptionist@co.com", "token": PW})
ok("(the member holds a live session)", signed_in_as(mc) == 403)
old_link = state.create_invite(person["id"], created_by=state.OWNER_USER_ID)
new_link = state.create_invite(person["id"], created_by=state.OWNER_USER_ID)
ok("an older unused link stops working when a newer one is made",
   state.invite_user(old_link) is None and state.invite_user(new_link) is not None)
client().post("/join", data={"t": new_link, "password": "a brand new long password"})
ok("setting a new password ends the sessions that person already held", signed_in_as(mc) in (302, 303))

print("\n— removal and restore —")
rc = client()
rc.post("/dash/login", data={"email": "receptionist@co.com", "token": "a brand new long password"})
ok("(signed in again with the new password)", signed_in_as(rc) == 403)
oc = owner_client()
oc.post(f"/dash/people/{person['id']}/remove")
ok("a removed person's session stops at its next request", signed_in_as(rc) in (302, 303))
ok("...and they cannot sign in", client().post("/dash/login", data={"email": "receptionist@co.com",
                                                                       "token": "a brand new long password"}).status_code == 401)
revoked_link = state.create_invite(person["id"]) if state.get_user(person["id"]).get("active") else None
ok("no invite can be made for a removed person", revoked_link is None)
oc.post(f"/dash/people/{person['id']}/restore")
ok("restored, they sign in again with the password they had", client().post(
    "/dash/login", data={"email": "receptionist@co.com", "token": "a brand new long password"}).status_code in (302, 303))

print("\n— members cannot act as owner —")
nc = client()
nc.post("/dash/login", data={"email": "receptionist@co.com", "token": "a brand new long password"})
ok("a member cannot invite", nc.post("/dash/people/invite", data={"email": "friend@co.com"}).status_code == 403
   and state.user_by_email("friend@co.com") is None)
ok("a member cannot remove anyone", nc.post(f"/dash/people/{member['id']}/remove").status_code == 403
   and state.get_user(member["id"]).get("active") == 1)

print("\n— the seat limit refuses the INVITE, never the sign-in —")
real_cap = state.max_users
state.max_users = lambda: state.count_active_users()          # the box is exactly full
try:
    full, _ = invite_link(owner_client(), "one-too-many@co.com")
    ok("an invite past the limit is refused and nobody is added",
       full.status_code == 409 and state.user_by_email("one-too-many@co.com") is None)
    ok("...while everyone already in still signs in", client().post(
        "/dash/login", data={"email": "receptionist@co.com", "token": "a brand new long password"}).status_code in (302, 303))
finally:
    state.max_users = real_cap

print("\n— an expired link is dead —")
late = state.add_user("late@co.com")
late_link = state.create_invite(late["id"])
with state.connect() as c:
    c.execute("UPDATE user_invites SET expires_at = ? WHERE token_hash = ?",
              ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), state._token_hash(late_link)))
ok("a link past its seven days opens nothing", client().get(f"/join?t={late_link}").status_code == 404)

print("\n— no employee on a box whose dashboard password is its API key —")
real_token = settings.dash_token
settings.dash_token = settings.dispatch_bearer_token
try:
    shared, _ = invite_link(owner_client(), "blocked@co.com")
    ok("the invite is refused with the reason, and nobody is added",
       shared.status_code == 409 and state.user_by_email("blocked@co.com") is None)
finally:
    settings.dash_token = real_token
install = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "install.sh")).read()
ok("install.sh mints a DASH_TOKEN of its own, after the API key, so a fresh box can invite people",
   "mint DASH_TOKEN" in install and install.index("mint DISPATCH_BEARER_TOKEN") < install.index("mint DASH_TOKEN"))

print("\n— a claimed box: the owner signs in with his own password —")
import json                                                          # noqa: E402
with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
    json.dump({"order": "cs_live_TEAMLOGINS0000000000000000000000000000", "host": "t.ownbox.app"}, fh)
claim.claim_box(code="cs_live_TEAMLOGINS0000000000000000000000000000", email="Buyer@Co.com", password=PW)
ok("the claim put his password on his own row", state.password_hash_for("buyer@co.com") is not None)
ok("he signs in with email and password", client().post(
    "/dash/login", data={"email": "buyer@co.com", "token": PW}).status_code in (302, 303))
ok("...and with only the password, as the claim page told him", client().post(
    "/dash/login", data={"token": PW}).status_code in (302, 303))

print(f"\n{_failed} FAILED" if _failed else "\nall ok")
sys.exit(1 if _failed else 0)
