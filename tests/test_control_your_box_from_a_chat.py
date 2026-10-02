"""Step 4 of docs/SCOPE_INBOX_CONNECTOR.md: the rest of the screen's controls, from a person's own AI.

Owner, 2026-10-02: a buyer runs the box from their own AI ("they need full control capabilities"), and how
a change leaves the box is "One-tap approve". So stopping and starting the box, turning reply writing on or
off, throwing a written reply away and opting someone out are each a proposal: the AI asks, the owner taps
Approve, and the screen's own function runs. Nothing changes by asking.

WHAT THIS SUITE DEFENDS:
  · a read seat sees none of these proposals, and an act seat sees all of them
  · asking changes nothing; approving runs the screen's own function; declining runs nothing
  · asking for what is already true asks for nothing
  · connect is a read: where each channel is connected, never a secret

Run: python tests/test_control_your_box_from_a_chat.py
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "control.db")
os.environ["AIOS_PAUSE_FILE"] = os.path.join(tempfile.mkdtemp(), "PAUSED")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

from core import state                                                  # noqa: E402

state.init_db()

try:
    from marketing.customer_voice.schema import DDL
    from marketing.customer_voice.inbox import store, tools as inbox_tools
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)

from core import approvals, box_tools, pause                            # noqa: E402
from core.connector import tools as registry                            # noqa: E402
from marketing.customer_voice.drafter import store as drafts            # noqa: E402

with state.connect() as c:
    c.executescript(DDL)

_failed = 0
SPACE = inbox_tools._space()
OWNER = "usr_owner"
CONTROLS = ("inbox.propose_drafting", "inbox.propose_discard_draft", "inbox.propose_opt_out",
            "core.propose_stop", "core.propose_start")


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def seed():
    store.upsert_conversation(space=SPACE, zcid="k1", participant="Quinn Flowers", platform="instagram",
                              last_inbound_at="2026-09-05T00:00:00Z")
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, zernio_message_id, direction,"
                  " sent_by, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), SPACE, "k1", "m-k1", "in", "contact", "how much for a bouquet",
                   "2026-09-05T00:00:00Z"))
    drafts.put(space=SPACE, zcid="k1", in_reply_to="m-k1", body="From $45, delivered.")


def test_who_sees_them():
    print("test_who_sees_them")
    seen_read = {t["name"] for t in registry.visible_to({"role": "read"})}
    seen_act = {t["name"] for t in registry.visible_to({"role": "act"})}
    ok("a read seat sees none of the controls", not (set(CONTROLS) & seen_read), str(set(CONTROLS) & seen_read))
    ok("...and an act seat sees all of them", set(CONTROLS) <= seen_act, str(set(CONTROLS) - seen_act))
    ok("...and none is named as if it sent, posted or published",
       all(not any(w in n for w in ("send", "post", "publish")) for n in CONTROLS))
    ok("connect is a read every seat sees", "inbox.connect" in seen_read)


def test_drafting():
    print("test_drafting")
    was = inbox_tools._drafting()
    same = inbox_tools.propose_drafting(on=was)
    ok("asking for what is already true asks for nothing", same.get("asked") is False, str(same))
    ok("a missing answer is refused", "error" in inbox_tools.propose_drafting())
    a = inbox_tools.propose_drafting(on=not was)
    ok("asking changes nothing", a.get("asked") and inbox_tools._drafting() == was, str(a))
    ok("...declining changes nothing", approvals.decide(a["approval"], False, by=OWNER)["status"] == "declined"
       and inbox_tools._drafting() == was)
    b = inbox_tools.propose_drafting(on=not was)
    r = approvals.decide(b["approval"], True, by=OWNER)
    ok("...approving flips the Settings screen's own switch", r["status"] == "done"
       and inbox_tools._drafting() == (not was), str(r))


def test_discard_and_opt_out():
    print("test_discard_and_opt_out")
    ok("no written reply: nothing is asked", inbox_tools.propose_discard_draft(id="nosuch").get("asked") is False)
    a = inbox_tools.propose_discard_draft(id="k1")
    ok("the owner sees the words being thrown away",
       approvals.get(a["approval"])["detail"]["arguments"]["It said"] == "From $45, delivered.")
    ok("asking throws nothing away", any(d["zcid"] == "k1" for d in drafts.waiting(SPACE)))
    approvals.decide(a["approval"], True, by=OWNER)
    ok("approving throws it away, unsent", not any(d["zcid"] == "k1" for d in drafts.waiting(SPACE)))

    o = inbox_tools.propose_opt_out(id="k1")
    ok("asking opts nobody out", o.get("asked") and not store.get_conversation(SPACE, "k1")["opted_out"])
    approvals.decide(o["approval"], True, by=OWNER)
    ok("approving opts them out", bool(store.get_conversation(SPACE, "k1")["opted_out"]))
    ok("...and asking again asks for nothing", inbox_tools.propose_opt_out(id="k1").get("asked") is False)
    ok("an unknown conversation asks for nothing", inbox_tools.propose_opt_out(id="nosuch").get("asked") is False)


def test_stop_and_start():
    print("test_stop_and_start")
    pause.MARKER = pathlib.Path(os.environ["AIOS_PAUSE_FILE"])
    ok("starting a running box asks for nothing", box_tools.propose_start().get("asked") is False)
    s = box_tools.propose_stop(seat={"label": "My Claude"})
    ok("asking to stop stops nothing", s.get("asked") and not pause.is_paused(), str(s))
    approvals.decide(s["approval"], True, by=OWNER)
    ok("approving stops it, as the Dashboard's button does", pause.is_paused())
    g = box_tools.propose_start()
    approvals.decide(g["approval"], True, by=OWNER)
    ok("...and one more tap starts it again", not pause.is_paused())


def test_connect_is_a_read():
    print("test_connect_is_a_read")
    c = inbox_tools.connect()
    ok("it names where each channel is connected",
       [h["open"] for h in c["how"]] == ["/inbox/mailbox", "/inbox/connect", "/settings/ai"], str(c["how"]))
    ok("...and holds no secret", not any(w in str(c).lower() for w in ("password\":", "api_key", "token", "sk-ant")))


def test_the_reviews_follow_ups():
    """OSDev1's review of #1803, 2026-10-02: who approved is recorded, an unreadable switch says unknown, a batch
    too big for one approval says to ask for fewer, and the web process registers both Inbox kinds."""
    print("test_the_reviews_follow_ups")
    import subprocess
    from core import box_settings
    was = inbox_tools._drafting()
    a = inbox_tools.propose_drafting(on=not was)
    approvals.decide(a["approval"], True, by="owner@example.com")
    ok("the switch records the person who approved", box_settings.describe("inbox", "drafts.enabled")
       .get("set_by") == "owner@example.com", str(box_settings.describe("inbox", "drafts.enabled")))
    ok("...and outside an approval nobody is deciding", approvals.decider() == "")

    s = box_tools.propose_stop()
    approvals.decide(s["approval"], True, by="owner@example.com")
    ok("a stop says who approved it", "owner@example.com" in pause.status(), pause.status())
    pause.resume()

    from marketing.customer_voice.drafter import draft as _draft
    real = _draft.enabled
    _draft.enabled = lambda: (_ for _ in ()).throw(RuntimeError("settings unreadable"))
    try:
        ok("an unreadable switch reads unknown, never on",
           inbox_tools.status()["writing_replies"] == "unknown"
           and inbox_tools.settings()["settings"][0]["value"] == "unknown")
    finally:
        _draft.enabled = real

    real_propose = approvals.propose

    def too_big(*a, **k):
        raise ValueError("this proposal is too large to show a person")
    approvals.propose = too_big
    try:
        store.upsert_conversation(space=SPACE, zcid="k2", participant="Remy Cafe", platform="instagram",
                                  last_inbound_at="2026-09-06T00:00:00Z")
        r = inbox_tools.propose_reply(id="k2", body="hello")
        ok("too much for one approval says to ask for fewer", r.get("asked") is False
           and "fewer" in str(r.get("error")), str(r))
    finally:
        approvals.propose = real_propose

    code = ("import os,tempfile; os.environ['AIOS_HERMETIC_TEST']='1'; "
            "os.environ['AIOS_DB_PATH']=os.path.join(tempfile.mkdtemp(),'w.db'); "
            "os.environ.setdefault('DISPATCH_BEARER_TOKEN','x'); "
            "from core import state; state.init_db(); from core.dispatch import app; from core import approvals; "
            "print(sorted(k for k in ('inbox_send','inbox_control','box_pause') if k in approvals._KINDS))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(pathlib.Path(__file__).resolve().parents[1])).stdout.strip().splitlines()
    ok("the web process, where approvals are decided, can run every kind these tools ask for",
       bool(out) and out[-1] == "['box_pause', 'inbox_control', 'inbox_send']", str(out[-1:]))


if __name__ == "__main__":
    seed()
    test_who_sees_them()
    test_drafting()
    test_discard_and_opt_out()
    test_stop_and_start()
    test_connect_is_a_read()
    test_the_reviews_follow_ups()
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("this file is in the workflow's suite list", "test_control_your_box_from_a_chat" in _wf.read_text())
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
