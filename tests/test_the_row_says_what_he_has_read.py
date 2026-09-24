"""Unread is bold, read is regular — owner, 2026-09-17: "New messages should be in Bold text.
Read messages in regular. Just like a normal inbox."

WHAT THIS SUITE IS REALLY GUARDING is the definition, not the font. Bold is easy; bold that
CLEARS is the whole feature. The row used to wear a "New" pill on `message_count == 1`, which
looks like an unread marker and is not one — it stays true on a conversation he has read ten
times, and it goes false the moment anyone replies, which is not reading either.

The tempting cheap version was `awaiting_reply` (the newest message is inbound). It does not
clear when he READS, only when he ANSWERS, so an owner who reads on a phone and replies from a
laptop an hour later watches every row stay bold — and a list where everything is bold says
nothing at all. So:

  * arriving makes it unread, and OPENING it makes it read;
  * OUR OWN reply must not make it unread — the drafter mirrors machine sends through the same
    table, so "unread" has to mean inbound-only or the box tells him he has not read himself;
  * a reply from them AFTER he read it makes it unread again, which is the cycle that makes the
    signal mean anything on day three;
  * both store readers answer it identically. The list and the search screen are separately
    written queries and a row that is bold until you search for it is a bug you only see once.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="read-state-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from datetime import datetime, timedelta, timezone   # noqa: E402

from core import state                               # noqa: E402

state.init_db()

from marketing.customer_voice.inbox import store     # noqa: E402

_failed = 0
NOW = datetime.now(timezone.utc)
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
    return c


def _wipe():
    with state.connect() as c:
        c.execute("DELETE FROM inbox_messages")
        c.execute("DELETE FROM inbox_conversations")


def _seed(zcid, who, lines, *, mins=5, platform="instagram"):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant=who,
                              last_inbound_at=(NOW - timedelta(minutes=mins)).isoformat())
    for i, (d, body) in enumerate(lines):
        store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-{i}", direction=d,
                             sent_by="contact" if d == "in" else "human", body=body)


def _row(c, who):
    html_ = c.get("/inbox/inbox").get_data(as_text=True)
    for row in re.findall(r'<a class="conv[^"]*".*?</a>', html_, re.S):
        if who in row:
            return row
    return ""


def _is_bold(c, who):
    return "conv unread" in _row(c, who)


def test_the_column_exists_at_all():
    """Migration 51. A tagged customer_voice step, so it runs on the machine's replay and not in
    the kernel pass — a Lead box has no such table to alter."""
    with state.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox_conversations)")}
    ok("inbox_conversations carries read_at", "read_at" in cols, str(sorted(cols)))
    ok("...and SCHEMA_VERSION reached the step that adds it", state.SCHEMA_VERSION >= 51,
       str(state.SCHEMA_VERSION))
    ok("...and the step is tagged to this machine, not the kernel",
       state._MIGRATION_OWNER.get(51) == "customer_voice", str(state._MIGRATION_OWNER.get(51)))


def test_it_arrives_unread_and_opening_it_makes_it_read():
    _wipe()
    _seed("zc-a", "Ana Ruiz", [("in", "can you quote a rewire?")])
    c = _c()
    ok("a message nobody has opened is unread", _is_bold(c, "Ana Ruiz"), _row(c, "Ana Ruiz")[:160])
    # THE WORD, FOR A READER THAT CANNOT SEE WEIGHT. Bold is invisible to a screen reader, and a
    # state carried only in a font is a state those users do not have.
    ok("...and says so in words for a screen reader", "Unread." in _row(c, "Ana Ruiz"))
    c.get("/inbox/inbox/zc-a")
    ok("opening the thread makes it read", not _is_bold(c, "Ana Ruiz"), _row(c, "Ana Ruiz")[:160])
    ok("...and the hidden word goes with it", "Unread." not in _row(c, "Ana Ruiz"))


def test_our_own_reply_never_makes_a_row_unread():
    """The drafter mirrors the machine's own sends into this same table. If `unread` counted them,
    the box would tell him he has not read a message he sent himself — and every conversation the
    machine answered overnight would be bold in the morning for no reason."""
    _wipe()
    _seed("zc-b", "Ben Cole", [("in", "morning — any slots?")])
    c = _c()
    c.get("/inbox/inbox/zc-b")
    ok("read after he opens it", not _is_bold(c, "Ben Cole"))
    store.record_message(space=SPACE, zcid="zc-b", zmid="b-out", direction="out",
                         sent_by="human", body="tomorrow at 9?")
    ok("...still read after WE reply", not _is_bold(c, "Ben Cole"), _row(c, "Ben Cole")[:160])
    store.record_message(space=SPACE, zcid="zc-b", zmid="b-ai", direction="out",
                         sent_by="ai", body="(machine draft sent)")
    ok("...and still read after the MACHINE replies", not _is_bold(c, "Ben Cole"))


def test_a_reply_from_them_after_he_read_it_goes_bold_again():
    """The cycle is the feature. A bold that only ever clears once is a bold that stops meaning
    anything on the second day."""
    _wipe()
    _seed("zc-c", "Cara Diaz", [("in", "is Friday still ok?")])
    c = _c()
    c.get("/inbox/inbox/zc-c")
    ok("read", not _is_bold(c, "Cara Diaz"))
    store.record_message(space=SPACE, zcid="zc-c", zmid="c-2", direction="in",
                         sent_by="contact", body="actually, can we move it?")
    ok("...and unread again the moment she writes back", _is_bold(c, "Cara Diaz"),
       _row(c, "Cara Diaz")[:160])
    c.get("/inbox/inbox/zc-c")
    ok("...and read again once he looks", not _is_bold(c, "Cara Diaz"))


def test_a_row_the_poller_knows_and_has_never_heard_from_is_not_unread():
    """`read_at IS NULL` means never opened, which is most rows the day this ships — but with no
    inbound message there is nothing to have read, and a box full of bold rows carrying no
    messages is the empty state shouting."""
    _wipe()
    store.upsert_conversation(space=SPACE, zcid="zc-d", platform="instagram",
                              participant="Dev Patel", last_inbound_at=None)
    c = _c()
    ok("a conversation with no inbound is not bold", not _is_bold(c, "Dev Patel"),
       _row(c, "Dev Patel")[:160])


def test_both_readers_answer_it_and_answer_it_the_same():
    """Separately written queries. A row that is bold in the list and regular in search is a bug
    that only shows up the first time somebody searches for a name they can see."""
    _wipe()
    _seed("zc-e", "Eve Lang", [("in", "sent you photos")])
    listed = {k["zernio_conversation_id"]: k for k in store.list_conversations(SPACE, limit=50)}
    found = {k["zernio_conversation_id"]: k for k in store.search_conversations(SPACE, "Eve")}
    ok("the list reader answers unread", "unread" in listed["zc-e"], str(sorted(listed["zc-e"])))
    ok("the search reader answers unread", "unread" in found["zc-e"], str(sorted(found["zc-e"])))
    ok("...and they agree while it is unread",
       bool(listed["zc-e"]["unread"]) is bool(found["zc-e"]["unread"]) is True)
    store.mark_read(SPACE, "zc-e")
    l2 = {k["zernio_conversation_id"]: k for k in store.list_conversations(SPACE, limit=50)}
    f2 = {k["zernio_conversation_id"]: k for k in store.search_conversations(SPACE, "Eve")}
    ok("...and they agree after it is read",
       bool(l2["zc-e"]["unread"]) is bool(f2["zc-e"]["unread"]) is False)


def test_read_is_the_resting_state_so_a_missing_flag_is_quiet():
    """WHICH WAY ROUND THE CLASS GOES IS THE DESIGN DECISION. `.unread` adds weight; there is no
    `.read` that removes it. So a row whose flag is absent — an older reader, a cached dict, a
    store that has not been migrated yet — renders REGULAR. The other way round, the failure mode
    is an inbox where every row is shouting."""
    from marketing.customer_voice.app import CSS
    ok("the bold rule is keyed on a class that must be ADDED",
       ".conv.unread .w b{" in CSS, "")
    ok("...and there is no .conv.read rule doing the opposite", ".conv.read" not in CSS)
    # AND THE TWO WEIGHTS ARE FAR ENOUGH APART TO SEE. Both states used to sit at 590, so making
    # unread heavier alone would have produced a list nobody could read the difference in.
    base = re.search(r"\.conv \.w b\{([^}]*)\}", CSS, re.S)
    bold = re.search(r"\.conv\.unread \.w b\{([^}]*)\}", CSS, re.S)
    ok("both weights are declared", bool(base and bold))
    if base and bold:
        # THE WEIGHTS ARE THE BOX'S TOKENS NOW (the design language: Inter at 400 and 600 only,
        # docs/BOX_DESIGN_REFERENCE.md), so a token is read as the number it stands for.
        _w = {"var(--w-regular)": 400, "var(--w-strong)": 600}

        def _weight(rule):
            v = re.search(r"font-weight:\s*(\d+|var\(--w-[a-z]+\))", rule).group(1)
            return int(v) if v.isdigit() else _w[v]
        bw, uw = _weight(base.group(1)), _weight(bold.group(1))
        ok(f"...and unread is decisively heavier ({bw} vs {uw})", uw - bw >= 180, f"{bw} -> {uw}")


def test_the_avatar_recedes_with_the_rest_of_a_read_row():
    """Demoting the name left the accent monogram as the loudest thing on a quiet row — the eye
    went to the initials instead of the person. The rule it reuses was dead (`.conv.out .av`,
    and nothing ever set `conv out`) and used `--dimmer`, which is 4.27:1 on `--bubble-in` in
    DARK mode — under AA for a 15px monogram, while clearing it in light."""
    from marketing.customer_voice.app import CSS
    ok("a read row's avatar takes the neutral ground",
       ".conv:not(.unread) .av{" in CSS, "")
    rule = re.search(r"\.conv:not\(\.unread\) \.av\{([^}]*)\}", CSS, re.S)
    ok("...and not the under-contrast token the dead rule used",
       bool(rule) and "--dimmer" not in rule.group(1), rule.group(1) if rule else "")
    # THE BRACE IS LOAD-BEARING IN THIS ASSERTION. Written without it first and it failed on a
    # correct build: the comment above the replacement NAMES the rule it replaced, and this
    # stylesheet is inlined into every page, so its comments are part of the string being
    # searched. A bare name matches the prose about the deletion as readily as the deletion.
    ok("...and the dead rule is gone rather than left to rot",
       ".conv.out .av{" not in CSS)


def test_ci_actually_runs_this_file():
    """`.github/` is ours and never ships, and this suite DOES ship to a Customer Voice box — it
    imports the inbox, which that box carries — so a bare read raises FileNotFoundError in a
    paying buyer's own run. Narrow on purpose: no `.github` at all means this is not the repo."""
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_the_row_says_what_he_has_read is in the workflow's suite list",
       "test_the_row_says_what_he_has_read" in wf)


if __name__ == "__main__":
    for fn in (test_the_column_exists_at_all,
               test_it_arrives_unread_and_opening_it_makes_it_read,
               test_our_own_reply_never_makes_a_row_unread,
               test_a_reply_from_them_after_he_read_it_goes_bold_again,
               test_a_row_the_poller_knows_and_has_never_heard_from_is_not_unread,
               test_both_readers_answer_it_and_answer_it_the_same,
               test_read_is_the_resting_state_so_a_missing_flag_is_quiet,
               test_the_avatar_recedes_with_the_rest_of_a_read_row,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
