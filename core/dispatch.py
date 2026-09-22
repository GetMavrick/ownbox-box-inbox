"""/dispatch — the single inbound endpoint (spec §3, §2.1).

POST /dispatch       enqueue work (bearer auth + strict idempotency)
GET  /dispatch/<id>  poll status/result
GET  /health         liveness (for the local watchdog)

There are no webhook receivers — HeyGen/Instantly are polled, and approvals come
back in through POST /dispatch. Run behind a TLS reverse proxy (gunicorn/waitress,
not the Flask dev server).
"""
import hmac
import subprocess
import pathlib
import time
import os
import json
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request

from core import state, version
from core.config import settings
from core.logging import get_logger
from core.queue import queue

log = get_logger(__name__)
app = Flask(__name__)
# Bound untrusted input at the ingress. The bearer token is the only auth; if it
# leaks, an oversized body must not be storable. A reel script is ~2 KB; 256 KB is
# generous. Flask returns 413 automatically when exceeded.
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

# Behind Caddy (TLS terminator on the same box): trust exactly ONE hop of
# X-Forwarded-* so request.is_secure is true over HTTPS — that's what flips the
# session cookie's Secure flag on. Gunicorn binds loopback, so the single trusted
# hop can only be local Caddy.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Ensure the schema is current the moment gunicorn imports this module — every
# entrypoint self-migrates so a deploy is pull+restart, never a forgotten step.
# init_db() is idempotent and BEGIN IMMEDIATE serializes the 2 gunicorn workers.
state.init_db()


def _load_web_modules() -> None:
    """Register department web blueprints named in config `web_modules:` — the same
    data-not-code seam as worker.load_modules(): the kernel never imports a business
    module directly. Each listed module exposes a Flask `blueprint`."""
    import importlib

    from core.config import get_config
    for path in get_config().get("web_modules", []):
        mod = importlib.import_module(path)
        app.register_blueprint(mod.blueprint)
        log.info("dispatch.web_module_loaded", module=path)
    _load_packs()


def _load_packs() -> None:
    """Import every discovered pack HERE TOO, so a pack that offers the web something can
    actually reach it.

    WHY THIS IS NOT A DUPLICATE OF worker.load_modules(). A pack hands the host things by
    calling `plug` at import (`provides_app_view`, `provides_register`), and what those calls
    set are MODULE-LEVEL GLOBALS. We run two units: aios-worker (`python -m core.worker`) and
    aios-dispatch (gunicorn, several workers), and a global set in one process does not exist
    in the other. The worker imported the packs, so the periodic ran and landed rows; this
    process served the pages and had never heard of them, so `machine_app._VIEW_SOURCE` stayed
    None and every page rendered the empty state no matter what the machine found. Live on the
    box 2026-09-06: 52 companies in the table, "No company has come through yet" on the page.

    The seam is unchanged: the kernel still imports no business module by name, it imports what
    `packs.discover()` found, exactly as the worker does. Import is the whole job here, so
    there is no blueprint to register and nothing to start; `register_periodic` is idempotent
    by name and only appends to a list, and this process never runs the periodic loop.

    A BROKEN PACK MUST NOT TAKE THE BOX DOWN. The worker can afford to raise on a bad import
    because it is a background unit; this one answers /dispatch, and the whole box is one
    endpoint. So an import failure is logged and skipped: the machine that failed loses its
    screens, and everything else — /dispatch included — keeps answering.
    """
    import importlib

    from core import packs
    for m in packs.discover():
        mod = m.get("module")
        if not mod:
            continue                                   # a config recipe: no module to import
        try:
            importlib.import_module(mod)
        except Exception as e:                         # noqa: BLE001 — see the docstring
            log.warning("dispatch.pack_import_failed", slug=m.get("slug"), module=mod,
                        error=str(e))
            # AND TELL THE CONNECTOR, so absence is a startup FACT rather than a 404 later.
            # Swallowing the failure is right for this process — the box must keep answering —
            # but a tool registry built on a swallowed import would advertise a tool that cannot
            # run. The pack's tools simply never registered; this records WHY, so "invisible by
            # design" stops being indistinguishable from "silently broken".
            try:
                from core.connector import tools as _ctools
                _ctools.note_absent(mod, f"{type(e).__name__}: {e}")
            except Exception:                          # noqa: BLE001
                pass                                   # never let bookkeeping break the loader
            continue
        log.info("dispatch.pack_loaded", slug=m["slug"], module=mod, host=m["host"])


# The dash shell is CORE, not a department, so the kernel mounts it directly rather
# than through `web_modules:` — no config edit on any box, existing or shipped, and
# one login for every machine on the box instead of two hosts claiming /dash/login.
from core import dash as _dash  # noqa: E402

app.register_blueprint(_dash.blueprint)

# THE CONNECTOR IS CORE TOO, and mounted the same way and for the same reason: no shipped box
# needs a config edit for the door to exist. Its routes live under /api/, which `_auth_gate`
# above already refuses without a valid seat, so mounting it opens nothing — a box with no seat
# minted answers 401 to every one of these, which is the state every box ships in.
#
# Registered AFTER the gate is defined, deliberately: a blueprint mounted before its guard is a
# window, and the ordering here is the only thing that makes that impossible to get wrong.
from core.connector import http as _connector_http  # noqa: E402
from core.connector import mcp as _connector_mcp  # noqa: E402

app.register_blueprint(_connector_http.blueprint)
# THE SECOND TRANSPORT, same gate, same registry. Both live under /api/, so the one gate above
# shuts both; a coworker product that only speaks MCP reaches the same functions as curl does.
app.register_blueprint(_connector_mcp.blueprint)

# TIER 1's tools, imported HERE because this is the composition root — the same place the dash
# shell and the connector itself are mounted. The registry needs no edit to gain them; importing
# the module that registers them is the whole mechanism, which is the point of a registry.
#
# And it must be imported in THIS process specifically. core/report.py records the failure: the
# worker and the web process load different module lists, so a tool registered only where the
# worker can see it would be advertised by one and absent from the other.
# WRAPPED, because core-level registration was NOT protected the way pack-level registration is
# (OSDev1's review of #1101). `_load_packs` above catches a pack's import failure and records it,
# so a broken pack cannot take the box down. This import sat outside that protection: a duplicate
# tool name raises ValueError from `tools.register`, and at core level that would kill the WHOLE
# web process at boot — the box stops answering /dispatch because a reporting tool collided.
#
# The registry's refusal is still right; it must not be silent. So it fails the same way a pack
# does: the tools are absent, the reason is recorded, and the box keeps answering.
try:
    from core import report_tools as _report_tools  # noqa: E402,F401
except Exception as _e:                            # noqa: BLE001
    log.error("dispatch.core_tools_import_failed", module="core.report_tools", error=str(_e))
    try:
        from core.connector import tools as _ctools2
        _ctools2.note_absent("core.report_tools", f"{type(_e).__name__}: {_e}")
    except Exception:                              # noqa: BLE001
        pass

_load_web_modules()


def _authorized(req) -> bool:
    token = settings.dispatch_bearer_token
    if not token:
        return False
    provided = req.headers.get("Authorization", "")
    # constant-time compare on a security-sensitive resold box
    return hmac.compare_digest(provided, f"Bearer {token}")


def _deploy_authorized(req) -> bool:
    """/deploy has its OWN token when one is set, and falls back to the bearer when it is not.

    WHY A SECOND TOKEN. `DISPATCH_BEARER_TOKEN` is not just the API key: `core/config.py` uses
    it as the fallback for `DASH_TOKEN` (the dashboard login) and `core/compliance.py` derives
    the unsubscribe signing key from it. So it unlocks the dashboard and signs opt-out links,
    and it has to be TYPED INTO A REMOTE SESSION for a phone to deploy. Handing that one string
    to anything is handing over all three.

    `DEPLOY_TOKEN` is narrow on purpose: everything it can do is ship `origin/main`, which is
    already the only thing /deploy will do for anybody. Leak it and the worst case is somebody
    redeploys the code that is already public on GitHub. Rotate it without touching the
    dashboard or invalidating a single unsubscribe link.

    Set it and it becomes the ONLY key to this door — the wide token stops working here, which
    is the whole point. Leave it unset and nothing changes for an existing box.
    """
    narrow = getattr(settings, "deploy_token", "") or ""
    if not narrow:
        return _authorized(req)
    provided = req.headers.get("Authorization", "")
    return hmac.compare_digest(provided, f"Bearer {narrow}")


# THE CONNECTOR'S DOOR, AND IT SHIPS SHUT (docs/PLAN_AIOS_CONNECTOR.md §6 step 1).
#
# `_auth_gate` below is an ALLOWLIST of prefixes, so anything it does not name falls through
# with no credential checked. That is correct for /health and load-bearing for /gtm/unsubscribe
# — a CAN-SPAM one-click opt-out link already printed in mail we have already sent, which MUST
# answer an unauthenticated stranger because that stranger is exercising a legal right. It is
# catastrophic for the connector: measured on main before this gate existed, a route under
# /api/v1/ answered `200` with its full body to a request carrying no Authorization header.
#
# So the fix is a NARROW gate on a new prefix, never a flip of the app's default.
#
# THE PREFIX IS /api/, NOT /api/v1/. The plan says v1; this is wider on purpose and the
# difference is the whole point of the step. Gating only /api/v1 leaves /api/v2 world-readable
# on the day someone writes it — the same bug, one version bump later, and nothing would catch
# it because the test would still be green. Measured before widening: the app carries 53 routes
# and ZERO of them are under /api/, so this costs nothing today and closes the repeat.
_API_PREFIX = "/api/"

# THE SHORT ADDRESS A BUYER IS GIVEN, and it is listed HERE because this is the only place that
# decides what needs a seat. `/api/v1/mcp` is the real endpoint; `https://<slug>.ownbox.app/mcp`
# is the one that fits on a screen and gets pasted into an assistant's settings.
#
# MATCHED EXACTLY, NEVER BY PREFIX. `startswith("/mcp")` would also match `/mcpanything`, and a
# route mounted there later would be served with no seat at all — the note above `_API_PREFIX`
# says this gate is "latent exactly until somebody mounts a connector route outside that prefix",
# and this IS that somebody. An unauthenticated MCP endpoint is every customer message on the box
# handed to anyone who finds the hostname.
_SEAT_PATHS = ("/mcp",)


def _needs_a_seat(path: str) -> bool:
    return path.startswith(_API_PREFIX) or path in _SEAT_PATHS


def _seat_authorized(req) -> bool:
    """Does this request carry a valid connector seat? No seat minted, no entry — ever.

    STEP 2 FILLED THIS IN. The credential is `<seat_id>.<secret>` in the Authorization header:
    one lookup by primary key, then one constant-time compare against a stored SHA-256. The
    secret itself is never stored, so a stolen database yields no working credential.

    A box with no seats minted refuses everything, which is the shipped state of every box until
    somebody deliberately mints one. That is the honest default: the door exists and is shut.

    DELIBERATELY NOT `_authorized`. The wide `DISPATCH_BEARER_TOKEN` must never open this door.
    It is not an API key: `core/config.py` falls back to it for `DASH_TOKEN` and
    `core/compliance.py` derives the unsubscribe SIGNING key from it, so it unlocks the
    dashboard and signs every opt-out link on the box. `_deploy_authorized` above already had to
    introduce a second, narrow token for exactly this reason — "handing that one string to
    anything is handing over all three" — and the connector must not re-make the mistake /deploy
    was written to undo. There is also no per-seat revocation for a secret that is simultaneously
    the dashboard password: revoking one seat would mean rotating the dashboard and invalidating
    every unsubscribe link in flight.

    IMPORTED INSIDE THE FUNCTION, not at module scope. core/dispatch.py is imported by tooling
    that has no database (the exporter's import scan among them), and a module-level import of
    anything that touches `state` turns a missing DB into an import-time crash of the whole app.
    """
    header = (req.headers.get("Authorization") or "")
    if not header.startswith("Bearer "):
        return False
    from core.connector import seats as _seats
    seat = _seats.verify(header[len("Bearer "):].strip())
    if seat is None:
        return False
    # Stash it for the route: re-verifying downstream would be a second DB read per request, and
    # a route that re-derives identity from the header is a route that can get it wrong.
    g.seat = seat
    _seats.touch(seat["id"])
    return True


# ── A FORM ON ANOTHER SITE MUST NOT ACT HERE WITH THIS BOX'S COOKIES ────────────────────────────
# Every sold box is a sibling under one domain (acme.ownbox.app, globex.ownbox.app), and that domain
# is not on the Public Suffix List, so to a browser the siblings are the SAME SITE. SameSite=Lax does
# not stop a page on another box from submitting a form here: the browser attaches this box's session
# cookie and the route sees a signed-in person. The first route that sends to a real person (a reply
# to a customer, #1145) made that concrete; no route checked where a request came from.
#
# The browser already says where a request came from (Sec-Fetch-Site on every current browser,
# Origin on every POST), so this is ONE check for the whole app, ahead of every blueprint: an unsafe
# method that carries cookies must come from this origin. It is keyed on COOKIES because borrowed
# credentials are the whole attack. A request with no cookie has nothing to borrow, so a server, curl,
# a vendor webhook or a bearer-token client passes untouched to its own route's auth.
#
# request.host is the PUBLIC name even behind Caddy: ProxyFix above trusts one hop of
# X-Forwarded-Host. A GET that changes state is not covered here, and is a bug in that route.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _cross_site(req) -> str | None:
    """Why this request must be refused, or None to let it through."""
    if req.method not in _UNSAFE_METHODS or not req.cookies:
        return None
    site = (req.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site:
        # same-origin: this box's own page. none: the person typed it or used a bookmark.
        return None if site in ("same-origin", "none") else f"sec-fetch-site={site}"
    origin = (req.headers.get("Origin") or "").strip()
    if not origin:
        return None                     # no browser signal at all: not a browser, nothing borrowed
    netloc = "" if origin == "null" else urlsplit(origin).netloc.lower()
    return None if netloc and netloc == (req.host or "").lower() else "origin-mismatch"


@app.before_request
def _same_origin_gate():
    why = _cross_site(request)
    if why:
        log.warning("dispatch.cross_site_refused", path=request.path, method=request.method, why=why)
        return ("Refused: this request came from another site. Open the page on this box and try again.",
                403, {"Content-Type": "text/plain; charset=utf-8"})
    return None


@app.before_request
def _auth_gate():
    if request.path.startswith("/deploy"):
        if not _deploy_authorized(request):
            return jsonify({"error": "unauthorized"}), 401
    elif _needs_a_seat(request.path):
        # Module-level lookup, not a captured reference: step 2 (and the test) replace this
        # function, and a closure over the original would keep refusing after it was filled in.
        if not globals()["_seat_authorized"](request):
            return jsonify({"error": "unauthorized"}), 401
    elif request.path.startswith("/dispatch") and not _authorized(request):
        return jsonify({"error": "unauthorized"}), 401


@app.post("/dispatch")
def dispatch():
    body = request.get_json(force=True, silent=True) or {}
    raw_text = body.get("raw_text")
    if not raw_text:
        return jsonify({"error": "raw_text is required"}), 400

    # Strict idempotency: callers MUST supply a key. We do NOT fall back to a
    # time-bucket hash — a retry crossing that boundary would double-spend, the
    # exact failure the spec forbids. Mavrick / batch_driver generate a stable key
    # (a UUID, or a deterministic hash of the request content).
    idem = body.get("idempotency_key")
    if not idem:
        return jsonify({"error": "idempotency_key is required"}), 400

    job, created = queue.enqueue(
        idempotency_key=idem,
        agent_name=body.get("agent_name"),
        intent=body.get("intent"),
        raw_text=raw_text,
        slack_channel_id=body.get("slack_channel_id"),
        slack_thread_ts=body.get("slack_thread_ts"),
    )
    log.info("dispatch.received", job_id=job["id"], created=created,
             agent=body.get("agent_name"), intent=job.get("intent"))
    return jsonify({"job_id": job["id"], "status": job["status"],
                    "duplicate": not created}), 202


@app.get("/dispatch/<job_id>")
def dispatch_status(job_id):
    job = state.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "result": json.loads(job["result"]) if job.get("result") else None,
        "error": job.get("error"),
    })


@app.post("/deploy")
def deploy():
    """Update this box to the newest VERIFIED release. THE PHONE'S DEPLOY BUTTON.

    WHY THIS EXISTS. The owner runs his business from wherever he is, and until now a deploy
    needed the Mac in his office: the deploy script ssh'd in from there, so the key lived there,
    so "ship it" meant "be at that desk". A session on his phone could review and merge and then
    had to stop and wait for a machine. This is the missing half — the box updates ITSELF, from
    the same GitHub it already pulls from, with no key and no laptop on the caller's side.

    WHAT A CALLER CAN CHOOSE: nothing. There is no ref, no branch, no path, no shell string in
    the request — the body is ignored entirely. It installs only the newest release tag that verifies
    against this box's own trust file (core/release/update.py, run by scripts/box_update.sh). An
    endpoint that deploys a caller-named branch is an endpoint that runs a stranger's code as
    root, and the bearer token is one leak away from being a stranger's.

    WHY IT RETURNS BEFORE IT FINISHES. The deploy restarts `aios-dispatch`, which is the very
    process answering this request, so anything run in-process would kill its own reply
    mid-flight and report a failure that did not happen. `systemd-run` hands the work to a
    transient unit owned by systemd, which survives the restart. So this returns 202 ACCEPTED,
    never 200 OK: it means "started", not "done".

    HOW THE CALLER KNOWS IT WORKED, without a shell: poll `GET /health` — unauthenticated, and
    it already reports the commit actually running — until `commit` is the sha you expected.
    That is a measurement of the live box, not a report of somebody's report.
    """
    if not _deploy_authorized(request):   # the gate above already did this — belt and braces
        return jsonify({"error": "unauthorized"}), 401
    unit = f"aios-selfdeploy-{int(time.time())}"
    script = str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "box_update.sh")
    if not os.path.exists(script):
        return jsonify({"error": "box_update.sh is not on this box"}), 500
    try:
        # No shell: a fixed argv, and nothing from the request reaches it.
        subprocess.Popen(["systemd-run", "--unit", unit, "--collect",
                          "--working-directory", "/opt/aios", "/bin/bash", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as e:
        log.warning("deploy.spawn_failed", error=f"{type(e).__name__}: {e}"[:200])
        return jsonify({"error": "could not start the deploy", "detail": type(e).__name__}), 500
    log.info("deploy.started", unit=unit)
    return jsonify({"status": "started", "unit": unit, "target": "newest verified release",
                    "verify": "poll GET /health until commit is the sha you expect"}), 202


@app.get("/health")
def health():
    """Liveness, plus WHICH COMMIT IS ACTUALLY RUNNING.

    The SHA is here because "how far behind is the box?" has been answered all week by reading
    the last DEVSTATE post, which is a report of somebody's last report and is wrong the moment
    somebody deploys without posting. One unauthenticated GET now answers it as a measurement.

    `restart_needed` is the case nobody could see: `git pull` moved the files and nothing
    restarted, so the deploy looks done and the running worker is still on the old code.

    Additive only — every consumer of this endpoint (`launch_check`, `install_services`,
    `expose.sh`, `gtm_smoke`) tests for a 200 and reads no fields, so nothing downstream
    changes. And it stays unauthenticated like the rest of the response: a commit SHA of a
    private repo is not a secret, and a liveness probe that needs a token is not a liveness
    probe.
    """
    return jsonify({"ok": True, **version.status()})
