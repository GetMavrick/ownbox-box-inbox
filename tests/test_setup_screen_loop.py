"""The set-up screen — every step the buyer owns, in the owner's order, as a GUIDE.

SET-UP HOLDS NO SETTINGS (owner, 2026-09-24, relayed by OSDev1): "there's only one place to add a
key or change a setting", and "I don't think a setup tab should have actual settings on it … set up
tab should be more like a guide or a wizard." docs/SCOPE_ONE_PLACE_PER_SETTING.md has the research
behind the guide. So this suite now holds the guide's contract: every machine step as a row, in
order, each linking to its one home with a return trip, status read from the box; and the forms,
refusals and redirects it used to check on this screen are checked on those homes instead.


Assigned by OSDev1 (2026-09-16): render `box_secrets.setup_state()` as a LOOP — Gmail first,
Zernio second, the link out drawn disabled until the key is in — rather than three bespoke flows
that drift apart. The step data is his, from #1250, carried word for word; this suite holds the
SCREEN to the contract rather than re-asserting his copy.

THE TWO ASSERTIONS WITH TEETH:

  * THE RENDERER KNOWS NO STEP BY NAME. A loop that contains `if key == "zernio"` is three bespoke
    flows wearing a for-statement, and it fails the same way they would — the third credential
    gets its own arm, and the arms drift. Asserted against the source of the render helpers.
  * EVERY STATUS THE CONTRACT CAN PRODUCE HAS A SENTENCE. #1250's docstring listed four; the
    credential stores on main produce five (`payment_required` arrives from `zernio_state()` and
    is the one a buyer can actually fix). A status the screen was told could not happen is how an
    unhandled state becomes a blank row in front of a paying customer.

Run: python tests/test_setup_screen_loop.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="setup-loop-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                    # noqa: E402

state.init_db()

from core import box_secrets as bs                        # noqa: E402


# ── NO TEST OPENS A SOCKET TO GOOGLE ────────────────────────────────────────────────────────────
# #1257 makes `put_email` VERIFY before it believes a credential: it signs in, SELECTs read-only,
# and refuses a well-formed dead password — which is right, and is the fix for the commonest wrong
# paste. It also means every `put_email` in a test opens a real IMAP connection from the runner to
# imap.gmail.com, is refused, and turns a suite red for a reason that has nothing to do with the
# suite. OSDev1 caught it by dry-merging and running these from inside an exported box.
#
# THE SEAM IS #1257's OWN, not one invented here: `box_secrets._mailbox_verify(host, user,
# password) -> (ok, status, detail)` exists precisely so the network call has one place to be
# replaced. Stubbed at module scope so it covers the direct calls AND the ones that arrive through
# a screen POST, which is the half a per-test stub would miss.
#
# `setattr`, NOT an assignment to a name that must already exist: this file has to pass BEFORE
# #1257 lands as well as after, and on today's main there is no such attribute.
#
# AND THE SAME FOR SENDING. `put_email` now also asks whether that password may SEND (SMTP), which
# is a second socket for every call here — 20 seconds of it in a runner with no egress, per test.
# `_mailbox_verify_send` is the parallel seam, and it is stubbed on the same line of reasoning.
def _never_calls_google() -> None:
    try:
        from core import box_secrets as _bs
        setattr(_bs, "_mailbox_verify", lambda host, user, password: (True, "connected", ""))
        setattr(_bs, "_mailbox_verify_send", lambda host, user, password: (True, "can_send", ""))
    except Exception:                            # noqa: BLE001 — a stub that cannot be set is not
        pass                                     # a reason to fail every test in the file


_never_calls_google()


_failed = 0
GOOD_PW = "abcd efgh ijkl mnop"
FLAT_PW = "abcdefghijklmnop"


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _c():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return app, c


def _text(html_: str) -> str:
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _reset():
    for n in (bs.EMAIL, bs.EMAIL_STATUS, bs.EMAIL_DETAIL,
              bs.ZERNIO, bs.ZERNIO_STATUS, bs.ZERNIO_DETAIL):
        bs.clear(n)


def test_the_screen_exists_and_is_shut_to_a_stranger():
    from core.dispatch import app
    ok("the box serves a set-up screen",
       "/inbox/setup" in {str(r) for r in app.url_map.iter_rules()})
    anon = app.test_client()
    for verb in ("get", "post"):
        r = getattr(anon, verb)("/inbox/setup")
        ok(f"an anonymous {verb.upper()} is refused",
           r.status_code in (302, 303) and "/dash/login" in (r.headers.get("Location") or ""),
           f"{r.status_code} {r.headers.get('Location')}")
        ok(f"...and serves no content on {verb.upper()}", len(r.get_data()) < 400)


def test_it_renders_the_contract_in_the_contract_s_order():
    """GMAIL FIRST, ZERNIO SECOND — and from the DATA, not from this screen's opinion.

    THIS ASSERTION USED TO SAY *EVERY* STEP AND IT WAS RIGHT TO, until 2026-09-22. What it
    encoded is that this screen is the box's set-up CONTRACT made visible rather than the inbox's
    own wizard — so it could not show a subset of the contract, and OSDev5 correctly reverted a
    change that made it try (`docs/SCOPE_BOX_STEPS_NEED_CORE_DOORS.md`).

    THE CONTRACT SPLIT INSTEAD OF THE SCREEN LYING ABOUT IT. Owner, 2026-09-22: *"Step four and
    five should not be in this wizard any longer"* — the AI account, the phone and the AI
    coworkers are the box's, finished in the box's own drawer on core screens. So a step now
    declares which surface it belongs to and this screen renders the MACHINE's.

    THE TEETH ARE KEPT, WHICH IS THE WHOLE POINT OF THIS DOCSTRING. It asserts a DEFINED SET —
    every machine step in the contract, in contract order — not "whatever happens to render".
    Deleting `surface` from a step, or filtering one out of the wizard for a reason nobody wrote
    down, still turns this red.
    """
    _reset()
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    machine_steps = [s for s in bs.SETUP_STEPS if bs.surface_of(s) == bs.SURFACE_MACHINE]
    box_steps = [s for s in bs.SETUP_STEPS if bs.surface_of(s) == bs.SURFACE_BOX]
    ok("the contract still has a machine side and a box side",
       bool(machine_steps) and bool(box_steps),
       f"machine={[s['key'] for s in machine_steps]} box={[s['key'] for s in box_steps]}")
    titles = [s["title"] for s in machine_steps]
    ok("every MACHINE step in the contract is on the screen",
       all(t in words for t in titles), str(titles))
    # `.find`, NEVER `.index`. A missing substring RAISES and a raise ends the whole file — it
    # did, on the probe, and every test below this line silently never ran. Third time tonight I
    # have written `.index` in a test; it is a habit, so this file has none left.
    seen = [words.find(t) for t in titles]
    ok("...in the contract's order, not alphabetical or arbitrary",
       all(i >= 0 for i in seen) and seen == sorted(seen), str(seen))
    html_ = c.get("/inbox/setup").get_data(as_text=True)
    ok("...numbered, so a person knows how many there are",
       re.search(r'class="gn"[^>]*>1<', html_) is not None)
    from marketing.customer_voice import app as voice
    for s in machine_steps:
        href = voice._setup_home(s)[0]
        ok(f"{s['key']}: its whole row links to its one home, with the way back",
           re.search(rf'<a class="grow[^"]*" id="{s["key"]}" href="{re.escape(href)}\?from=setup"',
                     html_) is not None, href)
        # THE INSTRUCTIONS LIVE ON THE HOME NOW, which is where a person reads them while doing it.
        home = _text(c.get(href).get_data(as_text=True))
        ok(f"{s['key']}: its instructions are on its home",
           all(step[:30] in home for step in s["steps"]), s["steps"][0][:40])
        ok(f"{s['key']}: ...and its note", s["note"][:40] in home, s["note"][:40])
    ok("SET-UP CARRIES NO FORM", "<form" not in html_ and 'type="password"' not in html_)
    # AND THE OTHER HALF, WHICH IS THE ASSERTION THE OWNER ACTUALLY ASKED FOR. A box step
    # reappearing here is the wizard growing back the two steps he had removed, and it would do
    # so silently — the page would simply be longer.
    for s in box_steps:
        ok(f"{s['key']}: the box's own step is NOT in the machine's wizard",
           s["title"] not in words, s["title"])


def test_the_renderer_knows_no_step_by_name():
    """A loop containing `if key == "zernio"` is three bespoke flows wearing a for-statement.
    The property OSDev1 asked for is that a THIRD credential is a new contract entry and no
    change here at all — so the render helpers are read and held to it."""
    src = pathlib.Path(__file__).resolve().parents[1] / "marketing/customer_voice/app.py"
    body = src.read_text()
    for fn in ("_setup_field", "_setup_step", "_guide_row"):
        start = body.find(f"def {fn}(")
        ok(f"{fn} exists to be read at all", start >= 0)
        if start < 0:
            continue
        end = body.find("\ndef ", start + 1)
        end = end if end > start else len(body)
        chunk = re.sub(r'(?s)""".*?"""', " ", body[start:end])     # docstrings may name them
        chunk = re.sub(r"#.*", " ", chunk)                          # so may comments
        # THE STEP KEYS AND THE VENDORS, not every word that happens to be a vendor's. "email"
        # is also an HTML input TYPE, and a renderer reading the type the contract gave it is
        # doing exactly the right thing — the first version of this test failed on
        # `autocomplete="email"`, which was the test being blunt rather than the code branching.
        # What must not appear is a step being singled out.
        for name in ("zernio", "gmail", "instagram", "messenger", "anthropic"):
            ok(f"{fn} does not name {name!r}", name not in chunk.lower(),
               chunk[max(0, chunk.lower().find(name) - 60):][:120])
        for key in (s["key"] for s in bs.SETUP_STEPS):
            ok(f"{fn} does not single out the {key!r} step",
               f'== "{key}"' not in chunk and f"== '{key}'" not in chunk, chunk[:80])


def test_every_status_the_stores_can_produce_has_a_sentence():
    """#1250's docstring listed four; the stores on main produce five. A status the screen was
    told could not happen renders as a blank row in front of a paying customer."""
    # `getattr`, NOT `from … import`. A missing name raises ImportError, and that ends the file
    # exactly as `.index` does — same lesson, different spelling, caught on the same probe.
    from marketing.customer_voice import app as voice
    table = getattr(voice, "_SET_STATUS", None)
    ok("the screen has a status table at all", isinstance(table, dict), str(type(table)))
    if not isinstance(table, dict):
        return
    _SET_STATUS = table
    produced = {"not_connected", "connected", "needs_reauth", "admin_disabled",
                "payment_required"}
    missing = sorted(produced - set(_SET_STATUS))
    ok("the screen has a sentence for every status a store can return", not missing, str(missing))
    ok("...and none of them is blank", all(v[0].strip() for v in _SET_STATUS.values()))
    # The one #1250 could not have known about, because it predates the verified store.
    ok("...including payment_required, which is the one a buyer can act on",
       "payment" in _SET_STATUS.get("payment_required", ("",))[0].lower(),
       str(_SET_STATUS.get("payment_required")))


def test_the_link_out_is_not_on_set_up():
    """CONNECTING HAPPENS ON THE STEP'S HOME. The social accounts page connects each platform once
    the key is in; set-up only says where that is, so it never sends anyone to the vendor early."""
    _reset()
    app, c = _c()
    body = c.get("/inbox/setup").get_data(as_text=True)
    z = [e for e in bs.setup_state() if e.get("link")][0]
    ok("set-up sends nobody to the vendor", f'href="{z["link"]["url"]}"' not in body)
    ok("...and draws no dead vendor button either", 'aria-disabled="true"' not in body)


def test_a_refusal_is_shown_on_the_home_the_guide_sent_him_to():
    """A refused password is said on the page where it was typed: the mailbox's one home, arrived
    at from set-up, which keeps the way back."""
    _reset()
    app, c = _c()
    body = c.post("/inbox/mailbox",
                  data={"from": "setup", "user": "owner@acme.com",
                        "password": "hunter2hunter2hunter2"}).get_data(as_text=True)
    ok("the store's sentence is shown", "not an app password" in _text(body))
    ok("...with the way back to set-up still there", 'href="/inbox/setup"' in body)
    ok("THE PASSWORD IS NEVER ECHOED", "hunter2" not in body)
    ok("...but the address they typed is kept", 'value="owner@acme.com"' in body)
    ok("and nothing was stored", not bs.email_credential())


def test_saving_on_the_home_brings_him_back_to_the_guide():
    _reset()
    app, c = _c()
    r = c.post("/inbox/mailbox", data={"from": "setup", "user": "owner@acme.com",
                                      "password": GOOD_PW})
    ok("a good credential redirects", r.status_code in (302, 303), str(r.status_code))
    ok("...back to the guide, at its own step",
       (r.headers.get("Location") or "").endswith("/inbox/setup?done=email#email"),
       r.headers.get("Location"))
    ok("...and it is stored", bs.email_credential().get("user") == "owner@acme.com")
    body = c.get("/inbox/setup?done=email").get_data(as_text=True)
    ok("the step now reads connected, naming the mailbox",
       "Connected" in _text(body) and "owner@acme.com" in _text(body))
    ok("...and the guide confirms it, read from the box", "is connected" in _text(body))
    ok("THE PASSWORD IS ON NO PAGE", FLAT_PW not in body and GOOD_PW not in body)
    # A "done" the box does not agree with is not confirmed: the link is not the proof.
    _reset()
    ok("a claimed done that the box does not hold is not confirmed",
       "is connected" not in _text(c.get("/inbox/setup?done=email").get_data(as_text=True)))


def test_a_post_to_set_up_saves_nothing():
    """Set-up has no forms. A post (an old page left open, a crafted request) stores nothing and
    lands back on the guide."""
    _reset()
    app, c = _c()
    for data in ({"step": "email", "user": "a@b.c", "password": GOOD_PW},
                 {"step": "../../etc", "user": "a@b.c"}):
        r = c.post("/inbox/setup", data=data)
        ok(f"a post ({data['step']}) goes back to the guide",
           r.status_code == 303 and (r.headers.get("Location") or "").endswith("/inbox/setup"),
           f"{r.status_code} {r.headers.get('Location')}")
    ok("...and nothing is stored", not bs.email_credential() and not bs.zernio_key())


def test_the_contract_never_hands_the_screen_a_secret():
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD_PW)
    import json
    blob = json.dumps(bs.setup_state(), default=str)
    ok("no password reaches the screen", FLAT_PW not in blob and GOOD_PW not in blob)
    ok("...though the address does, so he can see WHICH inbox", "owner@acme.com" in blob)


def test_an_unreadable_contract_is_a_page_not_a_500():
    """The set-up screen is where a stuck buyer goes. It is the worst page on the box to 500."""
    _reset()
    real = bs.setup_state
    try:
        def boom():
            raise RuntimeError("no such table: box_secrets")
        bs.setup_state = boom
        app, c = _c()
        r = c.get("/inbox/setup")
        ok("it still renders", r.status_code == 200, str(r.status_code))
        ok("...saying what it could not do", "could not read its own set-up" in _text(r.get_data(as_text=True)))
        ok("...and never a stack trace", "Traceback" not in r.get_data(as_text=True))
    finally:
        bs.setup_state = real


def test_the_connect_button_now_reaches_the_set_up_screen():
    _reset()
    from core import spaces
    import marketing.customer_voice.report as rep
    from marketing.customer_voice.inbox import store
    keep = (spaces.all_spaces, rep.report, store.list_conversations)
    try:
        spaces.all_spaces = lambda: [{"name": "default"}]
        rep.report = lambda day, **kw: {"headline": {}, "needs_you": [], "figures": {},
                                  "happened": [], "watch": []}
        store.list_conversations = lambda space, **kw: []
        app, c = _c()
        for path in ("/inbox/", "/inbox/inbox"):
            got = re.findall(r'<a class="btn" href="([^"]+)">([^<]+)<',
                             c.get(path).get_data(as_text=True))
            ok(f"{path} sends him to the set-up screen",
               got and got[0][0] == "/inbox/setup", str(got))
            ok(f"...and the words match where it goes",
               got and got[0][1] == "Set up your box", str(got))
    finally:
        spaces.all_spaces, rep.report, store.list_conversations = keep


def test_ci_actually_runs_this_file():
    wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    me = pathlib.Path(__file__).stem
    if not wf.is_file():
        # A SOLD BOX HAS NO CI AND THIS SUITE SHIPS INTO ONE — the sixth file with this defect,
        # and the one guarding the set-up loop a buyer walks on day one. Reported, not asserted:
        # the hazard is a hand-kept list in tests.yml drifting from a filename, and in a box there
        # is no list to have drifted. An ok() here would be true by construction.
        print(f"  --   no workflow here — this box is not the repo, so {me} has no list to be "
              "missing from")
        return
    ok(f"{me} is in the workflow's suite list", me in wf.read_text(),
       "CI would skip this file and still print green")


def _esc_in(body: str, text: str) -> bool:
    """Is `text` on the page, however the renderer escaped it?"""
    import html as _h
    return text in body or _h.escape(text) in body or text in _text(body)


if __name__ == "__main__":
    for fn in (test_the_screen_exists_and_is_shut_to_a_stranger,
               test_it_renders_the_contract_in_the_contract_s_order,
               test_the_renderer_knows_no_step_by_name,
               test_every_status_the_stores_can_produce_has_a_sentence,
               test_the_link_out_is_not_on_set_up,
               test_a_refusal_is_shown_on_the_home_the_guide_sent_him_to,
               test_saving_on_the_home_brings_him_back_to_the_guide,
               test_a_post_to_set_up_saves_nothing,
               test_the_contract_never_hands_the_screen_a_secret,
               test_an_unreadable_contract_is_a_page_not_a_500,
               test_the_connect_button_now_reaches_the_set_up_screen,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
