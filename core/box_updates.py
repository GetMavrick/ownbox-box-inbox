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
    "Updates arrive on their own. Twice a day this box asks for the newest release, checks that it "
    "was signed by us, and installs it — your settings, your keys and your data are never touched. "
    "If a release cannot be verified, the box stays exactly where it is.")
