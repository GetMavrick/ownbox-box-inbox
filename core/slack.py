"""Slack notifier — zero Claude reasoning. Posts job results, ready links, alerts.

If SLACK_BOT_TOKEN is unset (e.g. local dev), calls degrade to a log line so the
rest of the system runs unchanged.
"""
import contextvars

import requests

from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)
_API = "https://slack.com/api/chat.postMessage"

# A1 never-silent: a scope-local flag flipped on ANY confirmed post, so the worker can
# tell whether a job actually reached the owner. A ContextVar (not a module global) so
# concurrent contexts/tests never clobber one another; the worker resets it per job.
_posted_in_scope = contextvars.ContextVar("aios_slack_posted", default=False)


def begin_post_tracking() -> None:
    """Reset the per-scope 'a message was delivered' flag. The worker calls this right
    before running a handler, so posted_in_scope() then reflects only that one job."""
    _posted_in_scope.set(False)


def posted_in_scope() -> bool:
    """True iff some _post has been confirmed delivered since begin_post_tracking()."""
    return _posted_in_scope.get()


def is_operator(user_id: str | None) -> bool:
    """True if user_id is the configured operator (owner). FAILS CLOSED (owner decision
    2026-07-20, reversing the earlier permissive-when-unset contract).

    With no OPERATOR_SLACK_USER_ID set, nobody is the operator and every owner-only gate
    denies. The old permissive default meant a clone deployed from .env.example — where the
    var ships BLANK — granted every workspace member authority to trigger a paid HeyGen
    render on the client's plan, publish publicly to the client's accounts, and approve
    cold email from the client's verified domain. Permissive-until-configured is the wrong
    default for irreversible, billable, outward-facing actions; launch_check now refuses
    go-live without the var, and the denial logs at ERROR with the fix, so a single-user
    box that forgot to set it gets a named cause instead of buttons that silently no-op."""
    op = (settings.operator_slack_user_id or "").strip()
    if not op:
        log.error("slack.operator_unset_denying_owner_action",
                  hint="set OPERATOR_SLACK_USER_ID in .env to enable owner-only actions")
        return False
    return user_id == op


def _post(channel: str, text: str, thread_ts: str | None = None,
          blocks: list | None = None) -> bool:
    """Return True only if Slack confirmed delivery (ok:true). False on no token /
    no channel / HTTP error / Slack error. The watchdog uses this return value to
    decide whether to fire the external dead-man's switch — a notifier that can't
    actually reach the operator must be detectable through a different channel, or
    a silent Slack outage would hide 'everything is down'.

    `text` is ALWAYS sent: with blocks it is the notification preview + the
    accessibility/legacy-client fallback, so a Block Kit message degrades to
    readable text everywhere."""
    if not settings.slack_bot_token or not channel:
        log.info("slack.noop", channel=channel, text=text[:120])
        return None
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        r = requests.post(
            _API, json=payload,
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            timeout=10,
        )
        body = r.json() if r.content else {}
        if not r.ok or not body.get("ok"):
            log.warning("slack.failed", status=r.status_code, body=str(body)[:200])
            return None
        ts = body.get("ts")          # the message ts (truthy) — callers thread under it
        if ts:
            _posted_in_scope.set(True)   # A1: a message reached Slack in this scope
        return ts
    except Exception as e:  # pragma: no cover
        log.warning("slack.error", error=str(e))
        return None


def post(channel: str, text: str, blocks: list | None = None) -> str | None:
    """Post a top-level message. Returns the message `ts` on success (truthy — callers
    thread updates under it), None on noop/failure."""
    return _post(channel, text, blocks=blocks)


def thread_reply(channel: str, thread_ts: str, text: str,
                 blocks: list | None = None) -> str | None:
    return _post(channel, text, thread_ts, blocks=blocks)


def _reaction(action: str, channel: str, ts: str, name: str) -> bool:
    """Add/remove an emoji reaction on a message (reactions.add|remove). BEST-EFFORT — a
    progress indicator must NEVER crash or slow the work it's decorating, so every failure
    (no token, already_reacted, no_reaction, HTTP error) is swallowed to a logged no-op."""
    if not settings.slack_bot_token or not channel or not ts:
        return False
    try:
        r = requests.post(
            f"https://slack.com/api/reactions.{action}",
            json={"channel": channel, "timestamp": ts, "name": name},
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"}, timeout=8)
        body = r.json() if r.content else {}
        # already_reacted / no_reaction are benign races (double-add / remove-missing), not errors.
        if not body.get("ok") and body.get("error") not in ("already_reacted", "no_reaction"):
            log.info("slack.reaction_failed", action=action, name=name,
                     error=str(body.get("error"))[:60])
        return bool(body.get("ok"))
    except Exception as e:  # pragma: no cover — a reaction outage never touches the job
        log.info("slack.reaction_error", error=str(e)[:120])
        return False


def add_reaction(channel: str, ts: str, name: str) -> bool:
    """React ⏳/✅/❌ on the owner's message so they SEE the bot is on it. Best-effort."""
    return _reaction("add", channel, ts, name)


def remove_reaction(channel: str, ts: str, name: str) -> bool:
    return _reaction("remove", channel, ts, name)


def respond(response_url: str, text: str, *, replace_original: bool = True) -> bool:
    """Replace (or follow up on) an interactive message via its response_url —
    used to retire Block Kit buttons the instant a click is acknowledged, so a
    second tap can't double-fire. The url is pre-authenticated (no bot token) and
    valid ~30 min. Best-effort: the work is already enqueued, so a failure here
    only logs and the buttons simply linger."""
    if not response_url:
        return False
    try:
        # net-ok: Slack's OWN response_url, taken from a payload whose HMAC signature this box
        # verified before the handler ran. Not config, and not a stranger's host — Slack's.
        r = requests.post(response_url,
                          json={"text": text, "replace_original": replace_original},
                          timeout=10)
        return bool(r.ok)
    except Exception as e:  # pragma: no cover
        log.warning("slack.respond_error", error=str(e)[:200])
        return False


def _may_page_operator() -> tuple[bool, str]:
    """ONLY THE BOX MAY PAGE THE OWNER'S PHONE.

    Alert dedup lives in SQLite (`state.get_alert_row`), so it is per-database. Every other
    copy of this code — a cloud dev sandbox, a laptop, a CI runner, a test that forgot its
    hermetic header — starts with a FRESH database. Its alert table is empty, so every pass
    reads `prev == OK` and fires a brand-new FAILURE EDGE. One such process pages forever,
    and no amount of fixing the box helps.

    That is exactly what happened: the owner was DM'd
    `claude_code FAILED: CLAUDE_CODE_OAUTH_TOKEN not set in /opt/aios/.env` dozens of times a
    day for two months — a path that does not even exist off the box — while the box's own
    `claude_code` alert row sat OK, last fired 2026-07-25. Ten reports, two attempted fixes,
    both aimed at the box, which was never the sender. (Same bug class as the tests that DM'd
    the real operator, see the hermetic note in core/config.py.)

    FAIL-SAFE, NOT FAIL-CLOSED. Production is the DEFAULT: only a clear non-production signal
    silences the pager. A missed marker must never cost a real outage its alert, so this errs
    toward paging. `AIOS_OPERATOR_ALERTS=1` force-enables for a deliberate off-box test.
    """
    import os
    from pathlib import Path

    if os.environ.get("AIOS_OPERATOR_ALERTS") == "1":
        return True, ""
    if os.environ.get("AIOS_HERMETIC_TEST"):
        return False, "hermetic test"
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        return False, "CI runner"
    try:
        from core.config import ROOT
        if Path(ROOT) != Path("/opt/aios"):
            return False, f"not the production checkout (ROOT={ROOT})"
    except Exception:                    # noqa: BLE001 — never let the guard break paging
        return True, ""
    return True, ""


def send_dm(user_id: str, text: str) -> bool:
    """DM the operator. Suppressed off-box — see _may_page_operator for why that matters."""
    allowed, why = _may_page_operator()
    if not allowed:
        # Loud in logs, silent on the phone: a dev session still SEES what it would have sent.
        log.warning("slack.operator_dm_suppressed", reason=why, text=text[:160])
        return True                      # not a send failure — there was nothing to send
    # chat.postMessage accepts a user ID as the channel for a DM.
    return _post(user_id, text)


def upload_file(channel: str, *, content, filename: str, title: str | None = None,
                comment: str | None = None, thread_ts: str | None = None) -> bool:
    """Share a file into a channel via Slack's modern external-upload flow
    (files.getUploadURLExternal → PUT the bytes → files.completeUploadExternal). Returns
    True only on confirmed completion; False on no token / missing `files:write` scope /
    any transport or API error, so callers can fall back to a plain text post. Needs the
    bot to be a member of `channel` for the share to land."""
    if not settings.slack_bot_token or not channel:
        log.info("slack.upload_noop", channel=channel, filename=filename)
        return False
    data = content.encode() if isinstance(content, str) else content
    hdr = {"Authorization": f"Bearer {settings.slack_bot_token}"}
    try:
        r1 = requests.post("https://slack.com/api/files.getUploadURLExternal",
                           headers=hdr, data={"filename": filename, "length": len(data)},
                           timeout=10)
        b1 = r1.json() if r1.content else {}
        if not b1.get("ok"):
            log.warning("slack.upload_url_failed", body=str(b1)[:200])
            return False
        upload_url, file_id = b1.get("upload_url"), b1.get("file_id")
        if not (upload_url and file_id):       # ok:true but malformed → degrade to text fallback
            log.warning("slack.upload_url_malformed", body=str(b1)[:200])
            return False
        # net-ok: Slack's upload_url, returned by files.getUploadURLExternal on the call just
        # above, over TLS, authenticated with our own bot token.
        r2 = requests.post(upload_url, data=data, timeout=30)
        if not r2.ok:
            log.warning("slack.upload_put_failed", status=r2.status_code)
            return False
        payload = {"files": [{"id": file_id, "title": title or filename}],
                   "channel_id": channel}
        if comment:
            payload["initial_comment"] = comment
        if thread_ts:
            payload["thread_ts"] = thread_ts
        r3 = requests.post("https://slack.com/api/files.completeUploadExternal",
                           headers={**hdr, "Content-Type": "application/json; charset=utf-8"},
                           json=payload, timeout=10)
        b3 = r3.json() if r3.content else {}
        if not b3.get("ok"):
            log.warning("slack.upload_complete_failed", body=str(b3)[:200])
            return False
        _posted_in_scope.set(True)
        return True
    except Exception as e:  # pragma: no cover
        log.warning("slack.upload_error", error=str(e)[:200])
        return False


def read_thread(channel: str, thread_ts: str, limit: int = 12) -> list[dict]:
    """conversations.replies → [{user, text, ts, bot}] oldest-first, [] on any
    failure / missing config. Needs channels:history (public) or groups:history
    (private) — the clone bot carries both, member-channels only. Used by the
    brain handler so a threaded question is answered with its thread in view."""
    if not settings.slack_bot_token or not channel or not thread_ts:
        return []
    try:
        r = requests.get(
            "https://slack.com/api/conversations.replies",
            params={"channel": channel, "ts": thread_ts, "limit": limit},
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            timeout=10,
        )
        body = r.json() if r.content else {}
        if not r.ok or not body.get("ok"):
            log.warning("slack.read_thread_failed", status=r.status_code,
                        body=str(body)[:200])
            return []
        return [{"user": m.get("user") or m.get("bot_id") or "?",
                 "text": m.get("text") or "", "ts": m.get("ts"),
                 "bot": bool(m.get("bot_id"))}
                for m in body.get("messages", [])[:limit]]
    except Exception as e:  # pragma: no cover
        log.warning("slack.read_thread_error", error=str(e)[:200])
        return []
