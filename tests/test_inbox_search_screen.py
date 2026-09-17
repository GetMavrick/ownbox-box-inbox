"""The SCREEN half of search — the query box the buyer actually types into.

`tests/test_inbox_search.py` covers the store, and it was written knowing this file would exist:
"THIS SUITE COVERS THE STORE HALF ONLY. The query box and the next-page control belong to the
app, which is OSDev5's; `search_conversations` is the reader they will call." This is that half.

WHAT WAS TRUE UNTIL NOW. `search_conversations` had been finished for weeks — space-bound,
LIKE-escaped, searching message BODIES and not just names — and its ONLY caller was
`inbox/tools.py`. The assistant could search a buyer's inbox and the buyer could not, while
"Search everything" was bullet 6 on a page that has been taking money since #1224
(docs/AUDIT_499_CARD.md §6; OSDev0, 2026-09-16 02:30).

WHAT THESE TESTS ARE FOR, GIVEN THE STORE IS ALREADY COVERED. Only what can break on the screen:
the query surviving a chip tap, a count that belongs to the whole inbox being shown beside a
handful of matches, three different empty states that look identical and mean opposite things,
and the tenant boundary — the worst bug available on a page that shows what named customers said.
The wildcard tests are deliberately kept HERE as well as in the store suite: they are the one
property a future caller could defeat from this side, by pre-formatting the query before binding.

NOT COVERED, AND NOT BUILT: paging. `search_conversations` takes an `offset` this screen never
sends, so a search that fills the page says "the 50 most recent" rather than pretending to a
total. The next-page control the store suite anticipated is still owed.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-search-")) / "box.db"
# BEFORE ANY core IMPORT — `core.config.Settings` reads os.environ at class-body time.
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
    return (_NOW - timedelta(hours=hours)).isoformat()


# REAL SENTENCES, NOT "message 1". Every interesting property of this screen is about matching
# what somebody WROTE, and a fixture of placeholders can only ever test matching a name.
SEED = [
    ("zc-1", "Dana Whitfield",  "messenger", 1,  ["Do you do emergency boiler callouts?"]),
    ("zc-2", "Marcus Cole",     "instagram", 30, ["my kitchen tap is leaking badly",
                                                  "Can someone come Friday?"]),
    ("zc-3", "Priya Raman",     "messenger", 30, ["Saw your spring offer, what does it cover?"]),
    ("zc-4", "Len Okafor",      "messenger", 6,  ["boiler pressure keeps dropping",
                                                  "is that a leak?"]),
    ("zc-5", "Sofia Marchetti", "instagram", 3,  ["roof is leaking after the storm"]),
    ("zc-6", "Whitmore & Sons", "messenger", 9,  ["quote for a new bathroom please"]),
    # THE ESCAPE FIXTURE. `%` and `_` are LIKE's own wildcards, and a search for either must ask
    # for the character rather than for the whole inbox.
    ("zc-7", "Ola Berg",        "messenger", 12, ["is the 20% discount still on?"]),
    ("zc-8", "Reed Ashby",      "messenger", 14, ["my order ref is AB_9931"]),
]

# FIFTY-FIVE MORE, ALL OLDER AND ALL MATCHING ONE NONSENSE WORD. Two jobs: it is the only way to
# render a full page and prove the screen does not call a page size a total, and dating them
# older keeps the eight rows above on page one, so no other test depends on this one's size.
_FILLER_WORD = "widgetron"
_FILLER_N = 55


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
    from core import spaces
    from core.config import settings
    from marketing.customer_voice.inbox import store
    c = _client()                  # first: importing the app is what applies the schema
    sp = spaces.DEFAULT
    for zcid, who, plat, h, bodies in SEED:
        store.upsert_conversation(space=sp, zcid=zcid, platform=plat, participant=who,
                                  account_id="acct-1", last_inbound_at=_ago(h))
        for i, body in enumerate(bodies):
            store.record_message(space=sp, zcid=zcid, zmid=f"{zcid}-m{i}",
                                 direction="in" if i % 2 == 0 else "out",
                                 sent_by="contact" if i % 2 == 0 else "human", body=body)
    for i in range(_FILLER_N):
        z = f"zc-fill-{i}"
        store.upsert_conversation(space=sp, zcid=z, platform="messenger",
                                  participant=f"Filler {i}", account_id="acct-1",
                                  last_inbound_at=_ago(24 * 30 + i))
        store.record_message(space=sp, zcid=z, zmid=f"{z}-m0", direction="in",
                             sent_by="contact", body=f"a {_FILLER_WORD} enquiry")
    # ANOTHER CLIENT'S BOX, seeded in a different Space and never expected on this screen.
    store.upsert_conversation(space="tenant-b", zcid="zc-other", platform="messenger",
                              participant="Someone Elses Customer", account_id="acct-9",
                              last_inbound_at=_ago(2))
    store.record_message(space="tenant-b", zcid="zc-other", zmid="zc-other-m0", direction="in",
                         sent_by="contact", body="my boiler is leaking too")
    c.post("/dash/login", data={"token": settings.dash_token})
    # THE SEED IS ASSERTED, NOT ASSUMED. An empty box makes every test below pass by finding
    # nothing to disagree with.
    got = len(store.list_conversations(sp, limit=500))
    assert got == len(SEED) + _FILLER_N, f"seed did not land: {got} rows"
    return c


def _names(html: str) -> list:
    import re
    return [n.replace("&amp;", "&")
            # `conv[^"]*`: the row's class now carries read state (`conv unread`).
            for n in re.findall(r'<a class="conv[^"]*" href="[^"]*">.*?<b>([^<]*)</b>',
                                html, re.S)]


def test_the_buyer_can_search_at_all():
    c = _seeded()
    plain = c.get("/inbox/inbox").get_data(as_text=True)
    ok("there is a search box on the inbox", 'name="q"' in plain and 'role="search"' in plain)
    ok("...and it is a plain GET form, so it works before any script runs",
       'method="get"' in plain and 'action="/inbox/inbox"' in plain)
    ok("...and nothing claims a search when none was made", 'class="found"' not in plain)
    hit = c.get("/inbox/inbox?q=boiler").get_data(as_text=True)
    ok("...and the field carries back what he typed", 'value="boiler"' in hit)


def test_it_searches_what_people_wrote_and_not_only_their_names():
    """THE HEADLINE CAPABILITY. Matching `participant` alone answers "who" and never "what", and
    "what did they say about the leak" is the question somebody actually has."""
    c = _seeded()
    leak = _names(c.get("/inbox/inbox?q=leak").get_data(as_text=True))
    ok("a word nobody's NAME contains still finds them",
       set(leak) == {"Sofia Marchetti", "Len Okafor", "Marcus Cole"}, str(leak))
    ok("...including a message WE sent on the thread",
       "Len Okafor" in leak, "'is that a leak?' is an outbound message")
    boiler = _names(c.get("/inbox/inbox?q=boiler").get_data(as_text=True))
    ok("a second word finds a different set", set(boiler) == {"Dana Whitfield", "Len Okafor"},
       str(boiler))
    name = _names(c.get("/inbox/inbox?q=Whitmore").get_data(as_text=True))
    ok("...and a name still works", name == ["Whitmore & Sons"], str(name))


def test_likes_own_wildcards_are_asked_for_literally():
    """`%` IS A CHARACTER A CUSTOMER TYPES. "is the 20% discount still on" is an ordinary
    sentence, and a screen that answered `%` with the entire inbox would be both wrong and the
    shape of a query-injection: the one character that turns a filter into no filter."""
    c = _seeded()
    pct = _names(c.get("/inbox/inbox?q=%25").get_data(as_text=True))
    ok("a percent sign asks for a percent sign", pct == ["Ola Berg"], str(pct))
    ok("...and NOT for everything", len(pct) < len(SEED), f"{len(pct)} rows came back")
    und = _names(c.get("/inbox/inbox?q=AB_99").get_data(as_text=True))
    ok("an underscore is a character too", und == ["Reed Ashby"], str(und))
    # AND IT IS NOT VACUOUS: the underscore query must fail when the underscore is a wildcard.
    miss = _names(c.get("/inbox/inbox?q=AB_X9").get_data(as_text=True))
    ok("...proven by a query that only matches if _ were a wildcard", miss == [], str(miss))


def test_a_search_and_a_channel_narrow_each_other():
    c = _seeded()
    both = c.get("/inbox/inbox?q=leak&channel=instagram").get_data(as_text=True)
    ok("a channel narrows a search", set(_names(both)) == {"Sofia Marchetti", "Marcus Cole"},
       str(_names(both)))
    ok("...and the field keeps the channel so the next search stays narrowed",
       'name="channel" value="instagram"' in both)
    # PARSED, NOT MATCHED AS A STRING. The first version asserted the literal
    # "channel=messenger&q=leak" and broke the day the parameters were built in one place and
    # came out in the other order — a true property failing on a spelling. What is being asked
    # is whether a chip carries the query, so the chip's href is parsed and asked.
    import re as _re
    from urllib.parse import urlparse as _up, parse_qs as _pq
    chips = [_pq(_up(h.replace("&amp;", "&")).query)
             for h in _re.findall(r'<a class="chip[^"]*" href="([^"]+)"', both)]
    ok("...and the chips carry the query, so tapping one does not throw it away",
       chips and all(c.get("q") == ["leak"] for c in chips), str(chips))
    ok("...and All keeps it too",
       any(c.get("q") == ["leak"] and "channel" not in c for c in chips), str(chips))
    ok("...and no chip carries a page, because a new filter is a new list",
       not any("page" in c for c in chips), str(chips))


def test_a_count_for_the_whole_inbox_is_never_shown_beside_a_handful_of_matches():
    """`platforms_present` counts every row in the Space, not the matches. Shown during a search
    it is simply a wrong number on the screen, and scoping it needs a store this screen does not
    have — so the count comes off rather than being answered incorrectly."""
    c = _seeded()
    plain = c.get("/inbox/inbox").get_data(as_text=True)
    ok("the chips count when he is NOT searching", '<span class="n">' in plain)
    hit = c.get("/inbox/inbox?q=leak").get_data(as_text=True)
    ok("...and drop the number the moment he is", '<span class="n">' not in hit)
    ok("...while still offering the channels themselves", "channel=instagram" in hit)


def test_the_screen_never_calls_a_page_size_a_total():
    """The store returns at most fifty rows. "50 conversations" would be a count; it is a page
    size, and the Today screen refuses exactly this conflation by name — "0" and "nothing to
    report" are different claims."""
    c = _seeded()
    full = c.get(f"/inbox/inbox?q={_FILLER_WORD}").get_data(as_text=True)
    # A RANGE, NOT A TOTAL. This used to read "the 50 most recent", which was true and a dead
    # end; with a next page the honest sentence says exactly what is on the screen and implies
    # nothing about what is past it. The property under both spellings is the same one: the
    # screen never states a number it did not count.
    ok("a full page states a range, not a total", "Conversations 1-50 matching" in full,
       "it claimed a total it did not count")
    ok("...and does not state a bare number", "55 conversations" not in full)
    ok("...and offers the next page", "Older" in full)
    small = c.get("/inbox/inbox?q=boiler").get_data(as_text=True)
    ok("a short result states the real count", "2 conversations matching" in small)
    one = c.get("/inbox/inbox?q=Whitmore").get_data(as_text=True)
    ok("...and one is singular, because a product that says '1 conversations' reads as unfinished",
       "1 conversation matching" in one and "1 conversations" not in one)


def test_three_different_empties_never_read_as_each_other():
    """"Nothing matches boiler", "nothing on Instagram yet" and "you have no messages" look
    identical and mean opposite things. Showing the first-week welcome to a person with sixty
    conversations who mistyped a word is the worst reading of the three."""
    c = _seeded()
    miss = c.get("/inbox/inbox?q=quetzalcoatl").get_data(as_text=True)
    ok("a search with no match says so", "Nothing matches" in miss)
    ok("...and never says the box is empty", "No conversations yet" not in miss)
    ok("...and says what it searched, because he may think it only reads names",
       "not just their names" in miss)
    ok("...and offers the way out", "Show everything" in miss)
    ok("...and keeps the box so he can edit the word rather than retype it",
       'value="quetzalcoatl"' in miss)
    quiet = c.get("/inbox/inbox?channel=whatsapp").get_data(as_text=True)
    ok("an empty CHANNEL says that instead", "Nothing on WhatsApp yet" in quiet)
    ok("...and does not claim a search happened", "Nothing matches" not in quiet)


def test_one_clients_search_is_never_another_clients():
    """The single worst bug available on this screen. The Space is a bound predicate in the store
    and the screen resolves it once — asserted HERE anyway, because the way this arrives is a
    convenience default added later, and it would be invisible on a single-tenant box."""
    c = _seeded()
    from marketing.customer_voice.inbox import store
    from core import spaces
    other = store.search_conversations("tenant-b", "leaking")
    ok("the other Space really does hold a matching row", len(other) == 1, str(len(other)))
    mine = c.get("/inbox/inbox?q=leaking").get_data(as_text=True)
    ok("...and this screen never returns it",
       "Someone Elses Customer" not in mine and "zc-other" not in mine)
    ok("...while still finding its own", "Sofia Marchetti" in mine)
    ok("the store has no all-Spaces variant to reach for",
       not any(n for n in dir(store) if "all_space" in n.lower()))
    # THE FIRST VERSION OF THIS LINE COULD NOT FAIL: it asserted that a row seeded into
    # spaces.DEFAULT came back saying spaces.DEFAULT, which is true by construction and measures
    # nothing. What is worth pinning is the screen's own resolver — an unlabelled host is the
    # box's single tenant, and a label no Space carries must fall back there rather than land on
    # somebody else's brand.
    from marketing.customer_voice import app as voice
    ok("...and an unlabelled host resolves to this box's own Space",
       voice._space() == spaces.DEFAULT, voice._space())


def test_an_empty_query_is_not_a_search():
    """A blank box is a person who has not asked yet. Answering it with "0 conversations
    matching" would make the screen flicker between two meanings of empty."""
    c = _seeded()
    blank = c.get("/inbox/inbox?q=").get_data(as_text=True)
    ok("a blank query lists the inbox", "Dana Whitfield" in blank)
    ok("...and reports no search", 'class="found"' not in blank)
    spaces_only = c.get("/inbox/inbox?q=%20%20").get_data(as_text=True)
    ok("...and so does a query of nothing but spaces", 'class="found"' not in spaces_only)


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
    for fn in (test_the_buyer_can_search_at_all,
               test_it_searches_what_people_wrote_and_not_only_their_names,
               test_likes_own_wildcards_are_asked_for_literally,
               test_a_search_and_a_channel_narrow_each_other,
               test_a_count_for_the_whole_inbox_is_never_shown_beside_a_handful_of_matches,
               test_the_screen_never_calls_a_page_size_a_total,
               test_three_different_empties_never_read_as_each_other,
               test_one_clients_search_is_never_another_clients,
               test_an_empty_query_is_not_a_search,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
