"""A dot on the Inbox tab when something is unread — owner, 2026-09-17, choosing it over a
number after seeing both rendered on the real bar.

WHY A DOT AND NOT A COUNT, recorded because the next person will want to "improve" it into a
number: from another screen the only question is whether anything is waiting. A number invites a
precision the tab then has to defend — two conversations or two messages, two people or one who
wrote twice — and the comment above `_TABS` already refuses to encode a count in the icon itself
for the same reason. The NUMBER still exists, in `.vh`, because a dot is invisible to a screen
reader exactly as the row's bold is.

WHAT THIS SUITE REALLY GUARDS is that the dot goes OUT. A mark that lights and never clears is a
mark people stop seeing, and the clearing is the half that depends on read state actually
working end to end: the store's count, the row's predicate and `mark_read` all agreeing.
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="tab-dot-")) / "box.db"
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


def _seed(zcid, who, lines, *, mins=5):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform="instagram", participant=who,
                              last_inbound_at=(NOW - timedelta(minutes=mins)).isoformat())
    for i, (d, body) in enumerate(lines):
        store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-{i}", direction=d,
                             sent_by="contact" if d == "in" else "human", body=body)


def _bar(c, path="/inbox/inbox"):
    html_ = c.get(path).get_data(as_text=True)
    m = re.search(r'<nav class="tabs".*?</nav>', html_, re.S)
    return m.group(0) if m else ""


def _lit(c, path="/inbox/inbox"):
    return 'class="mark"' in _bar(c, path)


def test_the_dot_lights_when_something_is_unread_and_goes_out_when_it_is_read():
    _wipe()
    c = _c()
    ok("a quiet box shows no dot at all", not _lit(c), _bar(c)[:200])
    _seed("t1", "Ana Ruiz", [("in", "can you quote a rewire?")])
    ok("...it lights when a message arrives", _lit(c))
    c.get("/inbox/inbox/t1")
    ok("...and goes out when he opens it", not _lit(c), _bar(c)[:200])
    # THE CYCLE, which is the whole contract. A mark that lights once and never again is a mark
    # nobody looks at by the third day.
    store.record_message(space=SPACE, zcid="t1", zmid="t1-2", direction="in",
                         sent_by="contact", body="still there?")
    ok("...and lights again when she writes back", _lit(c))


def test_it_is_on_the_inbox_tab_and_only_there():
    _wipe()
    _seed("t2", "Ben Cole", [("in", "morning")])
    bar = _bar(_c())
    ok("exactly one dot on the bar", bar.count('class="mark"') == 1, str(bar.count('class="mark"')))
    ok("...and it is inside the Inbox tab's own anchor",
       bool(re.search(r'<a class="tab[^"]*" href="/inbox/inbox"[^>]*>(?:(?!</a>).)*class="mark"',
                      bar, re.S)), bar[:300])


def test_it_is_visible_from_the_other_screens_which_is_the_point():
    """The list already says it in words and shows bold rows. The dot exists for the moment he is
    somewhere else — if it only appeared on the screen that already told him, it would be
    decoration."""
    _wipe()
    _seed("t3", "Cara Diaz", [("in", "is Friday still ok?")])
    c = _c()
    for path in ("/inbox/", "/inbox/settings"):
        ok(f"lit on {path}", _lit(c, path), _bar(c, path)[:160])


def test_a_screen_reader_gets_the_number_the_dot_cannot_say():
    _wipe()
    _seed("t4", "Dev Patel", [("in", "one")])
    _seed("t5", "Eve Lang", [("in", "two")])
    bar = _bar(_c())
    ok("the dot itself is hidden from assistive tech",
       'class="mark" aria-hidden="true"' in bar, bar[:200])
    ok("...and the count is announced instead",
       '<span class="vh">2 unread</span>' in bar, bar[:300])


def test_the_count_and_the_rows_can_never_disagree():
    """One predicate, two readers. A dot lit over a list with nothing bold in it is invisible
    until somebody looks at both at once, which is exactly when it costs the most trust."""
    _wipe()
    _seed("t6", "Fay Munro", [("in", "a")])
    _seed("t7", "Gil Ortiz", [("in", "b"), ("out", "answered")])
    _seed("t8", "Hana Kim", [("in", "c")])
    c = _c()
    c.get("/inbox/inbox/t7")
    html_ = c.get("/inbox/inbox").get_data(as_text=True)
    bold = len(re.findall(r'<a class="conv unread"', html_))
    ok("the store's count equals the number of bold rows",
       store.unread_conversations(SPACE) == bold, f"count={store.unread_conversations(SPACE)} bold={bold}")
    ok("...and it is 2 here, not 3", bold == 2, str(bold))


def test_our_own_reply_does_not_light_it():
    """`_UNREAD` is inbound-only, and the drafter mirrors machine sends through the same table."""
    _wipe()
    _seed("t9", "Ivy Nakamura", [("in", "hello?")])
    c = _c()
    c.get("/inbox/inbox/t9")
    ok("out after he reads", not _lit(c))
    store.record_message(space=SPACE, zcid="t9", zmid="t9-o", direction="out",
                         sent_by="ai", body="(machine answered)")
    ok("...and the machine answering does not light it", not _lit(c), _bar(c)[:160])


def test_a_box_that_cannot_count_loses_the_dot_not_the_page():
    """The tab bar is in `_shell`, so this number is fetched on every page this app draws. A
    failure has to cost a dot and nothing more."""
    import marketing.customer_voice.app as _app
    src = pathlib.Path(_app.__file__).read_text()
    fn = src.split("def _unread()", 1)[1].split("def _tabbar", 1)[0]
    ok("the fetch is wrapped", "except Exception" in fn, fn[:200])
    ok("...and falls back to nothing-to-report", "return 0" in fn)
    # AND PROVEN, not merely read: a store that raises must still render the page.
    _wipe()
    _seed("ta", "Jon Reyes", [("in", "hi")])
    c = _c()
    ok("lit while the store works", _lit(c))
    real = store.unread_conversations
    try:
        def boom(_space):
            raise RuntimeError("database is locked")
        store.unread_conversations = boom
        page = c.get("/inbox/inbox")
        ok("the page still answers 200 when the count raises", page.status_code == 200,
           str(page.status_code))
        ok("...and simply shows no dot", 'class="mark"' not in page.get_data(as_text=True))
    finally:
        store.unread_conversations = real


def test_the_dot_is_drawn_from_tokens_and_keeps_its_edge_on_a_translucent_bar():
    from marketing.customer_voice.app import CSS
    rule = re.search(r"\.tab \.mark\{([^}]*)\}", CSS)
    ok("the rule exists", bool(rule), CSS[:120])
    body = rule.group(1) if rule else ""
    ok("...coloured from the accent token, not a literal",
       "var(--accent)" in body and "#" not in body, body)
    # THE RING. The bar is translucent over whatever scrolls under it, so an accent dot can land
    # on accent-coloured content and disappear.
    ok("...with a ring in the bar's own colour so it never vanishes",
       "box-shadow" in body and "var(--tab-bg)" in body, body)
    ok("...positioned off the centre line, which is stable at any tab count",
       "left:50%" in body, body)


def test_ci_actually_runs_this_file():
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_the_tab_says_something_is_waiting is in the workflow's suite list",
       "test_the_tab_says_something_is_waiting" in wf)


if __name__ == "__main__":
    for fn in (test_the_dot_lights_when_something_is_unread_and_goes_out_when_it_is_read,
               test_it_is_on_the_inbox_tab_and_only_there,
               test_it_is_visible_from_the_other_screens_which_is_the_point,
               test_a_screen_reader_gets_the_number_the_dot_cannot_say,
               test_the_count_and_the_rows_can_never_disagree,
               test_our_own_reply_does_not_light_it,
               test_a_box_that_cannot_count_loses_the_dot_not_the_page,
               test_the_dot_is_drawn_from_tokens_and_keeps_its_edge_on_a_translucent_bar,
               test_ci_actually_runs_this_file):
        print(fn.__name__)
        fn()
    print(f"{_failed} FAILED" if _failed else "all ok")
    sys.exit(1 if _failed else 0)
