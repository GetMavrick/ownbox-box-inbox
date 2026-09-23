# Build your own machine

This box is yours, and you can teach it new jobs. A **machine** is one job the box does, with its own
screens, its own work in the background and its own data. It uses the same AI account as the rest
of the box.

You build it in one place, **`my/machines/`**, and a machine there never stops this box taking our
updates. Updates come with Managed support.

## The one rule

**Build in `my/machines/`. Never edit the box's own files.**

- Every update we ship leaves `my/` alone, so nothing you put there can be overwritten or collide
  with something of ours.
- If any of the box's own files change, updates pause until they are put back. Your data and
  settings are never touched either way.
- To check, run `git status` in the box's folder. Anything it lists outside `my/` is a change to
  the box's own files.

If you build with an AI agent, show it this page first.

## What a machine looks like

One folder, named after the machine. Use lowercase letters, digits and hyphens:

```
my/machines/
  job-tracker/
    machine.yaml     # what it is
    __init__.py      # what it does: runs when the box starts
    ...              # anything else it needs, inside this folder only
```

`machine.yaml`:

```yaml
name: job-tracker          # must match the folder name
version: 0.1.0
requires_foundation: "1.1" # the box version it was built for
```

Keep everything inside the folder. A self-contained folder can be copied to another box, or shared
later, without changes.

## Plugging in

`__init__.py` runs when the box starts. It hooks into the box through the same places our own
machines use:

| To add… | Use |
|---|---|
| a row in the menu | `core.shell.register_section(key, order=, machine=, title=, href=, items=[...])`. The `key` uses lowercase letters, digits and underscores. |
| a screen | a Flask `Blueprint` named `blueprint` in `__init__.py`. The box mounts it. |
| background work | `core.worker.register(intent, handler)` and `core.worker.register_periodic(fn, interval_s=, name=)` |
| its own tables | `core.state.register_schema(machine, ddl)` |
| an entry in the morning report | `core.report.register_reporter(machine, title, fn)` |
| anything that needs AI | `core.brain.think(task=, prompt=, job_id=)`. **Always this, never an AI company's library directly.** It keeps every AI cost on the box's one meter and under its monthly ceiling. |

A tiny working example:

```python
# my/machines/job-tracker/__init__.py
from flask import Blueprint
from core import shell

shell.register_section("job_tracker", order=70, machine="job-tracker",
                       title="Job Tracker", href="/job-tracker")

blueprint = Blueprint("job_tracker", __name__)

@blueprint.get("/job-tracker")
def home():
    return "Your jobs will show up here."
```

Restart the box's services (`sudo systemctl restart aios-dispatch aios-worker`) and the new row
appears in the menu.

## When something goes wrong

A machine that fails to start never takes the box down. The box skips it, keeps everything else
running and records why. Look for `custom_machine.` in the box's log. You will see one of:

- **Starts with an error**: the error and the file it came from are logged. Fix it and restart.
- **`machine.yaml` is missing or wrong**: the log names the field.
- **Needs a newer box**: `requires_foundation` is higher than this box's version. Let the next
  update arrive, then restart.

## What we support

We support the box and our machines. **A machine you build is yours.** While the box is on Managed
support we keep it updated, and no update changes what is in `my/`.
