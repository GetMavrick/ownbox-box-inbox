"""The key features gate: the box uses each key feature the way a buyer does, and says whether each one works.

Owner, 2026-10-02: *"This is a key feature that can never break again… Without these features, we are nothing."*
In 24 hours both his own-AI sign-in and the MCP connector broke on his box, and nothing told us before he did.
OSDev1 assigned the gate the same evening: the box TESTS ITSELF, because nobody can reach a buyer's box from
outside (by design), and its check-in carries the result, so the release tool can refuse a release whose demo box
is red and the fleet can page when a delivered box turns red.

FOUR CHECKS, each the buyer's own path, never a shortcut around it:
  1. MCP CONNECTOR. A temporary seat this module mints and revokes, then a real client's session against the
     box's OWN public address (through Caddy and TLS, as a Claude app reaches it, but dialled on this box's own
     loopback so the credential never leaves it): initialize, the initialized notification, ping, tools/list,
     tools/call on EVERY read tool the list names, and one old 'aios.' name. A name in tools/list that tools/call
     refuses is a failure, and so is a tool whose schema a client can't take. The box's own tools only: never a
     connected app's, which would spend his quota with that app every hour on made-up arguments.
  2. THE AI ANSWERS. A real answer in the last 6 hours is the proof (core/ai_health.py records every one);
     without one, the same one-question test as Settings → AI. A box whose AI answers is asked at most once in
     6 hours, as #1820's probe.
  3. THE MORNING REVIEW BUILDS for yesterday, as the preview the page shows: no AI, no send, nothing stored.
  4. AN APPROVAL ROUND TRIP: propose, approve, done exactly once, and a second approve does nothing. Quietly
     (nobody's phone is told) and removed after, so the owner's approvals list never shows the box testing itself.

WHAT LEAVES THE BOX: counts and the names of what failed ("mcp:aeo.status", "ai"), never an error's text, an
answer, a credential or anything about a person. The detail stays in this box's own log.

EACH FAILURE SAYS WHOSE IT IS (#1857 H1, from OSDev4's review): `why` maps each failed name to `ours` (the box's own
break, which blocks a release and pages OSDev1), `vendor` (the AI app was down or busy: reported, never blocking) or
`unset` (no AI signed in yet, or the buyer's sign-in needs them: a fresh box is like this by design). Without it an
Anthropic outage would stop every release and every new box would page on its first hour. Only the AI check can be
anything but `ours`: the connector, the review and the approval are the box's own code.

Run: python -m core.key_features   (aios-keyfeatures.timer, hourly; it nudges a check-in after each run)
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import ssl
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.logging import get_logger

log = get_logger(__name__)

LAST = Path(os.environ.get("AIOS_KEY_FEATURES_LAST") or "/var/lib/aios/key_features.json")
LOCAL = "http://127.0.0.1:8000"          # gunicorn's own bind (deploy/aios-dispatch.service), when no address is set
OLD_NAME = "aios.core.health"            # the prefix #1743 dropped; old clients still call it (#1829)
PROTOCOL = "2025-06-18"
NAME_MAX = 64
FAILED_MAX = 20                          # names carried in the check-in, at most
AI_FRESH_S = 6 * 3600                    # the proof window #1820 settled on (core/watchdog.py)
KIND = "key_features_check"
SEAT_LABEL = "Key features check"
ERROR_META = "io.ownbox/error"           # core/connector/mcp.py puts an error's code here
_SAMPLE = {"integer": 1, "number": 1, "string": "ownbox", "boolean": False}
_RAN: list = []                          # the approval kind's runs, counted by the round trip
OURS, VENDOR, UNSET = "ours", "vendor", "unset"
REASONS = (OURS, VENDOR, UNSET)
_AI_LAST: dict = {}                      # the last AI test this run asked, so its failure can say whose it is
WEB_WAIT_S = 120                         # at boot the timer can fire before gunicorn listens (OnBootSec=4min)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


# ── 1. the MCP connector, as a client ───────────────────────────────────────────────────────────────────────
def base_url() -> str:
    from core.config import settings
    return (getattr(settings, "dashboard_base_url", "") or LOCAL).rstrip("/")


def _Loopback(host: str, port: int, **kw):
    """TLS to the box's public name, dialled on its own loopback. Caddy picks the site by SNI and Host and checks
    the certificate as a client does, but the socket never leaves the box, so the seat's credential cannot reach
    another host whatever DNS says. The same pinned connection the sign-in's fetch uses (core/net.py)."""
    from core import net
    return net.PinnedHTTPSConnection(host, "127.0.0.1", port, **kw)


def _http(url: str, body: dict, headers: dict) -> tuple[int, dict | None]:
    """One POST, the way a client sends it. -> (status, parsed JSON or None).

    NEVER FOLLOWS A REDIRECT (http.client has none to follow): a 3xx comes back as its status and fails the step,
    so the Authorization header is only ever sent to the one address asked for."""
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        conn = _Loopback(u.hostname, u.port or 443, timeout=30, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=30)
    try:
        conn.request("POST", u.path or "/", body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json",
                              "Accept": "application/json, text/event-stream", **headers})
        r = conn.getresponse()
        status, raw = r.status, r.read()
    finally:
        conn.close()
    try:
        return status, (json.loads(raw) if raw else None)
    except ValueError:
        return status, None


APPS = "read:apps"                       # a connected app's tools (core/connections/gateway.py): never called here


def _read_capabilities() -> list:
    """The read role's table plus every machine's own read grant, less the connected apps'. Asked of the registry
    after the box's machines have loaded, so it matches a real seat's."""
    from core.connector import tools
    return sorted(c for c in tools.held({"role": "read"}) if tools.ai_may_hold(c) and c != APPS)


def clean_up() -> int:
    """Undo what a run that never reached its `finally` left: systemd kills a run at its time limit, and that run's
    seat would stay live (hidden from his list, so nobody would ever revoke it) and its approval would wait on his
    Approvals screen for a week. Called at the START of every run. -> how many things it undid."""
    from core import state
    from core.connector import seats
    with state.connect() as c:
        # ONLY this gate's own: a live run seat (it carries capabilities) with this label. A connection the owner
        # happened to name the same is never a run seat, and is never touched.
        left = [r[0] for r in c.execute(
            "SELECT id FROM seats WHERE label = ? AND revoked_at IS NULL"
            " AND id IN (SELECT seat_id FROM seat_capabilities)", (SEAT_LABEL,)).fetchall()]
    for seat_id in left:
        seats.revoke(seat_id)
    with state.connect() as c:
        rows = c.execute("DELETE FROM approvals WHERE kind = ?", (KIND,)).rowcount or 0
    if left or rows:
        log.warning("key_features.cleaned_up", seats=len(left), approvals=rows)
    return len(left) + rows


def _sample_args(schema: dict) -> dict:
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    out = {}
    for name in schema.get("required") or []:
        t = (props.get(name) or {}).get("type")
        out[name] = _SAMPLE.get(t if isinstance(t, str) else "string", "ownbox")
    return out


def check_mcp(post=None, url: str | None = None) -> tuple[int, list]:
    """(passed, failed names). `post(url, body, headers) -> (status, json)` is the client; a test passes its own."""
    from core.connector import seats
    post = post or _http
    url = (url or base_url()) + "/mcp"
    passed, failed = 0, []
    seat_id, cred = seats.mint(SEAT_LABEL, "read", capabilities=_read_capabilities())
    auth = {"Authorization": f"Bearer {cred}"}

    def rpc(method, params=None, rpc_id=1, version=True):
        body = {"jsonrpc": "2.0", "method": method}
        if rpc_id is not None:
            body["id"] = rpc_id
        if params is not None:
            body["params"] = params
        return post(url, body, {**auth, **({"MCP-Protocol-Version": PROTOCOL} if version else {})})

    def step(name, good: bool, why: str = ""):
        nonlocal passed
        if good:
            passed += 1
        else:
            failed.append(f"mcp:{name}")
            log.warning("key_features.mcp_failed", step=name, why=why[:200])

    try:
        status, body = rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "ownbox-key-features", "version": "1"}},
                           version=False)
        step("initialize", status == 200 and isinstance((body or {}).get("result"), dict), f"{status} {body}")
        status, _ = rpc("notifications/initialized", rpc_id=None)
        step("notifications/initialized", status == 202, str(status))
        status, body = rpc("ping", rpc_id=2)
        step("ping", status == 200 and isinstance((body or {}).get("result"), dict), f"{status} {body}")
        status, body = rpc("tools/list", {}, rpc_id=3)
        listed = ((body or {}).get("result") or {}).get("tools") if status == 200 else None
        step("tools/list", isinstance(listed, list) and bool(listed), f"{status} {body}")
        for t in listed or []:
            name = str(t.get("name") or "")
            schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
            if len(name) > NAME_MAX or schema.get("type") != "object":
                step(f"{name}:schema", False, "a client can't take this tool's name or schema")
                continue
            args = _sample_args(schema)
            status, body = rpc("tools/call", {"name": name, "arguments": args}, rpc_id=4)
            res = (body or {}).get("result")
            # EVERY LISTED TOOL MUST ANSWER. "Not connected yet" is the tool working (a shipped box's commonest
            # state). Any other error is the break this gate exists for: a tool the list shows and the call refuses
            # (forbidden, a bad or missing argument), a crash, a JSON-RPC error or a failed request. Told apart by
            # the error's code (mcp.ERROR_META), never by its words. Measured 2026-10-02: on a fresh box every read
            # tool answers its sample arguments, or says not_configured.
            good = (status == 200 and isinstance(res, dict) and
                    (not res.get("isError") or (res.get("_meta") or {}).get(ERROR_META) == "not_configured"))
            step(name, good, f"{status} {body}"[:300])
        status, body = rpc("tools/call", {"name": OLD_NAME, "arguments": {}}, rpc_id=5)
        res = (body or {}).get("result")
        step(OLD_NAME, status == 200 and isinstance(res, dict) and not res.get("isError"), f"{status} {body}")
    except Exception as e:                               # noqa: BLE001 — the connector didn't answer at all
        step("connect", False, f"{type(e).__name__}: {e}")
    finally:
        seats.revoke(seat_id)
    return passed, failed


# ── 2. the AI answers ───────────────────────────────────────────────────────────────────────────────────────
def check_ai() -> bool:
    from core import ai_health
    st = ai_health.state()
    ok_at = str((st.get("last_ok") or {}).get("at") or "")
    fail_at = str((st.get("last_fail") or {}).get("at") or "")
    try:
        fresh = ok_at and ok_at > fail_at and \
            _now() - datetime.fromisoformat(ok_at) < timedelta(seconds=AI_FRESH_S)
    except ValueError:
        fresh = False
    if fresh:
        return True                                      # a real answer in 6 hours: no question needed
    got = ai_health.test()
    _AI_LAST.clear()
    _AI_LAST.update(got if isinstance(got, dict) else {})
    return bool(_AI_LAST.get("ok"))


def _ai_signed_in() -> bool:
    """Is an AI signed in at all, on the backend this box thinks with? The same credentials brain.can_think reads,
    asked directly so a missing CLI (our break) is never mistaken for a missing sign-in (the buyer's step)."""
    from core import box_secrets, brain
    held = {"codex": box_secrets.codex_connected, "claude_code": box_secrets.claude_oauth_token,
            "api": box_secrets.anthropic_key}.get(brain._backend())
    return held is None or bool(held())


def ai_why() -> str:
    """Whose failure the AI check's is: `unset` with no sign-in, else what the test's exception said (ai_health.kind),
    else `ours`. Never raises: a question it can't answer is ours."""
    try:
        if not _ai_signed_in():
            return UNSET
    except Exception:                                    # noqa: BLE001
        return OURS
    k = _AI_LAST.get("kind")
    return k if k in (VENDOR, UNSET) else OURS


# ── 3. the Morning Review builds ────────────────────────────────────────────────────────────────────────────
# ── 5. upkeep, checked rather than assumed (plan #1857 H14) ─────────────────────────────────────────────────────
UPGRADES_TIMER = "apt-daily-upgrade.timer"             # Ubuntu's own: it runs unattended-upgrades every day
UPGRADES_MAX_AGE_S = 48 * 3600
CERT_MIN_DAYS = 14


# WHERE THE TIMER WRITES ITS LAST RUN (Persistent=true timers keep a stamp file; its mtime is the last trigger).
# MEASURED ON UBUNTU 24.04, systemd 255 (OSDev1, 2026-10-03): `systemctl show` on a timer has no
# LastTriggerUSecRealtime at all, and prints LastTriggerUSec formatted ("Sat 2026-10-03 06:29:24 UTC") whatever
# --timestamp says. Reading the missing property made every box older than 48 hours page "red upgrades".
UPGRADES_STAMP = "/var/lib/systemd/timers/stamp-apt-daily-upgrade.timer"


def _parse_show(text: str) -> dict:
    """`systemctl show` output as {property: value}."""
    return dict(ln.split("=", 1) for ln in str(text or "").splitlines() if "=" in ln)


def _systemctl_show(unit: str) -> dict:
    """`systemctl show` for one unit, as {property: value}; {} where there is no systemctl."""
    try:
        r = subprocess.run(["systemctl", "show", unit, "-p", "ActiveState", "-p", "LastTriggerUSec"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return _parse_show(r.stdout)


def _last_upgrade_run(got: dict) -> float | None:
    """When the upgrade timer last fired, as epoch seconds, or None: its stamp file first, then LastTriggerUSec as
    systemd prints it ("Sat 2026-10-03 06:29:24 UTC"; "n/a" or empty when it never has)."""
    try:
        return os.path.getmtime(UPGRADES_STAMP)
    except OSError:
        pass
    raw = str(got.get("LastTriggerUSec") or "").strip()
    if not raw or raw.lower() in ("n/a", "0"):
        return None
    head, _, zone = raw.rpartition(" ")
    try:
        when = datetime.strptime(head, "%a %Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    # A server box runs on UTC; anything else is the box's own local time, which a naive datetime already is.
    return (when.replace(tzinfo=timezone.utc) if zone.upper() in ("UTC", "GMT") else when).timestamp()


def _box_age_s() -> float | None:
    """Seconds since this box was provisioned (the provisioner's file, core/claim.py), or None on a box without it."""
    from core import claim
    try:
        return _now().timestamp() - os.path.getmtime(claim.PROVISION_JSON)
    except OSError:
        return None


def check_upgrades(show=None, age=None, last=None) -> bool:
    """The box's security updates still run: Ubuntu's upgrade timer is active and fired in the last 48 hours.

    A BOX YOUNGER THAN 48 HOURS PASSES WHILE THE TIMER IS ACTIVE (OSDev1's review of #1867): the timer fires once a
    day around 06:00, so the rehearsal box a release waits on, minutes old, has never fired it, and calling that a
    break of ours would stop every release. Past 48 hours, it must have run."""
    got = (show or _systemctl_show)(UPGRADES_TIMER)
    ran = (last or _last_upgrade_run)(got)
    fresh = ran is not None and (_now().timestamp() - ran) < UPGRADES_MAX_AGE_S
    born = (age or _box_age_s)()
    young = born is not None and born < UPGRADES_MAX_AGE_S
    return got.get("ActiveState") == "active" and (fresh or young)


def _cert_days_left(url: str | None = None) -> float | None:
    """Days until the box's own certificate expires, read over the same loopback TLS the MCP check uses, or None
    when the box has no https address to read."""
    u = urllib.parse.urlsplit(url or base_url())
    if u.scheme != "https" or not u.hostname:
        return None
    sock = socket.create_connection(("127.0.0.1", u.port or 443), 10)
    with ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname) as tls:
        not_after = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    return (not_after - _now().timestamp()) / 86400


def check_certificate(days_left=None) -> bool:
    """The box's certificate has more than 14 days left (Caddy renews at 30, so less is a renewal that stopped)."""
    left = (days_left or _cert_days_left)()
    return left is None or left > CERT_MIN_DAYS


def check_review() -> bool:
    from core import report, review_brief
    yesterday = report.today() - timedelta(days=1)
    brief = review_brief.build(yesterday, remember=False)
    return isinstance(brief, dict) and bool(brief)


# ── 4. an approval round trip ───────────────────────────────────────────────────────────────────────────────
def _run_check(detail: dict) -> dict:
    _RAN.append(detail.get("nonce"))
    return {"ok": True, "text": "The box's self-test ran."}


def check_approval() -> bool:
    from core import approvals, state
    approvals.register_kind(KIND, run=_run_check)
    nonce = f"{time.time():.6f}"
    before = len(_RAN)
    row = approvals.propose(KIND, machine="core", title="Key features check", detail={"nonce": nonce},
                            quiet=True)
    try:
        first = approvals.decide(row["id"], True, by=SEAT_LABEL)
        again = approvals.decide(row["id"], True, by=SEAT_LABEL)
        return (first.get("ok") is True and first.get("status") == "done" and again.get("ok") is False
                and len(_RAN) - before == 1)
    finally:
        # THE OWNER NEVER SEES THE BOX TESTING ITSELF: the row goes, whatever happened.
        with state.connect() as c:
            c.execute("DELETE FROM approvals WHERE id = ? AND kind = ?", (row["id"], KIND))


# ── all four ────────────────────────────────────────────────────────────────────────────────────────────────
def run(*, post=None, url: str | None = None) -> dict:
    """Every check. -> {"at", "ok", "passed", "total", "failed": [names]}. Never raises."""
    passed, failed = 0, []
    _AI_LAST.clear()
    try:
        clean_up()
    except Exception as e:                               # noqa: BLE001 — a leftover never stops the checks
        log.warning("key_features.clean_up_failed", error=f"{type(e).__name__}: {e}"[:200])
    try:
        p, f = check_mcp(post=post, url=url)
        passed, failed = passed + p, failed + f
    except Exception as e:                               # noqa: BLE001
        failed.append("mcp")
        log.warning("key_features.mcp_crashed", error=f"{type(e).__name__}: {e}"[:200])
    for name, fn in (("ai", check_ai), ("review", check_review), ("approval", check_approval),
                     ("upgrades", check_upgrades), ("certificate", check_certificate)):
        try:
            good = bool(fn())
        except Exception as e:                           # noqa: BLE001 — a check that crashed failed
            log.warning("key_features.check_crashed", check=name, error=f"{type(e).__name__}: {e}"[:200])
            good = False
        if good:
            passed += 1
        else:
            failed.append(name)
    out = {"at": _iso(_now()), "ok": not failed, "passed": passed, "total": passed + len(failed),
           "failed": failed[:FAILED_MAX]}
    if out["failed"]:
        out["why"] = {n: (ai_why() if n == "ai" else OURS) for n in out["failed"]}
    log.info("key_features.ran", ok=out["ok"], passed=passed, failed=failed[:FAILED_MAX], why=out.get("why"))
    return out


def save(result: dict) -> None:
    try:
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps(result))
    except OSError as e:
        log.warning("key_features.save_failed", error=str(e)[:160])


def last() -> dict | None:
    """The last result, in the shape the check-in carries, or None. Never raises."""
    try:
        got = json.loads(LAST.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict):
        return None
    out = {"at": str(got.get("at") or "")[:32], "ok": got.get("ok") is True,
           "passed": int(got.get("passed") or 0), "total": int(got.get("total") or 0),
           "failed": [str(x)[:80] for x in (got.get("failed") or [])][:FAILED_MAX]}
    why = got.get("why") if isinstance(got.get("why"), dict) else {}
    reasons = {n: why[n] for n in out["failed"] if why.get(n) in REASONS}
    if reasons:
        out["why"] = reasons
    return out


def _wait_for_web(limit_s: float = WEB_WAIT_S, sleep=time.sleep) -> bool:
    """Wait for gunicorn's own bind to answer /health, at most `limit_s`. The timer fires 4 minutes after boot so a
    fresh box reports within minutes (#1857 H1), and on a box from the image the web process can still be starting.
    -> whether it answered. A box whose web never comes up runs the checks anyway and fails `mcp:connect`, ours."""
    u = urllib.parse.urlsplit(LOCAL)
    deadline = time.monotonic() + limit_s
    while True:
        try:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
            try:
                conn.request("GET", "/health")
                if conn.getresponse().status == 200:
                    return True
            finally:
                conn.close()
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        sleep(5)


def main() -> int:
    import core.dispatch  # noqa: F401 — loads every machine, so the tools and grants are the box's real ones
    if not _wait_for_web():
        log.warning("key_features.web_not_up", waited_s=WEB_WAIT_S)
    result = run()
    save(result)
    from core import checkin
    checkin.nudge()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
