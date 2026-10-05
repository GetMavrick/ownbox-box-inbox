"""Phone notifications on the Base Machine (owner, 10-04 22:2x, relayed by OSDev1: "finish them in Base").

A notification needs a service worker, and the only one was the Unified Inbox's, at /inbox/. So a box without the
Inbox could never reach a phone, while its Mobile App and Email pages promised it could (WebDev2's finding, 10-04).
Now the Base Machine serves its own at the root, and a box uses exactly one.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · /ui/sw.js is not served to a browser without a session, or not allowed the whole box (Service-Worker-Allowed: /);
  · a push shows no notification (Safari revokes the permission after one silent push), or a tap opens anything but
    a page on this box;
  · WHERE THE BASE WORKER IS THE BOX'S, the Morning Review (the app's first screen) doesn't carry the card that asks;
    the card shows in a Safari tab, or once permission is granted or refused, or after "Not now"; anything is asked
    or registered before a press; or the Dashboard or Mobile App page carries script that could ask (OSDev1's
    23:24: the Dashboard has no script, and the Mobile App page never asks, owner 09-20);
  · where a machine brings its own worker, the Review draws the card (two workers ring one mobile twice);
  · the Mobile App page says "when a customer writes" without a machine that reads customers' messages, or doesn't
    send a Base box's buyer to the Morning Review;
  · ONE ALERT PER EVENT: a person reached through a machine's worker is also reached through the Base one; or a
    person whose only subscription is the Base one, or an existing one saved before scopes were sent, is dropped;
  · the Inbox's own worker sends a tap on an Approvals notification anywhere but Approvals.
Run: python tests/test_base_push.py
"""
from __future__ import annotations

import json
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
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "push.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state  # noqa: E402

state.init_db()

from core import dash, push  # noqa: E402
from core.dash import box_settings, look, review as reviewmod  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []
NODE = shutil.which("node")


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:900]}"))
    if not cond:
        FAILS.append(label)


def client(user_id=None):
    c = app.test_client()
    if user_id:
        c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def page(c, path: str) -> str:
    return c.get(path).get_data(as_text=True)


def run_js(code: str, harness: str) -> dict:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "code.js").write_text(code)
    (d / "run.js").write_text(harness)
    r = subprocess.run([NODE, str(d / "run.js"), str(d / "code.js")], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (r.stderr or r.stdout)[-600:]}


owner = str(state.owner_user()["id"])
member = str(state.add_user("dana@glowmedspa.example", name="Dana Ruiz")["id"])
MACHINES = list(push._MACHINE_WORKERS)          # what this checkout's machines registered at import


def base_box() -> None:
    push._MACHINE_WORKERS[:] = []


def inbox_box() -> None:
    push._MACHINE_WORKERS[:] = ["/inbox/"]


print("\nthe Base Machine's worker")
r = client().get("/ui/sw.js")
ok("served to a browser with no session, as JavaScript, never cached stale",
   r.status_code == 200 and r.mimetype == "application/javascript" and r.headers.get("Cache-Control") == "no-cache",
   (r.status_code, r.mimetype, r.headers.get("Cache-Control")))
ok("...and allowed the whole box: Service-Worker-Allowed: /", r.headers.get("Service-Worker-Allowed") == "/",
   dict(r.headers))
SW = r.get_data(as_text=True)
if NODE:
    got = run_js(SW, r"""
const code = require('fs').readFileSync(process.argv[2], 'utf8');
const on = {}, shown = [], opened = [], navigated = [], waits = [];
const self = {
  addEventListener: (t, f) => { on[t] = f; }, skipWaiting() {},
  clients: { claim() { return Promise.resolve(); }, openWindow(u) { opened.push(u); return Promise.resolve(); },
             matchAll() { return Promise.resolve(globalThis.open_now ? [{ focus() {}, navigate(u) { navigated.push(u); } }] : []); } },
  registration: { showNotification(t, o) { shown.push([t, o.body, o.data.navigate]); return Promise.resolve(); } },
};
new Function('self', code)(self);
const ev = (x) => Object.assign({ waitUntil(p) { waits.push(p); } }, x);
on.push(ev({ data: { json() { return { title: 'Morning Review', body: 'Your morning review is ready',
                                       navigate: '/app/review/2026-10-04' }; } } }));
on.push(ev({ data: { json() { throw new Error('unreadable'); } } }));
on.push(ev({ data: null }));
for (const to of ['/approvals', 'https://example.org/x', '//example.org/x', '/\\example.org', 42, undefined]) {
  on.notificationclick(ev({ notification: { close() {}, data: { navigate: to } } }));
}
Promise.all(waits).then(() => {
  globalThis.open_now = true; const w2 = waits.length;
  on.notificationclick(ev({ notification: { close() {}, data: { navigate: '/approvals' } } }));
  return Promise.all(waits.slice(w2));
}).then(() => console.log(JSON.stringify({ shown, opened, navigated })));
""")
    shown = got.get("shown") or []
    ok("EVERY PUSH SHOWS A NOTIFICATION: the review's, one it can't read, one with nothing in it",
       len(shown) == 3 and shown[0] == ["Morning Review", "Your morning review is ready", "/app/review/2026-10-04"],
       got)
    ok("...and one it can't read still says something, under the box's name, opening the box",
       shown[1:] == [[look.base_name(), "Something new needs you.", "/"]] * 2 and look.base_name(), shown[1:])
    ok("A TAP OPENS A PAGE ON THIS BOX, NEVER ANOTHER SITE: Approvals opens, and every other target lands home",
       got.get("opened") == ["/approvals", "/", "/", "/", "/", "/"], got.get("opened"))
    ok("...and an open window is brought forward on the page, not a second one opened",
       got.get("navigated") == ["/approvals"] and len(got.get("opened") or []) == 6, got)
else:
    print("  --   no node on this machine, so the worker's behaviour is checked only for its text")
    ok("the worker shows a notification for every push", "showNotification(" in SW and "catch (err)" in SW)

print("\nwhere the Base Machine's worker is the box's: the Morning Review asks, nothing else does")
base_box()
review = page(client(owner), "/app/review")
ok("THE MORNING REVIEW CARRIES THE CARD, HIDDEN until it can work, with the client that subscribes",
   '<div class="card" id="ownbox-notify" hidden>' in review and ">Turn on notifications<" in review
   and "window.ownboxEnableNotifications = function" in review, review[:400])
ok("...and the press is what registers the worker, at the root",
   "register('/ui/sw.js', { scope: '/' })" in reviewmod.NOTIFY_JS)
ok("THE DASHBOARD CARRIES NO SCRIPT AT ALL", "<script" not in page(client(owner), "/dashboard"))
mobile = page(client(owner), "/settings/mobile")
ok("THE MOBILE APP PAGE NEVER ASKS: no button, no prompt (owner, 09-20)",
   "requestPermission" not in mobile and "ownbox-notify-yes" not in mobile and "register('/ui/sw.js'" not in mobile)
ok("...it points to the Morning Review, and says what this box notifies about",
   "on your Morning Review, the first time you open it from the app" in mobile
   and "notify you when your Morning Review is ready or something waits for your OK" in mobile
   and "when a customer writes" not in mobile, re.findall(r"notify you[^<.]*", mobile))
ok("...and its device check looks for the root worker, and sends a waiting mobile to the Review",
   "getRegistration(\"/\")" in mobile and "the box offers them on your Morning Review" in mobile
   and "offers them in Messages" not in mobile)
email = page(client(owner), "/settings/email")
ok("the Email page says 'alerts', never the inbox's", "Morning Review and alerts" in email
   and "inbox alerts" not in email, re.findall(r"Morning Review and [a-z ]+", email))
if NODE:
    for name, code in (("the review's client and card", push.CLIENT_JS + reviewmod.NOTIFY_JS),
                       ("the device check", push.device_js())):
        f = pathlib.Path(tempfile.mkdtemp()) / "x.js"
        f.write_text(code)
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        ok(f"{name}: valid JavaScript", r.returncode == 0, r.stderr[-400:])
    # THE CARD, DRIVEN: every state a mobile can be in, and what it does in each.
    HARNESS = r"""
const code = require('fs').readFileSync(process.argv[2], 'utf8');
const runs = [];
function run(perm, canNotify, hiddenBefore, press, saidNo) {
  const calls = [], els = {}, store = {};
  const el = (id) => (els[id] = els[id] || { id, hidden: id === 'ownbox-notify', disabled: false, style: {},
    textContent: '', handlers: {}, addEventListener(t, f) { this.handlers[t] = f; } });
  ['ownbox-notify', 'ownbox-notify-yes', 'ownbox-notify-no', 'ownbox-notify-said'].forEach(el);
  if (hiddenBefore) { store['ownbox.notify.hidden'] = '1'; }
  const g = { document: { getElementById: (id) => els[id] || null },
    navigator: { serviceWorker: { register(u, o) { calls.push('register ' + u + ' ' + o.scope); return Promise.resolve(); } } },
    Notification: { permission: perm },
    localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } } };
  g.window = { ownboxCanNotify: () => canNotify,
    ownboxEnableNotifications: () => { calls.push('enable'); return Promise.resolve({ ok: true }); },
    Notification: g.Notification };
  new Function('window', 'document', 'navigator', 'Notification', 'localStorage', code)(
    g.window, g.document, g.navigator, g.Notification, g.localStorage);
  const shown = !els['ownbox-notify'].hidden, before = calls.slice();
  if (press && els['ownbox-notify-yes'].handlers.click) { els['ownbox-notify-yes'].handlers.click(); }
  if (saidNo && els['ownbox-notify-no'].handlers.click) { els['ownbox-notify-no'].handlers.click(); }
  return new Promise((done) => setTimeout(() => done({ perm, canNotify, shown, before, calls,
    hiddenAfter: els['ownbox-notify'].hidden, stored: store['ownbox.notify.hidden'] || '',
    said: els['ownbox-notify-said'].textContent }), 5));
}
Promise.all([run('default', false), run('denied', true), run('granted', true), run('default', true),
             run('default', true, true), run('default', true, false, true), run('default', true, false, false, true)])
  .then((r) => console.log(JSON.stringify(r)));
"""
    got = run_js(reviewmod.NOTIFY_JS, HARNESS)
    if isinstance(got, dict):
        ok("the card could be driven", False, got)
    else:
        safari, denied, granted, fresh, notnow, pressed, saidno = got
        ok("A SAFARI TAB, NOT THE INSTALLED APP: the card stays hidden and nothing is asked",
           not safari["shown"] and safari["calls"] == [], safari)
        ok("PERMISSION REFUSED: hidden, and nothing is asked", not denied["shown"] and denied["calls"] == [], denied)
        ok("ALREADY ALLOWED: hidden, and it re-subscribes quietly (no prompt: permission is granted)",
           not granted["shown"] and granted["calls"] == ["register /ui/sw.js /", "enable"], granted)
        ok("NOT ASKED YET, IN THE APP: the card shows, and NOTHING IS ASKED UNTIL A PRESS",
           fresh["shown"] and fresh["calls"] == [], fresh)
        ok("'Not now' said before on this device: it stays hidden", not notnow["shown"] and notnow["calls"] == [], notnow)
        ok("THE PRESS REGISTERS THE WORKER AT THE ROOT, THEN ASKS, and says it worked",
           pressed["before"] == [] and pressed["calls"] == ["register /ui/sw.js /", "enable"]
           and pressed["said"] == "Done. This mobile will be notified.", pressed)
        ok("'Not now' hides it, and remembers on this device, asking nothing",
           saidno["hiddenAfter"] and saidno["stored"] == "1" and saidno["calls"] == [], saidno)

print("\nwhere a machine brings its own worker: nothing here changes")
inbox_box()
review = page(client(owner), "/app/review")
ok("the Morning Review draws no card and carries no prompt", "ownbox-notify" not in review
   and "requestPermission" not in review)
mobile = page(client(owner), "/settings/mobile")
ok("the Mobile App page still says 'when a customer writes', and the ask stays in Messages",
   "notify you when a customer writes" in mobile and "on the screen where you read your messages" in mobile
   and "requestPermission" not in mobile)
ok("...and the device check looks for that machine's worker", "getRegistration(\"/inbox/\")" in mobile)
ok("the Email page keeps 'inbox alerts'", "Morning Review and inbox alerts" in page(client(owner), "/settings/email"))

print("\none alert per event")
inbox_box()
OLD = "https://push.example/owner-ipad-before-scopes"
push.save_subscription(user_id=owner, endpoint=OLD, p256dh="p", auth="a")      # saved by a release before this one
ok("A SUBSCRIPTION SAVED BEFORE SCOPES WERE SENT keeps ringing (his /inbox one tonight)",
   [s["endpoint"] for s in push.subscriptions_for(owner)] == [OLD])
c = client(member)
ROOT_EP, INBOX_EP = "https://push.example/dana-base-app", "https://push.example/dana-inbox-app"
r = c.post("/settings/push/subscribe", json={"endpoint": ROOT_EP, "keys": {"p256dh": "p", "auth": "a"}, "scope": "/"})
ok("the button's subscription is stored, with the worker that holds it", r.status_code == 200
   and ROOT_EP in push._roots(), (r.status_code, r.get_json(silent=True)))
ok("...and while it is all she has, it rings", [s["endpoint"] for s in push.subscriptions_for(member)] == [ROOT_EP])
c.post("/settings/push/subscribe", json={"endpoint": INBOX_EP, "keys": {"p256dh": "p", "auth": "a"},
                                         "scope": "/inbox/"})
ok("ONCE SHE IS REACHED THROUGH THE MACHINE'S WORKER, THE BASE ONE STAYS QUIET: one alert per event, never two",
   [s["endpoint"] for s in push.subscriptions_for(member)] == [INBOX_EP], push.subscriptions_for(member))
ok("...and that never touches anyone else's", [s["endpoint"] for s in push.subscriptions_for(owner)] == [OLD])
push.forget(INBOX_EP)
ok("a machine subscription that is gone hands her back to the Base one",
   [s["endpoint"] for s in push.subscriptions_for(member)] == [ROOT_EP])
push.forget(ROOT_EP)
ok("a Base subscription that is gone leaves the list too", ROOT_EP not in push._roots())
ok("the client sends the worker's scope with every subscription",
   "scope = new URL(reg.scope).pathname" in push.CLIENT_JS and "scope: scope" in push.CLIENT_JS)

print("\nthe Unified Inbox's own worker")
inbox_app = ROOT / "marketing" / "customer_voice" / "app.py"
if inbox_app.exists():                       # a Lead or Content box ships no Inbox (test_recipe_ships re-runs this)
    text = inbox_app.read_text(encoding="utf-8")
    ok("a tap on an Approvals notification opens Approvals, not Messages", "to === '/approvals'" in text)
    init = (ROOT / "marketing" / "customer_voice" / "__init__.py").read_text(encoding="utf-8")
    ok("it says it brings its own worker, in the package both the web and the worker process import",
       'register_worker("/inbox/")' in init)
else:
    print("  --   this box ships no Unified Inbox")

push._MACHINE_WORKERS[:] = MACHINES
print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
