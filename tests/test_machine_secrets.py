"""A machine's own secrets: `m.secret` and its Keys page (OSDev1, 2026-10-02: "m.secret(name), generic, one home per
secret on the machine's settings page"; the Lead Magnet port needs a Sanity token, and OSDev5's website machine
reuses it).

What is measured, on the real store and the real page:
  * a machine declares a secret once with a label; until the owner saves one it reads ""
  * the owner saves it on the machine's Keys page; the machine reads it; the page never shows it back
  * a key with a space or a line break, or an empty one, is refused in a sentence, and nothing is saved
  * one machine can't read another's secret, and no machine reads the box's own keys
  * a member can't see or change the Keys page; a machine that declared nothing has no Keys page
  * Remove clears it; the page works at a mobile app's width (16px fields, full width)
  * `secret` is a promised seam of SDK 1

Run: python tests/test_machine_secrets.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="msecret_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

from core import box_secrets, machine_secrets, sdk, state  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
member = app.test_client()
member.set_cookie(dash.COOKIE, dash.new_session(state.add_user("sam@acme.co", name="Sam", role="member")["id"]))

SECRET = "sk_live_Abc123-xyz_987"
m = sdk.machine("lead-magnet")
other = sdk.machine("website")
PAGE = m.keys_page

print("declare and read")
v = m.secret("sanity_token", label="Sanity write token", help="Sanity → API → Tokens → add an Editor token")
ok("declared with a label, and empty until the owner saves one", v == "" and m.secret("sanity_token") == "")
ok("the Keys page lives under the machine's own name", PAGE == "/settings/machines/lead-magnet/keys", PAGE)
try:
    m.secret("Bad Name", label="x")
    bad = False
except ValueError:
    bad = True
ok("a secret's name is checked", bad)

print("the Keys page")
page = owner.get(PAGE)
html = page.get_data(as_text=True)
ok("the owner sees one field for it, with its label and help", page.status_code == 200
   and "Sanity write token" in html and "Tokens" in html and 'type="password"' in html, page.status_code)
ok("a field is 16px and full width, so a mobile app doesn't zoom on focus", "font-size:16px" in html
   and "width:100%" in html)
r = owner.post(PAGE, data={"name": "sanity_token", "do": "save", "value": SECRET})
ok("saving it redirects back with a sentence", r.status_code == 303 and "saved=sanity_token" in r.headers["Location"])
ok("...and the machine reads it", m.secret("sanity_token") == SECRET)
html = owner.get(PAGE + "?saved=sanity_token").get_data(as_text=True)
ok("the page never shows the saved value back, not even part of it", SECRET not in html and SECRET[-6:] not in html
   and "Saved." in html and "Sanity write token saved." in html)

r = owner.post(PAGE, data={"name": "sanity_token", "do": "save", "value": "abc def"})
ok("a key with a space is refused in a sentence, and the saved one is kept", r.status_code == 400
   and "space" in r.get_data(as_text=True) and m.secret("sanity_token") == SECRET)
r = owner.post(PAGE, data={"name": "sanity_token", "do": "save", "value": "  "})
ok("an empty key is refused", r.status_code == 400 and m.secret("sanity_token") == SECRET)
r = owner.post(PAGE, data={"name": "not_declared", "do": "save", "value": "x1"})
ok("a name the machine didn't declare is refused", r.status_code == 400)

print("isolation")
other.secret("sanity_token", label="Sanity token")
ok("another machine with the same secret name reads its own, not this one's", other.secret("sanity_token") == "")
box_secrets.put(box_secrets.ZERNIO, "zk_box_own_key_123")
ok("no machine reads the box's own keys", machine_secrets.get(m.key, "zernio_api_key") == "")
ok("the value is stored under the machine's own name",
   box_secrets.get(f"machine:{m.key}:sanity_token") == SECRET)

print("who can see it")
r = member.get(PAGE)
ok("a member can't see the Keys page", r.status_code in (302, 303, 403), r.status_code)
r = member.post(PAGE, data={"name": "sanity_token", "do": "remove"})
ok("...or change it", r.status_code in (302, 303, 403) and m.secret("sanity_token") == SECRET, r.status_code)
ok("a machine that declared nothing has no Keys page", owner.get("/settings/machines/job-tracker/keys").status_code == 404)
ok("a bad address is a 404", owner.get("/settings/machines/Bad_Slug/keys").status_code == 404)

print("remove")
r = owner.post(PAGE, data={"name": "sanity_token", "do": "remove"})
ok("Remove clears it", r.status_code == 303 and m.secret("sanity_token") == "")
ok("...and the page says it's not set", "Not set yet." in owner.get(PAGE).get_data(as_text=True))

ok("`secret` is a promised seam of SDK 1", "secret" in sdk.SEAMS)

print("ALL MACHINE SECRET CHECKS PASS" if not _failed else f"{_failed} MACHINE SECRET CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
