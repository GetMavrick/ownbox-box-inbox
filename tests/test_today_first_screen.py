"""The first screen a buyer sees — and on a box nothing can reach, the one thing he can do there.

THE NAME IS HISTORY, THE PROMISE IS NOT. This file guarded "Today", the inbox's first screen, from
2026-09-16: a box nothing could reach greeted its owner with a website uptime report, and the one
instruction on it was not a link. On 2026-09-29 the owner made Messages the first screen (IA
decision D1, docs/SCOPE_APP_IA.md: the day's summary is the Morning Review's, and the inbox opens
on its messages), and Today's address now forwards there. The promises this file kept move with
the screen they are about:

  1. the app's first address lands on the first screen;
  2. on a box nothing can reach, that screen says so, names the one thing to connect, and offers
     exactly one button, to a route THIS box serves — never a poll that will never find anything;
  3. on a box that is listening, it is the list, with no set-up standing in front of it.

Run: python tests/test_today_first_screen.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="first-screen-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _signed_in():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return app, c


def _text(html_: str) -> str:
    """What a person READS. Markup hides a duplicate and shows a sentence that is not there."""
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _with(*, listening: bool, conversations: list):
    """Render the first screen on a box we have fully described, and hand back (app, html)."""
    from core import box_secrets, spaces
    from marketing.customer_voice.inbox import store
    keep = (box_secrets.email_credential, spaces.all_spaces, store.list_conversations)
    try:
        box_secrets.email_credential = (lambda: {"user": "a@b.c", "password": "x"}) if listening \
            else (lambda: {})
        spaces.all_spaces = lambda: [{"name": "default"}]
        store.list_conversations = lambda space, **kw: list(conversations)
        app, c = _signed_in()
        return app, c.get("/inbox/inbox").get_data(as_text=True)
    finally:
        box_secrets.email_credential, spaces.all_spaces, store.list_conversations = keep


print("test_the_first_address_lands_on_the_first_screen")
_app, _c = _signed_in()
_r = _c.get("/inbox/")
ok("the app's first address forwards to its messages",
   _r.status_code == 302 and (_r.headers.get("Location") or "").endswith("/inbox/inbox"),
   f"{_r.status_code} {_r.headers.get('Location')}")

print("\ntest_a_box_nothing_can_reach_says_so_and_offers_the_one_way_in")
_app, _body = _with(listening=False, conversations=[])
_words = _text(_body.split("</style>", 1)[-1])
ok("it says plainly that nothing is connected", "nothing is connected" in _words.lower(), _words[:240])
ok("...and names the one thing to connect", "Connect your inbox" in _words, _words[:240])
# THE PROMISE THE EMPTY INBOX MUST NOT MAKE ON A DEAF BOX: "the first person who messages you
# appears here" — nobody is coming, because nothing is listening.
ok("...and promises no message that cannot arrive",
   "The first person who messages you appears here" not in _words, _words[:240])
_btns = re.findall(r'<a class="btn" href="([^"?#]+)', _body)
ok("there is exactly one button", len(_btns) == 1, str(_btns))
_routes = {str(r) for r in _app.url_map.iter_rules()}
ok("...pointing only at a route THIS box serves", _btns and all(b in _routes for b in _btns),
   f"{_btns} vs the url_map")

print("\ntest_a_listening_box_gets_its_list")
_conv = {"zcid": "zc_first", "platform": "email", "participant": "Hana Okoye",
         "last_inbound_at": "2026-09-29T08:00:00+00:00", "last_body": "Do you open on Saturdays?",
         "unread": 1}
_app, _body = _with(listening=True, conversations=[_conv])
_words = _text(_body.split("</style>", 1)[-1])
ok("the list is the screen", "Hana Okoye" in _words, _words[:240])
ok("...with no set-up standing in front of it", "nothing is connected" not in _words.lower())

print("\n— and this file cannot silently fall out of CI —")
_here = pathlib.Path(__file__).resolve().parents[1]
if not (_here / ".github").is_dir():
    print("  --   not the repo — a buyer's box has no CI manifest to be named in")
else:
    ok("registered in the suite list",
       "test_today_first_screen" in (_here / ".github/workflows/tests.yml").read_text())

print(f"\n{'FAILED — ' + str(_failed) + ' failure(s)' if _failed else 'all checks passed'}")
sys.exit(1 if _failed else 0)
