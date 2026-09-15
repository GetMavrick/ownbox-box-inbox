"""Waalaxy gateway (docs/WAALAXY_INTEGRATION_PLAN.md) - the LinkedIn arm's socket.

AIOS decides who, which list, what personalization, and when; campaigns, copy,
limits and account warming live in Waalaxy where the owner builds them by hand.
The API is four endpoints and exactly one write. Everything here is dark until
`waalaxy.enabled` flips true in config AND a per-subaccount key resolves.

THREE SUBACCOUNTS (owner, 2026-09-01): three LinkedIn accounts on one Waalaxy
team, to raise daily outreach volume. Each subaccount has its own API key and
its own lists/campaigns, so `accounts` is a first-class config dimension - a
push names the account it runs as, and nothing ever spans two.
"""
