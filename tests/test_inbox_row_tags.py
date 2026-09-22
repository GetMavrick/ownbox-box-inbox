"""What a conversation row tells you BEFORE you open it — the tags and the hover menu.

Owner, 2026-09-16: "we're also going to add some different tags on each conversation and an
action button menu on hover."

THE POINT OF THE TAGS IS THAT THEY CANNOT DISAGREE WITH THE SEND PATH. Every one is computed
from a column already on the row plus `window.decide` — the same function `reply.send_reply`
consults — so a row can never promise an answer the box would then refuse to post. These tests
seed a REAL box, render the REAL screen, and read the pills out of the HTML, because every
serious defect on this app this week was found by producing the artifact and none by reading it:
a `<= 1` that called an empty conversation New, a pill that slid under the channel logo, and a
reply box drawn on a channel the box has no policy to send on.

ISOLATED DATABASE, ON PURPOSE. This file seeds rows in every window state, and half of them are
states a real box reaches rarely. Running against the shared sandbox DB mixed them with other
suites' fixtures the first time and produced a row whose tags belonged to somebody else's
conversation — a result that looked like a bug in this code and was not.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-tags-")) / "box.db"
# BEFORE ANY core IMPORT. `core.config.Settings` reads os.environ at CLASS-BODY time, so a value
# set after the first import of core.config is read by nobody — this app's most repeated trap.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from datetime import datetime, timedelta, timezone   # noqa: E402

_failed = 0


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


_NOW = datetime.now(timezone.utc)


def _ago(hours):
    return None if hours is None else (_NOW - timedelta(hours=hours)).isoformat()


# ONE ROW PER STATE THE SCREEN CAN BE IN, and the expected tag beside it — so a change that
# quietly drops a state fails here rather than going unnoticed on a box nobody has that state on.
#   zcid, who, platform, hours since inbound, ad_title, account_id, messages, opted out, tags
SEED = [
    # WAS ["New"] until 2026-09-17. A conversation with one message and no constraint now wears
    # NOTHING — the owner had the pill removed and unread is a weight on the row instead. An
    # empty expectation is the point of this row: it proves a calm conversation stays calm.
    ("zc-new",    "Dana Whitfield",  "messenger", 1,    None,           "acct-1", 1, False,
     []),
    # WHATSAPP, NOT EMAIL, IS THE RULELESS CHANNEL NOW. Email was this suite's stand-in for "no
    # policy written" and it stopped being one the moment the email rule landed: written, cited,
    # and since 2026-09-22 sendable — the box mails it from the buyer's own address. Swapping in
    # a channel that genuinely has no rule keeps the state covered instead of quietly deleting
    # it, and the email row below now covers the opposite state: a channel with nothing to warn
    # about at all.
    ("zc-norule", "Terrence Hall",   "whatsapp",  2,    None,           "acct-1", 2, False,
     ["No reply rule"]),
    # EMAIL NOW CARRIES NO PILL AT ALL, and that is the assertion. It used to say "Send in your
    # mail app"; the owner ruled on 2026-09-22 and the box sends email itself, so the row has
    # nothing to warn about — no missing lane, and no window, because email has never had one.
    # An empty list here is the whole point: a pill is a claim, and there is no true one to make.
    ("zc-mail",   "Aurelia Bench",   "email",     2,    None,           "acct-1", 2, False,
     []),
    ("zc-ad",     "Sofia Marchetti", "instagram", 3,    "Roof repair",  "acct-1", 2, False,
     ["From Roof repair"]),
    ("zc-out",    "Joanna Reyes",    "messenger", 5,    None,           "acct-1", 3, True,
     ["Opted out"]),
    ("zc-shut",   "Marcus Cole",     "instagram", 30,   None,           "acct-1", 6, False,
     ["Window closed"]),
    ("zc-tagged", "Priya Raman",     "messenger", 30,   "Spring offer", "acct-1", 4, False,
     ["Tagged reply only", "From Spring offer"]),
    ("zc-limit",  "Len Okafor",      "tiktok",    60,   None,           "acct-1", 3, False,
     ["Limited replies"]),
    ("zc-quiet",  "Whitmore Plumb",  "messenger", None, None,           "acct-1", 0, False,
     ["No inbound yet"]),
]


def _client():
    import core.config as cfg
    from core.dispatch import app
    base = cfg.get_config()
    d = dict(base.get("dash") or {})
    d["app_token"] = ""
    merged = dict(base)
    merged["dash"] = d
    cfg.get_config = lambda m=merged: m
    return app.test_client()


def _seeded():
    # THE CLIENT IS BUILT FIRST AND THAT ORDER IS LOAD-BEARING. Importing the app is what applies
    # the schema; seeding an empty file before it raises "no such table: inbox_conversations",
    # which reads like a broken store and is only a missed migration.
    from core import spaces
    from core.config import settings
    from marketing.customer_voice.inbox import store
    c = _client()
    sp = spaces.DEFAULT
    for zcid, who, plat, h, ad, acct, n, out, _tags in SEED:
        store.upsert_conversation(space=sp, zcid=zcid, platform=plat, participant=who,
                                  ad_title=ad, account_id=acct, last_inbound_at=_ago(h))
        for i in range(n):
            store.record_message(space=sp, zcid=zcid, zmid=f"{zcid}-m{i}",
                                 direction="in" if i % 2 == 0 else "out",
                                 sent_by="contact" if i % 2 == 0 else "human",
                                 body=f"message {i}")
        if out:
            store.set_opted_out(sp, zcid)
    c.post("/dash/login", data={"token": settings.dash_token})
    # THE SEED IS ASSERTED, NOT ASSUMED. A silently empty box would make every test below pass by
    # finding nothing to disagree with — the shape that once let me report "clean" on an export
    # whose seed had failed.
    rows = store.list_conversations(sp, limit=50)
    assert len(rows) == len(SEED), f"seed did not land: {len(rows)} of {len(SEED)} rows"
    return c


def _rows(html: str) -> dict:
    """{participant: (tags, menu items)} read out of the RENDERED page."""
    import re
    out = {}
    for chunk in html.split('<div class="convrow">')[1:]:
        who = re.search(r"<b>([^<]*)</b>", chunk)
        tags = re.findall(r'<span class="tag \w+">([^<]*)</span>', chunk)
        acts = re.findall(r'<div class="menu">(.*?)</div>', chunk, re.S)
        menu = re.findall(r">([^<>]+)</a>", acts[0]) if acts else []
        out[who.group(1) if who else "?"] = (tags, menu)
    return out


def test_every_row_carries_the_tag_its_state_earns():
    """The whole table, rendered — not `_tag_list` called directly.

    A unit test on the helper would have passed on the day the pill was overlapping the channel
    logo and on the day `<= 1` called an empty conversation New. The screen is the artifact.
    """
    c = _seeded()
    seen = _rows(c.get("/inbox/inbox").get_data(as_text=True))
    ok("every seeded conversation is on the screen", len(seen) == len(SEED),
       f"{len(seen)} rows rendered of {len(SEED)}")
    for _zcid, who, _p, _h, _ad, _a, _n, _o, want in SEED:
        got = seen.get(who, ([], []))[0]
        ok(f"{who}: {want}", got == want, f"rendered {got}")


def test_a_conversation_with_no_messages_is_not_new():
    """Written `<= 1` first, and one render caught it: a row the poller knows about and has never
    heard a word on announced itself as New, directly beside a tag saying nothing had arrived.
    New means one message came in and nobody has answered it — not the empty state in a badge."""
    c = _seeded()
    seen = _rows(c.get("/inbox/inbox").get_data(as_text=True))
    quiet = seen["Whitmore Plumb"][0]
    ok("an empty conversation wears no New pill", "New" not in quiet, str(quiet))
    ok("...it says what is actually true of it", "No inbound yet" in quiet, str(quiet))
    # THE PILL IS GONE EVERYWHERE, not just on the empty row. It read as an unread marker and
    # measured `message_count == 1`, which stays true on a thread he has read ten times.
    ok("...and neither does a one-message conversation",
       "New" not in seen["Dana Whitfield"][0], str(seen["Dana Whitfield"][0]))


def test_the_menu_offers_reply_on_exactly_the_rows_that_can_be_replied_to():
    """THE INVARIANT, and the reason the menu is worth having at all: a Reply that lands on a
    thread with nowhere to type is the same broken promise as a greyed-out button, just further
    away. Checked on every seeded row in both directions, so neither side can drift alone."""
    c = _seeded()
    seen = _rows(c.get("/inbox/inbox").get_data(as_text=True))
    for zcid, who, _p, _h, _ad, _a, _n, _o, _t in SEED:
        offered = "Reply" in seen[who][1]
        thread = c.get(f"/inbox/inbox/{zcid}").get_data(as_text=True)
        has_box = 'id="reply"' in thread
        ok(f"{who}: menu offers Reply ({offered}) iff the thread has a box ({has_box})",
           offered == has_box)
    ok("...and it is not vacuous: some rows offer it",
       any("Reply" in v[1] for v in seen.values()))
    ok("...and some rows do not", any("Reply" not in v[1] for v in seen.values()))


def test_a_channel_with_no_written_policy_gets_no_reply_box():
    """`window.decide`'s most important branch, enforced one screen earlier.

    Adding a platform string to the poller must never be enough, by itself, to authorise a send
    on it — and a compose box with a Send button under it IS that authorisation, granted by an
    oversight. Found by rendering a seeded box on `email`, which has no rule written.
    """
    c = _seeded()
    t = c.get("/inbox/inbox/zc-norule").get_data(as_text=True)
    ok("no reply box on a channel with no send rule", 'id="reply"' not in t)
    ok("...and it says so in the buyer's words, not ours", "no reply rule for WhatsApp" in t)
    # AND EMAIL IS THE OTHER SIDE OF THE SAME GUARD. It used to be hidden here too, because the
    # box had no SMTP path; the owner ruled on 2026-09-22 and it has one, so the box gets its
    # reply box like any channel with a written rule. This pair is what keeps the guard honest:
    # one channel that must NOT get a box and one that must, checked on the same screen.
    m = c.get("/inbox/inbox/zc-mail").get_data(as_text=True)
    ok("email HAS a reply box now — the box sends it", 'id="reply"' in m)
    ok("...and is still not called a missing rule", "no reply rule for Email" not in m)
    ok("...and no longer sends the buyer off to their own mail app",
       "your own mail app" not in m)
    ok("...while still showing him everything that arrived", "message 0" in t)
    # THE OTHER HALF: a channel that DOES have a rule still gets its box, or this guard has
    # quietly deleted the product.
    live = c.get("/inbox/inbox/zc-new").get_data(as_text=True)
    ok("a channel with a rule keeps its reply box", 'id="reply"' in live)


def test_the_rule_probe_answers_from_window_and_not_from_a_second_table():
    """`_has_send_rule` asks `window.decide` rather than importing its private `_RULES`.

    A screen holding its own copy of that table is the duplicated-channel-table bug this app
    already removed once. The probe must also be able to say NO — a check that only ever returns
    True is not a check, and this app has shipped several of those.
    """
    from marketing.customer_voice import app as voice
    from marketing.customer_voice.inbox import window
    for key in sorted(window._RULES):
        # EVERY WRITTEN RULE IS FOUND, INCLUDING ONE THAT REFUSES. This loop used to hold that a
        # rule is written iff a fresh message is FREEFORM, which was true only while every rule
        # carried a window. Email's does not — it is written and cited, and `decide` blocks it
        # because the box does not send UNATTENDED, not because a person may not. So the probe
        # asks whether a rule EXISTS, and the flags carry the second, different facts.
        ok(f"a written rule is found for {key}", voice._has_send_rule(key))
    ok("a channel with no rule is refused", not voice._has_send_rule("whatsapp"))
    ok("...and so is one nobody has ever heard of", not voice._has_send_rule("carrier-pigeon"))
    ok("...and so is an empty platform", not voice._has_send_rule(""))
    # READ AS CODE, NOT AS TEXT. `"_RULES" in src` was the first version and it failed on this
    # module's own comment SAYING it must not read `_RULES` — the sixth guard on this app to fire
    # on its author's prose. Parsing the file answers the question actually being asked: does any
    # expression in the screen name that table?
    import ast
    tree = ast.parse(pathlib.Path(voice.__file__).read_text(encoding="utf-8"))
    named = [n for n in ast.walk(tree)
             if (isinstance(n, ast.Attribute) and n.attr == "_RULES")
             or (isinstance(n, ast.Name) and n.id == "_RULES")]
    ok("the screen keeps no second copy of the rule table", not named,
       f"{len(named)} reference(s) to _RULES in app.py")


def test_no_row_can_say_two_things_that_contradict_each_other():
    """Tags are ordered and capped, and the first one is exclusive by construction — opted out,
    no rule, no inbound and a window state are one choice, not four independent flags. Asserted
    because the cheap way to add the next tag is another `if`, and that is how a row ends up
    saying both that it has never heard from someone and that they are new."""
    c = _seeded()
    seen = _rows(c.get("/inbox/inbox").get_data(as_text=True))
    exclusive = {"Opted out", "No reply rule", "Send in your mail app", "No inbound yet",
                 "Window closed", "Tagged reply only", "Limited replies"}
    for who, (tags, _menu) in seen.items():
        n = len(exclusive.intersection(tags))
        ok(f"{who} states at most one thing about whether he can reply", n <= 1, str(tags))
        ok(f"...and never more than three tags in total", len(tags) <= 3, str(tags))
    # WAS: "'No inbound yet' never appears beside 'New'". The pill it guarded against no longer
    # exists, so the pair it forbade is unconstructable — kept as the stronger claim it was
    # really making, which is that no row invents a pill this table does not list.
    known = exclusive | {"From Spring offer", "From Autumn tune-up"}
    for who, (tags, _menu) in seen.items():
        unknown = [t for t in tags if t not in known and not t.startswith("From ")]
        ok(f"{who} states nothing this suite has not named", not unknown, str(unknown))


def test_the_row_is_still_one_link_with_the_menu_beside_it():
    """An <a> inside an <a> is invalid, and browsers recover from it by SPLITTING the outer one —
    which is how a Reply button silently starts opening the thread instead. The menu is a SIBLING
    of the row link, and that is a property worth pinning rather than eyeballing."""
    import re
    c = _seeded()
    html = c.get("/inbox/inbox").get_data(as_text=True)
    for chunk in html.split('<div class="convrow">')[1:]:
        row = chunk.split("</div>")[0]
        anchor = re.search(r'<a class="conv[^"]*".*?</a>', row, re.S)
        ok("the row link exists", anchor is not None)
        if anchor:
            ok("...and contains no nested anchor", "<a " not in anchor.group(0)[3:])
        break
    ok("the menu sits outside the row link",
       html.count('</a><details class="acts"') + html.count('</a></div>') > 0)


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
    for fn in (test_every_row_carries_the_tag_its_state_earns,
               test_a_conversation_with_no_messages_is_not_new,
               test_the_menu_offers_reply_on_exactly_the_rows_that_can_be_replied_to,
               test_a_channel_with_no_written_policy_gets_no_reply_box,
               test_the_rule_probe_answers_from_window_and_not_from_a_second_table,
               test_no_row_can_say_two_things_that_contradict_each_other,
               test_the_row_is_still_one_link_with_the_menu_beside_it,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
