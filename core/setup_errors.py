"""Every setup error a buyer can hit names its fix and links the exact page (plan #1857, launch bar 1).

Owner, 2026-10-01: setup fixes itself, and its errors name the fix. On 2026-10-03: "our customers don't have the
ability to ask you questions." Nearly every setup message already said what to do; almost none said WHERE, and a
buyer who meets one on a different screen (the Dashboard, the morning email, an AI's answer) has no way back to the
page that fixes it. So every setup page renders its errors through `card()`, which adds the one link that matters.

The sentences themselves stay with the code that knows what went wrong (claude_login, box_secrets, connections,
box_mail, google_search). tests/test_setup_errors_name_their_fix.py holds both halves: each literal error sentence in
those files says what to do, and each page here is a real page a buyer can open.
"""
from __future__ import annotations

import html

# WHERE EACH SETUP AREA IS FIXED: (address, the name on the menu).
PAGES = {
    "ai": ("/settings/ai", "AI Account"),
    "mcp": ("/settings/agent", "MCP Server"),
    "sources": ("/settings/sources", "Data Sources"),
    "search_console": ("/settings/aeo/google", "Search Console"),
    "email": ("/settings/email", "Sending Email"),
    "mobile": ("/settings/mobile", "Mobile App"),
    "access": ("/settings/access", "Server Access"),
    "updates": ("/settings/updates", "Updates"),
}
SUPPORT = "support@ownbox.io"


def link(area: str) -> str:
    """The page that fixes `area`, as a link."""
    href, name = PAGES[area]
    return f'<a href="{href}">Fix it on {html.escape(name)} &rarr;</a>'


def card(area: str, said: str, *, head: str = "That didn't work") -> str:
    """A setup error: what went wrong and what to do, then the page that fixes it."""
    return (f'<div class="card setup-error" style="border-color:var(--danger)"><h2>{html.escape(head)}</h2>'
            f'<p>{html.escape(str(said or ""))}</p><p>{link(area)}</p></div>')
