"""core.person: "Who is this?", one person across every machine, for the owner's own AI (#1857 F1).

The box already keeps one record per person (core/people.py, owner-approved 2026-10-01): every way a machine knows
someone, and what happened to them, across every join. This is that record read back in words, with the last touches,
what to ask next, and what the box can start.

ACT ONLY, AND HIDDEN BY DEFAULT. Owner, 2026-10-03 (D7 on #1857): "Only connections allowed to take actions can look
up who a person is. Names stay hidden by default." So the capability is `read:people`, held by act and service seats
and never by read (core/connector/tools.py's role table), and the answer shows initials and partly hidden addresses
unless the caller asks with `show: true`. The record itself is unchanged and shown in full on the box's own screens:
core/people.py records the owner's 2026-10-01 words, "I would just show them. No need to mask." D7 is the later
ruling and is about what an AI connection reads; the two are flagged on the PR for the owner to confirm.

FOUND BY AN ID, NEVER GUESSED. An email, a number, a handle or a person id finds a person exactly (core/people.find,
each kind in turn); a name finds one only when exactly one person in the box has it. Names never join people.
"""
from __future__ import annotations

from core import people, state
from core.connector import tools, words
from core.logging import get_logger

log = get_logger(__name__)

CAPABILITY = "read:people"
TOUCHES = 20


def _spaces() -> list[str]:
    try:
        from core import spaces
        return [s["name"] for s in spaces.all_spaces() if isinstance(s, dict) and s.get("name")]
    except Exception:                                    # noqa: BLE001
        return []


def _find(who: str) -> str | None:
    """The person id `who` names exactly, in any Space of this box, or None."""
    w = str(who or "").strip()
    if not w:
        return None
    if w.startswith("p_") and people.person(w):
        return people.person(w)["id"]
    for sp in _spaces():
        for kind in people.KINDS:                       # email, then the rest; normalize refuses what isn't one
            pid = people.find(sp, kind, w)
            if pid:
                return pid
    with state.connect() as c:                          # a name: only when exactly one person has it
        rows = c.execute("SELECT id FROM people WHERE merged_into IS NULL AND lower(name) = lower(?) LIMIT 2",
                         (w,)).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p[:1].isalpha()]
    return " ".join(f"{p[0].upper()}." for p in parts[:3]) or "someone"


def _hide(kind: str, value: str) -> str:
    """An id with most of it hidden: d•••@example.com, •••0134, @d•••."""
    v = str(value or "")
    if "@" in v and kind == "email":
        local, _, host = v.partition("@")
        return f"{local[:1]}•••@{host}"
    if kind == "phone":
        return "•••" + v[-4:]
    return f"{v[:1]}•••" if len(v) > 2 else "•••"


def lookup(who=None, show=False, seat=None) -> dict:
    """One person: channels, first seen, last touch, the last 20 touches. Hidden by default (D7)."""
    pid = _find(str(who or ""))
    if not pid:
        return {"found": False, "who": str(who or "")[:80]}
    p = people.person(pid) or {}
    touches = people.timeline(pid, limit=TOUCHES)
    reveal = bool(show)
    ids = [{"kind": i["kind"], "value": i["value"] if reveal else _hide(i["kind"], i["value"]),
            "added_by": i.get("added_by")} for i in p.get("ids") or []]
    log.info("person.read", seat=(seat or {}).get("id"), shown=reveal)
    return {"found": True, "id": pid, "shown": reveal,
            "name": (p.get("name") or "") if reveal else _initials(p.get("name") or ""),
            "channels": sorted({i["kind"] for i in ids}), "ids": ids,
            "first_seen_at": p.get("first_seen_at"), "first_seen_by": p.get("first_seen_by"),
            "last_touch_at": touches[0]["at"] if touches else None,
            "touches": touches}


def _reply_tool() -> str | None:
    """The tool that drafts a reply for a person to wait on Approvals, whichever machine registers one."""
    return next((n for n in sorted(tools.registry()) if n.endswith(".propose_reply")), None)


# HOW AN ID IS NAMED TO THE OWNER. Every kind reads as itself, but a visit.
_LABEL = {"web": "website visit"}


def _render(r: dict) -> str:
    if not r.get("found"):
        return words.answer(
            f"I don't know anyone by {words.quoted(r.get('who') or '', 60)} on this box yet. I find a person by an "
            "email, a number, a handle, or a name only one person has.",
            words.ask_next(("core.brief", "What needs me today?"), ("core.ask", "Who wrote to me this week?")))
    who = r.get("name") or "This person"
    seen = (f"First seen {words.day_words(str(r.get('first_seen_at') or '')[:10])} by "
            f"{str(r.get('first_seen_by') or 'the box').replace('_', ' ')}") if r.get("first_seen_at") else ""
    last = f"last touch {words.ago(r['last_touch_at'])}" if r.get("last_touch_at") else "nothing has happened yet"
    ways = [f"{_LABEL.get(i['kind'], i['kind'])}: {i['value']}" for i in r.get("ids") or []]
    lines = [f"{words.clock(t['at'])}: {str(t['kind']).replace('_', ' ')} ({str(t['machine']).replace('_', ' ')})"
             + (f", {t['ref']}" if t.get("ref") else "") for t in r.get("touches") or []]
    hidden = ("" if r.get("shown") else
              "Names and addresses are partly hidden. Ask again with show to see them in full.")
    rt = _reply_tool()
    return words.answer(
        f"{who}: {seen}; {last}." if seen else f"{who}: {last}.",
        words.section("Ways the box knows them:", ways),
        words.section(f"The last {len(lines)} things that happened:" if lines else "", lines),
        hidden,
        words.ask_next(("core.ask", "What did they ask about?"), ("core.brief", "What needs me today?")),
        words.can_start((rt, words.approve_line("Draft a reply to them"))) if rt else "")


tools.register(
    "person", title="Look up one person your box knows",
    fn=lookup, wants_seat=True, machine="core", min_role="act", capability=CAPABILITY, render=_render,
    description="Who is this? One person across every machine on the box: the ways the box knows them, when it "
                "first met them, their last touch and the last 20 things that happened. Find them by an email, a "
                "number, a handle, a person id, or a name only one person has. Names and addresses are partly "
                "hidden unless show is true.",
    args={"who": {"type": "string", "required": True,
                  "description": "An email, a number, a handle, a person id, or a full name."},
          "show": {"type": "boolean", "required": False,
                   "description": "true to show names and addresses in full."}})
