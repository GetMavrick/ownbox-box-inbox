"""A buyer connects their Claude subscription by signing in, not by finding a key.

WHY THIS EXISTS. Owner, 2026-09-18, hours from filming his own onboarding: *"What am I going to
click on to authorize Claude subscription? There's no key. It's a login."*

He was right and the screen was wrong. It asked for an `sk-ant-oat…` token — which is the OUTPUT
of a login somebody performs elsewhere, on a laptop, in a terminal, having first installed a CLI.
For a solo owner that is a wall, not onboarding.

WHAT MADE IT BUILDABLE, measured rather than assumed: run `claude setup-token` with no browser and
it PRINTS the claude.com authorize URL and then waits on stdin for the code. On a laptop the
browser opens and that URL is invisible; on a server it is the product. So the box runs the login,
hands the buyer the link, and takes back the short code Claude shows them.

WHAT THIS SUITE CAN AND CANNOT PROVE. It drives the REAL CLI where one is installed — starting a
login and capturing a real URL is checked end to end. It cannot complete a sign-in, because that
needs a human at claude.com with a real subscription. What it therefore pins hardest is EVERY
FAILURE PATH, since those are the ones a buyer meets alone and the ones no demo rehearses:
  · no CLI on the box                    · a code that is refused
  · a sign-in that expired               · an empty code
  · a member pressing the owner's button
and the rule that outranks all of them: a failed login stores NOTHING.

Run: python tests/test_you_sign_in_to_claude_from_the_box.py
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "login.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on (OSDev5, 2026-09-18). A dev
# box carries a subscription token; the buyer whose experience this is about carries none.
for _v in list(os.environ):
    if _v.startswith(("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ZERNIO_API_KEY")):
        os.environ.pop(_v, None)

from core import state  # noqa: E402

state.init_db()

from core import box_secrets as bs, claude_login, dash  # noqa: E402
from core.dispatch import app  # noqa: E402
# THE MACHINE, NAMED AT COLUMN 0 SO THE EXPORTER CAN SEE IT. `export_box.sh` decides which suites
# ship by scanning for a literal `marketing.<pkg>` import; a dependency reached only through
# `core.dispatch` is invisible to it, so this suite shipped into a Lead box — which has no inbox
# screens at all — and failed there. OSDev1 flagged it on this file, 2026-09-18.
from marketing.customer_voice import app as _inbox_app   # noqa: E402,F401

FAILS: list[str] = []
# WHICH BOXES THIS SUITE BELONGS IN. The connect screen it drives is the customer_voice machine's.
# This file imports only core, so scripts/export_box.sh read no dependency and shipped it into
# every box — including the Lead box test_recipe_ships builds, where those screens do not exist and
# the walk fails for a reason that is true about that box and silent about this feature. The
# exporter ships a suite only where every machine it names is installed and reads the quoted string
# form as well as import statements (export_box.sh:171-174), so naming the lane scopes it without
# importing the machine. Every Ownbox is a customer_voice box, so this still runs on what we sell.
_LANE = "marketing.customer_voice"

HAVE_CLI = claude_login.cli_present()


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def owner_client():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


# ── 1. the screen offers a button, not a homework assignment ─────────────────────────────
print("\ntest_the_setup_screen_offers_a_login_rather_than_a_key_hunt")

step = next(e for e in bs.SETUP_STEPS if e["key"] == "anthropic")
ok("the AI step declares a door a buyer can press",
   step.get("action_href") == "/inbox/connect-claude", str(step.get("action_href")))
ok("...labelled as a sign-in, because that is what it is",
   "sign in" in (step.get("action_label") or "").lower(), str(step.get("action_label")))

html = owner_client().get("/inbox/setup").get_data(as_text=True)
ok("...and the rendered set-up screen actually links to it",
   "/inbox/connect-claude" in html)
ok("...and still offers the paste-a-key fallback for anyone who has one",
   'name="key"' in html)

# THE OLD COPY TOLD A BUYER TO INSTALL A CLI AND RUN A COMMAND. That is the wall the owner hit,
# and a suite that let it come back would let the wall come back.
ok("...and no longer instructs the buyer to run a terminal command to get in",
   "claude setup-token" not in html,
   "the set-up screen still tells people to run `claude setup-token` themselves")


# ── 2. the connect screen itself ─────────────────────────────────────────────────────────
print("\ntest_the_connect_screen_says_what_happens_and_asks_for_nothing_secret")

r = owner_client().get("/inbox/connect-claude")
body = r.get_data(as_text=True)
ok("the connect screen answers", r.status_code == 200, str(r.status_code))
ok("...and offers Connect before any link exists, rather than a button to nowhere",
   'name="do" value="start"' in body)
ok("...promises the box never sees a password, which is the thing a person worries about",
   "never sees your password" in body)
ok("...and leaves a way back to pasting a key",
   "/inbox/setup" in body)

# NO LINK IS DRAWN BEFORE THERE IS ONE. Starting the login takes seconds and can fail; a
# "Sign in to Claude" button that leads nowhere is the dead end this screen exists to remove.
ok("...and does NOT show a sign-in link until one has actually been obtained",
   "claude.com/cai/oauth" not in body)


# ── 3. only the owner mints a credential the whole box then thinks on ────────────────────
print("\ntest_a_member_cannot_connect_an_account_the_whole_box_would_bill")

sam = state.add_user("sam@acme.co", name="Sam", role="member")
mc = app.test_client()
mc.set_cookie(dash.COOKIE, dash.new_session(sam["id"]))
mr = mc.get("/inbox/connect-claude")
ok("a member is refused the connect screen", mr.status_code == 403, str(mr.status_code))
mp = mc.post("/inbox/connect-claude", data={"do": "start"})
ok("...and refused the POST too, not merely the page",
   mp.status_code == 403, str(mp.status_code))


# ── 4. every failure a buyer can meet alone ──────────────────────────────────────────────
print("\ntest_every_failure_leaves_the_box_exactly_as_it_was")

bs.clear_claude_oauth()

err = None
try:
    claude_login.finish("")
except claude_login.LoginError as e:
    err = str(e)
ok("an empty code is refused with an instruction, not a stack trace",
   err is not None and "paste" in (err or "").lower(), str(err))

claude_login.cancel()
err = None
try:
    claude_login.finish("some-code")
except claude_login.LoginError as e:
    err = str(e)
ok("a code with no sign-in running says the sign-in expired and how to restart",
   err is not None and "expired" in (err or "").lower(), str(err))

ok("...and nothing was stored by any of that",
   bs.claude_oauth_token() == "", repr(bs.claude_oauth_token()))

# A MESSAGE A PERSON CAN ACT ON, EVERY TIME. Two earlier cuts of this quoted the CLI's terminal
# transcript at the buyer — which at that point is the wrapped authorize URL, so the explanation
# for their failed sign-in read `scope user 3Ainference code_challenge …`. That is worse than
# silence: it looks like the box broke, and it puts a code_challenge on screen.
for probe in ("", "some-code"):
    try:
        claude_login.finish(probe)
    except claude_login.LoginError as e:
        msg = str(e)
        ok(f"the message for {probe!r} carries no URL fragment or challenge",
           not re.search(r"https?://|code_challenge|%2F|[A-Za-z0-9_\-]{30,}", msg), msg[:90])


# ── 5. against the real CLI, where there is one ──────────────────────────────────────────
print("\ntest_the_box_really_can_start_a_login")

if HAVE_CLI:
    url = ""
    try:
        url = claude_login.start()
    except claude_login.LoginError as e:
        ok("starting a login returns a claude.com sign-in link", False, str(e))
    if url:
        ok("starting a login returns a claude.com sign-in link",
           url.startswith("https://claude.com/") and "authorize?" in url, url[:80])
        # IT MUST BE THE AUTHORIZE URL AND NOT MERELY A CLAUDE LINK. Handing a buyer the docs page
        # as their sign-in is a dead end that looks like a working feature.
        ok("...carrying the parameters that make it a real authorisation, not a docs page",
           "client_id=" in url and "code_challenge=" in url, url[:110])

        # THE ASSERTION BOTH OF US FAILED TO WRITE, AND THE ONE THAT MATTERS MOST. Two versions of
        # this feature returned a link whose `state` had the CLI's own next line welded onto it —
        #     …&state=GxqPKVIK…13xKqDkPastecodehereifprompted>
        # — because the URL is hard-wrapped and the prompt follows it after a blank line, and both
        # versions joined every newline. A buyer clicking that is refused by Claude.
        #
        # Every assertion above is TRUE of that broken link: it starts with claude.com, it carries
        # client_id and code_challenge. Checking the SHAPE of what the parameters contain is the
        # only thing that goes red, so check it here rather than trusting the next reader to.
        import urllib.parse as _up
        _st = (_up.parse_qs(_up.urlparse(url).query).get("state") or [""])[0]
        ok("...and the link is not corrupted by whatever the CLI printed next",
           bool(re.fullmatch(r"[A-Za-z0-9_\-]+", _st)) and "aste" not in url and ">" not in url,
           f"state={_st!r}")
        ok("...and the login is waiting, so a code pasted back has somewhere to go",
           claude_login.in_progress())

        # ── THE DEFECT THIS SUITE EXISTS TO KEEP OUT ────────────────────────────────────────
        # A box serves this app with TWO gunicorn workers, and nothing pins the buyer's second
        # request to the worker that served the first. While the session lived in a module
        # global, about half of all sign-ins came back to a worker that had never heard of the
        # login and told the buyer it had expired — on a box that was still waiting for them.
        #
        # A SEPARATE PROCESS IS THE ONLY HONEST TEST OF THAT. Asserting it in-process passes on
        # the broken version too, which is how it reached review in the first place.
        _probe = subprocess.run(
            [sys.executable, "-c",
             "import os;os.environ['AIOS_HERMETIC_TEST']='1'\n"
             "from core import state;state.init_db()\n"
             "from core import claude_login as c\n"
             "print(c.in_progress(), (c.pending_url() or '')[:40], sep='|')"],
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "AIOS_HERMETIC_TEST": "1"})
        _seen, _, _their_url = (_probe.stdout.strip().rpartition("\n")[2]).partition("|")
        ok("ANOTHER WORKER sees the same login — the two-process defect stays fixed",
           _seen == "True", f"rc={_probe.returncode} out={_probe.stdout[-200:]} err={_probe.stderr[-200:]}")
        ok("...and can hand the buyer back the very link they are signing in with",
           bool(_their_url) and url.startswith(_their_url), f"{_their_url!r} vs {url[:40]!r}")

        claude_login.cancel()
        ok("...and cancelling really ends it rather than leaving a process holding a terminal",
           not claude_login.in_progress())
else:
    # THE ABSENT-CLI PATH IS A REAL BUYER STATE, not a skip: a box built from an image that
    # predates the installer has no `claude` binary, and what it says then is all they get.
    err = None
    try:
        claude_login.start()
    except claude_login.LoginError as e:
        err = str(e)
    ok("with no CLI on the box, the screen says so and names who can fix it",
       err is not None and "not installed" in (err or "").lower()
       and "ownbox.io" in (err or ""), str(err))
    print("  --   no `claude` on this machine, so the live sign-in start is not exercised here")


# ── the link handed to the buyer is the whole link and nothing else ──────────────────────
print("\ntest_the_sign_in_link_is_not_corrupted_by_what_follows_it")

# MEASURED TWICE, ON TWO SEPARATE IMPLEMENTATIONS. The CLI prints the URL inside an OSC-8
# hyperlink AND as visible text the terminal wraps with cursor-moves instead of spaces. Joining
# those lines to reassemble it also swallows whatever comes next:
#
#   ...&state=BpZywyWl-…-DsHoldShiftwhileselectingtouseyourterminal
#   ...&state=…L9UhOVYPastecodehereifprompted>
#
# Claude refuses both, so the buyer clicks and it fails. EVERY assertion of the form "a claude.com
# URL came back, and it contains client_id" stays green on a broken link — which is what both of
# our test suites proved before this one. The escape is exact; it is read first.
RAW = (b"\x1b]8;id=x;https://claude.com/cai/oauth/authorize?code=true&client_id=abc"
       b"&code_challenge=xyz&state=ST123\x07visible\r\n"
       b"Hold Shift while selecting to use your terminal's native copy\r\n"
       b"Paste code here if prompted >")
got = claude_login.find_url(RAW, claude_login._ANSI.sub("", RAW.decode()))
ok("the link ends where it really ends", got.endswith("state=ST123"), got)
ok("...with no help text glued on", "HoldShift" not in got.replace(" ", ""), got)
ok("...and no prompt glued on", "Pastecode" not in got.replace(" ", ""), got)

# AND THE SHAPE A BOX ACTUALLY SEES, WHICH HAS NO ESCAPE IN IT AT ALL. Captured from the raw pty
# on 2026-09-18 with the box's own TERM=dumb and NO_COLOR=1: `\x1b]8;` never appears, the CLI
# hard-wraps the URL at the terminal width with `\r\r\n`, and prints its prompt after BLANK lines.
# The fixture above passes on a version that still corrupts this one, because that version reads
# the escape first and never reaches the fallback — and the fallback is the only path on a box.
#
# CI HAS NO `claude` BINARY, so the live assertions further up do not run there. Without this
# fixture the corruption is invisible to CI on every box we ship, which is how it survived twice.
RAW_BOX = (b"Browser didn't open? Use the url below to sign in\r\r\n"
           b"https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88\r\r\n"
           b"ed-5944d1962f5e&response_type=code&scope=user%3Ainference&code_challenge=YO6ArO\r\r\n"
           b"WUaHRl8dxEoUaiIIQQzjJ1nC&code_challenge_method=S256&state=FjmDPKkGaWmEgwcyx\r\r\n"
           b"WSlshro49DDIjV1_SRAEHUi7dY\r\r\n\r\r\n\r\r\n"
           b"\x1b[2GPaste\x1b[8Gcode\x1b[13Ghere\x1b[18Gif\x1b[21Gprompted\x1b[30G>\r\r\n")
box = claude_login.find_url(RAW_BOX, claude_login._ANSI.sub("", RAW_BOX.decode()))
ok("the wrapped link a real box prints is joined back together",
   box.endswith("state=FjmDPKkGaWmEgwcyxWSlshro49DDIjV1_SRAEHUi7dY"), box)
ok("...and stops at the blank line, so the prompt is not welded onto `state`",
   "Pastecode" not in box.replace(" ", "") and ">" not in box, box)

# A URL WITHOUT THE PARAMETERS IS NOT A SIGN-IN, and handing one over is a dead end that reads as
# a working feature.
ok("a claude.com link that is not an authorisation is refused rather than offered",
   claude_login.find_url(b"", "https://claude.com/cai/oauth/authorize?code=true") == "")


print("\n— the code is submitted with a CARRIAGE RETURN, the byte the prompt accepts —")
# THE OWNER LOST A DEMO DAY TO THIS, 2026-09-21. `claude setup-token` reads the code at a
# raw-mode masked prompt (it echoes asterisks). A raw prompt's Enter is \r; a bare \n is just
# another character in the buffer, so the code arrived COMPLETE and was never submitted — 92
# asterisks in the transcript, then ninety seconds of silence, then an error blaming his code.
#
# MEASURED ON A REAL BOX, claude 2.1.278, the same wrong code both ways:
#   LF -> 36 bytes back, no reaction
#   CR -> 247 bytes back, "OAuth error: ... status code 400. Press Enter to retry."
#
# CI CANNOT RUN ANY OF THAT — GitHub runners have no `claude` binary — so this asserts the BYTE
# instead. It is the only thing standing between us and somebody tidying \r back to \n.
ok("a submitted code ends with CR", claude_login.submit_bytes("abc#def").endswith(b"\r"),
   repr(claude_login.submit_bytes("abc#def")))
ok("...and NOT with a newline", not claude_login.submit_bytes("abc#def").endswith(b"\n"),
   "a raw-mode prompt buffers a newline instead of submitting on it")
ok("...and the code itself is unchanged", claude_login.submit_bytes("  abc#def  ") == b"abc#def\r",
   repr(claude_login.submit_bytes("  abc#def  ")))
ok("...whether it arrives as str or bytes",
   claude_login.submit_bytes(b"abc#def") == claude_login.submit_bytes("abc#def"))

# AND IT IS TYPED IN, NOT DUMPED. A REAL authorization code is ~92 characters, and the CLI's
# raw-mode reader cannot take it in one write: measured through the real finish() path on a real
# box (claude 2.1.278, 2026-09-21), 26 characters answered in 2.4s and 92 characters produced
# NINETY-TWO SECONDS OF SILENCE. The asterisk echo showed all 92 characters arriving, which is
# why every theory went looking at Claude, at the code, and at the buyer instead of at us.
#
# THE TEST USES A 92-CHARACTER CODE ON PURPOSE. A short one passes whether the write is chunked
# or not — that is exactly how this shipped, and how it would ship again.
_writes = []
_REAL_LENGTH_CODE = "n" * 47 + "#" + "W" + "x" * 43      # 92 chars, the shape Claude hands out
_orig_write = os.write


def _capture(fd, data):
    _writes.append(data)
    return len(data)


os.write = _capture
try:
    claude_login.submit_code(-1, _REAL_LENGTH_CODE, sleep=lambda _s: None)
finally:
    os.write = _orig_write

ok("a real-length code is written in several chunks, not one dump",
   len(_writes) > 1, f"{len(_writes)} write(s) for {len(_REAL_LENGTH_CODE)} characters")
ok("...and no single write exceeds the chunk size",
   all(len(w) <= claude_login.SUBMIT_CHUNK for w in _writes),
   str(sorted({len(w) for w in _writes})))
ok("...and what arrives is exactly the code plus CR",
   b"".join(_writes) == _REAL_LENGTH_CODE.encode() + b"\r",
   repr(b"".join(_writes))[:90])

print("\n— the support transcript cannot carry the credential it exists to debug —")
# THE DIAGNOSTIC NEARLY BECAME THE BREACH, 2026-09-21. The transcript keeper was added to explain
# FAILURES; a SUCCESS transcript ends with the CLI printing the minted token in full. A live
# one-year credential went to disk in plaintext and then onto a screen, and had to be revoked.
_SAMPLE = ("Long-lived authentication token created successfully! Your OAuth token (valid for 1 "
           "year): sk-ant-oat01-eb2Kr7HpWPv7RhkhupirAqB0jiUfRnJ5N3SZ5tkvbVnIAHWrCADoGeqVlFl1EY "
           "and an api one sk-ant-api03-QQQQQQQQQQQQQQQQQQQQ too")
_red = claude_login.redact(_SAMPLE)
ok("an oat token is redacted", "sk-ant-oat01-eb2" not in _red, _red[-80:])
ok("...and an api key with it", "sk-ant-api03-QQ" not in _red, _red[-80:])
ok("...while the surrounding words survive, so the log is still readable",
   "created successfully" in _red, _red[:60])
ok("...and redacting twice changes nothing", claude_login.redact(_red) == _red)

print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_you_sign_in_to_claude_from_the_box is in the workflow's suite list",
       "test_you_sign_in_to_claude_from_the_box" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
