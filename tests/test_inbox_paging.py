"""Page two — the other half of the card's bullet 6.

`docs/AUDIT_499_CARD.md` §6 carries a correction worth restating, because it is why this is a
small file: *"`list_conversations` takes `offset` and binds it properly, so paging is already
there. What is missing is the UI passing it."* The store has been able to do this for weeks; the
screen asked for fifty rows and never asked for the next fifty.

WHAT CAN ONLY BREAK HERE. The store's own suite covers `offset`. What this file is for is the
screen around it: that page two holds the NEXT fifty and not fifty overlapping ones, that a
control appears only when it can succeed, that a hand-typed page number cannot produce a negative
OFFSET or a 500, that running off the end reads as the end and not as an empty box, and that the
query and the channel survive a page turn while the page itself does not survive a new filter.

RENDERED, NOT REASONED ABOUT. The alignment defect this file's last test guards was invisible in
the markup and obvious the moment page three was drawn: the lone "Newer" control sat hard right
with its arrow pointing away from it, under a CSS comment that claimed the opposite.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="inbox-paging-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
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
# TWO FULL PAGES AND A SHORT THIRD. 123 is chosen so the last page is partial: a total that is an
# exact multiple of the page size hides the off-by-one that shows an empty final page.
_TOTAL = 123
_BODIES = ["boiler is leaking", "tap drips", "quote please", "roof after the storm", "no heating"]


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
    c = _client()                       # first: importing the app applies the schema
    sp = spaces.DEFAULT
    for i in range(_TOTAL):
        z = f"zc-{i:03d}"
        store.upsert_conversation(space=sp, zcid=z,
                                  platform="messenger" if i % 2 else "instagram",
                                  participant=f"Customer {i:03d}", account_id="a",
                                  last_inbound_at=(_NOW - timedelta(minutes=i)).isoformat())
        store.record_message(space=sp, zcid=z, zmid=f"{z}-m0", direction="in",
                             sent_by="contact", body=_BODIES[i % len(_BODIES)])
    # ANOTHER CLIENT'S BOX — never expected on any page of this one.
    store.upsert_conversation(space="tenant-b", zcid="zc-other", platform="messenger",
                              participant="Someone Elses Customer", account_id="a",
                              last_inbound_at=_NOW.isoformat())
    store.record_message(space="tenant-b", zcid="zc-other", zmid="zc-other-m0", direction="in",
                         sent_by="contact", body="boiler is leaking")
    c.post("/dash/login", data={"token": settings.dash_token})
    got = len(store.list_conversations(sp, limit=1000))
    assert got == _TOTAL, f"seed did not land: {got} of {_TOTAL}"
    return c


def _ids(html: str) -> list:
    return re.findall(r'<a class="conv" href="/inbox/inbox/([^"]+)"', html)


def _pager(html: str) -> dict:
    return {t.strip(): u.replace("&amp;", "&")
            for u, t in re.findall(r'<a class="pg[^"]*" href="([^"]+)"[^>]*>([^<]+)</a>', html)}


def test_page_two_holds_the_next_fifty_and_not_fifty_of_the_same():
    """THE ONE THAT MATTERS. An off-by-one in the offset shows the same rows twice or skips
    fifty conversations silently, and both look like a working pager."""
    c = _seeded()
    from marketing.customer_voice.app import PAGE
    p1 = _ids(c.get("/inbox/inbox").get_data(as_text=True))
    p2 = _ids(c.get("/inbox/inbox?page=2").get_data(as_text=True))
    p3 = _ids(c.get("/inbox/inbox?page=3").get_data(as_text=True))
    ok(f"a full page holds exactly {PAGE}", len(p1) == PAGE and len(p2) == PAGE,
       f"{len(p1)} / {len(p2)}")
    ok("...even though one more was fetched to know there is a next page",
       len(p1) == PAGE, "the sentinel row leaked into the list")
    ok("the last page holds the remainder", len(p3) == _TOTAL - 2 * PAGE, str(len(p3)))
    ok("no conversation appears on two pages", not (set(p1) & set(p2) or set(p2) & set(p3)))
    ok("and none is skipped between them", len(set(p1 + p2 + p3)) == _TOTAL,
       f"{len(set(p1 + p2 + p3))} of {_TOTAL} reachable")


def test_a_control_appears_only_when_it_can_succeed():
    """The greyed-out button this codebase keeps deleting, in its newest form."""
    c = _seeded()
    first = _pager(c.get("/inbox/inbox").get_data(as_text=True))
    mid = _pager(c.get("/inbox/inbox?page=2").get_data(as_text=True))
    last = _pager(c.get("/inbox/inbox?page=3").get_data(as_text=True))
    ok("page one offers Older and not Newer",
       "Older &rarr;" in first and "&larr; Newer" not in first, str(first))
    ok("a middle page offers both", len(mid) == 2, str(mid))
    ok("the last page offers Newer and not Older",
       "&larr; Newer" in last and "Older &rarr;" not in last, str(last))
    ok("Older goes forward", first.get("Older &rarr;") == "/inbox/inbox?page=2")
    ok("...and Newer goes back to a URL with no page in it at all",
       mid.get("&larr; Newer") == "/inbox/inbox", str(mid))


def test_a_hand_typed_page_can_never_produce_a_negative_offset():
    """A query string is not a promise. Every one of these is a person who has not asked for
    anything in particular, and the answer to that is the top of the list — not a 500, and
    never a negative OFFSET handed to the store."""
    c = _seeded()
    first = _ids(c.get("/inbox/inbox").get_data(as_text=True))
    for bad in ("banana", "-4", "0", "", "1e9999", "2; DROP TABLE inbox_messages"):
        from urllib.parse import quote
        r = c.get(f"/inbox/inbox?page={quote(bad)}")
        ok(f"page={bad!r} answers 200", r.status_code == 200, str(r.status_code))
        if bad in ("banana", "-4", "0", ""):
            ok(f"...and shows page one", _ids(r.get_data(as_text=True)) == first)
    ok("the messages table is still there",
       len(_ids(c.get("/inbox/inbox").get_data(as_text=True))) == 50)


def test_running_off_the_end_reads_as_the_end_not_as_an_empty_box():
    """THE FOURTH EMPTY, and the one a person reaches by accident — a stale bookmark, a back
    button, a typed number. "You have run off the end" and "you have no messages" are opposite
    facts, and this box holds 123 conversations."""
    c = _seeded()
    body = c.get("/inbox/inbox?page=9").get_data(as_text=True)
    ok("it says the page does not exist", "There is no page 9" in body)
    ok("...and never says the inbox is empty", "No conversations yet" not in body)
    ok("...and never claims a search happened", "Nothing matches" not in body)
    ok("...and says nothing is lost, because that is the fear", "Nothing has been lost" in body)
    ok("...and offers the way back", 'href="/inbox/inbox"' in body)
    hit = c.get("/inbox/inbox?q=boiler&page=9").get_data(as_text=True)
    ok("the same past-the-end on a search names the search",
       "There is no page 9" in hit and "boiler" in hit)


def test_the_query_and_the_channel_survive_a_page_turn_and_the_page_does_not_survive_them():
    """Both halves are the same rule: a link carries what the reader is NOT changing.

    The second half is the one that bites. Page 7 of Messenger is not page 7 of Instagram — it
    may not exist at all — so a chip that carried the page would land him on a blank screen he
    did not ask for, having tapped a filter.
    """
    c = _seeded()
    from urllib.parse import urlparse, parse_qs
    body = c.get("/inbox/inbox?q=boiler&channel=messenger").get_data(as_text=True)
    ok("a search inside a channel still finds rows", len(_ids(body)) > 0)
    paged = c.get("/inbox/inbox?channel=messenger&page=2").get_data(as_text=True)
    pg = _pager(paged)
    for label, url in pg.items():
        got = parse_qs(urlparse(url).query)
        ok(f"{label.strip()} keeps the channel", got.get("channel") == ["messenger"], str(got))
    chips = [parse_qs(urlparse(h.replace("&amp;", "&")).query)
             for h in re.findall(r'<a class="chip[^"]*" href="([^"]+)"', paged)]
    ok("no chip carries a page", chips and not any("page" in ch for ch in chips), str(chips))
    ok("...and the search form posts no page either",
       'name="page"' not in paged, "a hidden page field would pin a new search to page 2")


def test_the_range_is_a_position_and_never_a_total():
    """"50 conversations" would be a count; it is a page size. The Today screen refuses the same
    conflation by name — "0" and "nothing to report" are different claims."""
    c = _seeded()
    p1 = c.get("/inbox/inbox").get_data(as_text=True)
    p2 = c.get("/inbox/inbox?page=2").get_data(as_text=True)
    p3 = c.get("/inbox/inbox?page=3").get_data(as_text=True)
    ok("page one of a plain list says nothing, because it has nothing to say",
       'class="found"' not in p1)
    ok("page two says where he is", "Conversations 51-100" in p2, "no position on the screen")
    ok("the last page ends at the real last row", "Conversations 101-123" in p3)
    ok("...and no page states a total", "123 conversations" not in p2 + p3)
    # A SEARCH THAT FITS ON ONE PAGE *IS* A COUNT, and should say so rather than hedge.
    small = c.get("/inbox/inbox?q=boiler").get_data(as_text=True)
    ok("a search that fits on one page gives the real count",
       re.search(r"\d+ conversations matching", small) is not None,
       "it hedged when it actually knew")
    ok("...and offers no next page", "Older" not in _pager(small))


def test_one_clients_page_two_is_never_another_clients():
    """Paging is an offset into a filtered set, and the filter that matters most is the Space.
    Asserted on a LATER page because an offset is exactly where a boundary gets lost."""
    c = _seeded()
    for page in (1, 2, 3):
        body = c.get(f"/inbox/inbox?page={page}").get_data(as_text=True)
        ok(f"page {page} carries no other client's conversation",
           "Someone Elses Customer" not in body and "zc-other" not in body)


def test_the_lone_control_sits_on_its_own_side():
    """RENDERED PAGE THREE AND THERE IT WAS: the last page drew a single "← Newer" hard RIGHT,
    its arrow pointing away from it, under a CSS comment claiming Newer sits left. The first rule
    was `.pg:only-child{margin-left:auto}`, which fires for whichever control happens to be alone.

    The direction has to live in the class, not in how many siblings there are — so that is what
    is pinned here, because the position itself needs a browser and this does not.
    """
    from marketing.customer_voice import app as voice
    css = voice.CSS
    ok("the two controls are told apart in the markup",
       'class="pg prev"' in voice._pager(q="", channel="", page=2, more=True)
       and 'class="pg next"' in voice._pager(q="", channel="", page=2, more=True))
    rule = re.search(r"\.pager[^{}]*\.pg([^{}]*)\{[^}]*margin-left:\s*auto", css)
    ok("only the forward control is pushed to the far side",
       rule is not None and "next" in rule.group(1), rule.group(1) if rule else "no rule at all")
    ok("...and a lone Newer is still drawn", "&larr; Newer" in voice._pager(
        q="", channel="", page=3, more=False))
    ok("...while a page with nowhere to go draws no pager at all",
       voice._pager(q="", channel="", page=1, more=False) == "")


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
    for fn in (test_page_two_holds_the_next_fifty_and_not_fifty_of_the_same,
               test_a_control_appears_only_when_it_can_succeed,
               test_a_hand_typed_page_can_never_produce_a_negative_offset,
               test_running_off_the_end_reads_as_the_end_not_as_an_empty_box,
               test_the_query_and_the_channel_survive_a_page_turn_and_the_page_does_not_survive_them,
               test_the_range_is_a_position_and_never_a_total,
               test_one_clients_page_two_is_never_another_clients,
               test_the_lone_control_sits_on_its_own_side,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
