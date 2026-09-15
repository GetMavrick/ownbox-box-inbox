# CLAUDE.md — how this system works, and the rules it does not break

This file is read by Claude Code every time it opens this folder. It is the constitution of
your system: the layout, the invariants, and the handful of rules that stop an agent doing
something expensive or irreversible. Edit it as your system grows — it is yours now.

---

## What you have

An **operating system for a company**, not a script. Four parts:

| Part | What it is |
|---|---|
| `core/` | The kernel. Config, database, the worker that runs everything, the spend guard, the brain |
| `core/brain.py` | **The shared brain.** One reasoning gateway on your key. Every machine calls it |
| `marketing/` | A **department**. It holds machines |
| `my/` | Yours. Your voice, your settings. An update from us never touches it |

Departments are the top level: `marketing/` today, and `operations/`, `finance/`,
`customer_service/` when you have a machine to put in one. **Do not create an empty
department** — a folder with nothing in it is a promise the system does not keep.

A department holds **machines**. A machine holds sub-machines and `plugins/`.

```
your-aios/
  CLAUDE.md          this file — the rules
  DEVSTATE.md        the wall — how your agents talk to each other
  my/                your voice, your settings, your brand brief
  core/              kernel + the shared brain
  marketing/         a department
    lead_machine/    a machine
      plugins/       what you bolt on to it
  tests/             proof that it still works
```

## The shared brain

`core/brain.py` is the single place anything reasons. Every machine calls `think()`; nothing
calls an AI vendor directly. That is what makes spend countable, failures retryable, and the
model swappable in one line of config.

It runs on **your** `ANTHROPIC_API_KEY`. Set `brain.backend` in `config/aios.config.yaml`.

Every `.md` in **`my/knowledge/`** rides on every call it makes, in every machine — the facts
about your business (the brand brief is the voice). Keep the folder small; it is cached, not free.

## The wall

`DEVSTATE.md` is how two agents working on this system tell each other things. Read it
before you start; post to it when you learn something. The format is enforced by
`.claude/scripts/devstate-post.sh`, and the rules are at the top of the wall itself.

If you only ever take one habit from this system, take this one: **when something silently
did the wrong thing, post a GOTCHA.** That is the class of bug that costs weeks.

## Command and control

You are the operator. Claude Code sessions are your agents. Give each a role name, have them
post under it, and keep the hierarchy shallow: one agent that reviews and merges, others that
build. The wall is the only channel between them — never relay through yourself, because a
message you forget to pass on is a message that was never sent.

## The kill switch

`python scripts/halt.py` — timers skip and no job is claimed, within seconds, until
`python scripts/resume.py`. It is a file (`data/PAUSED`), so it survives a restart and works the
same on a laptop and a server. The cost guard halts the box when money runs out; this halts
it because you said so.

## The rules that do not bend

1. **The brain is the only door to a model.** No machine calls a vendor SDK directly.
2. **Meter every vendor call.** `cost_guard.check_vendor()` before, `record_vendor_usage()`
   after, with an idempotency key so a retry cannot double-charge you.
3. **Fail closed on anything outward-facing.** A send with no unsubscribe link, no sender
   identity, or no signing key must refuse — not warn, refuse.
4. **Secrets live in `.env`, never in the repo.** The shipped `.gitignore` enforces it. Check
   `git status` before your first push and confirm `.env` is not listed.
5. **Timers ship off.** Turning one on is a deliberate act, done after you have watched the
   lane run by hand.
6. **A guard that cannot fail is not a guard.** When you write a check, break the thing it
   watches and confirm it goes red. A scanner that silently matches nothing reports success
   forever.

## Adding a machine

A machine is a package under a department — a folder with an `__init__.py` that registers
what it does when the worker loads it. Add it to `modules:` in `config/aios.config.yaml` and
the worker picks it up on the next start. Nothing else has to know it exists.

The rule that makes this safe is one-way dependency: **a new machine may import the kernel
and the door it was given, and nothing else.** No machine reaches into another machine's
internals. That is what lets you add one without touching the one that already works.


## Proving it still works

```bash
.venv/bin/python tests/test_schema_integrity.py   # the database is whole
.venv/bin/python tests/test_plug_contract.py      # the door holds  (Lead Machine / AIOS)
.venv/bin/python tests/test_kernel.py             # the worker runs (Content Machine / AIOS)
```

Run them before you trust a change, and again after. Green means the system you have is the
system you built.
