"""The AEO Machine's old import name. It forwards and does nothing else.

The machine is `marketing/aeo_machine` (owner, 2026-09-25: "Yes, please code rename"). This name
stays only so a box that still lists it keeps booting: a box's own settings can carry a module list,
that list REPLACES the shipped one, and a name that vanished would stop its worker on the update
(OSDev1's condition for the rename, 2026-09-25). Delete this folder once no box lists the old name.
"""
from marketing.aeo_machine import *  # noqa: F401,F403 — importing it registers the machine's table and job
