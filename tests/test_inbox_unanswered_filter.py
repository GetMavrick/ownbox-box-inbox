"""The working view — how many people are waiting on you, and a way to see only those.

The inbox listed every conversation in one flat stream. On a box with two hundred of them the
question a person actually has when they open the app is not "what arrived" — it is "what is
still on me", and the screen could not answer it. `store.awaiting_reply()` already knew the
number and only the morning report read it.

THE NUMBER AND THE LIST MUST AGREE. A header saying "3 people are waiting" over a filtered list
of five is a screen nobody trusts twice, so the count and the filter are ONE SQL predicate
(`store._WAITING`), including the opted-out carve-out — somebody who said STOP is not waiting for
a reply. That agreement is asserted here rather than assumed, because the two used to be written
out separately and nothing would have caught them drifting.

Run: python tests/test_inbox_unanswered_filter.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="unanswered-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                    # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import store           # noqa: E402

_failed = 0
SPACE = "default"


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


def _wipe() -> None:
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


def _talk(zcid, who, msgs, *, platform="messenger", when="2026-09-15T06:00:00Z",
          ad_id=None, ad_title=None):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=when, ad_meta_id=ad_id, ad_title=ad_title)
    for d, body in msgs:
        store.record_message(space=SPACE, zcid=zcid, zmid=None, direction=d,
                             sent_by="them" if d == "in" else "us", body=body)


def _keys(rows):
    return {r["zernio_conversation_id"] for r in rows}


def test_waiting_is_about_direction_not_about_a_clock():
    """A conversation is waiting when THEIR message was the last one — whether that arrived a
    minute ago or a month ago. An old one is worse, not resolved."""
    _wipe()
    _talk("a", "Dana", [("in", "sink is backing up")])
    _talk("b", "Priya", [("in", "quote please"), ("out", "sent you one")])
    _talk("c", "Len", [("in", "boiler noise"), ("out", "on my way"), ("in", "how long?")])
    _talk("d", "Quiet Quentin", [])
    got = _keys(store.list_conversations(SPACE, waiting=True))
    ok("their message last → waiting", {"a", "c"} <= got, str(got))
    ok("...our message last → NOT waiting", "b" not in got, str(got))
    ok("...no messages at all → not waiting either", "d" not in got, str(got))


def test_the_header_count_and_the_filtered_list_are_the_same_predicate():
    """THE ASSERTION WITH TEETH. These were two separately written queries; if one grows a
    carve-out the other does not, the screen states a number it then contradicts."""
    _wipe()
    _talk("a", "Dana", [("in", "one")])
    _talk("b", "Priya", [("in", "two"), ("out", "answered")])
    _talk("c", "Len", [("in", "three")])
    n = store.awaiting_reply(SPACE)
    rows = store.list_conversations(SPACE, waiting=True, limit=500)
    ok("the count equals the length of the list it describes", n == len(rows), f"{n} vs {len(rows)}")
    app, c = _c()
    words = _text(c.get("/inbox/inbox").get_data(as_text=True))
    ok("...and the header says it in words", f"{n} people are" in words, words[:180])
    ok("...as a sentence, not a bare badge", "waiting on a reply" in words)


def test_somebody_who_said_STOP_is_not_waiting_for_a_reply():
    """The carve-out is in the shared predicate, so it applies to the number AND the list. An
    opted-out row counted as waiting is a number a person cannot act on and must not act on."""
    _wipe()
    _talk("a", "Dana", [("in", "one")])
    _talk("gone", "Gone Greg", [("in", "STOP")])
    store.set_opted_out(SPACE, "gone")
    ok("the count leaves them out", store.awaiting_reply(SPACE) == 1,
       str(store.awaiting_reply(SPACE)))
    ok("...and so does the list", _keys(store.list_conversations(SPACE, waiting=True)) == {"a"},
       str(_keys(store.list_conversations(SPACE, waiting=True))))


def test_the_filter_reaches_the_screen_and_changes_what_is_on_it():
    _wipe()
    _talk("a", "Dana Whitfield", [("in", "sink is backing up")])
    _talk("b", "Priya Raman", [("in", "quote please"), ("out", "sent you one")])
    app, c = _c()
    everything = _text(c.get("/inbox/inbox").get_data(as_text=True))
    ok("unfiltered, both are on the screen",
       "Dana Whitfield" in everything and "Priya Raman" in everything)
    only = _text(c.get("/inbox/inbox?waiting=1").get_data(as_text=True))
    ok("filtered, the one waiting is there", "Dana Whitfield" in only)
    ok("...and the one we answered is gone", "Priya Raman" not in only, only[:200])


def test_a_hand_typed_filter_value_is_off_rather_than_a_third_state():
    _wipe()
    _talk("a", "Dana", [("in", "one")])
    _talk("b", "Priya", [("in", "two"), ("out", "answered")])
    app, c = _c()
    for bad in ("banana", "0", "", "1%20OR%201"):
        words = _text(c.get(f"/inbox/inbox?waiting={bad}").get_data(as_text=True))
        ok(f"waiting={bad!r} behaves as no filter", "Priya" in words, words[:160])


def test_the_two_rows_do_not_both_say_All():
    """FOUND BY RENDERING THE SCREEN WITH BOTH ROWS ON IT. The filter row opens with "All" and the
    channel row opened with "All" too — two adjacent controls, the same word, different meanings.
    Third time this exact bug has been caught on this app by looking at the page rather than the
    code, so it is an assertion now."""
    _wipe()
    _talk("a", "Dana", [("in", "one")], platform="messenger")
    _talk("b", "Sam", [("in", "two")], platform="instagram")
    app, c = _c()
    html_ = c.get("/inbox/inbox").get_data(as_text=True)
    ok("both rows are on the screen",
       'class="chips pills"' in html_ and 'class="chips">' in html_)
    ok("...and only one control says 'All'", html_.count(">All<") == 1, str(html_.count(">All<")))
    ok("...the other says what it actually clears", ">Every channel<" in html_)


def test_one_destination_is_called_one_thing():
    """The channel row's first chip and the row menu's way out BOTH land on /inbox/inbox with no
    channel. They read "Every channel" and "All channels" — two spellings of one idea, which is
    how a reader concludes they must do different things. Found while checking whether the hover
    menu already existed, which it did; renaming the chip had quietly desynced them."""
    _wipe()
    _talk("a", "Dana", [("in", "one")], platform="messenger")
    _talk("b", "Sam", [("in", "two")], platform="instagram")
    app, c = _c()
    # ON A FILTERED VIEW, because the menu only offers the way out when there is one to offer.
    html_ = c.get("/inbox/inbox?channel=instagram").get_data(as_text=True)
    ok("the menu offers the way back to every channel", ">Every channel<" in html_)
    ok("...and nothing on the screen calls it anything else", "All channels" not in html_,
       html_[max(0, html_.find("All channels") - 80):][:160])


def test_every_control_carries_the_filter_instead_of_dropping_it():
    """A control that silently discards the filter is worse than no filter: he taps Instagram to
    narrow the list of what is waiting and gets the whole Instagram inbox back."""
    _wipe()
    for i in range(60):                         # enough rows to force a second page
        _talk(f"z{i}", f"Person {i}", [("in", f"message {i}")],
              platform="instagram" if i % 2 else "messenger")
    import html as _h
    app, c = _c()
    raw = c.get("/inbox/inbox?waiting=1").get_data(as_text=True)
    # UNESCAPED FIRST. An href in the markup carries `&amp;` between its parameters, which is
    # correct and is what a browser turns back into `&` before it requests anything. Matching the
    # raw markup asserted on the encoding rather than on the link, and failed while the links were
    # right — so the comparison happens on what the browser would see.
    html_ = _h.unescape(raw)
    ok("the channel chips carry it", "channel=instagram&waiting=1" in html_
       or "waiting=1&channel=instagram" in html_, html_[html_.find("instagram") - 90:][:180])
    ok("the pager carries it", "waiting=1&page=2" in html_ or "page=2&waiting=1" in html_,
       html_[html_.find("page=2") - 90:][:180])
    ok("the search box carries it as a hidden field",
       '<input type="hidden" name="waiting" value="1">' in raw)


def test_searching_inside_the_filter_stays_inside_it():
    _wipe()
    _talk("a", "Dana Whitfield", [("in", "the boiler is leaking")])
    _talk("b", "Priya Raman", [("in", "the boiler is fine now"), ("out", "glad to hear it")])
    ok("unfiltered, search finds both",
       _keys(store.search_conversations(SPACE, "boiler")) == {"a", "b"},
       str(_keys(store.search_conversations(SPACE, "boiler"))))
    ok("...filtered, it finds only the one still waiting",
       _keys(store.search_conversations(SPACE, "boiler", waiting=True)) == {"a"},
       str(_keys(store.search_conversations(SPACE, "boiler", waiting=True))))
    app, c = _c()
    words = _text(c.get("/inbox/inbox?waiting=1&q=boiler").get_data(as_text=True))
    ok("...and the screen agrees", "Dana Whitfield" in words and "Priya Raman" not in words,
       words[:200])


def test_an_inbox_with_nothing_waiting_is_told_so_rather_than_shown_a_blank():
    """THE ONLY EMPTY ON THIS SCREEN THAT IS GOOD NEWS, and the neutral "no conversations" would
    be the one moment the app had something to congratulate somebody for and said nothing."""
    _wipe()
    _talk("b", "Priya", [("in", "quote please"), ("out", "sent you one")])
    app, c = _c()
    words = _text(c.get("/inbox/inbox").get_data(as_text=True))
    ok("the header says he is caught up", "Nobody is waiting on you." in words, words[:200])
    ok("...and does not print a zero at him", "0 people" not in words)
    ok("...and offers no filter that could only empty the screen",
       'class="chips pills"' not in c.get("/inbox/inbox").get_data(as_text=True))
    filtered = _text(c.get("/inbox/inbox?waiting=1").get_data(as_text=True))
    ok("standing IN the empty filter, it says so in words", "You have answered everyone" in filtered)
    ok("...and still offers the way back out", "Show every conversation" in filtered)


def test_the_way_back_out_survives_answering_the_last_one():
    """The filter row is drawn while the filter is ON even at a count of zero. A control that
    vanishes exactly when it is needed is the same bug as one that was never there."""
    _wipe()
    _talk("b", "Priya", [("in", "quote please"), ("out", "sent you one")])
    app, c = _c()
    html_ = c.get("/inbox/inbox?waiting=1").get_data(as_text=True)
    ok("the filter row is still drawn", 'class="chips pills"' in html_)
    ok("...with All on it, un-selected", ">All<" in html_)


def test_fifty_rows_still_cost_one_query_with_the_filter_on():
    _wipe()
    for i in range(50):
        _talk(f"z{i}", f"Person {i}", [("in", f"message {i}")])
    seen = []
    real = state.connect

    def counted(*a, **k):
        seen.append(1)
        return real(*a, **k)

    state.connect = counted
    try:
        rows = store.list_conversations(SPACE, limit=50, waiting=True)
    finally:
        state.connect = real
    ok("fifty rows came back", len(rows) == 50, str(len(rows)))
    ok("...from ONE connection, not one per row", len(seen) == 1, str(len(seen)))
    ok("...and each still carries its message", all(r.get("preview") for r in rows))


def test_from_an_ad_filters_on_a_fact_the_poller_already_recorded():
    """NOT A NEW IDEA OF A LEAD. `ad_meta_id`/`ad_title` are written by the poller at INSERT for a
    click-to-message conversation, and the row has worn a "From <ad_title>" tag since it was
    built. This exposes that existing fact as a filter and invents nothing."""
    _wipe()
    _talk("plain", "Dana", [("in", "sink backing up")])
    _talk("ad", "Tom", [("in", "saw your offer")], platform="instagram",
          ad_id="23851234567890123", ad_title="Spring boiler service")
    got = _keys(store.list_conversations(SPACE, from_ad=True))
    ok("the conversation that came from an ad is in it", got == {"ad"}, str(got))
    app, c = _c()
    words = _text(c.get("/inbox/inbox?from_ad=1").get_data(as_text=True))
    ok("the screen agrees", "Tom" in words and "Dana" not in words, words[:200])
    ok("...and the row still says WHICH ad", "From Spring boiler service" in words)


def test_an_empty_ad_id_is_not_an_ad():
    """TRIMMED, NOT JUST NOT-NULL. A vendor that sends "" for an ad id would otherwise put every
    conversation in the box behind a filter labelled "from an ad" — the worst possible failure
    for a filter whose entire promise is that it is narrower than everything."""
    _wipe()
    _talk("blank", "Dana", [("in", "hello")], ad_id="", ad_title="")
    _talk("spaces", "Priya", [("in", "hello")], ad_id="   ", ad_title="   ")
    _talk("real", "Tom", [("in", "hello")], ad_id="238512", ad_title="Spring service")
    got = _keys(store.list_conversations(SPACE, from_ad=True))
    ok("only the real one counts as from an ad", got == {"real"}, str(got))
    ok("...and the count agrees with it",
       store.inbox_counts(SPACE)["from_ad"] == 1, str(store.inbox_counts(SPACE)))


def test_a_box_that_runs_no_ads_is_offered_no_ad_filter():
    """Most boxes, most weeks. A pill leading to an empty list is a promise about a kind of
    message this box has never received — the control this app keeps deleting."""
    _wipe()
    _talk("a", "Dana", [("in", "sink backing up")])
    app, c = _c()
    html_ = c.get("/inbox/inbox").get_data(as_text=True)
    ok("no ad pill is drawn", ">From an ad<" not in html_)
    ok("...but the filter row is still there for Unanswered", 'class="chips pills"' in html_)
    # STANDING IN IT ANYWAY, by a hand-typed URL: it must still say something true and offer a
    # way out, not render a bare empty list.
    words = _text(c.get("/inbox/inbox?from_ad=1").get_data(as_text=True))
    ok("reached by hand, it explains itself",
       "No conversation here started from one of your ads" in words, words[:220])
    ok("...without calling it a fault or calling them leads",
       "lead" not in words.lower() and "error" not in words.lower())
    ok("...and offers the way back", "Show every conversation" in words)


def test_both_counts_come_from_one_query():
    """The header and both pills are answered by the SAME scan. Asking separately doubles a scan
    that evaluates a correlated subquery per row, on the screen a person opens most."""
    _wipe()
    for i in range(30):
        _talk(f"z{i}", f"Person {i}", [("in", "hello")],
              ad_id="238512" if i % 3 == 0 else None,
              ad_title="Spring service" if i % 3 == 0 else None)
    seen = []
    real = state.connect

    def counted(*a, **k):
        seen.append(1)
        return real(*a, **k)

    state.connect = counted
    try:
        got = store.inbox_counts(SPACE)
    finally:
        state.connect = real
    ok("one connection for both numbers", len(seen) == 1, str(len(seen)))
    ok("waiting is right", got["waiting"] == 30, str(got))
    ok("from_ad is right", got["from_ad"] == 10, str(got))
    ok("...and each agrees with the list it describes",
       got["waiting"] == len(store.list_conversations(SPACE, limit=500, waiting=True))
       and got["from_ad"] == len(store.list_conversations(SPACE, limit=500, from_ad=True)),
       str(got))


def test_an_empty_box_gets_numbers_not_None():
    """SUM over no rows is NULL in SQL, not 0 — a box on its first day would otherwise hand the
    screen a None and render a filter it cannot use."""
    _wipe()
    got = store.inbox_counts(SPACE)
    ok("waiting is 0, not None", got["waiting"] == 0, repr(got["waiting"]))
    ok("from_ad is 0, not None", got["from_ad"] == 0, repr(got["from_ad"]))
    app, c = _c()
    ok("and the screen renders", c.get("/inbox/inbox").status_code == 200)


def test_the_two_filters_compose_rather_than_replacing_each_other():
    _wipe()
    _talk("both", "Tom", [("in", "saw your offer")], ad_id="238512", ad_title="Spring service")
    _talk("ad_answered", "Ann", [("in", "saw your offer"), ("out", "quoted her")],
          ad_id="238512", ad_title="Spring service")
    _talk("waiting_no_ad", "Dana", [("in", "sink backing up")])
    got = _keys(store.list_conversations(SPACE, waiting=True, from_ad=True))
    ok("only the one that is BOTH waiting and from an ad", got == {"both"}, str(got))
    import html as _h
    app, c = _c()
    html_ = _h.unescape(c.get("/inbox/inbox?waiting=1&from_ad=1").get_data(as_text=True))
    ok("the screen shows just that one",
       "Tom" in html_ and "Ann" not in html_ and "Dana" not in html_)
    # THE PILL LINK, not the search form's hidden fields — those carry both by design and would
    # make this assertion pass while the pills themselves dropped each other, which is exactly
    # what they were doing when this test was written.
    pills = html_[html_.find('class="chips pills"'):]
    pills = pills[:pills.find("</div>")]
    ok("the Unanswered pill can turn itself off while keeping the ad filter",
       'href="/inbox/inbox?from_ad=1">Unanswered' in pills, pills)
    ok("...and the ad pill likewise, keeping Unanswered",
       'href="/inbox/inbox?waiting=1">From an ad' in pills, pills)
    ok("...and both read as pressed, not as one radio choice",
       pills.count('aria-pressed="true"') == 2, pills)
    ok("...with All offering to clear both", 'href="/inbox/inbox">All' in pills, pills)


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING. `.github/` is ours and never ships, but this
    suite DOES ship to a Customer Voice box, which carries the screen it tests — and there this
    read raised FileNotFoundError in front of a paying customer. CI cannot see it:
    `test_recipe_ships` proves the shipped suites inside a fresh LEAD box, and this suite is
    correctly dropped from a Lead box, so the only box type that carries it is the one nothing
    exercises. Found by exporting a customer_voice box and running all of its suites by hand.

    Narrow on purpose: no `.github` at all means this is not the repo; a `.github` that exists
    with the workflow gone is still a real failure.
    """
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_inbox_unanswered_filter is in the workflow's suite list",
       "test_inbox_unanswered_filter" in wf)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("all ok" if not _failed else f"{_failed} FAILED")
    sys.exit(1 if _failed else 0)
