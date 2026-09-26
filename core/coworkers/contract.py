"""The coworker contract, version 1: the file a coworker is, and the receipt every run ends with.

PUBLIC, FROM THE FIRST COMMIT. Partners will write these files to sell machines that ship
coworkers (owner, 2026-09-26: developers and agencies within one to two months), so this module is
the one part of Shifts that cannot be fixed later by changing our own code. A published version is
never changed incompatibly; a new one is added beside it, and the old one keeps working for at
least twelve months after (owner, 2026-09-26, docs/SCOPE_SHIFTS.md §10). The partner-facing
description is docs/COWORKER_CONTRACT.md, and the two change together.

WHAT IS STRICT, AND WHY. The file is refused if it carries a key this version does not define, and
every limit below refuses rather than clamps. Loosening a rule later is compatible (a file that
passed still passes); tightening one breaks every file written against the looser rule. So v1
starts tight and grows only on purpose. The receipt goes the other way: the box writes it, and a
reader must ignore keys it does not know, because receipts gain fields as the box does.

NOTHING HERE RUNS ANYTHING. Parsing, validation and shapes only: no clock, no database, no AI, no
network. The tick, the runner and the steps are later pieces (§7), and they are built on this.
Never raises on a bad file: a bad file is a list of reasons, in plain words, for the person who
wrote it.

CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME. Steps name tools as `machine.tool`, and nothing
here knows which machines exist.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from datetime import date, datetime

from core.connector import tools

VERSION = 1                     # `coworker: 1` — the file format this module reads
SPOKEN = (1,)                   # every version this box reads. A newer file is refused, by name
RECEIPT_VERSION = 1             # `receipt: 1` — the shape every run ends with

FILE = "coworker.yaml"

# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────

# The folder name is the coworker's id, the same slug rule machines use (core/custom_machines.py),
# so one name means one thing on a box.
_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
# `from:` is "my" or the machine that shipped the coworker. Machines registered in code use
# underscores (aeo_machine), folder machines use hyphens, so both are accepted.
_MACHINE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
# A step names a tool the way a person would say it, `machine.tool`. The box's MCP publishes the
# same tool as `aios.machine.tool` (core/connector/tools.py), and `tool_name()` maps one to other.
_STEP = re.compile(r"^([a-z][a-z0-9_-]{1,40})\.([a-z][a-z0-9_]{1,60})$")
_JOB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}\.md$")
_TIME = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$")

# The web is a capability like any other, off unless granted (§2). It is not a tool on the box:
# the runner turns these into the AI's own search and fetch, and turns their absence into neither.
WEB = ("web:read", "web:search")

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_REQUIRED = ("coworker", "title", "job", "from", "may", "shifts")
_OPTIONAL = ("steps", "limits", "enabled")

# Technical limits, approved by OSDev1 in review of #1611 (2026-09-26). Each refuses rather than
# clamps, so each can be loosened later without breaking a file that passed.
TITLE_MAX = 60
JOB_MAX_BYTES = 32_000          # a job is instructions, not a knowledge base
SHIFTS_MAX = 24                 # enough for an hourly coworker
STEPS_MAX = 10                  # per phase
WINDOW_MIN = 5                  # minutes from start to latest. A one-minute tick needs slack
LIMITS = {"minutes": (1, 120, 30), "turns": (1, 200, 40)}   # (least, most, default)

OUTCOMES = ("DONE", "FAILED", "MISSED")
_STEP_OUTCOMES = ("ok", "failed", "skipped")
_PHASES = ("before", "after")


def may_grant(capability: str) -> bool:
    """May a coworker be granted this? What an AI may hold, plus the web. Never `act:`."""
    return capability in WEB or tools.ai_may_hold(capability)


def tool_name(step: str) -> str:
    """`acme.send_approved` → `aios.acme.send_approved`, the name the registry knows it by."""
    return f"aios.{step}"


# ── the file ─────────────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Shift:
    days: frozenset             # weekday numbers, Monday = 0, as date.weekday() counts
    start: str                  # "HH:MM" in the box's timezone
    latest: str                 # the last minute it may START; after that the shift is MISSED

    def works_on(self, day: date) -> bool:
        return day.weekday() in self.days


@dataclasses.dataclass(frozen=True)
class Coworker:
    slug: str
    title: str
    job: str                    # the job file's name, relative to the coworker's folder
    source: str                 # `from:` in the file: "my", or the machine that shipped it
    may: tuple                  # sorted capabilities; nothing else is callable
    shifts: tuple
    before: tuple               # step names, `machine.tool`, in the order they run
    after: tuple
    minutes: int
    turns: int
    enabled: bool


def slot_key(slug: str, day: date, start: str) -> str:
    """One shift on one day, `front-desk@2026-09-28T07:30`. A slot runs at most once (§3)."""
    return f"{slug}@{day.isoformat()}T{start}"


def risks(cw: Coworker) -> tuple:
    """What the owner is asked to tick before this coworker may run (§4.3).

    `private_and_web`: it can read the box's data AND reach the web, so a page it reads could talk
    it into putting what it read into a URL. Not refused, because some jobs need both; asked, in
    one plain sentence, on the screen that grants it. The tick is the owner's, kept by the box, and
    never in this file: a file that could carry it would ship from a partner already ticked.
    """
    reads = any(c.startswith("read:") for c in cw.may)
    webs = any(c in WEB for c in cw.may)
    return ("private_and_web",) if reads and webs else ()


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _time(value, where: str, why: list):
    # AN UNQUOTED 17:00 IN YAML IS THE NUMBER 1020 (YAML 1.1 reads it as base 60), and 07:30 is
    # the text "07:30". Converting the number back would make integer minutes part of the contract
    # forever, and a YAML 1.2 reader would disagree about the same file. So it is refused, with the
    # fix, which is to put the time in quotes.
    if isinstance(value, bool) or isinstance(value, int):
        why.append(f"{where} must be a time in quotes, like \"17:00\"")
        return None
    if not isinstance(value, str) or not _TIME.match(value.strip()):
        why.append(f"{where} must be a 24-hour time like \"07:30\", got {value!r}")
        return None
    h, m = value.strip().split(":")
    return f"{int(h):02d}:{m}"


def _days(value, where: str, why: list):
    """"Mon-Fri", "Mon,Wed,Fri", "Sat-Sun", "Fri-Mon" (wraps), or "Daily". Case does not matter."""
    if not isinstance(value, str) or not value.strip():
        why.append(f"{where} must be days like \"Mon-Fri\", \"Mon,Wed,Fri\" or \"Daily\"")
        return None
    text = value.strip().lower()
    if text == "daily":
        return frozenset(range(7))
    out = set()
    for part in (p.strip() for p in text.split(",")):
        ends = [e.strip() for e in part.split("-")]
        if len(ends) not in (1, 2) or any(e not in _DAY_NAMES for e in ends):
            why.append(f"{where}: {part!r} is not a day. Use Mon Tue Wed Thu Fri Sat Sun, a range "
                       f"like Mon-Fri, or Daily")
            return None
        a = _DAY_NAMES.index(ends[0])
        b = _DAY_NAMES.index(ends[-1])
        out.update((a + i) % 7 for i in range(((b - a) % 7) + 1))
    return frozenset(out)


def _shifts(value, why: list) -> tuple:
    if not isinstance(value, list) or not value:
        why.append("shifts must be a list with at least one shift")
        return ()
    if len(value) > SHIFTS_MAX:
        why.append(f"shifts: at most {SHIFTS_MAX}, got {len(value)}")
        return ()
    out = []
    for n, s in enumerate(value, 1):
        where = f"shift {n}"
        if not isinstance(s, dict):
            why.append(f"{where} must be a mapping with days, start and latest")
            continue
        extra = sorted(set(s) - {"days", "start", "latest"})
        if extra:
            why.append(f"{where} has keys this version does not define: {', '.join(map(str, extra))}")
        days = _days(s.get("days"), f"{where} days", why)
        start = _time(s.get("start"), f"{where} start", why)
        latest = _time(s.get("latest"), f"{where} latest", why)
        if days is None or start is None or latest is None:
            continue
        # A window never crosses midnight in v1: a slot belongs to exactly one day, which is what
        # makes "runs at most once" a lookup by key rather than a question about yesterday.
        if _minutes(latest) - _minutes(start) < WINDOW_MIN:
            why.append(f"{where}: latest must be at least {WINDOW_MIN} minutes after start, on the "
                       f"same day (start {start}, latest {latest})")
            continue
        out.append(Shift(days=days, start=start, latest=latest))
    # ONE SHIFT AT A TIME. Two windows of one coworker that overlap on a shared day would queue
    # behind each other and one would be MISSED by design. Said here, where it can be fixed.
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if a.days & b.days and (_minutes(a.start) <= _minutes(b.latest)
                                    and _minutes(b.start) <= _minutes(a.latest)):
                why.append(f"shifts {a.start}-{a.latest} and {b.start}-{b.latest} overlap on the "
                           f"same day; a coworker works one shift at a time")
    return tuple(out)


def _may(value, why: list) -> tuple:
    if not isinstance(value, list):
        why.append("may must be a list of capabilities, like [read:inbox, write:proposals]")
        return ()
    out = set()
    for c in value:
        if not isinstance(c, str):
            why.append(f"may: {c!r} is not a capability")
        elif c.startswith("act:"):
            why.append(f"may: {c} is an act: capability. A coworker never holds one; put the tool "
                       f"that needs it in steps, which the box calls itself")
        elif c.startswith("write:") and c != "write:proposals":
            why.append(f"may: {c}. A coworker's only write is write:proposals, which a person "
                       f"approves before anything happens")
        elif not may_grant(c):
            why.append(f"may: {c!r} is not a capability. They look like read:inbox, "
                       f"write:proposals, web:search or web:read")
        else:
            out.add(c)
    return tuple(sorted(out))


def _steps(value, why: list) -> tuple:
    if value is None:
        return (), ()
    if not isinstance(value, dict):
        why.append("steps must be a mapping with before and after lists")
        return (), ()
    extra = sorted(set(value) - set(_PHASES))
    if extra:
        why.append(f"steps has keys this version does not define: {', '.join(map(str, extra))}")
    got = []
    for phase in _PHASES:
        items = value.get(phase) or []
        if not isinstance(items, list):
            why.append(f"steps {phase} must be a list of tools like acme.send_approved")
            got.append(())
            continue
        if len(items) > STEPS_MAX:
            why.append(f"steps {phase}: at most {STEPS_MAX}, got {len(items)}")
        bad = [s for s in items if not isinstance(s, str) or not _STEP.match(s)]
        for s in bad:
            why.append(f"steps {phase}: {s!r} is not a tool. Name it machine.tool, like "
                       f"acme.send_approved")
        got.append(tuple(s for s in items if s not in bad))
    return got[0], got[1]


def _limits(value, why: list) -> tuple:
    value = {} if value is None else value
    if not isinstance(value, dict):
        why.append("limits must be a mapping, like {minutes: 30, turns: 40}")
        return LIMITS["minutes"][2], LIMITS["turns"][2]
    extra = sorted(set(value) - set(LIMITS))
    if extra:
        why.append(f"limits has keys this version does not define: {', '.join(map(str, extra))}")
    out = []
    for k, (lo, hi, default) in LIMITS.items():
        v = value.get(k, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            why.append(f"limits {k} must be a whole number from {lo} to {hi}, got {v!r}")
            v = default
        out.append(v)
    return tuple(out)


def parse(data, slug: str) -> tuple:
    """(Coworker, []) or (None, [every reason it was refused]). Never raises.

    `slug` is the folder's name. Every reason is collected, not just the first, so a partner's
    conformance check and the Shifts screen can show the whole list at once.
    """
    why: list = []
    if not isinstance(slug, str) or not _SLUG.match(slug):
        why.append(f"the folder name {slug!r} must be lowercase letters, digits and hyphens")
    if not isinstance(data, dict):
        return None, why + [f"{FILE} must be a mapping"]

    # THE VERSION FIRST, and alone. A file in a version this box does not read is refused before
    # anything else is judged by the wrong version's rules.
    v = data.get("coworker")
    if isinstance(v, bool) or not isinstance(v, int):
        return None, why + [f"{FILE} must start with the contract version, coworker: {VERSION}"]
    if v not in SPOKEN:
        return None, why + [f"this box reads coworker: {', '.join(map(str, SPOKEN))}; this file "
                            f"is coworker: {v}. Update the box to run it"]

    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        why.append(f"{FILE} is missing {', '.join(missing)}")
    # `on:` IS NOT A KEY IN YAML 1.1: PyYAML reads it (and yes, no, off) as the boolean True, so
    # the scope's `on: true` arrives as {True: True}. The key is `enabled:`, as in machine.yaml's
    # config, and a file that says `on:` is told so rather than told about a key named "True".
    if any(isinstance(k, bool) for k in data):
        why.append("use enabled: true or enabled: false (YAML reads a key named on, off, yes or "
                   "no as true or false)")
    extra = sorted(set(map(str, (k for k in data if not isinstance(k, bool))))
                   - set(_REQUIRED) - set(_OPTIONAL))
    if extra:
        why.append(f"{FILE} has keys coworker: {VERSION} does not define: {', '.join(extra)}")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or "\n" in title.strip():
        why.append("title must be one line of text")
    elif len(title.strip()) > TITLE_MAX:
        why.append(f"title: at most {TITLE_MAX} characters")
    job = data.get("job")
    if not isinstance(job, str) or not _JOB.match(job):
        why.append("job must be the name of a .md file in this folder, like job.md")
    source = data.get("from")
    if not isinstance(source, str) or not (source == "my" or _MACHINE.match(source)):
        why.append("from must be my, or the name of the machine that shipped this coworker")
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        why.append("enabled must be true or false")

    may = _may(data.get("may"), why) if "may" in data else ()
    shifts = _shifts(data.get("shifts"), why) if "shifts" in data else ()
    before, after = _steps(data.get("steps"), why)
    minutes, turns = _limits(data.get("limits"), why)

    if why:
        return None, why
    return Coworker(slug=slug, title=title.strip(), job=job, source=source, may=may,
                    shifts=shifts, before=before, after=after, minutes=minutes, turns=turns,
                    enabled=enabled), []


def load(folder) -> tuple:
    """Read one coworker folder: `coworker.yaml` plus its job file. (Coworker, []) or (None, why).

    Never raises. The job file is read here too, because a coworker whose job cannot be read is
    one that would start on time and do nothing, which is the silence §0 promises never happens.
    """
    folder = pathlib.Path(folder)
    f = folder / FILE
    if not f.is_file():
        return None, [f"no {FILE} in the folder"]
    try:
        import yaml
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except Exception as e:                          # noqa: BLE001 — a bad file is a reason
        return None, [f"{FILE} could not be read: {type(e).__name__}"]
    cw, why = parse(data, folder.name)
    if cw is None:
        return None, why
    job = folder / cw.job
    try:
        if job.is_symlink() or not job.is_file():
            return None, [f"job file {cw.job} is not in the folder"]
        size = job.stat().st_size
        if size > JOB_MAX_BYTES:
            return None, [f"job file {cw.job} is {size} bytes; at most {JOB_MAX_BYTES}"]
        if not job.read_text(encoding="utf-8").strip():
            return None, [f"job file {cw.job} is empty"]
    except (OSError, UnicodeDecodeError) as e:
        return None, [f"job file {cw.job} could not be read: {type(e).__name__}"]
    return cw, []


def unresolved(cw: Coworker, registry: dict) -> list:
    """Steps this box cannot call, as reasons. [] when every step resolves and can be called.

    Two ways a step fails here: its tool is not on the box, or the tool is not written to be a
    step (it does not take exactly the run context, below). Asked before a run (the preflight in
    §4), never when the file is read: a coworker whose machine is missing today is still a valid
    file, and says FAILED with the machine's name rather than disappearing.
    """
    out = []
    for phase, steps in (("before", cw.before), ("after", cw.after)):
        for s in steps:
            spec = registry.get(tool_name(s))
            if spec is None:
                machine = s.split(".", 1)[0]
                out.append(f"steps {phase}: {s} is not on this box (is the {machine} machine "
                           f"installed and working?)")
                continue
            why = step_signature(spec)
            if why:
                out.append(f"steps {phase}: {s} cannot be a step: {why}")
    return out


# ── the step call ────────────────────────────────────────────────────────────────────────────────
#
# PUBLIC, because partners write step tools (OSDev1, review of #1611). A step is a machine tool the
# BOX calls, before or after the AI, and this is the whole of what passes between them:
#
#   called with   the run context, exactly these five arguments and nothing the AI wrote.
#                 `workspace` is how an after-step reads what the AI left there.
#   dry_run       true on Run now. A step MUST then do nothing outward (no send, no publish, no
#                 charge) and say in its summary what it would have done. Run now is how an owner
#                 tries a coworker safely, and it stays safe only if every step honours this.
#   returns       {"summary": str, "outputs": [...]}, outputs in the receipt's typed shape; the
#                 runner merges them into the run's receipt.
#   fails         by raising, or by returning anything else. The step is failed and the run is
#                 FAILED, naming {machine, tool}.

STEP_ARGS = {"run_id": "string", "coworker": "string", "slot": "string", "workspace": "string",
             "dry_run": "boolean"}


def step_signature(spec: dict) -> str:
    """Why this registered tool cannot be called as a step, or "" when it can.

    EXACTLY the five, each required and of its type. An extra argument would be one the box never
    passes, and a tool that asks for the caller's seat is one that varies its answer by who asks;
    a step has no caller but the box.
    """
    args = spec.get("args") or {}
    if spec.get("wants_seat"):
        return "it asks for the caller's seat, and a step has no caller but the box"
    missing = [a for a in STEP_ARGS if a not in args]
    if missing:
        return f"it does not take {', '.join(missing)}"
    extra = sorted(set(args) - set(STEP_ARGS))
    if extra:
        return f"it takes {', '.join(extra)}, which the box never passes to a step"
    for a, kind in STEP_ARGS.items():
        if args[a].get("type") != kind or not args[a].get("required"):
            return f"{a} must be a required {kind}"
    return ""


def step_context(*, run_id: str, cw: Coworker, slot: str, workspace, dry_run: bool) -> dict:
    """The five arguments every step is called with. Raises ValueError on the box's own bug."""
    ctx = {"run_id": run_id, "coworker": cw.slug, "slot": slot, "workspace": str(workspace),
           "dry_run": dry_run}
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(slot, str) \
            or not slot.strip():
        raise ValueError("a step's run_id and slot are non-empty text")
    if not pathlib.Path(ctx["workspace"]).is_absolute():
        raise ValueError("a step's workspace is an absolute path")
    if not isinstance(dry_run, bool):
        raise ValueError("a step's dry_run is true or false")
    return ctx


def check_step_result(r) -> list:
    """Every way `r` is not what a step returns, as reasons. [] when it is."""
    if not isinstance(r, dict):
        return ["a step returns {summary, outputs}"]
    why = []
    extra = sorted(set(map(str, r)) - {"summary", "outputs"})
    if extra:
        why.append(f"a step returns only summary and outputs, not {', '.join(extra)}")
    if not isinstance(r.get("summary"), str) or not r.get("summary").strip():
        why.append("summary must be non-empty text: what the step did, or on a dry run what it "
                   "would have done")
    if not _outputs_ok(r.get("outputs")):
        why.append("outputs must be a list of {type, machine, id, title?}")
    return why


# ── the receipt ──────────────────────────────────────────────────────────────────────────────────
#
# Every run ends with exactly one (§0): DONE and what it did, FAILED and why, or MISSED and why.
# It is what the owner's report is written from, what a later handoff keys on (`outputs`), and
# what partner metering will count (`tools`, `usage`).

_RECEIPT_KEYS = ("receipt", "run_id", "coworker", "from", "slot", "outcome", "reason", "window",
                 "started_at", "ended_at", "attempts", "usage", "failed", "steps", "tools",
                 "outputs")


def _when(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        return datetime.fromisoformat(v).tzinfo is not None
    except ValueError:
        return False


def _nonneg(v, kind) -> bool:
    return not isinstance(v, bool) and isinstance(v, kind) and v >= 0


def check_receipt(r) -> list:
    """Every way `r` is not a v1 receipt, as reasons. [] when it is one."""
    if not isinstance(r, dict):
        return ["a receipt is a mapping"]
    why = []
    missing = [k for k in _RECEIPT_KEYS if k not in r]
    if missing:
        why.append(f"missing {', '.join(missing)}")
    extra = sorted(set(map(str, r)) - set(_RECEIPT_KEYS))
    if extra:
        why.append(f"keys receipt: {RECEIPT_VERSION} does not define: {', '.join(extra)}")
    if r.get("receipt") != RECEIPT_VERSION:
        why.append(f"receipt must be {RECEIPT_VERSION}")
    for k in ("run_id", "slot", "reason"):
        if not isinstance(r.get(k), str) or not r.get(k).strip():
            why.append(f"{k} must be non-empty text")
    if not isinstance(r.get("coworker"), str) or not _SLUG.match(r.get("coworker") or ""):
        why.append("coworker must be the coworker's folder name")
    src = r.get("from")
    if not isinstance(src, str) or not (src == "my" or _MACHINE.match(src)):
        why.append("from must be my or a machine's name")
    outcome = r.get("outcome")
    if outcome not in OUTCOMES:
        why.append(f"outcome must be one of {', '.join(OUTCOMES)}")

    w = r.get("window")
    if not isinstance(w, dict) or set(w) != {"start", "latest"} \
            or not all(_when(w.get(k)) for k in ("start", "latest")):
        why.append("window must be {start, latest} as times with a UTC offset")
    if not _when(r.get("ended_at")):
        why.append("ended_at must be a time with a UTC offset")
    # A MISSED run never started. Anything else did, and says when.
    if outcome == "MISSED":
        if r.get("started_at") is not None or r.get("attempts") != 0:
            why.append("a MISSED run has started_at null and attempts 0")
    elif outcome in OUTCOMES:
        if not _when(r.get("started_at")):
            why.append("started_at must be a time with a UTC offset")
        if r.get("attempts") not in (1, 2):
            why.append("attempts must be 1, or 2 after the one retry")

    u = r.get("usage")
    if not isinstance(u, dict) or set(u) != {"minutes", "turns", "cost_usd"} \
            or not _nonneg(u.get("minutes"), (int, float)) or not _nonneg(u.get("turns"), int) \
            or not (u.get("cost_usd") is None or _nonneg(u.get("cost_usd"), (int, float))):
        why.append("usage must be {minutes, turns, cost_usd}; cost_usd is null when not known")

    # WHO FAILED, BY NAME (§3): "FAILED: Acme Social's schedule_post returned an error", never a
    # mystery. Only a FAILED run names one, and it may be null when nothing on the box failed
    # (the AI was unreachable, a limit was hit); `reason` says which.
    f = r.get("failed")
    if f is not None:
        if outcome != "FAILED":
            why.append("failed is null unless the outcome is FAILED")
        if not isinstance(f, dict) or set(f) != {"machine", "tool"} \
                or not (isinstance(f.get("machine"), str) and _MACHINE.match(f["machine"])) \
                or not (f.get("tool") is None or (isinstance(f["tool"], str)
                                                  and _STEP.match(f["tool"]))):
            why.append("failed must be {machine, tool}; tool is machine.tool or null")

    steps = r.get("steps")
    if not isinstance(steps, list) or not all(
            isinstance(s, dict) and set(s) == {"phase", "tool", "outcome", "reason"}
            and s.get("phase") in _PHASES and s.get("outcome") in _STEP_OUTCOMES
            and isinstance(s.get("tool"), str) and _STEP.match(s["tool"])
            and isinstance(s.get("reason"), str) for s in steps):
        why.append("steps must be a list of {phase, tool, outcome, reason}")
    used = r.get("tools")
    if not isinstance(used, list) or not all(
            isinstance(t, dict) and set(t) == {"tool", "calls", "failed"}
            and isinstance(t.get("tool"), str) and _STEP.match(t["tool"])
            and _nonneg(t.get("calls"), int) and _nonneg(t.get("failed"), int)
            for t in used):
        why.append("tools must be a list of {tool, calls, failed}")
    # TYPED, so a later trigger has something to key on ("when the AEO coworker publishes an
    # article..."). `type` is the machine's own word for what it made; `id` is that machine's id.
    if not _outputs_ok(r.get("outputs")):
        why.append("outputs must be a list of {type, machine, id, title?}")
    return why


def _outputs_ok(outs) -> bool:
    """One shape for what a run made, whether the AI or a step made it."""
    return isinstance(outs, list) and all(
        isinstance(o, dict) and {"type", "machine", "id"} <= set(o)
        and set(o) <= {"type", "machine", "id", "title"}
        and all(isinstance(o.get(k), str) and o.get(k) for k in ("type", "machine", "id"))
        and _MACHINE.match(o["machine"]) and re.match(r"^[a-z][a-z0-9_]{1,40}$", o["type"])
        and isinstance(o.get("title", ""), str) for o in outs)


def receipt(*, run_id: str, cw: Coworker, slot: str, outcome: str, reason: str, window: dict,
            ended_at: str, started_at: str | None = None, attempts: int = 1,
            minutes: float = 0, turns: int = 0, cost_usd: float | None = None,
            failed: dict | None = None, steps: list | None = None, tools_used: list | None = None,
            outputs: list | None = None) -> dict:
    """Build a v1 receipt. Raises ValueError, naming every problem, if it would not be one.

    Raising is right here and nowhere else in this module: a malformed receipt is the box's own
    bug, and the runner's last resort (a FAILED receipt that says the receipt failed) is simpler to
    trust than a receipt that was quietly patched into shape.
    """
    r = {"receipt": RECEIPT_VERSION, "run_id": run_id, "coworker": cw.slug, "from": cw.source,
         "slot": slot, "outcome": outcome, "reason": reason, "window": window,
         "started_at": None if outcome == "MISSED" else started_at, "ended_at": ended_at,
         "attempts": 0 if outcome == "MISSED" else attempts,
         "usage": {"minutes": minutes, "turns": turns, "cost_usd": cost_usd},
         "failed": failed, "steps": list(steps or []), "tools": list(tools_used or []),
         "outputs": list(outputs or [])}
    why = check_receipt(r)
    if why:
        raise ValueError("not a receipt: " + "; ".join(why))
    return r
