"""Which rails this business has, and what state each one is in.

FOUR STATES, AND TWO OF THEM ARE NOT FAILURES (docs/PLAN_CUSTOMER_VOICE.md §1.10):

    not owned      -> renders NOTHING at all
    owned, unconnected -> "connect your Google Business Profile"
    connected, quiet   -> "0 new reviews this week"
    connected, failed  -> "we could not reach Google since Tuesday"

OWNERSHIP IS DECLARED, NEVER INFERRED, and that is the whole reason this module exists. The box
has no way to know that a restaurant has an unconnected Yelp listing while a B2B consultancy
never will. Guess in the safe-looking direction and every clone nags *connect your Yelp* forever
on a rail the business does not use — which is the empty-section failure the plan already fears,
arriving through the door the four-state ruling opened.

So `owned` is the owner's own answer (config `customer_voice.rails_owned`, overridable per box
in the database), and a rail nobody has been asked about is absent rather than assumed.
"""
from core import state
from core.logging import get_logger

log = get_logger(__name__)

# Every rail this machine will ever carry, in the order the segment renders them. A rail is
# listed here the day its code lands, whether or not any box owns it — the list is what the
# setup question is generated from, not a record of what is switched on.
ALL = ("uptime", "pagespeed", "reviews", "comments", "seo", "competitors")

# STAGE 1 IS THESE TWO, and they are the ones that need no account at all: is the front door up,
# and is it fast. Every box can answer both from its first poll.
NO_AUTH = ("uptime", "pagespeed")

OK, WARN, FAIL, CONNECT = "ok", "warn", "fail", "connect"


# READ THE CONFIG PER CALL, never the name bound at import. `from core.config import get_config`
# at module scope freezes the function object, so a box overlay reloaded at runtime — or a test
# that swaps it — moves core.config and NOT this module. That is not hypothetical: the first
# version of this file did exactly that, and two of its own tests passed VACUOUSLY, agreeing with
# the real shipped config instead of the one they had set. The same defect cost an hour in
# core/dash the same morning.
def _cfg() -> dict:
    from core.config import get_config
    return dict(get_config().get("customer_voice") or {})


def enabled() -> bool:
    from core.config import as_bool
    return as_bool(_cfg().get("enabled"), True)


def declared() -> set:
    """The rails the owner says this business has. Config is the answer a box ships with; a row
    in `voice_rails` is his later change of mind and wins."""
    raw = _cfg().get("rails_owned") or []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    owned = {str(r).strip().lower() for r in raw if str(r).strip()} & set(ALL)
    # NAMING A COMPETITOR IS DECLARING THE RAIL. He should not have to say "I have competitors"
    # in one setting and then list them in another — the list IS the answer, and asking twice is
    # how a box ends up watching a roster it was told not to watch.
    cfg = _cfg().get("competitors") or {}
    if (cfg.get("roster") or cfg.get("self")):
        owned.add("competitors")
    try:
        with state.connect() as c:
            for r in c.execute("SELECT rail, owned FROM voice_rails WHERE owned IS NOT NULL"):
                if r["owned"]:
                    owned.add(r["rail"])
                else:
                    owned.discard(r["rail"])       # an explicit NO overrides the shipped default
    except Exception as e:                          # noqa: BLE001 — no table yet is not a fault
        log.info("voice.rails_table_absent", why=type(e).__name__)
    return owned


def owns(rail: str) -> bool:
    return rail in declared()


def set_owned(rail: str, owned: bool) -> None:
    """His answer to "do you have a Yelp listing?", stored and changeable."""
    if rail not in ALL:
        raise ValueError(f"unknown rail {rail!r}; the rails are {ALL}")
    with state.connect() as c:
        c.execute(
            "INSERT INTO voice_rails (rail, owned, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(rail) DO UPDATE SET owned = excluded.owned, updated_at = excluded.updated_at",
            (rail, 1 if owned else 0, state._now()))


def mark_connected(rail: str) -> None:
    """Called the first time a rail's credential actually works. Never unset: a rail that worked
    once and is failing now is state 4 (`fail`), not state 2 (`connect`) — telling him to connect
    an account he already connected is how a dashboard teaches people to ignore it."""
    with state.connect() as c:
        c.execute(
            "INSERT INTO voice_rails (rail, owned, connected_at, updated_at) VALUES (?,1,?,?) "
            "ON CONFLICT(rail) DO UPDATE SET connected_at = COALESCE(voice_rails.connected_at, excluded.connected_at), "
            "  owned = 1, updated_at = excluded.updated_at",
            (rail, state._now(), state._now()))


def connected(rail: str) -> bool:
    """A rail needing no account is connected the moment it is owned — there is nothing to
    connect. That is what makes Stage 1 render real numbers on a box with no keys at all."""
    if rail in NO_AUTH:
        return owns(rail)
    try:
        with state.connect() as c:
            row = c.execute("SELECT connected_at FROM voice_rails WHERE rail = ?", (rail,)).fetchone()
        return bool(row and row["connected_at"])
    except Exception:                               # noqa: BLE001
        return False


def explain(source: str) -> str:
    """The last error as a person should read it, marker stripped."""
    e = (health(source).get("last_error") or "")
    return e[len(ACTIONABLE):] if e.startswith(ACTIONABLE) else e


def health(source: str) -> dict:
    """`{last_ok_at, last_try_at, last_error}` for one source, or empty if it has never run."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT * FROM voice_sources WHERE source = ?", (source,)).fetchone()
        return dict(row) if row else {}
    except Exception:                               # noqa: BLE001
        return {}


# A PREFIX, NOT A SECOND COLUMN. An error the owner can ACT on — a quota he can raise with a
# free key, an account he has not linked — is state 2 (`connect`), not state 4 (`fail`). Marking
# it in the stored text keeps one column and one write path, and the marker is stripped before
# anything renders it.
ACTIONABLE = "connect: "


def record(source: str, *, error: str | None = None, actionable: bool = False) -> None:
    """One attempt, kept whether it worked or not. `last_ok_at` is only ever moved forward by a
    SUCCESS, so "we could not reach it since Tuesday" is a fact this table can state."""
    now = state._now()
    with state.connect() as c:
        c.execute(
            "INSERT INTO voice_sources (source, last_ok_at, last_try_at, last_error, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(source) DO UPDATE SET "
            "  last_ok_at = CASE WHEN excluded.last_error IS NULL THEN excluded.last_try_at "
            "                    ELSE voice_sources.last_ok_at END, "
            "  last_try_at = excluded.last_try_at, "
            "  last_error = excluded.last_error, "
            "  updated_at = excluded.updated_at",
            (source, None if error else now, now,
             ((ACTIONABLE if actionable else "") + str(error))[:300] if error else None, now))


def state_of(rail: str) -> str | None:
    """The rail's state for the page, or None when it renders NOTHING (not owned).

    This is the one function the segment asks, so the four states cannot be re-derived slightly
    differently by each rail that wants to draw itself.
    """
    if not owns(rail):
        return None                                 # state 1 — not this business's rail
    if not connected(rail):
        return CONNECT                              # state 2 — his, and waiting on an account
    h = health(rail)
    err = h.get("last_error") or ""
    if err.startswith(ACTIONABLE):
        return CONNECT                              # something HE can fix, not something broken
    if err:
        return FAIL                                 # state 4 — connected, and the poll failed
    if not h.get("last_ok_at"):
        return CONNECT                              # owned, connectable, never yet polled
    return OK                                       # state 3 — connected and answering
