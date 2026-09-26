"""When shifts are due (core/coworkers/schedule.py): the clockwork on a fixed calendar.

Every rule in docs/SCOPE_SHIFTS.md §3 that is about TIME is pinned here with a fake clock:
windows are inclusive, due slots queue earliest-latest-first, a taken slot never comes back,
a slot is MISSED only if its latest fell since the last tick (so an outage reports each miss once
and a coworker switched on late is not blamed), a long outage looks back two days at most, and
07:30 stays 07:30 on the wall across daylight saving.

Run: python tests/test_coworker_schedule.py
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.coworkers import contract as C  # noqa: E402
from core.coworkers import schedule as S  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


PT = ZoneInfo("America/Los_Angeles")


def cw(slug, shifts, enabled=True):
    c, why = C.parse({"coworker": 1, "title": slug, "job": "job.md", "from": "my", "may": [],
                      "shifts": shifts, "enabled": enabled}, slug)
    assert c is not None, why
    return c


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=PT)


FRONT = cw("front-desk", [{"days": "Mon-Fri", "start": "07:30", "latest": "07:45"},
                          {"days": "Mon-Fri", "start": "17:00", "latest": "17:15"}])
MON = (2026, 9, 28)     # a Monday

print("a window is due from its start to its latest, inclusive")
for hm, want in (((7, 29), False), ((7, 30), True), ((7, 45), True), ((7, 46), False)):
    p = S.plan([FRONT], now=at(*MON, *hm), since=at(*MON, *hm) - timedelta(minutes=1), taken=())
    ok(f"at {hm[0]:02d}:{hm[1]:02d} due is {want}", bool(p.due) is want, str(p.due))
p = S.plan([FRONT], now=at(*MON, 7, 31), since=at(*MON, 7, 30), taken=())
ok("the due slot carries its key, coworker and window",
   p.due[0].key == "front-desk@2026-09-28T07:30" and p.due[0].start == at(*MON, 7, 30)
   and p.due[0].latest == at(*MON, 7, 45))
ok("a Saturday has no shifts", S.plan([FRONT], now=at(2026, 9, 26, 7, 31),
                                      since=at(2026, 9, 26, 7, 30), taken=()).due == ())
off = cw("off", [{"days": "Daily", "start": "07:30", "latest": "07:45"}], enabled=False)
p = S.plan([off], now=at(*MON, 7, 31), since=at(*MON, 6, 0), taken=())
ok("a coworker that is not enabled is never due, missed or next",
   p.due == () and p.missed == () and p.next_start is None)

print("due slots queue earliest latest first")
a = cw("a-long", [{"days": "Daily", "start": "09:00", "latest": "09:30"}])
b = cw("b-short", [{"days": "Daily", "start": "09:00", "latest": "09:10"}])
c = cw("c-mid", [{"days": "Daily", "start": "08:55", "latest": "09:20"}])
p = S.plan([a, b, c], now=at(*MON, 9, 1), since=at(*MON, 9, 0), taken=())
ok("the one that would miss soonest goes first",
   [s.coworker for s in p.due] == ["b-short", "c-mid", "a-long"], str([s.coworker for s in p.due]))
p = S.plan([a, b, c], now=at(*MON, 9, 1), since=at(*MON, 9, 0),
           taken={"b-short@2026-09-28T09:00"})
ok("a taken slot is never due again", [s.coworker for s in p.due] == ["c-mid", "a-long"])

print("MISSED: exactly the slots whose latest fell since the last tick")
p = S.plan([FRONT], now=at(*MON, 7, 46), since=at(*MON, 7, 45), taken=())
ok("untaken at its latest, then the next tick: MISSED",
   [s.key for s in p.missed] == ["front-desk@2026-09-28T07:30"], str(p.missed))
p = S.plan([FRONT], now=at(*MON, 7, 47), since=at(*MON, 7, 46), taken=())
ok("and reported only once", p.missed == ())
p = S.plan([FRONT], now=at(*MON, 7, 46), since=at(*MON, 7, 45),
           taken={"front-desk@2026-09-28T07:30"})
ok("a slot that ran is never missed", p.missed == ())
p = S.plan([FRONT], now=at(*MON, 18, 0), since=at(*MON, 6, 0), taken=())
ok("a box off from 06:00 to 18:00 reports both shifts it slept through, in order",
   [s.key for s in p.missed] == ["front-desk@2026-09-28T07:30", "front-desk@2026-09-28T17:00"])
p = S.plan([FRONT], now=at(*MON, 10, 0), since=at(*MON, 9, 59), taken=())
ok("a coworker first seen at 10:00 is not blamed for 07:30", p.missed == ())
p = S.plan([FRONT], now=at(*MON, 10, 0), since=None, taken=())
ok("the very first tick misses nothing", p.missed == ())
p = S.plan([FRONT], now=at(2026, 10, 9, 8, 0), since=at(2026, 9, 28, 6, 0), taken=())
ok("an outage of eleven days looks back two, no more",
   {s.key[11:21] for s in p.missed} == {"2026-10-07", "2026-10-08", "2026-10-09"}
   and all(s.latest >= at(2026, 10, 9, 8, 0) - S.LOOKBACK for s in p.missed),
   str(sorted({s.key[11:21] for s in p.missed})))

print("the next window, for update coordination")
p = S.plan([FRONT], now=at(*MON, 8, 0), since=at(*MON, 7, 59), taken={"front-desk@2026-09-28T07:30"})
ok("after the morning shift, the next start is 17:00", p.next_start == at(*MON, 17, 0))
p = S.plan([FRONT], now=at(*MON, 7, 35), since=at(*MON, 7, 34), taken=())
ok("while a window is open and untaken, its start is the next start (the update waits)",
   p.next_start == at(*MON, 7, 30))
p = S.plan([FRONT], now=at(2026, 10, 2, 18, 0), since=at(2026, 10, 2, 17, 59),
           taken={"front-desk@2026-10-02T17:00"})
ok("on Friday evening, the next start is Monday 07:30", p.next_start == at(2026, 10, 5, 7, 30),
   str(p.next_start))
weekly = cw("weekly", [{"days": "Wed", "start": "10:00", "latest": "10:30"}])
p = S.plan([weekly], now=at(2026, 9, 30, 11, 0), since=at(2026, 9, 30, 10, 59),
           taken={"weekly@2026-09-30T10:00"})
ok("a once-a-week shift that just ran finds next week's", p.next_start == at(2026, 10, 7, 10, 0),
   str(p.next_start))

print("daylight saving: the wall clock is what counts")
# US daylight saving ends Sunday 2026-11-01. Friday is PDT (UTC-7), the next Monday PST (UTC-8).
fri = S.plan([FRONT], now=at(2026, 10, 30, 7, 31), since=at(2026, 10, 30, 7, 30), taken=())
mon = S.plan([FRONT], now=at(2026, 11, 2, 7, 31), since=at(2026, 11, 2, 7, 30), taken=())
ok("07:30 on the Friday is 14:30 UTC", fri.due[0].start.astimezone(timezone.utc).hour == 14)
ok("07:30 on the Monday after is 15:30 UTC, and still due at 07:31 on the wall",
   mon.due and mon.due[0].start.astimezone(timezone.utc).hour == 15)
p = S.plan([FRONT], now=at(2026, 11, 2, 7, 46), since=at(2026, 10, 30, 18, 0),
           taken={"front-desk@2026-10-30T07:30", "front-desk@2026-10-30T17:00"})
ok("a weekend outage across the change misses only Monday's 07:30",
   [s.key for s in p.missed] == ["front-desk@2026-11-02T07:30"], str(p.missed))
# US daylight saving starts Sunday 2026-03-08: at 02:00 PST the clocks jump to 03:00 PDT, so
# 02:30 never happens that night. A shift written for 02:30 starts at the change, whole.
NIGHT = cw("night-audit", [{"days": "Daily", "start": "02:30", "latest": "03:00"}])
[gap] = S.slots_on(NIGHT, datetime(2026, 3, 8).date(), PT)
ok("a start the clocks skip is the moment they change: 03:00 PDT",
   gap.start == datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc), gap.start.isoformat())
ok("and it keeps its whole 30-minute window, so it is never started after its latest",
   gap.latest - gap.start == timedelta(minutes=30), f"{gap.start} .. {gap.latest}")
p = S.plan([NIGHT], now=datetime(2026, 3, 8, 3, 1, tzinfo=PT), since=datetime(2026, 3, 8, 3, 0,
                                                                                tzinfo=PT), taken=())
ok("so a tick just after the change starts it, and misses nothing",
   [x.key for x in p.due] == ["night-audit@2026-03-08T02:30"] and p.missed == (), str(p))
[normal] = S.slots_on(NIGHT, datetime(2026, 3, 9).date(), PT)
ok("the next night is its ordinary 02:30 to 03:00",
   normal.latest - normal.start == timedelta(minutes=30) and normal.start.hour == 2)

try:
    S.plan([FRONT], now=datetime(2026, 9, 28, 7, 31), since=None, taken=())
    ok("a naive clock is refused", False)
except ValueError:
    ok("a naive clock is refused", True)

print()
if _failed:
    print(f"{_failed} FAILED")
    sys.exit(1)
print("ALL OK")
