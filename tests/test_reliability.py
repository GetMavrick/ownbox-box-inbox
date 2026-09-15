"""Track A (P0 reliability) tests — no network, no LLM.

Covers the four never-silent / always-a-pulse guarantees:
  A1  a terminal owner-facing job NEVER ends in silence (success-but-quiet → fallback),
      while a job that already posted, or an automated job, is left alone.
  A2  the startup backend probe records a heartbeat + returns ok/fail.
  A3  the zero-LLM `health` report answers even with the brain forced down.
  A4  the static backend-config check flags missing credentials.

Run: python tests/test_reliability.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"  # skip the box .env — scrubs must stick (core/config.py)
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "rel.db")
for _k in [k for k in os.environ if k.startswith(("SLACK_", "ANTHROPIC_", "AIRTABLE_"))]:
    os.environ.pop(_k, None)                                    # hermetic

from core import slack, state, watchdog, worker            # noqa: E402
from core import slack_socket                               # noqa: E402
from core.queue import queue                                # noqa: E402

_REAL_ALERT = watchdog._alert   # captured before A2/other tests monkeypatch _alert away


def ok(name, cond):
    assert cond, f"FAIL: {name}"
    print(f"  ok — {name}")


_POSTS = []


def _fake_post(channel, text, thread_ts=None, blocks=None):
    """Stand in for the network _post: record + flip the post-tracker exactly like the
    real one (only a confirmed send flips it)."""
    _POSTS.append({"channel": channel, "thread": thread_ts, "text": text})
    slack._posted_in_scope.set(True)
    return "ts_fake"


slack._post = _fake_post   # post() / thread_reply() / send_dm() all route through _post

from core.config import settings as _settings                  # noqa: E402
_settings.slack_bot_token = "xoxb-test"   # A1 only fires when Slack is configured


def _enqueue(**kw):
    job, _ = queue.enqueue(idempotency_key=kw.pop("idem"), agent_name="t", **kw)
    return job


# ───────────────────────────── A1 ─────────────────────────────
def test_a1_silent_success_gets_a_fallback():
    state.init_db()
    _POSTS.clear()

    def silent_handler(job):          # does real work, posts NOTHING
        return {"did": "stuff"}
    worker.HANDLERS["reel"] = silent_handler
    _enqueue(idem="a1-silent", intent="reel", raw_text="do it",
             slack_channel_id="C1", slack_thread_ts="T1")
    worker.process_one()
    fb = [p for p in _POSTS if p["channel"] == "C1" and p["thread"] == "T1"]
    ok("a silent terminal success posts a fallback into the owner's thread", len(fb) == 1)
    ok("the fallback is the terse never-silent line", "Done" in fb[0]["text"])


def test_a1_handler_that_posts_gets_no_fallback():
    state.init_db()
    _POSTS.clear()

    def chatty_handler(job):
        slack.thread_reply(job["slack_channel_id"], job["slack_thread_ts"], "here you go")
        return {}
    worker.HANDLERS["reel"] = chatty_handler
    _enqueue(idem="a1-chatty", intent="reel", raw_text="do it",
             slack_channel_id="C1", slack_thread_ts="T2")
    worker.process_one()
    texts = [p["text"] for p in _POSTS if p["thread"] == "T2"]
    ok("the handler's own message is delivered", "here you go" in texts)
    ok("NO duplicate never-silent fallback is added", not any("Done" in t for t in texts))


def test_a1_automated_job_no_thread_is_left_silent():
    state.init_db()
    _POSTS.clear()
    worker.HANDLERS["reel"] = lambda job: {}      # silent
    _enqueue(idem="a1-auto", intent="reel", raw_text="sweep",
             slack_channel_id="C1")               # NO thread → automated, not owner-asked
    worker.process_one()
    ok("an automated job (no reply thread) is NOT force-posted to", _POSTS == [])
    print("PASS — A1: owner-facing silence impossible; automated sweeps stay quiet")


# ───────────────────────────── A2 ─────────────────────────────
def test_a2_startup_probe_sets_heartbeat():
    state.init_db()
    watchdog._alert = lambda *a, **k: True        # no Slack
    watchdog.probe_backend = lambda: ("claude_code", True, "ok")
    ok("check_backend_now returns True when the backend is up", watchdog.check_backend_now() is True)
    hbs = {h["component"]: h for h in state.get_heartbeats()}
    ok("a 'brain_backend' heartbeat is recorded ok", hbs["brain_backend"]["status"] == "ok")
    watchdog.probe_backend = lambda: ("claude_code", False, "revoked token")
    ok("check_backend_now returns False when the backend is dead", watchdog.check_backend_now() is False)
    hbs = {h["component"]: h for h in state.get_heartbeats()}
    ok("the heartbeat flips to fail", hbs["brain_backend"]["status"] == "fail")
    print("PASS — A2: startup probe pages + records the brain_backend pulse")


# ───────────────────────────── A3 ─────────────────────────────
def test_a3_health_answers_with_brain_down():
    state.init_db()
    now = datetime.now(timezone.utc).isoformat()
    state.heartbeat("worker", "ok")
    state.heartbeat("brain_backend", "fail")      # brain forced DOWN
    with state.connect() as c:
        c.execute("INSERT INTO jobs (id,status,created_at,updated_at,error) "
                  "VALUES ('j1','failed',?,?,'kaboom in produce')", (now, now))
        c.execute("INSERT INTO jobs (id,status,created_at,updated_at) "
                  "VALUES ('j2','queued',?,?)", (now, now))
        c.execute("INSERT INTO alert_state (key,state) VALUES ('heygen_api','FAIL')")
    from core import health
    r = health.report()
    ok("report renders a title", "Mavrick health" in r)
    ok("brain-down shows red", ":red_circle:" in r and "brain:" in r)
    ok("queue depth is reported", "queued" in r)
    ok("the last error is surfaced", "kaboom" in r)
    ok("a failing alert is surfaced", "heygen_api" in r)
    print("PASS — A3: zero-LLM health answers even with the brain down")


# ───────────────────────────── A4 ─────────────────────────────
def test_a4_backend_config_problems():
    import core.config as cfg
    from core.config import settings
    orig_get, orig_key = cfg.get_config, settings.anthropic_api_key
    try:
        cfg.get_config = lambda: {"brain": {"backend": "api"}}
        settings.anthropic_api_key = ""
        ok("missing ANTHROPIC_API_KEY is flagged",
           any("ANTHROPIC_API_KEY" in p for p in slack_socket._backend_config_problems()))
        settings.anthropic_api_key = "sk-test"
        ok("present key → no problems", slack_socket._backend_config_problems() == [])
    finally:
        cfg.get_config, settings.anthropic_api_key = orig_get, orig_key
    print("PASS — A4: misconfigured backend is caught at boot, not per-message")


def test_deconfigured_backend_alert_is_cleared():
    """A stale FAIL alert for a backend that ISN'T the configured one (e.g. anthropic_api
    on a claude_code box) is quietly resolved, so it can't sit red in `health` forever."""
    state.init_db()
    state.set_alert("anthropic_api", "FAIL")
    state.set_alert("claude_code", "OK")
    watchdog.resolve_deconfigured_backend_alerts("claude_code")
    ok("the deconfigured backend's stale FAIL is cleared to OK",
       state.get_alert_row("anthropic_api")["state"] == "OK")
    # the CONFIGURED backend's alert is never touched by this
    state.set_alert("claude_code", "FAIL")
    watchdog.resolve_deconfigured_backend_alerts("claude_code")
    ok("the configured backend's own alert is left alone",
       state.get_alert_row("claude_code")["state"] == "FAIL")
    print("PASS — stale deconfigured-backend alert auto-resolves (clean health pulse)")


def test_still_down_reminders_back_off():
    """Owner 2026-07-13: a standing outage must NOT re-page every 4h forever. The FAIL edge is
    immediate; the 'still down' nag then backs off exponentially (4h -> ... -> 24h cap) with how
    long it's been down, so a stuck token taps once/day instead of ~6x/day."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    def since(h):
        return (now - timedelta(hours=h)).isoformat()
    ok("interval is the base early (fresh outage)", watchdog._reminder_interval_h(since(1)) == 4)
    ok("interval doubles after 12h down", watchdog._reminder_interval_h(since(13)) == 8)
    ok("interval is capped at 24h for a multi-day outage", watchdog._reminder_interval_h(since(72)) == 24)
    ok("missing since_ts degrades to the base (never crashes the pass)",
       watchdog._reminder_interval_h(None) == 4 and watchdog._reminder_interval_h("garbage") == 4)

    # integration: a 3-day-old FAIL whose last nag was 5h ago is NOT due (needs 24h now)
    state.init_db()
    watchdog._alert = _REAL_ALERT
    sent = []
    import core.slack as _slk
    _orig = _slk.send_dm
    _slk.send_dm = lambda uid, text: (sent.append(text), True)[1]
    try:
        with state.connect() as c:
            c.execute("INSERT OR REPLACE INTO alert_state (key, state, since_ts, last_alert_ts) "
                      "VALUES ('heygen','FAIL',?,?)", (since(72), since(5)))
            c.commit()
        watchdog._alert("heygen", False, "still 401")
        ok("a 3-day-old FAIL nagged 5h ago does NOT re-page (24h cap not yet elapsed)", not sent)
    finally:
        _slk.send_dm = _orig
    print("PASS — 'still down' reminders back off (page-once-then-taper, 24h cap)")


def test_watchdog_operator_unset_logs_loud():
    """#13: with OPERATOR_SLACK_USER_ID unset the Slack DM is a silent no-op — the FAIL edge
    must be LOUD in structured logs so a probe failure is never invisible on such a box."""
    state.init_db()
    from core.config import settings as _s
    errs = []
    saved_err, saved_uid = watchdog.log.error, _s.operator_slack_user_id
    watchdog.log.error = lambda msg, **k: errs.append(msg)
    _s.operator_slack_user_id = ""
    try:
        _REAL_ALERT("probe:x13demo", False, "disk gone")     # real _alert; OK→FAIL transition edge
        ok("#13 operator-unset FAIL edge logs loudly",
           "watchdog.probe_failed_operator_unset" in errs)
    finally:
        watchdog.log.error, _s.operator_slack_user_id = saved_err, saved_uid
    print("PASS — #13 watchdog logs loudly when no operator is configured to page")


def test_watchdog_run_once_gating_and_new_probes():
    """#15 slack_socket probe gates on BOTH tokens (not app-token alone → no false perpetual
    FAIL); #G3/#G8 add re-page probes for aging indeterminate ops + parked prospect rows; #G7
    run_once self-inits the schema (proven by pointing at a FRESH, schemaless DB and not crashing)."""
    import tempfile as _tmp
    from core import cost_guard
    from core.config import settings as _s
    alerted = []
    names = ("_alert", "_probe_disk", "_probe_dispatch", "_probe_worker", "_probe_heartbeat",
             "probe_backend", "resolve_deconfigured_backend_alerts", "_retire_old_raws")
    saved = {n: getattr(watchdog, n) for n in names}
    saved_cg = (cost_guard.month_to_date_spend, cost_guard.ceiling, cost_guard.metered_vendors)
    saved_reap, saved_db = state.reap_orphan_jobs, _s.db_path
    saved_tok = (_s.slack_app_token, _s.slack_bot_token, _s.heygen_api_key, _s.healthcheck_url)
    try:
        _s.db_path = os.path.join(tempfile.mkdtemp(), "wd_fresh.db")   # schemaless → tests #G7
        watchdog._alert = lambda key, ok_, detail: (alerted.append(key) or True)
        for n in ("_probe_disk", "_probe_dispatch", "_probe_worker"):
            setattr(watchdog, n, lambda *a, **k: (True, "ok"))
        watchdog._probe_heartbeat = lambda comp: (True, "ok")
        watchdog.probe_backend = lambda: ("claude_code", True, "ok")
        watchdog.resolve_deconfigured_backend_alerts = lambda k: None
        watchdog._retire_old_raws = lambda: None
        state.reap_orphan_jobs = lambda **k: ([], 0)
        cost_guard.month_to_date_spend = lambda: 0.0
        cost_guard.ceiling = lambda: 100.0
        cost_guard.metered_vendors = lambda: []
        _s.heygen_api_key = _s.healthcheck_url = ""
        _s.slack_app_token, _s.slack_bot_token = "xapp", ""       # app only
        alerted.clear()
        watchdog.run_once()   # must NOT crash on the fresh DB → proves #G7 init_db
        ok("#15 slack_socket NOT probed with only the app token", "slack_socket" not in alerted)
        ok("#G3 aging-indeterminate re-page probe registered", "stuck_indeterminate" in alerted)
        ok("stuck-produce re-page probe registered", "stuck_produce" in alerted)
        ok("#G8 parked-prospect re-page probe registered", "prospect_lake_parked" in alerted)
        ok("lake-stalled re-page probe registered", "prospect_lake_stalled" in alerted)
        ok("inbox-poll health probe registered", "inbox_poll" in alerted)
        ok("zernio-sdk drift probe registered", "zernio_sdk" in alerted)
        ok("reel-failed-unpaged probe registered", "reel_failed_unpaged" in alerted)
        _s.slack_app_token, _s.slack_bot_token = "xapp", "xoxb"   # both
        alerted.clear()
        watchdog.run_once()
        ok("#15 slack_socket probed when BOTH tokens present", "slack_socket" in alerted)
    finally:
        for n, fn in saved.items():
            setattr(watchdog, n, fn)
        cost_guard.month_to_date_spend, cost_guard.ceiling, cost_guard.metered_vendors = saved_cg
        state.reap_orphan_jobs, _s.db_path = saved_reap, saved_db
        _s.slack_app_token, _s.slack_bot_token, _s.heygen_api_key, _s.healthcheck_url = saved_tok
    print("PASS — #15 both-token gate; #G3/#G8 re-page probes; #G7 watchdog self-init")


def _content_machine_loaded() -> bool:
    """THE SCHEMA SPLIT: reel_scripts is the Content Machine's table. This suite is the kernel's and
    ships to every image, so the machine is loaded from its file, never imported by name (the
    exporter drops a suite whose imports name a machine the image lacks) — and on an image without
    it, the produce probes have nothing to probe and the test says so instead of failing on a
    missing table. Three tests share this; each must call it, not init_db() alone."""
    from pathlib import Path as _P
    _schema = _P(state.__file__).resolve().parents[1] / "marketing" / "content_machine" / "schema.py"
    if not _schema.exists():
        ok("this image carries no Content Machine, so the stuck-produce probe has nothing to watch", True)
        return False
    _ns = {}
    exec(compile(_schema.read_text(), "content_machine/schema.py", "exec"), _ns)
    state.register_schema("content", _ns["DDL"])
    state.init_db()
    return True


def test_watchdog_pages_on_stuck_produce():
    """A reel wedged in 'producing' (a render that never finished) or 'approved' (a produce job
    enqueued but never claimed) past the cutoff pages the operator — the produce path has no
    reconcile sweep of its own, so this is what turns a dead render into a page instead of a
    silent stuck dashboard row. Fresh rows and finished ('ready') reels never fire."""
    from datetime import timedelta
    if not _content_machine_loaded():
        return
    with state.connect() as c:
        c.execute("DELETE FROM reel_scripts")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=watchdog._PRODUCE_STUCK_AGE_H + 1)).isoformat()

    okp, _ = watchdog._probe_stuck_produce()
    ok("no stuck reels → probe ok", okp is True)

    with state.connect() as c:
        c.execute("INSERT INTO reel_scripts (id,created_at,updated_at,mode,status) "
                  "VALUES ('r1',?,?,'manual','producing')", (old, old))
    okp, detail = watchdog._probe_stuck_produce()
    ok("a reel stuck in producing >cutoff → probe fails", okp is False)
    ok("the detail names the count", "1 reel" in detail)

    with state.connect() as c:   # an 'approved' reel that never got claimed, aged → also fails
        c.execute("UPDATE reel_scripts SET status='approved' WHERE id='r1'")
    okp, _ = watchdog._probe_stuck_produce()
    ok("a reel stuck in approved >cutoff → probe fails", okp is False)

    with state.connect() as c:   # a freshly-updated producing reel is NOT stuck
        c.execute("UPDATE reel_scripts SET status='producing', updated_at=? WHERE id='r1'",
                  (now.isoformat(),))
    okp, _ = watchdog._probe_stuck_produce()
    ok("a freshly-updated producing reel is not flagged", okp is True)

    with state.connect() as c:   # an aged READY reel finished — never 'stuck'
        c.execute("UPDATE reel_scripts SET status='ready', updated_at=? WHERE id='r1'", (old,))
    okp, _ = watchdog._probe_stuck_produce()
    ok("an aged READY reel is not stuck (it finished)", okp is True)
    print("PASS — watchdog pages on a reel stuck in producing/approved, not on healthy states")


def test_stuck_produce_ignores_queued_backlog():
    """M26: an 'approved' reel aged past the cutoff is a lost enqueue ONLY when no live
    produce job exists for it — otherwise it's just waiting behind the sequential overnight
    render backlog on the 1-vCPU box (false page). A 'producing' reel stays a real stall
    regardless of the queue (the arm must not be suppressed). Full-uuid ids avoid LIKE
    collisions between the reel id and job raw_text."""
    from datetime import timedelta
    if not _content_machine_loaded():
        return
    with state.connect() as c:
        c.execute("DELETE FROM reel_scripts")
        c.execute("DELETE FROM jobs")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=watchdog._PRODUCE_STUCK_AGE_H + 1)).isoformat()
    approved_id ="reel-approved-0000000000000000000000000001"
    producing_id = "reel-producing-000000000000000000000000002"
    with state.connect() as c:
        for sid, st in ((approved_id, "approved"), (producing_id, "producing")):
            c.execute("INSERT INTO reel_scripts (id,created_at,updated_at,mode,status) "
                      "VALUES (?,?,?,'manual',?)", (sid, old, old, st))
        # a LIVE produce job queued for the approved reel (a legit overnight backlog item)
        c.execute("INSERT INTO jobs (id,created_at,updated_at,status,intent,idempotency_key,"
                  "raw_text) VALUES ('j-q',?,?, 'queued','reel','k-q',?)",
                  (now.isoformat(), now.isoformat(),
                   '{"mode":"produce","script_id":"' + approved_id + '"}'))
    ids = set(watchdog._stuck_produce_ids())
    ok("approved reel WITH a queued produce job is NOT flagged (backlog, not stuck)",
       approved_id not in ids)
    ok("producing reel is still flagged even with jobs around (arm intact)",
       producing_id in ids)


def test_new_stuck_reel_pages_while_already_failing():
    """M28: a SECOND reel going stuck while the aggregate key is already FAIL pages
    immediately (edge on the new id) instead of waiting up to REMINDER_HOURS. Tests the
    extracted _page_new_stuck_produce helper directly (run_once is exercised elsewhere)."""
    from core.config import settings as _s
    if not _content_machine_loaded():
        return
    with state.connect() as c:
        c.execute("DELETE FROM reel_scripts")
        c.execute("DELETE FROM jobs")
        c.execute("DELETE FROM alert_state")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=watchdog._PRODUCE_STUCK_AGE_H + 1)).isoformat()
    a = "reel-stuck-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01"
    b = "reel-stuck-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb02"
    dms, saved_dm, saved_op = [], slack.send_dm, _s.operator_slack_user_id
    slack.send_dm = lambda uid, text: (dms.append(text) or True)
    _s.operator_slack_user_id = "U_OP"
    try:
        with state.connect() as c:
            c.execute("INSERT INTO reel_scripts (id,created_at,updated_at,mode,status) "
                      "VALUES (?,?,?,'manual','producing')", (a, old, old))
        # pass 1: NOT already-failing (this is the transition pass) → no fresh-edge page,
        # but the id set gets recorded so pass 2 can diff against it.
        paged, _ = watchdog._page_new_stuck_produce(was_failing=False)
        ok("no fresh-edge page on the transition pass (avoids doubling)", paged == set())
        ok("no DM on the transition pass", dms == [])
        # pass 2: a SECOND reel goes stuck while stuck_produce is ALREADY failing
        with state.connect() as c:
            c.execute("INSERT INTO reel_scripts (id,created_at,updated_at,mode,status) "
                      "VALUES (?,?,?,'manual','producing')", (b, old, old))
        paged, sent_ok = watchdog._page_new_stuck_produce(was_failing=True)
        ok("the newly-stuck reel is paged immediately (not after 4h)", paged == {b})
        ok("the DM fired and names the new reel id", len(dms) == 1 and b[:8] in dms[0]
           and sent_ok is True)
        # pass 3: unchanged set → NO re-page
        paged, _ = watchdog._page_new_stuck_produce(was_failing=True)
        ok("no fresh-edge page when the stuck set is unchanged", paged == set() and len(dms) == 1)

        # pass 4 (review HIGH): a transient DB error in _stuck_produce_ids must NOT wipe the
        # baseline (which would make A look brand-new next pass → spurious page).
        saved_ids = watchdog._stuck_produce_ids
        watchdog._stuck_produce_ids = lambda: (_ for _ in ()).throw(
            __import__("sqlite3").OperationalError("database is locked"))
        try:
            paged, sent_ok = watchdog._page_new_stuck_produce(was_failing=True)
            ok("a transient DB error skips cleanly (no page)", paged == set() and sent_ok is True)
            ok("the baseline is preserved through the hiccup (not wiped)",
               state.get_alert("stuck_produce:ids") == ",".join(sorted({a, b})))
        finally:
            watchdog._stuck_produce_ids = saved_ids
        paged, _ = watchdog._page_new_stuck_produce(was_failing=True)
        ok("next healthy pass does NOT re-page the already-known reels", paged == set())
    finally:
        slack.send_dm = saved_dm
        _s.operator_slack_user_id = saved_op


def test_deconfigured_probe_self_clears_a_prior_fail():
    """Review MED: a probe that was FAILING and is then DECONFIGURED must self-CLEAR (not be
    held FAIL forever by the skip-hold). The deconfigured state uses no 'skip (' prefix, so
    _alert runs its normal FAIL→OK recovery — matching _probe_inbox_poll's pattern."""
    from core.config import settings as _s
    state.init_db()
    with state.connect() as c:
        c.execute("DELETE FROM alert_state")
        c.execute("DELETE FROM prospects_outbox")
    dms, saved_dm, saved_op = [], slack.send_dm, _s.operator_slack_user_id
    saved_lake = (_s.mav_ingest_url, _s.aios_ingest_secret)
    slack.send_dm = lambda uid, text: (dms.append(text) or True)
    _s.operator_slack_user_id = "U_OP"
    watchdog._alert = _REAL_ALERT
    try:
        # configured + a stalled errored row aged past the cutoff → real FAIL + page
        _s.mav_ingest_url, _s.aios_ingest_secret = "https://x/ingest", "WRONGSECRET"
        old = (datetime.now(timezone.utc)
               - timedelta(hours=watchdog._LAKE_STALL_AGE_H + 1)).isoformat()
        with state.connect() as c:
            c.execute("INSERT INTO prospects_outbox (id,created_at,idempotency_key,payload,"
                      "attempts,error) VALUES ('s1',?,'k','{}',1,'HTTP 401')", (old,))
        okp, detail = watchdog._probe_prospect_lake_stalled()
        watchdog._alert("prospect_lake_stalled", okp, detail)
        ok("a real lake stall pages", state.get_alert_row("prospect_lake_stalled")["state"] == "FAIL")
        # operator remediates the way the probe's hint suggests: blanks the bad secret
        _s.aios_ingest_secret = ""
        okp, detail = watchdog._probe_prospect_lake_stalled()
        ok("deconfigured returns OK with NO 'skip (' prefix", okp is True
           and not detail.startswith("skip ("))
        watchdog._alert("prospect_lake_stalled", okp, detail)
        ok("the prior FAIL self-clears to OK (not stuck red forever)",
           state.get_alert_row("prospect_lake_stalled")["state"] == "OK")
    finally:
        slack.send_dm = saved_dm
        _s.operator_slack_user_id = saved_op
        _s.mav_ingest_url, _s.aios_ingest_secret = saved_lake


def test_probe_prospect_lake_stalled():
    """H2: a configured lake with unsent+errored rows aged past the cutoff pages (the
    auth-stall gap #117 opened). Dormant/unconfigured → skip. Never-errored backlog → OK."""
    from datetime import timedelta
    from core.config import settings as _s
    state.init_db()
    with state.connect() as c:
        c.execute("DELETE FROM prospects_outbox")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=watchdog._LAKE_STALL_AGE_H + 1)).isoformat()
    saved = (_s.mav_ingest_url, _s.aios_ingest_secret)
    try:
        _s.mav_ingest_url, _s.aios_ingest_secret = "", ""
        ok("unconfigured lake → skip (never pages)", watchdog._probe_prospect_lake_stalled()[0])
        _s.mav_ingest_url, _s.aios_ingest_secret = "https://x/ingest", "sec"
        with state.connect() as c:
            c.execute("INSERT INTO prospects_outbox (id,created_at,idempotency_key,"
                      "payload,attempts,error) VALUES ('o1',?,'k1','{}',0,NULL)", (old,))
        ok("aged unsent row with NO error does not page (dormant backlog)",
           watchdog._probe_prospect_lake_stalled()[0] is True)
        with state.connect() as c:
            c.execute("UPDATE prospects_outbox SET error='HTTP 401' WHERE id='o1'")
        ok("aged unsent row WITH an error pages (auth stall)",
           watchdog._probe_prospect_lake_stalled()[0] is False)
    finally:
        _s.mav_ingest_url, _s.aios_ingest_secret = saved


def test_probe_inbox_poll_health():
    """M6-read: heartbeats('inbox_poll') → probe. missing/disabled = OK (dormant), fail =
    page, stale ok = page."""
    state.init_db()
    with state.connect() as c:
        c.execute("DELETE FROM heartbeats WHERE component='inbox_poll'")
    ok("no inbox_poll heartbeat → OK (not active)", watchdog._probe_inbox_poll()[0] is True)
    state.heartbeat("inbox_poll", "disabled")
    ok("disabled (unkeyed box) → OK, never pages", watchdog._probe_inbox_poll()[0] is True)
    state.heartbeat("inbox_poll", "ok")
    ok("fresh ok → healthy", watchdog._probe_inbox_poll()[0] is True)
    state.heartbeat("inbox_poll", "fail")
    ok("all-spaces-failed → pages", watchdog._probe_inbox_poll()[0] is False)
    # stale ok (poller wedged): backdate the ts
    with state.connect() as c:
        stale = (datetime.now(timezone.utc)
                 - timedelta(seconds=watchdog._INBOX_POLL_MAX_AGE_S + 60)).isoformat()
        c.execute("UPDATE heartbeats SET status='ok', ts=? WHERE component='inbox_poll'",
                  (stale,))
    ok("stale ok heartbeat → pages (poller wedged)", watchdog._probe_inbox_poll()[0] is False)


def test_alert_skip_does_not_false_recover():
    """A probe that returns (True, 'skip (...)') COULDN'T EVALUATE — it must not flip a real
    standing FAIL to a false ':white_check_mark: recovered' DM (a transient DB error on one
    pass would otherwise clear a genuine alert)."""
    state.init_db()
    from core.config import settings as _s
    dms, saved_dm, saved_op = [], slack.send_dm, _s.operator_slack_user_id
    slack.send_dm = lambda uid, text: (dms.append(text) or True)
    _s.operator_slack_user_id = "U_OP"
    watchdog._alert = _REAL_ALERT      # use the real _alert, not the A2 stub
    try:
        with state.connect() as c:
            c.execute("DELETE FROM alert_state WHERE key='demo_probe'")
        watchdog._alert("demo_probe", False, "3 things broken")     # real FAIL edge
        ok("a real FAIL pages once", len(dms) == 1 and "FAILED" in dms[0])
        watchdog._alert("demo_probe", True, "skip (OperationalError)")   # transient hiccup
        ok("a 'skip' does NOT send a false recovered DM", len(dms) == 1)
        ok("the FAIL state is held through the skip",
           state.get_alert_row("demo_probe")["state"] == "FAIL")
        watchdog._alert("demo_probe", True, "0 things broken")      # genuine recovery
        ok("a substantive OK recovers properly", len(dms) == 2 and "recovered" in dms[1])
    finally:
        slack.send_dm = saved_dm
        _s.operator_slack_user_id = saved_op
    print("PASS — a 'skip' probe result holds state, never false-recovers a real alert")




# ───────────────────────────── A5 (W1.6) ─────────────────────────────
def test_a5_done_never_regresses_to_failed():
    """A job that reached 'done' has had its (possibly paid) side effects — a later
    queue.fail (notifier raise in the generic except, reaper race) must not flip it to
    'failed' and tell the owner a succeeded job failed."""
    state.init_db()
    job = _enqueue(idem="a5-done", intent="reel", raw_text="x")
    state.update_job(job["id"], status="running")
    queue.complete(job["id"], {"ok": 1})
    queue.fail(job["id"], "late notifier explosion")          # must be a no-op
    row = state.get_job(job["id"])
    assert row["status"] == "done", f"done regressed to {row['status']}"
    assert row["result"], "result survived"
    # a running job still fails normally (the guard is scoped to done, not a blanket)
    job2 = _enqueue(idem="a5-run", intent="reel", raw_text="y")
    state.update_job(job2["id"], status="running")
    queue.fail(job2["id"], "real failure")
    assert state.get_job(job2["id"])["status"] == "failed", "running->failed still works"
    print("PASS — A5: done is terminal; running still fails normally")


def test_a5_notifier_raise_cannot_fail_a_completed_job():
    """_never_silent runs after complete; its raise used to land in the worker's generic
    except -> queue.fail -> done overwritten + failure hook + ':warning:' to the owner."""
    state.init_db()
    _POSTS.clear()
    # process_one() takes the OLDEST queued job, so this test owns the table. It used to inherit
    # the DELETE the stuck-reel tests ran just before it; on an image without the Content Machine
    # those return early, and a queued job left by an earlier suite on the box's shared database
    # was processed instead of ours (the shipped-suite harness, 2026-09-13).
    with state.connect() as c:
        c.execute("DELETE FROM jobs")
    job = _enqueue(idem="a5-notify", intent="reel", raw_text="z")
    worker.HANDLERS["reel"] = lambda j: {"ok": 1}
    saved = worker._never_silent
    worker._never_silent = lambda j: (_ for _ in ()).throw(RuntimeError("slack down"))
    try:
        worker.process_one()
    finally:
        worker._never_silent = saved
    row = state.get_job(job["id"])
    assert row["status"] == "done", f"notifier raise flipped status to {row['status']}"
    assert not any("didn't go through" in p["text"] for p in _POSTS), \
        "owner was told a succeeded job failed"
    print("PASS — A5: a notifier raise can no longer fail a completed job")


if __name__ == "__main__":
    test_alert_skip_does_not_false_recover()
    test_a1_silent_success_gets_a_fallback()
    test_a1_handler_that_posts_gets_no_fallback()
    test_a1_automated_job_no_thread_is_left_silent()
    test_a2_startup_probe_sets_heartbeat()
    test_a3_health_answers_with_brain_down()
    test_a4_backend_config_problems()
    test_deconfigured_backend_alert_is_cleared()
    test_still_down_reminders_back_off()
    test_watchdog_operator_unset_logs_loud()
    test_watchdog_run_once_gating_and_new_probes()
    test_watchdog_pages_on_stuck_produce()
    test_stuck_produce_ignores_queued_backlog()
    test_new_stuck_reel_pages_while_already_failing()
    test_probe_prospect_lake_stalled()
    test_probe_inbox_poll_health()
    test_deconfigured_probe_self_clears_a_prior_fail()
    test_a5_done_never_regresses_to_failed()
    test_a5_notifier_raise_cannot_fail_a_completed_job()
    print("\nALL TRACK A RELIABILITY TESTS PASS")
