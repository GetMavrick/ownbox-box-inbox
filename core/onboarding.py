"""Set-up steps — how a machine asks the buyer for what it needs, without core knowing the machine.

THE BASE BOX STAYS CLEAN (owner, 2026-09-16: "We need to protect the base machine and keep it clean
for modular upgrades"). Before this module the set-up screen's contract lived in
`core/box_secrets.py`: the Gmail instructions, the Zernio instructions, and a line that picked each
step's status by name — `email_state() if step["key"] == "email" else zernio_state()`. Every box of
every type shipped the inbox's onboarding, and adding a machine meant editing core. Here a machine
registers its step at import, the way it already registers its jobs (`worker.register_periodic`),
its tables (`state.register_schema`) and its report (`report.register_reporter`), and core only
orders, merges and dispatches.

WHAT A STEP IS. Copy the buyer reads (title, why, numbered steps, fields, an optional note and an
optional link out), plus two callables the machine owns:

    state()                          -> {"status": <STATUSES>, "who": str|None, "detail": str}
    save(values, *, user_id=None)    -> None, or raise StepRejected("a sentence they can act on")

`save` receives ONLY the fields the step declared — never the raw form — and the screen never
receives a value, only a status and a sentence.

LIVE, NOT SNAPSHOTTED — which is why this does not copy the morning report's table. A buyer who
pastes a key must see "connected" on the next render, and `save` has to run in the process that
received the POST. So the reading process imports the machines' registrations itself
(`worker.import_registrations`), tolerating a broken machine rather than losing the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from core.logging import get_logger

log = get_logger(__name__)

# A CLOSED SET, because the screen renders a fixed set of states and an unknown one becomes a blank
# row in front of a paying customer. `unavailable` is the state core itself produces when a
# machine's state() raises or answers outside this set: a mailbox that could not be CHECKED must
# not read as "not connected", which would send someone to make a new password they did not need.
STATUSES = frozenset({"not_connected", "connected", "needs_reauth", "admin_disabled",
                      "payment_required", "unavailable"})
_FIELD_TYPES = frozenset({"text", "email", "password", "url"})
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class StepRejected(ValueError):
    """The value was not stored. The message is shown to the buyer, so it says what to fix."""


@dataclass(frozen=True)
class Step:
    key: str
    order: int
    machine: str
    title: str
    why: str
    fields: tuple
    steps: tuple
    state: Callable[[], Mapping]
    save: Callable[..., None]
    note: str = ""
    link: Mapping | None = field(default=None)


_STEPS: dict[str, Step] = {}


def register_step(key: str, *, order: int, machine: str, title: str, why: str, fields, steps,
                  state: Callable[[], Mapping], save: Callable[..., None], note: str = "",
                  link: Mapping | None = None) -> None:
    """Called by a machine at import. Idempotent for the same machine, so a re-import cannot stack
    two; refused for a key another machine already holds, so one machine cannot overwrite another's
    step by choosing the same word. Everything is checked here, at import, where a mistake is a
    failed boot log line — not on the screen, where it would be a customer's broken page."""
    if not _KEY.match(key or ""):
        raise ValueError(f"set-up step key {key!r} must be a short lowercase slug")
    if not machine or not isinstance(machine, str):
        raise ValueError(f"set-up step {key!r} must name the machine that owns it")
    held = _STEPS.get(key)
    if held is not None and held.machine != machine:
        raise ValueError(f"set-up step {key!r} is already registered by {held.machine!r}")
    if not isinstance(order, int):
        raise ValueError(f"set-up step {key!r} needs an integer order")
    if not (title or "").strip() or not (why or "").strip():
        raise ValueError(f"set-up step {key!r} needs a title and a reason a buyer can read")
    if not callable(state) or not callable(save):
        raise ValueError(f"set-up step {key!r} needs callable state() and save()")
    fields = tuple(dict(f) for f in (fields or ()))
    if not fields:
        raise ValueError(f"set-up step {key!r} asks for nothing — a step with no field is not a step")
    names = [f.get("name") for f in fields]
    for f in fields:
        if not f.get("name") or not f.get("label") or f.get("type") not in _FIELD_TYPES:
            raise ValueError(f"set-up step {key!r}: every field needs a name, a label and a type "
                             f"in {sorted(_FIELD_TYPES)} — got {f!r}")
    if len(set(names)) != len(names):
        raise ValueError(f"set-up step {key!r} declares the same field twice: {names}")
    steps = tuple(str(s) for s in (steps or ()))
    if not steps:
        raise ValueError(f"set-up step {key!r} needs at least one instruction")
    if link is not None:
        link = dict(link)
        if not link.get("label") or not str(link.get("url", "")).startswith("https://"):
            raise ValueError(f"set-up step {key!r}: a link needs a label and an https:// url")
    _STEPS[key] = Step(key=key, order=order, machine=machine, title=title, why=why, fields=fields,
                       steps=steps, state=state, save=save, note=note or "", link=link)
    log.info("onboarding.step_registered", key=key, machine=machine, order=order)


def _ensure_registered() -> None:
    from core import worker
    worker.import_registrations()


def _live_state(step: Step) -> dict:
    """What the screen may see about one step: a status from the closed set, who, and a sentence.
    Nothing else the machine returned passes through — so a state() that carelessly returns the
    stored credential still cannot put it on a page."""
    try:
        got = step.state() or {}
    except Exception as e:                          # noqa: BLE001 — one step, not the whole screen
        log.error("onboarding.state_failed", key=step.key, machine=step.machine,
                  error=type(e).__name__)
        return {"status": "unavailable", "who": None,
                "detail": "This could not be checked just now. Reload the page in a minute."}
    status = got.get("status") or "not_connected"
    if status not in STATUSES:
        log.error("onboarding.state_unknown_status", key=step.key, machine=step.machine,
                  status=str(status)[:40])
        return {"status": "unavailable", "who": None,
                "detail": "This could not be checked just now. Reload the page in a minute."}
    who = got.get("who", got.get("user"))
    return {"status": status, "who": str(who) if who else None,
            "detail": str(got.get("detail") or "").strip()[:300]}


def steps() -> list[dict]:
    """Every registered step, in order, merged with its live state — what the set-up screen renders.

    A step's link out is only offered once that step is past `not_connected`: sending someone to
    connect accounts before the box can read them is a step they would have to repeat. The machine
    may say why in `link["disabled_because"]`."""
    _ensure_registered()
    out = []
    for step in sorted(_STEPS.values(), key=lambda s: (s.order, s.key)):
        live = _live_state(step)
        # Assigned field by field, never `**live`: the machine's answer can only ever fill these
        # three slots, so nothing it returns can overwrite the step's own key, title or fields.
        entry = {"key": step.key, "order": step.order, "machine": step.machine, "title": step.title,
                 "why": step.why, "fields": [dict(f) for f in step.fields], "steps": list(step.steps),
                 "note": step.note, "status": live["status"], "who": live["who"],
                 "detail": live["detail"]}
        if step.link:
            enabled = live["status"] != "not_connected"
            entry["link"] = dict(step.link, enabled=enabled,
                                 disabled_because="" if enabled else
                                 (step.link.get("disabled_because") or "Finish this step first."))
        out.append(entry)
    return out


def save(key: str, form: Mapping, *, user_id: str | None = None) -> None:
    """Store what a buyer submitted for one step. Raises StepRejected with a sentence for them.

    Only the fields the step DECLARED are passed on, stripped of nothing else — a hidden form field
    cannot reach a machine's storage by being posted. A machine that still raises the older
    `box_secrets.SecretRejected` is translated, so a step can move onto this seam without rewriting
    every sentence it already says."""
    _ensure_registered()
    step = _STEPS.get(key)
    if step is None:
        raise StepRejected("That set-up step does not exist on this box.")
    values = {f["name"]: str(form.get(f["name"]) or "") for f in step.fields}
    from core.box_secrets import SecretRejected
    try:
        step.save(values, user_id=user_id)
    except StepRejected:
        raise
    except SecretRejected as e:
        raise StepRejected(str(e)) from e
    log.info("onboarding.step_saved", key=key, machine=step.machine, user=user_id)
