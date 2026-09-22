"""Something actually asks for notification permission — the piece that was never wired.

WHAT WAS TRUE BEFORE THIS, measured on main 2026-09-22 after the owner said *"it still hasn't
done what you're saying it will do"*:

    grep -rn "ownboxEnableNotifications" --include=*.py .
    marketing/customer_voice/app.py:929:  window.ownboxEnableNotifications = function () {

DEFINED ONCE, CALLED BY NOTHING. Not on a screen, not on a timer, not behind a button. So
`pushManager.subscribe` never ran on any box we have sold, no endpoint was ever stored,
`push.subscriptions_for()` returned empty forever, and every notification the poller tried to
send was a loop over zero rows that reported success.

EVERYTHING AROUND IT WAS FINISHED: a VAPID identity, RFC 8291 encryption written against the
RFCs rather than a library, a service worker, subscription storage, `notify._ring` wired into
the poller's pass. A complete channel with no front door — the same shape as the connector
before #1409 and `/deploy` before #1414. Built, correct, unreachable.

THIS SUITE'S JOB IS THAT ONE FACT. Every other assertion here is about the owner's ruling on
WHEN; the assertion that matters is that a rendered screen contains a CALL and not just a
definition, because that is the failure that shipped and it is invisible to every test that
only checks the function exists.

Run: python tests/test_the_box_asks_before_it_rings.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "ring.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

from core import state                                                   # noqa: E402

state.init_db()

from core import dash, push                                              # noqa: E402
from core.dispatch import app                                            # noqa: E402
# THE MACHINE, NAMED AT COLUMN 0 SO THE EXPORTER CAN SEE IT. This suite is about the inbox's own
# screen, so it must ship only where that machine does — `export_box.sh` decides by scanning for
# a literal `marketing.<pkg>` import, and a transitive one through `core.dispatch` is invisible
# to that scan. A suite that reached this screen transitively shipped into a Lead box and failed
# there on 13 assertions (2026-09-18).
from marketing.customer_voice import app as _inbox                       # noqa: E402
from marketing.customer_voice.inbox import store                         # noqa: E402

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


def inbox_html() -> str:
    return owner_client().get("/inbox/inbox").get_data(as_text=True)


# ── 1. never on first load, which is the owner's ruling and an irreversible cost ─────────
print("\ntest_an_empty_inbox_never_asks")

# OWNER, 2026-09-20: ask "after the buyer has seen their first real message, never on first
# load." That is not a preference — an iOS denial can only be undone in Settings, which nobody
# does, so a prompt fired at an empty box spends the whole channel, permanently, on somebody
# with nothing to be notified about yet.
empty = inbox_html()
ok("a box with no conversations offers nothing", "ownbox-ring" not in empty)
ok("...and does not even load the client that could ask",
   "ownboxEnableNotifications" not in empty)


# ── 2. once a real message exists, it offers — and the offer is a CALL, not a definition ─
print("\ntest_a_real_message_earns_the_ask")

with app.test_request_context("/inbox/inbox"):
    _space = _inbox._space()
store.upsert_conversation(space=_space, zcid="zc-ring-1", platform="email",
                          last_inbound_at=datetime.now(timezone.utc).isoformat())
live = inbox_html()
ok("the conversation is on the screen", "zc-ring-1" in live)
ok("...and the offer is there with it", 'id="ownbox-ring"' in live)

# THE ASSERTION THIS FILE EXISTS FOR. `ownboxEnableNotifications` was defined on every box and
# called from nowhere, which no test caught because the definition was always present. A
# definition is `window.ownboxEnableNotifications =`; a CALL is the name followed by `(`.
calls = live.count("ownboxEnableNotifications(")
ok("SOMETHING CALLS IT — the bug this suite exists for", calls >= 1,
   "ownboxEnableNotifications is defined and called by nothing, which is how push shipped inert")
ok("...and the client it calls is actually loaded on this page",
   "window.ownboxEnableNotifications = function" in live)
ok("...loaded from core, so it fetches core's endpoints",
   "/settings/push/key" in live and "/settings/push/subscribe" in live)
ok("...and there is exactly one copy of the client on the page",
   live.count("window.ownboxEnableNotifications = function") == 1,
   str(live.count("window.ownboxEnableNotifications = function")))


# ── 3. the page asks nothing by itself; a person presses something ───────────────────────
print("\ntest_the_page_never_fires_the_prompt_on_its_own")

ok("the offer starts hidden", 'id="ownbox-ring" hidden' in live)
ok("there is a button to accept", 'id="ownbox-ring-yes"' in live)
ok("...and a way to decline that is not a dead end", 'id="ownbox-ring-no"' in live)
# `requestPermission` APPEARS EXACTLY ONCE, inside the client's own function. A second occurrence
# means somebody wired a page to call it directly, which is the first-load prompt the owner ruled
# against arriving by another door.
ok("requestPermission is reachable only through the client",
   live.count("requestPermission") == 1, str(live.count("requestPermission")))


# ── 4. the three states, and only one of them asks ───────────────────────────────────────
print("\ntest_it_respects_an_answer_already_given")

js = _inbox._RING_JS
ok("a refusal is never asked again — the browser will not allow it anyway",
   "'denied'" in js and "return" in js.split("'denied'")[1][:40], js[:0] or "no denied branch")
# ALREADY GRANTED IS NOT A PROMPT. Re-subscribing repairs the case core.push.CLIENT_JS warns
# about: iOS drops a subscription after long disuse and the box keeps pushing at a dead endpoint.
ok("an existing yes re-subscribes quietly rather than asking again",
   "'granted'" in js and "ownboxEnableNotifications()" in js.split("'granted'")[1][:400])
ok("...and a Safari tab is never offered it, because it could not deliver",
   "ownboxCanBeRung()" in js)
ok("'not now' is remembered per viewer, and the page still renders without storage",
   "localStorage" in js and "catch" in js)
ok("a failure tells the person the reason rather than 'something went wrong'",
   ".why" in js)


# ── 5. the box can still draw this screen when push is missing entirely ──────────────────
print("\ntest_a_box_that_cannot_ring_still_serves_its_inbox")

# A SCREEN OUTRANKS A FEATURE. A box whose release predates `core.push` must still show somebody
# their messages; an inbox that 500s because notifications are missing is a worse box than one
# that cannot ring.
_real = push.CLIENT_JS
try:
    del push.CLIENT_JS
    ok("the inbox still answers with no client to load",
       owner_client().get("/inbox/inbox").status_code == 200)
finally:
    push.CLIENT_JS = _real
ok("...and serves it again once push is back", owner_client().get("/inbox/inbox").status_code == 200)


# ── 6. and this file cannot silently fall out of CI ──────────────────────────────────────
print("\ntest_ci_actually_runs_this_file")

import pathlib                                                           # noqa: E402

_wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml")
if _wf.exists():
    ok("registered in the suite list", "test_the_box_asks_before_it_rings" in _wf.read_text())
    import yaml                                                          # noqa: E402
    yaml.safe_load(_wf.read_text())
    ok("...and the workflow file is still valid YAML", True)

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
