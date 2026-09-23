"""What release this box runs, when it last looked for a newer one, and whether it is current.

WHY A BUYER NEEDS THIS PAGE. We sell a box that keeps getting better, and a buyer had no way to
see that happening: the dashboard showed three sections and not one of them said what release the
machine was on or that anything was arriving. A product that improves invisibly is, to the person
paying for it, a product that does not improve. Owner, 2026-09-23, made it demo-critical.

EVERYTHING HERE IS READ, and every fact comes from something the update path already writes:

    /var/lib/aios/release        the tag box_update.sh records after each verified install
    /var/lib/aios/updates.jsonl  one JSON line per decision, written by core/release/update.py
    HEAD's own release tag       read by core.release.update.installed_release()

Nothing is fetched, nothing is verified again, and no network is touched. This page must answer on
a box whose update check is BROKEN — that is the state a buyer most needs it to answer in.

THE STATES, AND WHY MOST OF THEM ARE NOT FAILURES. A box installed an hour ago has never checked,
and saying "out of date" would be a lie told to somebody who did nothing wrong. A box that found a
newer release is mid-update, not broken. Only two states here are anybody's problem: every release
refused verification, and the check could not run at all. Getting this backwards is how a person
learns to ignore the one screen that would have warned them.

STALE IS ITS OWN ANSWER. The timer runs at 08:00 and 20:00 UTC with up to three hours of spread,
so the worst honest gap is about fifteen hours. Past thirty — two missed windows — the box is not
checking, and reporting "up to date" from a check that old is a confident answer built on a stopped
clock.
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

REPO = pathlib.Path(os.environ.get("AIOS_REPO") or "/opt/aios")
UPDATES_LOG = pathlib.Path(os.environ.get("AIOS_UPDATES_LOG") or "/var/lib/aios/updates.jsonl")

# Two missed windows of a twice-daily timer plus its spread. See the module docstring.
STALE_AFTER_S = 30 * 3600


def _last_decision() -> dict | None:
    """The newest line of the update log, or None.

    READ FROM THE END AND TOLERANT OF A BAD LINE. The file is appended to by a shell script and by
    Python; a truncated final line (a box powered off mid-write) must cost the newest good entry,
    not the whole page.
    """
    try:
        lines = [ln for ln in UPDATES_LOG.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            entry = json.loads(ln)
        except ValueError:
            continue
        if isinstance(entry, dict):
            return entry
    return None


def _age_s(iso: str | None) -> int | None:
    """Seconds since `iso`, or None for UNKNOWN — never a sentinel number somebody could average."""
    try:
        return int((datetime.now(timezone.utc) - datetime.fromisoformat(
            str(iso).replace("Z", "+00:00"))).total_seconds())
    except Exception:                                    # noqa: BLE001 — a bad timestamp is not fatal
        return None


def _installed() -> str | None:
    try:
        from core.release.update import installed_release
        return installed_release(REPO)
    except Exception as e:                               # noqa: BLE001 — no git, no repo, no page break
        log.warning("box_updates.release_unreadable", error=type(e).__name__)
        return None


def state() -> dict:
    """Everything the screen needs, as typed fields. Never raises.

    `ok` is THREE-VALUED on purpose: True (current), False (a person needs to look), and None for
    every honest "we do not know yet" — never checked, never installed, or a check too old to
    believe. A green light computed from a stopped clock is the one answer worse than no answer.
    """
    release = _installed()
    d = _last_decision() or {}
    checked_at = d.get("at")
    age = _age_s(checked_at)
    status = str(d.get("status") or "")
    stale = age is not None and age > STALE_AFTER_S

    out = {
        "release": release,
        "checked_at": checked_at,
        "checked_s_ago": age,
        "stale": stale,
        "source_status": status or None,
    }

    if not d:
        out.update(state="never_checked", ok=None,
                   said="This box has not looked for an update yet. It checks twice a day on its "
                        "own; the first check happens within about half a day of setup.")
        return out

    if status == "up_to_date":
        out.update(state="current", ok=None if stale else True,
                   said="You are on the newest release.")
    elif status == "selected":
        # THE INSTALL RUNS IMMEDIATELY AFTER THE CHOICE, in the same script, and writes the new tag
        # to the marker. So a chosen tag that MATCHES what is installed means the update landed;
        # one that does not means it was chosen and has not landed, which is a different sentence.
        chosen = d.get("selected")
        if chosen and release and chosen == release:
            out.update(state="current", ok=None if stale else True,
                       said=f"You are on the newest release. It installed on its own when "
                            f"{chosen} was published.")
        else:
            out.update(state="updating", ok=None,
                       said=f"A newer release ({chosen or 'one'}) has been found and is being "
                            f"installed. Nothing for you to do.")
    elif status == "all_refused" and _reasons(d) == {"collision"}:
        clash = _detail_of(d, "collision")
        out.update(state="collision", ok=False, blocking=clash,
                   said="A newer release is ready, but a file somebody added on this box is where "
                        "that release needs to put one of its own"
                        + (f" ({clash})" if clash else "") + ". Move it into my/ or rename it, and "
                        "the next check installs the update. Nothing has been changed.")
    elif status == "all_refused" and _reasons(d) == {"dirty_tree"}:
        # EDITS THAT HAVE SINCE BEEN PUT BACK. The live check below takes over while they are still
        # there; this is the gap between putting them back and the next twice-daily check.
        out.update(state="waiting", ok=None,
                   said="The last check found files on this box had been changed, so it waited. "
                        "They are back as they came now; the next check installs the update.")
    elif status == "install_failed":
        out.update(state="install_failed", ok=False,
                   said=f"The last update ({d.get('tag') or 'a newer release'}) could not be "
                        f"installed, so this box stayed on the release it had. Nothing changed. "
                        f"Worth telling us about.")
    elif status == "all_refused":
        # THE ONLY STATE THAT IS GENUINELY SOMEBODY'S PROBLEM. A newer release exists and could not
        # be trusted; the box deliberately stayed where it was, which is correct and must be said
        # out loud rather than shown as a healthy "up to date".
        out.update(state="refused", ok=False,
                   said="A newer release was offered but could not be verified, so this box "
                        "stayed on the one it trusts. That is the safe outcome, and it is worth "
                        "telling us about.")
    elif status == "rolled_back":
        out.update(state="rolled_back", ok=False,
                   said=f"An update was installed and then undone, so this box is back on "
                        f"{d.get('to') or 'its previous release'}. Worth telling us about.")
    elif status == "cannot_run":
        out.update(state="cannot_check", ok=False,
                   said="This box could not check for updates. It is still running the release "
                        "it has; nothing has changed.")
    else:
        # A STATUS THIS VERSION DOES NOT KNOW is reported as unknown, never as healthy. An older
        # page reading a newer box's log must not translate a word it cannot read into a tick.
        out.update(state="unknown", ok=None,
                   said="This box reported an update result this page does not recognise.")

    # MANAGED ENDED OUTRANKS EVERYTHING (owner, 2026-09-23: updates come with Managed, and stop at the
    # end of the period already paid for). The box can no longer fetch releases, so every state above
    # would read as a failure — "could not check" — when the truth is a plan that ended.
    p = plan()
    if p.get("updates") == "off":
        when = _day(p.get("until"))
        out.update(state="no_managed", ok=None,
                   said=("Updates come with Ownbox Managed" + (f", which ended on {when}" if when else
                         ", and this box is not on it") + ". It keeps running exactly as it is — it is "
                         "yours — but it no longer receives updates. Resume Managed to get them again."))
        return out

    # EDITED FILES OUTRANK EVERYTHING ABOVE, and are read LIVE rather than from the last check. An
    # edit made an hour ago will stop the next update whatever the last log line says, and the
    # person who made it deserves to hear that now, from the page, not in twelve hours. This used to
    # surface as "could not be verified … worth telling us about" — a security-shaped sentence
    # pointing at us, for a change only the box's own people could undo (#1472 R3).
    edited = changed_files()
    if edited:
        out.update(state="edited", ok=False, changed=edited,
                   said=f"{len(edited)} {'file' if len(edited) == 1 else 'files'} that came with "
                        f"this box {'has' if len(edited) == 1 else 'have'} been changed on it, so it "
                        f"will not install updates until {'it is' if len(edited) == 1 else 'they are'} "
                        f"back as {'it' if len(edited) == 1 else 'they'} came. Nothing of yours is "
                        f"lost, and {'it is' if len(edited) == 1 else 'they are'} listed below.")
    if stale and out["ok"] is not False:
        out["said"] += (" Its last check was more than a day ago, though, so that may no longer "
                        "be true — the twice-daily check may not be running.")
    if release is None:
        out["ok"] = None
        out["said"] = ("This box cannot tell which release it is on. " + out["said"])
    return out


# ONE SENTENCE, IN ONE PLACE, because it is a promise about how the product behaves and it must
# read the same wherever it appears.
HOW_UPDATES_ARRIVE = (
    "Updates come with Ownbox Managed, and they arrive on their own. Twice a day this box asks for "
    "the newest release, checks that it "
    "was signed by us, and installs it — your settings, your keys and your data are never touched. "
    "If a release cannot be verified, the box stays exactly where it is.")


def _reasons(d: dict) -> set:
    return {str(r.get("reason") or "") for r in (d.get("refused") or []) if isinstance(r, dict)}


def _detail_of(d: dict, reason: str) -> str:
    for r in d.get("refused") or []:
        if isinstance(r, dict) and r.get("reason") == reason:
            return str(r.get("detail") or "")[:160]
    return ""


def _git(*args: str) -> str:
    import subprocess
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {r.stderr.strip()[:160]}")
    return r.stdout


def changed_files() -> list[str]:
    """Files that came with this box and have been changed on it. [] when none, or when unreadable.

    THE SAME QUESTION `core/release/verify.py` ASKS before every install (`git status`, tracked
    files only), asked here so the page and the updater can never disagree about it. Files somebody
    ADDED are not in it: those do not stop updates.
    """
    try:
        out = _git("status", "--porcelain", "--untracked-files=no")
    except Exception:                                    # noqa: BLE001 — no git, no repo: say nothing
        return []
    names = []
    for ln in out.splitlines():
        if len(ln) > 3:
            path = ln[3:].split(" -> ")[-1].strip().strip('"')
            if path and path not in names:
                names.append(path)
    return names


PUT_ASIDE = "my/put-aside"


def put_back(*, by: str = "") -> dict:
    """Save every change to the box's own files as a patch under my/, then put the files back.

    -> {"ok", "saved", "restored", "said"}. NEVER DELETES ANYTHING (owner: "Don't delete anything
    ever"). The order is the safety:

      1. the whole change is written to `my/put-aside/<when>.patch` — `my/` is never in a release,
         so an update cannot touch it and it does not itself stop updates;
      2. git is asked whether that patch, reversed, applies to the box as it is now — proof the
         file holds the change, all of it, before a single file is restored;
      3. only then are the files restored to the release, one path at a time. A file somebody
         created and ADDED to git is unstaged, not removed: it stays on disk, simply untracked.

    If step 1 or 2 fails, nothing is restored and the page says why.
    """
    import datetime as _dt

    files = changed_files()
    if not files:
        return {"ok": True, "saved": "", "restored": [], "said": "Nothing to put back — the box's "
                "files are as they came."}
    try:
        patch = _git("diff", "HEAD", "--binary", "--no-renames")
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "saved": "", "restored": [],
                "said": f"The changes could not be read, so nothing was touched ({e})."}
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = REPO / PUT_ASIDE
    target = folder / f"{stamp}.patch"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(patch)
        if target.read_text() != patch or not patch.strip():
            raise OSError("the saved copy does not match the change")
        _git("apply", "--check", "--reverse", str(target))
    except Exception as e:                               # noqa: BLE001
        return {"ok": False, "saved": "", "restored": [],
                "said": f"The changes could not be saved safely, so nothing was touched ({e})."}
    rel = f"{PUT_ASIDE}/{stamp}.patch"
    restored = []
    try:
        for path in [p for p in _git("diff", "HEAD", "--name-only", "--no-renames").splitlines() if p]:
            in_release = True
            try:
                _git("cat-file", "-e", f"HEAD:{path}")
            except Exception:                            # noqa: BLE001 — not in the release
                in_release = False
            if in_release:
                _git("checkout", "HEAD", "--", path)
            else:
                _git("rm", "--cached", "--quiet", "--", path)
            restored.append(path)
    except Exception as e:                               # noqa: BLE001
        log.error("box_updates.put_back_partial", by=by, error=str(e)[:160])
        return {"ok": False, "saved": rel, "restored": restored,
                "said": f"Your changes are saved in {rel}, but not every file could be put back "
                        f"({e}). Nothing is lost."}
    left = changed_files()
    log.info("box_updates.put_back", by=by, files=len(restored), saved=rel)
    if left:
        return {"ok": False, "saved": rel, "restored": restored,
                "said": f"Your changes are saved in {rel}. {len(left)} file(s) are still changed "
                        f"and need looking at: {', '.join(left[:5])}."}
    return {"ok": True, "saved": rel, "restored": restored,
            "said": f"Done. Your changes are saved in {rel} and the box's files are as they came, "
                    f"so the next check installs the update. To use your changes again later, "
                    f"build them into a machine of your own in my/ rather than into these files."}


# ── the plan this box is on (#1472 R9) ──────────────────────────────────────────────────────────

_PLAN_MACHINE, _PLAN_KEY = "core", "updates_plan"


def plan() -> dict:
    """What the provisioner last told this box about updates: {"updates": "on"|"off", "until"}.
    {} when it has said nothing — which is every box until the rule is switched on, and reads as ON."""
    try:
        from core import box_settings
        got = box_settings.get(_PLAN_MACHINE, _PLAN_KEY, default={})
        return got if isinstance(got, dict) else {}
    except Exception:                                    # noqa: BLE001 — unknown is not "off"
        return {}


def set_plan(updates: str, until: str = "") -> None:
    """Store what the provisioner says. Only "on" and "off" are accepted; anything else is refused.
    This only EXPLAINS: what stops updates is the key removed on Ownbox's side."""
    if updates not in ("on", "off"):
        raise ValueError(f"unknown updates plan {updates!r}")
    until = str(until or "")[:40]
    if until:
        datetime.fromisoformat(until.replace("Z", "+00:00"))   # a date or nothing — raises otherwise
    from core import box_settings
    box_settings.put(_PLAN_MACHINE, _PLAN_KEY, {"updates": updates, "until": until}, set_by="provisioner")


def _day(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%-d %B %Y")
    except (TypeError, ValueError):
        return ""
