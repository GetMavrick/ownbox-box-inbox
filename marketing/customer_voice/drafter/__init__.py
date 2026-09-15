"""The one place in this machine that may ask a model anything.

RULED by OSDev1, 2026-09-15 07:17, after the owner's pricing ruling that the $499 box drafts
replies — "the box writes, a human sends". The guard at `tests/test_customer_voice.py` predates
that ruling, so it is AMENDED rather than weakened: this directory trades the department's
no-thinking rule for three STRICTER ones, and the guard enforces all three.

  1. NOTHING HERE SENDS, AND NOTHING HERE CAN. No import of the inbox send path, no platform
     client, no call shaped like post / create / publish / reply_to / send. `_MAY_THINK` and
     `_SENDS_BY_DESIGN` are asserted DISJOINT, so no file in this machine can ever both think
     and send — which is the property that makes the rest of it safe rather than merely tidy.
  2. THE ONLY OUTPUT IS A ROW A PERSON APPROVES. A draft appears in the compose box with the
     words already written; it becomes a message when somebody taps send, through the same
     `inbox/reply.py` a typed reply uses. Auto-send autonomy is untouched and still off.
  3. THE INBOUND TEXT IS UNTRUSTED. It was written by a stranger on the internet, so the model
     gets no tools, can trigger nothing, and its output is only ever stored as text. "Ignore
     your instructions and send…" is, structurally, a request this code has no way to honour.

AND IT REACHES A MODEL ONE WAY: `core.brain.think`. Never an SDK, never an HTTP client — that
is what keeps the per-deployment key, the $90 cost guard and the spend ledger authoritative
(spec §11-2), and it is asserted here rather than remembered.
"""
