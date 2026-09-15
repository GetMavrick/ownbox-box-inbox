"""The four-endpoint Waalaxy surface (plan §2-3). Three reads, ONE write.

Everything takes the subaccount's Account object so the key, the lists and the
campaigns can never cross accounts. List/campaign name resolution happens here
and fails closed on a missing name.
"""
from core.logging import get_logger

from . import model, transport
from .errors import WaalaxyError

log = get_logger(__name__)


def test_connection(acct: model.Account) -> bool:
    """GET /integrations/test - credential check, zero side effects."""
    key = transport.resolve_key(acct.key_env)
    out = transport.get("/integrations/test", key)
    ok = out is True or (isinstance(out, dict) and out.get("success") is not False)
    log.info("waalaxy.test", account=acct.name, ok=bool(ok))
    return bool(ok)


def prospect_lists(acct: model.Account) -> list:
    """-> [{_id, name, totalProspects, ...}] for THIS subaccount only."""
    key = transport.resolve_key(acct.key_env)
    out = transport.get("/prospectLists/getProspectLists", key)
    lists = out.get("prospectLists") if isinstance(out, dict) else out
    return list(lists or [])


def campaigns(acct: model.Account) -> list:
    """-> [{_id, name, ...}], paused and running alike."""
    key = transport.resolve_key(acct.key_env)
    out = transport.get("/campaigns/getAll", key)
    camps = out.get("campaigns") if isinstance(out, dict) else out
    return list(camps or [])


def resolve_list_id(acct: model.Account, list_name: str) -> str:
    for l in prospect_lists(acct):
        if (l.get("name") or "").strip() == list_name.strip():
            return l.get("_id") or ""
    raise WaalaxyError(f"waalaxy list '{list_name}' not found on account "
                       f"'{acct.name}' (fail-closed - create it in Waalaxy or "
                       f"fix the route; nothing imports into a guessed list)")


def resolve_campaign_id(acct: model.Account, campaign_name: str) -> str:
    for cmp in campaigns(acct):
        if (cmp.get("name") or "").strip() == campaign_name.strip():
            return cmp.get("_id") or ""
    raise WaalaxyError(f"waalaxy campaign '{campaign_name}' not found on account "
                       f"'{acct.name}' (fail-closed)")


def add_prospects(acct: model.Account, prospects: list, list_id: str,
                  *, campaign_id: str | None = None) -> list:
    """POST /prospects/addProspectFromIntegration - THE ONLY WRITE. One attempt.

    prospects: [{url, customProfile?, customVariables?}]. Returns the per-prospect
    outcome rows (importCode / addToCampaignCode) the ledger reconciles against.
    Duplicate handling is left at the API defaults (no duplicates created, no
    cross-list moves, existing data never overwritten) - every non-default is a
    way to mutate prospects the owner curated by hand.
    """
    key = transport.resolve_key(acct.key_env)
    payload = {"prospects": prospects, "prospectListId": list_id,
               "origin": {"name": model.origin()}}
    if campaign_id:
        payload["campaignId"] = campaign_id
    out = transport.post("/prospects/addProspectFromIntegration", key, payload)
    rows = out.get("prospects") if isinstance(out, dict) else out
    log.info("waalaxy.import", account=acct.name, sent=len(prospects),
             returned=len(rows or []), enrolled=bool(campaign_id))
    return list(rows or [])
