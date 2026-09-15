#!/usr/bin/env python3
"""Stop everything now. Timers skip, jobs are not claimed, nothing sends or posts.
Reverse with scripts/resume.py. The worker may stay up; it idles."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import pause
p = pause.halt(" ".join(sys.argv[1:]) or "operator")
print(f"HALTED — marker at {p}\n  nothing runs until you: .venv/bin/python scripts/resume.py")
