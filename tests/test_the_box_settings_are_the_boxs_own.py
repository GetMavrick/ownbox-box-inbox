"""The AI account, the phone and the AI coworkers are the BOX's, and core holds their doors.

WHAT THIS IS ABOUT. Owner, 2026-09-22, after connecting his AI account from System Settings and
being thrown into the inbox's wizard to finish: *"we need to find tune our wizards. Step four and
five should not be in this wizard any longer"* — and, earlier the same session, *"Step three and
step four should be a core setting. The LLM and the phone."*

WHY IT WAS NOT A TWO-LINE CHANGE. Filtering the wizard turns `test_setup_screen_loop` red on
purpose: that screen is the box's set-up CONTRACT made visible, so it cannot show a subset of it.
And underneath, all three box-level steps were FINISHED INSIDE A MACHINE — the AI account at a
sign-in route the inbox served, the coworkers at the inbox's own screen, and the phone at no door
at all. OSDev5 scoped the three ways out (`docs/SCOPE_BOX_STEPS_NEED_CORE_DOORS.md`); OSDev1 chose
option C, the doors move into core.

THIS SUITE SHIPS INTO EVERY BOX AND NAMES NO MACHINE, deliberately — that is the claim being
tested. A Lead box with no inbox on it needs an AI account, a phone and a coworker seat just as
much, and before today could reach none of them. If any assertion here needed `marketing.*`, the
move would not have worked.

WHAT IT HOLDS:
  · the contract points at core doors, and at no machine path
  · every door answers, and the routes they replaced are gone
  · a member is offered the phone and NOT offered the two they would be refused at
  · the doors are gated, not merely undrawn — direct navigation 403s
  · the JSON endpoints answer JSON to a stranger, never a login page a fetch would parse
  · saving a key from Settings leaves you on Settings, and the key actually lands
  · core still names no machine path, in code or in prose

Run: python tests/test_the_box_settings_are_the_boxs_own.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "boxsettings.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on (OSDev5, 2026-09-18). Our
# dev boxes carry these; CI carries none, and a suite that passes only on one of the two is worse
# than no suite.
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

from core import box_secrets, dash, onboarding, push                     # noqa: E402
from core.dash import box_settings                                       # noqa: E402
from core.dispatch import app                                            # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def owner_client():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


def member_client():
    who = state.add_user("sam@acme.co", name="Sam", role="member")
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(who["id"]))
    return c


def step(key: str) -> dict:
    return next(e for e in box_secrets.SETUP_STEPS if e["key"] == key)


ROUTES = {str(r) for r in app.url_map.iter_rules()}


# ── 1. the contract says the box's steps are the box's, and points at core ───────────────
print("\ntest_the_contract_splits_and_points_at_doors_core_serves")

box = [e["key"] for e in box_secrets.SETUP_STEPS
       if box_secrets.surface_of(e) == box_secrets.SURFACE_BOX]
machine = [e["key"] for e in box_secrets.SETUP_STEPS
           if box_secrets.surface_of(e) == box_secrets.SURFACE_MACHINE]
ok("the box's three are the AI account, the phone and the AI coworkers",
   box == ["anthropic", "phone", "agent"], str(box))
ok("...and the mailbox and the channels stay the machine's", machine == ["email", "zernio"],
   str(machine))

# A STEP THAT DECLARES NOTHING IS THE MACHINE'S. Every machine written before today registers no
# surface at all, and a default that quietly moved their steps into core's drawer on upgrade is
# the worst kind of quiet.
ok("a step declaring no surface reads as the machine's",
   box_secrets.surface_of({"key": "whatever"}) == box_secrets.SURFACE_MACHINE)
ok("...and so does a step declaring nonsense",
   box_secrets.surface_of({"surface": "Box"}) == box_secrets.SURFACE_MACHINE)
ok("...and so does nothing at all", box_secrets.surface_of(None) == box_secrets.SURFACE_MACHINE)

doors = {"anthropic": step("anthropic").get("action_href"),
         "phone": step("phone").get("action_href"),
         "agent": (step("agent").get("link") or {}).get("url")}
for key, href in doors.items():
    ok(f"{key}: its door is a path core serves", href in box_settings.registered_doors(), str(href))
    ok(f"{key}: ...and names no machine", not str(href).startswith("/inbox/"), str(href))
    ok(f"{key}: ...and the app actually routes it", href in ROUTES, str(href))


# ── 2. the routes the machine was holding are gone from it ───────────────────────────────
print("\ntest_the_machine_no_longer_serves_the_box_s_doors")

for gone in ("/inbox/connect-claude", "/inbox/agent", "/inbox/push/key", "/inbox/push/subscribe"):
    ok(f"{gone} is no longer served", gone not in ROUTES)
for here in ("/settings/ai", "/settings/phone", "/settings/agent",
             "/settings/push/key", "/settings/push/subscribe"):
    ok(f"{here} is", here in ROUTES)

# THE CLIENT SCRIPT MOVED WITH ITS ENDPOINTS, which is the only reason it can live in core: while
# it fetched `/inbox/push/*` it named a machine twice.
ok("the push script fetches core's endpoints", "/settings/push/key" in push.CLIENT_JS
   and "/settings/push/subscribe" in push.CLIENT_JS)
ok("...and names no machine at all", "/inbox/" not in push.CLIENT_JS)
# AND IT STILL ASKS NOBODY. Owner, 2026-09-20: "after the buyer has seen their first real message,
# never on first load." An iOS denial is close to permanent, so a script that called this itself
# would spend the whole channel on page one.
ok("...and calls neither of the two functions it defines",
   push.CLIENT_JS.count("ownboxEnableNotifications") == 1
   and push.CLIENT_JS.count("ownboxCanBeRung") == 2,   # defined once, read once by the other
   str((push.CLIENT_JS.count("ownboxEnableNotifications"),
        push.CLIENT_JS.count("ownboxCanBeRung"))))


# ── 3. the owner's Settings offers all three and each one answers ────────────────────────
print("\ntest_the_owner_is_offered_every_box_setting_and_each_door_opens")

oc = owner_client()
html = oc.get("/settings").get_data(as_text=True)
for key in box:
    ok(f"{key}: the step is named on Settings", step(key)["title"] in html)
    ok(f"{key}: ...and its door is linked from there", str(doors[key]) in html, str(doors[key]))
for href in box_settings.registered_doors():
    r = oc.get(href)
    ok(f"{href} answers the owner", r.status_code == 200, str(r.status_code))


# ── 4. a member meets no door they are refused at, and keeps the one that is theirs ──────
print("\ntest_a_member_is_offered_their_phone_and_no_door_that_would_refuse_them")

mc = member_client()
mhtml = mc.get("/settings").get_data(as_text=True)
ok("a member still sees all three rows, so they know what the box has",
   all(step(k)["title"] in mhtml for k in box), str(box))
# A PHONE BELONGS TO A PERSON, NOT TO WHOEVER BOUGHT THE BOX. A member working the inbox all day
# needs the notification more than the owner does.
ok("...is offered the phone, which is theirs", "/settings/phone" in mhtml)
ok("...and is NOT offered the AI account, which bills the whole box",
   "/settings/ai" not in mhtml)
ok("...nor the coworker seat, which reads every message on it",
   "/settings/agent" not in mhtml)

# UNDRAWN IS NOT THE SAME AS SHUT. The link being absent is what stops the dead end; the gate is
# what stops the member. Both, or this is a hidden button rather than a permission.
for href in ("/settings/ai", "/settings/agent"):
    ok(f"{href} refuses a member outright", mc.get(href).status_code == 403,
       str(mc.get(href).status_code))
    ok(f"{href} ...on the POST too, not merely the page",
       mc.post(href, data={"do": "start"}).status_code == 403)
ok("/settings/phone admits a member", mc.get("/settings/phone").status_code == 200)


# ── 5. a stranger gets a refusal in the shape the caller can read ────────────────────────
print("\ntest_a_stranger_is_refused_and_a_fetch_is_refused_in_json")

anon = app.test_client()
for href in box_settings.registered_doors():
    r = anon.get(href)
    ok(f"{href} refuses anonymous", r.status_code in (302, 303)
       and "/dash/login" in (r.headers.get("Location") or ""), str(r.status_code))
    ok(f"{href} ...and serves no content while doing it", len(r.get_data()) < 400)

# A FETCH CANNOT FOLLOW A LOGIN REDIRECT USEFULLY. `push.CLIENT_JS` reads these two as JSON, so a
# 302 onto an HTML page surfaces to the buyer as a JSON parse error given as the reason their
# notifications failed.
kr = anon.get("/settings/push/key")
ok("the push key endpoint answers a stranger in JSON", kr.status_code == 403
   and kr.is_json, f"{kr.status_code} {kr.content_type}")
ok("...and says it is not available rather than handing out a key",
   kr.get_json().get("available") is False and not kr.get_json().get("key"), kr.get_data(as_text=True))
sr = anon.post("/settings/push/subscribe", json={"endpoint": "https://example.com/x"})
ok("the subscribe endpoint answers a stranger in JSON",
   sr.status_code == 403 and sr.is_json, f"{sr.status_code} {sr.content_type}")
ok("...and stores nothing", sr.get_json().get("ok") is False, sr.get_data(as_text=True))


# ── 6. connecting from Settings lands the key AND leaves you on Settings ─────────────────
print("\ntest_connecting_the_ai_account_from_settings_leaves_you_on_settings")

box_secrets.clear_claude_oauth()
before = box_secrets.anthropic_state()["status"]
ok("nothing is connected to begin with", before == "not_connected", before)

# A SUBSCRIPTION TOKEN, which needs no vendor call to store — an API key would reach Anthropic.
token = "sk-ant-oat" + ("T" * 48)
r = oc.post("/settings/ai", data={"do": "key", "key": token})
ok("the save is accepted", r.status_code in (302, 303), str(r.status_code))
# THE OWNER'S ACCEPTANCE, IN OSDEV5'S WORDS: connecting the AI account from System Settings leaves
# you on System Settings. He hit the opposite of this and it is what started the whole change.
ok("...and lands back on Settings, not inside a machine",
   (r.headers.get("Location") or "").endswith("/settings"), str(r.headers.get("Location")))
ok("...and the credential actually landed",
   box_secrets.anthropic_state()["status"] == "connected",
   str(box_secrets.anthropic_state()))
ok("...and the box knows it holds a subscription rather than an API key",
   bool(box_secrets.claude_oauth_token()))

# THE TICK IS OFFERED AND IS NEVER REQUIRED. Owner, 2026-09-18: "We are not policing people's
# usage of their own property... we're going above and beyond by putting a link to Anthropic terms
# and to OpenAI terms right there on the page."
ai = oc.get("/settings/ai").get_data(as_text=True)
ok("the terms of both vendors are linked on the page",
   all(url in ai for _label, url in box_secrets.TERMS_LINKS), str(box_secrets.TERMS_LINKS))
ok("...and the consent tick is offered", 'name="subscription_consent"' in ai)
ok("...and is NOT required — the box refuses nobody",
   'name="subscription_consent"' in ai
   and "required" not in ai.split('name="subscription_consent"')[1][:120],
   ai.split('name="subscription_consent"')[1][:120])
box_secrets.clear_claude_oauth()


# ── 7. a coworker seat is minted, shown once, listed and revoked — all in core ────────────
print("\ntest_a_coworker_seat_is_minted_and_revoked_without_leaving_the_box_s_settings")

from core.connector import seats                                         # noqa: E402

r = oc.post("/settings/agent", data={"do": "mint", "label": "Grok on X", "role": "read"})
minted = r.get_data(as_text=True)
ok("minting answers with a page rather than a redirect", r.status_code == 200, str(r.status_code))
# NEVER A REDIRECT AND NEVER A QUERY STRING: gunicorn logs raw query strings, so a credential in
# a URL is a credential in the box's log file.
ok("...and the credential is in the body, never in a Location", not r.headers.get("Location"))
ok("...and the page says it is shown once before it shows it",
   minted.find("only time") < minted.find("MCP"), str((minted.find("only time"), minted.find("MCP"))))
# ONE ADDRESS, THE SHORT ONE (#1417, OSDev1). The minting screen printed `/api/v1/mcp` while the
# screen that links to it printed `/mcp` — both work, but a buyer shown two addresses for one
# thing reasonably concludes one is wrong, mid-paste of a secret. His fix landed on main while
# this screen was moving into core, so it is asserted here or the next merge quietly loses it.
ok("...and it prints ONE address, the short one", "/mcp" in minted and "/api/v1/mcp" not in minted,
   "two different MCP addresses for one thing — #1417 reverted by a merge")
ok("...the same one the listing screen shows",
   "/api/v1/mcp" not in oc.get("/settings/agent").get_data(as_text=True))
# #1419 (OSDev1), CARRIED ACROSS THE SAME WAY, and pinned so the next merge cannot lose it either:
# read-and-draft is the preselected answer (owner: "we're always going to want to read and
# draft"), the three sign-in steps come from `box_secrets.AGENT_STEPS`, and the by-hand key form
# is folded away behind the sign-in path rather than leading the screen.
_ag = oc.get("/settings/agent").get_data(as_text=True)
ok("read-and-draft is the preselected role", 'value="act" checked' in _ag and 'value="read" checked' not in _ag)
ok("...and a mint with no role given lands as read-and-draft",
   (lambda r: r.status_code == 200 and any(
       s_.get("role") == "act" for s_ in seats.all_seats() if s_.get("label") == "Default role"))(
       oc.post("/settings/agent", data={"do": "mint", "label": "Default role"})),
   str([(s_.get("label"), s_.get("role")) for s_ in seats.all_seats()]))
ok("the three sign-in steps are on the screen, from the contract",
   all(t[:30] in _ag for t in getattr(box_secrets, "AGENT_STEPS", ())) and len(getattr(box_secrets, "AGENT_STEPS", ())) == 3)
ok("...and the by-hand key form is folded away, not leading", _ag.find("<details") < _ag.find('name="do" value="mint"'))
for s_ in seats.all_seats():
    if s_.get("label") == "Default role" and not s_.get("revoked_at"):
        seats.revoke(s_["id"])
live = [s for s in seats.all_seats() if not s.get("revoked_at")]
ok("...and the seat exists", any(s.get("label") == "Grok on X" for s in live), str(live))

listed = oc.get("/settings/agent").get_data(as_text=True)
ok("the seat is listed so it can be revoked without remembering it", "Grok on X" in listed)
sid = next(s["id"] for s in live if s.get("label") == "Grok on X")
rr = oc.get(f"/settings/agent?revoke={sid}")
ok("revoking answers", rr.status_code in (302, 303), str(rr.status_code))
ok("...and the seat is revoked",
   all(s.get("revoked_at") for s in seats.all_seats() if s.get("label") == "Grok on X"),
   str(seats.all_seats()))
# A REVOKED SEAT IS STILL LISTED. It is the audit trail: "this assistant had access between these
# dates" is a question a buyer will eventually be asked by somebody else.
ok("...and still listed afterwards, as the audit trail",
   "Grok on X" in oc.get("/settings/agent").get_data(as_text=True))


# ── 8. the phone finally has a screen of its own ─────────────────────────────────────────
print("\ntest_the_phone_step_has_a_door_at_all_which_it_never_had")

ph = oc.get("/settings/phone").get_data(as_text=True)
# FROM THE CONTRACT, NOT FROM THE PAGE'S OWN COPY. Two surfaces render these words — this page and
# the printed handout — so `_PHONE_STEP["platforms"]` is where they live and this asserts the page
# is actually drawing them. A page with its own copy passes a test written against its own copy.
plats = step("phone").get("platforms") or ()
ok("the contract carries per-platform instructions", len(plats) >= 2, str(len(plats)))
for pl in plats:
    ok(f"the phone page carries the {pl['title']} heading", pl["title"] in ph)
    for line in pl["steps"]:
        ok(f"  …and: {line[:34]}…", line[:34] in ph)
# INSTALLING IS HERE; ASKING IS NOT.
ok("...and fires no permission prompt of its own", "requestPermission" not in ph)


# ── 8b. the handout is meant to be left on a counter, so it carries nothing private ──────
print("\ntest_the_printable_handout_is_safe_to_leave_on_a_counter")

# THE PERSON WHO NEEDS THIS PAGE IS OFTEN NOT THE PERSON WHO BOUGHT THE BOX. Owner, 2026-09-20:
# "A nice printable page that people can hand to their colleagues." A box seats three, and whoever
# watches the inbox at 8 AM is frequently not the owner.
sheet_r = mc.get("/settings/phone/print")            # a MEMBER — this is the seat it is for
ok("the handout answers a member", sheet_r.status_code == 200, str(sheet_r.status_code))
sheet = sheet_r.get_data(as_text=True)

from core import dash as _dash                                           # noqa: E402

ok("it carries the business's own name, not ours", _dash.brand() in sheet, _dash.brand())
ok("...and the address to open on the phone", "/settings/phone" in sheet)
ok("...and both platforms, because whoever prints it does not know which phone is next",
   "iPhone" in sheet and "Android" in sheet)
ok("...and a print stylesheet, so it is one sheet and not the whole app",
   "@media print" in sheet)
ok("...and none of the box's navigation, which means nothing to a colleague",
   "/dashboard" not in sheet and "/settings/ai" not in sheet and "/settings/agent" not in sheet)
ok("...and it fires no permission prompt", "requestPermission" not in sheet)

# THE ONE THAT MATTERS, AND IT IS ASSERTED AGAINST REAL SECRETS RATHER THAN A PATTERN. The day
# somebody adds "and here is your key" to this page is the day a key goes on a noticeboard, so the
# box is given genuine credentials FIRST and the sheet is then read for every one of them.
_tok = "sk-ant-oat" + ("H" * 48)
box_secrets.put_claude_oauth(_tok, consented=True, user_id=state.owner_user()["id"])
_sid, _cred = seats.mint("Handout leak check", "read")
fresh = mc.get("/settings/phone/print").get_data(as_text=True)
for label, secret in (("the AI subscription token", _tok),
                      ("a coworker seat credential", _cred),
                      ("the dispatch bearer token", os.environ["DISPATCH_BEARER_TOKEN"]),
                      ("the dashboard password", os.environ["DASH_TOKEN"])):
    ok(f"the handout does not carry {label}", secret not in fresh,
       f"{label} is printed on a sheet meant to be left on a counter")
# AND NO SIGN OF A PERSON EITHER — it is handed to somebody who is not on the box yet.
for label, secret in (("an account's email address", "sam@acme.co"),
                      ("the owner's email address", state.owner_user().get("email") or "\0")):
    ok(f"the handout does not carry {label}", secret not in fresh, label)
seats.revoke(_sid)
box_secrets.clear_claude_oauth()

# AN ADDRESS THAT CANNOT WORK FROM ANOTHER DEVICE MUST NEVER REACH PAPER.
#
# MEASURED, NOT IMAGINED (2026-09-22): the owner opened a sheet rendered from a box reached over
# `localhost`, followed its last instruction on his phone, and Safari said it could not connect to
# the server. `localhost` on a phone IS that phone. A dead link on a screen is a back button; a
# dead address on a sheet pinned to a wall is read by somebody who was not there when it was
# printed and has nobody to ask — the precise person this page exists for.
from core.dash.box_settings import _handout_address                      # noqa: E402

for root in ("http://localhost", "http://localhost:8000", "http://127.0.0.1:5000",
             "http://[::1]:5000", "http://0.0.0.0:8000", "http://box.localhost",
             "http://169.254.11.4"):
    ok(f"{root} never reaches paper", _handout_address(root) == "", _handout_address(root))
for root, want in (("https://acme.ownbox.app", "https://acme.ownbox.app/settings/phone"),
                   ("https://inbox.acmeplumbing.com",
                    "https://inbox.acmeplumbing.com/settings/phone"),
                   # A LAN ADDRESS IS ALLOWED ON PURPOSE: it is how a receptionist on the shop's
                   # own wifi reaches a box that is not on the public internet. Refusing it would
                   # be us deciding how somebody may run their own box.
                   ("http://192.168.1.40:8000", "http://192.168.1.40:8000/settings/phone"),
                   ("http://10.0.0.5", "http://10.0.0.5/settings/phone")):
    ok(f"{root} prints as itself", _handout_address(root) == want, _handout_address(root))

# AND THE SHEET SAYS SO RATHER THAN GOING QUIET. A sheet that silently drops its own address is
# one somebody prints, hands over, and only then discovers is useless.
ok("a sheet with no printable address explains itself instead",
   "open this page at the box" in fresh and "print it again" in fresh,
   fresh[fresh.find("Then open this address"):][:220])
ok("...and does not print the dead address anyway", "localhost/settings/phone" not in fresh,
   "a dead address reached paper")


# THE PAGE OFFERS IT, or nobody ever prints it.
page = mc.get("/settings/phone").get_data(as_text=True)
ok("the phone page links to the handout", "/settings/phone/print" in page)
# INSTALLING IS HERE; ASKING IS NOT — the page must not fire the prompt it is teaching people to
# expect later. An iOS denial is close to permanent.
ok("...and the page itself asks for nothing", "requestPermission" not in page)


# ── 9. core still names no machine, in code or in prose ──────────────────────────────────
print("\ntest_core_still_names_no_machine_path")

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for rel in ("core/dash/home.py", "core/dash/box_settings.py"):
    src = open(os.path.join(_root, rel), encoding="utf-8").read()
    ok(f"{rel} names no machine path", "/inbox/" not in src,
       f"{rel} spells /inbox/ — core must not, in code OR in a comment")

# THE SEAM CARRIES THE SAME WORD, so a machine can declare a box-level step one day without this
# contract having two vocabularies — and it is CHECKED AT IMPORT rather than read leniently later.
# A typo ("Box", "system") would otherwise fall through `surface_of`'s default and the step would
# simply render on the wrong screen, which is a bug nobody would think to look for.
def _refused(**kw) -> bool:
    try:
        onboarding.register_step(
            "bogus_surface", order=1, machine="nobody", title="T", why="W",
            fields=({"name": "k", "label": "K", "type": "password"},), steps=("s",),
            state=lambda: {"status": "not_connected"},
            save=lambda values, user_id=None: None, **kw)
    except ValueError:
        return True
    return False


ok("the seam refuses a surface that is neither the box's nor a machine's",
   _refused(surface="system"))
ok("...and accepts the two that are", not _refused(surface="box") and not _refused(surface="machine"))
ok("...and a step that declares none is the machine's, through the seam too",
   not _refused() and next(e for e in onboarding.steps() if e["key"] == "bogus_surface")
   .get("surface") == "machine",
   str([e.get("surface") for e in onboarding.steps() if e["key"] == "bogus_surface"]))


print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
