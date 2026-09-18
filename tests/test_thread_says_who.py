"""The thread calls the customer by name, and prints the clock when it changes.

TWO THINGS A PERSON READS ON THIS SCREEN, and both were saying the wrong thing.

"THEM". Every message the customer sent was labelled `them` — on a screen whose header is that
customer's name. The file already argues the case for the other side, in its own comment: "once
more than one person can answer, 'you' is wrong for everyone except whoever typed it." That
reasoning was only half-applied. We took trouble to name OUR human and called theirs a direction.

THE CLOCK ON EVERY LINE. Two messages sent inside the same minute printed the same
"Wed 16 Sep, 20:02" twice, and a busy thread became a column of repeated dates. A person reads a
timestamp to learn when something happened; a repeat teaches nothing.

Neither is cosmetic on a phone: this is the screen a buyer opens the moment somebody messages
them, and it is the one where the box either sounds like it knows who is talking or does not.

Run: python tests/test_thread_says_who.py
"""
import os
import pathlib
import re
import sys
import tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="thread-who-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                    # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import store           # noqa: E402

_failed = 0
SPACE = "default"
NOW = dt.datetime.now(dt.timezone.utc)


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


def _thread(zcid, who, msgs, *, platform="messenger"):
    """msgs = [(direction, body, minutes_ago, sent_by)] — backdated so stamps genuinely differ."""
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=NOW.isoformat(), account_id="a1")
    for d, b, _m, by in msgs:
        store.record_message(space=SPACE, zcid=zcid, zmid=None, direction=d, sent_by=by, body=b)
    with state.connect() as c:
        rows = c.execute("SELECT id FROM inbox_messages WHERE zernio_conversation_id = ? "
                         " ORDER BY created_at, id", (zcid,)).fetchall()
        for row, (_d, _b, mins, _by) in zip(rows, msgs):
            c.execute("UPDATE inbox_messages SET created_at = ? WHERE id = ?",
                      ((NOW - dt.timedelta(minutes=mins)).isoformat(), row["id"]))


def _page(zcid):
    app, c = _c()
    return c.get(f"/inbox/inbox/{zcid}").get_data(as_text=True)


def test_the_customer_is_called_by_their_name_not_them():
    _wipe()
    _thread("t1", "Len Okafor", [("in", "Boiler is banging", 90, "contact"),
                                 ("out", "On my way", 30, "human")])
    html_ = _page("t1")
    words = _text(html_)
    ok("their message is signed with their name", "Len ·" in words, words[:220])
    # THE BYLINES, NOT THE WHOLE PAGE. The first cut of this asserted "them" appeared nowhere and
    # failed on "Drafts are off. Turn them on in Settings" — a correct "them", about drafts. A
    # test that forbids an English word rather than the thing it means will keep firing on good
    # copy until somebody deletes it.
    bylines = re.findall(r'<div class="m">(.*?)</div>', html_)
    ok("...and no byline calls a person 'them'",
       bylines and not any(re.match(r"\s*them\b", b, re.I) for b in bylines), str(bylines))
    ok("our side still reads 'you'", any(b.startswith("you") for b in bylines), str(bylines))


def test_the_first_name_only_because_the_header_already_said_the_rest():
    """A thread of "Len Okafor · 20:02" down the page is the header stuttering."""
    _wipe()
    _thread("t2", "Len Okafor", [("in", "one", 90, "contact"), ("in", "two", 40, "contact")])
    html_ = _page("t2")
    words = _text(html_)
    ok("the header carries the whole name", "Len Okafor" in words)
    ok("...and the bubbles carry only the first", words.count("Len Okafor") == 1,
       str(words.count("Len Okafor")))
    ok("...which is still a name, not a direction", words.count("Len ·") >= 1)


def test_a_contact_with_no_name_still_reads_as_a_person():
    """The poller records conversations before it knows who they are. "Someone" is the fallback
    the header already uses; a blank byline or a bare direction is not an improvement."""
    _wipe()
    _thread("t3", "", [("in", "hello", 30, "contact")])
    words = _text(_page("t3"))
    ok("the page renders at all", "hello" in words)
    ok("...with a human-readable byline", "Them ·" in words, words[:200])
    ok("...and no empty byline", " · ·" not in words and not re.search(r">\s*·", words))


def test_the_clock_prints_when_it_changes_and_not_when_it_does_not():
    """Two messages in the same minute printed the same stamp twice and taught nothing."""
    _wipe()
    _thread("t4", "Len Okafor", [("in", "first", 90, "contact"),
                                 ("in", "second", 90, "contact"),
                                 ("in", "third", 5, "contact")])
    words = _text(_page("t4"))
    # \d{1,2}, NOT \d{2}. The stamp is built with "%-H:%M" — the hour is deliberately NOT
    # zero-padded, because a person reading a thread reads "7:03" and not "07:03". A two-digit
    # regex therefore matches nothing between midnight and 09:59 on the box's own clock, so this
    # suite went red every night and green again every morning, for no reason a reader could see.
    # Caught on 2026-09-18 with the box rendering 7:03 and 8:28; it had passed an hour earlier.
    stamps = re.findall(r"\d{1,2}:\d{2}", words)
    ok("three messages are on the page",
       all(w in words for w in ("first", "second", "third")))
    ok("...but the same minute is printed once, not twice",
       len(stamps) == 2, f"{stamps}")
    ok("...and a different minute prints again", len(set(stamps)) == 2, f"{stamps}")
    ok("every bubble still says who said it", words.count("Len") >= 3, str(words.count("Len")))


def test_a_name_cannot_put_markup_on_the_screen():
    """The name comes from the vendor, so it is a customer-supplied string on our page."""
    _wipe()
    _thread("t5", "<script>alert(1)</script>Mallory", [("in", "hi", 10, "contact")])
    html_ = _page("t5")
    ok("no raw script tag reaches the page", "<script>alert(1)</script>" not in html_)
    ok("...and it is escaped rather than dropped", "&lt;script&gt;" in html_)


def test_the_machines_own_answers_are_still_labelled_as_the_machine():
    """The reason this byline exists at all: on a screen where the box may have answered for him,
    not saying which is which is the one thing that would make him distrust the surface."""
    _wipe()
    _thread("t6", "Len Okafor", [("in", "quote please", 60, "contact"),
                                 ("out", "Here is a quote", 30, "ai")])
    words = _text(_page("t6"))
    ok("the machine's message says so", "the machine" in words, words[:220])
    ok("...and is not passed off as the owner", "you ·" not in words, words[:220])


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING. `.github/` is ours and never ships, and this suite
    DOES ship to a Customer Voice box — the bare read crashed a buyer's own run twice yesterday."""
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_thread_says_who is in the workflow's suite list", "test_thread_says_who" in wf)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("all ok" if not _failed else f"{_failed} FAILED")
    sys.exit(1 if _failed else 0)
