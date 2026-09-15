#!/usr/bin/env python3
"""Reverse scripts/halt.py."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import pause
print("RESUMED — timers and jobs run again on their next tick" if pause.resume() else "was not paused")
