"""Unified Inbox — the poller + opener-handler ORCHESTRATION (Phase 1).

The Zernio SDK contract (F-1..F-6, isolation) is proven in tests/test_zernio_gateway.py
+ test_zernio_contract.py. THIS suite mocks the gateway (`zernio.client` → a fake scoped
client) and proves the orchestration the department owns: the poll watermark, STOP
routing, ad attribution, account_id threading, and the opener's five guards
(exactly-once, kill switch, opt-out, 24h window, rate cap, indeterminate-safe).

Run: python tests/test_unified_inbox.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"  # skip the box .env — scrubs must stick (core/config.py)
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "inbox.db")
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors import zernio                                    # noqa: E402


# ── a fake gateway ScopedClient the tests drive ──────────────────────────────
class FakeInbox:
    def __init__(self):
        self.list_result = {"conversations": [], "next_cursor": None}
        self.messages_result = {"messages": [], "next_cursor": None}
        self.send_impl = lambda cid, aid, text, tag: {"message_id": "out-1"}
        self.list_raises = None

    def list(self, *, limit=50):
        if self.list_raises:
            raise self.list_raises
        return self.list_result

    def messages(self, cid, account_id, *, limit=100):
        return self.messages_result

    def send(self, cid, account_id, text, *, tag=None):
        return self.send_impl(cid, account_id, text, tag)


class FakeScoped:
    def __init__(self, inbox):
        self.inbox = inbox


INBOX = FakeInbox()
zernio.client = lambda space: FakeScoped(INBOX)      # every department call hits our fake

from core import slack, state                                  # noqa: E402
from core.config import get_config                             # noqa: E402
from core.exceptions import RateCapped, RetryableError         # noqa: E402
from marketing.customer_voice.inbox import handler, poller, store, window   # noqa: E402

PASS = 0


def ok(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"PASS — {name}")
    else:
        print(f"FAIL — {name}")
        sys.exit(1)


def main():
    state.init_db()
    SLACK: list = []
    slack.post = lambda ch, text, blocks=None: SLACK.append(text) or True
    now = datetime.now(timezone.utc)

    # window: fresh inbound → freeform; stale/absent → blocked (fail closed)
    ok("window: fresh inbound is freeform",
       window.allowed_send((now - timedelta(hours=1)).isoformat()) == "freeform")
    ok("window: 25h-old inbound is blocked",
       window.allowed_send((now - timedelta(hours=25)).isoformat()) == "blocked")
    ok("window: no inbound is blocked", window.allowed_send(None) == "blocked")

    # ── poller: one job per new inbound; account_id threaded (F-1) ────────────
    # Real conversation schema: id + accountId + updatedTime; NO ad field on the
    # polling surface (F-6) — the tolerant metadata.referral read stays for the day
    # Zernio adds it, exercised here for forward-compat.
    conv = {"id": "zc-1", "accountId": "acc-mm", "updatedTime": "t1",
            "participantId": "psid-123", "participantName": "Ada L.",
            "metadata": {"referral": {"meta_ad_id": "ad-77", "meta_ad_title": "Win With AI"}}}
    msgs = {"messages": [{"id": "zm-1", "direction": "in", "message": "is this real?",
                          "createdAt": now.isoformat()}], "next_cursor": None}
    poller._spaces = lambda: [{"name": "meet-mavrick", "zernio_key": "sk_mm",
                               "zernio_profile_id": None, "slack_channel": "C_REEL"}]
    INBOX.list_result = {"conversations": [conv], "next_cursor": None}
    INBOX.messages_result = msgs
    ok("poll enqueues the new inbound once", poller.poll_sweep()["enqueued"] == 1)
    ok("re-poll (unchanged activity) enqueues nothing", poller.poll_sweep()["enqueued"] == 0)
    conv["updatedTime"] = "t2"                      # activity changed, same message
    ok("changed activity + same message enqueues nothing", poller.poll_sweep()["enqueued"] == 0)
    row = store.get_conversation("meet-mavrick", "zc-1")
    ok("ad attribution captured (forward-compat)",
       row["ad_meta_id"] == "ad-77" and row["ad_title"] == "Win With AI")
    with state.connect() as c:
        j = c.execute("SELECT raw_text FROM jobs WHERE idempotency_key=?",
                      ("inbox:meet-mavrick:zc-1:zm-1",)).fetchone()
    ok("account_id threaded into the job payload (F-1)",
       j is not None and json.loads(j["raw_text"]).get("account_id") == "acc-mm")

    # WS2 — the Messenger contact is de-anonymized into the People lake (observe-safe).
    with state.connect() as c:
        lr = c.execute("SELECT payload FROM prospects_outbox WHERE idempotency_key=?",
                       ("outbox:messenger-create:psid-123",)).fetchone()
        n_lake = c.execute("SELECT COUNT(*) AS n FROM prospects_outbox WHERE "
                           "idempotency_key=?",
                           ("outbox:messenger-create:psid-123",)).fetchone()["n"]
    p = json.loads(lr["payload"]) if lr else {}
    ok("WS2: new inbound enqueues a messenger_psid lake row",
       lr is not None and p["identities"] == [{"kind": "messenger_psid", "value": "psid-123"}]
       and p["stage_hint"] == "cold")
    ok("WS2: touch carries channel=messenger + ad attribution + shared-schema utm",
       p.get("touch", {}).get("channel") == "messenger"
       and p["touch"]["meta"]["meta_ad_id"] == "ad-77"
       and p["touch"]["utm"] == {"utm_source": "messenger", "utm_medium": "paid_social",
                                 "utm_content": "ad-77"})
    ok("WS2: idempotent — the two re-polls above did NOT double-lake", n_lake == 1)

    # ── STOP anywhere in the page is routed OVER a newer inbound ──────────────
    stop_conv = {"id": "zc-stoppage", "accountId": "acc-mm", "updatedTime": "s1", "metadata": {}}
    INBOX.list_result = {"conversations": [stop_conv], "next_cursor": None}
    INBOX.messages_result = {"messages": [
        {"id": "zm-stop", "direction": "in", "message": "STOP",
         "createdAt": (now - timedelta(minutes=5)).isoformat()},
        {"id": "zm-newer", "direction": "in", "message": "actually, tell me more",
         "createdAt": now.isoformat()}], "next_cursor": None}
    ok("poll enqueues the STOP conversation", poller.poll_sweep()["enqueued"] == 1)
    with state.connect() as c:
        j = c.execute("SELECT raw_text FROM jobs WHERE idempotency_key=?",
                      ("inbox:meet-mavrick:zc-stoppage:zm-stop",)).fetchone()
    ok("STOP routed as the target despite a newer inbound",
       j is not None and json.loads(j["raw_text"])["inbound_text"] == "STOP")
    ok("watermark advanced to the actual newest (no reprocessing loop)",
       store.get_watermark("meet-mavrick", "zc-stoppage")["last_seen_msg_id"] == "zm-newer")

    # ── a conversation with NO accountId is skipped (can't operate) ───────────
    INBOX.list_result = {"conversations": [{"id": "zc-noacct", "updatedTime": "n1"}],
                         "next_cursor": None}
    ok("conversation without accountId is skipped", poller.poll_sweep()["enqueued"] == 0)

    # ── handler: the opener path ──────────────────────────────────────────────
    get_config()["inbox"] = {"autonomy": "opener", "hourly_send_cap": 40}
    sent_calls: list = []
    handler._space = lambda name: {"name": name, "zernio_key": "sk_mm",
                                   "zernio_profile_id": None, "opener_template": "Custom opener!"}
    INBOX.send_impl = lambda cid, aid, text, tag: (
        sent_calls.append({"cid": cid, "account_id": aid, "text": text})
        or {"message_id": f"out-{len(sent_calls)}"})
    job = {"id": "j1", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-1", "account_id": "acc-mm",
         "inbound_msg_id": "zm-1", "inbound_text": "is this real?",
         "inbound_at": now.isoformat(), "ad_meta_id": "ad-77", "ad_title": "Win With AI"})}
    res = handler.handle(dict(job))
    ok("opener sent with account_id + template",
       res["status"] == "opened" and sent_calls[-1]["account_id"] == "acc-mm"
       and sent_calls[-1]["text"] == "Custom opener!")
    ok("owner notified of the new ad lead", any("New Messenger ad lead" in m for m in SLACK))
    ok("second run is already_opened (claim holds), no resend",
       handler.handle(dict(job))["status"] == "already_opened" and len(sent_calls) == 1)
    with state.connect() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM inbox_send_ledger WHERE "
                      "zernio_conversation_id='zc-1' AND status='ok'").fetchone()["n"]
    ok("exactly one ledger row", n == 1)

    # missing account_id in the payload → surfaced, never auto-replied
    job_na = {"id": "jna", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-na", "inbound_msg_id": "zm-na",
         "inbound_text": "hi", "inbound_at": now.isoformat()})}
    ok("missing account_id → no_account, no send",
       handler.handle(job_na)["status"] == "no_account" and len(sent_calls) == 1)

    # kill switch — string "off" + FAIL-CLOSED on non-active values
    for val, label in [("off", 'string "off"'), (False, "YAML-boolean off"),
                       (True, "YAML-boolean on"), ("OFF", "upper OFF"),
                       ("observer", "a typo"), ("reply", "unknown tier")]:
        get_config()["inbox"] = {"autonomy": val, "hourly_send_cap": 40}
        j = {"id": f"jfc{label}", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
            {"space": "meet-mavrick", "zcid": f"zc-{label}"[:20], "account_id": "acc-mm",
             "inbound_msg_id": "zmx", "inbound_text": "hi", "inbound_at": now.isoformat()})}
        ok(f"fail-closed: autonomy={label} observes, never sends",
           handler.handle(j)["status"] == "observed" and len(sent_calls) == 1)
    get_config()["inbox"] = {"autonomy": "opener", "hourly_send_cap": 40}

    # STOP → opted out (runs before the account_id guard — opt-out law first)
    job_stop = {"id": "j3", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-stop", "inbound_msg_id": "zm-s",
         "inbound_text": "STOP", "inbound_at": now.isoformat()})}
    store.upsert_conversation(space="meet-mavrick", zcid="zc-stop", last_inbound_at=now.isoformat())
    ok("STOP marks opted_out and sends nothing",
       handler.handle(job_stop)["status"] == "opted_out"
       and store.get_conversation("meet-mavrick", "zc-stop")["opted_out"] == 1
       and len(sent_calls) == 1)

    # stale window → blocked
    job_old = {"id": "j4", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-old", "inbound_msg_id": "zm-o",
         "inbound_text": "hi", "inbound_at": (now - timedelta(hours=30)).isoformat()})}
    ok("stale inbound is window_blocked",
       handler.handle(job_old)["status"] == "window_blocked" and len(sent_calls) == 1)

    # rate cap → RateCapped DEFERRAL before any claim (never RetryableError — that
    # path's ~15-min attempt budget terminally failed burst leads inside a 60-min
    # cap window; RateCapped requeues with not_before + a refunded attempt)
    get_config()["inbox"] = {"autonomy": "opener", "hourly_send_cap": 0}
    job_cap = {"id": "j5", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-cap", "account_id": "acc-mm",
         "inbound_msg_id": "zm-c", "inbound_text": "hi", "inbound_at": now.isoformat()})}
    try:
        handler.handle(job_cap)
        ok("hourly cap raises RateCapped", False)
    except RateCapped as e:
        ok("hourly cap raises RateCapped", True)
        nb = datetime.fromisoformat(e.not_before)
        ok("deferral lands in the future", nb > now)
        oldest = store.oldest_counted_send("meet-mavrick")
        ok("not_before = oldest counted send aging out (+1h buffer)",
           oldest is not None
           and nb >= datetime.fromisoformat(oldest) + timedelta(hours=1))
    ok("RateCapped must NOT be a RetryableError (branch-order guard)",
       not issubclass(RateCapped, RetryableError))
    cap_row = store.get_conversation("meet-mavrick", "zc-cap")
    ok("cap did not burn the claim", cap_row is None or not cap_row["opener_sent_at"])
    get_config()["inbox"] = {"autonomy": "opener", "hourly_send_cap": 40}

    # autonomy ABSENT (missing inbox block / mis-indented key) → inert, self-reports
    saved_inbox = get_config().pop("inbox", None)
    job_abs = {"id": "jabs", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-abs", "account_id": "acc-mm",
         "inbound_msg_id": "zm-a", "inbound_text": "hi", "inbound_at": now.isoformat()})}
    ok("missing inbox block observes, never sends",
       handler.handle(dict(job_abs))["status"] == "observed" and len(sent_calls) == 1)
    ok("absent-key observe self-reports the blocking tier",
       any("'unset'" in m for m in SLACK))
    get_config()["inbox"] = {}
    ok("inbox block without autonomy key observes too",
       handler.handle(dict(job_abs))["status"] == "observed" and len(sent_calls) == 1)
    get_config()["inbox"] = saved_inbox or {"autonomy": "opener", "hourly_send_cap": 40}

    # crash between claim and send → honest "claimed but no send recorded" notify
    store.upsert_conversation(space="meet-mavrick", zcid="zc-l8",
                              last_inbound_at=now.isoformat())
    ok("test setup: claim without ledger row", store.claim_opener("meet-mavrick", "zc-l8"))
    job_l8 = {"id": "jl8", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-l8", "account_id": "acc-mm",
         "inbound_msg_id": "zm-l8", "inbound_text": "hello?",
         "inbound_at": now.isoformat()})}
    res_l8 = handler.handle(dict(job_l8))
    ok("claimed-but-unsent detected and reported honestly",
       res_l8["status"] == "already_opened"
       and any("no send is recorded" in m for m in SLACK))

    # indeterminate send: claim kept, retry never resends
    INBOX.send_impl = lambda cid, aid, text, tag: (_ for _ in ()).throw(
        zernio.ZernioError("read timed out", indeterminate=True))
    job_ind = {"id": "j6", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-ind", "account_id": "acc-mm",
         "inbound_msg_id": "zm-i", "inbound_text": "hi", "inbound_at": now.isoformat()})}
    ok("indeterminate send recorded, claim kept",
       handler.handle(dict(job_ind))["status"] == "indeterminate"
       and store.get_conversation("meet-mavrick", "zc-ind")["opener_sent_at"] is not None)
    INBOX.send_impl = lambda cid, aid, text, tag: (sent_calls.append({"cid": cid})
                                                   or {"message_id": "out-X"})
    ok("retry after indeterminate never resends",
       handler.handle(dict(job_ind))["status"] == "already_opened"
       and all(c2.get("cid") != "zc-ind" for c2 in sent_calls))

    # determinate failure: claim released → the retry sends
    INBOX.send_impl = lambda cid, aid, text, tag: (_ for _ in ()).throw(
        zernio.ZernioError("400 bad request", indeterminate=False))
    job_det = {"id": "j7", "slack_channel_id": "C_REEL", "raw_text": json.dumps(
        {"space": "meet-mavrick", "zcid": "zc-det", "account_id": "acc-mm",
         "inbound_msg_id": "zm-d", "inbound_text": "hi", "inbound_at": now.isoformat()})}
    try:
        handler.handle(dict(job_det))
        ok("determinate failure raises RetryableError", False)
    except RetryableError:
        ok("determinate failure raises RetryableError", True)
    INBOX.send_impl = lambda cid, aid, text, tag: (sent_calls.append({"cid": cid})
                                                   or {"message_id": "out-Y"})
    ok("released claim lets the retry send", handler.handle(dict(job_det))["status"] == "opened")

    # ── poll-list warning is THROTTLED (no 45s log spam) ──────────────────────
    poller._poll_fail.clear()
    warns, infos = [], []
    _sv_w, _sv_i, _sv_n = poller.log.warning, poller.log.info, poller._now_s
    poller.log.warning = lambda msg, **k: warns.append(msg)
    poller.log.info = lambda msg, **k: infos.append(msg)
    clock = [1000.0]
    poller._now_s = lambda: clock[0]
    poller._spaces = lambda: [{"name": "sp-x", "zernio_key": "k",
                               "zernio_profile_id": None, "slack_channel": None}]
    INBOX.list_raises = zernio.ZernioError("PLATFORM_NOT_SUPPORTED")
    for _ in range(3):
        poller.poll_sweep()
    ok("repeated poll failure warns once (throttled)",
       sum(1 for m in warns if m == "inbox.poll_list_failed") == 1)
    ok("suppressed repeats still counted", poller._poll_fail["sp-x"]["count"] == 3)
    clock[0] += poller._POLL_WARN_THROTTLE_S + 1
    poller.poll_sweep()
    ok("re-warns after the throttle window",
       sum(1 for m in warns if m == "inbox.poll_list_failed") == 2)
    INBOX.list_raises = None
    INBOX.list_result = {"conversations": [], "next_cursor": None}
    poller.poll_sweep()
    ok("recovery logged + throttle cleared",
       "sp-x" not in poller._poll_fail and "inbox.poll_recovered" in infos)
    poller.log.warning, poller.log.info, poller._now_s = _sv_w, _sv_i, _sv_n

    # ── crash-safety: watermark must NOT advance past an un-enqueued inbound ──
    # (a worker crash between the watermark commit and the job enqueue used to
    # skip the message — including a routed STOP — forever; fix reordered them)
    convC = {"id": "zc-crash", "accountId": "acc-mm", "updatedTime": "c1",
             "participantId": "psid-crash"}
    INBOX.list_result = {"conversations": [convC], "next_cursor": None}
    INBOX.messages_result = {"messages": [{"id": "zm-c1", "direction": "in",
                                           "message": "hello?",
                                           "createdAt": now.isoformat()}],
                             "next_cursor": None}
    poller._spaces = lambda: [{"name": "meet-mavrick", "zernio_key": "sk_mm",
                               "zernio_profile_id": None, "slack_channel": None}]
    real_enqueue = poller.queue.enqueue

    def _boom(**kw):
        raise RuntimeError("job store unavailable")
    poller.queue.enqueue = _boom
    r = poller.poll_sweep()
    ok("enqueue failure aborts neither the sweep nor other conversations",
       r.get("enqueued") == 0)
    wm = store.get_watermark("meet-mavrick", "zc-crash")
    ok("watermark did NOT advance past the lost enqueue",
       not wm or wm.get("last_seen_msg_id") != "zm-c1")
    poller.queue.enqueue = real_enqueue
    ok("re-poll after recovery enqueues the inbound",
       poller.poll_sweep()["enqueued"] == 1)
    with state.connect() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE idempotency_key=?",
                      ("inbox:meet-mavrick:zc-crash:zm-c1",)).fetchone()["n"]
    ok("exactly one job exists for the recovered inbound (idempotent replay)", n == 1)
    wm = store.get_watermark("meet-mavrick", "zc-crash")
    ok("watermark advanced only after the job durably exists",
       bool(wm) and wm.get("last_seen_msg_id") == "zm-c1")

    # ── watchdog-visible poll health (heartbeats row) ─────────────────────────
    with state.connect() as c:
        hb = c.execute("SELECT status FROM heartbeats WHERE component='inbox_poll'"
                       ).fetchone()
    ok("successful sweep writes inbox_poll heartbeat 'ok'",
       hb is not None and hb["status"] == "ok")
    INBOX.list_raises = zernio.ZernioError("revoked key")
    poller.poll_sweep()
    with state.connect() as c:
        hb = c.execute("SELECT status FROM heartbeats WHERE component='inbox_poll'"
                       ).fetchone()
    ok("all-spaces-failing sweep writes 'fail'", hb and hb["status"] == "fail")
    INBOX.list_raises = None
    poller._spaces = lambda: []
    poller.poll_sweep()
    with state.connect() as c:
        hb = c.execute("SELECT status FROM heartbeats WHERE component='inbox_poll'"
                       ).fetchone()
    ok("unkeyed box marks the poll heartbeat 'disabled' (dormant ≠ dead)",
       hb and hb["status"] == "disabled")

    print(f"\nALL {PASS} TESTS PASS — unified_inbox orchestration (watermark, STOP, "
          "account_id, exactly-once, kill switch, window, cap, indeterminate) holds "
          "against the mocked gateway.")


if __name__ == "__main__":
    main()
