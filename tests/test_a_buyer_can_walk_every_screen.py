"""Every screen a buyer reaches on their own box answers, and every link on it goes somewhere.

WHY THIS EXISTS. Owner, 2026-09-18, filming his own onboarding for an investor presentation:
"please do everything you can to make sure that all of the screens through the provisioning
process are connected and functioning."

WHAT THE OTHER SUITES DO NOT DO. Each of them walks the screens IT is about, in the state IT sets
up. Nothing has ever crawled the product the way a person does — land on the first screen, press
whatever is in front of you, and keep going. That is precisely how the two worst finds of this
week were made and missed: the set-up screen existed and nothing linked to it (#1374), and the
Managed cancel door existed with no handle (#1359). Neither was a broken screen. Both were
screens nobody could REACH, and a suite that only visits the URL it already knows can never see
that.

So this one starts at `/dashboard` and follows links, breadth-first, the way a buyer would.

THE TWO STATES THAT MATTER, and they are different products to walk:
  · NOTHING CONNECTED — what the owner films. The box is minutes old; every set-up prompt is
    showing and every link on it had better resolve.
  · EVERYTHING CONNECTED — the box a week later, where the set-up prompts have gone and the
    working screens have taken over. A link that only exists in this state is exactly the kind
    nobody thinks to check.

WHAT COUNTS AS BROKEN: any 404 or 5xx, and any redirect that lands on one. A redirect to a login
or a sibling screen is fine — that is the product working.

Run: python tests/test_a_buyer_can_walk_every_screen.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "walk.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
# NEVER READ THE MACHINE YOU RUN ON for a fact the product branches on — OSDev5's rule, 2026-09-18.
# A dev box carries vendor keys; a customer's box on its first morning carries none, and the whole
# point of this walk is to see what THAT person sees.
for _v in list(os.environ):
    if _v.startswith(("ZERNIO_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")):
        os.environ.pop(_v, None)

from core import state  # noqa: E402

state.init_db()

from core import box_secrets, dash, shell  # noqa: E402
from core.dispatch import app  # noqa: E402
# THE MACHINE, NAMED AT COLUMN 0 SO THE EXPORTER CAN SEE IT. `export_box.sh` decides which suites
# ship by scanning each file for a literal `marketing.<pkg>` import; a dependency reached only
# through `core.dispatch` is invisible to that scan, so the suite ships into a Lead box — which has
# no inbox at all — and fails there on screens that box was never meant to have. Same trap that
# reddened #1374 (OSDev1 flagged this file for it on 2026-09-18).
from marketing.customer_voice import app as _inbox_app   # noqa: E402,F401

# WHICH BOXES THIS WALK BELONGS IN. The screens it crawls — /inbox/ and the doors leading to it —
# are the customer_voice machine's. It imports only core, so the exporter read no dependency and
# shipped it into every box, including the Lead box that test_recipe_ships builds: there /inbox/
# is a 404 and the first screen redirects into nothing, both TRUE about that box and both silent
# about this product. scripts/export_box.sh ships a suite only where every machine it names is
# installed, and it reads the quoted string form as well as import statements, so naming the lane
# here scopes the walk without importing the machine or changing what it asserts on a sold box
# (every Ownbox is a customer_voice box). A Lead box deserves its own walk; that one is not this
# file's job, and writing it here would have meant redesigning the crawl at merge time.
_LANE = "marketing.customer_voice"

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def owner():
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
    return c


# HREFS ONLY, AND ONLY THE BOX'S OWN. An external link (Anthropic's terms, Google's app-password
# page) is not this box's to answer for, and fetching one would put the network in a suite.
_HREF = re.compile(r'href="([^"]+)"')


def links_on(html: str) -> set[str]:
    out = set()
    for h in _HREF.findall(html):
        h = h.split("#")[0]
        if not h or h.startswith(("http://", "https://", "mailto:", "tel:", "javascript:", "data:")):
            continue
        if h.startswith("/"):
            out.add(h)
    return out


# THE DOORS THAT ARE NOT GET. A link that POSTs (sign out, stop everything) is reached by pressing
# a button, and GETting it would either do nothing or do something destructive to the walk. They
# are checked by their own suites; here they are named so the crawler does not report them as dead.
SKIP = {"/dash/logout", "/logout", "/dash/stop"}


# EVERY PAGE THE WALK READ, so what a buyer reads on it can be checked too (section 2b).
READ: dict[str, str] = {}


def walk(client, start: str = "/dashboard") -> tuple[dict, dict]:
    """Breadth-first from `start`. Returns (status by path, where each path was found)."""
    seen: dict[str, int] = {}
    found_on: dict[str, str] = {start: "(the first screen)"}
    queue = [start]
    while queue:
        path = queue.pop(0)
        if path in seen or path in SKIP:
            continue
        r = client.get(path)
        code = r.status_code
        # A REDIRECT IS NOT AN ANSWER — follow it, because a link that 302s onto a 404 is exactly
        # as broken as one that 404s directly, and reads as fine in a status table.
        hops = 0
        while code in (301, 302, 303, 307, 308) and hops < 5:
            nxt = r.headers.get("Location", "")
            if not nxt.startswith("/"):
                break
            r = client.get(nxt)
            code, hops = r.status_code, hops + 1
        seen[path] = code
        if code != 200:
            continue
        # ONLY HTML HAS LINKS. A box links its own icon and manifest from every page, and the
        # first of those is a PNG — decoding it as text raised before this check existed. They
        # still COUNT: an icon that 404s is a broken link on every screen the buyer sees.
        if "html" not in (r.headers.get("Content-Type") or "").lower():
            continue
        body = r.get_data(as_text=True)
        READ[path] = body
        for href in links_on(body):
            if href not in seen and href not in queue:
                found_on.setdefault(href, path)
                queue.append(href)
    return seen, found_on


def report(name: str, seen: dict, found_on: dict) -> None:
    broken = {p: c for p, c in seen.items() if c >= 400}
    ok(f"{name}: every screen a buyer can reach answers ({len(seen)} walked)",
       not broken,
       "; ".join(f"{p} -> {c} (linked from {found_on.get(p, '?')})" for p, c in sorted(broken.items())))


# ── 1. the box the owner will film: minutes old, nothing connected ───────────────────────
print("\ntest_a_brand_new_box_has_no_dead_ends")

for _n in (box_secrets.EMAIL, box_secrets.EMAIL_STATUS, box_secrets.ZERNIO,
           box_secrets.ANTHROPIC, box_secrets.CLAUDE_OAUTH):
    box_secrets.clear(_n)

fresh, fresh_from = walk(owner())
report("a brand-new box", fresh, fresh_from)

# THE WALK MUST ACTUALLY HAVE WALKED. A crawler that finds one page and stops is green and
# worthless — the failure mode every "no broken links" check has. This is the guard on the guard.
ok("...and the walk really crawled the product rather than one page",
   len(fresh) >= 5, f"only reached {sorted(fresh)}")

# THE SET-UP SCREEN IS REACHABLE FROM WHERE A BUYER LANDS. This is #1374's finding turned into a
# standing assertion: the screen was never broken, it was unreachable, and only a walk sees that.
setup = shell.setup_href()
ok("...and the set-up screen is reachable by following links from the first screen",
   bool(setup) and setup in fresh, f"setup_href={setup!r} reached={setup in fresh}")
ok("...arriving with a 200, not a redirect into nothing",
   fresh.get(setup) == 200, str(fresh.get(setup)))


# ── 2. the same box once it is working ───────────────────────────────────────────────────
print("\ntest_a_connected_box_has_no_dead_ends_either")

# Through `put`, never the front doors: `put_email` signs in to a mail server and `put_anthropic`
# calls Anthropic. A suite that reached the network would fail in CI and cost money here.
box_secrets.put(box_secrets.EMAIL, json.dumps(
    {"host": "imap.gmail.com", "user": "a@b.co", "password": "x" * 16}))
box_secrets.put(box_secrets.EMAIL_STATUS, "connected")
box_secrets.put(box_secrets.ZERNIO, "z" * 40)
box_secrets.put(box_secrets.ANTHROPIC, "sk-ant-api03-" + "a" * 60)

done, done_from = walk(owner())
report("a connected box", done, done_from)

# THE SECOND STATE REACHES SCREENS THE FIRST CANNOT, which is the reason it is walked at all.
new_screens = sorted(set(done) - set(fresh))
print(f"       (connecting the box opened {len(new_screens)} more screens: "
      f"{', '.join(new_screens[:6])}{'…' if len(new_screens) > 6 else ''})")


# ── 2b. what a buyer reads on every one of those screens ─────────────────────────────────
print("\ntest_every_screen_reads_as_words")
# FOUND BY LOOKING, 2026-09-24: the inbox's install page told every buyer "that is the
# phone\\u2019s rule" — an escape printed as six characters, around a noun reserved for the
# receptionist machine (CLAUDE.md, mobile first). Neither shows in a diff of a Python string, and
# no suite read the page as a person does. This one now reads every page the two walks reached.
def _words(html_: str) -> str:
    html_ = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", html_)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_))


_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}")
_RESERVED = re.compile(r"\b(phone|phones|ring|call|calls|dial|line|voice)\b", re.I)
_esc_on, _noun_on = {}, {}
for _p, _h in sorted(READ.items()):
    _t = _words(_h)
    if _ESCAPE.search(_t):
        _esc_on[_p] = _ESCAPE.search(_t).group(0)
    _m = _RESERVED.search(_t)
    if _m:
        _noun_on[_p] = _t[max(0, _m.start() - 40):_m.end() + 20]
ok(f"no screen prints an escape sequence as text ({len(READ)} read)", not _esc_on, str(_esc_on))
ok("no screen uses a noun reserved for the receptionist machine", not _noun_on, str(_noun_on))

# A PROMISE THE BOX BREAKS IN THE DEMO'S OWN TAP. Found by looking, 2026-09-24: inbox Settings and
# the mailbox page said "It never sends", while Send on an email thread goes out through the
# buyer's own mailbox (marketing/customer_voice/inbox/reply.py -> email_channel.send). What holds
# is narrower: it sends only what the buyer sends. The words "this mobile" went the same way, a
# noun swapped for a reserved one that no longer read as English; the screens say "this device".
_PROMISE = re.compile(r"\bnever sends\b|\bonly reads\b|\bthis mobile\b", re.I)
_promise_on = {}
for _p, _h in sorted(READ.items()):
    _t = _words(_h)
    _m = _PROMISE.search(_t)
    if _m:
        _promise_on[_p] = _t[max(0, _m.start() - 40):_m.end() + 20]
ok("no screen says the box never sends, or 'this mobile'", not _promise_on, str(_promise_on))

# "SOCIAL ACCOUNTS", NOT "A CHANNEL", for the thing a buyer connects (#1515). Two labels survived
# it, found in the launch sweep: the connected row's "Add or remove a channel" link, which the
# Zernio step also quoted by name, and the connect button's fallback, "Connect a channel". The
# list's "Every channel" filter is a filter over channels and stays.
_CHANNEL = re.compile(r"\b(?:Connect a channel|Add or remove a channel)\b", re.I)
_channel_on = {_p: _CHANNEL.search(_words(_h)).group(0) for _p, _h in sorted(READ.items())
               if _CHANNEL.search(_words(_h))}
ok("no screen asks a buyer to connect 'a channel'", not _channel_on, str(_channel_on))


# ── 3. a member is not shown doors that are not theirs ───────────────────────────────────
print("\ntest_a_second_person_on_the_box_meets_no_dead_ends")

# A box seats three. The owner films this one, but the first thing he will do afterwards is add
# somebody — and an owner-only route that a member's own screen LINKS to is a 403 in their face.
sam = state.add_user("sam@acme.co", name="Sam", role="member")
mc = app.test_client()
mc.set_cookie(dash.COOKIE, dash.new_session(sam["id"]))
mem, mem_from = walk(mc)
report("a member's box", mem, mem_from)

forbidden = {p: c for p, c in mem.items() if c == 403}
ok("...and no screen a member can reach offers them a door they are refused at",
   not forbidden,
   "; ".join(f"{p} (linked from {mem_from.get(p, '?')})" for p in sorted(forbidden)))


# ── 4. the screens BEFORE the dashboard — where the owner's film actually starts ─────────
print("\ntest_the_way_in_from_the_welcome_email_answers_too")

# THE WALK ABOVE BEGINS ALREADY SIGNED IN, WHICH IS NOT WHERE A BUYER BEGINS. The welcome email
# sends them to /claim on a box they have never opened. If that screen, or anything it links to,
# is broken, every assertion above is about a product nobody reached — and it is the first thing
# on camera.
anon = app.test_client()

r = anon.get("/claim")
ok("the claim screen answers to somebody who has never signed in",
   r.status_code in (200, 302), str(r.status_code))

# WHERE AN UNAUTHENTICATED VISITOR IS SENT FROM THE FRONT DOOR. Not asserted as a fixed URL —
# that is the product's choice — only that it is somewhere real rather than a 404 or a crash.
for door in ("/", "/dashboard", "/inbox/"):
    rr = anon.get(door)
    hops = 0
    while rr.status_code in (301, 302, 303, 307, 308) and hops < 5:
        nxt = rr.headers.get("Location", "")
        if not nxt.startswith("/"):
            break
        rr, hops = anon.get(nxt), hops + 1
    ok(f"a signed-out visitor at {door} lands somewhere real, never on a 404 or a crash",
       rr.status_code < 400, f"{door} -> {rr.status_code}")

# AND EVERY LINK ON THE CLAIM SCREEN ITSELF. It is one page with few links, and a dead one there
# is the worst-placed broken link in the product.
# A COUNT OF ZERO IS NOT A PASS, AND SAYING SO IS THE POINT. This box is already claimed by the
# time the suite reaches here — `owner_user()` exists — so /claim redirects and there is no form
# to read links from. An assertion that silently reports "0 checked · ok" is the vacuous guard
# this repo has been bitten by twice this week; it says which case it is instead.
if r.status_code == 200 and "html" in (r.headers.get("Content-Type") or "").lower():
    claim_links = links_on(r.get_data(as_text=True))
    bad = {}
    for href in claim_links:
        if href in SKIP:
            continue
        rr = anon.get(href)
        if rr.status_code >= 400:
            bad[href] = rr.status_code
    if claim_links:
        ok(f"every link on the claim screen goes somewhere ({len(claim_links)} checked)",
           not bad, str(bad))
    else:
        print("  --   the claim screen carries no links of its own (it is a form) — nothing to walk")
else:
    print(f"  --   /claim redirected ({r.status_code}): this box is already claimed, so the "
          f"unclaimed form is not on screen here. tests/test_box_first_login covers that state.")


print("\n— and this file cannot silently fall out of CI —")
import pathlib  # noqa: E402

_wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
if _wf.is_file():
    ok("test_a_buyer_can_walk_every_screen is in the workflow's suite list",
       "test_a_buyer_can_walk_every_screen" in _wf.read_text())
else:
    print("  --   no workflow file here (a box, not the repo) — nothing to check")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("ALL OK")
