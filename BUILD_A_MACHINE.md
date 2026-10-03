# Build your own machine

This box is yours, and you can teach it new jobs. A **machine** is one job the box does, with its own
screens, its own work and its own data. It uses the same AI account as the rest of the box.

You build it in one place, **`my/machines/`**, on one promise: **`from core import sdk`**. Everything
in `sdk` keeps working, with the same names, on every box on `sdk: 1`. If an update ever breaks it,
that is ours to fix. Anything else in the box's code can change in any update.

If you build with an AI agent, show it this page first.

## Your machine's own corner

Every page of a machine of your own lives under `/my/<its name>` (`m.home`), and every name it takes on the box starts `my_`. Official add-on machines keep the plain names, so yours never takes a name an add-on needs, and the box refuses a page outside its corner with the fix in one sentence.

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
| add a screen | `@m.screen(m.home)` (or a path under it, like `m.home + "/settings"`) on a function that returns `sdk.page(path, title=, lede=, body=)`. Screens are behind the box's sign-in; `owner_only=True` limits one to the owner. |
| style a screen | the classes in `sdk.STYLE_CLASSES`: `card`, `quiet`, `addr`, `consent`. |
| put a line on the Morning Review | `m.reporter(title, fn)`. `fn(day)` returns `{}` on a quiet day, or a `title`, a `headline` and what `happened`. |
| answer your AI's questions | `m.tool(name, fn=, title=, description=, capability="read:<noun>", render=)`. The `title` is what you read when your AI asks permission, in plain words: "Read my job list". `render(result)` returns the answer in words, which your AI shows as it is; put `m.link(m.home)` in it so the answer ends with a link you can tap. `check` warns on a tool without `render=` now, and will refuse one in the next release. |
| keep a small value | `m.setting(key, default)` and `m.save_setting(key, value)` |
| keep more than that | your own SQLite file in `m.data_dir()`, inside your machine's folder |
| run work on a schedule | `m.every(seconds, fn)`: every 15 seconds or more, in the box's worker. Each run has its own thread and a time budget; a run that fails is shown on the Add a Machine page, and the next one goes ahead. Keep progress in `m.data_dir()`. For work an AI does on a shift (Pro), ship a **coworker** instead: `coworkers/<name>/coworker.yaml`, hired on the Coworkers page. |
| run conversations in the inbox | `m.claim(conversation, title=)` takes charge of one: the inbox stops drafting it and shows "Handled by <title>". `m.messages(conversation, since=)` reads it, `m.send_dm(conversation, text, key=, buttons=, quick_replies=)` answers through the inbox's own checks (opted out, Stop everything, the channel's 24-hour window, the hourly cap; one `key` is sent once), and `m.release(conversation, note=)` lets go, or hands it back to a person with a note. A quick reply's tap comes back as their message. Needs the Unified Inbox on the box. |
| start a conversation from a comment | `m.comments(post=)` reads recent Instagram comments; `m.reply_to_comment(comment, text, key=, quick_replies=)` answers one privately, which opens the conversation. `m.conversation_for(comment)` finds the commenter's DM conversation once they write back (or None), and `m.follows_you(conversation)` says True, False, or None when Instagram didn't say. One private reply per comment, within 7 days of it, never to someone who opted out, and not while the box is stopped. |
| know the people you meet | `m.person(kind, value)` gives the one person behind an email, a phone, an Instagram handle or a prospect id, the same id whichever machine asks; on a box that runs more than one business, add `space=`. `m.touch(person, kind, ref=)` says what happened ("guide_sent", "booked"), filed with that person's own business. Neither ever raises. |
| use a key for another service (an API token) | `m.secret(name, label=, help=)` declares it once; `m.secret(name)` reads it, or "" until it's saved. The owner saves it on your machine's Keys page, `m.keys_page` (owner only); a saved key is never shown back. Never put a key in a setting, a file or your code. |
| add a section to another machine's screen | `m.panel(slot, title=, render=)`. `render()` returns the card's HTML. A machine's guide names the slots its screens offer. If it raises, the card says it couldn't load and the screen is unharmed. Keep `render()` to a quick read of what you already have: the screen waits for it, so do the slow work in `m.every`. |

A tiny working example:

```python
# my/machines/job-tracker/__init__.py
from core import sdk

m = sdk.machine("job-tracker")
blueprint = m.blueprint
m.menu("job_tracker", title="Job Tracker", href=m.home)     # m.home is /my/job-tracker


@m.screen(m.home)
def home():
    return sdk.page(m.home, title="Job Tracker", lede="Your jobs, on your box.",
                    body='<div class="card"><p>Your jobs will show up here.</p></div>')
```

Restart the box's services (`sudo systemctl restart aios-dispatch aios-worker`) and the new row
appears in the menu.

**Not promised**, so `check` flags them: the box's own background workers and its database
tables. Use `m.every` for scheduled work, and your own SQLite file for data, so our updates can
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
