"""Google Places (New) — a VENDOR client, shared, not a machine's private helper.

Moved out of `marketing/lead_machine/` on 2026-09-09 because Customer Voice needs it and could
not have it: a Lead box ships `PKGS="lead_machine"` and a Content box ships `content_machine`,
so a machine importing another machine's client is a machine that does not exist on half the
boxes it ships to. `core/vendors/` is where the other three already live (prospect_lake,
waalaxy, zernio) and this is the same thing.

NOTHING ABOUT THE CLIENT CHANGED. Same field masks, same exclusion-only paring, same metering
into `google_places`, same error contract. `marketing.lead_machine.places_client` is an ALIAS
for this module — the identical object, not a copy — so every existing caller and every test
that monkeypatches it by name keeps working unchanged.
"""
from .client import *          # noqa: F401,F403 — the surface is the client's
from . import client           # noqa: F401 — `from core.vendors import places; places.client`
