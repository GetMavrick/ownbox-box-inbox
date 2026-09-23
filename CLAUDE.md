# CLAUDE.md — how this system works, and the rules it does not break

This file is read by Claude Code every time it opens this folder. It is the constitution of
your system: the layout, the invariants, and the handful of rules that stop an agent doing
something expensive or irreversible.

**It comes with the box, and updates replace it — do not edit it.** Your own rules for your agents
go in `my/CLAUDE.md`, which is read below and which no update ever touches:

@my/CLAUDE.md

## The one rule that keeps updates coming

Every file that came with this box belongs to its release. **If you or an agent edit one, the box
stops installing updates** — safely, nothing is lost, but it stays on the release it has until the
file is put back. Before you finish any change, run:

```bash
git status --untracked-files=no
```

It must list nothing. If it lists files, either move that work into `my/`, or open **System
Settings → Updates** on the dashboard, which names the files and can put them back (your changes
are saved to `my/put-aside/` first, never deleted).

Everything that is yours lives in `my/`, and updates never touch it:

| What | Where |
|---|---|
| Your settings — any key in `config/aios.config.yaml`, overridden | `my/settings.yaml` |
| Facts about your business the brain should know | `my/knowledge/` |
| Your rules for your agents | `my/CLAUDE.md` |
| Your own machines | `my/machines/<name>/` |

---

## What you have

An **operating system for a company**, not a script. Four parts:

| Part | What it is |
|---|---|
| `core/` | The kernel. Config, database, the worker that runs everything, the spend guard, the brain |
| `core/brain.py` | **The shared brain.** One reasoning gateway on your key. Every machine calls it |
| `marketing/` | A **department**. It holds machines |
| `my/` | Yours. Your voice, your settings, your machines. An update from us never touches it |

Departments are the top level, and they are ours: `marketing/` today, and more as we ship them.
Your own work does not go in a department — it goes in `my/machines/`, where an update can never
collide with it.

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

It runs on **your** `ANTHROPIC_API_KEY`. To change `brain.backend`, set it in `my/settings.yaml` —
never in `config/aios.config.yaml`, which comes with the box.

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

Build your own machines. Yours live in **`my/machines/<name>/`**, one self-contained folder each,
with nothing of it anywhere else. A machine folder needs two things, or the box skips it and says
why on the Add a Machine page:

- **`machine.yaml`**, with `name`, `version` and `requires_foundation`. The `name` is lowercase
  letters, digits and hyphens, and it must be the folder's own name.
- **`__init__.py`**, which registers what the machine does when it is loaded.

That folder is what keeps your machine from ever stopping an update, and it is what lets you copy
a machine to another box later. The full walkthrough is `BUILD_A_MACHINE.md` in this folder, once
your box has the update that loads machines.

**Never wire a machine in by editing `config/aios.config.yaml`** or any other file that came with
the box: that is the edit that stops updates. The box loads machines from `my/machines/` by
itself; if the release you are on does not do that yet, an update will, and this section is
replaced with the exact steps when it does.

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
