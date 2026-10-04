"""Answers in words: the small helpers every tool's `render` uses, so they all read the same.

OWNER, 2026-10-02, after asking his own AI for the Morning Review through this connector and getting a block of
stored numbers back: *"We sell AI Business machines… This is not an AI business machine. This is a dumb box."*
The only text a tool handed the AI was its record, serialised (docs/SCOPE_SMART_BOX_ANSWERS.md §1). A tool now
registers a `render` beside it, and its answer is written to be read: what needs the owner first, then what
happened, in plain words, with the full link to the page each fact comes from, ending with what to ask next and
what the box can start. The data still travels beside the words, untouched, for the AI to work with.

ONE PLACE FOR THE SHAPE, so twenty renders written by four people cannot drift into twenty voices. A render
builds its lines and hands them to `answer()`, `ask_next()` and `can_start()`; the links come from `link()`, the
times from `as_of()`.

NEVER OFFER WHAT NO TOOL CAN DO (the scope's §2). Every "Ask next" question and every "I can start" line names the
tool that answers it, and a line whose tool this connection can't use is dropped: a read connection is never
offered a send it is not allowed to ask for. The connection is the one the transport is answering
(`seat_scope`); a render run outside one (a test, a script) offers every tool the box has.

NOTHING HERE REASONS. Pure string work over stored facts, the same rule as the registry (CLAUDE.md section 11-6).
"""
from __future__ import annotations

import contextlib
import contextvars
import re
from datetime import datetime, timezone

from core.logging import get_logger

log = get_logger(__name__)

# WHO IS ASKING, while a render runs. A render takes only the result (`render(result) -> str`), so the transport
# says who the answer is for here, and `usable()` reads it. The same pattern as `approvals.decider()`.
_SEAT: contextvars.ContextVar = contextvars.ContextVar("connector_words_seat", default=None)

ASK_NEXT = "Ask next:"
CAN_START = "I can start:"
APPROVALS = "/approvals"          # core/approvals.PAGE; the screen where every proposal waits for a person
MOST_ASKS = 3


@contextlib.contextmanager
def seat_scope(seat: dict | None):
    """Render for this seat: offers it can't use are left out."""
    token = _SEAT.set(seat)
    try:
        yield
    finally:
        _SEAT.reset(token)


def usable(tool: str) -> bool:
    """Can the connection being answered use `tool`? The registry's own two gates, never a list of names."""
    from core.connector import tools
    spec = tools.registry().get(tool)
    if spec is None:
        return False
    seat = _SEAT.get()
    if seat is None:
        return True
    return spec["capability"] in tools.held(seat) and tools.may(seat.get("role"), spec["min_role"])


# ── links ──────────────────────────────────────────────────────────────────────────────────────────

def base_url() -> str:
    """This box's own address, or "" when it can't be known.

    THE SAME SOURCE THE 8 AM MESSAGE USES (core/report.page_url): DASHBOARD_BASE_URL, which bootstrap writes as
    `https://<host>` on every box it builds. Then the host the box was provisioned as, then the address this very
    request came in on, which is the box (a connection reaches `https://<box>/mcp`). A bare path is better than a
    guessed host, so with none of the three the caller writes the path as it is."""
    try:
        from core.config import settings
        base = str(getattr(settings, "dashboard_base_url", "") or "").strip().rstrip("/")
        if base.startswith(("https://", "http://")):
            return base
    except Exception:                                    # noqa: BLE001 — a link, never the answer
        pass
    try:
        from core import claim
        host = claim.provisioned_host()
        if host:
            return "https://" + host.strip().rstrip("/")
    except Exception:                                    # noqa: BLE001
        pass
    try:
        from flask import has_request_context, request
        if has_request_context():
            host = request.host or ""
            if host.startswith(("127.0.0.1", "localhost")):
                return str(request.host_url or "").rstrip("/")
            if host:
                return "https://" + host
    except Exception:                                    # noqa: BLE001
        pass
    return ""


def link(path: str) -> str:
    """A FULL address on this box for `path` (`/inbox/waiting` -> `https://<box>/inbox/waiting`).

    A bare path is a dead end in a chat: the AI can't open it and the owner can't tap it."""
    p = str(path or "").strip()
    if not p or p.startswith(("https://", "http://")):
        return p
    base = base_url()
    return f"{base}/{p.lstrip('/')}" if base else p


# ── time ───────────────────────────────────────────────────────────────────────────────────────────

def _tz():
    """The box's one timezone (`cost.timezone`, core/report.tz), or UTC."""
    try:
        from core import report
        return report.tz()
    except Exception:                                    # noqa: BLE001
        return timezone.utc


def _parse(iso) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _utc_label(tz) -> str:
    return " UTC" if str(getattr(tz, "key", tz)) in ("UTC", "Etc/UTC", "UTC+00:00") else ""


def clock(iso) -> str:
    """`1:54 PM`, on the box's clock; `Oct 1, 1:54 PM` when it isn't today. Labelled UTC when the box has no
    timezone set, so nobody reads a UTC time as their own."""
    d = _parse(iso)
    if d is None:
        return ""
    tz = _tz()
    local = d.astimezone(tz)
    today = datetime.now(tz).date()
    hm = local.strftime("%I:%M %p").lstrip("0")
    day = "" if local.date() == today else f"{local.strftime('%b')} {local.day}, "
    return f"{day}{hm}{_utc_label(tz)}"


def unavailable(vendor: str, when=None, *, own: bool = True) -> dict:
    """ONE RULE FOR EVERY TOOL THAT READS A VENDOR (#1857 H5): a vendor that does not answer never fails the answer.
    The result carries this under `unavailable`, and its render says the sentence, so every machine says it the same
    way. `own=False` when the box has nothing of its own to show instead.

    `vendor` is the name the buyer connected (PostHog, Google Search Console). The box's own AI is "The box's own AI":
    pages name no AI vendor (owner, 2026-10-03)."""
    t = when if isinstance(when, datetime) else (_parse(when) if when else datetime.now(timezone.utc))
    at = t.isoformat(timespec="seconds")
    tail = "; these are the box's own numbers." if own else ". It is asked again next time."
    return {"vendor": vendor, "at": at, "words": f"{vendor} didn't answer at {clock(at)}{tail}"}


def could_find_out(source_key: str) -> str:
    """"I could find out: connect Search Console: <link>", when a tool cannot answer for want of a source (#1857 F1).

    `source_key` is a source the box knows: "search_console" (the box's own Google sign-in), an app from the Data
    Sources ideas (core/connections/ideas.py, e.g. "posthog", "notion"), or a Data Sources card ("website"). The name
    and the address come from those, so the sentence names what the owner sees on the page. "" for anything else."""
    key = str(source_key or "")
    try:
        from core import source_cards
        from core.connections import ideas
        if key == "search_console":
            name, path = ideas.SEARCH_CONSOLE_IDEA[0], ideas.SEARCH_CONSOLE
        elif ideas.get(key):
            name, path = ideas.get(key)["name"], "/settings/sources"
        elif source_cards.get(key):
            name, path = source_cards.get(key)["title"], f"/settings/sources/{key}"
        else:
            return ""
    except Exception:                                    # noqa: BLE001 — a sentence is never worth an answer
        return ""
    return f"I could find out: connect {name}: {link(path)}"


def as_of(iso, stale: bool = False) -> str:
    """Freshness in words. `stale`: said, never hidden (core/report_tools.py: stale-and-labelled is survivable)."""
    when = clock(iso)
    if not when:
        return "This may be out of date." if stale else ""
    return f"As of {when}." + (" This may be out of date: the box has not refreshed it since then." if stale else "")


def ago(iso) -> str:
    """`3 hours ago`, `2 days ago`, `just now`; "" when there is no time."""
    d = _parse(iso)
    if d is None:
        return ""
    s = (datetime.now(timezone.utc) - d).total_seconds()
    if s < 90:
        return "just now"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= size:
            n = int(s // size)
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def day_words(day) -> str:
    """`2026-10-02` -> `Thu, Oct 2` (core/report.day_label, so the page and the answer label a day alike)."""
    try:
        from core import report
        return report.day_label(str(day))
    except Exception:                                    # noqa: BLE001
        return str(day or "")


# ── numbers and lines ─────────────────────────────────────────────────────────────────────────────

def n(v) -> str:
    """`1,204`; a float without its noise; anything else as text."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"
    return str(v if v is not None else "")


def plural(count, one: str, many: str | None = None) -> str:
    """`1 reply`, `22 replies`."""
    try:
        c = int(count)
    except (TypeError, ValueError):
        c = 0
    return f"{n(c)} {one if c == 1 else (many or one + 's')}"


def change(pct) -> str:
    """`up 231%`, `down 12%`, `the same as the week before`; "" when there was nothing before to compare."""
    if pct is None or isinstance(pct, bool):
        return ""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return ""
    if p == 0:
        return "the same as the week before"
    return f"{'up' if p > 0 else 'down'} {abs(p):,.0f}%"


def quoted(text, most: int = 140) -> str:
    """Somebody's words, in quotes, cut where a chat line should end."""
    t = " ".join(str(text or "").split())
    if len(t) > most:
        t = t[:most - 1].rstrip() + "…"
    return f"“{t}”"


def plain(text) -> str:
    """A sentence from somewhere else in the box, made safe to put in a line: no braces, one line, a capital.
    A line that opens with an address keeps it as written ("glowmedspa.com: 179 visits", not "Glowmedspa...")."""
    t = " ".join(str(text or "").replace("{", "(").replace("}", ")").split())
    if not t or "." in t.split(" ", 1)[0].rstrip(":,"):
        return t
    return t[:1].upper() + t[1:]


def bullets(lines) -> str:
    return "\n".join(f"- {x}" for x in lines if str(x or "").strip())


def section(heading: str, lines) -> str:
    """A heading and its bullets, or "" when there are none (a heading over nothing is noise)."""
    body = bullets(lines)
    return f"{heading}\n{body}" if body else ""


# ── what to ask next, what the box can start ─────────────────────────────────────────────────────

def _offered_pairs(pairs) -> list[tuple[str, str]]:
    """`(tool, words)` for each offer this connection can use, the words de-duplicated, in order."""
    seen, out = set(), []
    for tool, words in pairs or ():
        w = str(words or "").strip()
        if w and w not in seen and usable(tool):
            seen.add(w)
            out.append((tool, w))
    return out


def _offered(pairs) -> list:
    return [w for _, w in _offered_pairs(pairs)]


def ask_next(*pairs) -> str:
    """`Ask next:` and up to three questions, each `(tool that answers it, the question as a person asks it)`.

    NEVER EMPTY. Should every question name a tool this connection can't use, the box's own "what can you do"
    stands in, which every connection may ask: an answer always ends by saying where to go next."""
    asks = _offered(pairs)[:MOST_ASKS]
    if not asks:
        asks = ["What can my box do for me?"]
    return f"{ASK_NEXT}\n{bullets(asks)}"


def can_start(*pairs) -> str:
    """`I can start:` and what this connection may ask the box to do, each `(propose tool, the offer)`, or ""."""
    picked = _offered_pairs(pairs)[:MOST_ASKS]
    if picked:
        _remember_offers([tool for tool, _ in picked])
    return f"{CAN_START}\n{bullets([w for _, w in picked])}" if picked else ""


# WHAT A QUESTION'S OWN AI WAS OFFERED (OSDev1, 2026-10-04; H2's mark 4, "I can start"). The daily check found core.ask
# leaving out a task that a tool it had just read offered, e.g. aeo.searches' "Write an article on your top search".
# So while the box's AI answers a question on its own seat ("Ask ask_<id>", core/ask.py), every offer a tool shows it
# is kept, and the answer carries them when the AI's own reply names none. Tool names only, never a customer's words.
_ASK_SEAT_LABEL = "Ask ask_"


def _remember_offers(tool_names) -> None:
    seat = _SEAT.get()
    if not seat or not str(seat.get("label") or "").startswith(_ASK_SEAT_LABEL):
        return
    try:
        from core import ask
        ask.note_offers(str(seat.get("id") or ""), tool_names)
    except Exception as e:                                  # noqa: BLE001 — an answer never fails on bookkeeping
        log.warning("words.offers_not_kept", error=type(e).__name__)


def approve_line(what: str) -> str:
    """`<what>. You approve it on Approvals: <link>` — the one way every offer is put."""
    return f"{what.rstrip('.')}. You approve it on Approvals: {link(APPROVALS)}"


def answer(*parts) -> str:
    """The parts, blank-line separated, empty ones dropped."""
    return "\n\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


# ── proposals ─────────────────────────────────────────────────────────────────────────────────────

def _approval_title(aid) -> str:
    try:
        from core import approvals
        row = approvals.get(str(aid)) or {}
        return str(row.get("title") or "")
    except Exception:                                    # noqa: BLE001 — the sentence still stands
        return ""


def proposal(result: dict, *asks) -> str:
    """Any propose_* tool's answer: what now waits on Approvals, with its link, or why nothing was asked.

    THE SAME WORDS FOR EVERY MACHINE'S PROPOSALS, because the owner meets them all on one screen."""
    r = result if isinstance(result, dict) else {}
    if r.get("asked") and r.get("approval"):
        title = _approval_title(r["approval"]) or "your request"
        head = (f"This was already waiting on Approvals: {quoted(title, 120)}. Nothing has changed yet."
                if r.get("repeat") else
                f"Waiting on Approvals: {quoted(title, 120)}. Nothing has changed yet, and nothing will until you "
                f"approve it.")
        body = [head, f"Approve or decline it here: {link(APPROVALS)}"]
        skipped = r.get("skipped") or []
        if skipped:
            body.append("One of them had no written reply waiting, so it was left out." if len(skipped) == 1 else
                        f"{n(len(skipped))} of them had no written reply waiting, so they were left out.")
        return answer("\n".join(body), ask_next(*asks))
    why = r.get("error") or r.get("note") or r.get("text") or "the box did not say why"
    return answer(f"Nothing was asked for. {plain(why).rstrip('.')}.", ask_next(*asks))


# ── a Morning Review segment's follow-ons, offered by the machine that wrote the segment ─────────────
# core/report_tools.py renders the whole review and must not know what any machine's numbers mean (core names no
# machine: tests/test_core_boundary.py). So a machine says, beside its own tools, what its segment lets the owner
# ask next and start: `review_offers("<segment machine>", fn)`, fn(segment) -> {"ask": [(tool, question)],
# "start": [(tool, offer)]}.
_REVIEW_OFFERS: dict = {}


def review_offers(machine: str, fn) -> None:
    if not callable(fn):
        raise ValueError(f"review_offers({machine!r}) needs a callable")
    _REVIEW_OFFERS[str(machine)] = fn


def offers_for(segment: dict) -> dict:
    """{"ask": [...], "start": [...]} for one stored segment; empty when its machine offers none or fails."""
    fn = _REVIEW_OFFERS.get(str((segment or {}).get("machine") or ""))
    if fn is None:
        return {"ask": [], "start": []}
    try:
        got = fn(segment) or {}
        return {"ask": list(got.get("ask") or []), "start": list(got.get("start") or [])}
    except Exception as e:                               # noqa: BLE001 — one machine's offer, never the answer
        log.warning("connector.review_offer_failed", machine=segment.get("machine"), error=type(e).__name__)
        return {"ask": [], "start": []}


_FIRST_NUMBER = re.compile(r"\d[\d,]*")


def first_number(text) -> str:
    """The first number in a line, without commas ("120 waiting" -> "120"), or ""."""
    m = _FIRST_NUMBER.search(str(text or ""))
    return m.group(0).replace(",", "") if m else ""
