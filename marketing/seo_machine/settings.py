"""Every SEO setting for this box, from the screen the buyer actually uses.

WHY NOT THE YAML, WHICH IS WHERE THIS STARTED. OSDev1 blocked #1554 over it and the reason is
checkable rather than stylistic:

  * **A buyer cannot edit it.** `scripts/export_box.sh` says so in its own words, about `vendors`:
    a box "cannot be given a key later without editing the one file we tell the buyer not to edit".
  * **A sold box does not even have it.** The `customer_voice` case's KEEP list is
    `cost worker brain vendors spaces customer_voice inbox models model_ids rates dash review
    notify` — no `seo`. The section is stripped on export, so `get_config().get("seo")` is `None`
    and the machine reports "not configured" forever, on the only boxes that matter.
  * **It would not take effect anyway.** `get_config` is `lru_cache(maxsize=1)`, so a screen that
    wrote YAML would change nothing until the box restarted — `core/state.py:309` records that
    measurement, and it is why `box_settings` exists at all.

SO THE DEFAULTS LIVE HERE, IN CODE. That is the part YAML cannot do: a box whose config section
was stripped still boots with a sane dataset and API version, and needs only the values that are
genuinely its own. `box_settings.get()` already layers the buyer's answer over the config over
this default, so this module is thin on purpose.

NOTHING HERE IS OURS. Every value starts empty or neutral. A fresh box publishes nothing until
someone fills the screen in, which is the standing rule (owner, 2026-09-25, via OSDev1).

OSDev4: this is the accessor the 06:47 contract names, written here only to unblock the publisher.
It registers nothing and owns no screen — absorb it into the shell or replace it.
"""
from __future__ import annotations

from core import box_settings

MACHINE = "seo"

# What a box starts with when nobody has entered anything. Neutral, never Ownbox's.
DEFAULTS: dict = {
    "project_id": "",         # the box's own Sanity project
    "dataset": "production",
    "api_version": "2025-02-19",
    "site_url": "",           # e.g. https://example.com — articles are served at /articles/<slug>
    "host": "",               # bare hostname for IndexNow
    "indexnow_key": "",       # public by design; the site serves it at /<key>.txt
    "never_words": [],        # whole words, case-insensitive, plural-aware
    "never_phrases": [],      # substrings, for terms of art
    "competitors": [],
    "allowed_numbers": [],    # the box's price and fact list; anything else is refused
    "facts": [],              # the only claims the writer may state
    "weekly_cap": 4,
    # The Airtable table the business plans its articles in (sources.py). Its key is a credential,
    # so it lives in box_secrets, never here.
    "airtable_base": "",      # app…
    "airtable_table": "",     # tbl…
    "airtable_view": "",      # viw…, optional: the view the machine follows
}


def get() -> dict:
    """Every setting, with the buyer's answer winning over the box's default.

    Never raises: `box_settings.get` is built not to, because a settings store that throws takes
    down the thing it was meant to configure.
    """
    return {key: box_settings.get(MACHINE, key, default=default)
            for key, default in DEFAULTS.items()}


# How a list may arrive when it was saved as one string (a textarea, an older save, a hand-set
# value). One entry per line always. Commas ALSO separate single words, which cannot contain one;
# nowhere else, because "$1,599" is one fact and "Acme, Inc." is one competitor.
_COMMA_SPLITS = ("never_words",)


def _as_list(key: str, value) -> tuple:
    """A stored list, whatever shape it was saved in, as clean entries.

    REVIEW ITEM 9 (OSDev1, #1554): `tuple("call\nvoice")` is a tuple of CHARACTERS, so a list
    saved as text turned the never-use list into the alphabet, and every article was refused on
    the letter "a". And entries were never stripped, so " voice" matched nothing.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.splitlines()
        if key in _COMMA_SPLITS:
            parts = [w for line in parts for w in line.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    out = []
    for p in parts:
        p = str(p).strip()
        if p and p not in out:
            out.append(p)
    return tuple(out)


def lists() -> dict:
    """Just the four the guard takes, in the shape it takes them: clean tuples of strings."""
    s = get()
    return {k: _as_list(k, s.get(k)) for k in
            ("never_words", "never_phrases", "competitors", "allowed_numbers")}


def facts() -> tuple:
    """The claims the writer may state, cleaned the same way. One per line when saved as text."""
    return _as_list("facts", get().get("facts"))
