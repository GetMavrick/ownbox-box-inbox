"""The tool registry — one definition, and every transport reads it.

THE ONE DECISION EVERYTHING ELSE FOLLOWS FROM (docs/PLAN_AIOS_CONNECTOR.md section 4): a caller
never posts free text. It invokes a NAMED function with TYPED arguments, and the function fixes
the intent. The alternative is not hypothetical — prose posted to /dispatch lands in the keyword
router at core/slack_socket.py:50-64, whose own comments record commands that "fell through to
brain in a DM, which is WORSE than a dead command", returning a confident confirmation while
nothing changed. Inside one team that is a bug somebody notices. Across a team boundary it is
invisible from both sides.

The payoff is that THE FUNCTION LIST IS THE PERMISSION LIST. Widening access later is adding
rows, not re-reasoning about what a sentence might have meant.

QUESTIONS, NOT MODULES (section 11.5). A machine REGISTERS the questions it can answer; the
connector serves whatever registered. There is no tuple of machine names in this file to keep in
sync, so a box answers for whatever it runs — including machines written after the plan, and
including a buyer's own — with no edit to core.

THREE STATES, AND THE MIDDLE ONE IS THE WHOLE POINT (section 3.1):

    absent          the machine is not on this box. The tool is not in the manifest AT ALL, and
                    absence is a STARTUP fact: a module that failed to import never registered,
                    so there is nothing to 404 on later.
    not_configured  the machine is here and has no credentials. This is the COMMON state, because
                    the house convention is that a module ships inert until its keys are set. It
                    is returned as a TYPED state, never as an empty result.
    a result        the real answer.

Without the middle state a buyer holds a box where the machine is installed, the tool is
advertised, and there is no data behind it — and an agent answers `0`, and sounds certain. Silence
is worse than an error.

NOTHING HERE REASONS. No brain.think, no vendor call, no spend. Registry, validation and dispatch
are deterministic work, which CLAUDE.md section 11-6 says must never route through a model.
"""
import re

from core.logging import get_logger

log = get_logger(__name__)

# Bumped when the SHAPE of the manifest or the call envelope changes in a way a client must
# notice. Not a version of the tool list — tools come and go per box, and that is what the
# manifest is for.
CONTRACT_VERSION = "1"

# read < act; service is act with a higher ceiling, not more permission. Kept coarse on purpose
# (section 4): the consuming agent already carries per-tool policy, and permissions configured
# twice in two systems with no single view is how a customer comes to believe something is off
# when it is on.
ROLE_RANK = {"read": 1, "act": 2, "service": 2}

_ARG_TYPES = {"string": str, "integer": int, "boolean": bool}

# A CAPABILITY IS REQUIRED ON EVERY TOOL, and it is deliberately NOT the role.
#
# `min_role` is a coarse rank (read < act) and it answers "how much power". A capability answers
# "power over WHAT", and those are different questions the moment one read tool is more sensitive
# than another: a Morning Review and a customer's personal data are both reads, and a seat that
# may see the first must not automatically see the second. Roles cannot express that; a set can.
#
# Required rather than defaulted, because a default is the one a hurried tool gets, and the tool
# most likely to be written in a hurry is the one added to answer an urgent question about people.
# Refusing at import is cheap; discovering it on a customer's box is not.
_CAPABILITY = re.compile(r"^(read|write):[a-z][a-z0-9_]{2,39}$")

# WHAT EACH ROLE MAY SEE, and the point of this table is what is ABSENT from it.
#
# Every box we sell is a golden image cloned per customer, so the shipped default IS the product:
# there is no configuration step between the image and a customer's live box where a mistake gets
# caught. A capability that is granted by default is granted on every droplet forever.
#
# `read:leads_pii` is therefore DEFINED and granted to NOBODY. It has no tools behind it yet, and
# that is deliberate — the filtering is proven here, on a rail where a mistake costs nothing,
# rather than on the day somebody registers the first tool that returns a person's contact
# details. Opening it is B3's per-seat grant and a deliberate act on the customer's own box.
_ROLE_CAPABILITIES = {
    "read":    frozenset({"read:manifest", "read:reports"}),
    "act":     frozenset({"read:manifest", "read:reports", "write:proposals"}),
    "service": frozenset({"read:manifest", "read:reports", "write:proposals"}),
}


def visible_to(seat: dict) -> list:
    """The tools this seat may actually call, sorted. What `tools/list` is allowed to show.

    A SEAT NEVER SEES A TOOL IT CANNOT CALL. Showing it and refusing the call is worse than
    hiding it: a model that can see a tool will try it, then explain the refusal to a customer as
    if the box were broken.
    """
    held = _ROLE_CAPABILITIES.get(seat.get("role"), frozenset())
    return [s for s in sorted(_REGISTRY.values(), key=lambda s: s["name"])
            if s["capability"] in held]


# MCP's unspecified annotation defaults are hostile to a product like ours: `destructiveHint` and
# `openWorldHint` both default to TRUE. Unannotated, a read-only morning report looks destructive
# to every client and earns a confirmation prompt on the most common call we serve.
#
# DERIVED FROM THE CAPABILITY, never hand-written per tool. Hand-written means one tool gets it
# wrong and nothing catches it; derived means the verb decides, in one place. Our writes are
# proposals a human approves, so they are additive rather than destructive either way.
def annotations_for(capability: str) -> dict:
    reading = capability.startswith("read:")
    return {"readOnlyHint": reading,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False}

_REGISTRY: dict = {}
# What did NOT register, and why. A machine that failed to import is invisible by design — the
# kernel deliberately swallows a pack import failure so a broken pack cannot take the box down —
# and "invisible by design" is indistinguishable from "silently broken" unless somebody writes it
# down. This is where an operator looks.
_ABSENT: list = []


class NotConfigured:
    """The machine is here; its credentials are not. Returned, never raised.

    Raised, it would be caught by whatever catches errors and reported as a failure, which is a
    different and wronger thing to tell a buyer than "you have not connected this yet."
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self):                                    # pragma: no cover - debugging aid
        return f"NotConfigured({self.reason!r})"


class ToolError(Exception):
    """A refusal the caller should see, with the HTTP status it maps to."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def register(name: str, *, fn, description: str, machine: str, capability: str,
             min_role: str = "read", args: dict | None = None,
             wants_seat: bool = False, output: dict | None = None) -> None:
    """Declare one question this box can answer.

    `wants_seat=True` hands the resolved seat to the function as a keyword. Opt-in, because most
    tools must not know who is asking — a tool that varies its ANSWER by caller is a tool nobody
    can reason about. It exists for one job the plan names explicitly: WITHHOLDING A FIELD by
    role, where the money rail is kept from a `read` seat. The seat is never part of `args`, so
    it can never be supplied by the caller.

    `args` is {name: {"type": "string"|"integer"|"boolean", "required": bool, "description": str}}.
    Deliberately small: a schema language would be a dependency and an argument about which
    dialect, and every Tier 1 question so far takes a date or an id.
    """
    # THE PUBLIC NAME IS COMPUTED, never hand-written. A client aggregating several MCP servers
    # collides on bare names — two servers each offering `search` — and the spec says the server
    # name is not unique enough to disambiguate with, so the prefix has to be in the tool name
    # itself. Computed here so a call site cannot spell it differently from its neighbour, and so
    # the machine in the name is always the machine that owns the tool.
    #
    # NO ALIAS TO THE OLD BARE NAME. We have zero customers and every box is a golden image built
    # fresh, so there is nothing in the field to migrate; an alias would be dead weight cloned
    # onto every droplet we ever ship. (Owner, 2026-09-13.)
    name = f"aios.{machine}.{name}"
    if name in _REGISTRY:
        # Two machines claiming one name is a silent overwrite in a dict, and the loser's tool
        # disappears with no signal anywhere. Refuse at import, where somebody is watching.
        raise ValueError(f"tool {name!r} is already registered by {_REGISTRY[name]['machine']!r}")
    if min_role not in ROLE_RANK:
        raise ValueError(f"min_role must be one of {sorted(ROLE_RANK)}, got {min_role!r}")
    # KEYWORD-ONLY AND NO DEFAULT: a tool that forgets it fails at import with the name in the
    # TypeError, which is the moment somebody is watching. The shape check is here so the
    # vocabulary cannot drift into free text one tool at a time.
    if not isinstance(capability, str) or not _CAPABILITY.match(capability):
        raise ValueError(f"tool {name!r}: capability must look like 'read:reports' or "
                         f"'write:replies', got {capability!r}")
    if not callable(fn):
        raise ValueError(f"tool {name!r} needs a callable")
    for arg, spec in (args or {}).items():
        if spec.get("type") not in _ARG_TYPES:
            raise ValueError(f"tool {name!r} arg {arg!r}: type must be one of {sorted(_ARG_TYPES)}")
    if "seat" in (args or {}):
        # The seat is identity, resolved from a verified credential. A caller that could pass it
        # as an argument could claim to be anyone, which is the whole game.
        raise ValueError(f"tool {name!r}: 'seat' is not an argument a caller may supply")
    _REGISTRY[name] = {"name": name, "fn": fn, "description": description,
                       "machine": machine, "min_role": min_role, "args": args or {},
                       "wants_seat": wants_seat, "capability": capability,
                       # OPTIONAL, AND OMITTED IS THE HONEST ANSWER FOR A BIG NESTED RESULT.
                       # Where an outputSchema is present a client MUST validate against it, so a
                       # schema that drifts from the code breaks calls that would otherwise work.
                       # Declared where the shape is small and stable; absent where it is not.
                       "output": output}
    log.info("connector.tool_registered", tool=name, machine=machine, min_role=min_role,
             capability=capability)


def note_absent(machine: str, reason: str) -> None:
    """Record a machine that could not be loaded here, so absence is legible rather than silent."""
    _ABSENT.append({"machine": machine, "reason": reason})
    log.warning("connector.machine_absent", machine=machine, reason=reason)


def registry() -> dict:
    return dict(_REGISTRY)


def absent() -> list:
    return list(_ABSENT)


def _reset_for_tests() -> None:
    _REGISTRY.clear()
    _ABSENT.clear()


def may(seat_role: str, min_role: str) -> bool:
    return ROLE_RANK.get(seat_role, 0) >= ROLE_RANK.get(min_role, 99)


def validate(spec: dict, raw: dict | None) -> dict:
    """Return the accepted arguments, or raise ToolError. Unknown keys are REFUSED, not ignored.

    Ignoring an unknown key is how a caller silently gets a different query than it asked for: it
    sends `limit`, the tool never heard of `limit`, and it receives a full table believing it
    asked for ten rows. Refusing is the only answer that cannot mislead.
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ToolError("bad_args", "arguments must be an object")
    declared = spec["args"]
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        raise ToolError("unknown_args", f"this tool has no argument(s): {', '.join(unknown)}")
    out = {}
    for arg, decl in declared.items():
        if arg not in raw:
            if decl.get("required"):
                raise ToolError("missing_arg", f"{arg} is required")
            continue
        want = _ARG_TYPES[decl["type"]]
        val = raw[arg]
        # bool is an int in Python, and an agent sending true for a count is a bug worth naming
        # rather than coercing into 1.
        if want is int and isinstance(val, bool):
            raise ToolError("bad_arg", f"{arg} must be an integer, got a boolean")
        if not isinstance(val, want):
            raise ToolError("bad_arg", f"{arg} must be {decl['type']}")
        out[arg] = val
    return out


def call(name: str, raw_args: dict | None, seat: dict) -> tuple[dict, int]:
    """Invoke a tool for a seat. Returns (body, http_status). ALWAYS writes one audit row.

    Every exit writes to seat_actions — allowed, refused and failed alike. A ledger that records
    only successes cannot show a credential being probed, which is the row an incident is
    reconstructed from.
    """
    from core.connector import seats as _seats

    spec = _REGISTRY.get(name)
    if spec is None:
        # A tool that is absent is absent at STARTUP; by here the honest answer is that this box
        # does not serve it, which is a different sentence from "that failed".
        _seats.record(seat["id"], name, outcome="unknown")
        return {"error": "unknown_tool",
                "message": f"this box does not serve {name!r}; call manifest for what it does",
                }, 404

    # THE CAPABILITY IS THE GATE, AND IT IS CHECKED HERE RATHER THAN ONLY AT tools/list.
    #
    # Filtering what a seat SEES is not authorisation, it is decoration. Caught in review of
    # #1127 by OSDev1 and then reproduced: a `read` seat could not see a `read:leads_pii` tool in
    # tools/list or in the manifest, and calling it by name returned 200 WITH THE ROWS. Hiding a
    # door is not locking it, and a tool name is a guess away.
    #
    # BOTH CHECKS RUN, and they answer different questions: the capability is power over WHAT,
    # `min_role` is how MUCH. A tool may be `read:reports` and still need `act`, so neither
    # subsumes the other and a seat must satisfy both.
    held = _ROLE_CAPABILITIES.get(seat.get("role"), frozenset())
    if spec["capability"] not in held:
        _seats.record(seat["id"], name, outcome="denied")
        return {"error": "forbidden",
                "message": f"{name} needs the {spec['capability']} capability; "
                           f"this seat does not hold it",
                }, 403

    if not may(seat["role"], spec["min_role"]):
        _seats.record(seat["id"], name, outcome="denied")
        return {"error": "forbidden",
                "message": f"{name} needs role {spec['min_role']}; this seat is {seat['role']}",
                }, 403

    try:
        args = validate(spec, raw_args)
    except ToolError as e:
        _seats.record(seat["id"], name, outcome="denied")
        return {"error": e.code, "message": e.message}, e.status

    try:
        result = spec["fn"](**args, seat=seat) if spec.get("wants_seat") else spec["fn"](**args)
    except Exception as e:                                  # noqa: BLE001
        # CATCH THE CLASS, not a list of our own exception types. core/brain.py has two backends
        # that raise differently, and on 2026-09-08 a handler catching only our types let an
        # exported box's SDK auth error escape and 500 a page. Same shape, same rule here.
        _seats.record(seat["id"], name, outcome="error", args=args)
        log.error("connector.tool_failed", tool=name, seat=seat["id"],
                  error=f"{type(e).__name__}: {e}")
        # The message is deliberately not the exception text: it can carry a path, a query, or a
        # key, and this body goes to whoever holds a read seat.
        return {"error": "tool_failed", "message": f"{name} could not answer"}, 500

    if isinstance(result, NotConfigured):
        _seats.record(seat["id"], name, outcome="ok", args=args)
        return {"tool": name, "not_configured": True, "reason": result.reason,
                "message": f"{spec['machine']} is on this box but is not connected yet"}, 200

    _seats.record(seat["id"], name, outcome="ok", args=args)
    return {"tool": name, "result": result}, 200
