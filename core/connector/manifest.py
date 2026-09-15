"""What THIS box can actually answer — built from the registry that dispatches the calls.

Built from the same dict `call()` reads, so it cannot drift from reality. A manifest assembled
from a separate list is a promise maintained by hand, and the first time somebody adds a tool
without updating it, an agent is told the box can do something it cannot.

ABSENCE IS A STARTUP FACT, NOT A 404 (docs/PLAN_AIOS_CONNECTOR.md section 4). A tool whose machine
could not be resolved in the WEB process is not in this document at all. That matters because
core/dispatch.py deliberately catches a pack import failure and logs a warning — right for its own
purpose, "A BROKEN PACK MUST NOT TAKE THE BOX DOWN", and exactly wrong as the foundation of a tool
registry, where the same swallow would advertise a tool that cannot run. So the registry only ever
holds what imported, and what did not import is listed separately under `absent` rather than
vanishing.

THE BOX TYPE IS DERIVED, NOT STAMPED. Nothing in the repo records "this is a lead box": the
exporter picks module lists per type and never writes the type down. So it is inferred from the
modules this box actually loads, which is the running truth — CLAUDE.md: when the config and the
spec disagree, the config wins. It is reported as `derived` so nobody mistakes it for a guarantee.
"""
import uuid
from datetime import datetime, timezone

from core import state
from core.connector import tools
from core.logging import get_logger

log = get_logger(__name__)

# Prefix -> the name a human uses for that box type. Order matters only for reporting.
_MACHINE_PREFIXES = {
    "marketing.lead_machine": "lead_machine",
    "marketing.content_machine": "content_machine",
    "marketing.customer_voice": "customer_voice",
    "marketing.ads_machine": "ads_machine",
}


def box_id() -> str:
    """This box's stable id. Generated once, then read forever.

    Never derived from a hostname, a domain or a path: all three change under a restore, and an
    identity that changes under a restore is not an identity. Section 11.6 sells replacing a
    failed box from its export in under an hour, and the replacement has to still BE that box to
    a caller holding its seats.
    """
    with state.connect() as c:
        row = c.execute("SELECT box_id FROM connector_box WHERE only_row = 1").fetchone()
        if row:
            return row["box_id"]
        new = "box_" + uuid.uuid4().hex[:16]
        # Another process may have won the race between the read and here; its id is as good as
        # ours and the row is the tie-breaker, so take whatever is there afterwards.
        c.execute("INSERT OR IGNORE INTO connector_box (only_row, box_id, created_at) "
                  "VALUES (1, ?, ?)", (new, datetime.now(timezone.utc).isoformat()))
        row = c.execute("SELECT box_id FROM connector_box WHERE only_row = 1").fetchone()
    return row["box_id"]


def machines() -> list:
    """The machine packages this box loads, per its own config."""
    try:
        from core.config import get_config
        mods = get_config().get("modules") or []
    except Exception as e:                                  # noqa: BLE001
        log.warning("connector.modules_unreadable", error=type(e).__name__)
        return []
    found = []
    for m in mods:
        for prefix, label in _MACHINE_PREFIXES.items():
            if str(m).startswith(prefix) and label not in found:
                found.append(label)
    return found


def box_type() -> str:
    """lead | content | customer_voice | aios | unknown — DERIVED, and labelled as such."""
    found = set(machines())
    if not found:
        return "unknown"
    if len(found) == 1:
        only = next(iter(found))
        return {"lead_machine": "lead", "content_machine": "content",
                "customer_voice": "customer_voice"}.get(only, only)
    return "aios"


def build(*, seat: dict) -> dict:
    """The manifest. Describes capability, never data — but only the capability THIS seat holds.

    FILTERED, AND THIS IS NOT A REFINEMENT. `tools/list` filters by capability, and if the
    manifest did not, that filter would be one call away from bypassed: a seat that cannot see a
    tool in the list would find it here, named, with its arguments. One surface hiding a tool and
    another advertising it is not a smaller hole than no filtering — it is the same hole wearing
    a better first impression.
    """
    reg = {s["name"]: s for s in tools.visible_to(seat)}
    return {
        "box_id": box_id(),
        "box_type": box_type(),
        "box_type_source": "derived from loaded modules; this box stores no box type",
        "contract_version": tools.CONTRACT_VERSION,
        "machines": machines(),
        "tools": [
            {
                "name": s["name"],
                "description": s["description"],
                "machine": s["machine"],
                "min_role": s["min_role"],
                "capability": s["capability"],
                "args": s["args"],
            }
            for s in sorted(reg.values(), key=lambda s: s["name"])
        ],
        # Named, not hidden. A machine that failed to load is invisible to the tool list BY
        # DESIGN, and invisible-by-design reads identically to silently-broken unless it is
        # written down somewhere an operator will look.
        "absent": tools.absent(),
    }


# THE MANIFEST IS ITSELF A TOOL, not only a route, and that is a requirement rather than a
# flourish: the MCP transport (step 9) supports TOOL CALLS ONLY, so a manifest exposed purely as
# a resource is invisible to the agent that most needs it — it would have to guess what the box
# can do, which is the failure this whole design exists to prevent. Registered here, in its own
# module, so importing the package is all it takes.
#
# min_role="read": it describes CAPABILITY, never data. The lowest seat on the box may ask what
# the box can do; that is how a client discovers it has nothing it is allowed to call.
TOOL = "aios.core.manifest"   # the computed public name; callers use this, never a literal

tools.register(
    "manifest",
    fn=build,
    description="What this box is and every tool it can serve, with argument schemas and the "
                "minimum role each needs.",
    machine="core",
    min_role="read",
    # Its own capability, not read:reports. A seat may be allowed to ask what the box can do
    # while being allowed to call none of it — that is how a client discovers it has nothing.
    capability="read:manifest",
    # The one tool that must know who is asking, because its whole answer is "what may you call".
    wants_seat=True,
)
