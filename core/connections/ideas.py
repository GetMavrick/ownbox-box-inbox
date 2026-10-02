"""Ideas for what to connect: the table a new owner arrives at on Data Sources.

Owner, 2026-10-02: "We want a clean Page for customers to arrive at which gives them lots of ideas for things to
connect. I would say Google search console would be another top choice, notion would be one, Post hog would be another
top choice. And then do some quick research on what the most popular MCPs are." Then: "a table format where it's like
name of the app and then a sentence on what you can do with it and what business use case and outcome." He approved
this list from a preview the same day.

SUGGESTED, NEVER ASSUMED (owner, 2026-10-02: "We need to suggest things, but not assume everybody is going to want to
connect"). An idea is a row in a table with a Connect button that fills in the form; nothing is connected, scheduled or
shown as "not connected" until the owner connects it, and an idea already connected leaves the list.

ONLY OFFICIAL, HOSTED MCP SERVERS (OSDev1's ruling): each address is the vendor's own, as its documentation gives it,
checked 2026-10-02 at the page in `doc`, and each lets any client connect by its address and the app's own sign-in.
Apps whose server needs a registered or allow-listed client first (Slack, Asana, Dropbox, Jira and Confluence,
Intercom, Square, Gmail and Google Drive) are left out until a box can connect them the same way. HubSpot was on the
list he approved and came off in OSDev1's review: the box's own discovery against mcp.hubspot.com says it needs the box
registered by hand, and HubSpot's docs agree. Each address left was checked live by OSDev1 the same day (PayPal's is
/mcp; /http answers 404).

Google Search Console has no MCP server; the box connects it itself, with one Google sign-in, on its own screen.

These are apps, not departments: core may name them (tests/test_core_boundary.py names departments).
"""
from __future__ import annotations

from urllib.parse import urlsplit

SEARCH_CONSOLE = "/settings/aeo/google"   # core/dash/google_search.py: the box's own Google sign-in, on every box

# (slug, name, category, the vendor's MCP address, what it does for the business, the vendor's page it was checked at)
MCP_IDEAS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("notion", "Notion", "Docs and wiki", "https://mcp.notion.com/mcp",
     "Your coworkers answer questions from your company wiki and flag pages that are out of date, so your team stops "
     "asking the same thing twice.",
     "https://developers.notion.com/guides/mcp/get-started-with-mcp"),
    ("posthog", "PostHog", "Website analytics", "https://mcp.posthog.com/mcp",
     "Find the step where website visitors give up, and which pages turn visitors into customers.",
     "https://posthog.com/docs/model-context-protocol"),
    ("stripe", "Stripe", "Payments", "https://mcp.stripe.com",
     "Each morning, see failed renewals and unpaid invoices, so you know who to chase before revenue slips.",
     "https://docs.stripe.com/mcp"),
    ("calendly", "Calendly", "Scheduling", "https://mcp.calendly.com",
     "See tomorrow's booked meetings with who is coming and why, so you walk in prepared.",
     "https://developer.calendly.com/docs/mcp/calendly-mcp-server"),
    ("monday", "monday.com", "Projects", "https://mcp.monday.com/mcp",
     "A daily list of overdue work and who owns it, without opening a single board.",
     "https://developer.monday.com/api-reference/docs/integrate-with-monday-mcp"),
    ("airtable", "Airtable", "Database", "https://mcp.airtable.com/mcp",
     "Ask plain questions of your inventory, client tracker or content calendar, without building a view.",
     "https://airtable.com/developers/agents/mcp/getting-started"),
    ("klaviyo", "Klaviyo", "Email marketing", "https://mcp.klaviyo.com/mcp?read-only=true",
     "Learn which campaigns and flows actually made money last month, and which to retire.",
     "https://developers.klaviyo.com/en/docs/connect_to_the_klaviyo_mcp_server"),
    ("pipedrive", "Pipedrive", "CRM", "https://mcp.pipedrive.ai/mcp",
     "Deals with no activity in two weeks, each with a suggested next step.",
     "https://support.pipedrive.com/en/article/mcp-claude"),
    ("clickup", "ClickUp", "Projects", "https://mcp.clickup.com/mcp",
     "A standup summary written for you: what moved, what is stuck, and who needs help.",
     "https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server"),
    ("paypal", "PayPal", "Payments", "https://mcp.paypal.com/mcp",
     "A weekly list of open disputes and unpaid invoices, so cash doesn't sit in limbo.",
     "https://developer.paypal.com/tools/mcp-server/"),
    ("wix", "Wix", "Website and bookings", "https://mcp.wix.com/mcp",
     "This week's bookings, orders and new contacts from your Wix site, in one place.",
     "https://www.wix.com/studio/developers/mcp-server"),
    ("fireflies", "Fireflies", "Meeting notes", "https://api.fireflies.ai/mcp",
     "Pull the action items and promised follow-ups out of your sales meetings, so nothing promised is forgotten.",
     "https://docs.fireflies.ai/getting-started/mcp-configuration"),
)

SEARCH_CONSOLE_IDEA = ("Google Search Console", "Search",
                       "See which searches bring people to your website and which pages are slipping, every week, so "
                       "you know what to write next.")


def get(slug: str) -> dict | None:
    """One MCP idea by its slug, for the Connect button that fills in the form."""
    for s, name, cat, url, what, doc in MCP_IDEAS:
        if s == str(slug or ""):
            return {"slug": s, "name": name, "category": cat, "url": url, "what": what, "doc": doc}
    return None


def _host(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def not_connected(connected_hosts: set[str]) -> list[dict]:
    """The MCP ideas whose server this box has not connected yet, in the approved order."""
    return [i for i in (get(s) for s, *_ in MCP_IDEAS) if i and _host(i["url"]) not in connected_hosts]
