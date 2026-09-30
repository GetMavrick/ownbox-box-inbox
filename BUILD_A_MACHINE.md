# Build your own machine

This box is yours, and you can teach it new jobs. A **machine** is one job the box does, with its own
screens, its own work and its own data. It uses the same AI account as the rest of the box.

You build it in one place, **`my/machines/`**, on one promise: **`from core import sdk`**. Everything
in `sdk` keeps working, with the same names, on every box on `sdk: 1`. If an update ever breaks it,
that is ours to fix. Anything else in the box's code can change in any update.

If you build with an AI agent, show it this page first.

## Start in one command

```bash
python scripts/ownbox.py new job-tracker     # a working machine in my/machines/job-tracker/
python scripts/ownbox.py check job-tracker   # is it built only on what the box promises?
sudo systemctl restart aios-dispatch aios-worker
```

The starter already has a menu row, a signed-in screen, a line on the Morning Review and a question
your AI can ask. Change it from there, and run `check` after every change.

## The two rules

1. **Build in `my/machines/`. Never edit the box's own files.** Every update leaves `my/` alone. If
   one of the box's own files changes, updates pause until it is put back. `git status` in the box's
   folder lists any such change.
2. **Use only `from core import sdk`.** `check` names anything else, and says what to use instead.

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
requires_foundation: "1.2" # the box version it was built on
needs: []                  # plan features it needs, like [coworkers]
sdk: 1                     # the promise it is built on
```

## Everything the box promises (`sdk: 1`)

Get your machine's handle once, at the top of `__init__.py`, and do everything through it:

```python
from core import sdk
m = sdk.machine("job-tracker")        # the folder's own name
blueprint = m.blueprint               # the box mounts this when it starts
```

| To… | Use |
|---|---|
| use AI | `m.think(task, prompt)`. **Always this, never an AI company's library.** It runs on the box's one AI account, under its monthly ceiling. |
| add a row to the menu | `m.menu(key, title=, href=)`. The `key` is lowercase letters, digits and underscores. |
| add a screen | `@m.screen("/path")` on a function that returns `sdk.page(path, title=, lede=, body=)`. Screens are behind the box's sign-in; `owner_only=True` limits one to the owner. |
| style a screen | the classes in `sdk.STYLE_CLASSES`: `card`, `quiet`, `addr`, `consent`. |
| put a line on the Morning Review | `m.reporter(title, fn)`. `fn(day)` returns `{}` on a quiet day, or a `title`, a `headline` and what `happened`. |
| answer your AI's questions | `m.tool(name, fn=, description=, capability="read:<noun>")` |
| keep a small value | `m.setting(key, default)` and `m.save_setting(key, value)` |
| keep more than that | your own SQLite file in `m.data_dir()`, inside your machine's folder |
| run work on a schedule | a **coworker**: ship it as `coworkers/<name>/coworker.yaml` in your folder, and hire it on the Shifts page (Pro). |

A tiny working example:

```python
# my/machines/job-tracker/__init__.py
from core import sdk

m = sdk.machine("job-tracker")
blueprint = m.blueprint
m.menu("job_tracker", title="Job Tracker", href="/job-tracker")


@m.screen("/job-tracker")
def home():
    return sdk.page("/job-tracker", title="Job Tracker", lede="Your jobs, on your box.",
                    body='<div class="card"><p>Your jobs will show up here.</p></div>')
```

Restart the box's services (`sudo systemctl restart aios-dispatch aios-worker`) and the new row
appears in the menu.

**Not promised yet**, so `check` flags them: the box's own background workers and its database
tables. Use a coworker for scheduled work, and your own SQLite file for data, so our updates can
never collide with yours.

## When something goes wrong

A machine that fails to start never takes the box down. The box skips it, keeps everything else
running, and says why on the **Add a Machine** page. You'll see one of these:

- **Not started, with an error**: the error and the file it came from. Fix it and restart.
- **`machine.yaml` is missing or wrong**: the page names the field.
- **Needs a newer box**: `requires_foundation` or `sdk` is higher than this box has. Let the next
  update arrive, then restart.
- **Stopped by an update**: the page says which one, and whose fix it is. A machine built only on
  `sdk` was broken by us, so tell us. One that reached past `sdk` is told what it used, and how to
  stop.

## What we support

We support the box, our machines and the `sdk` promise. **A machine you build is yours.** While the
box is on Managed we keep it updated, and no update changes what is in `my/`.
