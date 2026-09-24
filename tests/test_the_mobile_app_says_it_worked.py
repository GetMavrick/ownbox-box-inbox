"""Installing the app ends with the box saying whether it worked (walk #9).

docs/JOURNEY_WALK_2026-09-23.md, finding 9: a buyer who followed the install steps on
`/settings/mobile` saw no prompt and nothing change, and could conclude the box was broken.
OSDev1's done-when: "one line on the box saying it's connected, or exactly what's missing."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · the sentence disappears from either place it runs. `/settings/mobile` is outside the installed
    app's /inbox/ scope, so on an iPhone only the inbox's own Settings tab can see the app's
    registration; both carry it.
  · the box says "connected" about a device it holds no registration for, or about someone else's
    device. `/settings/push/mine` answers one bit, for the person asking, about their own rows.
  · the check fires the permission prompt. The owner's ruling (2026-09-20) is that the box asks
    after the first real message, from Messages, never on a settings page.
  · a state reads "registering" forever: re-registering waits on the service worker, which only
    controls /inbox/, so it may only run where that worker is in control, and it times out.
  · a buyer reads a reserved noun (CLAUDE.md, mobile first).

The browser half was driven in Chromium for every state (computer, not installed, waiting, blocked,
connected, allowed-but-unregistered, box cannot send); screenshots are in the PR.

Run: python tests/test_the_mobile_app_says_it_worked.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "device.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import dash, push                                           # noqa: E402
from core.dispatch import app                                         # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def client(user_id=None):
    c = app.test_client()
    if user_id:
        c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


owner = state.owner_user()["id"]
member = state.add_user("sam@example.com", name="Sam")["id"]
MINE, SAMS = "https://push.example/owner-mobile", "https://push.example/sam-mobile"
push.save_subscription(user_id=str(owner), endpoint=MINE, p256dh="p", auth="a")
push.save_subscription(user_id=str(member), endpoint=SAMS, p256dh="p", auth="a")


print("\ntest_the_box_answers_one_bit_about_your_own_device")
def mine(c, endpoint):
    r = c.post("/settings/push/mine", json={"endpoint": endpoint})
    return r.status_code, (r.get_json(silent=True) or {}).get("mine")
ok("your own stored endpoint is yours", mine(client(owner), MINE) == (200, True))
ok("an endpoint the box never stored is not", mine(client(owner), "https://push.example/new") == (200, False))
ok("somebody else's device is never reported as yours", mine(client(owner), SAMS) == (200, False))
ok("...and theirs is theirs", mine(client(member), SAMS) == (200, True))
ok("an empty endpoint is not a device", mine(client(owner), "") == (200, False))
ok("signed out, it answers nothing", mine(client(), MINE)[0] in (302, 401, 403), str(mine(client(), MINE)))


print("\ntest_the_sentence_is_on_both_screens")
html = client(owner).get("/settings/mobile").get_data(as_text=True)
ok("/settings/mobile has the device sentence", 'id="ownbox-device"' in html)
ok("...and the script that fills it", "getElementById('ownbox-device')" in html)
ok("...but no code that could fire the permission prompt", "requestPermission" not in html)
ok("...and it comes before the box-wide card, which is not about your device",
   html.find('id="ownbox-device"') < html.find("Where this box stands"))
ok("without JavaScript it says what it cannot know, not nothing",
   "Open this page with JavaScript on" in html)
_inbox = ROOT / "marketing" / "customer_voice" / "app.py"
if _inbox.is_file():
    ib = client(owner).get("/inbox/settings").get_data(as_text=True)
    ok("the inbox's own Settings tab has it too, where the installed app can see itself",
       'id="ownbox-device"' in ib and "getElementById('ownbox-device')" in ib)
    ok("...with a way to the install steps", 'href="/settings/mobile"' in ib)
    ok("the stale 'System follows' sentence is gone", "System follows" not in ib)
else:
    print("  --   no inbox machine ships on this box, so there is no inbox Settings tab to check")


print("\ntest_the_check_never_asks_the_question_itself")
js = push.DEVICE_JS
ok("the device check never fires the permission prompt itself",
   "requestPermission" not in js)
_enable = js.find("ownboxEnableNotifications()")
ok("it re-registers only after the answer is already yes",
   _enable != -1 and js.rfind("Notification.permission !== 'granted'", 0, _enable) != -1)
ok("...only where the app's service worker is in control, and the client is loaded",
   js.rfind("navigator.serviceWorker.controller", 0, _enable) != -1
   and js.rfind("typeof window.ownboxEnableNotifications", 0, _enable) != -1)
ok("...and never waits forever", "setTimeout" in js and "Promise.race" in js)
ok("the box's own ability to send is asked first",
   js.find("/settings/push/key") != -1 and js.find("/settings/push/key") < js.find("registered();"))


print("\ntest_every_state_is_a_sentence")
states = set(re.findall(r"say\('([a-z-]+)'", js))
want = {"connected", "computer", "not-installed", "unsupported", "blocked", "waiting",
        "open-app", "checking", "failed", "box-off"}
ok("every state the walk needs has its own sentence", want <= states, str(sorted(want - states)))
_said = " ".join(re.findall(r"'([^']*)'", js))
_banned = re.findall(r"\b(phone|phones|ring|call|calls|dial|line|voice|answer)\b", _said, re.I)
ok("no reserved noun in anything a buyer reads", not _banned, str(_banned))


print("\ntest_the_script_parses")
_node = shutil.which("node")
if _node:
    f = pathlib.Path(_T) / "device.js"
    f.write_text(push.CLIENT_JS + push.DEVICE_JS)
    r = subprocess.run([_node, "--check", str(f)], capture_output=True, text=True)
    ok("the client and the device check are valid JavaScript", r.returncode == 0, r.stderr[-300:])
else:
    print("  --   no node on this machine, so the syntax check is skipped here")


print("\n— and this file cannot silently fall out of CI —")
_wf = ROOT / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_the_mobile_app_says_it_worked is in the workflow's suite list",
       "test_the_mobile_app_says_it_worked" in _wf.read_text())

print("\nALL OK" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
