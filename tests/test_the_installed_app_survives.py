"""The inbox stays installable — asserted on the PAGES, not just the endpoints.

Owner, 2026-09-17: *"Yes PWA is absolutely crucial."* He said it in answer to my flag that joining
the inbox page to the rail chrome risks the installed phone app, so this suite is written BEFORE
that change and must be green on both sides of it.

WHAT WAS ALREADY GUARDED, and is not repeated here: `tests/test_customer_voice_app.py` proves the
four install files are SERVED, ungated, with the right content types, and that the manifest's
`display` is non-default. That is the vendor half.

WHAT NOTHING GUARDED IS THE PAGE'S OWN HEAD — and that is exactly what a chrome swap removes. A new
shell that renders a rail and a drawer but forgets `<link rel="manifest">` leaves every endpoint
answering 200 while the app stops being installable: no test goes red, no log line appears, and the
only symptom is that "Add to Home Screen" quietly becomes a bookmark. On iOS the apple-touch-icon
and the two apple meta tags are the same story — MDN says `Notification` does not even EXIST unless
the page is a home-screen app.

SO IT ASKS EVERY INBOX PAGE, not one. The shell is shared, but a chrome change tends to land on
the page being redesigned first, and a half-converted app is the state most likely to ship.

Run: python tests/test_the_installed_app_survives.py
"""
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="pwa-survives-")) / "box.db"
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ["DASH_TOKEN"] = "the-owners-own-password"

from core import state                                      # noqa: E402

state.init_db()

from marketing.customer_voice import app as _app            # noqa: E402

_failed = 0

# EVERY PAGE THE APP SERVES BEHIND THE DOOR. A thread is included because it is the deepest screen
# and the one a notification opens straight into — the exact path where losing the install matters.
PAGES = ("/inbox/", "/inbox/inbox", "/inbox/inbox/zc_seed", "/inbox/settings")


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _seed() -> None:
    from core import spaces
    from marketing.customer_voice.inbox import store
    sp = spaces.DEFAULT                     # what _space() resolves to on a bare host
    store.upsert_conversation(space=sp, zcid="zc_seed", platform="instagram",
                              participant="Dana Whitfield",
                              last_inbound_at="2026-09-17T12:00:00+00:00")
    store.record_message(space=sp, zcid="zc_seed", zmid="m_seed", direction="in",
                         sent_by="contact", body="Do you do same-day call-outs?")


def _client(theme: str = ""):
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    if theme:
        c.set_cookie(_app.THEME_COOKIE, theme)
    return c


def _head(path: str, theme: str = "") -> str:
    html_ = _client(theme).get(path).get_data(as_text=True)
    return html_.split("</head>", 1)[0]


def test_every_page_still_links_the_manifest():
    print("test_every_page_still_links_the_manifest")
    # THE ONE THAT TURNS AN APP BACK INTO A BOOKMARK, silently. No browser complains.
    for p in PAGES:
        head = _head(p)
        ok(f"{p} links the manifest",
           re.search(r'<link[^>]+rel="manifest"[^>]+href="/inbox/manifest\.webmanifest"', head)
           is not None, head[-200:])


def test_every_page_still_carries_the_ios_install_tags():
    print("test_every_page_still_carries_the_ios_install_tags")
    # APPLE READS ITS OWN META and always has. Without these the icon on the home screen is a
    # screenshot of the page and `Notification` does not exist at all.
    for p in PAGES:
        head = _head(p)
        ok(f"{p} has the apple touch icon",
           re.search(r'<link[^>]+rel="apple-touch-icon"', head) is not None)
        ok(f"{p} says it is web-app capable",
           'name="apple-mobile-web-app-capable" content="yes"' in head)
        ok(f"{p} sets the status bar style",
           'name="apple-mobile-web-app-status-bar-style"' in head)


def test_the_viewport_still_reaches_the_notch():
    print("test_the_viewport_still_reaches_the_notch")
    # `viewport-fit=cover` IS WHAT MAKES env(safe-area-inset-*) NON-ZERO. Drop it and every
    # safe-area rule in the stylesheet silently computes to 0 — the bar slides under the clock.
    for p in PAGES:
        ok(f"{p} asks for the full viewport", "viewport-fit=cover" in _head(p))


def test_the_status_bar_still_follows_the_theme():
    print("test_the_status_bar_still_follows_the_theme")
    # INSTALLED, iOS PAINTS BEHIND THE CLOCK WITH theme-color. Pinned to the wrong value it puts a
    # black band above a white app. Unstamped is WHITE on purpose (owner, 2026-09-16: white first),
    # so this asserts the value, not merely that the tag exists.
    light = _app._THEME_BG["light"]
    dark = _app._THEME_BG["dark"]
    for p in PAGES:
        ok(f"{p} unstamped is the light bar", f'name="theme-color" content="{light}"' in _head(p),
           _head(p)[-160:])
        ok(f"{p} follows a dark choice", f'name="theme-color" content="{dark}"'
           in _head(p, theme="dark"))


def test_his_theme_choice_still_reaches_the_html_element():
    print("test_his_theme_choice_still_reaches_the_html_element")
    # THE STAMP IS THE TOGGLE. Every dark rule hangs off `<html data-theme="dark">`; a chrome that
    # renders its own <html> without it turns the toggle into a control that cannot succeed.
    for p in PAGES:
        for choice, want in (("dark", 'data-theme="dark"'), ("light", 'data-theme="light"')):
            html_ = _client(choice).get(p).get_data(as_text=True)
            tag = re.search(r"<html[^>]*>", html_)
            ok(f"{p} stamps {choice}", bool(tag) and want in tag.group(0),
               tag.group(0) if tag else "no <html> tag")
        html_ = _client().get(p).get_data(as_text=True)
        tag = re.search(r"<html[^>]*>", html_).group(0)
        ok(f"{p} unchosen carries no stamp", "data-theme" not in tag, tag)


def test_the_page_still_asks_for_the_worker():
    print("test_the_page_still_asks_for_the_worker")
    # OFFLINE AND PUSH BOTH DIE WITHOUT THIS, and the page is where the ask lives.
    for p in PAGES:
        ok(f"{p} registers the service worker",
           "serviceWorker" in _client().get(p).get_data(as_text=True))


def test_the_install_files_are_still_served():
    print("test_the_install_files_are_still_served")
    # THE VENDOR HALF, kept here as one line per file so a failure says WHICH one. The detailed
    # contract (ungated, content types, non-default display) stays in test_customer_voice_app.
    c = _client()
    for path, kind in (("/inbox/manifest.webmanifest", "application/manifest+json"),
                       ("/inbox/sw.js", "javascript"),
                       ("/inbox/icon-192.png", "image/png")):
        r = c.get(path)
        ok(f"{path} -> 200 {kind}", r.status_code == 200
           and kind in r.headers.get("Content-Type", ""),
           f"{r.status_code} {r.headers.get('Content-Type','')}")
    m = json.loads(c.get("/inbox/manifest.webmanifest").get_data(as_text=True))
    ok("the app still starts inside /inbox/",
       m.get("start_url") == "/inbox/" and m.get("scope") == "/inbox/",
       f"{m.get('start_url')} {m.get('scope')}")


def test_the_installed_app_wears_the_box_ground():
    print("test_the_installed_app_wears_the_box_ground")
    # FOUND 2026-09-24 (OSDev5's pointer, the launch sweep): the manifest still carried #0b0d10,
    # so the installed app opened on a near-black splash under a black status bar, and the light
    # page's own theme-color was #eff2f4, the grey-blue of the inbox before box.css. The page is
    # the box's cream now; all three read the one value box.css paints the ground with.
    css = (pathlib.Path(__file__).resolve().parents[1] / "core" / "dash" / "static"
           / "box.css").read_text()
    mt = re.search(r"--ground:\s*(#[0-9a-fA-F]{6})", css)
    ground = mt.group(1).lower() if mt else "(no --ground in box.css)"
    m = json.loads(_client().get("/inbox/manifest.webmanifest").get_data(as_text=True))
    ok("the manifest's theme_color is the box's ground", str(m.get("theme_color")).lower() == ground,
       f"{m.get('theme_color')} vs {ground}")
    ok("...and so is its splash background", str(m.get("background_color")).lower() == ground,
       f"{m.get('background_color')} vs {ground}")
    ok("...and the light page's status bar", _app._THEME_BG["light"].lower() == ground,
       f"{_app._THEME_BG['light']} vs {ground}")


def test_the_suite_is_named_in_ci():
    print("test_the_suite_is_named_in_ci")
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("this suite runs in CI", "test_the_installed_app_survives \\" in wf)


def main() -> int:
    _seed()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print()
            fn()
    print("\n" + ("FAILED" if _failed else "PASS") + f" — {_failed} failure(s)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
