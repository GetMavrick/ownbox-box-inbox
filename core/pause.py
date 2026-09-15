"""The kill switch. `data/PAUSED` exists → nothing runs; remove it → everything resumes.

A file, not a flag in memory: it survives a worker restart, it works from a laptop or a
VPS with the same two commands, and a nervous operator can `ls data/` and know. The cost
guard halts the box when money runs out; this halts it because a human said so.

  python scripts/halt.py     "stop everything now" — timers skip, jobs are not claimed
  python scripts/resume.py   reverse it
"""
import datetime as _dt
import os
import pathlib

from core.config import ROOT

MARKER = pathlib.Path(os.environ.get("AIOS_PAUSE_FILE", str(ROOT / "data" / "PAUSED")))


def is_paused() -> bool:
    return MARKER.exists()


def halt(reason: str = "operator") -> pathlib.Path:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(f"{_dt.datetime.now(_dt.timezone.utc).isoformat()} {reason}\n")
    return MARKER


def resume() -> bool:
    if MARKER.exists():
        MARKER.unlink()
        return True
    return False


def status() -> str:
    return f"PAUSED since {MARKER.read_text().strip()}" if MARKER.exists() else "running"
