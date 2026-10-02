"""Today's brief: the Morning Review, thought through. The top things that matter, why, and what to do next.

docs/SCOPE_SMART_BOX_ANSWERS.md (#1839), Phase 1. Owner, 2026-10-02, after asking his AI for the Morning Review
over the connector and getting a block of stored numbers back: *"This is not an AI business machine. This is a
dumb box."* Then: *"Yes, let's make sure that the box is going to use inference and give the information and
then offer additional reports data points and tasks."* (D1). And D5, ruled the same day: a box with no AI signed
in answers in plain words, and says "sign in your AI on Settings for insights".

WRITTEN AHEAD, READ INSTANTLY. The worker writes the brief after each review snapshot (core/report.py `refresh`),
so `core.brief` never waits on an AI: an AI app gives a connector only so long to answer, and the one question
every owner asks first ("what's going on today?") must never be the one that times out.

  * The PLAIN brief is rebuilt whenever the stored numbers change. No AI: the facts in words, needs-you first.
  * The THOUGHT-THROUGH brief is the box's own AI ranking those facts: the top three, why each matters, what to
    do, what to ask next, and what the box can start. Only when the numbers moved since the last one, and at most
    once in 6 hours (BRIEF_EVERY_S), so never more than 4 a day, on the cheap model (config models.brief). The box's
    own AI sign-in pays, and it draws on the owner's own weekly limit, so the cadence is OSDev1's rule of 2026-10-02
    and is pinned in tests/test_smart_box_answers.py. Nothing per question to us (scope §7).

EVERY NUMBER IS THE BOX'S. The AI is given the stored rows and nothing else, and every number in what it writes
must appear in them (review_brief._grounded, the Morning Review's own rule); an item that fails is dropped, and a
brief left with nothing falls back to the plain one. "I can start" offers only a proposal tool this box really
has, by name, and a proposal still waits for a person's tap on Approvals.

SPEND IS NOT IN IT. The meters row is left out of the facts, so a brief read by any seat never carries the
box's spend (report_tools withholds it from a read seat, for the same reason).

THE SHAPE (`get()`, and what `core.brief` returns):
    {
      "text":       the brief in plain words, ready to read out (what an AI app shows),
      "as_of":      ISO time of the newest number in it,
      "built_at":   ISO time it was written,
      "from":       "ai" | "plain",
      "summary":    one sentence | "",
      "top":        [{"title", "why", "do", "href"}],   at most 3
      "needs_you":  [{"text", "href"}],
      "machines":   [{"title", "said"}],                one sentence per machine that has something to say
      "ask_next":   [str],                              at most 3
      "can_start":  [{"tool", "title", "why"}],         at most 3; each a real proposal tool
      "insights":   "" | the D5 line, when no AI is signed in,
      "ranked_at":  ISO time the AI ranked it, present only when the numbers moved since (see `get`)
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from core import report
from core.logging import get_logger

log = get_logger(__name__)

NS = "core"
KEY = "smart_brief"
AI_TASK = "brief"                          # config models: -> haiku
BRIEF_EVERY_S = 6 * 3600                   # the AI rewrites it at most this often (4 a day), only when numbers moved
TOP_MAX, ASK_MAX, START_MAX = 3, 3, 3
NO_AI = "Sign in your AI on Settings for insights"          # D5, the owner's ruling, 2026-10-02
_TITLE_MAX, _WHY_MAX, _ASK_LEN = 90, 280, 100

_SYSTEM = (
    "You are the AI inside a small business owner's own business machine. You are given today's and yesterday's "
    "stored numbers from each part of the business. Write the owner a short brief: what matters most today, why, "
    "and what to do. Rules: use ONLY the facts given; never invent a number, a name, a cause or an event. If you "
    "can't see why something happened, don't guess. Rank by what most needs the owner or most changed. Plain, "
    "warm words a busy owner reads in ten seconds; no jargon, no field names, no exclamation marks. Never use the "
    "words phone, ring, call, dial, line, voice or engine. "
    "Reply with JSON only: {\"summary\": \"one sentence\", \"top\": [{\"title\": \"under 90 characters\", "
    "\"why\": \"one or two sentences, with the number\", \"do\": \"one short next step\", \"href\": \"a link "
    "copied exactly from the facts, or empty\"}], \"ask_next\": [\"a question the owner could ask next that "
    "these facts' machines can answer\"], \"can_start\": [{\"tool\": \"a name copied exactly from "
    "startable\", \"why\": \"one sentence\"}]}. At most 3 top, 3 ask_next, 3 can_start; fewer is fine.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _base() -> str:
    from core.config import settings
    return (getattr(settings, "dashboard_base_url", "") or "").rstrip("/")


def _link(href) -> str:
    """A full link to the box, never a bare path: an AI app shows a path as text nobody can open."""
    h = str(href or "").strip()
    if not h:
        return ""
    if h.startswith("/"):
        return (_base() + h) if _base() else h
    return h if h.startswith("https://") or h.startswith("http://") else ""


def _when(iso: str) -> str:
    """'2:15 PM' on the box's own clock, or ''."""
    try:
        t = datetime.fromisoformat(str(iso)).astimezone(report.tz())
    except (TypeError, ValueError):
        return ""
    return t.strftime("%I:%M %p").lstrip("0")


# ── the facts: the stored rows, in words a model and a person can both read ─────────────────────────────────
def _value(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


def _machine_line(r: dict) -> str:
    """One machine in one line: its headline, then what happened. '' when it has nothing to say."""
    parts = []
    h = r.get("headline") or {}
    if report.has_value(h.get("value")) and str(h.get("label") or "").strip():
        d = h.get("delta")
        delta = (f" ({'+' if d > 0 else ''}{_value(d)} since yesterday)"
                 if isinstance(d, (int, float)) and not isinstance(d, bool) and d else "")
        parts.append(f"{_value(h['value'])} {str(h['label']).strip()}{delta}")
    for x in r.get("happened") or []:
        text = str(x.get("text") or "").strip()
        if not text or x.get("truncated"):
            continue
        v = x.get("value")
        # A value that already opens the text ("43 messages came in") is not said twice.
        if report.has_value(v) and not text.startswith(_value(v)) and not isinstance(v, str):
            text = f"{_value(v)} {text}"
        elif isinstance(v, str) and v.strip() and v.strip() not in text and not v.strip().endswith("%"):
            text = f"{text}: {v.strip()}"
        parts.append(text)
    if r.get("error"):
        parts.append("its numbers could not be read this time")
    return "; ".join(parts[:6])


def facts(now: datetime | None = None) -> dict:
    """Today's and yesterday's stored rows, as the brief and the AI see them. Pure SQL, no machine imported."""
    now = now or _now()
    today = report.today(now)
    rows_t = report.read(today)
    rows_y = report.read(today - timedelta(days=1))
    segs = [r for r in rows_t if r.get("machine") and r.get("machine") != report.METERS]
    needs, machines = [], []
    for r in segs:
        if r.get("error"):
            machines.append({"title": str(r.get("title") or r.get("machine")), "said": _machine_line(r)})
            continue
        for n in r.get("needs_you") or []:
            text = str(n.get("text") or "").strip()
            if text and not n.get("truncated"):
                needs.append({"text": text, "href": _link(n.get("href"))})
        line = _machine_line(r)
        if line:
            machines.append({"title": str(r.get("title") or r.get("machine")), "said": line})
    yesterday = []
    for r in rows_y:
        if r.get("machine") in (None, report.METERS) or r.get("error"):
            continue
        line = _machine_line(r)
        if line:
            yesterday.append({"title": str(r.get("title") or r.get("machine")), "said": line})
    newest = max((str(r.get("written_at") or "") for r in segs), default="")
    return {"as_of": newest, "needs_you": needs, "machines": machines, "yesterday": yesterday}


def _fingerprint(f: dict) -> str:
    body = {k: f[k] for k in ("needs_you", "machines", "yesterday")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def startable() -> list[dict]:
    """The proposal tools this box has: what "I can start" may offer. A connected app's tools are left out (the
    box can't vouch for what they do), and so is anything that is not a proposal."""
    from core.connector import tools
    out = []
    for name, s in sorted(tools.registry().items()):
        if s.get("capability") == "write:proposals" and not name.startswith("app_"):
            out.append({"tool": name, "title": str(s.get("title") or name)})
    return out


# ── writing it ──────────────────────────────────────────────────────────────────────────────────────────────
def _text(b: dict) -> str:
    """The brief in words, ready for an AI app to show as is."""
    when = _when(b.get("as_of") or "")
    out = [f"Your business today{', as of ' + when if when else ''}."]
    if b.get("ranked_at") and b.get("top"):
        # BETWEEN REWRITES, SAID OUT LOUD: the ranking is the AI's from earlier, the numbers under it are now.
        out.append(f"What your box's AI ranked at {_when(b['ranked_at'])}:")
    if b.get("summary"):
        out.append(b["summary"])
    if b.get("top"):
        lines = []
        for i, t in enumerate(b["top"], 1):
            s = f"{i}. {t['title']}."
            if t.get("why"):
                s += f" {t['why']}"
            if t.get("do"):
                s += f" Next: {t['do']}"
            if t.get("href"):
                s += f" ({t['href']})"
            lines.append(s)
        out.append("\n".join(lines))
    if b.get("needs_you"):
        out.append(("Needs you now:" if b.get("ranked_at") else "Needs you:") + "\n" + "\n".join(
            f"- {n['text']}" + (f" ({n['href']})" if n.get("href") else "") for n in b["needs_you"][:8]))
    if b.get("machines") and (not b.get("top") or b.get("ranked_at")):
        out.append(("Right now:\n" if b.get("ranked_at") and b.get("top") else "") + "\n".join(f"{m['title']}: {m['said']}." for m in b["machines"]))
    if not (b.get("top") or b.get("needs_you") or b.get("machines")):
        out.append("Nothing has been reported yet today. The first numbers arrive within fifteen minutes of the "
                   "box starting.")
    if b.get("ask_next"):
        out.append("Ask me next: " + " · ".join(b["ask_next"]))
    if b.get("can_start"):
        out.append("I can start: " + " · ".join(c["title"] for c in b["can_start"])
                   + " (each waits for your OK on Approvals)")
    if b.get("insights"):
        link = _link("/settings/ai")
        out.append(b["insights"] + (f": {link}" if link else "."))
    return "\n\n".join(out)


def plain(f: dict | None = None, *, why_no_ai: str = "", now: datetime | None = None) -> dict:
    """The brief with no AI: the facts in words, needs-you first. D5: says how to get insights when no AI is
    signed in (`why_no_ai` set); says nothing about the AI when it is only between rewrites."""
    f = f if f is not None else facts()
    b = {"as_of": f["as_of"], "built_at": (now or _now()).isoformat(timespec="seconds"), "from": "plain", "summary": "",
         "top": [], "needs_you": f["needs_you"], "machines": f["machines"], "ask_next": [], "can_start": [],
         "insights": NO_AI if why_no_ai else "", "fingerprint": _fingerprint(f)}
    b["text"] = _text(b)
    return b


def _grounded(text: str, facts_text: str) -> bool:
    from core.review_brief import _grounded as g
    return g(text, facts_text)


def _clean(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def thought(f: dict, think, now: datetime | None = None) -> dict | None:
    """The box's AI ranks the facts. None when its answer can't be used (not JSON, or nothing grounded)."""
    starts = startable()
    ask = {"facts": {"today": {"needs_you": f["needs_you"], "machines": f["machines"]},
                     "yesterday": f["yesterday"]},
           "startable": [s["tool"] for s in starts]}
    facts_text = json.dumps(ask["facts"], ensure_ascii=False)
    raw = think(AI_TASK, json.dumps(ask, ensure_ascii=False), system=_SYSTEM, max_tokens=900, timeout=60)
    try:
        got = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except ValueError:
        return None
    if not isinstance(got, dict):
        return None
    links = {n["href"] for n in f["needs_you"] if n.get("href")}
    top = []
    for t in got.get("top") or []:
        if not isinstance(t, dict):
            continue
        item = {"title": _clean(t.get("title"), _TITLE_MAX).rstrip("."), "why": _clean(t.get("why"), _WHY_MAX),
                "do": _clean(t.get("do"), 160),
                # A LINK IS ONE THE BOX GAVE IT, or none: a model's guess at a URL is a page that isn't there.
                "href": _clean(t.get("href"), 300) if _clean(t.get("href"), 300) in links else ""}
        if item["title"] and _grounded(" ".join((item["title"], item["why"], item["do"])), facts_text):
            top.append(item)
    asks = [q for q in (_clean(a, _ASK_LEN) for a in (got.get("ask_next") or []) if isinstance(a, str))
            if q and _grounded(q, facts_text)]
    titles = {s["tool"]: s["title"] for s in starts}
    can = []
    for c in got.get("can_start") or []:
        name = _clean((c or {}).get("tool") if isinstance(c, dict) else "", 80)
        if name in titles and name not in {x["tool"] for x in can}:
            why = _clean(c.get("why"), 200)
            can.append({"tool": name, "title": titles[name], "why": why if _grounded(why, facts_text) else ""})
    if not top:
        return None
    summary = _clean(got.get("summary"), 240)
    b = {"as_of": f["as_of"], "built_at": (now or _now()).isoformat(timespec="seconds"), "from": "ai",
         "summary": summary if _grounded(summary, facts_text) else "", "top": top[:TOP_MAX],
         "needs_you": f["needs_you"], "machines": f["machines"], "ask_next": asks[:ASK_MAX],
         "can_start": can[:START_MAX], "insights": "", "fingerprint": _fingerprint(f)}
    b["text"] = _text(b)
    return b


# ── stored, refreshed, read ─────────────────────────────────────────────────────────────────────────────────
def _stored() -> dict | None:
    try:
        from core import box_settings
        got = box_settings.get(NS, KEY)
    except Exception:                                    # noqa: BLE001 — a brief never breaks its reader
        return None
    return got if isinstance(got, dict) and got.get("text") else None


def _store(b: dict) -> None:
    from core import box_settings
    box_settings.put(NS, KEY, b, set_by="brief")


def _age_s(iso: str, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _same_day(iso: str, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(iso)).astimezone(report.tz()).date() == report.today(now)
    except (TypeError, ValueError):
        return False


def _ai_ready() -> tuple[bool, str]:
    if os.environ.get("AIOS_HERMETIC_TEST"):
        return False, "a test never spends"
    try:
        from core import brain
        return brain.can_think()
    except Exception as e:                               # noqa: BLE001
        return False, type(e).__name__


def refresh(now: datetime | None = None, *, think=None) -> dict:
    """After a review snapshot: rewrite the brief if its numbers moved. Worker only. Never raises.

    -> {"status": "same" | "plain" | "ai" | "failed"}. `think` is a test's stand-in for brain.think."""
    now = now or _now()
    try:
        f = facts(now)
        fp = _fingerprint(f)
        old = _stored()
        if think is None:
            ready, why = _ai_ready()
            if ready:
                from core import brain
                think = brain.think
        else:
            ready, why = True, ""
        # THE CLOCK IS THE LAST TIME THE AI WAS ASKED, not the last AI brief. A failed ask stores a plain brief, and
        # keyed on "from" the next 15-minute snapshot asked again: an AI that times out was asked 96 times a day, on
        # the owner's own weekly limit (OSDev1, landing #1842; test_recipe_ships caught it in a Lead box).
        o = old or {}
        tried = o.get("built_at") if o.get("from") == "ai" else o.get("ai_tried_at")
        ai_due = ready and (not tried or _age_s(tried, now) >= BRIEF_EVERY_S)
        if old and old.get("fingerprint") == fp and (old.get("from") == "ai" or not ai_due) \
                and bool(old.get("insights")) == (not ready):
            return {"status": "same"}
        if ai_due and (f["needs_you"] or f["machines"]):
            tried = now.isoformat(timespec="seconds")
            t0 = time.monotonic()
            try:
                b = thought(f, think, now)
            except Exception as e:                       # noqa: BLE001 — no AI is a plain brief, on time
                log.info("brief.ai_skipped", why=f"{type(e).__name__}: {str(e)[:120]}")
                b = None
            if b:
                _store(b)
                log.info("brief.written", source="ai", top=len(b["top"]), took_s=round(time.monotonic() - t0, 1))
                return {"status": "ai"}
        if old and old.get("from") == "ai" and ready and not ai_due and _same_day(old.get("built_at"), now):
            # BETWEEN REWRITES TODAY'S AI RANKING STAYS: `get()` reads it with the numbers as they are now, and says
            # when it was ranked. A plain brief swapped in would read as the box getting dumber through the day.
            return {"status": "same"}
        b = plain(f, why_no_ai=why if not ready else "", now=now)
        if tried:
            b["ai_tried_at"] = tried
        _store(b)
        log.info("brief.written", source="plain")
        return {"status": "plain"}
    except Exception as e:                               # noqa: BLE001 — never costs the snapshot
        log.warning("brief.refresh_failed", error=f"{type(e).__name__}: {str(e)[:160]}")
        return {"status": "failed"}


def get(now: datetime | None = None) -> dict:
    """The brief, now. Instant: pure SQL and no AI, whatever happens.

      * the stored brief, while the numbers are still the ones it was written from (a quiet afternoon changes
        nothing, so the AI's brief stays, however old);
      * today's AI ranking with the numbers as they are NOW, when they moved since it was ranked: the needs-you
        list and each machine's sentence are read fresh, and the text says when the ranking was made;
      * otherwise the plain brief, from the stored rows. A ranking from another day is never shown as today's."""
    now = now or _now()
    f = facts(now)
    b = _stored()
    if b and b.get("fingerprint") == _fingerprint(f):
        return b
    if b and b.get("from") == "ai" and _same_day(b.get("built_at"), now):
        fresh = {**b, "as_of": f["as_of"], "needs_you": f["needs_you"], "machines": f["machines"],
                 "ranked_at": b.get("built_at"), "fingerprint": _fingerprint(f)}
        fresh["text"] = _text(fresh)
        return fresh
    ready, why = _ai_ready()
    return plain(f, why_no_ai=why if not ready else "", now=now)


# ── the tool ────────────────────────────────────────────────────────────────────────────────────────────────
def tool(seat=None) -> dict:
    """core.brief: today's brief, written ahead, so instant."""
    b = get()
    return {k: b.get(k) for k in ("text", "as_of", "built_at", "from", "summary", "top", "needs_you", "machines",
                                   "ask_next", "can_start", "insights", "ranked_at") if k in b}


from core.connector import tools as _tools  # noqa: E402

_tools.register(
    "brief", title="Read today's brief from your box",
    fn=tool, wants_seat=True, machine="core", min_role="read", capability="read:reports",
    # ITS TEXT IS ALREADY THE ANSWER IN WORDS (#1841's render=): the AI shows it as it is.
    render=lambda r: r.get("text") or "",
    description="Today's brief, written by the box's own AI: the top things that matter, why, what to do, what to "
                "ask next and what the box can start. Instant. Show the 'text' to the owner as it is; don't show "
                "the other fields.")
