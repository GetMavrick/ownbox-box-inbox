"""The row shows the message — owner, 2026-09-16: "Make sure that the messages are readable."

The list said "1 message". The one thing a person opens an inbox to read was the one thing the
list would not tell them, so every row had to be opened to find out whether it mattered. A row
that shows a COUNT of messages is not readable at any contrast.

WHAT CAN ONLY BREAK HERE, and why each is its own test:

  * IT MUST BE THE NEWEST MESSAGE. The preview and the "waiting on you" tag come from two
    subqueries, and if they are ordered differently the row describes one message and tags
    another — which is invisible until a conversation has two messages and a reply between them.
  * "You:" MUST TRACK THE SAME FACT. It answers whether the ball is in their court, which is what
    the list is for.
  * A CUSTOMER CAN SEND ANYTHING. The body is the only text on this screen written by a stranger,
    and it now goes on a row instead of staying inside a thread. That is a wider surface, so the
    escaping is asserted rather than assumed.
  * FIFTY ROWS MUST COST ONE QUERY. A correlated subquery is free; a per-row lookup is the N+1
    that makes a phone screen feel broken on a slow connection.

Run: python tests/test_inbox_row_preview.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="row-preview-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from datetime import datetime, timedelta, timezone   # noqa: E402

from core import state                               # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import store     # noqa: E402

_failed = 0
NOW = datetime.now(timezone.utc)


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


def _talk(zcid: str, who: str, lines, *, mins: int = 5, platform: str = "instagram"):
    """Seed one conversation. `lines` is [(direction, body), …] oldest first."""
    store.upsert_conversation(space="default", zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=(NOW - timedelta(minutes=mins)).isoformat())
    for i, (direction, body) in enumerate(lines):
        store.record_message(space="default", zcid=zcid, zmid=f"{zcid}-{i}",
                             direction=direction, sent_by="contact" if direction == "in" else "human",
                             body=body)


def test_the_row_carries_the_message_not_a_count_of_them():
    _talk("zc-a", "Dana Whitfield", [("in", "Kitchen sink backing up, can you come Saturday?")])
    app, c = _c()
    words = _text(c.get("/inbox/inbox").get_data(as_text=True))
    ok("the message is on the row",
       "Kitchen sink backing up, can you come Saturday?" in words, words[:220])


def test_it_is_the_NEWEST_message_and_agrees_with_the_waiting_tag():
    """Two subqueries, one truth. Ordered differently they describe different messages, and that
    is invisible until a conversation has history — which every real one does."""
    _talk("zc-b", "Marcus Bell", [("in", "FIRST — how much for a new hot water system?"),
                                  ("out", "About nine hundred fitted, I can do Thursday."),
                                  ("in", "NEWEST — Thursday works, what time?")])
    rows = {r["zernio_conversation_id"]: r for r in store.list_conversations("default")}
    r = rows["zc-b"]
    ok("the preview is the newest message", "NEWEST" in (r.get("preview") or ""), str(r.get("preview")))
    ok("...and not the first", "FIRST" not in (r.get("preview") or ""))
    ok("...and the waiting flag agrees the newest came IN", bool(r.get("awaiting_reply")))

    _talk("zc-c", "Priya Raman", [("in", "Do you take card?"),
                                  ("out", "NEWEST — card on the day is easiest.")])
    r2 = {x["zernio_conversation_id"]: x for x in store.list_conversations("default")}["zc-c"]
    ok("when we spoke last the preview is ours", "NEWEST" in (r2.get("preview") or ""))
    ok("...and the waiting flag agrees nobody is waiting", not r2.get("awaiting_reply"))


def test_you_prefixes_the_line_only_when_we_spoke_last():
    app, c = _c()
    body = c.get("/inbox/inbox").get_data(as_text=True)
    sec = {}
    for zcid in ("zc-b", "zc-c"):
        i = body.find(zcid)
        sec[zcid] = body[i:i + 900] if i >= 0 else ""
        ok(f"{zcid} is on the page", i >= 0)
    ok("the row we answered last is marked 'You:'", "You:" in sec.get("zc-c", ""))
    ok("...and the row waiting on us is not", "You:" not in sec.get("zc-b", ""))


def test_a_conversation_with_no_message_says_so_rather_than_showing_a_blank():
    """The poller knows about rows it has never heard a word on. An empty line there reads as a
    rendering fault, which is the thing this app keeps deleting."""
    store.upsert_conversation(space="default", zcid="zc-quiet", platform="messenger",
                              participant="Silent Sam",
                              last_inbound_at=(NOW - timedelta(hours=9)).isoformat())
    app, c = _c()
    body = c.get("/inbox/inbox").get_data(as_text=True)
    i = body.find("zc-quiet")
    ok("the row is on the page", i >= 0)
    ok("...and says there is no message yet", "No message yet" in body[i:i + 900], body[i:i + 300])


def test_a_customer_cannot_put_markup_on_the_inbox_screen():
    """The body is the only text here written by a STRANGER, and it has just moved from inside a
    thread onto the list. A wider surface gets the assertion, not the benefit of the doubt."""
    _talk("zc-x", "Mallory", [("in", '<script>alert(1)</script> and <b>bold</b> "quoted"')])
    app, c = _c()
    body = c.get("/inbox/inbox").get_data(as_text=True)
    ok("no raw script tag reaches the page", "<script>alert(1)</script>" not in body)
    ok("...nor a raw bold tag from a message", "<b>bold</b>" not in body)
    ok("...and it is escaped, not silently dropped", "&lt;script&gt;" in body)
    ok("...and a person still reads what they sent", "alert(1)" in _text(body))


def test_fifty_rows_cost_one_query_not_fifty_one():
    """A correlated subquery is free; a per-row lookup is the N+1 that makes a phone screen feel
    broken. Counted, because "it is in the same SELECT" is a claim about code, not behaviour."""
    for i in range(50):
        _talk(f"zc-n{i}", f"Person {i}", [("in", f"message number {i}")], mins=i + 1)
    calls = {"n": 0}
    real = state.connect

    class Counting:
        def __enter__(self):
            calls["n"] += 1
            self._cm = real()
            return self._cm.__enter__()

        def __exit__(self, *a):
            return self._cm.__exit__(*a)

    state.connect = lambda: Counting()
    try:
        rows = store.list_conversations("default", limit=50)
    finally:
        state.connect = real
    ok("fifty rows came back", len(rows) >= 50, str(len(rows)))
    ok("...from ONE connection, not one per row", calls["n"] == 1, f"{calls['n']} connections")
    ok("...and every one of them carries its message",
       all(r.get("preview") for r in rows if r["zernio_conversation_id"].startswith("zc-n")))


def test_a_very_long_message_is_trimmed_before_it_crosses_the_wire():
    """A pasted email can be tens of kilobytes; fifty of them is a megabyte to render one clipped
    line on a phone. Trimmed in SQL, so the page stays a page."""
    _talk("zc-long", "Verbose Vic", [("in", "x" * 20000)])
    r = {x["zernio_conversation_id"]: x for x in store.list_conversations("default")}["zc-long"]
    n = len(r.get("preview") or "")
    ok("the store trims it", 0 < n <= 240, str(n))
    app, c = _c()
    body = c.get("/inbox/inbox").get_data(as_text=True)
    ok("...so the page never carries the whole thing", "x" * 1000 not in body)


def test_search_rows_carry_it_too():
    """A row that is waiting does not stop waiting because somebody typed a name into a box, and
    it does not lose its message either — the two readers must agree."""
    hits = store.search_conversations("default", "Dana")
    ok("search found the conversation", bool(hits), str(len(hits)))
    ok("...and its row carries the message", all(h.get("preview") for h in hits), str(hits[:1]))


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING. `.github/` is ours and never ships, and this suite
    DOES ship to a Customer Voice box — it imports the inbox, which that box carries — so a bare
    read raises FileNotFoundError in a paying buyer's own run. `test_recipe_ships` cannot see it:
    that proves the shipped suites inside a fresh LEAD box, where this suite is correctly dropped.

    Narrow on purpose: no `.github` at all means this is not the repo and there is no CI manifest
    to be named in; a `.github` that exists with the workflow gone is still a real failure."""
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_inbox_row_preview is in the workflow's suite list", "test_inbox_row_preview" in wf)


if __name__ == "__main__":
    for fn in (test_the_row_carries_the_message_not_a_count_of_them,
               test_it_is_the_NEWEST_message_and_agrees_with_the_waiting_tag,
               test_you_prefixes_the_line_only_when_we_spoke_last,
               test_a_conversation_with_no_message_says_so_rather_than_showing_a_blank,
               test_a_customer_cannot_put_markup_on_the_inbox_screen,
               test_fifty_rows_cost_one_query_not_fifty_one,
               test_a_very_long_message_is_trimmed_before_it_crosses_the_wire,
               test_search_rows_carry_it_too,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
