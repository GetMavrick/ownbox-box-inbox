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
_CAPABILITY = re.compile(r"^(read|write|act):[a-z][a-z0-9_]{2,39}$")

# THREE VERBS, AND THE THIRD IS HELD BY NO SEAT (docs/SCOPE_SHIFTS.md §4.2).
#
#   read:   sees
#   write:  leaves something for a human to approve (a proposal, a draft)
#   act:    does it: sends, publishes, charges
#
# `act:` exists so a machine can register the tool a coworker's STEP calls, the box calling it
# itself before or after the AI, with the owner's grant. No role below holds an `act:`
# capability and `ai_may_hold()` refuses every one, so no seat, whether a person's assistant or a
# coworker's run, can call one. That is the whole safety argument for combining machines from
# different authors: whatever a web page talks the AI into, the most it can do is draft.

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
# WHAT EACH ROLE MAY REACH. A capability nobody grants is a capability nobody can use: tools
# carrying it register fine and `visible_to` hides them from every seat, so the feature is DARK
# and its registration test still passes. That happened — `read:inbox` shipped on three tools in
# #1205 with no role holding it, and `tools/list` kept answering four (OSDev1, 2026-09-15).
# Adding a capability here is therefore part of adding one anywhere.
#
# WHY `read:inbox` IS GRANTED AT ALL, and to these two. Every seat on a box is minted by that
# box's owner, with `scripts/seat.py`, for their own assistant — there is no seat a stranger
# holds, and the inbox is the thing the box was bought to read. A `read` seat that cannot read
# the inbox is the product's headline feature withheld from the only person who could have
# created the credential.
#
# `read:leads_pii` STAYS UNGRANTED, and the contrast is the point: it is not an oversight that
# some capability has no holder, it is how a capability is kept for a decision nobody has made
# yet. This list is where that decision gets made, visibly.
# read:spend is held by act and service and NOT by read. That is not a rank judgement — a read
# seat holds four other reads — it is the one field this system has already decided is the owner's
# alone: core/report_tools.py withholds the meters segment from a read seat, by name and loudly.
# Granting read:spend to `read` would hand the same number back through a different door.
# `read:apps` (docs/SCOPE_CONNECTIONS_MCP_FIRST.md, owner-approved 2026-10-01): the READ tools of the apps the
# owner connected on Data sources, served through this box as their one gateway. Granted to every role,
# because every seat on a box is minted by its owner for their own assistant, and which tools are on at all
# is the owner's choice per connection (core/connections/store.py). Actions are not this capability: they
# come later, each behind a person's yes.
# `read:people` ("Who is this?", core/person_tool.py) is held by act and service and NOT by read: owner, 2026-10-03,
# D7 on #1857, "Only connections allowed to take actions can look up who a person is. Names stay hidden by default."
_ROLE_CAPABILITIES = {
    "read":    frozenset({"read:manifest", "read:reports", "read:inbox", "read:health", "read:apps"}),
    "act":     frozenset({"read:manifest", "read:reports", "read:inbox", "read:health",
                          "read:spend", "write:proposals", "read:apps", "read:people"}),
    "service": frozenset({"read:manifest", "read:reports", "read:inbox", "read:health",
                          "read:spend", "write:proposals", "read:apps", "read:people"}),
}


def visible_to(seat: dict) -> list:
    """The tools this seat may actually call, sorted. What `tools/list` is allowed to show.

    A SEAT NEVER SEES A TOOL IT CANNOT CALL. Showing it and refusing the call is worse than
    hiding it: a model that can see a tool will try it, then explain the refusal to a customer as
    if the box were broken.
    """
    mine = held(seat)
    return [s for s in sorted(_REGISTRY.values(), key=lambda s: s["name"])
            if s["capability"] in mine]


def ai_may_hold(capability: str) -> bool:
    """May an AI's seat ever hold this? Any `read:`, and `write:proposals`. Never `act:`.

    `write:proposals` rather than `write:*`, because a write is only safe to hand an AI when a
    human approves what it wrote, and proposals are the one write this box has built that way.
    A machine that adds another approved-before-it-lands write widens this on purpose, in review.
    """
    return (isinstance(capability, str) and bool(_CAPABILITY.match(capability))
            and (capability.startswith("read:") or capability == "write:proposals"))


def held(seat: dict) -> frozenset:
    """The capabilities this seat holds. A RUN SEAT's are its own list; anyone else's, its role's.

    A run seat is minted for one coworker's shift and holds exactly what that coworker was
    granted (docs/SCOPE_SHIFTS.md §4.1), not a role's worth: a coworker allowed to read the inbox
    must not also see what the box is spending because its role happens to. The list is filtered
    through `ai_may_hold()` HERE as well as at mint, so a row written by anything else still
    cannot carry an `act:` capability into a call.
    """
    explicit = seat.get("capabilities")
    if explicit is not None:
        return frozenset(c for c in explicit if ai_may_hold(c))
    role = _ROLE_CAPABILITIES.get(seat.get("role"))
    if role is None:
        return frozenset()
    return role | granted()


# A MACHINE'S OWN READ CAPABILITY, GRANTED TO EVERY ROLE BY ONE LINE IN THE MACHINE. Owner,
# 2026-10-02: a person must be in full command of every machine from their own AI, and the first
# thing that takes is seeing what the machine sees. A machine registers its read tools under
# `read:<slug>` (read:aeo, read:website) and calls `grant()` beside them; core never names a
# machine (test_core_boundary), so the table above cannot. EXPLICIT, NEVER AUTOMATIC: a capability
# nobody granted stays invisible to every seat (test_connector_mcp: read:leads_pii), because the
# tool most likely to be written in a hurry is the one about people, and the grant line is what a
# reviewer sees. What the table already decides stays decided: read:spend is withheld from read
# seats on purpose, and a grant of it is refused.
_TABLED = frozenset().union(*_ROLE_CAPABILITIES.values())
_GRANTED: set = set()


def grant(capability: str) -> None:
    """Hold `capability` (a machine's own `read:<slug>`) in every role. Refused for anything the role
    table already decides, and for anything but a read."""
    if not (isinstance(capability, str) and _CAPABILITY.match(capability) and capability.startswith("read:")):
        raise ValueError(f"grant: only a machine's own read capability can be granted, got {capability!r}")
    if capability in _TABLED:
        raise ValueError(f"grant: {capability} is decided by the role table, not by a machine")
    _GRANTED.add(capability)
    log.info("connector.capability_granted", capability=capability)


def granted() -> frozenset:
    return frozenset(_GRANTED)


def machine_reads() -> frozenset:
    """Every registered `read:` capability the role table does not name."""
    return frozenset(s["capability"] for s in _REGISTRY.values()
                     if s["capability"].startswith("read:") and s["capability"] not in _TABLED)


# MCP's unspecified annotation defaults are hostile to a product like ours: `destructiveHint` and
# `openWorldHint` both default to TRUE. Unannotated, a read-only morning report looks destructive
# to every client and earns a confirmation prompt on the most common call we serve.
#
# DERIVED FROM THE CAPABILITY, never hand-written per tool. Hand-written means one tool gets it
# wrong and nothing catches it; derived means the verb decides, in one place. Our writes are
# proposals a human approves, so they are additive rather than destructive either way.
def annotations_for(capability: str) -> dict:
    reading = capability.startswith("read:")
    # An `act:` tool reaches the world (a sent email cannot be unsent), so it says so. No seat
    # sees one today; the hints are for the day an owner's trusted coworker is allowed to.
    acting = capability.startswith("act:")
    return {"readOnlyHint": reading,
            "destructiveHint": False,
            "idempotentHint": not acting,
            "openWorldHint": acting}

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


# A TITLE IS WHAT THE OWNER IS GRANTING, IN HIS WORDS (owner, 2026-10-01, on Claude's connector screen:
# "human readable permissions that a human understands what they are granting. I don't think all of
# them should start with AIOS."). Plain words: no dots, no underscores, never "AIOS".
_TITLE = re.compile(r"^[A-Z][A-Za-z0-9 ,'’()-]{2,59}$")


def plain_title(title: str) -> bool:
    """Is `title` something a person reads as plain words? (Also used by the suite.)"""
    return bool(_TITLE.match(title or "")) and "aios" not in title.lower()


def make_spec(name: str, *, fn, description: str, machine: str, capability: str,
              min_role: str = "read", args: dict | None = None,
              wants_seat: bool = False, output: dict | None = None, title: str | None = None,
              input_schema: dict | None = None, replacing=frozenset(), render=None) -> dict:
    """Declare one question this box can answer: every rule `register` keeps, returned as the spec and not
    yet registered. `replacing` names machines whose tools `swap` is about to replace, so their names and
    titles don't count as taken.

    `wants_seat=True` hands the resolved seat to the function as a keyword. Opt-in, because most
    tools must not know who is asking — a tool that varies its ANSWER by caller is a tool nobody
    can reason about. It exists for one job the plan names explicitly: WITHHOLDING A FIELD by
    role, where the money rail is kept from a `read` seat. The seat is never part of `args`, so
    it can never be supplied by the caller.

    `title` is what a person reads on their AI's permission screen: what they are granting, in plain
    words ("Search your inbox"). Every tool Ownbox ships passes one. A custom machine that does not
    gets one made from its name ("Latest notes"), so no machine built on SDK v1 stops loading.

    `args` is {name: {"type": "string"|"integer"|"boolean", "required": bool, "description": str}}.
    Deliberately small: a schema language would be a dependency and an argument about which
    dialect, and every Tier 1 question so far takes a date or an id.

    `render(result) -> str` writes the answer a person reads (core/connector/words.py). Owner, 2026-10-02,
    handed his own Morning Review as a block of stored fields: *"This is not an AI business machine. This is a
    dumb box."* The MCP transport sends this text, and the result travels beside it untouched. Optional, so a
    machine of the owner's own (SDK v1) and a connected app's tools keep answering as they did. It never leaves
    the box: the manifest, `tools/list` and the HTTP API each build their own fields and none carries it.
    """
    # THE PUBLIC NAME IS COMPUTED, never hand-written: `<machine>.<name>`, so a call site cannot spell
    # it differently from its neighbour and the machine in the name is always the one that owns it.
    # The machine stays in the name because two machines may both offer a `search`.
    #
    # NO `aios.` PREFIX (owner, 2026-10-01, assigned by OSDev1). It was there for clients that merge
    # several servers' tools, but a client already keeps each server's tools apart (Claude shows
    # them under the connector's own name), and the prefix was the "AIOS" the owner read on every
    # permission. An old name is still ANSWERED in call() (OLD_PREFIX), never LISTED: a client that cached
    # the old list kept getting "does not serve" on every call, which is more than the per-tool reset the
    # owner accepted.
    name = f"{machine}.{name}"
    if title is None:
        title = name.split(".", 1)[1].replace("_", " ").capitalize()
        given = False
    else:
        given = True
    if not plain_title(title):
        raise ValueError(f"tool {name!r}: title must be plain words a person reads, like "
                         f"'Search your inbox' (no dots, underscores or AIOS), got {title!r}")
    if any(t["title"] == title and t["name"] != name and t["machine"] not in replacing
           for t in list(_REGISTRY.values())):
        raise ValueError(f"tool {name!r}: title {title!r} is already used; two permissions must not "
                         f"read the same")
    if name in _REGISTRY and _REGISTRY[name]["machine"] not in replacing:
        # Two machines claiming one name is a silent overwrite in a dict, and the loser's tool
        # disappears with no signal anywhere. Refuse at import, where somebody is watching.
        raise ValueError(f"tool {name!r} is already registered by {_REGISTRY[name]['machine']!r}")
    if min_role not in ROLE_RANK:
        raise ValueError(f"min_role must be one of {sorted(ROLE_RANK)}, got {min_role!r}")
    # KEYWORD-ONLY AND NO DEFAULT: a tool that forgets it fails at import with the name in the
    # TypeError, which is the moment somebody is watching. The shape check is here so the
    # vocabulary cannot drift into free text one tool at a time.
    if not isinstance(capability, str) or not _CAPABILITY.match(capability):
        raise ValueError(f"tool {name!r}: capability must look like 'read:reports', "
                         f"'write:proposals' or 'act:send_email', got {capability!r}")
    if not callable(fn):
        raise ValueError(f"tool {name!r} needs a callable")
    if render is not None and not callable(render):
        raise ValueError(f"tool {name!r}: render must be a function that takes the result and returns words")
    for arg, spec in (args or {}).items():
        if spec.get("type") not in _ARG_TYPES:
            raise ValueError(f"tool {name!r} arg {arg!r}: type must be one of {sorted(_ARG_TYPES)}")
    if "seat" in (args or {}):
        # The seat is identity, resolved from a verified credential. A caller that could pass it
        # as an argument could claim to be anyone, which is the whole game.
        raise ValueError(f"tool {name!r}: 'seat' is not an argument a caller may supply")
    return {"name": name, "title": title, "title_given": given, "fn": fn,
                       "description": description,
                       "machine": machine, "min_role": min_role, "args": args or {},
                       "wants_seat": wants_seat, "capability": capability,
                       # OPTIONAL, AND OMITTED IS THE HONEST ANSWER FOR A BIG NESTED RESULT.
                       # Where an outputSchema is present a client MUST validate against it, so a
                       # schema that drifts from the code breaks calls that would otherwise work.
                       # Declared where the shape is small and stable; absent where it is not.
                       "output": output,
                       # AN APP'S OWN SCHEMA, passed through untouched (core/connections/gateway.py). Our
                       # small `args` vocabulary can't describe another app's tools, and translating it
                       # would be guessing; the app validates its own arguments.
                       "input_schema": input_schema if isinstance(input_schema, dict) else None,
                       # THE ANSWER IN WORDS (see the docstring). A function, like `fn`: kept here for the
                       # transport, never serialised.
                       "render": render}


def register(name: str, *, fn, description: str, machine: str, capability: str,
             min_role: str = "read", args: dict | None = None,
             wants_seat: bool = False, output: dict | None = None, title: str | None = None,
             input_schema: dict | None = None, render=None) -> None:
    """Declare one question this box can answer (the rules are `make_spec`'s). A name or title already taken
    is refused here, at import, where somebody is watching."""
    spec = make_spec(name, fn=fn, description=description, machine=machine, capability=capability,
                     min_role=min_role, args=args, wants_seat=wants_seat, output=output, title=title,
                     input_schema=input_schema, render=render)
    _REGISTRY[spec["name"]] = spec
    log.info("connector.tool_registered", tool=spec["name"], title=spec["title"], machine=machine,
             min_role=min_role, capability=capability)


def swap(machines, specs: list) -> None:
    """Replace every tool of `machines` with `specs` (made with `make_spec(..., replacing=machines)`), with no
    moment where a tool that stays is missing: new and changed tools land first, then the ones that went are
    removed. For connected apps (core/connections/gateway.py), whose tools change while the box runs; a call
    landing mid-swap finds either the old tool or the new one, never neither (OSDev4, review of #1751)."""
    machines, keep = set(machines), {s["name"] for s in specs}
    for spec in specs:
        _REGISTRY[spec["name"]] = spec
    for gone in [n for n, s in list(_REGISTRY.items()) if s["machine"] in machines and n not in keep]:
        _REGISTRY.pop(gone, None)


def unregister_machine(machine: str) -> int:
    """Remove every tool one machine registered. For connected apps only (core/connections/gateway.py), whose
    tools come and go while the box runs; a shipped machine's tools are registered once, at import."""
    gone = [n for n, s in _REGISTRY.items() if s["machine"] == machine]
    for n in gone:
        _REGISTRY.pop(n, None)
    return len(gone)


def titles(excluding=()) -> set:
    """Every title in use, except those of the machines in `excluding` (about to be replaced): a new tool must
    not read the same as one already here."""
    skip = set(excluding)
    return {s["title"] for s in list(_REGISTRY.values()) if s["machine"] not in skip}


def _json_dumps(v) -> str:
    import json as _j
    try:
        return _j.dumps(v)
    except (TypeError, ValueError):
        return "x" * 20_000                  # unserialisable: refused by the size rule, never raised


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
    if spec.get("input_schema") is not None:
        # A CONNECTED APP'S TOOL: its own server validates its own arguments. Here only the shape and the
        # size: an object, small enough to forward.
        if len(_json_dumps(raw)) > 16_000:
            raise ToolError("bad_args", "arguments are too large to send")
        return raw
    declared = spec["args"]
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        raise ToolError("unknown_args", f"this tool has no argument(s): {', '.join(unknown)}")
    out = {}
    for arg, decl in declared.items():
        # NULL IS ABSENT. Many clients send `"limit": null` for an optional argument they don't set; that
        # was refused as "limit must be integer". A null required argument is still missing.
        if arg not in raw or raw[arg] is None:
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


OLD_PREFIX = "aios."      # the public names before #1743; still answered, never listed (see call)


def lookup(name: str) -> dict | None:
    """The spec `call()` runs for `name`, by its old spelling too, or None. The MCP transport reads the tool's
    `render` through this, so an old name gets the same answer in words as the new one."""
    spec = _REGISTRY.get(name)
    if spec is None and isinstance(name, str) and name.startswith(OLD_PREFIX):
        # AN OLD NAME STILL ANSWERS (2026-10-02). #1743 dropped the `aios.` prefix from every public name, and a
        # client that listed the tools before then keeps calling `aios.<machine>.<tool>`. Measured that day: OSDev1's own
        # connector to the owner's box answered EVERY call with "this box does not serve", the evening before he
        # films his investor demo. So the old name reaches the same tool, through the SAME gates in call() (capability,
        # role, arguments); tools/list and the manifest still show only the new names. The audit row keeps the
        # name the client sent, so seat_actions shows when old names stop arriving (keep this until 2027-10,
        # the 12-month rule for contracts partners and owners build on).
        spec = _REGISTRY.get(name[len(OLD_PREFIX):])
    return spec


def call(name: str, raw_args: dict | None, seat: dict) -> tuple[dict, int]:
    """Invoke a tool for a seat. Returns (body, http_status). ALWAYS writes one audit row.

    Every exit writes to seat_actions — allowed, refused and failed alike. A ledger that records
    only successes cannot show a credential being probed, which is the row an incident is
    reconstructed from.
    """
    from core.connector import seats as _seats

    spec = lookup(name)
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
    if spec["capability"] not in held(seat):
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
