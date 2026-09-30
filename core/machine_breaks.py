"""R4: when an update stops a machine the owner built, the owner is told (SCOPE_ADD_MACHINE_PAGE).

THE GAP THIS CLOSES. A machine in `my/machines/` is the buyer's own code, and it imports the box's
code. An update can change something it used, and then it no longer starts. R8 made sure that can
never take the box down (`custom_machines.load` never raises), which is right, and it also means the
box keeps running and says nothing: the owner's machine is simply gone from the menu after an update
they did not ask for. The Add a Machine page shows "Not started" if they happen to look. This makes
the box say so, once, and say which update did it.

WHY AFTER THE BOOT, AND NOT BEFORE THE INSTALL. The scope first proposed checking a machine's
`requires_foundation` before an update installs. That check can never fire: the rule is "the box's
foundation is at least what the machine needs" (`packs.requires_ok`), and a release's foundation
only goes up. What really stops a machine is an import that fails against the new code, and the
only honest place to see that is the first boot on the new release.

WHAT IS COMPARED. After each boot the worker records the release it booted and which of the owner's
machines started. On the next boot, a machine that started on the previous release and does not start
on this one was stopped BY THE UPDATE. That one is recorded with the release and the reason and
announced once. A machine that was already broken, or that the owner just added and got wrong, is
the owner's own edit and is not blamed on us. When a stopped machine starts again, its record goes.

ONE PROCESS WRITES THIS. The worker boots once per start (`worker.run_forever` → `load_modules`). The
web process runs two gunicorn workers, and both would announce the same break.

NEVER RAISES. It runs in the worker's boot, where one exception would stop every job on the box.
CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME: slugs here are data read from `my/machines/`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

NS = "core"                                 # box_settings namespace: the box's own records
LAST_BOOT = "custom_machines_last_boot"     # {"release": str, "ok": [slug, ...]}
STOPPED = "custom_machines_stopped"         # {slug: {"release", "reason", "since"}}
PUSH_TITLE = "Add a Machine"
PUSH_BODY = "An update stopped a machine you built. Open Add a Machine to see why."
NAVIGATE = "/add-machine"


def _release() -> str:
    try:
        from core import box_updates
        return box_updates._installed() or ""
    except Exception:                           # noqa: BLE001 — no release known: compare nothing
        return ""


def _get(key: str, default):
    from core import box_settings
    v = box_settings.get(NS, key, default=None)
    return v if isinstance(v, type(default)) else default


def _put(key: str, value) -> None:
    from core import box_settings
    box_settings.put(NS, key, value, set_by="box")


def stopped() -> dict:
    """{slug: {"release", "reason", "since", "promised"?, "unpromised"?}}: machines an update
    stopped, still not starting, and whether each was built only on what the box promises."""
    try:
        return _get(STOPPED, {})
    except Exception:                           # noqa: BLE001 — a page reads this; it must not 500
        return {}


def observe(results: list, *, release: str | None = None, notify=None) -> list:
    """Record this boot and announce what the update stopped. Returns the newly stopped slugs.

    `results` is what `custom_machines.load()` returned for this boot. Never raises.
    """
    try:
        release = _release() if release is None else release
        last = _get(LAST_BOOT, {})
        was_ok = set(last.get("ok") or [])
        before = str(last.get("release") or "")
        ok_now = sorted(r["slug"] for r in results if r.get("ok"))
        failing = {r["slug"]: str(r.get("reason") or "") for r in results if not r.get("ok")}

        rec = stopped()
        # A machine that starts again, or that the owner removed, is no longer stopped.
        rec = {s: v for s, v in rec.items() if s in failing}
        newly = []
        if release and before and release != before:
            for slug in sorted(failing):
                if slug in was_ok:
                    rec[slug] = {"release": release, "reason": failing[slug][:300],
                                 "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 **_whose(slug)}
                    newly.append(slug)
        _put(STOPPED, rec)
        # THE LAST BOOT IS WHAT IS COMPARED NEXT. A boot with no known release is recorded without
        # one, so the next boot compares nothing rather than something made up.
        _put(LAST_BOOT, {"release": release, "ok": ok_now})
        if newly:
            log.error("custom_machine.stopped_by_update", release=release, machines=newly,
                      previous=before)
            (notify or _notify)(newly, release)
        return newly
    except Exception as e:                      # noqa: BLE001 — see the module docstring
        log.error("machine_breaks.observe_failed", error=f"{type(e).__name__}: {e}"[:300])
        return []


def _whose(slug: str) -> dict:
    """WAS A PROMISE BROKEN? (docs/SCOPE_MACHINE_MARKETPLACE.md §9.2) A machine built only on
    `core.sdk` that an update stopped is OUR bug: we promised those seams. One that reached past the
    facade used something we never promised, and the first such thing, with its fix, is what the
    owner (or their AI) needs to read. The same rules as `scripts/ownbox.py check`.

    {"promised": True} | {"promised": False, "unpromised": "<problem>. Fix: <sentence>"} | {} unknown.
    """
    try:
        from core import custom_machines, sdk_check
        found = sdk_check.check(custom_machines.machines_dir() / slug)
    except Exception:                           # noqa: BLE001 — unknown is said as nothing, not a guess
        return {}
    if not found:
        return {"promised": True}
    first = found[0]
    return {"promised": False, "unpromised": f"{first['problem']}. Fix: {first['fix']}"[:300]}


def _notify(slugs: list, release: str) -> None:
    """One app notification to the owner's devices. A fixed sentence: it shows on a locked screen,
    so it names no machine. The page it opens does."""
    try:
        from core import push, state
        owner = state.owner_user()
        for sub in push.subscriptions_for(owner["id"]):
            push.send(sub, title=PUSH_TITLE, body=PUSH_BODY, navigate=NAVIGATE)
    except Exception as e:                      # noqa: BLE001 — the page still says it
        log.warning("machine_breaks.notify_failed", error=f"{type(e).__name__}: {e}"[:200])
