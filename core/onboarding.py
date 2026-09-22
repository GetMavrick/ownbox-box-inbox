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

# WHAT A SETTING MAY BE, AND IT IS A CLOSED SET FOR THE SAME REASON THE FIELD TYPES ARE. One
# renderer draws every connection's settings; an open set is how a machine ends up shipping HTML
# into the screen, and then the screen is the thing that knows what each machine means.
_SETTING_TYPES = frozenset({"toggle", "choice", "text", "number"})

# WHO MAY CHANGE IT. `users.role` is a closed set of two on a box — 'owner' | 'member' — so this is
# too. Credentials and anything that spends money are the owner's; a preference a person holds for
# themselves is not (docs/PLAN_BOX_SETTINGS_AND_CONNECTIONS.md §6).
_SETTING_ROLES = frozenset({"owner", "member"})

# WHOSE ANSWER IT IS. `box` is one answer for the machine; `person` is an answer each seat holds
# separately. "Which timezone this box is in" is a box answer and "notify me at 8am" is not — and
# discovering that after a box has two people in it means migrating live rows, so a setting says
# which it is on the day it is declared.
_SETTING_SCOPES = frozenset({"box", "person"})

# HOW LONG A TEXT ANSWER MAY BE, and why only this type needs a number at all. Three of the four
# setting types are bounded by their own shape: a toggle is a bool, a choice is one of a declared
# list, and a number is an int. `text` is whatever arrived in the form body — so it is the one a
# paste can turn into a row that nothing renders, no screen can shrink again, and every read of
# that machine's settings carries forever. OSDev1's gate on the registry step, 2026-09-17: "put()
# trusts its caller, so the registry must refuse undeclared keys and cap value size."
#
# COUNTED IN CHARACTERS, not bytes, because the sentence the buyer reads is in characters and a
# cap he cannot count against is a cap he cannot satisfy. 200 characters of emoji is 800 bytes,
# which is nothing next to the reason for the limit.
#
# A MACHINE MAY ASK FOR MORE, up to the ceiling, by declaring `max_len` — and the ceiling exists
# so that "more" is a decision made here once rather than by whoever writes the next machine.
_TEXT_MAX = 200
_TEXT_CEILING = 2000
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
    settings: tuple = ()
    # WHICH SCREEN RENDERS IT — `box_secrets.SURFACE_MACHINE` or `SURFACE_BOX`. The default is the
    # machine's own set-up screen, which is where every step registered before 2026-09-22 already
    # rendered; a default that moved somebody's step into core's Settings on upgrade would be the
    # worst kind of quiet. A machine declares `surface="box"` only for something every machine on
    # the box would need, and `core/dash/home.py` is what draws those.
    surface: str = "machine"


_STEPS: dict[str, Step] = {}


def register_step(key: str, *, order: int, machine: str, title: str, why: str, fields, steps,
                  state: Callable[[], Mapping], save: Callable[..., None], note: str = "",
                  link: Mapping | None = None, settings=(), surface: str = "machine") -> None:
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
    # CHECKED HERE RATHER THAN READ LENIENTLY LATER. A typo — "Box", "core", "system" — would
    # otherwise fall through `surface_of`'s default and the step would simply render on the wrong
    # screen, which is a bug nobody would think to look for.
    if surface not in ("box", "machine"):
        raise ValueError(f"set-up step {key!r}: surface must be 'box' or 'machine', got {surface!r}")
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
    settings = tuple(dict(x) for x in (settings or ()))
    seen: set = set()
    for opt in settings:
        name = opt.get("name")
        if not name or not opt.get("label"):
            raise ValueError(f"set-up step {key!r}: every setting needs a name and a label — {opt!r}")
        if opt.get("type") not in _SETTING_TYPES:
            raise ValueError(f"set-up step {key!r}: setting {name!r} needs a type in "
                             f"{sorted(_SETTING_TYPES)} — got {opt.get('type')!r}")
        if opt.setdefault("who", "owner") not in _SETTING_ROLES:
            raise ValueError(f"set-up step {key!r}: setting {name!r} has an unknown `who` "
                             f"{opt['who']!r} — {sorted(_SETTING_ROLES)}")
        if opt.setdefault("scope", "box") not in _SETTING_SCOPES:
            raise ValueError(f"set-up step {key!r}: setting {name!r} has an unknown `scope` "
                             f"{opt['scope']!r} — {sorted(_SETTING_SCOPES)}")
        # A CHOICE WITH NO CHOICES IS A DEAD CONTROL, and it renders as an empty dropdown the buyer
        # cannot use. Caught here rather than on his screen.
        if opt["type"] == "choice" and not tuple(opt.get("choices") or ()):
            raise ValueError(f"set-up step {key!r}: setting {name!r} is a choice with no choices")
        # THE CAP IS RESOLVED AT IMPORT, NOT AT THE WRITE, so a machine that declares a nonsense
        # limit fails the box's own boot rather than one buyer's save. A limit on a type that is
        # already bounded is REFUSED rather than ignored: a declaration the code does not honour
        # is a lie the next reader believes.
        if opt["type"] == "text":
            cap = opt.setdefault("max_len", _TEXT_MAX)
            if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= _TEXT_CEILING:
                raise ValueError(f"set-up step {key!r}: setting {name!r} has a max_len of {cap!r} "
                                 f"— it must be a whole number from 1 to {_TEXT_CEILING}")
        elif "max_len" in opt:
            raise ValueError(f"set-up step {key!r}: setting {name!r} is a {opt['type']} and cannot "
                             f"declare max_len — only `text` is unbounded without one")
        if name in seen:
            raise ValueError(f"set-up step {key!r} declares the setting {name!r} twice")
        seen.add(name)
    steps = tuple(str(s) for s in (steps or ()))
    if not steps:
        raise ValueError(f"set-up step {key!r} needs at least one instruction")
    if link is not None:
        link = dict(link)
        if not link.get("label") or not str(link.get("url", "")).startswith("https://"):
            raise ValueError(f"set-up step {key!r}: a link needs a label and an https:// url")
    _STEPS[key] = Step(key=key, order=order, machine=machine, title=title, why=why, fields=fields,
                       steps=steps, state=state, save=save, note=note or "", link=link,
                       settings=tuple(settings), surface=surface)
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
                 "detail": live["detail"],
                 # COPIED, NEVER HANDED OUT. The same rule the fields follow one line up: a screen
                 # that mutated this would be editing the registry every other screen reads.
                 "settings": [dict(o) for o in step.settings], "surface": step.surface}
        if step.link:
            enabled = live["status"] != "not_connected"
            entry["link"] = dict(step.link, enabled=enabled,
                                 disabled_because="" if enabled else
                                 (step.link.get("disabled_because") or "Finish this step first."))
        out.append(entry)
    return out


def settings_sections() -> list[dict]:
    """The settings menu — ONE entry per machine that declares settings. Derived, never registered.

    R4 OF docs/SCOPE_WHAT_A_MACHINE_MAY_PUT_IN_THE_MENU.md.

    WHO ACTUALLY BACKED THIS, corrected after OSDev1 caught me writing "backed by the owner" with
    no words of his behind it (2026-09-17). The design is backed by OSDev1 as core owner — "I back
    R1-R4 as core owner", on the wall — and by OSDev5, whose #1322 says "agreed without
    reservation" and who is waiting on it. The OWNER said "Yes go!!" in my session, on a turn
    where R4 was the only open question I had put to him; that is a go-ahead to build and it is
    not a ruling on the design, and the difference matters because he reads these files to know
    what he has already decided. If he rules on the substance, quote him here and delete this
    paragraph.

    A machine would otherwise declare its settings in one place and its
    settings MENU ENTRY in another, and two declarations of one intent drift in both directions:

      * an entry whose page has no settings behind it — a dead control on a paying customer's
        screen, and the kind this repo keeps deleting by name;
      * a setting that is stored, role-gated, enforced, and CANNOT BE REACHED.

    Neither is catchable by a test that looks at only one registry, because each registry is
    self-consistent. Deriving one from the other removes the disagreement rather than policing it.

    ONE ENTRY PER MACHINE, NOT PER STEP — R1, and it is the budget that keeps the rail on a phone.
    The inbox declares its mailbox and its social account as two set-up steps; in Settings it is
    one row called Inbox, and both sit behind it. Six machines is six rows.

    CORE SUPPLIES THE TRUTH AND THE SCREEN SUPPLIES THE WORDS. What comes back is the machine's
    key and the steps beneath it, never a label — `core/` names no machine, and the human name a
    machine goes by already exists where the rail is declared. A screen joins the two; a machine
    that has settings but no rail section still appears here, which is the point, because its
    settings exist either way.

    NO ORDER BAKED IN. R3 (who decides rail order) is still open between OSDev1 and OSDev5 — the
    box's recipe or the morning report's — so this sorts by the `order` a step already carries,
    which is what the set-up screen has always used. Whatever R3 settles applies at the rail, not
    here, and nothing below has to change for it.
    """
    _ensure_registered()
    out: dict[str, dict] = {}
    for step in sorted(_STEPS.values(), key=lambda s: (s.order, s.key)):
        if not step.settings:
            continue                      # a machine with nothing to change gets no row. R4.
        row = out.setdefault(step.machine, {"machine": step.machine, "order": step.order,
                                            "steps": [], "count": 0})
        row["steps"].append(step.key)
        row["count"] += len(step.settings)
        # THE MACHINE'S PLACE IS ITS EARLIEST STEP'S. `_STEPS` is filled by machines importing at
        # boot, so the hazard this closes is IMPORT ORDER: which machine imports first is a fact
        # about a recipe file, not a decision anyone made, and a menu that depended on it would
        # differ between two boxes with the same machines. A machine that declares a lower number
        # does still move up — while R3 is open, position is a function of the numbers a machine
        # picks, which is precisely what R3 exists to take away.
        row["order"] = min(row["order"], step.order)
    return sorted(out.values(), key=lambda r: (r["order"], r["machine"]))


def settings_for(key: str) -> list[dict]:
    """The settings one step declared. Empty for a step that declared none, which is most of them."""
    _ensure_registered()
    step = _STEPS.get(str(key))
    return [dict(o) for o in (step.settings if step else ())]


def read_settings(key: str, *, user_id: str | None = None) -> dict:
    """{name: value in force} for this step, for this person. Never raises.

    THE DECLARED DEFAULT IS THE FLOOR, not `None`. A screen handed None for a toggle nobody has
    touched renders it off, and a machine reading it decides nothing is switched on — so a step's
    own default is what the store falls through to, and a setting works the day it is declared
    rather than the day somebody first opens the screen.
    """
    from core import box_settings
    step = _STEPS.get(str(key))
    if step is None:
        return {}
    out = {}
    for opt in step.settings:
        # A `box` setting ignores who is asking. Passing a user id for one would look up a row that
        # must never exist, and the day one did (a hand-written row, a bad migration) one person
        # would silently see a different answer than the box.
        scope_user = user_id if opt.get("scope") == "person" else None
        out[opt["name"]] = box_settings.get(step.machine, f"{key}.{opt['name']}",
                                            user_id=scope_user, default=opt.get("default"))
    return out


def write_settings(key: str, values: Mapping, *, user_id: str | None = None,
                   role: str = "member") -> None:
    """Store what a person changed. Raises StepRejected with a sentence they can read.

    THE ROLE GATE IS HERE AND NOT ON THE SCREEN, and that is the point. A screen that hides a
    control is a courtesy; the gate belongs where the write happens, because a form can be posted
    without ever rendering the page that would have hidden it.

    ONLY DECLARED SETTINGS, exactly as `save()` hands a machine only its declared fields. A name
    this step never declared is dropped rather than stored, so a crafted form cannot write a key
    into a machine's namespace and have it read back later as if the machine had put it there.

    AND ONLY AS FAR AS THE DECLARATION ALLOWS: `_coerce` turns each value back into its own type
    and refuses a `text` answer past the step's cap. `box_settings.put` trusts whatever it is
    handed — deliberately, it is a store — so this is the door where a form stops being a form.
    """
    from core import box_settings
    _ensure_registered()
    step = _STEPS.get(str(key))
    if step is None:
        raise StepRejected("That form is not one this screen knows.")
    declared = {o["name"]: o for o in step.settings}

    # CHECKED IN FULL BEFORE ANYTHING IS WRITTEN. A form carries several settings, and a rejection
    # part-way through a single loop would store the fields before the bad one and refuse the
    # rest — so the buyer reads "that did not work" over a box that half changed, and the only way
    # to find out which half is to reload the page. Two passes cost one dict on a form of five
    # fields; a save that is true of some of the form is not a state worth being fast about.
    ready = []
    for name, raw in dict(values or {}).items():
        opt = declared.get(name)
        if opt is None:
            continue                                 # not declared: not stored. See the docstring.
        if opt["who"] == "owner" and str(role or "").strip().lower() != "owner":
            raise StepRejected(f"Only the box's owner can change {opt['label']}.")
        ready.append((name, opt, _coerce(opt, raw)))

    for name, opt, value in ready:
        scope_user = user_id if opt.get("scope") == "person" else None
        box_settings.put(step.machine, f"{key}.{name}", value,
                         user_id=scope_user, set_by=user_id)


def _coerce(opt: Mapping, raw):
    """A form posts strings; the store keeps types. Raises StepRejected with the buyer's sentence.

    A SETTINGS SCREEN IS AN HTML FORM, so `False` arrives as the string "false" or as nothing at
    all, and a store that kept it verbatim would hand back a truthy string — a toggle that can
    never be switched off. Every type is turned back into itself here, once, rather than at each
    reader.
    """
    kind = opt.get("type")
    if kind == "toggle":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "on", "yes")
    if kind == "number":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            raise StepRejected(f"{opt['label']} needs to be a number.") from None
    if kind == "choice":
        choices = [str(c) for c in (opt.get("choices") or ())]
        got = str(raw)
        if got not in choices:
            # NEVER ECHO THE POSTED VALUE BACK INTO THE SENTENCE. It renders on a page, and a
            # value a stranger chose is not something to print at somebody.
            raise StepRejected(f"Choose one of the offered options for {opt['label']}.")
        return got
    # `text`, and the one branch where the buyer can hand us any length at all. The sentence names
    # the limit because "too long" without a number is a form he cannot get past; it still never
    # echoes what he typed, for the reason the choice branch above gives.
    text = str(raw)
    cap = int(opt.get("max_len") or _TEXT_MAX)
    if len(text) > cap:
        raise StepRejected(f"{opt['label']} is too long — {cap} characters at most.")
    return text


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
