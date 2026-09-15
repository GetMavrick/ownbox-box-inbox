"""Slack Socket-Mode daemon — the clone bot's ears.

The box DIALS OUT to Slack over a websocket (Socket Mode): no inbound webhook,
no public events URL, no signature-verification surface — the single-ingress
invariant survives intact, and this works on a future clone before it even has
DNS. The daemon turns @mentions in ALLOWLISTED channels into queue jobs; the
worker's existing notify plumbing posts the threaded replies with the bot token.

Integration rules enforced here (docs/AIOS_SLACK_INTEGRATION.md, Mavrick repo):
  rule 1  channels NOT in SLACK_CHANNEL_INTENTS are ignored by construction
  rule 3  bot-authored events are dropped (no loops); we only wake on app_mention
  rule 5  Slack retries collapse onto ONE job — idempotency_key = the event_id
          (enqueue happens BEFORE the ack, so at-least-once delivery + idempotent
          enqueue = exactly-once work)

Channel→intent map (deployment config, env):
    SLACK_CHANNEL_INTENTS=C0AAA1111:reel,C0BBB2222:brain
"""
import re
import threading
import time

from core import state
from core.config import settings
from core.logging import get_logger
from core.queue import queue

log = get_logger(__name__)

_HEARTBEAT_INTERVAL = 60.0
# Strip ONLY the leading @bot mention(s). A mention later in the text is the
# owner's content (e.g. a script that names someone) and must survive verbatim —
# it becomes captions.
_MENTION_RE = re.compile(r"^(?:\s*<@[A-Z0-9]+>[\s:,]*)+")


def channel_map() -> dict:
    """Parse SLACK_CHANNEL_INTENTS → {channel_id: intent}. Empty/garbage-tolerant."""
    out = {}
    for part in (settings.slack_channel_intents or "").split(","):
        if ":" in part:
            cid, intent = part.split(":", 1)
            if cid.strip() and intent.strip():
                out[cid.strip()] = intent.strip()
    return out


# First-word → department, so the owner can run the whole system from one DM.
_REEL_DM_KW = {"watch", "unwatch", "creators", "scrape"}
_GTM_DM_KW = {"discover", "lead", "leads", "list", "draft", "approve", "reject", "suppress",
              "check", "sync", "sending", "send", "from", "status",
              # These GTM commands exist in slack_parser + handler but were unreachable via DM
              # (first word routed to brain), only tappable as buttons: `sales` / `sales done
              # <id>` (Sales inbox), `cancel <id>` (stop a scheduled follow-up), bare `cap <N>`.
              "sales", "cancel", "cap",
              # Same bug class, caught in the pre-GTM audit. These are fully implemented in
              # slack_parser + handler but fell through to `brain` in a DM, which is WORSE than a
              # dead command: `target Berlin, Germany` got a fluent LLM confirmation while no
              # state changed at all. `doctor` is the documented go-live gate.
              "doctor", "target", "industry", "strictness", "rescore"}

# A pasted social/video link that ISN'T a keyword command is an agent task: Mavrick
# fetches the transcript and replies. Kept to the hosts the agent can actually act on.
_AGENT_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:instagram\.com|x\.com|twitter\.com)/", re.IGNORECASE)


def _route_to_agent(text: str) -> bool:
    if not _AGENT_URL_RE.search(text or ""):
        return False
    first = (text.split() or [""])[0].lower().lstrip("/")
    # A link present → the agent (transcribe), UNLESS the message is an explicit reel
    # command (watch/unwatch/scrape/creators). GTM keywords no longer veto a link, so
    # natural phrasing — "check this <link>", "from this <link> write a script" — reaches
    # the agent instead of misrouting to GTM.
    return first not in _REEL_DM_KW


# Lead-magnet control verbs. The machine is pinned to a Space whose Slack channel is the SAME
# #reels channel the reel surface uses, so its verbs need an explicit lane or the channel's 'reel'
# intent eats them (an 'activate scale' comes back as reel help). Only the UNAMBIGUOUS verbs are
# hijacked bare — no other surface (reel/gtm) claims activate/pause; the full verb set
# (publish/status/pause/welcome) stays reachable with an explicit `leadmagnet …` prefix so it can
# never shadow a reel or gtm word.
_LEADMAGNET_KW = {"activate", "pause"}


def _route_to_leadmagnet(text: str) -> str | None:
    """Return the normalized `leadmagnet <verb> <args>` raw_text when `text` is a lead-magnet
    command, else None. Recognizes an explicit `leadmagnet …` prefix or a bare activate/pause.
    Deterministic — the handler owns enablement, owner-gating, and Space-pinning."""
    t = (text or "").strip()
    first = (t.split() or [""])[0].lower().lstrip("/")
    if first == "leadmagnet":
        return t                                   # already prefixed → handler parses as-is
    if first in _LEADMAGNET_KW:
        return f"leadmagnet {t}"
    return None


def _dm_intent(text: str) -> str:
    """Route a direct message by its first word: reel/gtm commands go to their
    department, everything else is general chat (brain). Overridable later with
    SLACK_DM_INTENT if a single fixed lane is ever wanted."""
    forced = (getattr(settings, "slack_dm_intent", "") or "").strip()
    if forced:
        return forced
    first = (text.split() or [""])[0].lower().lstrip("/")
    if first in _REEL_DM_KW:
        return "reel"
    if first in _GTM_DM_KW:
        return "gtm"
    return "brain"


# A3: the zero-LLM `health` pulse. A distinct word ('status' is already a gtm command),
# answered IN THIS PROCESS rather than enqueued, so it works when the worker or the
# reasoning backend is down — exactly when the operator most needs a pulse.
_HEALTH_RE = re.compile(r"^\s*(?:health|sys)\b", re.IGNORECASE)


def _is_health(text: str) -> bool:
    return bool(_HEALTH_RE.match(text or ""))


def _answer_health(event: dict) -> None:
    """Post the pure-DB health report into the asking thread. Best-effort + zero-Claude;
    a read/post hiccup only logs (the pulse must never itself raise into the listener)."""
    from core import health, slack
    channel = event.get("channel")
    if not channel:
        return
    thread = event.get("thread_ts") or event.get("ts")
    try:
        slack.thread_reply(channel, thread, health.report())
    except Exception as e:  # noqa: BLE001
        log.warning("slack_socket.health_error", error=str(e)[:200])


def _backend_config_problems() -> list[str]:
    """A4 — static (no-LLM) check that the CONFIGURED reasoning backend's credentials are
    present at boot. [] = all good. The live counterpart is the worker's A2 probe."""
    import shutil

    from core.config import get_config
    backend = (get_config().get("brain") or {}).get("backend", "api")
    problems = []
    if backend == "claude_code":
        if not shutil.which("claude"):
            problems.append("`claude` CLI not installed (scripts/install_claude_code.sh)")
        if not settings.claude_code_oauth_token:
            problems.append("CLAUDE_CODE_OAUTH_TOKEN not set")
    elif not settings.anthropic_api_key:
        problems.append("ANTHROPIC_API_KEY not set (brain.backend=api)")
    return problems


# Block Kit button action_id → (intent, command template). The button's `value`
# carries the entity id, so a click becomes the exact text command the handler
# already knows — one code path for typed commands and taps alike.
_BUTTON_CMDS = {
    "gtm_approve": ("gtm", "approve {v}"),
    "gtm_reject":  ("gtm", "reject {v}"),
    "gtm_cancel":  ("gtm", "cancel {v}"),      # stop a scheduled follow-up (touch 2)
    "gtm_sales_done": ("gtm", "sales done {v}"),  # mark a Sales hand-off worked
    "reel_queue":  ("reel", "produce script: {v}"),
    "reel_skip":   ("reel", "skip {v}"),
    "reel_post":   ("autopost", "post {v}"),   # tap → the exactly-once auto_poster path
    "reel_add_airtable": ("reel", "add to airtable {v}"),
    # RE-PUBLISH the networks a post missed. The value is the SCRIPT ID only —
    # never a base, channel or platform list — so the handler resolves the Space
    # from the row and this works unchanged for every current and future table.
    "reel_republish": ("autopost", "republish failed {v}"),
}
_RETIRE_VERB = {"gtm_approve": "Approving", "gtm_reject": "Rejecting",
                "gtm_cancel": "Cancelling follow-up",
                "gtm_sales_done": "Marking worked",
                "reel_queue": "Queuing production", "reel_skip": "Skipping",
                "reel_post": "Posting", "reel_add_airtable": "Adding to Airtable",
                "reel_republish": "Re-publishing"}
# Buttons that SPEND money or POST publicly — only the operator may tap them, even in a
# shared channel. The actual irreversible action lives behind these, so this is the gate
# that matters most. (FAIL-CLOSED: with no OPERATOR_SLACK_USER_ID set, every tap is refused # — see slack.is_operator; launch_check asserts the var before go-live.)
# reel_add_airtable is here too: Airtable is OWNER-CONTROLLED (owner mandate 2026-07-08) —
# creating the external record is exactly the decision only the owner gets to make.
_OWNER_ONLY_ACTIONS = {"reel_queue", "reel_post", "gtm_approve", "reel_add_airtable",
                       # posts publicly — the same gate as the first publish
                       "reel_republish"}
# Link buttons (a `url` opens client-side) still POST an interaction — ack it and
# do nothing, so there's no work to enqueue and no "unknown action" noise.
_LINK_ACTIONS = {"reel_edit"}


def handle_request(client, req) -> None:
    """One Socket-Mode envelope. MUST never raise — a listener exception would
    kill the read loop and silently deafen the bot."""
    try:
        _handle(client, req)
    except Exception as e:  # noqa: BLE001
        log.error("slack_socket.handler_error", error=str(e)[:300])
        _ack(client, req)   # still ack: a poison envelope must not redeliver forever


def _ack(client, req) -> None:
    try:
        from slack_sdk.socket_mode.response import SocketModeResponse
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    except Exception as e:  # noqa: BLE001
        log.warning("slack_socket.ack_failed", error=str(e)[:200])


def _handle(client, req) -> None:
    if req.type == "interactive":
        _handle_interactive(client, req)
        return
    if req.type != "events_api":
        _ack(client, req)
        return
    payload = req.payload or {}
    event = payload.get("event") or {}

    # Drop our own + any bot's events (no loops, rule 3) — bot_id covers AIOS's
    # own replies AND prod Mavrick.
    if event.get("bot_id"):
        _ack(client, req)
        return

    etype = event.get("type")
    if etype == "app_mention":
        # Channel @mention → channel→intent allowlist (rule 1).
        intent = channel_map().get(event.get("channel") or "")
        if not intent:
            log.info("slack_socket.unlisted_channel", channel=event.get("channel"))
            _ack(client, req)
            return
        text = _MENTION_RE.sub("", event.get("text") or "").strip()
    elif (etype == "message" and event.get("channel_type") == "im"
          and not event.get("subtype") and event.get("user")):
        # A DIRECT MESSAGE to Mavrick (no @mention needed in a DM). Route by the
        # first word so the owner can run the whole system from one DM thread.
        text = (event.get("text") or "").strip()
        intent = _dm_intent(text)
    else:
        _ack(client, req)
        return
    # A3: a `health`/`sys` pulse is answered HERE (pure DB, this process) and never
    # enqueued — so it still replies when the worker or the reasoning backend is down.
    if _is_health(text):
        _answer_health(event)
        _ack(client, req)
        return
    # A pasted reel/post link (not a keyword command) → the agent: it fetches the
    # transcript and replies. Overrides the channel/DM default so a link works anywhere
    # Mavrick already listens.
    # A reel link in a #reels channel goes to the DETERMINISTIC reel intake (transcribe
    # → save the record verbatim), NOT the model agent — the reel machine never invents.
    # In a DM / other lane, a link still routes to the agent for a transcript reply.
    # A lead-magnet control verb (activate/pause a keyword, or an explicit `leadmagnet …`) →
    # the leadmagnet handler, OVERRIDING the channel default. Without this, the pinned Space's
    # #reels channel routes these to the reel intent and they come back as reel help. Checked
    # before the link→agent reroute; a link never starts with a lead-magnet verb, so they don't
    # conflict.
    lm_raw = _route_to_leadmagnet(text)
    if lm_raw is not None:
        intent, text = "leadmagnet", lm_raw
    elif intent and intent != "reel" and _route_to_agent(text):
        intent = "agent"
    event_id = payload.get("event_id") or event.get("event_ts") or event.get("ts")
    try:
        job, created = queue.enqueue(
            idempotency_key=f"slack:{event_id}",
            intent=intent,
            raw_text=text,
            agent_name="slack-clone-bot",
            slack_channel_id=event.get("channel"),
            slack_user_id=event.get("user"),     # the ACTOR — gates owner-only actions
            # Replies thread under the owner's message (or stay in its thread).
            slack_thread_ts=event.get("thread_ts") or event.get("ts"),
        )
    except Exception as e:  # noqa: BLE001
        # Do NOT ack: Slack redelivers (up to 3x), the idempotency key dedups —
        # a transient DB hiccup costs a retry, never a lost message.
        log.error("slack_socket.enqueue_failed_will_redeliver", error=str(e)[:200])
        return
    log.info("slack_socket.enqueued", job_id=job["id"], intent=intent,
             duplicate=not created)
    _ack(client, req)   # ack AFTER enqueue: at-least-once + idempotent = exactly-once


def _handle_interactive(client, req) -> None:
    """A Block Kit button click (block_actions). Translate it to the equivalent
    text command and enqueue it — the SAME idempotent path as a typed command —
    then retire the buttons so a second tap can't double-fire. ACK fast: Slack's
    interactive window is ~3s, so enqueue (one fast insert) then ack, and do the
    cosmetic button-retire last."""
    payload = req.payload or {}
    if payload.get("type") != "block_actions":
        _ack(client, req)
        return
    actions = payload.get("actions") or []
    action = actions[0] if actions else {}
    action_id = action.get("action_id") or ""
    value = (action.get("value") or "").strip()
    if action_id in _LINK_ACTIONS:
        # The url already opened in the user's browser; nothing to enqueue.
        _ack(client, req)
        return
    spec = _BUTTON_CMDS.get(action_id)
    if not spec or not value:
        log.info("slack_socket.unknown_action", action_id=action_id)
        _ack(client, req)
        return
    tapper = (payload.get("user") or {}).get("id")
    from core import slack
    if action_id in _OWNER_ONLY_ACTIONS and not slack.is_operator(tapper):
        # Spend/post is owner-only — a non-owner tap is refused and the button stays for the
        # owner. The actual money/public action is behind THIS tap, so this is the real gate.
        log.info("slack_socket.non_owner_action_blocked", action_id=action_id, user=tapper)
        _ack(client, req)
        _refuse_owner_only(payload)
        return
    intent, tmpl = spec
    channel = (payload.get("channel") or {}).get("id")
    msg = payload.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")
    try:
        job, created = queue.enqueue(
            # one entity+action+message = one job; a double-tap dedups to a no-op.
            idempotency_key=f"slack:btn:{action_id}:{value}:{msg.get('ts')}",
            intent=intent,
            raw_text=tmpl.format(v=value),
            agent_name="slack-clone-bot",
            slack_channel_id=channel,
            slack_user_id=tapper,             # the ACTOR who tapped
            # Outcome posts in the draft's own thread (rule: never lose the thread).
            slack_thread_ts=thread_ts,
        )
    except Exception as e:  # noqa: BLE001 — do NOT ack; Slack redelivers, idem dedups
        log.error("slack_socket.action_enqueue_failed", error=str(e)[:200])
        return
    log.info("slack_socket.action_enqueued", job_id=job["id"], intent=intent,
             action_id=action_id, duplicate=not created)
    _ack(client, req)
    _retire_buttons(payload, action_id, created)


def _retire_buttons(payload: dict, action_id: str, created: bool) -> None:
    """Replace the button message with its own text + a status line (no blocks →
    buttons vanish), so the action can't be re-fired while it runs. Best-effort."""
    url = payload.get("response_url")
    if not url:
        return
    original = (payload.get("message") or {}).get("text") or "Done."
    if created:
        note = f"\n\n:hourglass_flowing_sand: _{_RETIRE_VERB.get(action_id, 'Working on it')}…_"
    else:
        note = "\n\n:repeat: _Already in progress._"
    try:
        from core import slack
        slack.respond(url, original + note)
    except Exception as e:  # noqa: BLE001
        log.warning("slack_socket.retire_buttons_error", error=str(e)[:200])


def _refuse_owner_only(payload: dict) -> None:
    """Tell a non-owner (best-effort, via the interaction's response_url) that spend/post
    needs the owner. The button is NOT retired, so the owner can still tap it."""
    url = payload.get("response_url")
    if not url:
        return
    try:
        from core import slack
        slack.respond(url, ":lock: Only the owner can approve a paid render or a public post.",
                      replace_original=False)
    except Exception as e:  # noqa: BLE001
        log.warning("slack_socket.refuse_owner_only_error", error=str(e)[:200])


def _liveness_tick(client, streak: dict) -> None:
    """One heartbeat-loop tick. The beat is HONEST: it fires only while the
    websocket is actually connected — a beating daemon with a dead socket would
    be a deaf bot the watchdog can never page. After 3 consecutive disconnected
    ticks (~3 min; the SDK's own auto-reconnect gets first chance), nudge a
    reconnect ourselves — self-healing instead of waiting for a human."""
    try:
        if client.is_connected():
            streak["down"] = 0
            state.heartbeat("slack_socket", "ok")
            return
        streak["down"] += 1
        log.warning("slack_socket.disconnected", consecutive=streak["down"])
        if streak["down"] >= 3:
            log.error("slack_socket.reconnect_nudge")
            client.connect()
            streak["down"] = 0
    except Exception as e:  # noqa: BLE001 — liveness must never kill itself
        log.warning("slack_socket.heartbeat_error", error=str(e)[:200])


def _heartbeat_loop(client) -> None:
    streak = {"down": 0}
    while True:
        _liveness_tick(client, streak)
        time.sleep(_HEARTBEAT_INTERVAL)


def run_forever() -> None:
    if not (settings.slack_app_token and settings.slack_bot_token):
        # Unit installed before tokens exist (or a clone without Slack): stay
        # green-but-inert instead of crash-looping under Restart=always.
        log.warning("slack_socket.unconfigured_idle",
                    have_app_token=bool(settings.slack_app_token),
                    have_bot_token=bool(settings.slack_bot_token))
        while True:
            time.sleep(3600)

    state.init_db()  # self-migrate on startup (every entrypoint does), so a deploy
    #                  is pull+restart with no separate migration step.

    # Config sanity at startup, loudly: a typo'd intent would otherwise fail
    # silently per-message ("no module registered") instead of once, here.
    from core.worker import KNOWN_INTENTS
    unknown = {c: i for c, i in channel_map().items() if i not in KNOWN_INTENTS}
    if unknown:
        log.error("slack_socket.unknown_intents_in_map", map=unknown,
                  known=list(KNOWN_INTENTS))

    # A4: assert the reasoning backend's credentials exist at boot — ONE loud page now
    # instead of every reply failing quietly (the 'bot is a mess' shape). Static presence
    # check (cheap); the worker's A2 startup probe is the live counterpart.
    problems = _backend_config_problems()
    if problems:
        log.error("slack_socket.backend_misconfigured", problems=problems)
        try:
            from core import slack
            if settings.operator_slack_user_id:
                slack.send_dm(settings.operator_slack_user_id,
                              ":warning: Mavrick boot — reasoning backend misconfigured: "
                              + "; ".join(problems))
        except Exception as e:  # noqa: BLE001 — a boot page failing must not stop boot
            log.warning("slack_socket.boot_page_error", error=str(e)[:200])

    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.web import WebClient
    client = SocketModeClient(app_token=settings.slack_app_token,
                              web_client=WebClient(token=settings.slack_bot_token))
    client.socket_mode_request_listeners.append(handle_request)
    client.connect()    # raises on bad credentials → systemd restart, NO beats
    log.info("slack_socket.connected", channels=list(channel_map()))
    # Beats start only after a successful connect — a crash-looping daemon must
    # look DEAD to the watchdog, not freshly beating every restart.
    threading.Thread(target=_heartbeat_loop, args=(client,), daemon=True,
                     name="slack-socket-heartbeat").start()
    threading.Event().wait()


if __name__ == "__main__":
    # Same dual-namespace guard as core.worker (bit us live): delegate to the
    # canonical module so there is exactly one of everything in the process.
    from core import slack_socket as _canonical
    _canonical.run_forever()
