"""The set-up screen renders whatever a machine registered — and knows none of it by name.

Assigned by OSDev1 (2026-09-16, 17:20): "the set-up screen renders onboarding.steps() with no
step-specific code", and "steps() adds the status `unavailable` — render it with its detail."

WHY A MADE-UP MACHINE. Every test here registers a step for a machine that does not exist, whose
key is a word no line of this product has ever contained. That is the only way to prove the claim:
a screen that renders `gutter_pigeons` — its title, its reason, its numbered instructions, its
password field masked, its link out drawn dead with the reason it is dead — cannot be a screen
that has an arm for Gmail and an arm for Zernio. If it renders a step it has never heard of, it
will render OSDev4's when he moves the inbox across, and the third machine's after that.

THE SEQUENCING THIS SUITE ALSO PINS. #1273 adds the seam and registers NOTHING; OSDev4 moves the
inbox onto it next. Between those landings `onboarding.steps()` is empty, and a screen that
rendered it unconditionally would show a paying customer a set-up page with nothing on it. So the
empty-seam case is a test, not an assumption: with nothing registered the old contract still
renders in full.

AND THE MOMENT SOMETHING IS REGISTERED, THE TWO ARE MERGED — this line used to read "the seam
wins", which was the intent when it was written and turned out to be a landmine (OSDev4,
2026-09-17, scope #1307). Winning meant the FIRST step registered through the seam, about anything
at all, deleted every step still in `box_secrets` from the buyer's screen: measured, three became
one. The concern underneath it was never "hide the old contract", it was "nobody sees a step
twice" — which `_setup_source` now guarantees by deduping on key, so a migrated step REPLACES its
predecessor in place and an unmigrated one still renders, once. That also makes the migration this
paragraph describes possible one step at a time instead of all three in a single commit.

Run: python tests/test_setup_screen_on_the_seam.py
"""
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB = pathlib.Path(tempfile.mkdtemp(prefix="setup-seam-")) / "box.db"
# BEFORE ANY core IMPORT — core.config.Settings reads os.environ at class-body time.
os.environ["AIOS_DB_PATH"] = str(_DB)
os.environ.setdefault("DASH_TOKEN", "test-dash-pw")

from core import state                                    # noqa: E402

state.init_db()

from core import box_secrets as bs                        # noqa: E402
from core import onboarding                               # noqa: E402

_failed = 0
# A word this product has never contained, so a screen cannot have an arm for it.
KEY = "gutter_pigeons"
MACHINE = "gutter_pigeons"


def ok(what: str, cond: bool, got: str = "") -> None:
    global _failed
    if cond:
        print(f"  ok   {what}")
    else:
        _failed += 1
        print(f"  FAIL {what}" + (f"  — {got}" if got else ""))


def _c():
    from core.config import settings
    from core.dispatch import app
    c = app.test_client()
    c.post("/dash/login", data={"token": settings.dash_token})
    return app, c


def _text(html_: str) -> str:
    import html as _h
    stripped = re.sub(r"(?s)<(script|style|svg).*?</\1>", " ", html_)
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _clear_seam() -> None:
    """The registry is a module global and these tests register into it. Reaching into `_STEPS`
    is deliberate: there is no public unregister, because a live box never unregisters."""
    onboarding._STEPS.clear()


def _register(*, state_fn, save_fn=None, link=None, key=KEY) -> list:
    """One fabricated step. `saved` is the list the machine appends to, so a test can assert on
    EXACTLY what core handed it — which is how the 'only declared fields' claim is checked."""
    saved: list = []
    onboarding.register_step(
        key=key, order=50, machine=MACHINE,
        title="Connect your gutter pigeons",
        why="So the birds know which roof is yours.",
        fields=({"name": "loft", "label": "Loft address", "type": "text",
                 "placeholder": "north tower"},
                {"name": "whistle", "label": "Whistle code", "type": "password"}),
        steps=("Open the loft hatch.", "Read the ring on the third bird.", "Type what it says."),
        note="Pigeons are not covered by your warranty.",
        state=state_fn,
        save=save_fn or (lambda values, *, user_id=None: saved.append((values, user_id))),
        link=link,
    )
    return saved


def test_a_step_the_screen_has_never_heard_of_renders_in_full():
    """THE ASSIGNMENT, stated as a test. Nothing below names Gmail or Zernio."""
    _clear_seam()
    _register(state_fn=lambda: {"status": "not_connected", "detail": ""})
    app, c = _c()
    html_ = c.get("/inbox/setup").get_data(as_text=True)
    words = _text(html_)
    ok("its title is on the screen", "Connect your gutter pigeons" in words)
    ok("...and the reason a buyer is being asked", "So the birds know which roof is yours." in words)
    for i, line in enumerate(("Open the loft hatch.", "Read the ring on the third bird."), 1):
        ok(f"...and instruction {i}, numbered", line in words and f"{i} {line}" in words, words[:160])
    ok("...and its note", "Pigeons are not covered by your warranty." in words)
    ok("...and both of its fields, by the labels the MACHINE chose",
       "Loft address" in words and "Whistle code" in words)
    ok("...with the password field rendered as a password, not as text",
       'type="password" name="whistle"' in html_, html_[html_.find("whistle") - 60:][:120])
    ok("...and its status in words a person can act on", "Not connected yet" in words)


def test_the_screen_contains_no_step_key_at_all():
    """A loop with `if key == "zernio"` in it is three bespoke flows wearing a for-statement.
    Asserted against the SOURCE of the screen, because a rendered page cannot show absence."""
    # THE MODULE IS ASKED WHERE IT LIVES. A hardcoded "marketing/customer_voice/app.py" read this
    # file relative to the CWD, and it shipped to a box that has no such machine: the box exporter
    # builds a per-box-type image, and a Lead box carries `lead_machine` and nothing else. It drops
    # a suite whose IMPORTS name a module the box lacks — and this suite reached the screen only
    # through `core.dispatch`, so the scan saw no dependency, the suite travelled, and it died in
    # the box with FileNotFoundError (caught by test_recipe_ships, which runs every shipped suite
    # inside a fresh Lead box). Importing the module fixes both halves at once: the exporter now
    # sees the real dependency, and the path stops being a guess about the working directory.
    #
    # AND THIS PROSE IS A `#` COMMENT, NOT A DOCSTRING, WHICH IS NOT A STYLE CHOICE.
    # `test_suite_integrity._drives_exporter` asks whether a suite RUNS the box exporter, and it
    # answers by looking for the script's name in the file with `#` comments stripped and every
    # string literal — docstrings included — deliberately kept. That is the conservative direction
    # and it is the right one: a real driver names the script in a string it hands to a subprocess.
    # Naming the script in a docstring here tripped it, and the remedy its message offers — add
    # this file to the exporter's skip set — is the wrong one, because this suite does not drive
    # the exporter and skip-listing it would hide that. Its own docstring records the same false
    # positive happening to test_demo_first_run. So the explanation moved out of the docstring
    # instead, which costs the reader nothing.
    from marketing.customer_voice import app as voice
    src = pathlib.Path(voice.__file__).read_text()
    body = src[src.find("# ── the set-up screen"):]
    body = body[:body.find("# ── the mailbox")] if "# ── the mailbox" in body else body
    for key in ("email", "zernio"):
        ok(f"the renderer never names {key!r}",
           f'== "{key}"' not in body and f"== '{key}'" not in body,
           body[max(0, body.find(f'== "{key}"') - 80):][:160])


def test_a_machine_that_cannot_answer_reads_unavailable_never_not_connected():
    """OSDev1, 17:20: render `unavailable` with its detail.

    THE SENTENCE MATTERS MORE THAN THE WORD. A mailbox that could not be CHECKED must not read as
    "not connected" — that sends someone to Google to make a new App password they did not need,
    and when it does not help they ask for a refund. Core decides this, not the machine: a state()
    that raises is `unavailable` with a sentence core supplies."""
    _clear_seam()

    def _explodes():
        raise RuntimeError("the loft is on fire")

    _register(state_fn=_explodes)
    entry = [e for e in onboarding.steps() if e["key"] == KEY][0]
    ok("core turns a raising state() into `unavailable`", entry["status"] == "unavailable",
       str(entry["status"]))
    ok("...with a sentence for the buyer", len(entry["detail"]) > 20, entry["detail"])
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    # Matched case-insensitively ON PURPOSE: the screen prints core's SENTENCE here rather than
    # the label, because printing both said the same thing twice (see the stutter test below), and
    # in the sentence the phrase is mid-clause — "This could not be checked just now."
    ok("the screen says the box could not check it",
       "could not be checked just now" in words.lower())
    ok("...in core's own sentence, whole", entry["detail"] in words, entry["detail"])
    # SCOPED TO THIS STEP'S ROW, not to the whole page (OSDev4, 2026-09-17). The assertion read
    # the entire document, which worked only while the seam REPLACED the old contract and the page
    # therefore held one step. `_setup_source` merges now, so email/zernio/AI-key render alongside
    # — and they are genuinely "Not connected yet" on a fresh box, which made a true page fail a
    # test about a different row. What this always meant is that THIS step must not be called
    # not-connected when the truth is that it could not be checked; that is what it says now, and
    # it still fails if the unavailable row ever claims not-connected.
    _row = words.split("Connect your gutter pigeons", 1)[-1][:240]
    ok("...and does NOT tell him it is not connected", "Not connected yet" not in _row, _row)
    ok("...and never leaks why it broke", "loft is on fire" not in words and "RuntimeError" not in words)


def test_unavailable_neither_stutters_nor_offers_to_replace_what_it_could_not_find():
    """BOTH OF THESE WERE FOUND BY RENDERING THE SCREEN AND READING IT, not by an assertion.

    THE STUTTER: core's sentence for `unavailable` reads "This could not be checked just now.
    Reload the page in a minute." under a label reading "Could not be checked just now". On the
    page that is the same thing twice in one breath. The sentence wins, because the sentence is
    the half that says what to do.

    THE WRONG CLAIM: the button said "Replace it". We just failed to CHECK this step, so we do not
    know that anything is stored — offering to replace a thing whose existence we could not
    establish is the same mistake `unavailable` exists to prevent, pointing the other way."""
    _clear_seam()

    def _explodes():
        raise RuntimeError("the loft is on fire")

    _register(state_fn=_explodes)
    app, c = _c()
    html_ = c.get("/inbox/setup").get_data(as_text=True)
    words = _text(html_)
    low = words.lower()
    ok("the page still says the box could not check it", "could not be checked just now" in low)
    ok("...ONCE, not twice", low.count("could not be checked just now") == 1,
       str(low.count("could not be checked just now")))
    ok("...and still tells him what to do about it", "Reload the page in a minute" in words)
    ok("the button does not offer to replace what we never found", "Replace it" not in words,
       words[:200])
    ok("...it offers to save", "Save" in words)


def test_a_status_outside_the_closed_set_is_unavailable_not_a_blank_row():
    """The other half of the same guard: a machine that answers `"probably"` is a machine that
    would otherwise put an unlabelled row in front of a paying customer."""
    _clear_seam()
    _register(state_fn=lambda: {"status": "probably", "detail": "hmm"})
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    ok("an unknown status renders as unavailable",
       "could not be checked just now" in words.lower(), words[:200])
    ok("...and the machine's own word never reaches the page", "probably" not in words.lower())


def test_saving_goes_through_core_and_the_machine_gets_only_what_it_declared():
    """A POST is the seam's other half. Core passes the DECLARED fields and nothing else, so a
    hidden field added to the form by hand cannot reach a store that happens to read it."""
    _clear_seam()
    saved = _register(state_fn=lambda: {"status": "not_connected", "detail": ""})
    app, c = _c()
    r = c.post("/inbox/setup", data={"step": KEY, "loft": "north tower", "whistle": "coo-coo",
                                     "admin": "1", "user_id": "usr_someone_else"})
    ok("a good save redirects rather than re-rendering", r.status_code in (302, 303),
       str(r.status_code))
    ok("...back to its own step", (r.headers.get("Location") or "").endswith(f"#{KEY}"),
       str(r.headers.get("Location")))
    ok("the machine was called exactly once", len(saved) == 1, str(len(saved)))
    values = saved[0][0] if saved else {}
    ok("...with the fields it declared", values.get("loft") == "north tower"
       and values.get("whistle") == "coo-coo", str(values))
    ok("...AND NOTHING ELSE — the posted extras never arrived",
       set(values) == {"loft", "whistle"}, str(sorted(values)))


def test_a_rejection_is_shown_against_its_own_step_and_the_password_is_not_put_back():
    _clear_seam()

    def _refuses(values, *, user_id=None):
        raise onboarding.StepRejected("That whistle code is four notes short.")

    _register(state_fn=lambda: {"status": "not_connected", "detail": ""}, save_fn=_refuses)
    app, c = _c()
    r = c.post("/inbox/setup", data={"step": KEY, "loft": "north tower", "whistle": "coo"})
    html_ = r.get_data(as_text=True)
    words = _text(html_)
    ok("a refusal re-renders the page rather than redirecting", r.status_code == 200,
       str(r.status_code))
    ok("...and says what to fix, in the machine's own words",
       "That whistle code is four notes short." in words)
    ok("THE PASSWORD IS ON NO PAGE", "coo-coo" not in html_ and 'value="coo"' not in html_)
    ok("...but what he typed in the other field is kept", 'value="north tower"' in html_,
       html_[html_.find("loft") - 40:][:160])


def test_the_link_out_stays_dead_until_the_step_is_past_not_connected():
    _clear_seam()
    _register(state_fn=lambda: {"status": "not_connected", "detail": ""},
              link={"label": "Choose which roofs", "url": "https://example.invalid/roofs",
                    "disabled_because": "Tell us the loft first.", "new_tab": True})
    app, c = _c()
    html_ = c.get("/inbox/setup").get_data(as_text=True)
    ok("the button is drawn", "Choose which roofs" in _text(html_))
    ok("...and plainly dead", 'aria-disabled="true"' in html_)
    ok("...saying why it is waiting", "Tell us the loft first." in _text(html_))
    ok("...and offering no href to press", 'href="https://example.invalid/roofs"' not in html_)

    _clear_seam()
    _register(state_fn=lambda: {"status": "connected", "who": "north tower", "detail": ""},
              link={"label": "Choose which roofs", "url": "https://example.invalid/roofs",
                    "new_tab": True})
    app, c = _c()
    html_ = c.get("/inbox/setup").get_data(as_text=True)
    ok("once the step is past not_connected the button is live",
       'href="https://example.invalid/roofs"' in html_)
    ok("...and a new tab we did not write carries rel=noopener", "noopener" in html_)


def test_who_is_shown_so_he_can_see_WHICH_account_is_connected():
    _clear_seam()
    _register(state_fn=lambda: {"status": "connected", "who": "north tower", "detail": ""})
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    ok("connected names the account", "Connected" in words and "north tower" in words, words[:200])


def test_with_an_EMPTY_seam_the_old_contract_still_renders_in_full():
    """THE SEQUENCING TRAP, as a test. #1273 registers nothing and OSDev4 moves the inbox onto the
    seam next; between those two landings this is the live path, and a blank set-up page is a
    customer who cannot connect anything. Delete this test with the fallback it guards."""
    _clear_seam()
    ok("the seam really is empty", onboarding.steps() == [], str(onboarding.steps()))
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    titles = [s["title"] for s in bs.SETUP_STEPS]
    ok("every step of the old contract is still on the screen",
       all(t in words for t in titles), str(titles))
    seen = [words.find(t) for t in titles]
    ok("...in its order", all(i >= 0 for i in seen) and seen == sorted(seen), str(seen))


def test_a_seam_step_never_renders_twice():
    """NOBODY SEES A STEP TWICE — which is what this test was always for, asserted directly.

    It read: "Otherwise the day OSDev4 registers the inbox's steps, every buyer sees each of them
    twice." That is the intent, and it is right. The assertion underneath it was a PROXY — no
    `box_secrets` title anywhere on the page — which held only because `_setup_source` PREFERRED
    the seam and so rendered one source or the other.

    Preferring turned out to be a landmine (OSDev4, 2026-09-17, scope #1307): the first step
    registered through the seam, about anything at all, deleted every step still living in
    `box_secrets` from the buyer's screen. Measured — three steps became one. `_setup_source`
    merges and dedupes by key now, so an unmigrated step legitimately still renders, ONCE, and a
    migrated one replaces its predecessor in place.

    So the proxy is replaced by the thing it stood for, and the new assertion is STRICTER than the
    old one: it counts every title on the page rather than checking a set is absent, which would
    also have caught a duplicate the old form could not see.
    """
    _clear_seam()
    _register(state_fn=lambda: {"status": "not_connected", "detail": ""})
    app, c = _c()
    words = _text(c.get("/inbox/setup").get_data(as_text=True))
    ok("the registered step renders", words.count("Connect your gutter pigeons") == 1,
       str(words.count("Connect your gutter pigeons")))
    dupes = [t for t in [s["title"] for s in bs.SETUP_STEPS] if words.count(t) > 1]
    ok("...and NOTHING on the page is rendered twice", not dupes, str(dupes))
    ok("...and the box's own credentials are still offered, which is the bug this replaced",
       all(s["title"] in words for s in bs.SETUP_STEPS),
       str([s["title"] for s in bs.SETUP_STEPS if s["title"] not in words]))


def test_ci_actually_runs_this_file():
    """A GUARD MUST KNOW WHERE IT IS STANDING, and this one did not — twice in one file now.

    `.github/` is ours and never ships, but this suite DOES ship to a Customer Voice box, which
    carries the screen it tests. There the bare relative read raised FileNotFoundError and took
    the run down in front of a paying customer. CI cannot see it: `test_recipe_ships` proves the
    shipped suites inside a fresh LEAD box, and this suite is correctly dropped from a Lead box —
    so the only box type that carries it is the one nothing exercises. Found by exporting a
    customer_voice box and running all 71 of its suites by hand.

    Narrow on purpose: no `.github` at all means this is not the repo and there is no CI manifest
    to be named in; a `.github` that exists with the workflow gone is still a real failure.
    """
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        print("  --   not the repo — a buyer's box has no CI manifest to be named in")
        return
    wf = (here / ".github/workflows/tests.yml").read_text()
    ok("test_setup_screen_on_the_seam is in the workflow's suite list",
       "test_setup_screen_on_the_seam" in wf)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    _clear_seam()
    print("all ok" if not _failed else f"{_failed} FAILED")
    sys.exit(1 if _failed else 0)
