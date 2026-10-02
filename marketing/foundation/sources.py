"""Where a visit came from, in words a person uses: search, an AI answer (and which assistant), social, email,
direct, or another site. Derived from the referring domain the store keeps raw, so the buckets can change
without a migration.
"""
from __future__ import annotations

AI = "ai"
SEARCH = "search"
SOCIAL = "social"
EMAIL = "email"
DIRECT = "direct"
OTHER = "other"

# THE ANSWER ENGINES, by the domain a visitor arrives from, and the name a person knows them by. Only answer
# engines count as AI: DuckDuckGo and Brave Search are ordinary search and would inflate the number an AEO buyer
# is paying to move (OSDev1's review of #1584; marketing/aeo_machine/posthog.py keeps the same list).
ASSISTANTS = {
    "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity", "www.perplexity.ai": "Perplexity",
    "gemini.google.com": "Gemini", "copilot.microsoft.com": "Copilot",
    "claude.ai": "Claude", "you.com": "You.com",
}
_SEARCH = ("bing.com", "duckduckgo.com", "search.brave.com", "yahoo.com", "ecosia.org", "baidu.com",
           "startpage.com", "qwant.com")
_SEARCH_FAMILIES = ("google", "yandex")          # google.com, www.google.co.uk, yandex.ru: the first labels say so
_SOCIAL = ("facebook.com", "instagram.com", "linkedin.com", "lnkd.in", "x.com", "t.co", "twitter.com",
           "youtube.com", "youtu.be", "tiktok.com", "reddit.com", "pinterest.com", "threads.net")
_EMAIL = ("mail.google.com", "outlook.live.com", "outlook.office.com", "outlook.office365.com",
          "mail.yahoo.com", "mail.proton.me")
_UTM_AI = {"chatgpt.com": "ChatGPT", "chatgpt": "ChatGPT", "perplexity": "Perplexity", "copilot": "Copilot"}


def _host(ref: str) -> str:
    return str(ref or "").strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]


def classify(ref: str, utm_source: str | None = None) -> tuple[str, str]:
    """(bucket, label). The label is the assistant for AI, the domain otherwise, "" for direct."""
    h = _host(ref)
    u = str(utm_source or "").strip().lower()
    if u in _UTM_AI:
        return AI, _UTM_AI[u]
    if h in ASSISTANTS:
        return AI, ASSISTANTS[h]
    if h in ("", "$direct", "direct"):
        return DIRECT, ""
    if h in _EMAIL or u == "email":
        return EMAIL, h
    labels = h.split(".")
    if (labels[0] in _SEARCH_FAMILIES or (labels[0] == "www" and len(labels) > 1 and labels[1] in _SEARCH_FAMILIES)) \
            or any(h == d or h.endswith("." + d) for d in _SEARCH):
        return SEARCH, h
    if any(h == d or h.endswith("." + d) for d in _SOCIAL):
        return SOCIAL, h
    return OTHER, h


def by_source(referrers: list[tuple[str, int]]) -> dict:
    """{bucket: visits} plus {"assistants": {name: visits}} from [(ref, visits), ...]."""
    out: dict = {AI: 0, SEARCH: 0, SOCIAL: 0, EMAIL: 0, DIRECT: 0, OTHER: 0, "assistants": {}}
    for ref, visits in referrers:
        bucket, label = classify(ref)
        out[bucket] += int(visits or 0)
        if bucket == AI:
            out["assistants"][label] = out["assistants"].get(label, 0) + int(visits or 0)
    return out
