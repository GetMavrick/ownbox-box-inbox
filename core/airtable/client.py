"""Airtable Web API client — list / create / update records on one base.

Generic on purpose: no field names, no sync policy, no reel knowledge. Callers
pass plain dicts of {FieldName: value}. Errors raise AirtableError; the sync
layer decides what is fatal vs. best-effort (a sync sweep must never take the
worker down, so it swallows + logs).
"""
import time

import requests

from core.config import settings
from core.exceptions import RetryableError
from core.logging import get_logger

log = get_logger(__name__)
_API = "https://api.airtable.com/v0"
_META_API = "https://api.airtable.com/v0/meta/bases"
_TIMEOUT = 20


class AirtableError(RuntimeError):
    """Any non-2xx from Airtable, or a transport failure."""


def is_configured() -> bool:
    """True only when key + base + table are all present. Until then the whole
    integration is dormant — no sweeps, no pushes."""
    return bool(settings.airtable_api_key and settings.airtable_base_id
                and settings.airtable_videos_table)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.airtable_api_key}",
            "Content-Type": "application/json"}


def _table(table: str | None) -> str:
    return table or settings.airtable_videos_table


def _url(table: str | None, rec_id: str | None = None, base: str | None = None) -> str:
    base_id = base or settings.airtable_base_id
    url = f"{_API}/{base_id}/{_table(table)}"
    return f"{url}/{rec_id}" if rec_id else url


def _raise(resp) -> dict:
    if not resp.ok:
        # W2.5 (audit C9): 429 (Airtable's 5 req/s cap, 30s penalty) and 5xx are
        # TRANSIENT — as an AirtableError they terminal-failed the job and told the
        # owner "your reel failed" over routine throttling. In-client retry happens in
        # _request; by the time this raises, retries are exhausted → RetryableError so
        # the worker's backoff machinery owns it. Real 4xx (permissions, bad field)
        # stays AirtableError: retrying a 403 converges to the same honest failure.
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableError(f"airtable transient {resp.status_code}: "
                                 f"{str(resp.text)[:200]}")
        # Body carries Airtable's typed error (e.g. INVALID_PERMISSIONS) — keep it
        # short, NEVER echo the request (a script body could be large/sensitive).
        raise AirtableError(f"airtable {resp.status_code}: {str(resp.text)[:300]}")
    return resp.json() if resp.content else {}


_TRANSIENT_TRIES = 3


def _request(method: str, url: str, **kw):
    """One HTTP call with transient-retry (W2.5): a 429 honors Retry-After (capped),
    a 5xx gets one quick retry. Everything else returns to the caller's _raise."""
    last = None
    for attempt in range(_TRANSIENT_TRIES):
        try:
            resp = requests.request(method, url, timeout=_TIMEOUT, **kw)
        except requests.RequestException as e:
            raise AirtableError(f"airtable transport error: {str(e)[:200]}")
        if resp.status_code == 429:
            # A MONTHLY-quota 429 (PUBLIC_API_BILLING_LIMIT_EXCEEDED) resets in DAYS —
            # sleeping Retry-After x3 per call turned every sync sweep into ~5 minutes
            # of pure waiting that clogged the worker's serial periodic loop (live
            # 2026-07-21). Short-circuit: no retry can succeed inside this process's
            # lifetime; _raise still maps it to RetryableError so jobs back off cleanly.
            if "BILLING" in (resp.text or "")[:300].upper():
                return resp
            last = resp
            try:
                wait = min(float(resp.headers.get("Retry-After", 30)), 35.0)
            except (TypeError, ValueError):
                wait = 30.0
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            last = resp
            time.sleep(1.5)
            continue
        return resp
    return last



def list_records(table: str | None = None, *, base: str | None = None,
                 page_size: int = 100,
                 max_records: int | None = None, fields: list[str] | None = None,
                 sort: list[dict] | None = None, filter_formula: str | None = None) -> list[dict]:
    """Return [{id, fields, createdTime}], following pagination up to max_records
    (None = all). Bounded by page_size per request. `filter_formula` is an Airtable
    filterByFormula (e.g. '{VID}=25') applied server-side. A list failure raises — the
    sync layer treats inbound-list failure as 'skip this sweep', never as data loss."""
    out: list[dict] = []
    offset = None
    params: dict = {"pageSize": min(page_size, 100)}
    if fields:
        params["fields[]"] = fields
    if filter_formula:
        params["filterByFormula"] = filter_formula
    if sort:
        for i, s in enumerate(sort):
            params[f"sort[{i}][field]"] = s["field"]
            params[f"sort[{i}][direction]"] = s.get("direction", "asc")
    while True:
        q = dict(params)
        if offset:
            q["offset"] = offset
        try:
            resp = _request("get", _url(table, base=base), headers=_headers(), params=q)
        except requests.RequestException as e:
            raise AirtableError(f"airtable list transport error: {str(e)[:200]}")
        body = _raise(resp)
        out.extend(body.get("records", []))
        if max_records and len(out) >= max_records:
            return out[:max_records]
        offset = body.get("offset")
        if not offset:
            return out
        time.sleep(0.21)   # Airtable caps at 5 req/s/base; stay comfortably under


def create_record(fields: dict, table: str | None = None, base: str | None = None) -> dict:
    """Create one record; return {id, fields, createdTime}. Raises on failure."""
    try:
        resp = _request("post", _url(table, base=base), headers=_headers(),
                             json={"fields": fields, "typecast": True})
    except requests.RequestException as e:
        raise AirtableError(f"airtable create transport error: {str(e)[:200]}")
    return _raise(resp)


def update_record(rec_id: str, fields: dict, table: str | None = None,
                  base: str | None = None) -> dict:
    """PATCH one record (leaves unlisted fields untouched). Raises on failure."""
    try:
        resp = _request("patch", _url(table, rec_id, base=base), headers=_headers(),
                              json={"fields": fields, "typecast": True})
    except requests.RequestException as e:
        raise AirtableError(f"airtable update transport error: {str(e)[:200]}")
    return _raise(resp)


def get_record(rec_id: str, table: str | None = None, base: str | None = None) -> dict:
    """Fetch one record → {id, fields, createdTime}. Raises on failure."""
    try:
        resp = _request("get", _url(table, rec_id, base=base), headers=_headers())
    except requests.RequestException as e:
        raise AirtableError(f"airtable get transport error: {str(e)[:200]}")
    return _raise(resp)


def append_attachments(rec_id: str, field: str, new_urls, *,
                       filenames=None, table: str | None = None,
                       base: str | None = None) -> dict:
    """Append attachment URL(s) to an attachment field WITHOUT dropping what's there.

    THE GOTCHA this exists for: an Airtable PATCH REPLACES an attachment cell wholesale —
    it does not merge — so a naive `update_record(rec, {field: [{"url": new}]})` silently
    WIPES every existing attachment. To keep them, the write must include the existing
    attachments too. We reference each existing one by its stable `id` (the read-back
    `url` is a short-lived airtableusercontent.com link that can expire before the write),
    then append the new url(s). Result: the field carries every prior version PLUS the new
    one — the owner's "multiple ad versions in one field" model.

    new_urls: a single url str or a list. filenames (optional, positional to new_urls)
    labels the new attachments in the cell. Returns the updated record."""
    if isinstance(new_urls, str):
        new_urls = [new_urls]
    rec = get_record(rec_id, table=table, base=base)
    existing = (rec.get("fields") or {}).get(field) or []
    keep = [{"id": a["id"]} for a in existing if isinstance(a, dict) and a.get("id")]
    add = []
    for i, url in enumerate(new_urls):
        item = {"url": url}
        if filenames and i < len(filenames) and filenames[i]:
            item["filename"] = filenames[i]
        add.append(item)
    return update_record(rec_id, {field: keep + add}, table=table, base=base)


def delete_record(rec_id: str, table: str | None = None, base: str | None = None) -> dict:
    """DELETE one record. Only used to clean up the throwaway record that seeds
    single-select options — AIOS NEVER deletes content records."""
    try:
        resp = _request("delete", _url(table, rec_id, base=base), headers=_headers())
    except requests.RequestException as e:
        raise AirtableError(f"airtable delete transport error: {str(e)[:200]}")
    return _raise(resp)


# ── Metadata API — SCHEMA only (create/read tables + fields), never record data. Requires the
# PAT to carry schema.bases:read/write, which a records-only token won't have — every caller
# here is a deliberate, owner-triggered setup step (never a hot/periodic path), and every
# caller must treat AirtableError as "not supported on this token/plan, fall back to a manual
# spec" rather than a hard failure. ─────────────────────────────────────────────────────────

def get_base_schema(base: str | None = None) -> dict:
    """{"tables": [{id, name, fields: [{id, name, type}, ...]}, ...]} for one base. Raises
    AirtableError on any failure (including insufficient token scope) — callers decide whether
    that means 'skip, fall back to manual setup' or 'surface to the owner'."""
    base_id = base or settings.airtable_base_id
    try:
        resp = _request("get", f"{_META_API}/{base_id}/tables", headers=_headers())
    except requests.RequestException as e:
        raise AirtableError(f"airtable schema transport error: {str(e)[:200]}")
    return _raise(resp)


def create_table(name: str, fields: list[dict], *, description: str = "",
                 base: str | None = None) -> dict:
    """Create a new table with an initial field set in one call (Airtable requires at least
    one field to create a table). `fields`: [{"name": ..., "type": ..., "options": {...}}, ...]
    — see Airtable's field-type docs for the type/options vocabulary (singleLineText,
    multilineText, multipleAttachments, singleSelect, multipleRecordLinks, ...). Raises
    AirtableError — most commonly INSUFFICIENT_PERMISSIONS/NOT_AUTHORIZED when the PAT lacks
    schema.bases:write or the plan doesn't support the Metadata write API."""
    base_id = base or settings.airtable_base_id
    payload = {"name": name, "fields": fields}
    if description:
        payload["description"] = description
    try:
        resp = _request("post", f"{_META_API}/{base_id}/tables", headers=_headers(),
                             json=payload)
    except requests.RequestException as e:
        raise AirtableError(f"airtable create_table transport error: {str(e)[:200]}")
    return _raise(resp)


def create_field(table_id: str, field: dict, *, base: str | None = None) -> dict:
    """Add one field to an EXISTING table. `field`: {"name": ..., "type": ..., "options": {...}}.
    Raises AirtableError on failure (permissions, duplicate name, unsupported type)."""
    base_id = base or settings.airtable_base_id
    try:
        resp = _request("post", f"{_META_API}/{base_id}/tables/{table_id}/fields",
                             headers=_headers(), json=field)
    except requests.RequestException as e:
        raise AirtableError(f"airtable create_field transport error: {str(e)[:200]}")
    return _raise(resp)
