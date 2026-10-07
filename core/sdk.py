"""The box's promise to anyone building their own machine: `from core import sdk` (sdk: 1).

docs/SCOPE_MACHINE_MARKETPLACE.md §9 (owner-approved 2026-09-29, SDK v0). A company that builds a
machine for itself, in `my/machines/`, imports THIS and nothing else from the box. Everything here is
promised: it keeps working, with the same names and arguments, for every box on `sdk: 1`, and if an
update breaks it that is our bug. Everything else in `core/` is ours to change in any update, and
`python scripts/ownbox.py check` says so the moment a machine reaches for it.

VERSIONED ON ITS OWN. `VERSION` moves only when a promise here changes, never with the foundation
(core/FOUNDATION_VERSION), which moves whenever the box grows. A machine says `sdk: 1` in its
machine.yaml; a box whose facade is older than that refuses the machine by name instead of letting it
half-start (core/custom_machines.py).

WHY THIN WRAPPERS, NOT RE-EXPORTS. A re-export promises the whole signature of the thing behind it,
including every keyword we add next month. Each function here takes exactly the arguments we promise
and passes the rest itself, so the seam behind it can grow without a company's machine noticing.

WHAT IS PROMISED (§9.2), AND WHAT IS NOT:
  * reasoning        m.think(task, prompt): the only way to use AI, on this box's account and under
                     its monthly spend ceiling (core.brain.think)
  * the morning page m.reporter(title, fn)
  * tools            m.tool(name, fn=, description=, capability=, args=, render=): a question this box can
                     answer for the owner's AI (core.connector.tools); `render(result)` is the answer in
                     words, and m.link(path) a full address to put in it. Coworkers need no code: a
                     machine ships them as files, `coworkers/<name>/coworker.yaml` (coworker: 1).
  * the menu         m.menu(key, title=, href=): only those three; the menu's look is ours
  * screens          m.screen(path): a signed-in page on the box's own chrome, and STYLE_CLASSES.
                     Signed-in is the default: a screen nobody gates is a page the internet can read.
  * small state      m.setting / m.save_setting (core.box_settings), and m.data_dir() for the
                     machine's own SQLite file inside its own folder
  * scheduled jobs   m.every(seconds, fn): at most every 15 seconds, in the worker, on its own thread
                     under a time budget; a job that fails is reported, never stops the box
                     (core.machine_jobs). Added in SDK 1, owner 2026-10-01.
  * panels           m.panel(slot, title=, render=): a card on another machine's screen, in a slot
                     that screen offers; one that fails shows a quiet card (core.panels). Added in
                     SDK 1, owner 2026-10-01.
  * conversations    m.claim / m.release / m.messages / m.send_dm: work with the box's inbox through
                     its own send path and gates (core.conversations). A conversation a machine claims
                     isn't drafted or counted as waiting by the inbox. Added in SDK 1, owner 2026-10-01.
  * the manifest     machine.yaml: name, version, requires_foundation, needs:, sdk
  NOT promised: worker.register / register_periodic (use m.every; a job on the worker's own loop
  holds up every other one), state.register_schema (a company's tables would block our migrations:
  use m.data_dir()), and any other `core.*` import.
"""
from __future__ import annotations

import functools
import pathlib
import re

from core.logging import get_logger

log = get_logger(__name__)

VERSION = 1

# The manifest fields a machine may rely on, and the seams above by name. `scripts/ownbox.py check`
# and core/machine_breaks.py read these, so there is one list of what we promised.
MANIFEST_FIELDS = ("name", "version", "requires_foundation", "needs", "sdk")
SEAMS = ("machine", "think", "reporter", "tool", "menu", "screen", "page", "setting",
         "save_setting", "data_dir", "every", "panel", "claim", "release", "messages", "send_dm",
         "comments", "reply_to_comment", "conversation_for", "follows_you", "person", "touch", "secret",
         "STYLE_CLASSES", "VERSION")

# The style classes a screen may use. Our markup changes; these names keep their meaning.
STYLE_CLASSES = ("card", "quiet", "addr", "consent")

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _key(slug: str) -> str:
    """The registries' key form of a machine's slug: `job-tracker` → `my_job_tracker`.

    YOUR OWN MACHINES LIVE IN THEIR OWN CORNER (owner, 2026-10-01: "ownbox new should have some sort of
    prefix"). Every name a machine of your own takes on the box (its settings, its Morning Review part, its
    tools, its menu row) starts `my_`, and every page lives under `/my/<slug>`. Official add-on machines keep
    the plain names, so a machine of yours can never take a name an add-on needs, and becoming an official
    add-on later is one deliberate move to the plain names, never a collision."""
    return "my_" + slug.replace("-", "_")


def _home(slug: str) -> str:
    """Where every page of this machine lives: `/my/<slug>`."""
    return f"/my/{slug}"


def page(path: str, *, title: str, lede: str, body: str) -> str:
    """The box's own page around `body`: its menu, a title, one plain sentence, then your cards."""
    from core.dash.home import chrome
    return chrome(path, title=title, lede=lede, body=body)


def _space(space: str | None) -> str | None:
    """The Space a person belongs to: the one named, or this box's only one. None when the box runs several and
    none was named, so a person is never filed under a business it doesn't belong to."""
    from core import spaces
    names = [s["name"] for s in spaces.all_spaces() if isinstance(s, dict) and s.get("name")]
    if space:
        return space if space in names else None
    return names[0] if len(names) == 1 else None


class Machine:
    """One machine's handle on the box. Get it once, at the top of `__init__.py`:

        from core import sdk
        m = sdk.machine("job-tracker")      # the folder's own name
    """

    def __init__(self, slug: str):
        if not _SLUG.match(str(slug or "")):
            raise ValueError(f"machine name {slug!r} must be lowercase letters, digits and hyphens, "
                             f"and the same as its folder's name")
        self.slug = slug
        self.key = _key(slug)
        self.home = _home(slug)                  # `m.home`: this machine's own address on the box
        from flask import Blueprint
        # The box mounts a package's `blueprint`; `__init__.py` sets `blueprint = m.blueprint`.
        self.blueprint = Blueprint(self.key, f"my_machines.{slug.replace('-', '_')}")

    def _own(self, path: str, what: str) -> str:
        """`path` if it is this machine's own address or below it; else the one-sentence fix."""
        path = str(path or "")
        if path == self.home or path.startswith(self.home + "/") or path.startswith(self.home + "?"):
            return path
        raise ValueError(f"{what} {path!r} is outside this machine's own address: every page of a machine of "
                         f"your own lives under {self.home} (use m.home), so it never takes a name an "
                         f"official add-on needs")

    # ── reasoning ─────────────────────────────────────────────────────────────────────────────
    def think(self, task: str, prompt: str, *, system: str | None = None,
              max_tokens: int = 1024) -> str:
        """Ask the box's AI. Metered on this box's account and refused past its monthly ceiling, so
        it can raise when the box is out of budget: catch it where a page must still answer."""
        from core import brain
        return brain.think(task, prompt, system=system, max_tokens=max_tokens, machine=self.key)

    # ── the morning page ──────────────────────────────────────────────────────────────────────
    def reporter(self, title: str, fn) -> None:
        """`fn(day)` returns this machine's part of the owner's Morning Review for that day."""
        from core import report
        report.register_reporter(self.key, title, fn)

    def day_window(self, day) -> tuple[str, str]:
        """The UTC bounds `(start, end)` of one of the box's LOCAL days: the `day` a reporter is handed.

        EVERY TIMESTAMP IS STORED UTC, AND THE REVIEW'S DAY IS THE OWNER'S. Count a row as that day's when
        `start <= stamp < end`, never by its UTC date: on a Pacific box a UTC date is the next day from 5 PM on, so
        Lead Magnet's evening captures landed in tomorrow's review (WebDev2's walk, 2026-10-07)."""
        from core import report
        return report.window(day)

    # ── tools for the owner's AI ──────────────────────────────────────────────────────────────
    def tool(self, name: str, *, fn, description: str, capability: str,
             args: dict | None = None, title: str | None = None, render=None) -> None:
        """One question the owner's AI may ask this box. `capability` is `read:<noun>` for a
        question that changes nothing; the box decides who may ask it. `title` is what the owner
        reads when their AI asks permission ("Read your latest notes"); without one, the box makes
        one from the name. Added in SDK 1 without breaking anything built on it: it is optional.

        `render(result)` returns THE ANSWER IN WORDS: what the owner's AI shows as it is, before the
        data (#1857 F3). Every tool of the box's own has one; a tool without one hands the AI raw
        fields to explain, and it explains them worse. `python scripts/ownbox.py check` warns on a
        tool without one now, and will refuse it in the next release. Optional in SDK 1."""
        from core.connector import tools
        tools.register(name, fn=fn, description=description, machine=self.key,
                       capability=capability, args=args, title=title, render=render)

    def link(self, path: str) -> str:
        """A FULL address on this box for one of this machine's pages (`https://<box>/my/...`), for an
        answer in words: a bare path is a dead end in a chat."""
        from core.connector import words
        return words.link(self._own(path, "link"))

    # ── the menu ──────────────────────────────────────────────────────────────────────────────
    def menu(self, key: str, *, title: str, href: str) -> None:
        """One row in the box's menu, pointing at one of this machine's own pages (under `m.home`). Where it sits
        and how it looks are the box's to decide."""
        from core import shell
        key = key if str(key).startswith("my_") else "my_" + str(key)
        shell.register_section(key, order=90, machine=self.key, title=title, href=self._own(href, "menu link"))

    # ── screens ───────────────────────────────────────────────────────────────────────────────
    def screen(self, path: str, *, owner_only: bool = False, methods=("GET",)):
        """A page of this machine, behind the box's own sign-in, at `m.home` or below it. Return `sdk.page(...)`.

            @m.screen(m.home)                    # /my/job-tracker
            def home():
                return sdk.page(m.home, title="Jobs", lede="...", body='<div class="card">…</div>')
        """
        path = self._own(path, "screen")

        def wrap(fn):
            @functools.wraps(fn)
            def gated(*a, **kw):
                from core.dash import review
                refuse = review._admit(owner_only=owner_only)
                return refuse if refuse is not None else fn(*a, **kw)
            self.blueprint.add_url_rule(path, endpoint=fn.__name__, view_func=gated,
                                        methods=list(methods))
            return gated
        return wrap

    # ── small state ───────────────────────────────────────────────────────────────────────────
    def setting(self, key: str, default=None):
        """A small value this machine saved (a choice, a list, a date). Never raises."""
        from core import box_settings
        return box_settings.get(self.key, key, default=default)

    def save_setting(self, key: str, value, *, by: str = "") -> None:
        from core import box_settings
        box_settings.put(self.key, key, value, set_by=by or self.slug)

    # ── secrets ───────────────────────────────────────────────────────────────────────────────
    def secret(self, name: str, *, label: str | None = None, help: str = "") -> str:
        """A key this machine needs (an API token), or "" until the owner saves one.

        Declare it once, at the top of `__init__.py`, with the words the owner reads:

            m.secret("sanity_token", label="Sanity write token", help="Sanity → API → Tokens → Editor")

        and read it where you use it: `m.secret("sanity_token")`. The box gives your machine one Keys page,
        `m.keys_page` (owner only), with a field for each secret you declared; a saved value is never shown back.
        Never put a key in a setting, a file or your code: a setting is not a secret, and code is shared."""
        from core import machine_secrets
        if label is not None:
            machine_secrets.declare(self.key, name, label=label, help=help)
        return machine_secrets.get(self.key, name)

    @property
    def keys_page(self) -> str:
        """Where the owner saves this machine's secrets: link to it from your own screen."""
        return f"/settings/machines/{self.slug}/keys"

    # ── scheduled jobs ────────────────────────────────────────────────────────────────────────
    def every(self, seconds, fn=None, *, name: str | None = None):
        """Run `fn()` every `seconds` (15 or more) in the box's worker. Either form works:

            m.every(60, check_orders)

            @m.every(60)
            def check_orders(): ...

        Each run gets its own thread and a time budget; a run still going when the next is due is left to
        finish, never doubled. A run that raises is reported on the Add a Machine page and the next one goes
        ahead. The job is known as `<this machine>.<name>`; `name` defaults to the function's name. Keep
        your own progress in `m.data_dir()`, so a restart picks up where the last run stopped."""
        from core import machine_jobs
        if fn is None:
            def wrap(f):
                machine_jobs.register(self.key, seconds, f, name=name)
                return f
            return wrap
        machine_jobs.register(self.key, seconds, fn, name=name)
        return fn

    # ── panels on another machine's screen ────────────────────────────────────────────────────
    def panel(self, slot: str, *, title: str, render) -> None:
        """A card in `slot` on another machine's screen (that machine's guide names its slots). `render()`
        returns the card's HTML; the box draws the card and its `title`. It is shown to whoever that screen
        shows itself to. A render that raises shows "This section couldn't load", never a broken screen.
        Link to your own pages under `m.home` for anything more than a card."""
        from core import panels
        panels.register(slot, machine=self.key, title=title, render=render)

    # ── conversations in the box's inbox ──────────────────────────────────────────────────────
    def claim(self, conversation: str, *, title: str, days: float = 7, trigger: dict | None = None) -> bool:
        """Take charge of a conversation in the inbox. -> True if this machine holds it.

        While held, the inbox doesn't draft it, greet it or count it as waiting on a person; it shows it as
        "Handled by <title>". It is never taken from another machine. Calling again renews it; it lets go by
        itself after `days` (at most 30), so a stalled machine can't silence anyone for good.

        Once a PERSON replied and took it over, it is theirs: no machine claims it again, however the conversation
        goes on, until the owner hands it back, or the person starts again: pass `trigger`, the comment
        `m.comments()` returned for their NEW comment. The box checks it read that comment itself, that this person
        wrote it, and that it is newer than the takeover; anything else opens nothing."""
        from core import conversations
        kw = {}
        if trigger is not None:
            kw["trigger"] = str(trigger.get("id") or "") if isinstance(trigger, dict) else ""
        return bool(conversations.provider().claim(machine=self.key, title=title, conversation=conversation,
                                                   days=days, **kw))

    def release(self, conversation: str, *, note: str = "") -> bool:
        """Let go of a conversation. With a `note`, it is HANDED BACK: the note is shown to whoever answers it
        ("Asked about refunds while waiting for their email"). Without one, it simply finished."""
        from core import conversations
        return bool(conversations.provider().release(machine=self.key, conversation=conversation, note=note))

    def messages(self, conversation: str, *, since: str | None = None) -> list[dict]:
        """A conversation's messages, oldest first: {"id", "direction" ("in"/"out"), "sent_by", "body", "at"}.
        Pass the last `at` you saw as `since` to get only what is new."""
        from core import conversations
        return conversations.provider().messages(conversation=conversation, since=since)

    def send_dm(self, conversation: str, text: str, *, key: str, buttons: list | None = None,
                quick_replies: list | None = None) -> dict:
        """Send one message on a conversation this machine has claimed, through the inbox's own send path.

        `key` names the step ("ask_email"): the same key on the same conversation is never sent twice. The
        inbox refuses when a person has replied since you claimed it (the claim ends and the conversation is
        theirs: don't claim it again), the person opted out, the box is stopped, the channel's window is closed (on
        Instagram, 24 hours after their own message) or the hourly cap is reached. -> {"status": "sent" |
        "duplicate" | "refused" | "unknown", "message_id", "reason", "code"}. "unknown" means it may have gone:
        don't send it again. `reason` is a sentence for a person; decide in code on `code`, which a rewording
        never changes: "taken_over", "opted_out", "box_stopped", "window_closed", "hourly_cap", "not_claimed",
        "too_old", "not_ready" (try again soon), "unchecked", or "refused" for anything else.

        `buttons`: up to 3 links, each {"title": "Get the guide", "url": "https://…"} (titles cut at 20 characters).
        `quick_replies`: up to 13 titles ("I just followed you!"). A tap SENDS the title back as their message,
        which `m.messages` then shows you: that is how a conversation moves on without a webhook."""
        from core import conversations
        return conversations.provider().send(machine=self.key, conversation=conversation, text=text, key=key,
                                             buttons=buttons, quick_replies=quick_replies)

    def comments(self, *, post: str | None = None, since: str | None = None) -> list[dict]:
        """Recent comments on the box's Instagram account (or one `post`), read-only: {"id", "post", "account",
        "space", "text", "author": {"id", "username", "name"}, "at"}. Pass one back, unchanged, to
        `reply_to_comment`. `since` is an `at` a previous call returned: only newer comments come back, so a poll
        doesn't re-read everything. Replies under a comment and your account's own comments are left out. Never
        raises: a Space or a post the box can't read right now is skipped and logged, and comes back next time."""
        from core import conversations
        return conversations.provider().comments(post=post, since=since)

    def conversation_for(self, comment: dict) -> str | None:
        """The DM conversation with whoever wrote `comment`, or None until there is one (it appears once they
        write back or tap a quick reply). Matched on their account id and @handle, never a display name.
        Never raises: if the box can't look right now, it is None too."""
        from core import conversations
        return conversations.provider().conversation_for(comment=comment)

    def follows_you(self, conversation: str) -> bool | None:
        """Does the person on this conversation follow your account? True or False, or None when Instagram
        didn't say, which is common on a polled inbox (or the box couldn't look): decide what None means for your
        machine."""
        from core import conversations
        return conversations.provider().follows_you(conversation=conversation)

    def reply_to_comment(self, comment: dict, text: str, *, key: str, quick_replies: list | None = None) -> dict:
        """Answer a comment with a private message: the first message to someone who has never written to you,
        and the start of a conversation you can then `claim`. Instagram allows ONE private reply per comment,
        within 7 days of it; the inbox also refuses when the box is stopped, the person opted out, or the hourly
        cap is reached. Same answer as `send_dm`."""
        from core import conversations
        return conversations.provider().reply_to_comment(machine=self.key, comment=comment, text=text, key=key,
                                                         quick_replies=quick_replies)

    # ── ONE PERSON, EVERY MACHINE (docs/SCOPE_ONE_PERSON_RECORD.md): the two calls a machine needs, nothing more
    # (OSDev1's ruling A1 on docs/PLAN_OWNBOX_RUNS_ON_OWNBOX.md, 2026-10-02). A machine of your own adds the people it
    # meets and says what happened, and every other machine and the Morning Review see the same person.

    def person(self, kind: str, value, *, name: str = "", space: str | None = None) -> str | None:
        """The person behind an id, made the first time any machine meets them: `m.person("email", "ava@x.com")`.

        `kind` is how you know them: "email", "phone", "instagram", "messenger", "facebook", "prospect" or "web".
        Returns their person id, the same whichever machine asks, or None for an id that isn't one. Never raises.
        `space` matters only on a box that runs more than one; on one that does, name it, or nothing is recorded."""
        try:
            from core import people
            sp = _space(space)
            return people.identify(sp, str(kind or ""), value, machine=self.key, name=str(name or "")[:120]) \
                if sp else None
        except Exception as e:                           # noqa: BLE001 — the promise is "never raises"
            log.warning("sdk.person_failed", machine=self.key, error=type(e).__name__)
            return None

    def touch(self, person: str | None, kind: str, ref: str = "") -> bool:
        """Record what happened to this person: `m.touch(pid, "guide_sent", ref="reel-42")`.

        `kind` is lowercase letters and underscores ("email_sent", "booked"); a step or a campaign goes in `ref`.
        The event is filed with the person's own Space, so it never lands under another business. The same thing
        told twice is recorded once. Returns False for a bad call or a repeat. Never raises."""
        try:
            from core import people
            if not isinstance(person, str) or not isinstance(kind, str):
                return False
            who = people.person(person)
            if not who or not who.get("space"):
                return False
            return people.touch(who["space"], person, machine=self.key, kind=kind, ref=str(ref or ""))
        except Exception as e:                           # noqa: BLE001 — the promise is "never raises"
            log.warning("sdk.touch_failed", machine=self.key, error=type(e).__name__)
            return False

    def data_dir(self) -> pathlib.Path:
        """A folder of this machine's own, for its own SQLite file or anything larger than a setting.
        Inside its folder, so the machine stays one folder that can be copied to another box."""
        from core import custom_machines
        d = custom_machines.machines_dir() / self.slug / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d


def machine(slug: str) -> Machine:
    """This machine's handle: `m = sdk.machine("<the folder's name>")`."""
    return Machine(slug)
