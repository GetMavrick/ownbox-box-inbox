"""Machines ship coworkers; the owner hires them (docs/SCOPE_SHIFTS.md §5, §4.3).

A machine folder may carry `coworkers/<name>/` in exactly the coworker file format. Those are
OFFERS: they never run where they are. Hiring copies one into `my/coworkers/`, where the owner can
change its job or its times, and where a machine update can never overwrite it.

THE PERMISSION SHEET IS DATA, NOT A PAGE. `sheet()` says, in the owner's words, what the coworker
wants, what it can't do, what the box will do for it, and when it works, the way a mobile app lists
its permissions. The Shifts screen (OSDev5, §7 piece 4) draws it; nothing here renders HTML.

READING THE BOX'S DATA AND REACHING THE WEB, TOGETHER, NEEDS THE OWNER'S OK (§4.3). A web page the
coworker reads could talk it into putting what it read into a URL. That is not refused, because
some jobs need both; it is asked, once, in one sentence. The OK is stored by the BOX, against the
EXACT grant: change the grant and it is asked again. It is never in the coworker's file, because a
file that could carry it would ship from a partner already ticked.

VERSIONED BY THE EXISTING MECHANISM. A machine that ships coworkers declares
`requires_foundation: "1.2"` in its machine.yaml; an older box refuses the machine as it always
has, and this box refuses to offer coworkers from a machine that does not declare it, saying so.

CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME: offers come from whatever folders are in
my/machines/.
"""
from __future__ import annotations

import pathlib
import shutil

from core.coworkers import contract

FOUNDATION = "1.2"              # the first foundation that runs coworkers a machine ships

RISK = ("This coworker can read your box's data and can also reach the web. A web page it reads "
        "could try to trick it into putting what it read into a web address.")
CANNOT = ("send, publish or pay for anything", "see your keys or passwords")

# The words for each capability, as the owner reads them. A capability not listed here is
# described from its own name, so a partner's new one is never shown blank.
_WANTS = {
    "read:inbox": "read your conversations",
    "read:reports": "read your morning report",
    "read:health": "see whether the box is running",
    "read:spend": "see what the box is spending",
    "read:manifest": "see which tools the box has",
    "write:proposals": "draft things for your approval",
    "web:search": "search the web",
    "web:read": "read web pages",
}
_DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _vtuple(v) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return (0,)


def _machines():
    from core import custom_machines
    return custom_machines.discover()


def _mine() -> pathlib.Path:
    from core.coworkers import runner
    return runner.coworkers_dir()


def offers() -> list:
    """Every coworker a machine on this box ships, hireable or not, with why not.

    [{"machine", "name", "coworker" (Coworker|None), "reason", "hired"}], sorted. Never raises.
    """
    out = []
    for m in _machines():
        folder = pathlib.Path(m["path"]) / "coworkers"
        if not folder.is_dir():
            continue
        names = sorted(p.name for p in folder.iterdir()
                       if p.is_dir() and not p.name.startswith((".", "_")))
        manifest = m.get("manifest")
        for name in names:
            offer = {"machine": m["slug"], "name": name, "coworker": None, "reason": "",
                     "hired": _hired_from(name, m["slug"])}
            if manifest is None:
                offer["reason"] = f"its machine cannot load: {m.get('reason') or 'unknown'}"
            elif _vtuple(manifest.get("requires_foundation")) < _vtuple(FOUNDATION):
                offer["reason"] = (f"its machine must declare requires_foundation "
                                   f"\"{FOUNDATION}\" to ship coworkers")
            else:
                cw, why = contract.load(folder / name)
                if cw is None:
                    offer["reason"] = "; ".join(why[:3])
                elif cw.source != m["slug"]:
                    offer["reason"] = (f"its file says from: {cw.source}, and it is shipped by "
                                       f"{m['slug']}")
                else:
                    offer["coworker"] = cw
            out.append(offer)
    return out


def _hired_from(name: str, machine: str) -> bool:
    """Is my/coworkers/<name> THIS machine's coworker? A folder of the same name the owner made,
    or another machine's, is not this offer hired."""
    if not (_mine() / name / contract.FILE).is_file():
        return False
    cw, _ = contract.load(_mine() / name)
    return cw is not None and cw.source == machine


def fingerprint_of(cw: contract.Coworker) -> str:
    """What the permission sheet showed, as one value: the grant, the steps and the times.

    hire() takes it back and refuses when the offer no longer matches, so a machine update between
    the sheet and the tap can never hire a wider grant, a new act: step, or new hours the owner did
    not see. The job's words are not in it: they are shown, not granted.
    """
    import hashlib
    import json
    shown = {"may": sorted(cw.may), "before": list(cw.before), "after": list(cw.after),
             "shifts": [[sorted(s.days), s.start, s.latest] for s in cw.shifts]}
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()


def _days(days: frozenset) -> str:
    if days == frozenset(range(7)):
        return "Every day"
    if days == frozenset(range(5)):
        return "Weekdays"
    if days == frozenset({5, 6}):
        return "Weekends"
    return ", ".join(_DAY[d] for d in sorted(days))


def _want(cap: str) -> str:
    if cap in _WANTS:
        return _WANTS[cap]
    verb, noun = cap.split(":", 1)
    return f"{'read' if verb == 'read' else 'draft'} your {noun.replace('_', ' ')}"


def sheet(cw: contract.Coworker, *, machine: str = "", registry: dict | None = None) -> dict:
    """What the owner reads before hiring (or before letting their own coworker run).

    {"who", "wants": [...], "cannot": [...], "box_does": [...], "works": [...], "risk": str|None,
     "fingerprint": str}. The screen hands `fingerprint` back to hire() with the owner's tap.
    """
    reg = registry if registry is not None else _registry()
    who = f"{machine}'s {cw.title}" if machine and machine != "my" else cw.title
    box_does = []
    for phase, steps in (("before", cw.before), ("after", cw.after)):
        for s in steps:
            spec = reg.get(contract.tool_name(s)) or {}
            what = spec.get("description") or s
            box_does.append(f"{'Before' if phase == 'before' else 'After'} its shift: {what}")
    return {"who": who,
            "wants": [_want(c) for c in cw.may],
            "cannot": list(CANNOT),
            "box_does": box_does,
            "works": [f"{_days(s.days)}, starting between {s.start} and {s.latest}"
                      for s in cw.shifts],
            "risk": RISK if contract.risks(cw) else None,
            "fingerprint": fingerprint_of(cw)}


def _registry() -> dict:
    try:
        from core.connector import tools
        return tools.registry()
    except Exception:                                   # noqa: BLE001 — a sheet without step words
        return {}


# ── the owner's OK for data and the web together (§4.3) ──────────────────────────────────────────

def _key(cw: contract.Coworker) -> str:
    # KEYED ON WHERE IT CAME FROM AND ITS NAME: a hired `scout` removed and a `scout` of the owner's
    # own made later are two coworkers, and the first one's OK never covers the second.
    return f"risk_ok:{cw.source}:{cw.slug}"


def acknowledge(cw: contract.Coworker, *, by: str) -> None:
    """The owner said yes to this coworker's exact grant. `by` is who, for the audit answer."""
    from core import box_settings
    box_settings.put("coworkers", _key(cw), sorted(cw.may), set_by=str(by or "")[:80] or None)


def acknowledged(cw: contract.Coworker) -> bool:
    """True when this coworker needs no OK, or has one for exactly the grant it has now.

    FROM THE DATABASE ROW ONLY. box_settings.get() falls through to the config files, and an OK in
    a file is exactly what §4.3 rules out: a file could ship from a partner already ticked.
    """
    if not contract.risks(cw):
        return True
    from core import box_settings
    try:
        return box_settings._read("coworkers", _key(cw), None) == sorted(cw.may)
    except Exception:                                   # noqa: BLE001 — unreadable: not given
        return False


# ── hiring ───────────────────────────────────────────────────────────────────────────────────────

def hire(machine: str, name: str, *, fingerprint: str, by: str,
         accept_risk: bool = False) -> contract.Coworker:
    """Copy a machine's coworker into my/coworkers/<name>, switched on. Raises ValueError, in the
    owner's words, when it can't: nothing is half-copied.

    `fingerprint` is sheet()["fingerprint"] from the sheet the owner saw; if the offer changed
    since (a machine update), nothing is hired and the sheet must be read again. `by` is who
    tapped, recorded with the OK. `accept_risk` is the owner's tick on the §4.3 sentence; hiring a
    coworker that needs it without it is refused, so the sheet cannot be skipped.
    """
    offer = next((o for o in offers() if o["machine"] == machine and o["name"] == name), None)
    if offer is None:
        raise ValueError(f"{machine} does not offer a coworker called {name}")
    cw = offer["coworker"]
    if cw is None:
        raise ValueError(f"{name} can't be hired: {offer['reason']}")
    target = _mine() / name
    if target.exists():
        raise ValueError(f"You already have a coworker called {name}. Rename or remove that "
                         f"one first")
    if fingerprint_of(cw) != fingerprint:
        raise ValueError(f"{name} has changed since you looked at it. Open it again to see what "
                         f"it asks for now.")
    if contract.risks(cw) and not accept_risk:
        raise ValueError(RISK + " Tick the box to hire it anyway.")

    import yaml
    src = pathlib.Path(next(m["path"] for m in _machines() if m["slug"] == machine)) \
        / "coworkers" / name
    data = yaml.safe_load((src / contract.FILE).read_text(encoding="utf-8"))
    data["enabled"] = True
    # STAGED UNDER ITS REAL NAME (the folder name is the coworker's id, and it is validated),
    # in a dot-folder the tick never reads, then moved into place in one rename.
    tmp = _mine() / ".hiring" / name
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        # ONLY THE TWO FILES A COWORKER IS. Anything else in the offer's folder stays with the
        # machine: a hired coworker is a file and a job, and nothing a partner can smuggle in.
        (tmp / contract.FILE).write_text(yaml.safe_dump(data, sort_keys=False),
                                         encoding="utf-8")
        shutil.copyfile(src / cw.job, tmp / cw.job)
        hired, why = contract.load(tmp)
        if hired is None:
            raise ValueError(f"{name} could not be hired: " + "; ".join(why[:3]))
        # THE OK IS SAVED BEFORE THE COWORKER EXISTS. Saved after, a failed write would leave it
        # hired but unable to run, with the owner told it failed and a retry told it exists.
        if accept_risk and contract.risks(hired):
            acknowledge(hired, by=by)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    hired, _ = contract.load(target)
    return hired
