"""Space resolver — the multi-client binding (docs/SPACES.md).

A Space = one client. Per-Space resource BINDINGS: its Airtable base/table, its
Slack channel, and its Zernio key. SHARED across every Space (one of each): the
HeyGen account, HyperFrames, and the website (all records on one login). A Space
that omits the Slack channel or Zernio key inherits the global one.

Per-Space SECRETS (the Zernio key) are never committed: the config names an env
var (`zernio_key_env`) and the real key lives in the box `.env`.

Backward-compatible by construction: with no `spaces:` config block, there is one
implicit Space named 'default' built from the flat config — so a single-tenant box
runs completely unchanged, and reels stamped 'default' resolve to it.
"""
import os

from core.config import get_config, settings

DEFAULT = "default"


def _secret(env_name: str | None) -> str | None:
    """Resolve a per-Space secret named by the config to its value in the box .env
    (so a client's key is referenced by NAME in git, never by value)."""
    if not env_name:
        return None
    return (os.environ.get(env_name) or "").strip() or None


def _norm(s: dict) -> dict:
    """A config block → a resolved Space. Only `airtable_base` is required (read with
    .get so one malformed block can't crash every Space's resolution); the Slack
    channel falls back to the global when omitted.

    The Zernio key is the brand-safety crown jewel: a Space that NAMES its own key
    (`zernio_key_env`) gets THAT key or NOTHING — never the global one. Silently
    inheriting the global key would let client B's reel post to client A's socials if
    B's key env var were ever missing. Only a Space with no `zernio_key_env` at all
    (single-tenant) inherits the global key."""
    key_env = s.get("zernio_key_env")
    return {
        "name": s.get("name") or DEFAULT,
        "airtable_base": s.get("airtable_base"),
        "airtable_table": s.get("airtable_table") or settings.airtable_videos_table,
        "slack_channel": s.get("slack_channel") or settings.reel_slack_channel_id,
        "zernio_key": (_secret(key_env) if key_env else settings.zernio_api_key),
        # The Space's Zernio Profile (sub-account) id — the isolation boundary under
        # the target ONE-key model (docs/ZERNIO_SDK_INTEGRATION.md §5). Optional and
        # NOT a secret (an internal id, referenced inline). Unset = transitional
        # two-key mode: the per-Space key isolates and profile scoping is a no-op.
        "zernio_profile_id": s.get("zernio_profile_id"),
        # Carousel machine (spec §4): the Space's CAROUSELS table binding. ABSENT = the
        # carousel machine is completely inert for this Space — no sweep, no commands act.
        # Same-base second table, so isolation rides the existing base binding.
        "carousel_table": s.get("carousel_table"),
        # WRITTEN content (owner 2026-07-30): the Space's WRITTEN table binding — the lead
        # magnet + brief lanes read it instead of airtable_table. ABSENT = keep reading
        # VIDEOS, so the cutover stays one reversible config line.
        # THIS DICT IS A WHITELIST: a binding missing from here is silently dropped, and the
        # config line that sets it looks correct while doing nothing. That is exactly how
        # written_table shipped inert — every new Space key MUST be added here too.
        "written_table": s.get("written_table"),
        # WHERE THIS SPACE'S REELS MAY GO (owner 2026-08-19). Absent = the global default in
        # auto_poster (`_DEFAULT_REEL_PLATFORMS`). Present = THIS Space's ceiling instead,
        # which is how one client can publish to Instagram while another deliberately does
        # not. Hardcoded per Space on purpose: the owner does not want to drive this from the
        # board's per-row picker, and a ceiling that lives in version control cannot be
        # widened by ticking a box.
        "reel_platforms": s.get("reel_platforms"),
        # WHERE THIS SPACE'S CAROUSELS MAY GO (owner 2026-08-23). Same contract as
        # `reel_platforms`, one step later: absent = the module default in auto_poster
        # (`_CAROUSEL_PLATFORMS`, which includes Facebook), present = THIS Space's ceiling.
        # It matters more than the reel one, because CAROUSELS has no `Platforms` column and
        # so there is no per-row narrowing to fall back on.
        "carousel_platforms": s.get("carousel_platforms"),
        # THE SPACE'S PUBLIC LABEL (owner 2026-09-05: one droplet, many subdomains — a demo app
        # answers at <industry>.nlvl.co). `label: online-coaches` makes this Space the one that
        # `online-coaches.<demo zone>` shows on /dash, and the one the box's /tls/ask will hold a
        # certificate for. OPT-IN: a Space with no label is reachable only on the bare host — a
        # Space NAME is a stamp on rows, never an address, so `default.nlvl.co` stays a 403.
        # core/dash reads the same key straight from config (core imports no machine); the test
        # in tests/test_dash_demo_tabs.py holds the two readers to one answer.
        "label": (str(s.get("label") or "").strip().lower() or None),
    }


def _default_space() -> dict:
    """The implicit single-tenant Space from the flat env (no `spaces:` block)."""
    return {
        "name": DEFAULT,
        "airtable_base": settings.airtable_base_id,
        "airtable_table": settings.airtable_videos_table,
        "slack_channel": settings.reel_slack_channel_id,
        "zernio_key": settings.zernio_api_key,
        "zernio_profile_id": None,     # single-tenant: the key isolates
        "carousel_table": None,        # opt-in per Space (spec §4)
        "written_table": None,         # opt-in per Space (owner 2026-07-30)
        "label": None,                 # opt-in per Space (owner 2026-09-05): no label, no subdomain
    }


def all_spaces() -> list[dict]:
    """Every configured Space. From the `spaces:` config list, else one implicit
    'default' Space from the flat AIRTABLE_* config (single-tenant compatibility)."""
    cfg = get_config().get("spaces")
    if cfg:
        return [_norm(s) for s in cfg if s.get("airtable_base")]
    return [_default_space()]


def space_by_name(name: str, *, allow_default_alias: bool = True) -> dict | None:
    """Resolve a reel's `space` stamp → its bindings, or None if it can't be resolved.

    `allow_default_alias` (default True) keeps backward compatibility for owner-internal
    actions (produce, announce): the legacy/implicit 'default' stamp aliases to the sole
    Space. But for IRREVERSIBLE, PUBLIC actions (social posting) callers pass
    allow_default_alias=False, so an unknown or 'default' stamp NEVER aliases onto an
    arbitrary client under a multi-Space config (that would post client B's reel to
    client A's socials). A single configured Space always resolves (single-tenant)."""
    target = name or DEFAULT
    spaces = all_spaces()
    for s in spaces:
        if s["name"] == target:
            return s
    if len(spaces) == 1:                      # single-tenant: the lone Space owns it
        return spaces[0]
    if allow_default_alias and target == DEFAULT:
        return spaces[0] if spaces else None  # legacy alias, owner-internal paths only
    return None                               # multi-Space + unresolved → caller fails closed


def space_by_label(label: str | None) -> dict | None:
    """`online-coaches` → the Space whose `label:` is that, or None. This is the CONTENT scope:
    core.dash.host_label() hands the first label of the request host here and the /dash tabs show
    only that Space's rows. No alias, no default — an unlabelled host is the whole box (as today),
    and a label no Space carries is None so a caller can never scope onto the wrong brand."""
    lab = (label or "").strip().lower()
    if not lab:
        return None
    for s in all_spaces():
        if s.get("label") == lab:
            return s
    return None


def space_by_channel(channel_id: str | None) -> str:
    """Slack channel id → the NAME of the Space that owns it, for STAMPING a reel
    created from that channel. Without this, a reel born in a client's #reels channel
    stamps the implicit 'default' and aliases to the PRIMARY base — client B's reel
    would land in client A's Airtable (and announce in A's channel). Falls back to
    DEFAULT when the channel isn't a Space's channel (e.g. the global reel channel),
    preserving single-tenant behavior."""
    if channel_id:
        for s in all_spaces():
            if s.get("slack_channel") == channel_id:
                return s["name"]
    return DEFAULT
