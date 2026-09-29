"""The box's check-in: it tells Ownbox it is running (docs/PLAN_NO_GHOST_BOXES.md P1).

OWNER, 2026-09-29: *"One of my biggest fears launching this venture is that we're not going to deliver
high-quality manage support services and we will have a large percentage of disconnected ghost boxes. We
always want to have some sort of connection and ability to bring boxes back into our managed fleet."*
He approved the direction the same day (#1678). This is the box's half; the fleet's half, which receives
it and pages a person when a box goes quiet, is provisioner/checkin.py (P2).

WHY THE BOX REACHES OUT. Every other contact is Ownbox reaching IN (https://<host>/deploy/* with a
token), and that breaks the moment the address, the token, the certificate or the cloud account changes.
A box that can reach the internet can still say "I'm here" through all of those, including from inside
its owner's own cloud account after it has been taken home.

WHAT MAKES IT HONEST, NOT A BACKDOOR:
  * IT CARRIES NO CUSTOMER DATA. `FIELDS` is the whole of it: which box, what it runs, whether its last
    update worked, which of its own health checks fail (by name only), and its plan. No mail, leads,
    contacts, conversations, settings values or secrets. The receiver refuses any other field, and
    tests/test_box_checkin.py holds the same list, so adding one is a reviewed decision on both sides.
  * THE OWNER SEES IT. Settings → Updates shows the exact last message, when it went and where.
  * THE OWNER CAN SWITCH IT OFF, and off means off: one last message says "switched off", so Ownbox
    knows the difference between a box that chose quiet and a box that went dark, and then nothing.
  * IT GRANTS NOTHING. It is information sent TO Ownbox. It opens no way into the box, and the box
    reads nothing from the reply.

HOW IT IS SIGNED. With the box's own update key (/var/lib/aios/update_key, minted by
scripts/box_update_key.sh), `ssh-keygen -Y sign -n ownbox-checkin`. The fleet verifies it with the PUBLIC
half the box already publishes on /health. No new secret exists, and none is sent.

WHEN. aios-checkin.timer: two minutes after boot, then every six hours with spread. scripts/box_update.sh
starts one after every update attempt, whatever it decided. A systemd timer rather than a worker job on
purpose: the check-in must still go out when the worker is the thing that is broken.

IT NEVER BREAKS THE BOX. Nothing here raises to a caller. A check-in that cannot be sent is recorded
locally and the next tick tries again; it never blocks the updater, the worker or a page.

WIRE FORMAT (matches provisioner/checkin.py, #1681): POST {URL} with body
{"payload": "<the payload as JSON text>", "sig": "<armored ssh signature over exactly that text>"}.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 1
NAMESPACE = "ownbox-checkin"
URL = os.environ.get("AIOS_CHECKIN_URL") or "https://orders.ownbox.app/checkin"
MAX_BYTES = 8192                          # the receiver refuses anything larger
FIELDS = frozenset({"v", "host", "order_id", "droplet_id", "release", "update", "doctor", "plan",
                    "watchdog_ok", "enabled", "sent_at"})

KEY = Path(os.environ.get("AIOS_UPDATE_KEY") or "/var/lib/aios/update_key")
LAST = Path(os.environ.get("AIOS_CHECKIN_LAST") or "/var/lib/aios/checkin_last.json")
METADATA_ID = "http://169.254.169.254/metadata/v1/id"   # DigitalOcean: which droplet this is
EVERY = "every six hours"                                # said on the screen; the timer is the truth
_SETTING_MACHINE, _SETTING_KEY = "core", "checkin"

# The watchdog beats every 30 minutes; two hours without any beat means it is not running.
_WATCHDOG_FRESH_S = 2 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


# ── the owner's switch ─────────────────────────────────────────────────────────────────────────────

def enabled() -> bool:
    """On unless the owner switched it off. An unreadable setting reads as ON: the default the owner
    was shown, and the one that keeps a box findable."""
    try:
        from core import box_settings
        got = box_settings.get(_SETTING_MACHINE, _SETTING_KEY, default={})
        return not (isinstance(got, dict) and got.get("enabled") is False)
    except Exception:                                    # noqa: BLE001 — see the docstring
        return True


def set_enabled(on: bool, *, by: str = "") -> None:
    from core import box_settings
    box_settings.put(_SETTING_MACHINE, _SETTING_KEY, {"enabled": bool(on)}, set_by=by or "owner")


# ── what it says ───────────────────────────────────────────────────────────────────────────────────

def _provision() -> dict:
    try:
        from core import claim
        with open(claim.PROVISION_JSON, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:                                    # noqa: BLE001 — no provision.json: not a sold box
        return {}


def _droplet_id() -> str:
    """This droplet's id from DigitalOcean's metadata service, or "" anywhere else. It is what tells our
    original apart from a copy taken home: same host, same key, a different droplet."""
    if os.environ.get("AIOS_HERMETIC_TEST"):
        return os.environ.get("AIOS_CHECKIN_DROPLET_ID", "")
    try:
        with urllib.request.urlopen(METADATA_ID, timeout=2) as r:
            got = r.read(64).decode("ascii", "replace").strip()
        return got if got.isdigit() else ""
    except Exception:                                    # noqa: BLE001 — not on DigitalOcean, or it is slow
        return ""


def _release() -> str | None:
    try:
        from core import box_updates
        return box_updates._installed()
    except Exception:                                    # noqa: BLE001
        return None


# How the updater's own words reduce to the one word the fleet acts on.
_RESULT = {"up_to_date": "ok", "selected": "ok", "no_managed": "off", "rolled_back": "rolled_back",
           "install_failed": "failed", "all_refused": "failed", "cannot_run": "failed"}


def _update() -> dict:
    """The last update decision: when, the updater's own status word, and the fleet's one word."""
    try:
        from core import box_updates
        d = box_updates._last_decision() or {}
    except Exception:                                    # noqa: BLE001
        d = {}
    status = str(d.get("status") or "")
    return {"at": d.get("at"), "status": status or None,
            "result": _RESULT.get(status, "unknown" if status else "never")}


def _doctor() -> tuple[dict, bool | None]:
    """The box's own health checks, BY NAME ONLY, and whether the watchdog is still running them.

    The checks are the watchdog's probes (heartbeat components `probe:<name>`, core/watchdog.py): their
    names and ok/warn/fail, never the detail, which can hold an address or an error message."""
    try:
        from core import state
        beats = [b for b in state.get_heartbeats() if str(b.get("component") or "").startswith("probe:")]
    except Exception:                                    # noqa: BLE001
        return {"ok": 0, "warn": 0, "fail": 0, "failing": []}, None
    counts = {"ok": 0, "warn": 0, "fail": 0}
    failing, newest = [], None
    for b in beats:
        raw = str(b.get("status") or "")
        st = "ok" if raw == "ok" or raw.startswith("ok:") else ("fail" if raw.startswith("fail") else "warn")
        counts[st] += 1
        if st == "fail":
            failing.append(str(b.get("component"))[len("probe:"):][:40])
        try:
            t = datetime.fromisoformat(str(b.get("ts")).replace("Z", "+00:00"))
            t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            newest = t if newest is None or t > newest else newest
        except (TypeError, ValueError):
            pass
    watchdog_ok = None if newest is None else (_now() - newest).total_seconds() < _WATCHDOG_FRESH_S
    return {**counts, "failing": sorted(failing)[:10]}, watchdog_ok


def _plan() -> dict:
    out: dict = {}
    try:
        from core import box_updates
        out["updates"] = box_updates.plan().get("updates") or "untold"
    except Exception:                                    # noqa: BLE001
        out["updates"] = "untold"
    try:
        from core import tiers
        out["tier"] = tiers.current().get("tier")
    except Exception:                                    # noqa: BLE001
        pass
    return out


def payload(*, on: bool | None = None) -> dict | None:
    """The whole check-in, or None on a box that is not a sold box (no host or order in provision.json):
    the operator's own box and a developer's checkout say nothing to anyone."""
    prov = _provision()
    host, order = str(prov.get("host") or "").strip(), str(prov.get("order") or "").strip()
    if not host or not order:
        return None
    doctor, watchdog_ok = _doctor()
    out = {"v": FORMAT, "host": host, "order_id": order, "droplet_id": _droplet_id(),
           "release": _release(), "update": _update(), "doctor": doctor, "plan": _plan(),
           "watchdog_ok": watchdog_ok, "enabled": enabled() if on is None else bool(on),
           "sent_at": _iso(_now())}
    assert set(out) == FIELDS, "the check-in's fields changed without the list changing"
    return out


# ── signing and sending ────────────────────────────────────────────────────────────────────────────

def sign(text: str, *, key: Path | None = None) -> str | None:
    """An armored ssh signature over exactly `text`, or None when the box has no key or signing fails."""
    key = key or KEY
    if not key.is_file():
        return None
    d = tempfile.mkdtemp(prefix="checkin.")
    msg = Path(d) / "payload"
    try:
        msg.write_text(text, encoding="utf-8")
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(msg)],
                           capture_output=True, text=True, timeout=20)
        sig = Path(str(msg) + ".sig")
        return sig.read_text() if r.returncode == 0 and sig.is_file() else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for p in Path(d).glob("*"):
            p.unlink(missing_ok=True)
        os.rmdir(d)


def _post(body: bytes) -> tuple[int | None, str]:
    if os.environ.get("AIOS_HERMETIC_TEST"):
        return None, "hermetic"                          # a test never reaches the real fleet
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "ownbox-box"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, f"HTTP {e.code}"
    except Exception as e:                               # noqa: BLE001 — DNS, TLS, timeout, no network
        return None, type(e).__name__


def last() -> dict:
    """What the box last sent, for the owner's screen: {"at", "to", "sent", "result", "payload"}, or {}."""
    try:
        got = json.loads(LAST.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(entry: dict) -> None:
    try:
        LAST.parent.mkdir(parents=True, exist_ok=True)
        tmp = LAST.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=1), encoding="utf-8")
        tmp.replace(LAST)
    except OSError:
        pass


def run(*, post=_post) -> str:
    """One check-in, if one is due to be said. Returns what happened, in one word. Never raises.

      sent            the fleet accepted it
      failed          it could not be delivered (recorded; the next tick tries again)
      switched_off    the owner turned it off and Ownbox has already been told; nothing was sent
      not_a_box       no host/order in provision.json (the operator's box, a developer's checkout)
      no_key          the box has no update key to sign with
    """
    try:
        on = enabled()
        prev = last()
        if not on and prev.get("payload", {}).get("enabled") is False and prev.get("sent"):
            return "switched_off"                        # told once already; off means nothing more
        p = payload(on=on)
        if p is None:
            return "not_a_box"
        text = json.dumps(p, separators=(",", ":"), sort_keys=True)
        sig = sign(text)
        if sig is None:
            _record({"at": p["sent_at"], "to": URL, "sent": False, "result": "no_key", "payload": p})
            return "no_key"
        body = json.dumps({"payload": text, "sig": sig}).encode("utf-8")
        if len(body) > MAX_BYTES:                        # never happens with FIELDS; say so if it does
            _record({"at": p["sent_at"], "to": URL, "sent": False, "result": "too_large", "payload": p})
            return "failed"
        status, why = post(body)
        ok = status is not None and 200 <= status < 300
        _record({"at": p["sent_at"], "to": URL, "sent": ok,
                 "result": "accepted" if ok else (why or f"HTTP {status}"), "payload": p})
        return "sent" if ok else "failed"
    except Exception as e:                               # noqa: BLE001 — a check-in never breaks the box
        _record({"at": _iso(_now()), "to": URL, "sent": False, "result": type(e).__name__, "payload": {}})
        return "failed"


def main() -> int:
    print(f"check-in: {run()}")
    return 0                                             # a failed check-in is not a failed unit


if __name__ == "__main__":
    raise SystemExit(main())
