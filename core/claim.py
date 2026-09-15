"""First login — the box hands itself over, once, to whoever bought it.

THE GAP THIS CLOSES. Provisioning builds a droplet, points a subdomain at it and emails the
buyer (docs/SPEC_OWNBOX_DELIVERY.md). Then the buyer arrives at a password box holding a
password only WE know, and there is no way in. Putting one in the email would mean Ownbox mints
a box secret and keeps a copy of it, which the delivery design refuses outright — the whole
premise is that the customer's box holds no secret of ours and we hold none of theirs.

So the box hands ITSELF over. It already has, on its own disk, the one fact only the buyer and
Stripe share: the Checkout Session id the droplet was built from, written at first boot to
/opt/aios/provision.json. Prove you hold it and you may set this box's owner, once.

WHAT THE CLAIM CODE IS AND IS NOT. It is a Stripe Checkout Session id — an identifier of a
completed purchase, not a payment credential and not a card. It opens exactly one door, exactly
once, on exactly one box, and the moment that door is used it opens nothing at all. That is why
it is safe to put in an email and why it is NOT safe to treat as a password: it is not secret
from Stripe, it appears in the buyer's own receipt, and it does not expire on its own. The
single-use claim is the control, not the secrecy of the string.

WHY THE PASSWORD IS THE BOX'S AND NOT THE USER'S. This box authenticates with ONE human
credential today: `DASH_TOKEN`, compared in `core/dash.login`. `users` rows are IDENTITY — who
is acting — and carry no password column, by design (core/state.py, the users table). Claiming
therefore sets THE BOX PASSWORD, stored here as a hash, and `login` accepts it. Giving every
user their own password is a real and separate piece of work (employees, resets, lockout policy)
and inventing it inside a delivery flow, at the exact moment the buyer cannot get in, is how a
box ends up with two half-built authentication schemes. One door, better hinged.

DASH_TOKEN KEEPS WORKING, FOREVER. Owner, 2026-09-07: "I don't ever wanna be locked out of these
machines." A claimed box accepts either credential; claiming ADDS a way in and removes none.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

from core import state
from core.logging import get_logger

log = get_logger(__name__)

# Written by provisioner/userdata.py at droplet creation, 0600 root:root. The dispatch service
# runs as root (deploy/aios-dispatch.service sets no User=), which is why this file is readable
# from a request at all — asserted in tests rather than assumed, because the day someone adds
# `User=aios` to that unit, first login stops working and nothing else does.
PROVISION_JSON = os.environ.get("AIOS_PROVISION_JSON", "/opt/aios/provision.json")

# scrypt, from the standard library. Not hand-rolled (owner, 2026-09-12: "Never hand roll
# anything"), no new dependency on a 1-vCPU box, and memory-hard rather than merely slow. n=2**14
# measures ~50ms on a dev box and a few hundred on the droplet: unnoticeable once per login,
# ruinous a billion times over. The parameters are stored IN the hash string so raising them
# later cannot strand a box whose owner set his password under the old ones.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_MAXMEM = 64 * 1024 * 1024        # 128*r*n is 16MB; OpenSSL's default cap is 32MB. Explicit.

MIN_PASSWORD = 12                 # length only, deliberately — see `password_problem`


class ClaimRefused(Exception):
    """The claim did not happen, and nothing changed.

    CARRIES A `kind`, NOT A SENTENCE TO RENDER. What a person should be told differs per state
    and the words belong to docs/COPY_INBOX_FIRST_RUN.md §1 — a screen rendering whatever string
    an exception happened to hold is how "invalid code" reaches a buyer, which that doc forbids
    by name. The `str()` stays useful for logs and for callers with no screen.
    """

    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        super().__init__(detail or kind)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32,
                        maxmem=_MAXMEM, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(s=salt.hex(), h=dk.hex(), **_SCRYPT)


def verify_password(password: str, stored: str) -> bool:
    """Constant-time against a stored hash. False for anything malformed — a corrupted row is
    a refusal, never an exception that a caller might mistake for a pass."""
    try:
        kind, n, r, p, salt_hex, want = str(stored or "").split("$")
        if kind != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), dklen=32,
                            n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM)
    except Exception:                       # noqa: BLE001 — unreadable is refused, not raised
        return False
    return hmac.compare_digest(dk.hex(), want)


def provisioned_order() -> str | None:
    """The Stripe Checkout Session id this box was built from, or None.

    None is the ORDINARY answer on a box nobody bought — the owner's own machine, a dev
    checkout, a box installed by hand. Those have nothing to claim and must say so plainly
    rather than render a form that can never succeed.
    """
    try:
        with open(PROVISION_JSON, encoding="utf-8") as fh:
            order = json.load(fh).get("order")
    except Exception:                       # noqa: BLE001 — absent, unreadable, or not JSON
        return None
    order = str(order or "").strip()
    return order or None


def provisioned_host() -> str:
    """The hostname this box was built as, or "". §1.1 names it back to the buyer — "This one is
    yours — <hostname>" — which is the cheapest possible proof they are on the right machine and
    not on a page that could belong to anybody."""
    try:
        with open(PROVISION_JSON, encoding="utf-8") as fh:
            return str(json.load(fh).get("host") or "").strip()
    except Exception:                       # noqa: BLE001 — absent or unreadable is simply no name
        return ""


def claimed() -> dict | None:
    """The claim row, or None. Cheap enough to call on every render of the claim page."""
    try:
        with state.connect() as c:
            row = c.execute("SELECT * FROM box_claim WHERE id = 1").fetchone()
    except Exception:                       # noqa: BLE001 — a database too old to have the table
        return None
    return dict(row) if row else None


def code_matches(code: str) -> bool:
    """Constant-time, and False when there is nothing to match.

    `compare_digest` on equal-length-independent input is the point: a `==` here leaks the length
    of the shared prefix, which for a fixed-format id like `cs_live_…` turns guessing from
    impossible into a few thousand requests. The throttle in the route is the other half — one
    without the other is not a defence (core/dash: "twelve wrong passwords answered in 2.2
    seconds", measured on the live box).
    """
    want = provisioned_order()
    got = str(code or "").strip()
    if not want or not got:
        return False
    return hmac.compare_digest(got, want)


def password_problem(password: str, *, email: str = "", code: str = "") -> str | None:
    """Why this password is unacceptable, or None.

    LENGTH ONLY, NO COMPOSITION RULES. Character-class requirements push people toward
    `Password1!` and are not what NIST has recommended for years; length is what buys work
    against a hash. The two extra refusals below are not composition rules but identity ones:
    a password equal to the address or to the claim code is not a password, it is the same
    string twice, and the claim code in particular travels in an email and a URL.
    """
    pw = str(password or "")
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if email and pw.strip().lower() == str(email).strip().lower():
        return "That is your email address, not a password."
    if code and pw.strip() == str(code).strip():
        return "That is the code from your email, not a password."
    return None


def claim_box(*, code: str, email: str, password: str,
              ip: str = "", user_agent: str = "") -> dict:
    """Claim this box. Returns the owner row. Raises ClaimRefused, having changed nothing.

    ORDER OF CHECKS IS THE POINT. The code is proven BEFORE the email and password are looked
    at, so a stranger without the code cannot learn anything about what this box will accept —
    and the already-claimed check comes first of all, so a second claimant is told the box is
    taken whether or not their code is right. A refusal that varies with the guess is a way to
    test guesses.
    """
    if claimed():
        raise ClaimRefused("already_claimed")
    if provisioned_order() is None:
        # Not a failure of the claim: this box was never sold. A separate state because the buyer
        # of a real box and the owner of a hand-installed one need completely different next steps.
        raise ClaimRefused("not_sellable")
    if not code_matches(code):
        raise ClaimRefused("bad_code")

    # PAST THIS LINE THE CALLER HAS PROVEN THEY HOLD THE ORDER, so telling them precisely what is
    # wrong with their email or password costs nothing: they are the buyer. A wrong code never
    # reaches here, so it can never draw a password complaint.
    #
    # WHAT THIS DOES AND DOES NOT PREVENT, stated accurately because the comment that used to sit
    # here overstated it. It is NOT a uniform refusal. Someone who guesses a code and submits a
    # deliberately bad password learns from the reply that the code was right, WITHOUT spending
    # the one-shot claim. That is a real distinction and it is accepted rather than overlooked:
    # the alternative is telling a paying customer "that link did not work" when their password
    # is simply eight characters, which is a support ticket and a refund risk at the worst moment.
    # The control is the throttle plus the shape of the secret — a Stripe Checkout Session id,
    # answered five times before the wait becomes exponential.
    email = str(email or "").strip().lower()
    if not email or "@" not in email or " " in email:
        raise ClaimRefused("bad_email")
    problem = password_problem(password, email=email, code=code)
    if problem:
        raise ClaimRefused("bad_password", problem)

    # THE EXISTING OWNER ROW, NOT A NEW ONE. Every session on this box already points at it
    # (migration 47), so minting a second owner would leave the box with two and the older
    # sessions pointing at the wrong one. Claiming gives that row the buyer's address.
    owner = state.owner_user()
    now = state._now()
    with state.connect() as c:
        # THE INSERT IS THE CLAIM. `CHECK (id = 1)` makes a second one fail inside SQLite, so
        # two simultaneous submits cannot both succeed — the check at the top of this function
        # is a courtesy that produces a good message, not the guarantee.
        try:
            c.execute(
                "INSERT INTO box_claim (id, claimed_at, order_id, user_id, email, pw_hash, "
                "ip, user_agent) VALUES (1,?,?,?,?,?,?,?)",
                (now, provisioned_order(), owner["id"], email, hash_password(password),
                 str(ip or "")[:64], str(user_agent or "")[:200]))
        except Exception as e:              # noqa: BLE001 — the loser of a race, almost surely
            log.warning("claim.refused_second", error=type(e).__name__)
            raise ClaimRefused("This box has already been set up. Sign in instead.") from e
        c.execute("UPDATE users SET email = ?, role = 'owner', active = 1 WHERE id = ?",
                  (email, owner["id"]))
    # THE AUDIT LINE. Who took this box, when, from where. It carries the order id and never the
    # password or the code — `core/logging` would scrub a key-ish value anyway, and a claim code
    # in a journal that survives the claim is a thing to not write rather than to redact.
    log.info("claim.box_claimed", order=provisioned_order(), user=owner["id"],
             email=email, ip=str(ip or "")[:64], at=now)
    return state.get_user(owner["id"]) or owner


def password_ok(password: str) -> bool:
    """Does this password open a CLAIMED box? False on an unclaimed one, always.

    The login's other credential is DASH_TOKEN and it is checked separately and still works —
    this only ever ADDS a way in (owner, 2026-09-07: "I don't ever wanna be locked out").
    """
    row = claimed()
    if not row:
        return False
    return verify_password(str(password or ""), str(row.get("pw_hash") or ""))
