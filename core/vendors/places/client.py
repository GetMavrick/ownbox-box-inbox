"""Google Places API (New) client — Text Search + Place Details.

The COLD tier's discovery surface. Places is a RANKED, CAPPED search surface, not a
database you can dump: ~20 results per page, ~60 per query (3 pages). Coverage comes
from MORE TILES (cells x categories), never deeper paging — see discovery_places.

  POST /v1/places:searchText     text query -> up to 20 places/page (+ nextPageToken)
  GET  /v1/places/{place_id}     the paid Details call, per row, only after the fit gate

EXCLUSION-ONLY TYPE PARING (standing owner rule): we filter OUT known-bad `types`
after the fact and NEVER send `includedPrimaryTypes` — an inclusion filter silently
hides businesses Google filed under an odd primary category, and a business you never
saw is worse than one you skip on purpose.

Every request is metered into the vendor ledger as `google_places` so cost_guard sees
it and a per-campaign ceiling can pause THAT campaign without touching the system.
Field masks are REQUIRED by this API and are also the price tier — ask for the
narrowest set that answers the question. Error contract Rule 2: transient -> Retryable,
hard/quota -> VendorError/BudgetExceeded.
"""
import requests

from core import cost_guard, state
from core.config import settings
from core.exceptions import BudgetExceeded, RetryableError, VendorError, is_hard_quota
from core.logging import get_logger, scrub_secrets

log = get_logger(__name__)

_BASE = "https://places.googleapis.com/v1"
_TIMEOUT = 30
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
_VENDOR = "google_places"

# Search-tier fields: everything gtm_businesses stores from a LIST hit. Deliberately
# excludes Details-only fields (hours, editorial summary) — those cost more and are
# fetched later, per row, only for rows that pass the fit gate.
SEARCH_FIELDS = ",".join([
    "places.id", "places.displayName", "places.websiteUri",
    "places.nationalPhoneNumber", "places.formattedAddress",
    "places.addressComponents", "places.location",
    "places.primaryType", "places.types", "places.businessStatus",
    "places.rating", "places.userRatingCount",
    "nextPageToken",
])

DETAILS_FIELDS = ",".join([
    "id", "displayName", "websiteUri", "nationalPhoneNumber",
    "formattedAddress", "businessStatus", "regularOpeningHours",
])

# Types that are never a local-service-business prospect. Exclusion-only: a listing is
# dropped if its PRIMARY type is here, never restricted to an allow-list.
EXCLUDED_PRIMARY_TYPES = {
    "locality", "political", "postal_code", "route", "street_address",
    "country", "administrative_area_level_1", "administrative_area_level_2",
}


def is_configured() -> bool:
    return bool((getattr(settings, "google_places_api_key", "") or "").strip())


def _headers(field_mask: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": field_mask,
    }


def _guard(units: float, job_id: str | None) -> None:
    """Meter BEFORE the call so a ceiling stops the next request, not after we've paid."""
    cost_guard.check_vendor(_VENDOR, units)


def _record(units: float, job_id: str | None) -> None:
    try:
        state.record_vendor_usage(_VENDOR, units, job_id=job_id)
    except Exception as e:  # metering must never sink a good result
        log.warning("places.meter_failed", error=scrub_secrets(str(e))[:160])


def _post(path: str, payload: dict, field_mask: str, *, job_id=None) -> dict:
    if not is_configured():
        raise VendorError(_VENDOR, "config", "GOOGLE_PLACES_API_KEY required")
    # (The surrogate-id guard lives in place_details(), the only call that carries a place id.
    #  A copy of it sat HERE from 2026-08-08 to 2026-09-05 and killed every text search with a
    #  NameError while the suite stayed green — see test_search_reaches_the_wire.)
    _guard(1, job_id)
    try:
        r = requests.post(f"{_BASE}/{path}", json=payload,
                          headers=_headers(field_mask), timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise RetryableError(f"places {type(e).__name__}: {e}") from e
    _record(1, job_id)
    if r.status_code in _TRANSIENT_STATUS:
        if r.status_code == 429 and is_hard_quota(r.text):
            raise BudgetExceeded(f"places hard quota: {r.text[:160]}")
        raise RetryableError(f"places HTTP {r.status_code}: {r.text[:160]}")
    if not r.ok:
        raise VendorError(_VENDOR, r.status_code, scrub_secrets(r.text)[:200])
    try:
        return r.json()
    except ValueError as e:
        raise VendorError(_VENDOR, "shape", f"non-JSON: {e}") from e


def _component(place: dict, kind: str) -> str | None:
    for c in place.get("addressComponents") or []:
        if kind in (c.get("types") or []):
            return c.get("shortText") or c.get("longText")
    return None


def map_place(place: dict) -> dict:
    """One Places result -> the businesses_store.upsert field shape. Pure + testable."""
    loc = place.get("location") or {}
    return {
        "place_id": place.get("id"),
        "name_places": (place.get("displayName") or {}).get("text") or "",
        "website": place.get("websiteUri"),
        "phone": place.get("nationalPhoneNumber"),
        "address": place.get("formattedAddress"),
        "locality": _component(place, "locality"),
        "region": _component(place, "administrative_area_level_1"),
        "postal": _component(place, "postal_code"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "primary_type": place.get("primaryType"),
        "types": place.get("types") or [],
        "business_status": place.get("businessStatus"),
        "rating": place.get("rating"),
        "reviews_total": place.get("userRatingCount"),
    }


def excluded(mapped: dict) -> bool:
    """Exclusion-only paring — drop obvious non-businesses, keep everything else."""
    return (mapped.get("primary_type") or "") in EXCLUDED_PRIMARY_TYPES


def text_search(query: str, *, page_token: str | None = None,
                max_results: int = 20, job_id: str | None = None) -> tuple[list[dict], str | None]:
    """One page of results for `query` -> (mapped places, next_page_token).

    NO includedPrimaryTypes (exclusion-only rule). Caller tiles the query space; this
    returns at most ~20 and a token for up to ~60 total.
    """
    payload: dict = {"textQuery": query, "maxResultCount": max_results}
    if page_token:
        payload["pageToken"] = page_token
    data = _post("places:searchText", payload, SEARCH_FIELDS, job_id=job_id)
    places = [map_place(p) for p in (data.get("places") or [])]
    kept = [p for p in places if p.get("place_id") and not excluded(p)]
    log.info("places.text_search", query=query[:80], returned=len(places), kept=len(kept))
    return kept, data.get("nextPageToken")


def place_details(place_id: str, *, job_id: str | None = None) -> dict:
    """The PAID per-row Details call. Only for rows that already passed the fit gate —
    never pay Details on a row you will not mail. Caller stamps `details_at`."""
    if not is_configured():
        raise VendorError(_VENDOR, "config", "GOOGLE_PLACES_API_KEY required")
    # OUR SURROGATE MUST NEVER REACH GOOGLE. `place_id` is the internal dedupe key and it is
    # origin-prefixed for every row that did not come from Places — on 2026-08-08 that was 706
    # of 707 (csv:606, overture:100, one real id). A Google place id is an opaque token and
    # never contains a colon. Without this, one synthetic id spends a PAID Details call to be
    # told it does not exist. Checked before _guard so it costs nothing, not even a ledger row.
    if not place_id or ":" in place_id:
        raise VendorError(_VENDOR, "bad_place_id",
                          f"{place_id!r} is an internal surrogate, not a Google place id "
                          f"— read gtm_businesses.google_place_id instead")
    _guard(1, job_id)
    try:
        r = requests.get(f"{_BASE}/places/{place_id}",
                         headers=_headers(DETAILS_FIELDS), timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise RetryableError(f"places details {type(e).__name__}: {e}") from e
    _record(1, job_id)
    if r.status_code in _TRANSIENT_STATUS:
        raise RetryableError(f"places details HTTP {r.status_code}: {r.text[:160]}")
    if not r.ok:
        raise VendorError(_VENDOR, r.status_code, scrub_secrets(r.text)[:200])
    return r.json()
