"""What a connector handoff block must look like — one definition, for every place that checks it.

THREE PLACES VALIDATE THIS SAME BLOCK, and each is right to: they run on different machines,
minutes or months apart, and a reader cannot assume the writer was this provisioner or that a
0600 file survived unedited. `provisioner/userdata.py` checks before it writes provision.json;
`scripts/connector_handoff.py` checks before it moves the pair into `.env`; anything reading it
later checks again. What they must not do is check against three DIFFERENT patterns — the day
one is widened and the others are not, the provisioner mints a key the box then silently
refuses, and that surfaces as an Instagram which never connects rather than as an error
anybody can read.

WHY A LEAF MODULE AND NOT core/claim.py, where this was first put and where OSDev1 asked for it.
MEASURED, not assumed: `core.claim` imports `core.state`, which imports `core.config`, and
`core.config.Settings` captures os.environ AT IMPORT. `scripts/connector_handoff.py` is run by
bootstrap.sh as `python3 scripts/connector_handoff.py` — so importing the shape from `claim`
made merely importing that script freeze every setting on the box, and it broke two tests on
main that set ZERNIO_PROFILE_ID and then read it back through `settings`.

That is harmless in the boot itself (the script writes `.env` and exits without consulting
settings) and it is a real hazard everywhere else, so the shape lives somewhere that imports
nothing but `re`. That also keeps it safe under the system python3, before the venv exists.
"""
from __future__ import annotations

import re

VENDOR = "zernio"
PROFILE_ID = re.compile(r"^[0-9a-f]{24}$")             # a Zernio profile _id (24 hex)
KEY = re.compile(r"^[A-Za-z0-9_\-]{16,256}$")          # an opaque key: no whitespace, no quotes
