"""When shifts are due: the clockwork, as pure functions of the coworkers and the time.

docs/SCOPE_SHIFTS.md §3. Nothing here reads a clock, a file or the database. The tick passes in
`now`, the last time it ran, and the slots already taken, and gets back what to start, what was
missed and when the next window opens. So every rule below is tested on a fixed calendar, daylight
saving days included, and the same code runs on a laptop with a fake clock (the partner SDK, §7).

THE RULES, EACH A LINE IN THE SCOPE:

  A shift is a WINDOW, start to latest. It is due from its start until its latest, inclusive.
  Due slots queue and run one at a time, EARLIEST LATEST FIRST: the one that would miss soonest
  goes first, whatever order the coworkers were written in.
  A slot RUNS AT MOST ONCE. Its key is coworker + date + start; any row for it, of any status,
  means it is taken.
  A slot that could not start by its latest is MISSED, never run late. The tick reports exactly
  the slots whose latest fell since it last ran, so a box that was off for three hours reports
  each shift it slept through once, and a coworker switched on at 10:00 is not blamed for 07:30.
  Starts are computed in the owner's timezone on every tick, with zoneinfo, so daylight saving
  moves nothing: 07:30 is 07:30 on the wall the day the clocks change.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, time, timedelta, timezone

from core.coworkers import contract

# How far back the tick looks for slots it slept through. A box off for longer reports the last
# two days' misses and no more: a pile of week-old MISSED reports helps nobody.
LOOKBACK = timedelta(days=2)
# How far ahead the next window is looked for. A week and a day, so a once-a-week shift is found
# from any day, and Friday evening finds Monday morning.
LOOKAHEAD_DAYS = 8


@dataclasses.dataclass(frozen=True)
class Slot:
    key: str                    # contract.slot_key: the at-most-once identity
    coworker: str
    start: datetime             # aware, in the box's timezone
    latest: datetime


@dataclasses.dataclass(frozen=True)
class Plan:
    due: tuple                  # start these, in this order, one at a time
    missed: tuple               # report these MISSED; they will never run
    next_start: datetime | None  # the soonest window start still to come, for update coordination


def _exists(d: datetime) -> bool:
    """False for a wall time the clocks skip (the spring-forward hour): it does not round-trip."""
    back = d.astimezone(timezone.utc).astimezone(d.tzinfo)
    return back.replace(tzinfo=None) == d.replace(tzinfo=None)


def _at(day: date, hhmm: str, tz) -> tuple:
    """(the moment, whether the wall time existed). A time the clocks skip is the moment they
    change: 02:30 on a spring-forward night is 03:00, when the owner's clock next reads a time at
    or after it. Read as-is, zoneinfo would place it an hour LATE, after the window's own latest.
    """
    h, m = hhmm.split(":")
    d = datetime.combine(day, time(int(h), int(m)), tzinfo=tz)
    if _exists(d):
        return d, True
    for _ in range(24 * 60):                     # a gap is an hour, rarely more; a day is a bound
        d = (d.replace(tzinfo=None) + timedelta(minutes=1)).replace(tzinfo=tz)
        if _exists(d):
            return d, False
    return d, False


def slots_on(cw: contract.Coworker, day: date, tz) -> list:
    """The coworker's slots on one calendar day in the owner's timezone. [] when it is off."""
    if not cw.enabled:
        return []
    out = []
    for s in cw.shifts:
        if not s.works_on(day):
            continue
        start, real = _at(day, s.start, tz)
        latest, _ = _at(day, s.latest, tz)
        if not real:
            # A START THE CLOCKS SKIPPED moves to the change and keeps its whole window, so the
            # shift still gets the minutes it was written with. Measured in real time.
            h1, m1 = map(int, s.start.split(":"))
            h2, m2 = map(int, s.latest.split(":"))
            length = timedelta(minutes=(h2 * 60 + m2) - (h1 * 60 + m1))
            latest = max(_utc(latest), _utc(start) + length).astimezone(tz)
        out.append(Slot(key=contract.slot_key(cw.slug, day, s.start), coworker=cw.slug,
                        start=start, latest=latest))
    return out


def _utc(d: datetime) -> datetime:
    # Aware datetimes in a zoneinfo zone compare by wall clock when they share the zone, which is
    # wrong across a daylight-saving change. Every comparison here is made in UTC.
    return d.astimezone(timezone.utc)


def plan(coworkers, *, now: datetime, since: datetime | None, taken) -> Plan:
    """What the tick should do at `now`. `since` is when it last ran (None: never).

    `taken` is every slot key that already has a run row, whatever its status.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware, in the box's timezone")
    tz = now.tzinfo
    n = _utc(now)
    # A first tick has nothing it could have missed. A long outage looks back LOOKBACK at most.
    floor = _utc(since) if since is not None else n
    floor = max(floor, n - LOOKBACK)

    first = (floor.astimezone(tz)).date() - timedelta(days=1)
    days = [first + timedelta(days=i) for i in range((now.date() - first).days + LOOKAHEAD_DAYS)]
    slots = [s for cw in coworkers for d in days for s in slots_on(cw, d, tz)]

    taken = set(taken)
    due, missed, upcoming = [], [], []
    for s in slots:
        if s.key in taken:
            continue
        st, lt = _utc(s.start), _utc(s.latest)
        if st <= n <= lt:
            due.append(s)
        elif floor <= lt < n:
            missed.append(s)
        elif st > n:
            upcoming.append(s)

    due.sort(key=lambda s: (_utc(s.latest), _utc(s.start), s.coworker))
    missed.sort(key=lambda s: (_utc(s.latest), s.coworker))
    starts = [s.start for s in due] + [s.start for s in upcoming]
    nxt = min(starts, key=_utc) if starts else None
    return Plan(due=tuple(due), missed=tuple(missed), next_start=nxt)
