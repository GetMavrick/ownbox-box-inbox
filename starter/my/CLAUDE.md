# my/CLAUDE.md — your rules for your agents

This file is yours. The box's own `CLAUDE.md` reads it on every session, and no update ever
touches it. Write here whatever your agents should always know about how you work: your
conventions, what they may and may not do, who reviews what.

A good first line to keep: **build in `my/`, and leave the box's own files as they came** — that is
what keeps updates arriving.

## Building a machine (read this before writing one)

- Start from the starter: `python scripts/ownbox.py new <name>` writes a working machine in
  `my/machines/<name>/`. The guide is `BUILD_A_MACHINE.md`.
- Import only **`from core import sdk`**. It is what the box promises to keep working (`sdk: 1`);
  anything else in the box's code can change in any update.
- Use AI only through `m.think(...)`, so every cost is on the box's one account and under its ceiling.
- After every change, run `python scripts/ownbox.py check <name>` and fix what it names; each
  finding says its fix in one sentence. Then run the machine's own test.

