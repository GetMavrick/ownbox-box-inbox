"""Drafts have an on-switch — the one that decides whether a sold box does its job.

THE STATE OF A DELIVERED BOX BEFORE THIS: `brain.backend` ships as `api` (bring your own key),
`install.sh` mints none and the provisioner passes none, so `drafter/draft.py` returns
{"skipped": "unconfigured"} and the box files every message and writes nothing. `/inbox/settings`
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
# and therefore no `/inbox/*` routes. Twelve assertions here failed there while passing in the repo:
# the sixth suite to hit the shape OSDev4 catalogued in #1182.
#
# THE SPLIT IS NOT "SKIP THIS SUITE". The half that matters most is core — `box_secrets`, `brain`
# reading it, the export, the journal — and that half ships into every box and runs in every box.
# Only the six screen tests, which need routes a lead box does not serve, stand down. A suite that
# exits 0 on a missing directory stops testing the thing it was written for.
#
# AND THE QUESTION IS ABOUT THE MACHINE, NOT ABOUT A ROUTE. No `marketing/customer_voice` directory
# means this box does not have the inbox and there is no screen to hold. A box that HAS the
# directory and still does not serve `/inbox/settings` is broken, and these tests say so — which is
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


class _Refused(Exception):
    """Stands in for the SDK's AuthenticationError / PermissionDeniedError, by STATUS.

    `brain._is_transient` and `verify_key` both read `status_code` and the class NAME, and a real
    `anthropic.AuthenticationError` cannot be constructed without an httpx response. Matching on
    the attribute is what the spine actually does — see `_TRANSIENT_EXC_NAMES` and the comment
    above it explaining why the SDK is never imported to classify an error."""
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


class APIConnectionError(Exception):
    """NAMED, not numbered — this is the half of the split that carries no status at all, and the
    name is the only thing `_is_transient` can match it on."""


def vendor(answer):
    """Put a stand-in `anthropic` on sys.modules for the length of a `with` block.

    NO NETWORK, IN EITHER ENVIRONMENT, AND THAT IS THE POINT OF INJECTING RATHER THAN SKIPPING.
    `anthropic>=0.40` is a real dependency (pyproject.toml:7), so on a machine that has it a test
    posting a fake key would make a REAL call to Anthropic and get a real 401 — passing for the
    wrong reason, costing a round trip, and failing in a sandbox with no egress. `sys.modules`
    wins over the installed package, so the same stub serves both.

    `answer` is 200 for a key the vendor accepts, an int status for one it refuses or a transient
    failure, or an exception instance to raise as-is."""
    import contextlib
    import types

    class _Models:
        def list(self, **kw):
            if answer == 200:
                return object()
            raise answer if isinstance(answer, Exception) else _Refused(answer)

    class _Anthropic:
        def __init__(self, **kw):
            # WHAT THE PROBE IS BUILT WITH IS ASSERTED, not assumed: a probe that inherited
            # `max_retries=4` from the worker's client would sit on a dead network for a minute
            # with a person watching a button, and one built without the pasted key would report
            # a verdict about whatever is already stored on the box.
            self.kw = kw
            self.models = _Models()
            _Anthropic.last = kw

    @contextlib.contextmanager
    def _cm():
        mod = types.ModuleType("anthropic")
        mod.Anthropic = _Anthropic
        old = sys.modules.get("anthropic")
        sys.modules["anthropic"] = mod
        try:
            yield _Anthropic
        finally:
            if old is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = old
    return _cm()


def no_vendor():
    """A box whose own SDK will not import — the sandbox, and the half-installed box."""
    import contextlib
    import types

    @contextlib.contextmanager
    def _cm():
        broken = types.ModuleType("anthropic")   # a module with no `Anthropic` in it: the import
        old = sys.modules.get("anthropic")       # `from anthropic import Anthropic` raises
        sys.modules["anthropic"] = broken
        try:
            yield
        finally:
            if old is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = old
    return _cm()


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
    html = client().get("/inbox/settings").get_data(as_text=True)
    ok("the row is there", "Drafts" in html)
    # §2.6: phrased as a capability, not a missing key. "No API key configured" is a fault report
    # about our plumbing; the buyer did nothing wrong and does not know what an API key is.
    ok("...phrased as what the box can do", "write a reply for every message" in html)
    ok("...and the reason BYOK is an advantage, not an apology",
       "nothing you receive passes through us" in html)
    ok("...with a control that goes somewhere", 'href="/inbox/drafts"' in html)
    low = html.lower()
    ok("never a fault report", "not configured" not in low and "api key configured" not in low
       and "missing" not in low)


def test_the_threads_offer_lands_where_the_thing_is_turned_on():
    print("test_the_threads_offer_lands_where_the_thing_is_turned_on")
    # THE ONE PLACE A BUYER MEETS THE MISSING DRAFT is the thread, under the reply box, and the
    # line there is an OFFER: "Turn them on." It used to link to /inbox/settings, which turns
    # nothing on — the buyer then had to find "Connect an AI account" to reach the one field they
    # wanted. Two clicks for a promise worded as one, on the first thread of their first day.
    # Found by walking an exported customer_voice box as customer #1 (OSDev4, 2026-09-17).
    clear()
    from marketing.customer_voice.inbox import store
    store.upsert_conversation(space="default", zcid="conv-offer", platform="instagram",
                              participant="Priya", last_inbound_at="2026-09-17T18:00:00Z",
                              account_id="acc-1")
    store.record_message(space="default", zcid="conv-offer", zmid="m-offer", direction="in",
                         sent_by="contact", body="Are you open Sunday?")
    html = client().get("/inbox/inbox/conv-offer").get_data(as_text=True)
    ok("the thread says drafts are off", "Drafts are off" in html)
    ok("...and the offer goes to the page that TURNS THEM ON, not to the menu that lists it",
       'href="/inbox/drafts"' in html, html[html.find("Drafts are off"):][:200])
    ok("...and it no longer sends them to Settings to go looking",
       'href="/inbox/settings" style="color:var(--accent)">Turn' not in html)

    # AND THE DESTINATION REALLY IS THE PLACE, rather than another signpost: it carries the field.
    dest = client().get("/inbox/drafts").get_data(as_text=True)
    ok("the page it lands on has the key field", 'name="key"' in dest)
    ok("...and a control that says what it does", "Turn drafts on" in dest)


def test_settings_says_it_is_on_and_keeps_the_promise_in_view():
    print("test_settings_says_it_is_on_and_keeps_the_promise_in_view")
    box_secrets.put(box_secrets.ANTHROPIC, KEY)
    html = client().get("/inbox/settings").get_data(as_text=True)
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
    for path in ("/inbox/settings", "/inbox/drafts", "/inbox/", "/inbox/inbox"):
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
    # A VENDOR THAT SAYS YES, because since `put_anthropic` the screen asks one. Before this the
    # line below stored a key nobody had checked, which is the whole of what this PR fixes.
    with vendor(200):
        r = c.post("/inbox/drafts", data={"key": KEY})
    ok("posting a key redirects back to Settings",
       r.status_code in (301, 302, 303) and "/inbox/settings" in r.headers.get("Location", ""))
    ok("...and the key is stored", box_secrets.get(box_secrets.ANTHROPIC) == KEY)
    r = c.get("/inbox/drafts?off=1")
    ok("turning off redirects back to Settings", r.status_code in (301, 302, 303))
    ok("...and the key is gone", box_secrets.get(box_secrets.ANTHROPIC) == "")
    # An empty submit is a typo, not a failure: say what to do, store nothing, no lecture.
    r = c.post("/inbox/drafts", data={"key": "   "})
    ok("an empty submit stores nothing", box_secrets.get(box_secrets.ANTHROPIC) == "")
    ok("...and says what to do", "Paste the key" in r.get_data(as_text=True))


def test_a_stranger_cannot_set_this_boxs_key():
    print("test_a_stranger_cannot_set_this_boxs_key")
    clear()
    anon = flask_app.test_client()
    r = anon.post("/inbox/drafts", data={"key": "sk-ant-planted-by-a-stranger"})
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


def test_a_well_formed_dud_never_reaches_the_table():
    print("test_a_well_formed_dud_never_reaches_the_table")
    # THE HOLE THIS SUITE'S OWN TITLE CLAIMED WAS CLOSED. The test above is named "a key is
    # checked when it is typed, not hours later in a worker" and it checked a SHAPE, so the one
    # key a shape cannot catch went straight in: measured on an exported customer_voice box on
    # 2026-09-17, `sk-ant-api03-` + 80 characters was stored, `can_think()` answered (True,
    # 'api'), and /inbox/settings then stated "Drafts On. Ownbox writes a reply for every message
    # that arrives" on a box where it never would.
    from core import box_secrets as bs
    dud = "sk-ant-api03-" + "A" * 80
    clear()
    with vendor(401):
        try:
            bs.put_anthropic(dud)
            ok("a key the vendor refuses is not stored", False, "it was stored")
        except bs.SecretRejected as e:
            ok("a key the vendor refuses is not stored", bs.get(bs.ANTHROPIC) == "")
            ok("...and the sentence sends them to the console, not to support",
               "console" in str(e).lower(), str(e))
    # 403 IS A DIFFERENT FIX FROM 401 AND SO IT IS A DIFFERENT SENTENCE. An account out of credit
    # refuses a perfectly good key, and a person told "we did not recognise it" mints a second
    # one and hits the identical wall.
    with vendor(403):
        try:
            bs.put_anthropic(dud)
            ok("a refusal for credit is not stored either", False, "it was stored")
        except bs.SecretRejected as e:
            ok("a refusal for credit is not stored either", bs.get(bs.ANTHROPIC) == "")
            ok("...and says credit rather than telling them to re-copy the key",
               "credit" in str(e).lower(), str(e))
    clear()


def test_unreachable_is_not_refused():
    print("test_unreachable_is_not_refused")
    # OSDEV5'S RULE FROM THE MAILBOX, AND IT BINDS HARDER HERE. Told "your key is wrong", a person
    # goes to the console and makes a NEW key they did not need — and now has two, one of which
    # they believe is broken. A bad minute must never produce that sentence.
    from core import box_secrets as bs
    good = "sk-ant-api03-" + "B" * 80
    for label, answer in (("a rate limit", 429), ("a 5xx", 503),
                          ("a dropped connection", APIConnectionError("reset")),
                          ("a box whose own SDK will not import", None)):
        clear()
        ctx = no_vendor() if answer is None else vendor(answer)
        with ctx:
            try:
                bs.put_anthropic(good)
                ok(f"{label}: nothing is stored", False, "it was stored")
            except bs.SecretRejected as e:
                said = str(e).lower()
                ok(f"{label}: nothing is stored", bs.get(bs.ANTHROPIC) == "")
                ok(f"{label}: says try again, and does not condemn the key",
                   "try again" in said and "did not recognise" not in said
                   and "credit" not in said, str(e))
    clear()


def test_the_probe_is_built_with_the_pasted_key_and_does_not_wait_a_minute():
    print("test_the_probe_is_built_with_the_pasted_key_and_does_not_wait_a_minute")
    # TWO WAYS THIS COULD BE WRITTEN AND STILL LOOK GREEN. A probe built from `_client_()` would
    # authenticate whatever is ALREADY on the box — so pasting a dud over a working key would
    # report success. And a probe that inherited the worker's `max_retries=4` would back off four
    # times on a dead network while a person watches a button that has not moved.
    from core import box_secrets as bs
    from core import brain
    clear()
    bs.put(bs.ANTHROPIC, "sk-ant-api03-" + "O" * 80)   # something already on the box
    fresh = "sk-ant-api03-" + "N" * 80
    with vendor(200) as Fake:
        brain.verify_key(fresh)
        ok("the probe carries the key that was just pasted", Fake.last.get("api_key") == fresh,
           str(Fake.last.get("api_key"))[:20])
        ok("...and does not inherit the worker's four retries",
           Fake.last.get("max_retries") == 0, str(Fake.last.get("max_retries")))
        ok("...and is bounded by a timeout a person would wait out",
           0 < float(Fake.last.get("timeout") or 0) <= 30, str(Fake.last.get("timeout")))
    clear()


def test_a_refusal_days_later_reaches_both_screens_and_they_agree():
    print("test_a_refusal_days_later_reaches_both_screens_and_they_agree")
    # THE HALF THE PROBE CANNOT COVER. A key checked on Tuesday can be revoked on Friday, or its
    # account can run out of credit — and the box finds out in the worker, where nobody is
    # looking. Before this, Settings went on stating "Drafts On. Ownbox writes a reply for every
    # message that arrives" and the thread note stayed silent, because both asked `is_set`.
    from core import box_secrets as bs
    from marketing.customer_voice import app as _app
    clear()
    with vendor(200):
        bs.put_anthropic(KEY)
    ok("a working key reads connected", bs.anthropic_state()["status"] == "connected")
    ok("...and Settings says so", "On. Ownbox writes a reply" in _app._drafts_row())

    bs.note_anthropic_status("needs_reauth", "401 invalid x-api-key")
    row = _app._drafts_row()
    ok("a revoked key moves the Settings row off On", "On. Ownbox writes a reply" not in row, row[:120])
    ok("...and says what to do rather than naming a 401",
       "Paste a new key" in row and "401" not in row, row[:160])

    bs.note_anthropic_status("payment_required", "403 credit balance too low")
    row = _app._drafts_row()
    ok("an account out of credit gets its OWN sentence, not the re-paste one",
       "credit" in row.lower() and "Paste a new key" not in row, row[:160])

    # THE TWO SCREENS READ ONE VALUE. They disagreed by construction before: Settings asked
    # `is_set` and so did the thread, so a refused key made both of them wrong in the same breath
    # — and any future fix to one alone would have made them wrong in DIFFERENT directions.
    import inspect
    thread = inspect.getsource(_app)
    ok("neither screen decides this from `is_set` any more",
       "is_set(box_secrets.ANTHROPIC)" not in thread, "a screen still asks whether a key exists")

    # THE THREAD NOTE ITSELF, RENDERED. `_compose` is called directly rather than through the
    # page because a full thread needs a polled `account_id` and an open window to draw a reply
    # box at all — and none of that is what this is about. `conv` carries what the compose box
    # needs and nothing else.
    conv = {"zernio_conversation_id": "c9", "platform": "messenger", "account_id": "acct_1",
            "last_inbound_at": state._now(), "opted_out": 0}
    # AND ONE INBOUND MESSAGE, because that is what decides whether there is a box to draw.
    #
    # THIS IS A SEMANTIC CONFLICT RESOLVED, not a new requirement. These four calls arrived on
    # main with #1358 written against `_compose(zcid, conv)`; #1304 changed the signature to
    # `(zcid, conv, msgs)` because the box used to be gated on `conv["last_inbound_at"]` — an
    # inference a poller fills, NULL on threads the customer demonstrably wrote on — and now
    # asks the messages themselves. Both changes were green apart and the merge broke: exactly
    # the failure the merge run exists to find. `last_inbound_at` above is left as it was; it
    # still dates the send window, which is the other half of what this conv is for.
    msgs = [{"direction": "in", "sent_by": "contact", "body": "are you open Sunday?",
             "created_at": state._now()}]
    bs.note_anthropic_status("needs_reauth", "401")
    box = _app._compose("c9", conv, msgs)
    ok("the thread says drafting is PAUSED, where the draft would have been",
       "Drafts are paused" in box, box[-220:] if box else "(no reply box rendered)")
    ok("...and does not claim they were never turned on",
       "Drafts are off" not in box)
    # EACH NOTE GOES WHERE ITS OWN FIX IS — #1356's rule, and the two paused states do not share
    # a fix. A key the vendor no longer accepts is replaced in the key form; an account with no
    # credit is fixed in the Anthropic console, and Settings is where that sentence and its link
    # live. Sending the second one to the key form would offer a control that cannot help.
    ok("...and sends them to the form that replaces the key, not to a menu",
       'href="/inbox/drafts"' in box and "Paste a new one" in box, box[-260:])
    bs.note_anthropic_status("payment_required", "403")
    box = _app._compose("c9", conv, msgs)
    ok("an account out of credit is told so on the thread too",
       "needs credit" in box, box[-260:])
    ok("...and goes to Settings, because the fix is a card in the console, not a field here",
       'href="/inbox/settings"' in box and "/inbox/drafts" not in box, box[-260:])
    bs.note_anthropic_status("needs_reauth", "401")
    with vendor(200):
        bs.put_anthropic(KEY)
    ok("...and says nothing at all once the key works again",
       "Drafts are paused" not in _app._compose("c9", conv, msgs))
    bs.clear_anthropic()
    ok("...while a box with no key still gets the original off note",
       "Drafts are off" in _app._compose("c9", conv, msgs))
    with vendor(200):
        bs.put_anthropic(KEY)

    # AND TURNING IT OFF TAKES THE STATUS WITH IT, or the next key inherits this refusal.
    bs.clear_anthropic()
    ok("turning drafts off clears the vendor's last word too",
       bs.get(bs.ANTHROPIC_STATUS) == "" and bs.get(bs.ANTHROPIC_DETAIL) == "")
    with vendor(200):
        bs.put_anthropic(KEY)
    ok("...so a fresh key is not greeted with the old one's refusal",
       bs.anthropic_state()["status"] == "connected")
    clear()


def test_the_drafter_records_a_refusal_but_never_a_bad_minute():
    print("test_the_drafter_records_a_refusal_but_never_a_bad_minute")
    # THE JUDGEMENT THAT MATTERS MOST HERE. This runs unattended every few minutes; a rate limit
    # that wrote `needs_reauth` would tell a buyer their working key had stopped working, and
    # they would go and make a new one. Only a refusal is a refusal.
    from core import box_secrets as bs
    from marketing.customer_voice.drafter import draft as D
    for label, exc, expected in (
            ("a 401", _Refused(401), "needs_reauth"),
            ("a 403", _Refused(403), "payment_required"),
            ("a rate limit", _Refused(429), "connected"),
            ("a 5xx", _Refused(503), "connected"),
            ("a dropped connection", APIConnectionError("reset"), "connected"),
            ("a cap, which is not the key's fault", ValueError("over budget"), "connected")):
        clear()
        with vendor(200):
            bs.put_anthropic(KEY)
        D._note_if_refused(exc)
        ok(f"{label} -> {expected}", bs.anthropic_state()["status"] == expected,
           bs.anthropic_state()["status"])
    # AND A DRAFT THAT LANDS CLEARS IT, because `payment_required` is fixed in the console and
    # nobody comes back to Settings to say so. Without this the row sticks red over a box that
    # has quietly started drafting again.
    bs.note_anthropic_status("payment_required", "403")
    D._note_recovered()
    ok("a draft that lands clears a standing refusal",
       bs.anthropic_state()["status"] == "connected")

    # AND THE SWEEP ACTUALLY CALLS IT. Everything above tests the two helpers directly, which is
    # green whether or not `draft_one` ever reaches them — deleting the call site left this suite
    # passing on the first run of it. So the last two go through the real path.
    from core import brain, cost_guard
    real_think, real_check = brain.think, cost_guard.check_vendor
    try:
        cost_guard.check_vendor = lambda *a, **k: None
        clear()
        with vendor(200):
            bs.put_anthropic(KEY)
        brain.think = lambda **kw: (_ for _ in ()).throw(_Refused(401))
        D.draft_one(space="default", zcid="c1", in_reply_to="m1", inbound="hello", history=[])
        ok("a refusal inside the sweep reaches the row a buyer reads",
           bs.anthropic_state()["status"] == "needs_reauth", bs.anthropic_state()["status"])
        brain.think = lambda **kw: "Sure — we open at nine."
        D.draft_one(space="default", zcid="c2", in_reply_to="m2", inbound="hello", history=[])
        ok("...and a draft that lands in the sweep clears it again",
           bs.anthropic_state()["status"] == "connected", bs.anthropic_state()["status"])
    finally:
        brain.think, cost_guard.check_vendor = real_think, real_check
    clear()


def test_the_screen_shows_the_stores_own_refusal_not_one_of_its_own():
    print("test_the_screen_shows_the_stores_own_refusal_not_one_of_its_own")
    # SPLIT OUT OF THE TEST ABOVE, which is otherwise pure core and runs in every box. These two
    # need `/inbox/drafts`, and leaving them in there is what kept this suite red inside a lead
    # box after the other twelve were fixed. The second one also passed on that box's 404 — a
    # page that does not exist echoes nothing — so it is now asserted after the page has rendered.
    from core import box_secrets as bs
    clear()
    r = client().post("/inbox/drafts", data={"key": "hello@example.com"})
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
        test_the_threads_offer_lands_where_the_thing_is_turned_on()
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
        print("  --   this box does not carry the Unified Inbox — no /inbox screens to hold; "
              "the key, the export and the journal are checked above and below")
    test_the_key_is_not_in_the_data_export()
    test_the_key_is_never_written_to_the_journal()
    test_a_key_is_checked_when_it_is_typed_not_hours_later_in_a_worker()
    test_a_well_formed_dud_never_reaches_the_table()
    test_unreachable_is_not_refused()
    test_the_probe_is_built_with_the_pasted_key_and_does_not_wait_a_minute()
    # BOTH NEED THE INBOX: one renders `_drafts_row`, the other imports the drafter.
    if HAS_INBOX:
        test_a_refusal_days_later_reaches_both_screens_and_they_agree()
        test_the_drafter_records_a_refusal_but_never_a_bad_minute()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
