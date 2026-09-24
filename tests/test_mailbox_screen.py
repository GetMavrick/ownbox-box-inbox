"""The screen where a buyer connects their inbox — which did not exist.

`core.box_secrets.put_email()` shipped with EXACTLY ONE OCCURRENCE IN THE REPOSITORY: its own
definition. Measured across every live branch on 2026-09-16 — no screen, no route, no script ever
called it. The mailbox credential could therefore only be set by someone with a shell, and the
buyer has no shell. Meanwhile a bare box's Settings offered an AI key, a theme and an install
guide: three things, none of which connect anything.

WHAT THIS FILE IS FOR. Not that a form posts — that a person who is stuck gets unstuck. Google
hides App passwords until 2-Step Verification is on, so the ORDER of the instructions is load-
bearing. People paste their account password instead, every time, and that one must be refused
with a sentence rather than stored. Google revokes app passwords whenever the account password
changes, so "it worked last month" is the normal way this breaks. And an administrator can switch
the whole feature off, which no amount of retrying will fix.

AND ONE RULE THIS SCREEN WILL NOT BEND: "saved" is not "connected". `put_email` writes
`status=connected` after a successful WRITE — nothing has spoken to Google at that point.

Run: python tests/test_mailbox_screen.py
"""
import os
import pathlib
import json as _json
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="mailbox-screen-")) / "box.db"
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
GOOD = "abcd efgh ijkl mnop"          # as Google DISPLAYS it — with the spaces in
FLAT = "abcdefghijklmnop"

# ── a mail server to sign in to ─────────────────────────────────────────────────────────────
# SAVING A CREDENTIAL IS NOW ITSELF A CONNECTION (#1257, from OSDev5's own find on the wall):
# `put_email` opens the mailbox instead of counting sixteen characters, because Google revokes
# every app password whenever the account password changes — so the commonest wrong paste is a
# well-formed dead one, which a shape check cannot catch. This screen therefore needs a server
# standing behind it, and "a good credential is stored" now means "one the server ACCEPTS".
import imaplib  # noqa: E402


class _FakeIMAP:
    def __init__(self, host, **kw):
        self.host = host
        self.fail_login: str | None = None

    def login(self, user, password):
        if self.fail_login:
            raise imaplib.IMAP4.error(self.fail_login)
        return ("OK", [b""])

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def logout(self):
        return ("BYE", [b""])


def _server(**kw):
    """Stand a mail server up for the next connection. Kwargs set fields on it (fail_login=…)."""
    def factory(host, **kwargs):
        f = _FakeIMAP(host, **kwargs)
        for k, v in kw.items():
            setattr(f, k, v)
        return f
    imaplib.IMAP4_SSL = factory                               # type: ignore[assignment]


_server()


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
    for name in (bs.EMAIL, bs.EMAIL_STATUS, bs.EMAIL_DETAIL):
        bs.clear(name)


def test_the_screen_exists_and_is_shut_to_a_stranger():
    """A mailbox password is the most dangerous thing this app can be asked to hold."""
    from core.dispatch import app
    routes = {str(r) for r in app.url_map.iter_rules()}
    ok("the box serves somewhere to connect an inbox", "/inbox/mailbox" in routes)
    anon = app.test_client()
    for verb in ("get", "post"):
        r = getattr(anon, verb)("/inbox/mailbox")
        ok(f"an anonymous {verb.upper()} is refused",
           r.status_code in (302, 303) and "/dash/login" in (r.headers.get("Location") or ""),
           f"{r.status_code} {r.headers.get('Location')}")
        ok(f"...and serves no content on {verb.upper()}", len(r.get_data()) < 400)


def test_day_one_tells_a_stuck_person_where_to_look():
    """THE ORDER IS THE WHOLE INSTRUCTION. Google HIDES App passwords until 2-Step Verification
    is on, so a buyer sent looking for it first finds nothing and concludes we are wrong."""
    _reset()
    app, c = _c()
    body = c.get("/inbox/mailbox").get_data(as_text=True)
    words = _text(body)
    ok("it says what the box will do with the mailbox", "reads the mail your customers send" in words)
    # IT SENDS WHAT THE BUYER SENDS, AND SAYS SO. Until 2026-09-24 this line read "it never sends
    # anything", while Send on an email thread went out through this very mailbox
    # (inbox/reply.py -> email_channel.send). The promise that holds is the narrower one.
    ok("...what it sends, which is only what they send",
       "sends only the replies you send" in words and "never sends anything" not in words)
    ok("...and what it will never do", "never marks a message read" in words)
    ok("...and why it will not take their ordinary password", "app password" in words)
    i2, iapp = words.lower().find("2-step verification"), words.lower().find("app passwords and open")
    ok("2-Step Verification comes BEFORE App passwords", 0 <= i2 < iapp, f"{i2} vs {iapp}")
    ok("...and the dead end is named before they hit it",
       "administrator has switched it off" in words)
    # COUNTED INSIDE THE FORM, NOT ON THE PAGE. This app's shell gained the box's menu on
    # 2026-09-18, and core's drawer is a CSS-only checkbox — so every screen it draws now
    # carries one `<input>` that is not a field anybody fills. A page-wide count read 3 and
    # said "three fields", which was never what this line meant: it means the form asks for a
    # host and a password and offers one button. Scoped, it says exactly that, and it would
    # still catch a third field appearing in the form.
    _form = body.split("<form", 1)[-1].split("</form>", 1)[0]
    # WHERE THE MAIL LIVES IS NOW ASKED FIRST (#1483 finding 1: the inbox only knew Gmail). So the
    # form is one choice — the provider — and three fields: the server (used only for "Another
    # provider"), the address and the password. Still one button, still nothing else.
    ok("there is one provider choice, three fields and one button",
       _form.count("<select") == 1 and _form.count("<input") == 3 and body.count("</button>") == 1,
       f"{_form.count('<select')} selects, {_form.count('<input')} fields in the form, "
       f"{body.count('</button>')} buttons on the page")
    ok("...and the password field is a password field", 'type="password"' in body)


def test_the_mistake_people_actually_make_is_refused_in_words():
    """Pasting the Google ACCOUNT password is the mistake, and it is the one that must never be
    stored: far broader than the box needs, and it would keep working after they revoked the
    thing they believed they had given us."""
    _reset()
    app, c = _c()
    r = c.post("/inbox/mailbox", data={"user": "owner@acme.com", "password": "hunter2hunter2hunter2"})
    ok("it is not stored", r.status_code == 200 and not bs.email_credential(), str(r.status_code))
    body = r.get_data(as_text=True)
    ok("...and the screen says which thing they pasted", "not an app password" in _text(body))
    ok("...and tells them what one looks like", "four groups of four" in _text(body))
    ok("THE PASSWORD IS NEVER ECHOED BACK", "hunter2" not in body)
    ok("...but the address they typed is kept, so the typo costs one field not two",
       'value="owner@acme.com"' in body)


def test_a_missing_address_is_its_own_sentence():
    _reset()
    app, c = _c()
    body = c.post("/inbox/mailbox", data={"user": "", "password": FLAT}).get_data(as_text=True)
    ok("it says the address is what is missing",
       "full email address" in _text(body), _text(body)[:160])
    ok("...and not the password sentence", "not an app password" not in _text(body))


def test_saving_it_works_and_saying_connected_would_be_a_lie():
    """`put_email` writes status=connected after a successful WRITE, not a successful LOGIN —
    nothing has spoken to Google at that point. A screen that said "Connected" the instant a
    password was pasted would be wrong for every typo."""
    _reset()
    app, c = _c()
    r = c.post("/inbox/mailbox", data={"user": "owner@acme.com", "password": GOOD})
    ok("a good credential redirects rather than re-posting on refresh",
       r.status_code in (302, 303), str(r.status_code))
    ok("...and it is stored", bs.email_credential().get("user") == "owner@acme.com")
    ok("...with the spaces Google shows stripped, because that is how people paste it",
       bs.email_credential().get("password") == FLAT)

    body = c.get(r.headers["Location"]).get_data(as_text=True)
    words = _text(body)
    ok("the screen names the mailbox, so he can see WHICH inbox", "owner@acme.com" in words)
    ok("...and says the box will TRY it", "will try that password on the next check" in words)
    for lie in ("Connected", "connected and working", "We are reading"):
        ok(f"...and never claims {lie!r} before Google has said so", lie not in words)
    ok("THE PASSWORD IS ON NO PAGE, ever", FLAT not in body and GOOD not in body)
    ok("...and there is a way to stop", "Stop reading this inbox" in words)


def test_google_refusing_it_later_is_the_normal_way_this_breaks():
    """Google revokes an app password whenever the ACCOUNT password changes. "It worked last
    month" is the common case, and "authentication failed" teaches a buyer nothing."""
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    app, c = _c()
    words = _text(c.get("/inbox/mailbox").get_data(as_text=True))
    ok("it names the mailbox that is refusing", "owner@acme.com" in words)
    ok("...and says WHY it usually happens", "account password changes" in words)
    ok("...and quotes what Google actually said", "AUTHENTICATIONFAILED" in words)
    ok("...and offers the fix on the same screen, steps and all",
       "2-Step Verification" in words and "Use this password instead" in words)


def test_an_administrator_switching_it_off_offers_no_button_to_press():
    """No app password can be made at all, so a form here is the dead control this app keeps
    deleting — it would fail forever and read as the buyer's fault."""
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    bs.note_email_status("admin_disabled", "Your administrator has disabled this feature")
    app, c = _c()
    body = c.get("/inbox/mailbox").get_data(as_text=True)
    words = _text(body)
    ok("it says who can fix it", "administrator" in words and "Admin console" in words)
    # NO FORM AT ALL, which is the strongest way to say "no field that cannot succeed" and is
    # immune to the shell's own furniture. This read `"<input" not in body` and went red when
    # the box's menu arrived, because that drawer is a checkbox — an input, but not a field.
    ok("...and offers no field that cannot succeed",
       "<form" not in body, f"a form is still rendered: {body.count('<form')}")
    ok("...and says the rest of the box is unaffected", "nothing else about your box" in words)


def test_stopping_takes_the_reason_with_the_credential():
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    app, c = _c()
    r = c.get("/inbox/mailbox?off=1")
    ok("it redirects rather than answering in place", r.status_code in (302, 303))
    ok("the credential is gone", not bs.email_credential())
    st = bs.email_state()
    ok("...and so is the status", st["status"] == "not_connected", str(st))
    ok("...and so is the old reason — nothing left to quote at somebody",
       not bs.get(bs.EMAIL_DETAIL), bs.get(bs.EMAIL_DETAIL))


def test_recording_a_successful_login_does_not_raise():
    """THE LINE WAS `put(EMAIL_DETAIL, detail[:300] if detail else " ")`. The space was there
    because `validate` rejects an empty value — and `validate` also STRIPS, so the space became
    "" and the call raised `SecretRejected("Paste the key from your AI account, then turn drafts
    on.")`: a sentence about an AI key, thrown while recording a mailbox login, inside the poller
    where nobody is looking. It never fired only because the poller calls this on FAILURE alone,
    always with a detail. It would have raised for the first person to write the success path."""
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    try:
        bs.note_email_status("connected")
        ok("the poller can record that the mailbox works", True)
    except Exception as e:                       # noqa: BLE001 — the whole point of the test
        ok("the poller can record that the mailbox works", False, f"{type(e).__name__}: {e}")
    ok("...and the last failure's sentence goes with it",
       not bs.get(bs.EMAIL_DETAIL), bs.get(bs.EMAIL_DETAIL))
    ok("...and the status is the new one", bs.email_state()["status"] == "connected")

    # AND THE FAILURE ARMS TOO — the half my first fix missed, caught in review by OSDev1. The
    # `" "` placeholder was there to get past `validate`'s empty check, and `validate` STRIPS
    # first, so an empty detail raised the AI-key sentence while recording why a MAILBOX was
    # refused. Reproduced on both arms before the fix; asserted on all of them after.
    for status, detail in (("needs_reauth", ""), ("admin_disabled", ""),
                           ("needs_reauth", "   "), ("connected", "")):
        try:
            bs.note_email_status(status, detail)
            ok(f"{status} with detail={detail!r} does not raise", True)
            ok(f"...and leaves nothing to quote at somebody", not bs.email_state()["detail"],
               bs.email_state()["detail"])
        except Exception as e:                   # noqa: BLE001 — the whole point of the test
            ok(f"{status} with detail={detail!r} does not raise", False,
               f"{type(e).__name__}: {e}")
    # A REAL detail still survives, or the fix would have deleted the feature.
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    ok("a real reason is still kept and shown",
       "AUTHENTICATIONFAILED" in bs.email_state()["detail"], bs.email_state()["detail"])


def test_a_fixed_password_does_not_keep_the_old_complaint():
    """Without this, a buyer who fixes a revoked password keeps "Invalid credentials" in
    `email_state()` forever — a sentence about a credential that no longer exists."""
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password="ponmlkjihgfedcba")
    ok("the old reason is cleared with the old password",
       not bs.email_state()["detail"], bs.email_state()["detail"])


def test_the_connect_button_now_reaches_somewhere_that_can_act():
    """It pointed at Settings, which on a bare box offers an AI key, a theme and an install guide
    — three things, none of which connect anything. Measured by rendering it."""
    _reset()
    from core import spaces
    import marketing.customer_voice.report as rep
    keep = (spaces.all_spaces, rep.report)
    try:
        spaces.all_spaces = lambda: [{"name": "default"}]
        rep.report = lambda day, **kw: {"headline": {}, "needs_you": [], "figures": [],
                                  "happened": [], "watch": []}
        app, c = _c()
        body = c.get("/inbox/").get_data(as_text=True)
        routes = {str(r) for r in app.url_map.iter_rules()}
        targets = re.findall(r'class="btn" href="([^"?#]+)', body)
        ok("Today offers a button", bool(targets), body[:200])
        ok("...pointing at a route this box serves", all(t in routes for t in targets), str(targets))
        ok("...and it is not the page that cannot connect anything",
           targets != ["/inbox/settings"], str(targets))
    finally:
        spaces.all_spaces, rep.report = keep


def test_settings_can_still_reach_it_after_it_is_set_up():
    """A DEAD END I BUILT, found by reading the rendered Settings rather than the code.

    Every link to this screen lived on an EMPTY state — the empty inbox and the first-run Today.
    So connecting an inbox DELETED the only way back to it, and the way back is not a nicety:
    Google revokes an app password whenever the account password changes, which is the single
    most likely reason a buyer needs this screen again, and by then both empty states are gone.
    The only remaining route was typing the URL, which a buyer on a phone will not do.
    """
    app, c = _c()
    for state_, why in ((None, "nothing connected"), ("connected", "already set up"),
                        ("needs_reauth", "Google is refusing it"),
                        ("admin_disabled", "the administrator switched it off")):
        _reset()
        if state_:
            bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
            if state_ != "connected":
                bs.note_email_status(state_, "AUTHENTICATIONFAILED Invalid credentials")
        body = c.get("/inbox/settings").get_data(as_text=True)
        ok(f"Settings reaches the mailbox screen when {why}",
           "/inbox/mailbox" in body, re.findall(r'href="(/inbox/[^"]*)"', body))
        ok(f"...and the password is not on Settings when {why}",
           FLAT not in body and GOOD not in body)

    # AND IT SAYS WHICH STATE, because "is it still reading my mail" is what the row is for.
    _reset()
    bs.put_email(host="imap.gmail.com", user="owner@acme.com", password=GOOD)
    words = _text(c.get("/inbox/settings").get_data(as_text=True))
    ok("a connected box names the mailbox on Settings", "owner@acme.com" in words)
    bs.note_email_status("needs_reauth", "AUTHENTICATIONFAILED Invalid credentials")
    words = _text(c.get("/inbox/settings").get_data(as_text=True))
    ok("...and a refused one says nothing is arriving, rather than still reading 'connected'",
       "refusing the app password" in words and "nothing from this inbox is arriving" in words,
       words[:200])


def test_the_row_is_absent_on_a_box_that_does_not_serve_the_screen():
    """Same rule as every other link in this app: which pages exist is a per-box fact. A row
    pointing at a route this box lacks is a 404 with a sentence in front of it."""
    from marketing.customer_voice import app as voice
    from core.dispatch import app as flask_app
    row = getattr(voice, "_mailbox_row", None)          # same rule as above: report, never raise
    ok("the app has a mailbox row to draw", callable(row), str(row))
    if not callable(row):
        return
    with flask_app.test_request_context("/inbox/settings"):
        real = voice._live
        try:
            voice._live = lambda *paths: ""      # a box serving none of them
            ok("no row is drawn", row() == "", row()[:80])
        finally:
            voice._live = real


def test_the_button_says_what_the_screen_it_reaches_actually_offers():
    """"Connect a channel" PROMISES A CHOICE. On a box serving the hub there is one; on a box
    without it the same button reaches the mailbox screen, which offers exactly one thing — and a
    buyer who pressed it expecting to pick Instagram and arrived at a Gmail form was told
    something untrue of his box. A label and a destination that disagree is a 404 with the noise
    turned off: nothing breaks, he simply believes the product is not what he was shown."""
    from marketing.customer_voice import app as voice
    # `getattr`, NOT `voice._connect_verb`. A missing attribute RAISES, and a raise ends the whole
    # file — I probed this test against the previous commit and it aborted here with an
    # AttributeError instead of reporting a failure, taking `test_ci_actually_runs_this_file` with
    # it. That is the same hazard as the `.index` call in test_today_first_screen, reintroduced in
    # the very next file I wrote. A test that cannot report its own absence is not a test.
    verb = getattr(voice, "_connect_verb", None)
    ok("the app decides the verb from the destination at all", callable(verb), str(verb))
    if not callable(verb):
        return
    # THE TABLE IS WRITTEN OUT HERE ON PURPOSE, not read from the app. A test that asks the
    # helper what it says and then checks it said that proves nothing. So a new destination is a
    # deliberate line in this file — which is how the set-up screen's arrival was caught.
    pairs = {"/inbox/setup": "Set up your box",
             "/inbox/connect": "Connect a channel",
             "/inbox/mailbox": "Connect your inbox"}
    for go, said in pairs.items():
        ok(f"{go} is offered as {said!r}", verb(go) == said, verb(go))
    ok("...and no two destinations share a label", len(set(pairs.values())) == len(pairs))
    ok("an unforeseen destination still gets an honest verb, never a channel promise",
       "channel" not in verb("/inbox/settings").lower(), verb("/inbox/settings"))

    # AND ON THE PAGE, not just in the helper: the href and the words must travel together.
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
            ok(f"{path} offers one button", len(got) == 1, str(got))
            if got:
                href, words = got[0]
                ok(f"...whose words match where it goes ({href})",
                   words == pairs.get(href, words) and href in pairs, f"{words!r} -> {href}")
    finally:
        spaces.all_spaces, rep.report, store.list_conversations = keep


def test_the_form_is_not_buried_under_the_instructions():
    """The fields come BEFORE the four Google steps, on every branch that shows both.

    MEASURED IN A REAL BROWSER, NOT INFERRED. OSDev5, 2026-09-18, on a 390x844 phone: the email
    field sat at y=809 in an 844px viewport — below the fold and behind the tab bar, with 154
    words of instructions above it. A buyer read a wall of Google steps and never learned there
    WAS a form until they scrolled, on the screen that is step one of set-up. Reproduced on main
    at 809 and measured at 398 with the order swapped, same browser and viewport.

    THE GUARD IS ON THE ORDER, NOT ON A PIXEL. A y-coordinate assertion would need a browser in
    CI and would break on any font or padding change that did not matter; what must not come back
    is the ORDER, and DOM position is exactly that, cheaply. 398 is the consequence of the order
    and it is recorded above so the next person knows what number to expect.

    BOTH BRANCHES THAT SHOW BOTH. First run, and `needs_reauth` — and the case is stronger on the
    second, where the person has already had a working app password and may have made the
    replacement before arriving.
    """
    # `ok` IN THIS FILE IS ok(what, cond, got) — LABEL FIRST, unlike the suites I wrote this
    # week. I got it backwards on the first pass and every assertion printed "ok True": a
    # non-empty string as the condition is always truthy, so the test could not fail. Caught by
    # reading the output rather than the exit code, which is the only way that shape ever shows.
    from core import box_secrets as bs
    app, c = _c()

    def order(html_: str):
        """(index of the first credential field, index of step 1) in the raw HTML."""
        return html_.find('name="user"'), html_.find("In your Google Account")

    bs.clear(bs.EMAIL)
    field, steps = order(c.get("/inbox/mailbox").get_data(as_text=True))
    ok("first run: the screen shows both a form and the steps", field > 0 and steps > 0,
       f"field={field} steps={steps}")
    ok("first run: the form comes BEFORE the wall of instructions", field < steps,
       f"field at {field}, steps at {steps} — a buyer scrolls past 150 words to find the field")

    # `needs_reauth` — a person who HAS had a working password and is being refused.
    bs.put(bs.EMAIL, _json.dumps({"host": "imap.gmail.com", "user": "a@b.co",
                                  "password": "x" * 16}))
    bs.note_email_status("needs_reauth", "Google refused it")
    field, steps = order(c.get("/inbox/mailbox").get_data(as_text=True))
    ok("refused: the screen shows both", field > 0 and steps > 0, f"field={field} steps={steps}")
    ok("refused: the replacement field comes first, not the tutorial again", field < steps,
       f"field at {field}, steps at {steps}")
    bs.clear(bs.EMAIL)
    bs.clear(bs.EMAIL_STATUS)


def test_ci_actually_runs_this_file():
    wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    me = pathlib.Path(__file__).stem
    if not wf.is_file():
        # A SOLD BOX HAS NO CI AND THIS SUITE SHIPS INTO ONE. The read used to raise
        # FileNotFoundError and take the whole file down with it, so a buyer running their own
        # suites watched this one crash.
        #
        # Reported, not asserted, and deliberately so. The hazard this guards is a HAND-MAINTAINED
        # list in tests.yml drifting away from a filename. In a box there is no list, so there is
        # nothing that could have drifted — the box runs whatever is in tests/. Writing an ok()
        # here would mean inventing a condition that is true by construction, which is the shape
        # of a check that proves nothing. The line says why it did not run instead.
        print(f"  --   no workflow here — this box is not the repo, so {me} has no list to be "
              "missing from")
        return
    ok(f"{me} is in the workflow's suite list", me in wf.read_text(),
       "CI would skip this file and still print green")


if __name__ == "__main__":
    for fn in (test_the_screen_exists_and_is_shut_to_a_stranger,
               test_day_one_tells_a_stuck_person_where_to_look,
               test_the_mistake_people_actually_make_is_refused_in_words,
               test_a_missing_address_is_its_own_sentence,
               test_saving_it_works_and_saying_connected_would_be_a_lie,
               test_google_refusing_it_later_is_the_normal_way_this_breaks,
               test_an_administrator_switching_it_off_offers_no_button_to_press,
               test_stopping_takes_the_reason_with_the_credential,
               test_recording_a_successful_login_does_not_raise,
               test_a_fixed_password_does_not_keep_the_old_complaint,
               test_the_connect_button_now_reaches_somewhere_that_can_act,
               test_settings_can_still_reach_it_after_it_is_set_up,
               test_the_row_is_absent_on_a_box_that_does_not_serve_the_screen,
               test_the_button_says_what_the_screen_it_reaches_actually_offers,
               test_the_form_is_not_buried_under_the_instructions,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
