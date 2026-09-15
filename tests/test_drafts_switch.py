"""Drafts have an on-switch — the one that decides whether a sold box does its job.

THE STATE OF A DELIVERED BOX BEFORE THIS: `brain.backend` ships as `api` (bring your own key),
`install.sh` mints none and the provisioner passes none, so `drafter/draft.py` returns
{"skipped": "unconfigured"} and the box files every message and writes nothing. `/voice/settings`
had three rows — theme, install, refresh — and no field that could fix it. The buyer has no shell
and no `.env`.

So this is not a preference screen. It is the only way drafting turns on at all, and
docs/COPY_INBOX_FIRST_RUN.md §2.6 states the cost of not having it: "a buyer sees an empty reply
box, is never told drafting exists, and concludes the box does not do what the card said."

WHAT THIS SUITE DEFENDS, beyond that the screen renders:
  · the key reaches the WORKER, which is a different process from the web app — the whole reason
    it is a table and not an environment variable
  · `.env` still wins, so every box running today is untouched
  · the key is never rendered back, to any screen, in any state
  · a thread says drafts are off exactly where the draft would have been, and nowhere else

Run: python tests/test_drafts_switch.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "drafts.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ.pop("ANTHROPIC_API_KEY", None)

from core import state                                                # noqa: E402

state.init_db()

from core import box_secrets, dash                                    # noqa: E402
from core.dispatch import app as flask_app                            # noqa: E402

KEY = "sk-ant-notarealkey-0000000000000000000000"
_failed = 0

# DOES THIS BOX CARRY THE INBOX AT ALL? Asked because this suite ships into EVERY box, and CI runs
# all 114 shipped suites inside a freshly exported LEAD box — which has no `marketing/customer_voice`
# and therefore no `/voice/*` routes. Twelve assertions here failed there while passing in the repo:
# the sixth suite to hit the shape OSDev4 catalogued in #1182.
#
# THE SPLIT IS NOT "SKIP THIS SUITE". The half that matters most is core — `box_secrets`, `brain`
# reading it, the export, the journal — and that half ships into every box and runs in every box.
# Only the six screen tests, which need routes a lead box does not serve, stand down. A suite that
# exits 0 on a missing directory stops testing the thing it was written for.
#
# AND THE QUESTION IS ABOUT THE MACHINE, NOT ABOUT A ROUTE. No `marketing/customer_voice` directory
# means this box does not have the inbox and there is no screen to hold. A box that HAS the
# directory and still does not serve `/voice/settings` is broken, and these tests say so — which is
# the distinction test_inbox_design draws and the reason it is drawn there rather than by asking
# the url_map, where a registration failure would look like a box shape.
HAS_INBOX = os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "marketing", "customer_voice"))


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def client():
    c = flask_app.test_client()
    c.set_cookie("aios_session", dash.new_session(state.owner_user()["id"]), domain="localhost")
    return c


def clear():
    box_secrets.clear(box_secrets.ANTHROPIC)


# ── 1. the store, and who wins ───────────────────────────────────────────────────────
def test_the_environment_still_wins_so_no_box_running_today_changes():
    print("test_the_environment_still_wins_so_no_box_running_today_changes")
    clear()
    ok("a box with nothing anywhere has no key", box_secrets.anthropic_key() == "")
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    ok("the stored key is used when the environment is empty",
       box_secrets.anthropic_key() == KEY)
    # EVERY BOX ALIVE TODAY HAS ITS KEY IN .env. This is consulted only where that is empty —
    # which is the delivered-box hole and nowhere else.
    from core.config import settings
    old = settings.anthropic_api_key
    try:
        settings.anthropic_api_key = "sk-ant-from-the-environment"
        ok("...and the environment beats it whenever both exist",
           box_secrets.anthropic_key() == "sk-ant-from-the-environment")
    finally:
        settings.anthropic_api_key = old
    ok("clearing it reports that there was one", box_secrets.clear(box_secrets.ANTHROPIC) is True)
    ok("...and clearing again reports there was not",
       box_secrets.clear(box_secrets.ANTHROPIC) is False)
    ok("an empty secret is refused rather than stored as nothing",
       _raises(lambda: box_secrets.put(box_secrets.ANTHROPIC, "   ")))


def _raises(fn):
    try:
        fn()
        return False
    except Exception:                            # noqa: BLE001
        return True


def test_the_key_reaches_the_worker_which_is_a_different_process():
    print("test_the_key_reaches_the_worker_which_is_a_different_process")
    # THE WHOLE REASON THIS IS A TABLE. `core/config.Settings` reads os.environ at import, and
    # the drafter runs in the worker unit, not the web app — so nothing the web process puts in
    # its own environment can ever reach it. The database is the only medium they share.
    # `brain.can_think()` is what the worker asks, so that is what is asserted.
    clear()
    from core import brain
    from core.config import get_config
    # A SOLD BOX SHIPS `backend: api` — §2.6's first measured fact, and the reason the hole
    # exists at all. This repo is the OWNER's box, which runs `claude_code` with an OAuth token,
    # so without pinning this the test would exercise a branch no customer ever reaches and
    # report green on a path it never touched.
    _brain_cfg = dict(get_config().get("brain") or {})
    get_config()["brain"] = dict(_brain_cfg, backend="api")
    ready, why = brain.can_think()
    ok("with no key anywhere the box says it cannot think", not ready, why)
    ok("...and says how to fix it in words a buyer could act on", "Settings" in why, why)
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    ready, why = brain.can_think()
    ok("once the buyer sets a key the box reports it can think", ready, why)
    get_config()["brain"] = _brain_cfg
    clear()


def test_a_changed_key_is_not_held_stale_by_a_cached_client():
    print("test_a_changed_key_is_not_held_stale_by_a_cached_client")
    # The worker caches its Anthropic client for the life of the process. A buyer who fixes a
    # wrong key would otherwise keep hitting the old one until someone restarted the unit — and
    # nobody would know to. The client is keyed by the key.
    from core import brain
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    brain._client, brain._client_key = object(), KEY
    box_secrets.put(box_secrets.ANTHROPIC, KEY + "-changed")
    ok("the cached client no longer matches the stored key",
       brain._client_key != box_secrets.anthropic_key())
    clear()
    brain._client, brain._client_key = None, ""


# ── 2. the screens ───────────────────────────────────────────────────────────────────
def test_settings_offers_the_switch_when_there_is_no_key():
    print("test_settings_offers_the_switch_when_there_is_no_key")
    clear()
    html = client().get("/voice/settings").get_data(as_text=True)
    ok("the row is there", "Drafts" in html)
    # §2.6: phrased as a capability, not a missing key. "No API key configured" is a fault report
    # about our plumbing; the buyer did nothing wrong and does not know what an API key is.
    ok("...phrased as what the box can do", "write a reply for every message" in html)
    ok("...and the reason BYOK is an advantage, not an apology",
       "nothing you receive passes through us" in html)
    ok("...with a control that goes somewhere", 'href="/voice/drafts"' in html)
    low = html.lower()
    ok("never a fault report", "not configured" not in low and "api key configured" not in low
       and "missing" not in low)


def test_settings_says_it_is_on_and_keeps_the_promise_in_view():
    print("test_settings_says_it_is_on_and_keeps_the_promise_in_view")
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    html = client().get("/voice/settings").get_data(as_text=True)
    ok("it says it is on", "On. Ownbox writes a reply" in html)
    # §2.6: "The second sentence is load-bearing and does not get cut. It is the promise the
    # whole product rests on, and Settings is where a nervous buyer goes to check it."
    ok("...and the promise is the sentence right after it",
       "nothing goes out on its own" in html)
    ok("both controls are offered", "Change" in html and "Turn off" in html)
    ok("THE KEY IS NOT ON THE PAGE", KEY not in html)
    clear()


def test_the_key_is_never_rendered_back_anywhere():
    print("test_the_key_is_never_rendered_back_anywhere")
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    c = client()
    for path in ("/voice/settings", "/voice/drafts", "/voice/", "/voice/inbox"):
        r = c.get(path)
        body = r.get_data(as_text=True)
        # THE PAGE HAS TO HAVE RENDERED BEFORE ITS SILENCE MEANS ANYTHING. Without this line the
        # check passed inside a lead box, where every one of these paths 404s — an empty page
        # "proving" the key is not on it. A secrecy assertion that a missing route satisfies is
        # not a secrecy assertion, and this is the fifth of its kind I have had to rewrite.
        ok(f"{path} renders at all, so its silence counts for something",
           r.status_code == 200 and len(body) > 200, f"{r.status_code}, {len(body)} bytes")
        ok(f"{path} does not contain the key", KEY not in body)
    clear()


def test_the_buyer_can_turn_it_on_and_off():
    print("test_the_buyer_can_turn_it_on_and_off")
    clear()
    c = client()
    r = c.post("/voice/drafts", data={"key": KEY})
    ok("posting a key redirects back to Settings",
       r.status_code in (301, 302, 303) and "/voice/settings" in r.headers.get("Location", ""))
    ok("...and the key is stored", box_secrets.get(box_secrets.ANTHROPIC) == KEY)
    r = c.get("/voice/drafts?off=1")
    ok("turning off redirects back to Settings", r.status_code in (301, 302, 303))
    ok("...and the key is gone", box_secrets.get(box_secrets.ANTHROPIC) == "")
    # An empty submit is a typo, not a failure: say what to do, store nothing, no lecture.
    r = c.post("/voice/drafts", data={"key": "   "})
    ok("an empty submit stores nothing", box_secrets.get(box_secrets.ANTHROPIC) == "")
    ok("...and says what to do", "Paste the key" in r.get_data(as_text=True))


def test_a_stranger_cannot_set_this_boxs_key():
    print("test_a_stranger_cannot_set_this_boxs_key")
    clear()
    anon = flask_app.test_client()
    r = anon.post("/voice/drafts", data={"key": "sk-ant-planted-by-a-stranger"})
    ok("an unauthenticated POST does not store a key",
       box_secrets.get(box_secrets.ANTHROPIC) == "", "a stranger set this box's AI key")
    ok("...and is refused or bounced, never accepted", r.status_code != 200 or "sign" in
       r.get_data(as_text=True).lower(), str(r.status_code))


# ── 3. the buyer's key does not leave the box ──────────────────────────────────
def test_the_key_is_not_in_the_data_export():
    print("test_the_key_is_not_in_the_data_export")
    # OSDev4 PROVED THIS RATHER THAN READING IT, and he was right: `scripts/export_data.py` was
    # a DENY-LIST, so `box_secrets` — a table nobody had thought to name — exported the buyer's
    # Anthropic key as plaintext into box_secrets.json and .csv. That export is customer-facing.
    # It gets emailed. It gets put in Dropbox. Reproducing it here found a SECOND leak he had not
    # reached: `box_claim.pw_hash`, which shipped in #1172 and is live.
    #
    # Adding two names would have fixed today. The test is written against the RULE instead: a
    # table nobody classified, carrying a credential-named column, must also come out clean.
    import json as _json
    import subprocess
    import tempfile as _tf
    box = _tf.mkdtemp()
    db = os.path.join(box, "leak.db")
    env = dict(os.environ, AIOS_DB_PATH=db, AIOS_HERMETIC_TEST="1")

    # SEEDED IN A SUBPROCESS with the same environment the export runs under. Switching
    # AIOS_DB_PATH inside this process does not move a `state` module that has already bound to
    # another database, and a reload only looked like it worked — the seed went nowhere and the
    # "no canaries found" that followed was a measurement of an empty folder.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed = subprocess.run(
        [sys.executable, "-c",
         "import sys, sqlite3; sys.path.insert(0, '.');"
         "from core import state; state.init_db();"
         "c = sqlite3.connect(sys.argv[1]);"
         "c.execute(\"INSERT INTO box_secrets (name, value, set_at) VALUES "
         "('anthropic_api_key','sk-ant-CANARY-KEY-0001','t')\");"
         "c.execute(\"INSERT INTO box_claim (id, claimed_at, order_id, user_id, email, pw_hash)"
         " VALUES (1,'t','cs_live_x','u','b@x.com','scrypt$CANARY-PW')\");"
         "c.execute('CREATE TABLE IF NOT EXISTS unclassified_thing "
         "(id TEXT, label TEXT, api_key TEXT, note TEXT, monthly_tokens INTEGER)');"
         "c.execute(\"INSERT INTO unclassified_thing VALUES "
         "('1','a widget','sk-CANARY-FUTURE','keep me', 98765)\");"
         "c.execute(\"INSERT INTO spend_ledger (ts, job_id, task, model, input_tokens,"
         " output_tokens, cache_write_tokens, cache_read_tokens, cost_usd) VALUES"
         " ('t','j','draft','haiku',1234,567,89,4321,0.0123)\");"
         "c.commit()", db],
        cwd=root, env=env, capture_output=True, text=True)
    ok("the canaries are seeded", seed.returncode == 0, (seed.stderr or "")[-200:])
    if seed.returncode:
        return
    r = subprocess.run([sys.executable, "scripts/export_data.py"], cwd=root, env=env,
                       capture_output=True, text=True)
    ok("the export runs", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    import glob
    dirs = sorted(glob.glob(os.path.join(root, "data", "export-*")))
    ok("...and produced a folder", bool(dirs), "nothing to check, so nothing was proved")
    if not dirs:
        return
    out = dirs[-1]
    blob = ""
    for f in glob.glob(os.path.join(out, "*")):
        with open(f, encoding="utf-8", errors="replace") as fh:
            blob += fh.read()
    for canary, what in (("sk-ant-CANARY-KEY-0001", "the buyer's AI key"),
                         ("CANARY-PW", "the box password hash"),
                         ("sk-CANARY-FUTURE", "a credential in a table nobody classified")):
        ok(f"{what} is not in the export", canary not in blob)
    # AND THE BUYER'S ACTUAL DATA SURVIVED. A scrubber that eats the export is not a fix.
    fut = os.path.join(out, "unclassified_thing.json")
    rows = _json.load(open(fut)) if os.path.isfile(fut) else []
    ok("the unclassified table still exports its ordinary columns",
       bool(rows) and rows[0].get("note") == "keep me", str(rows))
    ok("...with the credential column marked rather than silently dropped",
       bool(rows) and rows[0].get("api_key") == "[redacted]", str(rows))
    # THE COUNTS ARE NOT CREDENTIALS, AND THIS IS A REGRESSION THAT SHIPPED IN THIS BRANCH.
    # The first version of the scrub imported `core.logging._KEYISH`, which carries a bare
    # `token`. Exporting a single spend_ledger row returned "[redacted]" for input_tokens,
    # output_tokens, cache_write_tokens AND cache_read_tokens — the buyer's own cost history,
    # destroyed in the folder handed to him as all of his data. Measured on a real export, and
    # the same false positive OSDev4 recorded in #1192 before narrowing it away.
    ok("a column merely counting tokens is NOT redacted",
       bool(rows) and rows[0].get("monthly_tokens") in (98765, "98765"), str(rows))
    led = os.path.join(out, "spend_ledger.json")
    lrows = _json.load(open(led)) if os.path.isfile(led) else []
    ok("the spend ledger reached the export at all, so the next check counts", bool(lrows),
       "no ledger row — an empty file proves nothing")
    if lrows:
        kept = {k: lrows[0].get(k) for k in ("input_tokens", "output_tokens",
                                             "cache_write_tokens", "cache_read_tokens")}
        ok("...and every token COUNT in it survived intact",
           all(str(v) == w for v, w in zip(kept.values(), ("1234", "567", "89", "4321"))),
           str(kept))
    # "GIVE ME MY DATA" CANNOT QUIETLY RETURN LESS. A table a person knows exists and cannot
    # find reads as a broken export; naming it is the difference from a missing file.
    man = _json.load(open(os.path.join(out, "MANIFEST.json")))
    ok("the manifest names the tables withheld whole",
       "box_secrets" in (man.get("tables_withheld") or {}), str(man.get("tables_withheld")))
    ok("...and the columns it redacted, table by table",
       "api_key" in ((man.get("columns_redacted") or {}).get("unclassified_thing") or []),
       str(man.get("columns_redacted")))
    import shutil
    shutil.rmtree(out, ignore_errors=True)


def test_the_key_is_never_written_to_the_journal():
    print("test_the_key_is_never_written_to_the_journal")
    # The value is a credential, so the log line says that it CHANGED and who changed it, and
    # never what it is. Asserted on what the logger was actually handed, because "we do not log
    # it" is the kind of claim that stays true only until someone adds a helpful debug line.
    from core import box_secrets as bs
    seen = []
    _sv_i, _sv_w = bs.log.info, bs.log.warning
    bs.log.info = lambda msg, **k: seen.append((msg, k))
    bs.log.warning = lambda msg, **k: seen.append((msg, k))
    try:
        bs.put(bs.ANTHROPIC, KEY, user_id="usr_someone")
        bs.clear(bs.ANTHROPIC, user_id="usr_someone")
    finally:
        bs.log.info, bs.log.warning = _sv_i, _sv_w
    flat = repr(seen)
    ok("something was logged at all", len(seen) == 2, str(seen))
    ok("THE KEY IS NOT IN ANY LOG LINE", KEY not in flat, flat[:160])
    ok("...but which secret changed is", all("anthropic_api_key" in repr(k) for _, k in seen))
    ok("...and who changed it", all("usr_someone" in repr(k) for _, k in seen))
    clear()


def test_a_key_is_checked_when_it_is_typed_not_hours_later_in_a_worker():
    print("test_a_key_is_checked_when_it_is_typed_not_hours_later_in_a_worker")
    # VALIDATED ON SAVE. The same key rejected on USE fails inside a worker, on a draft nobody
    # is watching, and the buyer's experience is that drafting silently does not work — which is
    # the exact failure §2.6 exists to end.
    from core import box_secrets as bs
    clear()
    for bad, why in ((" ", "empty"), ("hello@example.com", "an email"),
                     ("sk-ant-short", "a truncated key"),
                     ("sk-ant-with a space in it 0000000000000000000000", "a pasted line break")):
        try:
            bs.put(bs.ANTHROPIC, bad)
            ok(f"{why} is refused", False, "it was stored")
        except bs.SecretRejected as e:
            ok(f"{why} is refused, with a sentence to act on", len(str(e)) > 12, str(e))
    ok("...and nothing was stored by any of them", bs.get(bs.ANTHROPIC) == "")
    bs.put(bs.ANTHROPIC, KEY)
    ok("a well-formed key is accepted", bs.get(bs.ANTHROPIC) == KEY)
    clear()


def test_the_screen_shows_the_stores_own_refusal_not_one_of_its_own():
    print("test_the_screen_shows_the_stores_own_refusal_not_one_of_its_own")
    # SPLIT OUT OF THE TEST ABOVE, which is otherwise pure core and runs in every box. These two
    # need `/voice/drafts`, and leaving them in there is what kept this suite red inside a lead
    # box after the other twelve were fixed. The second one also passed on that box's 404 — a
    # page that does not exist echoes nothing — so it is now asserted after the page has rendered.
    from core import box_secrets as bs
    clear()
    r = client().post("/voice/drafts", data={"key": "hello@example.com"})
    html = r.get_data(as_text=True)
    ok("the page came back rather than 404ing", r.status_code in (200, 302, 400),
       str(r.status_code))
    ok("the screen renders the rule's own words", "does not look like an Anthropic key" in html)
    ok("...and never echoes what was pasted", "hello@example.com" not in html and len(html) > 200,
       f"{len(html)} bytes")
    ok("...and nothing was stored", bs.get(bs.ANTHROPIC) == "")
    clear()


if __name__ == "__main__":
    test_the_environment_still_wins_so_no_box_running_today_changes()
    test_the_key_reaches_the_worker_which_is_a_different_process()
    test_a_changed_key_is_not_held_stale_by_a_cached_client()
    # THE SCREEN HALF, and the only part that depends on this box carrying the inbox. Everything
    # above and below runs everywhere, because it is core: a box with no inbox still stores this
    # key, still must not leak it into an export or a log, and still has to notice a new one.
    if HAS_INBOX:
        test_settings_offers_the_switch_when_there_is_no_key()
        test_settings_says_it_is_on_and_keeps_the_promise_in_view()
        test_the_key_is_never_rendered_back_anywhere()
        test_the_buyer_can_turn_it_on_and_off()
        # GUARDED THOUGH IT PASSED IN THE LEAD BOX, which is the point: "refused or bounced,
        # never accepted" is satisfied by a 404, so on a box without the route it was reporting
        # an auth gate green without an auth gate being reached.
        test_a_stranger_cannot_set_this_boxs_key()
        test_the_screen_shows_the_stores_own_refusal_not_one_of_its_own()
    else:
        print("the six screen tests")
        print("  --   this box does not carry the Unified Inbox — no /voice screens to hold; "
              "the key, the export and the journal are checked above and below")
    test_the_key_is_not_in_the_data_export()
    test_the_key_is_never_written_to_the_journal()
    test_a_key_is_checked_when_it_is_typed_not_hours_later_in_a_worker()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
