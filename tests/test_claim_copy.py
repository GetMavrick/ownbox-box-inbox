"""The claim screens say what the copy doc says — checked against the doc, not against a copy.

THE FAILURE THIS EXISTS TO STOP HAPPENED. `/claim` was built before
docs/COPY_INBOX_FIRST_RUN.md §1 was written, and when the copy landed nothing compared the two.
The shipped screens quietly:

  · dropped §1.1's closing line — "This link works once. After you use it nobody else can claim
    this box — including us." — which the doc marks ⚠️ DO NOT CUT, because it is the entire
    ownership argument delivered at the one moment it is provable
  · opened the wrong-code state with "That code does not match this box", which is the "invalid
    code" accusation §1.2 forbids by name, at the moment the buyer is least sure of themselves
  · never named the box back to them at all

None of that is a bug a test of behaviour would ever catch. Every screen rendered, every status
code was right, and the first thing a paying customer saw was still wrong.

SO THIS SUITE PARSES THE DOC. It reads §1's fenced blocks, pulls the lines out of them, and
requires the rendered pages to contain them. Hardcoding the phrases here would just move the
duplication one file over and drift the same way — the point is that the doc is the source and
the screens are held to it, so changing a screen means changing the doc first.

Run: python tests/test_claim_copy.py
"""
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "COPY_INBOX_FIRST_RUN.md"

if not DOC.is_file():
    # A SOLD BOX SHIPS NO docs/. This suite ships into every box (it imports only core), and a
    # check that is meaningful only in the repo has to SAY SO where it does not apply rather than
    # die on a missing file and take the box's whole suite run down with it — which is exactly
    # what test_inbox_design did to CI once already.
    print("  --   no docs/ here — this box is not the repo, so there is no copy to check against")
    sys.exit(0)

_T = tempfile.mkdtemp()
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(_T, "copy.db")
os.environ["AIOS_PROVISION_JSON"] = os.path.join(_T, "provision.json")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state                                                # noqa: E402

state.init_db()

from core import claim                                                # noqa: E402
from core.dispatch import app as flask_app                            # noqa: E402

ORDER = "cs_live_Q7rTt2mK9xLp4vNb8wZa3cYd6eFg1hJk"
HOST = "testco.ownbox.app"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def provision(order=ORDER, host=HOST):
    with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
        json.dump({"buyer": "Ownbox Test Co", "order": order, "host": host,
                   "box_type": "customer_voice"}, fh)


def unclaim():
    with state.connect() as c:
        c.execute("DELETE FROM box_claim")


# ── reading the copy out of the doc ──────────────────────────────────────────────────
def doc_block(heading: str) -> str:
    """The fenced block under a `### <heading>` section of the copy doc."""
    text = DOC.read_text(encoding="utf-8")
    start = text.find(f"### {heading}")
    assert start >= 0, f"the doc has no section '{heading}' — did it get renamed?"
    fence = text.find("```", start)
    end = text.find("```", fence + 3)
    assert 0 < fence < end, f"no fenced copy under '{heading}'"
    return text[fence + 3:end]


def phrases(block: str) -> list[str]:
    """The sentences a screen must contain, lifted out of a fenced copy block.

    The blocks are written for a person to read — `h1:`, `sub:`, `body:`, `small:`, wrapped, with
    `[ Buttons ]` and `<placeholders>`. This turns them into literal fragments to look for:
    labels dropped, wrapping collapsed, and anything in angle brackets treated as a hole, because
    `<hostname>` is filled in per box and cannot be matched literally.
    """
    out, current = [], None
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            out.append(line.strip("[] ").strip())       # a button label
            current = None
            continue
        m = re.match(r"^(h1|sub|body|small):\s*(.*)$", line)
        if m:
            current = m.group(2).strip()
            out.append(current)
        elif out and current is not None:               # a wrapped continuation of the last line
            out[-1] = out[-1] + " " + line
    frags = []
    for item in out:
        for piece in re.split(r"<[^>]+>", item):        # <hostname> is a hole, not text
            piece = re.sub(r"\s+", " ", piece).strip(" .,—-")
            if len(piece) > 12:
                frags.append(piece)
    return frags


def page(path="/claim"):
    return flask_app.test_client().get(path).get_data(as_text=True)


def contains(haystack: str, needle: str) -> bool:
    """Is this text VISIBLE TO A PERSON on the page?

    Text nodes plus the attributes a person actually reads — `placeholder` and `aria-label`.
    Stripping tags alone was not enough and the difference is not pedantry: the doc writes a
    field as `[ Your email address ]`, which is rendered as a placeholder, so a tags-stripped
    check reported §1.1's field labels missing when they were on the screen. Attribute values
    are listed by name rather than matching the raw HTML, because matching raw HTML would let a
    phrase "pass" from inside a comment or a URL and quietly stop meaning anything.
    """
    return re.sub(r"\s+", " ", needle) in _visible(haystack)


def _visible(haystack: str) -> str:
    """Everything on the page a person can read, and nothing else.

    `<script>` and `<style>` BODIES ARE DROPPED FIRST, not just their tags. Stripping tags alone
    leaves the stylesheet's own text in the haystack, and a check run over that is a check that
    can pass — or fail — on a CSS declaration: the §2.7 case in this file once "found a lecture"
    by matching `text-transform:uppercase` in the dashboard's stylesheet.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", haystack, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    shown = " ".join(re.findall(r'(?:placeholder|aria-label)="([^"]*)"', haystack))
    return re.sub(r"\s+", " ", text + " " + shown)


# ── 1. the screens say what the doc says ─────────────────────────────────────────────
def test_the_ready_screen_says_what_the_doc_says():
    print("test_the_ready_screen_says_what_the_doc_says")
    unclaim()
    provision()
    html = page(f"/claim?c={ORDER}")
    missing = [f for f in phrases(doc_block("1.1 Valid code, not yet claimed"))
               if not contains(html, f)]
    ok("every line of §1.1 is on the screen", not missing, str(missing))
    # CALLED OUT SEPARATELY BECAUSE THE DOC CALLS IT OUT SEPARATELY. "Do not cut it for space" is
    # the kind of instruction that gets cut for space, and it went missing once already.
    ok("...including the ownership line the doc marks DO NOT CUT",
       contains(html, "nobody else can claim this box"))
    ok("the box names itself back to the buyer", HOST in html, "no hostname on the screen")


def test_a_box_with_no_hostname_drops_the_clause_rather_than_printing_a_hole():
    print("test_a_box_with_no_hostname_drops_the_clause_rather_than_printing_a_hole")
    unclaim()
    provision(host="")
    html = page(f"/claim?c={ORDER}")
    ok("the screen still renders", "Your box is ready" in html)
    ok("...and shows no empty placeholder where the hostname would be",
       "—  ." not in html and "yours —  " not in html and "<hostname>" not in html)
    ok("...while the ownership line survives", contains(html, "nobody else can claim this box"))
    provision()


def test_the_wrong_code_state_never_accuses_the_buyer():
    print("test_the_wrong_code_state_never_accuses_the_buyer")
    unclaim()
    provision()
    from core import dash as _dash
    _dash._fails.clear()
    html = flask_app.test_client().post("/claim", data={
        "c": "cs_live_not_the_right_one", "email": "buyer@testco.com",
        "password": "a-long-enough-password"}).get_data(as_text=True)
    missing = [f for f in phrases(doc_block("1.2 Wrong or missing code")) if not contains(html, f)]
    ok("every line of §1.2 is on the screen", not missing, str(missing))
    # THE DOC FORBIDS THIS PHRASE BY NAME: "Never 'invalid code'. It tells the buyer nothing they
    # can act on and reads as an accusation at the moment they are least sure of themselves."
    ok("...and the words the doc forbids appear nowhere on it",
       "invalid code" not in html.lower() and "does not match" not in html.lower(),
       "the accusation §1.2 bans by name is back")
    _dash._fails.clear()


def test_the_throttle_reads_as_protection_not_as_a_fault():
    print("test_the_throttle_reads_as_protection_not_as_a_fault")
    unclaim()
    provision()
    from core import dash as _dash
    _dash._fails.clear()
    c = flask_app.test_client()
    html = ""
    for i in range(_dash._FREE_ATTEMPTS + 3):
        r = c.post("/claim", data={"c": f"cs_live_wrong_{i}", "email": "b@t.com",
                                   "password": "a-long-enough-password"})
        if r.status_code == 429:
            html = r.get_data(as_text=True)
            break
    ok("the throttle is reached", bool(html), "never got a 429 to read")
    missing = [f for f in phrases(doc_block("1.3 Throttled")) if not contains(html, f)]
    ok("every line of §1.3 is on the screen", not missing, str(missing))
    ok("...and it says WHY, so it stops reading like a fault",
       contains(html, "so nobody can guess their way into your box"))
    _dash._fails.clear()


def test_the_claimed_state_offers_both_paths():
    print("test_the_claimed_state_offers_both_paths")
    unclaim()
    provision()
    claim.claim_box(code=ORDER, email="buyer@testco.com", password="a-long-enough-password")
    html = page("/claim")
    missing = [f for f in phrases(doc_block("1.4 Already claimed")) if not contains(html, f)]
    ok("every line of §1.4 is on the screen", not missing, str(missing))
    # "Both paths, always. A dead end here is a support ticket at best."
    ok("...the sign-in path is a real link", 'href="/dash/login"' in html)
    ok("...and the other path — reply to the welcome email — is spelled out",
       contains(html, "reply to your welcome email"))
    # Still no oracle: the answer cannot vary with the code, or the claimed page becomes a way to
    # test guesses long after claiming itself stopped working.
    ok("the answer is identical with a right code and a wrong one",
       page(f"/claim?c={ORDER}") == page("/claim?c=cs_live_wrong"))


def test_a_short_password_is_a_typo_not_a_failed_claim():
    print("test_a_short_password_is_a_typo_not_a_failed_claim")
    # §2.7, written by OSDev4 after I flagged this as the one state §1 did not cover. Its ruling
    # is "do not write this line — render the one the rule already returns", because the rule is
    # built from MIN_PASSWORD and prose is not: §2.7 records that its own first draft specified
    # ten against a box that refuses at twelve.
    unclaim()
    provision()
    from core import dash as _dash
    _dash._fails.clear()
    SHORT = "short"
    html = flask_app.test_client().post("/claim", data={
        "c": ORDER, "email": "Buyer@TestCo.com", "password": SHORT}).get_data(as_text=True)

    ok("the screen shows the sentence the RULE returns, not one written beside it",
       claim.password_problem(SHORT) in html, claim.password_problem(SHORT))
    ok("...so the screen cannot disagree with the box about the number",
       str(claim.MIN_PASSWORD) in html)
    # THE CODE STAYS VALID AND THEY STAY ON THE PAGE. "A box that spends its single-use code on
    # a typo is a support ticket on the day of the sale."
    ok("the form comes back rather than a dead end with a Try-again link",
       'name="password"' in html and 'action="/claim"' in html)
    ok("...still carrying the code, so the claim is not spent", ORDER in html)
    ok("...and their address, so they are not retyping it", "Buyer@TestCo.com" in html)
    ok("...and the claim genuinely still works afterwards",
       claim.claim_box(code=ORDER, email="buyer@testco.com",
                       password="a-long-enough-password")["role"] == "owner")
    # "Never echo what was typed. Not in the field, not in the message, not in a log."
    ok("the password is nowhere on the page", SHORT not in html.replace("Buyer@TestCo.com", ""))
    # "No lecture. No strength meter, no 'for your security', no list of character classes."
    # CHECKED AGAINST VISIBLE TEXT, not the raw document: the first version of this grepped the
    # whole response and matched `text-transform:uppercase` in the dashboard's own stylesheet —
    # a check that fails on CSS is a check nobody can keep green honestly.
    shown = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                                       html, flags=re.S | re.I)).lower()
    shown = re.sub(r"<[^>]+>", " ", shown)
    ok("no lecture around it",
       not any(w in shown for w in ("for your security", "strength", "uppercase",
                                    "special character")), shown[:160])
    _dash._fails.clear()


# §2.8's LINE. It is written in docs/COPY_INBOX_FIRST_RUN.md §2.8, which lands in OSDev4's #1184
# and is NOT on this branch — so the sentence is pinned here as a literal AND checked against the
# doc the moment the section arrives. Both, deliberately: an `if the section exists` check alone
# would pass silently on a branch without it, and a check that can pass by a file being absent is
# the failure mode I have already shipped four times in this suite's siblings.
BAD_EMAIL_LINE = ("That does not look like an email address. This is the address you will sign "
                  "in with.")


def test_a_mistyped_address_never_shows_the_buyer_an_internal_name():
    print("test_a_mistyped_address_never_shows_the_buyer_an_internal_name")
    # §2.8, found by OSDev4 reviewing #1185 — by POSTING a malformed address to the built screens,
    # which is the only way this was ever going to surface. `ClaimRefused("bad_email")` carries no
    # detail, `str(e)` falls back to the kind, and `claim_submit` rendered `str(e)`: the literal
    # string `bad_email` appeared in red on the first screen a paying customer opens. Reproduced
    # here before the fix, so this assertion is known to fail against the old code.
    unclaim()
    provision()
    from core import dash as _dash
    _dash._fails.clear()
    html = flask_app.test_client().post("/claim", data={
        "c": ORDER, "email": "buyer-at-testco.com",
        "password": "a-long-enough-password"}).get_data(as_text=True)

    ok("the internal kind is nowhere a person can read it",
       not contains(html, "bad_email"), _visible(html)[:200])
    ok("...and the screen says what §2.8 says instead", contains(html, BAD_EMAIL_LINE))
    # THE SECOND SENTENCE IS NOT DECORATION. It is the only place in the whole funnel a buyer is
    # told what the address is FOR; without it, a person reading the field as a mailing-list
    # signup types a throwaway and locks himself out of the box he has just paid for.
    ok("...including the sentence that says what the address is for",
       contains(html, "the address you will sign in with"))
    # Same §2.7 shape: a typo is not a failed claim.
    ok("the form comes back rather than a dead end",
       'name="email"' in html and 'action="/claim"' in html)
    ok("...still carrying the code, so the claim is not spent", ORDER in html)
    ok("...and the claim genuinely still works afterwards",
       claim.claim_box(code=ORDER, email="buyer@testco.com",
                       password="a-long-enough-password")["role"] == "owner")
    # And if §2.8 has landed in the doc, the doc wins — the literal above may not drift from it.
    if "### 2.8" in DOC.read_text(encoding="utf-8"):
        for frag in phrases(doc_block("2.8 A buyer with the right code and a mistyped email "
                                      "address")):
            if frag != "bad_email":
                ok(f"doc §2.8: {frag[:56]}", contains(html, frag))
    _dash._fails.clear()


def test_no_refusal_kind_is_ever_visible_text():
    print("test_no_refusal_kind_is_ever_visible_text")
    from core import dash as _dash
    # THE KINDS ARE READ OUT OF THE SOURCE, NOT LISTED HERE. A list in this file would be a second
    # place to remember, and the next kind would get added to one of them only — which is exactly
    # how `bad_email` came to be raised with no words behind it. This check therefore covers kinds
    # nobody has written yet.
    kinds = set(re.findall(r'ClaimRefused\(\s*"([a-z_]+)"',
                           (ROOT / "core" / "claim.py").read_text(encoding="utf-8")))
    ok("the refusal kinds were found in core/claim.py at all", len(kinds) >= 4, sorted(kinds))
    routed = set(_dash._CLAIM_REFUSALS) | set(_dash._CLAIM_PROBLEMS) | set(_dash._CLAIM_REDIRECTS)
    orphans = sorted(k for k in kinds if k not in routed)
    ok("every kind has somewhere to go — a page, a line under the field, or a redirect",
       not orphans, str(orphans))
    # THIS ALREADY EARNED ITS KEEP. `already_claimed` was in neither copy table: its words are
    # real (§1.4) but it reached them through a redirect hardcoded in the view, so "the kind has
    # copy" was true by an arrangement nothing described. Writing the redirect down as data is
    # what makes the rule checkable at all.
    both = sorted(k for k in _dash._CLAIM_PROBLEMS if k in _dash._CLAIM_REFUSALS)
    ok("...and no kind is in two of them, which would make the screen depend on read order",
       not both, str(both))

    # THE STRUCTURAL HALF ABOVE, THE MEASURED HALF HERE. Drive every reachable refusal to a real
    # screen and read what a person sees. A table can be right while the renderer still reaches
    # past it — `problem=str(e)` did, for a year of nobody noticing.
    def screen(**form):
        _dash._fails.clear()
        c = flask_app.test_client()
        r = c.post("/claim", data={"password": "a-long-enough-password", **form})
        if r.status_code in (301, 302):                 # already_claimed renders at GET /claim
            r = c.get(r.headers["Location"])
        return r.get_data(as_text=True)

    good = {"c": ORDER, "email": "buyer@testco.com"}
    unclaim(); provision()
    seen = {"bad_code": screen(c="cs_live_not_the_order", email="buyer@testco.com"),
            "bad_email": screen(**{**good, "email": "buyer-at-testco.com"}),
            "bad_password": screen(**{**good, "password": "short"})}
    unclaim()
    with open(os.environ["AIOS_PROVISION_JSON"], "w", encoding="utf-8") as fh:
        fh.write("{}")                                   # a box that was never sold
    seen["not_sellable"] = screen(**good)
    provision()
    claim.claim_box(code=ORDER, email="buyer@testco.com", password="a-long-enough-password")
    seen["already_claimed"] = screen(**good)

    for kind, html in seen.items():
        ok(f"{kind}: the kind itself is not on the screen", not contains(html, kind),
           _visible(html)[:180])
        # AND THE SCREEN SAID SOMETHING. A blank page shows no kind either; the point is that a
        # person was told what happened, so require the copy this kind is supposed to carry.
        words = (_dash._CLAIM_PROBLEMS.get(kind) or _dash._CLAIM_REFUSALS.get(kind, ("", ""))[0])
        if kind != "bad_password":                       # its words come from the rule, not a table
            ok(f"...and it said what it is supposed to say", contains(html, words[:48]),
               _visible(html)[:180])
    unclaim()
    _dash._fails.clear()
def test_the_front_door_wears_the_product_not_the_operator_dashboard():
    print("test_the_front_door_wears_the_product_not_the_operator_dashboard")
    # FOUND BY RENDERING IT, not by reading the markup. The words were already right and the page
    # still said the wrong thing: /claim came through the operator shell — dark, a breadcrumb
    # reading "<operator> / Set up your box", a purple button, "Powered by <operator>" in the
    # footer. The first screen a paying customer sees, looking like an internal admin tool with
    # somebody else's name on it.
    unclaim()
    provision()
    html = page(f"/claim?c={ORDER}")

    # THE OPERATOR'S NAME IS THE TEST. `brand()` is whatever this box is branded as, and on the
    # claim screen it is the one name that must not appear: the buyer has never heard of them.
    from core.dash import brand
    ok("the operator's name is nowhere on the buyer's first screen",
       brand().lower() not in html.lower(), brand())
    ok("...and neither is the dashboard's footer", "powered by" not in html.lower())
    ok("...nor its breadcrumb", "crumb" not in html.lower())

    # ASSERTED ON THE BUTTON, not on the stylesheet. The first version looked for the accent
    # anywhere in the page and passed with the button turned dashboard-purple — the accent was
    # still present in the focus ring and the link colour. The one element a buyer reads as "this
    # is the product" is the thing they are about to press.
    import re as _re
    btn = _re.search(r"button\{[^}]*\}", html)
    ok("the button carries the product's accent, not the dashboard's",
       bool(btn) and "#e05d38" in btn.group(0), btn.group(0)[:90] if btn else "no button rule")
    ok("it paints its own background rather than borrowing a host's",
       "background:#eff2f4" in html.replace(" ", ""))
    ok("it is a page in its own right", html.lstrip().startswith("<!doctype html"))
    ok("...that fits a phone", "width=device-width" in html)
    ok("...and is never indexed — a claim link is not a public page",
       "noindex" in html)

    # A REAL LABEL, NOT A PLACEHOLDER. A placeholder disappears the moment someone starts typing,
    # which is exactly when a person checks what a field wanted. The render showed both at once,
    # saying the same words twice; the label is the half that survives focus.
    ok("every field has a label bound to it",
       'for="claim-email"' in html and 'id="claim-email"' in html
       and 'for="claim-pw"' in html and 'id="claim-pw"' in html)
    ok("...and does not repeat itself as a placeholder",
       'placeholder="Your email address"' not in html)


if __name__ == "__main__":
    test_the_ready_screen_says_what_the_doc_says()
    test_a_box_with_no_hostname_drops_the_clause_rather_than_printing_a_hole()
    test_the_wrong_code_state_never_accuses_the_buyer()
    test_the_throttle_reads_as_protection_not_as_a_fault()
    test_the_claimed_state_offers_both_paths()
    test_a_short_password_is_a_typo_not_a_failed_claim()
    test_a_mistyped_address_never_shows_the_buyer_an_internal_name()
    test_no_refusal_kind_is_ever_visible_text()
    test_the_front_door_wears_the_product_not_the_operator_dashboard()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
