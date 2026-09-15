"""Customer Voice on a phone — stage 1: the door, the shell, the Today screen.

docs/SPEC_CUSTOMER_VOICE_PHONE_APP.md §6 stage 1. The stages that follow add a manifest, a service
worker and push; this one adds a real page behind a real door, and the door is what these tests
spend most of their effort on, because it is the half that can lose customer data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _client(token: str = ""):
    """A test client on an app whose config carries `token` as dash.app_token."""
    import core.config as cfg
    from core.dispatch import app
    base = cfg.get_config()
    d = dict(base.get("dash") or {})
    d["app_token"] = token
    merged = dict(base)
    merged["dash"] = d
    cfg.get_config = lambda m=merged: m
    return app, app.test_client()


def test_the_phone_app_answers_nobody_without_a_credential():
    """THERE IS NO PUBLIC TIER, and that is the one place this app does not copy the lead app.

    `machine_app` publishes a masked page because a list of companies drawn from public records is
    a sales surface — obscured, it can be shown to anybody. This app shows what a NAMED customer
    said to this business. There is no redacted version of that worth serving to a stranger, so an
    unauthenticated request gets the login page and not a thinner screen.

    Asserted as a property of the whole surface rather than of one route, because the failure mode
    is a route added later that forgets: the gate hangs on the blueprint so it cannot be forgotten,
    and this walks every /voice route there is to prove it.
    """
    import core.config as cfg
    real = cfg.get_config
    try:
        app, c = _client(token="")
        routes = sorted({str(r) for r in app.url_map.iter_rules() if str(r).startswith("/voice")})
        ok("the blueprint is mounted", bool(routes), str(routes))
        # THE FOUR INSTALL FILES ARE DELIBERATELY PUBLIC (stage 3) — a browser fetches a manifest
        # without cookies, so a gated one fails to install silently. They are excluded HERE and
        # covered HARDER in test_the_install_files_are_public_and_carry_nothing, which seeds real
        # customer data and proves none of it appears in any of them. Read from the module's own
        # allow-list rather than re-typed, so the two can never drift apart.
        from marketing.customer_voice.app import PUBLIC_PATHS
        # EACH ROUTE BY ITS OWN METHOD. A GET at a POST-only route is refused by Flask's ROUTING
        # with a 405, before any before_request runs — so walking everything with GET would have
        # "proved" the gate holds on a route the gate never saw. The beacon at /voice/installed is
        # exactly that route, and it is a WRITE, so it is the one most worth hitting properly.
        methods = {}
        for rule in app.url_map.iter_rules():
            if str(rule).startswith("/voice"):
                methods[str(rule)] = rule.methods - {"HEAD", "OPTIONS"}
        for path in routes:
            if path in PUBLIC_PATHS or "<" in path:
                continue
            verb = "POST" if "POST" in methods.get(path, {"GET"}) else "GET"
            r = c.post(path) if verb == "POST" else c.get(path)
            ok(f"{path} refuses an anonymous {verb}",
               r.status_code in (302, 303) and "/dash/login" in (r.headers.get("Location") or ""),
               f"{r.status_code} {r.headers.get('Location')}")
            body = r.get_data()
            ok(f"...and serves no content at {path}", len(body) < 400, f"{len(body)} bytes")
        ok("the exemption is exactly four files and no more",
           len(PUBLIC_PATHS) == 4, str(sorted(PUBLIC_PATHS)))
    finally:
        cfg.get_config = real


def test_the_dash_password_opens_it_and_today_renders():
    """THE OWNER'S OWN CREDENTIAL, checked first — the same order as `machine_app.unlocked()`.

    He already holds a dash session; asking about the app token first is how the lead app once let
    a signed-in owner look at his own obscured data.
    """
    import core.config as cfg
    from core.config import settings
    real = cfg.get_config
    try:
        app, c = _client(token="")
        r = c.post("/dash/login", data={"token": settings.dash_token})
        ok("the dash password is accepted", r.status_code in (302, 303), str(r.status_code))
        r2 = c.get("/voice/")
        ok("...and the phone app opens on it", r2.status_code == 200, str(r2.status_code))
        body = r2.get_data(as_text=True)
        ok("...on the Today screen", ">Today<" in body)
        ok("...wearing the box's own name, not ours",
           "Mavrick" not in body, "the brand is hard-coded somewhere")
        # A PAGE OF CUSTOMER MESSAGES IS NEVER A SEARCH RESULT, whoever holds the link.
        ok("...and asks not to be indexed, in the header",
           "noindex" in (r2.headers.get("X-Robots-Tag") or ""), r2.headers.get("X-Robots-Tag"))
        ok("...and in the markup", 'name="robots"' in body)
        # PHONE FIRST IS A CLAIM THE MARKUP HAS TO CARRY.
        ok("...and is laid out for a phone", "width=device-width" in body)
    finally:
        cfg.get_config = real


def test_the_token_opens_it_too_and_never_stays_in_the_address():
    """SAME LESSON AS #1074, and it has to be re-proven here because it is a different blueprint.

    gunicorn runs `--access-logfile -`, so the raw request line — query included — goes to the
    journal. A `?k=<token>` therefore lands in the log, the address bar and the history. Honoured
    once, banked in a cookie, then redirected to the same path with the token gone.
    """
    import core.config as cfg
    real = cfg.get_config
    try:
        app, c = _client(token="a-real-token")
        r = c.get("/voice/?k=a-real-token")
        ok("the token is honoured", r.status_code == 303, str(r.status_code))
        loc = r.headers.get("Location") or ""
        ok("...and the address it sends him to carries NO token",
           "k=" not in loc and "a-real-token" not in loc, loc)
        ok("...and the cookie was banked", "aios_app_k" in str(r.headers))
        r2 = c.get("/voice/")
        ok("...so the next page opens without it", r2.status_code == 200, str(r2.status_code))

        app, c2 = _client(token="a-real-token")
        r3 = c2.get("/voice/?k=wrong")
        ok("a wrong token opens nothing",
           r3.status_code in (302, 303) and "/dash/login" in (r3.headers.get("Location") or ""),
           f"{r3.status_code} {r3.headers.get('Location')}")
    finally:
        cfg.get_config = real


def test_a_blank_token_cannot_be_matched_by_a_blank_guess():
    """FAILS CLOSED, and this is the assertion that would have caught the worst possible bug here:
    an empty `app_token` compared against an empty query string is a string that equals itself.
    The lead app's `unlocked()` returns False on a blank token before it compares anything, and
    this gate has to do the same or a box that never set one is wide open."""
    import core.config as cfg
    real = cfg.get_config
    try:
        app, c = _client(token="")
        for guess in ("", "anything"):
            r = c.get(f"/voice/?k={guess}")
            ok(f"a blank configured token refuses {guess!r}",
               r.status_code in (302, 303) and "/dash/login" in (r.headers.get("Location") or ""),
               f"{r.status_code} {r.headers.get('Location')}")
    finally:
        cfg.get_config = real


def test_an_unreadable_report_is_a_thin_page_not_a_500():
    """THE FRONT PAGE OUTRANKS THE CAUSE. The report reads a dozen tables and a box mid-migration
    can be missing one — the lead app's front page 500'd on exactly that, on `daily_reports`, and
    a 500 on the screen he opens first reads as "the machine is broken", not "one table is late"."""
    import core.config as cfg
    from core.config import settings
    import marketing.customer_voice.report as rep
    real, real_report = cfg.get_config, rep.report
    try:
        app, c = _client(token="")
        c.post("/dash/login", data={"token": settings.dash_token})

        def boom(day):
            raise RuntimeError("no such table: voice_observations")

        rep.report = boom
        r = c.get("/voice/")
        ok("an unreadable report still renders a page", r.status_code == 200, str(r.status_code))
        body = r.get_data(as_text=True)
        ok("...that says so plainly", "could not be read" in body)
        ok("...and no stack trace reaches the screen", "Traceback" not in body)
    finally:
        rep.report, cfg.get_config = real_report, real


def test_one_clients_inbox_is_never_another_clients():
    """THE WORST BUG AVAILABLE IN THIS SURFACE, so it is pinned rather than reasoned about.

    `space` is the tenant boundary (rubric #1). The conversation id arrives from a URL, and
    `zernio_conversation_id` is vendor-unique — so a reader scoped on the id ALONE would make the
    boundary rest on a vendor's uniqueness guarantee and on nobody ever guessing an id. Both
    readers take two predicates instead, and this proves it from the outside: seeded in one Space,
    invisible from another, by both the list and the thread.

    The 404 rather than a 403 is deliberate too. A boundary that announces what exists on the
    other side of it is not a boundary.
    """
    from core import spaces
    from marketing.customer_voice.inbox import store

    store.upsert_conversation(space="tenant-a", zcid="zc-boundary",
                              participant="Belongs To A",
                              last_inbound_at="2026-09-12T10:00:00+00:00")
    store.record_message(space="tenant-a", zcid="zc-boundary", zmid="bm1",
                         direction="in", sent_by="contact", body="a-only-body")

    ok("the list shows it to its own Space",
       any(k["zernio_conversation_id"] == "zc-boundary"
           for k in store.list_conversations("tenant-a")))
    ok("...and NOT to another Space",
       not any(k["zernio_conversation_id"] == "zc-boundary"
               for k in store.list_conversations("tenant-b")), "cross-tenant leak in the list")
    ok("the thread reads for its own Space",
       [m["body"] for m in store.messages_for("tenant-a", "zc-boundary")] == ["a-only-body"])
    ok("...and is EMPTY for another Space, id known and all",
       store.messages_for("tenant-b", "zc-boundary") == [],
       "cross-tenant leak in the thread")
    ok("...and the default Space cannot see it either",
       store.messages_for(spaces.DEFAULT, "zc-boundary") == [])


def test_the_inbox_screen_shows_the_conversation_and_says_who_spoke():
    """THE SCREEN THE PRODUCT IS FOR. Today is the number; this is the thing a person came to read.

    WHO SAID IT, ON EVERY LINE. `sent_by` is contact | ai | human, and on a screen where the
    machine may have answered on his behalf, not saying which is which is the one thing that would
    make him distrust the whole surface.

    AND NEWEST INBOUND FIRST, not newest `updated_at`: that column moves when WE touch a row — an
    opener claim, an opt-out, a watermark — so ordering on it floats a conversation up because the
    machine did something rather than because a person said something.
    """
    import core.config as cfg
    from core.config import settings
    from core import spaces
    from marketing.customer_voice.inbox import store
    real = cfg.get_config
    try:
        sp = spaces.DEFAULT
        store.upsert_conversation(space=sp, zcid="zc-old", participant="Older Person",
                                  last_inbound_at="2026-09-10T09:00:00+00:00")
        store.upsert_conversation(space=sp, zcid="zc-new", participant="Dana Whitfield",
                                  ad_title="Spring offer",
                                  last_inbound_at="2026-09-12T17:02:00+00:00")
        store.record_message(space=sp, zcid="zc-new", zmid="n1", direction="in",
                             sent_by="contact", body="Any openings Friday?")
        store.record_message(space=sp, zcid="zc-new", zmid="n2", direction="out",
                             sent_by="ai", body="2pm or 4pm - which suits?")

        app, c = _client(token="")
        c.post("/dash/login", data={"token": settings.dash_token})
        body = c.get("/voice/inbox").get_data(as_text=True)
        ok("the person's name is on the row", "Dana Whitfield" in body)
        ok("...with how many messages", "2 messages" in body)
        ok("...and where they came from", "Spring offer" in body)
        ok("newest inbound first, not newest touched",
           body.index("Dana Whitfield") < body.index("Older Person"), "ordering is wrong")
        ok("the whole row is the tap target, not a word inside it",
           '<a class="conv" href="/voice/inbox/zc-new"' in body)

        thread = c.get("/voice/inbox/zc-new").get_data(as_text=True)
        ok("the thread carries both messages",
           "Any openings Friday?" in thread and "which suits" in thread)
        ok("...and says which of them the MACHINE sent", "the machine" in thread)
        ok("...and which of them they sent", ">them ·" in thread or "them ·" in thread)
        ok("...oldest first, the way a thread is read",
           thread.index("Any openings Friday?") < thread.index("which suits"))
        ok("...with a way back to the list", 'href="/voice/inbox"' in thread)

        # OPTED OUT IS SAID ON THE ROW, because replying to someone who opted out is the one
        # mistake this screen can help him make.
        store.set_opted_out(sp, "zc-new")
        again = c.get("/voice/inbox").get_data(as_text=True)
        ok("an opted-out conversation says so on the list", "opted out" in again)
        t2 = c.get("/voice/inbox/zc-new").get_data(as_text=True)
        ok("...and on the conversation itself", "has opted out" in t2)
    finally:
        cfg.get_config = real


def test_a_quiet_inbox_says_so_rather_than_looking_broken():
    """An empty screen reads as a broken app — the defect the lead app spent two days removing from
    every branch. A business with no messages yet is the normal first week, not a fault."""
    import core.config as cfg
    from core.config import settings
    from marketing.customer_voice.inbox import store
    real, real_list = cfg.get_config, store.list_conversations
    try:
        app, c = _client(token="")
        c.post("/dash/login", data={"token": settings.dash_token})
        store.list_conversations = lambda space, **kw: []
        body = c.get("/voice/inbox").get_data(as_text=True)
        ok("a quiet inbox says it is quiet", "No conversations yet" in body)
        ok("...and does not claim a fault", "could not be read" not in body)

        def boom(space, **kw):
            raise RuntimeError("no such table: inbox_conversations")

        store.list_conversations = boom
        r = c.get("/voice/inbox")
        ok("an unreadable inbox is a page, not a 500", r.status_code == 200, str(r.status_code))
        b2 = r.get_data(as_text=True)
        ok("...that says which of the two it is", "could not be read" in b2)
        ok("...and never shows a stack trace", "Traceback" not in b2)
    finally:
        store.list_conversations, cfg.get_config = real_list, real


def test_the_install_files_are_public_and_carry_nothing():
    """THE ONE HOLE IN THE GATE, so it is the most-tested thing in this file.

    A browser fetches a manifest WITHOUT cookies unless the link says otherwise, so a gated
    manifest fails to install and says nothing a person could act on — the silent failure this
    whole module is written against. Four paths are therefore exempt, by an explicit allow-list
    rather than a prefix, because a prefix rule is how an exemption meant for four files ends up
    covering a route somebody adds under the same folder next year.

    THE EXEMPTION IS ONLY SAFE IF THOSE FOUR CARRY NOTHING, and that is asserted rather than
    reasoned about: seeded real customer data first, then checked it appears in none of them.
    """
    import json

    from core import spaces
    from marketing.customer_voice.inbox import store
    import core.config as cfg
    real = cfg.get_config
    try:
        sp = spaces.DEFAULT
        store.upsert_conversation(space=sp, zcid="zc-secret",
                                  participant="Nadia Solberg-Testcase",
                                  last_inbound_at="2026-09-12T10:00:00+00:00")
        store.record_message(space=sp, zcid="zc-secret", zmid="s1", direction="in",
                             sent_by="contact", body="my private message body")

        app_, c = _client(token="")
        for path, kind in (("/voice/manifest.webmanifest", "application/manifest+json"),
                           ("/voice/icon-192.png", "image/png"),
                           ("/voice/icon-512.png", "image/png"),
                           ("/voice/sw.js", "application/javascript")):
            r = c.get(path)
            ok(f"{path} answers a browser with no credential", r.status_code == 200,
               str(r.status_code))
            ok(f"...as {kind}", kind in (r.headers.get("Content-Type") or ""),
               r.headers.get("Content-Type"))
            body = r.get_data()
            for secret in (b"Nadia", b"Solberg", b"private message", b"zc-secret"):
                ok(f"...and carries no {secret.decode()!r}", secret not in body,
                   "customer data in a public file")

        # AND NOTHING ELSE GOT OUT WITH THEM. The allow-list is exact, so every other route on
        # this blueprint must still refuse — walked, not assumed.
        exempt = {"/voice/manifest.webmanifest", "/voice/icon-192.png", "/voice/icon-512.png",
                  "/voice/sw.js"}
        for rule in sorted({str(r) for r in app_.url_map.iter_rules()
                            if str(r).startswith("/voice")}):
            if rule in exempt or "<" in rule:
                continue
            r = c.get(rule)
            ok(f"{rule} is still shut to a stranger",
               r.status_code in (302, 303, 405) and (
                   r.status_code == 405 or "/dash/login" in (r.headers.get("Location") or "")),
               f"{r.status_code} {r.headers.get('Location')}")

        # THE MANIFEST SAYS THE ONE THING iOS REQUIRES. MDN: `Notification` is undefined on iOS
        # unless the page is a home-screen app AND the manifest has a non-default `display`.
        # Without this exact value there is no push on an iPhone, ever — so it is pinned by value.
        m = json.loads(c.get("/voice/manifest.webmanifest").get_data(as_text=True))
        ok("display is standalone, which is what makes push possible at all",
           m.get("display") == "standalone", str(m.get("display")))
        ok("...start_url points back through the gated door", m.get("start_url") == "/voice/")
        ok("...and it names both sizes Chrome asks for",
           sorted(i["sizes"] for i in m["icons"]) == ["192x192", "512x512"],
           str([i["sizes"] for i in m["icons"]]))
        ok("...maskable, so an Android launcher crops our safe zone and not a square",
           all("maskable" in i.get("purpose", "") for i in m["icons"]))
    finally:
        cfg.get_config = real


def test_the_icons_are_real_pngs_drawn_without_a_dependency():
    """PILLOW IS NOT ON THIS BOX. `test_box_boots` fails today on a missing PIL, so importing it
    here would take the whole app down on exactly the machine this ships to. The bytes are written
    by hand, and "written by hand" is worth verifying rather than trusting — a malformed PNG is a
    broken icon on a home screen, which is the one place a person cannot miss it."""
    from marketing.customer_voice import app as _app
    import struct
    for size in (192, 512):
        b = _app._png(size)
        ok(f"{size}px has the PNG signature", b[:8] == b"\x89PNG\r\n\x1a\n")
        ok(f"...and ends with IEND", b[-8:-4] == b"IEND")
        w, h = struct.unpack(">II", b[16:24])
        ok(f"...and really is {size}x{size}", (w, h) == (size, size), f"{w}x{h}")
        ok("...and is not a stub", len(b) > 200, f"{len(b)} bytes")
    # DRAWN ONCE PER PROCESS. `/voice/icon-512.png` is in PUBLIC_PATHS, so it is the one route on
    # this app a stranger can hit without a credential, and it is 262k iterations of a Python loop
    # on a one-vCPU box. Without the cache a loop of requests buys our CPU for free. Asserted on
    # the function rather than by timing it, because a timing test on a shared runner is a flake.
    ok("the drawing is cached", hasattr(_app._png, "cache_info"), str(type(_app._png)))
    ok("...and a repeat call hands back the same object, not a redraw",
       _app._png(192) is _app._png(192))


def test_the_worker_can_never_push_silently():
    """APPLE REVOKES THE PERMISSION FOR IT. "Safari doesn't support invisible push
    notifications... If you don't [show one], Safari revokes the push notification permission for
    your site." One silent push costs the whole channel for that device, permanently — so the
    handler must show a notification on EVERY path, including the one where the payload cannot be
    parsed, which is exactly the path a careless implementation leaves out."""
    from marketing.customer_voice import app as _app
    js = _app.SW_JS
    ok("the push handler shows a notification", "showNotification" in js)
    ok("...and has a fallback title for an unreadable payload", "|| 'Unified Inbox'" in js)
    ok("...and a fallback body", "|| 'Something new came in.'" in js)
    ok("...with the parse wrapped so a bad payload cannot skip the notification",
       "try {" in js and "catch" in js)
    ok("a click takes him to the inbox", "notificationclick" in js and "/voice/inbox" in js)
    # AND ONLY EVER INSIDE OUR OWN APP. `navigate` is read off the payload, and the payload is
    # ours and encrypted to the subscription — so this is depth, not a hole being plugged. It is
    # asserted anyway because an untested guard is one a later simplification deletes without
    # anything going red, and because `//evil.com/x` is not caught by a check for "starts with /".
    ok("the click target is clamped to /voice/", "indexOf('/voice/') !== 0" in js)
    ok("...and anything else falls back to the inbox rather than opening",
       "to = '/voice/inbox'" in js.split("indexOf('/voice/') !== 0", 1)[-1][:120])
    # SCOPE IS THE SILENT ONE. MDN: a worker cannot claim a scope broader than where it is served.
    # It registers, reports success, and intercepts nothing if this is wrong.
    ok("the registration states its scope explicitly", "scope: '/voice/'" in _app.JS)
    ok("...and the shell asks for the worker at all", "serviceWorker" in _app.JS)


def test_the_phone_app_is_self_contained():
    """SPEC §1.2 AND INVARIANT 7. A clone that installs Customer Voice and not the Lead Machine
    must get a working phone app, so nothing here may import `machine_app` — and a change to the
    lead app's chrome must not be able to restyle this one, which is the same rule stated from the
    other side. `sites/` is the other half: those bytes are not on the box.

    Read from the AST rather than by grepping the text, because a mention inside a docstring is not
    an import and a test that cannot tell the difference gets deleted the first time it cries wolf.
    """
    import ast
    import pathlib
    src = pathlib.Path("marketing/customer_voice/app.py").read_text()
    tree = ast.parse(src)
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    bad = sorted(m for m in imported if "machine_app" in m or m.startswith("sites"))
    ok("it imports nothing from the lead app or from sites/", not bad, str(bad))
    # AND IT STILL IMPORTS core.dash — so the test cannot pass by the module importing nothing at
    # all, which is how an absence-only guard goes vacuous the moment the file is refactored.
    ok("...while still going through core for the door and the brand",
       any(m == "core.dash" or m.startswith("core.") for m in imported), str(sorted(imported)))


def test_ci_actually_runs_this_file():
    """A SUITE CI NEVER RUNS IS WORSE THAN NO SUITE, because it reports green. The workflow keeps a
    HAND-MAINTAINED list of suite names — the same silent-skip hazard as `test_machine_app`'s
    runner tuple, one level up — so this file has to be named in it, and this is the assertion that
    notices if a rename ever separates the two."""
    import pathlib
    wf = pathlib.Path(".github/workflows/tests.yml").read_text()
    me = pathlib.Path(__file__).stem
    ok(f"{me} is in the workflow's suite list", me in wf,
       "CI would skip this file and still print green")


if __name__ == "__main__":
    for fn in (test_the_phone_app_answers_nobody_without_a_credential,
               test_the_dash_password_opens_it_and_today_renders,
               test_the_token_opens_it_too_and_never_stays_in_the_address,
               test_a_blank_token_cannot_be_matched_by_a_blank_guess,
               test_an_unreadable_report_is_a_thin_page_not_a_500,
               test_one_clients_inbox_is_never_another_clients,
               test_the_inbox_screen_shows_the_conversation_and_says_who_spoke,
               test_a_quiet_inbox_says_so_rather_than_looking_broken,
               test_the_install_files_are_public_and_carry_nothing,
               test_the_icons_are_real_pngs_drawn_without_a_dependency,
               test_the_worker_can_never_push_silently,
               test_the_phone_app_is_self_contained,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
