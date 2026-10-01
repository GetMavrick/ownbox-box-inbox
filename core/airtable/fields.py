"""Match a business's own Airtable fields to what a machine needs. Never ask them to rename anything.

Owner, 2026-10-01, connecting his own base to the AEO Machine: it refused his table because a field was
called "Seed", not "Seed Idea", and he had to rename it. "Clients are gonna be having lots of issues and there's
going to be a ton of technical support tickets!!!" Then: "Go, build the field matching."

A MACHINE DESCRIBES WHAT IT NEEDS AS ROLES ("the question each article answers", "the status", "the URL"), each
with the kinds of field that can hold it and the names a business is likely to have used. `match` reads the
table's real fields and picks the best one for each role: the exact name first, then a likely name, then a name
that contains one. A field is used for one role only. What can't be matched is listed with its plain label, so
the screen can offer the fix (pick a field, or add it in one tap) instead of an error that sends them to Airtable.

ONE MAP PER MACHINE, kept in the box's settings under "airtable" (one place per setting), so several machines
can share one base, each with its own map onto the same tables. `field(machine, role)` is how a machine's
code finds the business's name for a role every time it reads or writes a record.

STATUSES TOO. A machine's own states ("ready to write", "published") are matched to the business's own select
options ("Create Article", "Posted") by the same rules, with `match_options`.

Pure functions plus two small settings calls; never raises on a strange table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _f

# What each kind of role accepts, in Airtable's own type names.
KINDS = {
    "text": ("singleLineText", "multilineText", "richText"),
    "long_text": ("multilineText", "richText", "singleLineText"),
    "url": ("url", "singleLineText"),
    "status": ("singleSelect",),
    "date": ("dateTime", "date"),
    "number": ("number", "autoNumber", "percent", "currency"),
    "files": ("multipleAttachments",),
    "choices": ("multipleSelects", "singleSelect"),
}
NS = "airtable"


@dataclass(frozen=True)
class Role:
    """One thing a machine needs from a table. `aliases` are names a business is likely to have used, best
    first; `label` is what a person reads ("the question each article answers")."""
    name: str
    label: str
    kind: str
    aliases: tuple = ()
    required: bool = True
    extra_types: tuple = _f(default=())

    def accepts(self, airtable_type: str) -> bool:
        return airtable_type in KINDS.get(self.kind, ()) + tuple(self.extra_types)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _score(role: Role, field_name: str) -> int:
    """How well this field's NAME fits the role: 100 exact, 90..60 a likely name (earlier aliases first),
    40..10 a name that contains one or is contained in one. 0 means no."""
    n = _norm(field_name)
    if not n:
        return 0
    names = [_norm(role.name)] + [_norm(a) for a in role.aliases]
    if n == names[0]:
        return 100
    for i, a in enumerate(names[1:]):
        if a and n == a:
            return max(60, 90 - i * 3)
    for i, a in enumerate(names):
        if len(a) >= 3 and len(n) >= 3 and (a in n or n in a):
            return max(10, 40 - i * 3)
    return 0


def match(roles, fields) -> dict:
    """{role name: the field name to use, or None}. `fields` are a table's fields as the Airtable schema
    lists them ({"name", "type"}). Each field is used for one role at most; ties go to the earlier field."""
    fields = [f for f in (fields or []) if isinstance(f, dict) and f.get("name")]
    options = []
    for role in roles:
        for pos, f in enumerate(fields):
            if role.accepts(str(f.get("type") or "")):
                s = _score(role, f["name"])
                if s:
                    options.append((-s, pos, role.name, f["name"]))
    out, used = {r.name: None for r in roles}, set()
    for _, _, role_name, field_name in sorted(options):
        if out[role_name] is None and field_name not in used:
            out[role_name] = field_name
            used.add(field_name)
    return out


def missing(roles, mapping) -> list:
    """The required roles nothing was matched to: [(name, label, kind)], for the screen to offer a fix."""
    return [(r.name, r.label, r.kind) for r in roles if r.required and not (mapping or {}).get(r.name)]


def candidates(role: Role, fields) -> list:
    """Every field of a kind that can hold this role, for the screen's dropdown, best name first."""
    fields = [f for f in (fields or []) if isinstance(f, dict) and f.get("name")]
    ok = [(pos, f["name"]) for pos, f in enumerate(fields) if role.accepts(str(f.get("type") or ""))]
    return [name for _, name in sorted(ok, key=lambda p: (-_score(role, p[1]), p[0]))]


def match_options(states: dict, options) -> dict:
    """{state: the business's select option, or None}. `states` maps each of a machine's own states to the
    option names a business is likely to have used, best first; `options` are the select's real choices."""
    roles = [Role(state, state, "status", tuple(aliases)) for state, aliases in states.items()]
    as_fields = [{"name": str(o), "type": "singleSelect"} for o in (options or []) if str(o or "").strip()]
    return match(roles, as_fields)


# ── the saved map: one per machine, in the box's settings ───────────────────────────────────────────

def save_map(machine: str, mapping: dict, *, by: str = "") -> None:
    from core import box_settings
    clean = {str(k): str(v) for k, v in (mapping or {}).items() if v}
    box_settings.put(NS, f"fields:{machine}", clean, set_by=by or machine)


def load_map(machine: str) -> dict:
    from core import box_settings
    got = box_settings.get(NS, f"fields:{machine}", default={}) or {}
    return got if isinstance(got, dict) else {}


def field(machine: str, role: str, default: str | None = None) -> str | None:
    """The business's own field name for this machine's role, as matched and saved; `default` until then."""
    return load_map(machine).get(role) or default
