"""The AEO machine's two data sources, Sanity and Airtable: what a connection is, and the proof of one.

BOTH ARE REQUIRED (owner, 2026-09-25): *"Business owners are going to be required to use sanity and
set that up. And then they're gonna be required to use Airtable. These are gonna be for hard-core
serious people that want a serious automated content machine."* Airtable is where the business
plans its articles; Sanity is where its website reads them; the machine writes in between.

A CONNECTION IS SAVED ONLY ONCE IT HAS BEEN PROVED. Each `check_*` makes the same requests the
machine will make later, with what the owner just typed, and answers None or ONE SENTENCE saying
what to fix. The screen saves nothing until the answer is None. A key that is refused here is
refused while the owner is looking at the field they pasted it into. The same key refused later
fails inside the worker, hours on, on an article nobody is watching.

NO FLASK HERE, AND NO WRITES. This file reads the network and returns words. `app.py` owns the
screens and the saving, so this file can be tested without either.

CREDENTIALS NEVER APPEAR IN WHAT THIS RETURNS. A sentence names the field and the fix, never the
value, so nothing a vendor echoes back can put a token on a page.
"""
from __future__ import annotations

import json as _json
import re
from urllib.parse import quote, urlencode, urlsplit

from core import box_secrets, net
from core.logging import get_logger

from . import settings

log = get_logger(__name__)

# ── Sanity ────────────────────────────────────────────────────────────────────────────────────

# THE PUBLISHER READS THIS NAME (publisher.TOKEN_KEY); tests/test_aeo_sources.py holds them equal.
SANITY_TOKEN = "SANITY_API_TOKEN_OWNBOX"
# What the machine creates. The proof is a dry-run create of exactly this, so a token that passes
# here can do the one thing the publisher needs.
_DOC_TYPE = "article"

PROJECT_RE = re.compile(r"^[a-z0-9]{4,32}$")
DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SANITY_BAD_TOKEN = "Sanity did not accept that token. Copy it again from sanity.io/manage."
SANITY_READ_ONLY = ("That token can read but not write. In sanity.io/manage, make a token with "
                    "Editor access and paste that one.")
SANITY_NO_PROJECT = "Sanity has no project with that ID. Copy the ID from sanity.io/manage."
SANITY_UNREACHABLE = "Sanity did not answer. Check the project ID, then try again in a minute."


def _sanity_base(project: str) -> str:
    ver = "v" + str(settings.get().get("api_version") or "2025-02-19").lstrip("v")
    return f"https://{project}.api.sanity.io/{ver}"


def _sanity_other(status: int) -> str:
    return f"Sanity answered with an error ({status}). Try again in a minute."


def check_sanity(project: str, dataset: str, token: str) -> str | None:
    """None if this token can write articles into this project's dataset, else what to fix.

    TWO REQUESTS, IN THIS ORDER, because each failure means something different to the owner:
      1. `users/me` proves the token opens this project at all. Here 401 is a bad token and 404 is
         a project that does not exist.
      2. A dry-run create of one article proves the token may WRITE to this dataset. Nothing is
         stored: Sanity checks the mutation and discards it. Here 403 is a read-only token, the
         likeliest mistake, since Viewer is the first choice Sanity offers.
    """
    headers = {"Authorization": f"Bearer {token}"}
    base = _sanity_base(project)
    status, _body = net.get_public(f"{base}/users/me", headers=headers)
    if status == 0:
        return SANITY_UNREACHABLE
    if status in (401, 403):
        return SANITY_BAD_TOKEN
    if status == 404:
        return SANITY_NO_PROJECT
    if status != 200:
        return _sanity_other(status)

    mutation = {"mutations": [{"create": {"_type": _DOC_TYPE, "title": "Connection check"}}]}
    try:
        status, body = net.post_public(
            f"{base}/data/mutate/{quote(dataset, safe='')}?dryRun=true",
            headers={**headers, "Content-Type": "application/json"}, json=mutation)
    except net.PostRefused:
        return SANITY_UNREACHABLE
    if status == 200:
        return None
    if status == 401:
        return SANITY_BAD_TOKEN
    if status == 403:
        return SANITY_READ_ONLY
    if status == 404 or "dataset" in (body or "").lower():
        return (f"This project has no dataset called {dataset}. Check the name under Datasets "
                "in sanity.io/manage.")
    return _sanity_other(status)


def sanity_state() -> dict:
    """What the screens show. `connected` means the project and a token are both saved, which
    happens only after `check_sanity` passed."""
    s = settings.get()
    project, dataset = s.get("project_id") or "", s.get("dataset") or "production"
    token = box_secrets.is_set(SANITY_TOKEN)
    return {"connected": bool(project and token), "project": project, "dataset": dataset,
            "token_saved": token}


# ── Airtable ──────────────────────────────────────────────────────────────────────────────────

AIRTABLE_KEY = "AIRTABLE_API_KEY_SEO"   # storage name kept: see settings.MACHINE
_API = "https://api.airtable.com/v0"

# THE FIELDS THE MACHINE READS AND WRITES, BY NAME. A proposal until the owner's template lands
# (owner, 2026-09-25: "we are going to provide them a template table and that's on me"). When it
# does, this tuple follows the template, and the check below names any field a table is missing.
TEMPLATE_FIELDS = ("Question", "Status", "URL")
# Shown as "Copy our template" once the owner supplies it. Empty means no link is drawn.
TEMPLATE_URL = ""

# What a token needs, in Airtable's own names, so the owner can match them on Airtable's screen.
SCOPES = ("data.records:read", "data.records:write", "schema.bases:read")

_BASE_RE = re.compile(r"\b(app[A-Za-z0-9]{14})\b")
_TABLE_RE = re.compile(r"\b(tbl[A-Za-z0-9]{14})\b")
_VIEW_RE = re.compile(r"\b(viw[A-Za-z0-9]{14})\b")

AIRTABLE_BAD_KEY = "Airtable did not accept that key. Copy it again from Airtable's Builder hub."
AIRTABLE_NO_BASE = ("That key cannot open this base. In Airtable, edit the token, add this base "
                    "under Access, and give it the schema.bases:read scope.")
AIRTABLE_NO_READ = ("That key cannot read this table's records. In Airtable, edit the token and "
                    "add the data.records:read scope.")
AIRTABLE_UNREACHABLE = "Airtable did not answer. Try again in a minute."
AIRTABLE_UNREADABLE = ("Airtable's description of this base could not be read. Try again in a "
                       "minute, and if it happens again, tell us the base is very large.")
# A BASE'S SCHEMA IS BIG. Every table, field and select option comes back in one answer, and a
# working content base with twenty-odd tables passes core.net's 400 KB default. Cut short, the JSON
# does not parse and the check would say the table is missing (OSDev1's review of #1572).
_SCHEMA_MAX_BYTES = 5_000_000
AIRTABLE_BAD_URL = ("That is not a table's address. In Airtable, open the table and copy the "
                    "address from your browser. It starts with https://airtable.com/app.")


class BadTableUrl(ValueError):
    pass


def parse_table_url(url: str) -> tuple[str, str, str]:
    """(base, table, view) from an address copied out of the browser. The view may be "".

    Only airtable.com, and only an address that names a base AND a table: a base alone does not
    say which table holds the plan, and guessing the first one is how a machine writes into the
    wrong table.
    """
    raw = str(url or "").strip()
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise BadTableUrl(AIRTABLE_BAD_URL) from None
    if host not in ("airtable.com", "www.airtable.com"):
        raise BadTableUrl(AIRTABLE_BAD_URL)
    path = parts.path
    base, table, view = _BASE_RE.search(path), _TABLE_RE.search(path), _VIEW_RE.search(path)
    if not base or not table:
        raise BadTableUrl(AIRTABLE_BAD_URL)
    return base.group(1), table.group(1), view.group(1) if view else ""


def table_url(base: str, table: str, view: str = "") -> str:
    return "https://airtable.com/" + "/".join(p for p in (base, table, view) if p)


def _json_or_empty(body: str) -> dict:
    try:
        out = _json.loads(body or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def check_airtable(key: str, base: str, table: str, view: str = "") -> str | None:
    """None if this key can read this table and it has the template's fields, else what to fix.

    THREE REQUESTS, each a question the owner can act on:
      1. `whoami`: is the key real? Where Airtable lists the token's scopes, any missing one is
         named in Airtable's own words.
      2. The base's schema: can the key open the base, is the table there, is the view there, and
         does the table have every field the machine reads and writes, by name.
      3. One record: can the key read the rows, not only the schema.

    WRITE PERMISSION IS PROVED ONLY WHERE AIRTABLE LISTS SCOPES. Proving it otherwise means writing
    to the owner's table, and a set-up screen that edits the plan is not a check.
    """
    headers = {"Authorization": f"Bearer {key}"}

    status, body = net.get_public(f"{_API}/meta/whoami", headers=headers)
    if status == 0:
        return AIRTABLE_UNREACHABLE
    if status in (401, 403):
        return AIRTABLE_BAD_KEY
    if status != 200:
        return f"Airtable answered with an error ({status}). Try again in a minute."
    scopes = _json_or_empty(body).get("scopes")
    if isinstance(scopes, list):
        lacking = [s for s in SCOPES if s not in scopes]
        if lacking:
            return ("That key is missing " + ", ".join(lacking) + ". In Airtable, edit the "
                    "token and add " + ("it." if len(lacking) == 1 else "them."))

    status, body = net.get_public(f"{_API}/meta/bases/{base}/tables", headers=headers,
                                  max_bytes=_SCHEMA_MAX_BYTES)
    if status == 0:
        return AIRTABLE_UNREACHABLE
    if status == 401:
        return AIRTABLE_BAD_KEY
    if status in (403, 404):
        return AIRTABLE_NO_BASE
    if status != 200:
        return f"Airtable answered with an error ({status}). Try again in a minute."
    schema = _json_or_empty(body)
    if not isinstance(schema.get("tables"), list):
        return AIRTABLE_UNREADABLE
    tables = schema["tables"]
    found = next((t for t in tables if isinstance(t, dict) and t.get("id") == table), None)
    if found is None:
        return ("That base has no table at that address. Open the table in Airtable and copy "
                "the address again.")
    if view and not any(isinstance(v, dict) and v.get("id") == view
                        for v in found.get("views") or []):
        return ("That table has no view at that address. Open the view in Airtable and copy "
                "the address again.")
    names = {str(f.get("name") or "") for f in found.get("fields") or [] if isinstance(f, dict)}
    lacking = [f for f in TEMPLATE_FIELDS if f not in names]
    if lacking:
        return ("Your table is missing " + ("the field " if len(lacking) == 1 else "the fields ")
                + ", ".join(lacking) + ". Add " + ("it" if len(lacking) == 1 else "them")
                + " with exactly that name, or start from our template.")

    query = {"maxRecords": "1"}
    if view:
        query["view"] = view
    status, _body = net.get_public(f"{_API}/{base}/{table}?{urlencode(query)}", headers=headers)
    if status == 0:
        return AIRTABLE_UNREACHABLE
    if status == 401:
        return AIRTABLE_BAD_KEY
    if status in (403, 404):
        return AIRTABLE_NO_READ
    if status != 200:
        return f"Airtable answered with an error ({status}). Try again in a minute."
    return None


def airtable_state() -> dict:
    s = settings.get()
    base, table, view = (s.get("airtable_base") or "", s.get("airtable_table") or "",
                         s.get("airtable_view") or "")
    key = box_secrets.is_set(AIRTABLE_KEY)
    return {"connected": bool(base and table and key), "base": base, "table": table,
            "view": view, "url": table_url(base, table, view) if base and table else "",
            "key_saved": key}
