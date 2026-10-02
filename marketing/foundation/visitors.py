"""Which website visits were people: the one rule, used by the daily report and the marketing foundation.

WHY (2026-10-02, measured): on Oct 1, 53 of 67 visits to ownbox.io and brian-macdonald.com came from
data-center networks. Mail security scanners (Microsoft above all) open every link in a cold email
before the recipient sees it, so a business that sends cold email sees its traffic inflated several
times over. The owner's own test visits were three more.

THE RULE, per visit (a PostHog session):
  1. A browser its owner marked (?internal=on, so every event carries internal_user) is "you".
  2. A visit from a data-center network is a scanner unless it behaves like a person: it lasts at least
     ENGAGED_SECONDS and clicks or views a second page. Microsoft's network also carries Windows 365 and
     Entra users, and some prospects browse through a VPN, so those still count. Clicks alone do not:
     measured over Sep 23 to Oct 2, 20 of 58 data-center visits clicked (Microsoft's scanner clicks the
     menu, "Resources", "Machines", the price button) and 57 of the 58 ended within 26 seconds.
  3. Everything else is a person, including a visit with no address and an address the table does not
     know. Visits from AI apps (Claude, ChatGPT and the like) are people, the ones the AEO work exists to
     win, and are counted separately so they show.

THE NETWORK TABLE is iptoasn.com's public-domain IP-to-ASN file, read locally. No visitor address leaves
the machine and nothing is paid per lookup. `AsnTable.load()` reads a .tsv or .tsv.gz path. A box reads the
copy bundled with its release (`TABLE`, `bundled()`): the same file cut down to DATA_CENTERS' networks by
scripts/refresh_datacenter_ranges.py, which OSDev1 runs at each release cut. So a buyer pastes no key, the box
calls no lookup service per visit, and the address is read from PostHog into memory and never written on the
box (WebDev2, the foundation's half, OSDev1's ruling 2026-10-02).

THE OWNER'S OWN VISITS (rule 1) are also the ones his PostHog project's own test-account filters leave out, on
the foundation's side (`test_account`), and never by user agent.

Pure functions, no imports from the box, so the standalone report script and the foundation share it.
"""
from __future__ import annotations

import bisect
import gzip
import ipaddress
import re
from pathlib import Path

# A change to anything that decides who is left out bumps this, and the foundation re-counts 28 days once
# (marketing/foundation/sync.recount), so this week and last both count people.
RULE = "people-1"

# Networks no person browses from: mail scanners, clouds, hosting. iCloud Private Relay (Akamai, Fastly,
# Cloudflare) carries people, so those are not here.
DATA_CENTERS = {
    8075: "Microsoft", 8070: "Microsoft", 8068: "Microsoft",
    15169: "Google", 396982: "Google", 36040: "Google", 19527: "Google",
    16509: "Amazon", 14618: "Amazon", 32934: "Facebook",
    14061: "DigitalOcean", 16276: "OVH", 24940: "Hetzner", 63949: "Linode",
    20473: "Vultr", 396362: "Leaseweb", 60781: "Leaseweb", 28753: "Leaseweb",
    197540: "netcup", 212238: "Datacamp", 64286: "LogicWeb", 9009: "M247",
    134756: "Chinanet IDC",
}

# A DATA-CENTER VISIT IS A PERSON ONLY IF IT LASTED THIS LONG, and clicked or reached a second page (OSDev1's
# ruling on OSDev5's data, 2026-10-02: Microsoft's scanner clicks menus, and 57 of 58 data-center visits ended
# within 26 seconds).
ENGAGED_SECONDS = 60

# posthog-js names these browsers from the app's user agent. Their visitors are people.
AI_APPS = ("Claude", "ChatGPT", "Perplexity", "Copilot", "Gemini")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class AsnTable:
    """IP address to ASN, from iptoasn.com's ip2asn file (start, end, asn, country, name per line)."""

    def __init__(self, rows):
        self._v4 = sorted((s, e, a) for s, e, a, v in rows if v == 4)
        self._v6 = sorted((s, e, a) for s, e, a, v in rows if v == 6)
        self._starts4 = [r[0] for r in self._v4]
        self._starts6 = [r[0] for r in self._v6]

    @classmethod
    def load(cls, path: str) -> "AsnTable":
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            return cls.parse(f)

    @classmethod
    def parse(cls, lines) -> "AsnTable":
        rows = []
        for line in lines:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[2] in ("", "0"):
                continue
            try:
                start, end = ipaddress.ip_address(parts[0]), ipaddress.ip_address(parts[1])
                rows.append((int(start), int(end), int(parts[2]), start.version))
            except ValueError:
                continue
        return cls(rows)

    def asn(self, ip: str) -> int | None:
        try:
            addr = ipaddress.ip_address((ip or "").strip())
        except ValueError:
            return None
        n = int(addr)
        rows, starts = (self._v4, self._starts4) if addr.version == 4 else (self._v6, self._starts6)
        i = bisect.bisect_right(starts, n) - 1
        if i >= 0 and rows[i][0] <= n <= rows[i][1]:
            return rows[i][2]
        return None


YOU = "you"                   # every reason that is the owner's own starts with this


def classify(visit: dict, table: AsnTable | None) -> str | None:
    """Why a visit is left out, or None when it was a person. `visit` has ip, internal, pageviews, clicks
    and seconds (first event to last), and, on the foundation's side, test_account: True when the PostHog
    project's own test-account filters leave it out."""
    if visit.get("internal"):
        return "you (marked browser)"
    if visit.get("test_account"):
        return "you (test account)"
    engaged = (visit.get("seconds") or 0) >= ENGAGED_SECONDS and (
        (visit.get("pageviews") or 0) >= 2 or (visit.get("clicks") or 0) >= 1)
    if engaged:
        return None
    asn = table.asn(visit.get("ip") or "") if table else None
    name = DATA_CENTERS.get(asn) if asn else None
    return f"{name} scanners" if name else None


def ai_app(browser: str | None) -> str | None:
    """The AI app a person arrived from, when their browser is one."""
    return browser if browser in AI_APPS else None


def safe_ids(ids) -> list:
    """Only UUID-shaped session ids may enter a query; anything else is dropped, never escaped."""
    return sorted(i for i in ids if isinstance(i, str) and _UUID.match(i))


# ── the foundation's half (WebDev2): the bundled table, a site-day sorted, the clause, the words ──────────

TABLE = Path(__file__).with_name("data") / "datacenter_ranges.tsv.gz"
_BUNDLED: list = []


def bundled() -> AsnTable:
    """The table shipped with this release, read once."""
    if not _BUNDLED:
        _BUNDLED.append(AsnTable.load(str(TABLE)))
    return _BUNDLED[0]


def network(ip, table: AsnTable | None = None) -> str | None:
    """The data-center network an address belongs to ("Microsoft"), "" for any other address, None when there is
    no address or it can't be read."""
    try:
        ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return None
    asn = (table or bundled()).asn(str(ip).strip())
    return DATA_CENTERS.get(asn, "") if asn else ""


def sort(visits, table: AsnTable | None = None) -> tuple[list, dict, int]:
    """One site-day's visits, each a dict with sid and what `classify` reads -> (the session ids to leave out, safe
    for a query; {reason: visits}; visits whose address couldn't be checked, counted as people).

    A visit that should be left out but whose id is not a well-formed session id can't be named in a query, so it is
    counted as a person and as not checked, never guessed at."""
    table = table or bundled()
    out, why, unchecked = [], {}, 0
    for v in visits:
        reason = classify(v, table)
        if reason is None:
            unchecked += network(v.get("ip"), table) is None
            continue
        if not safe_ids([v.get("sid")]):
            unchecked += 1
            continue
        out.append(v["sid"])
        why[reason] = why.get(reason, 0) + 1
    return safe_ids(out), why, unchecked


def keep(left_out) -> str:
    """The HogQL clause that keeps only people's visits. Only UUID-shaped ids are named (safe_ids), so nothing a
    visitor sends can reach the query."""
    ids = safe_ids(left_out)
    return "1 = 1" if not ids else "$session_id not in (" + ", ".join(f"'{s}'" for s in ids) + ")"


def say(why) -> str:
    """The visits left out, in the owner's words (OSDev1's ruling): "26 mail-scanner visits and 3 of yours". "" when
    none. `why` is {reason: visits} or [(reason, visits)]."""
    pairs = list(why.items() if isinstance(why, dict) else why)
    yours = sum(int(n or 0) for k, n in pairs if str(k).startswith(YOU))
    scanners = sum(int(n or 0) for k, n in pairs if not str(k).startswith(YOU))
    parts = ([f"{scanners:,} mail-scanner visit{'s' if scanners != 1 else ''}"] if scanners else []) \
        + ([f"{yours:,} of yours"] if yours else [])
    return " and ".join(parts)
