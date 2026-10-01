"""Apps the owner connects to their box, each through the app's own MCP server.

docs/SCOPE_CONNECTIONS_MCP_FIRST.md, phase 1, owner-approved 2026-10-01 ("Approve all four with your
recommendations"). The owner gives an app's MCP address (and its token, when the app uses one) on Data sources.
The box asks the app what it can do, turns on only the tools the app says only read, and serves them as its own
tools under the `read:apps` capability. A coworker in a shift calls them like any other tool, so the run's
capabilities, its receipt and the box's one gateway all apply unchanged.

    client.py   speaks MCP to the app (Streamable HTTP), public https only
    store.py    what is connected and which tools are on; the address and token live in box_secrets
    gateway.py  registers the tools that are on with this box's MCP, current as of each request
"""
