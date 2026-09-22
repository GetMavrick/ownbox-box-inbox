"""A mailer's plain-text fallback, made readable — and no sender markup, ever.

WHAT A BUYER WAS SEEING (OSDev5, 2026-09-22): `email_channel._body_text()` keeps text/plain and
discards the text/html part at ingest, so the thread renders the FALLBACK part — the one nobody
at the sending company proofreads. `<https://…>` link syntax, tables exploded one value per line,
stacks of blank lines. `.msg .b` is `white-space:pre-wrap`, so every one of those blank lines was
dead space on the screen. Gmail shows the HTML. We never kept it.

THIS SUITE DEFENDS THE FIX AND THE REFUSAL. The fix: collapse the gaps, make the links real. The
refusal: no sender HTML is rendered, and the escaping happens BEFORE anything assembles markup,
so there is no ordering in which a sender's angle bracket becomes a tag.

Run: python tests/test_a_message_reads_like_a_person_wrote_it.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"

try:
    from marketing.customer_voice.inbox.render import collapse_blank_lines, readable
except ImportError:
    print("  --   no customer_voice on this box — nothing to render")
    sys.exit(0)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


_TAG = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")
_ALLOWED_TAGS = {"a", "br"}


def live_tags(html_out: str) -> set:
    """Every tag that would actually PARSE, which is the only question that matters.

    NOT "is the word 'onerror' in the output". An escaped `&lt;img src=x onerror=…&gt;` contains
    that word and is completely inert — it renders as text a buyer can read, which is exactly
    what should happen to markup somebody emailed them. My first version of this test asserted
    on the substring and failed on correct output, which is a test that punishes the right
    behaviour. What matters is whether a BROWSER would build an element.
    """
    return {t.lower() for t in _TAG.findall(html_out)}


def test_nothing_a_sender_writes_becomes_markup():
    print("test_nothing_a_sender_writes_becomes_markup")
    # THE WHOLE REASON THERE IS NO SANITISER HERE. If any of these parse, the buyer's inbox is an
    # XSS target that ships on every box we sell.
    for payload in ('<script>alert(1)</script>',
                    '<img src=x onerror=alert(1)>',
                    '<iframe src="https://evil.test"></iframe>',
                    '<a href="javascript:alert(1)">click</a>',
                    '<style>body{display:none}</style>',
                    "<svg/onload=alert(1)>",
                    '<div onclick="steal()">x</div>'):
        tags = live_tags(readable(payload)) - _ALLOWED_TAGS
        ok(f"inert: {payload[:34]}", not tags, f"live tags: {sorted(tags)}")

    # AND THE ONLY TAGS IT EVER EMITS ARE ITS OWN, even on input engineered to look like markup.
    mixed = readable("hi <b>bold</b> and https://example.com/x and <script>y</script>")
    ok("only <a> and <br> are ever produced",
       live_tags(mixed) <= _ALLOWED_TAGS, f"live tags: {sorted(live_tags(mixed))}")

    # A javascript: or data: URL must never become an href. The pattern only matches http(s).
    ok("a javascript: URL is not linkified",
       'href="javascript:' not in readable("javascript:alert(1)"), readable("javascript:alert(1)"))
    ok("...nor a data: URL",
       'href="data:' not in readable("data:text/html,<script>x</script>"))
    # AND A QUOTE CANNOT BREAK OUT OF THE HREF IT LANDS IN.
    broke = readable('https://example.com/a"onmouseover="alert(1)')
    ok("a quote in a URL cannot escape the attribute", 'onmouseover="alert' not in broke, broke[:120])


def test_the_gaps_go_and_the_shape_stays():
    print("test_the_gaps_go_and_the_shape_stays")
    body = "Hi,\n\n\n\n\nYour domain is ready.\n\n\nLine one\nLine two\n\n\n"
    got = collapse_blank_lines(body)
    ok("a run of blank lines becomes one", "\n\n\n" not in got, repr(got))
    # SINGLE NEWLINES ARE THE ONLY STRUCTURE A FALLBACK HAS — an address per line, a cell per
    # line. Reflowing them would destroy the message to tidy it.
    ok("...but single newlines survive", "Line one\nLine two" in got, repr(got))
    ok("it does not open with blank lines", not got.startswith("\n"), repr(got[:12]))
    ok("...nor end with them", not got.endswith("\n"), repr(got[-12:]))
    ok("trailing spaces on a line go", "  \n" not in got and not got.endswith(" "), repr(got))


def test_links_become_links():
    print("test_links_become_links")
    # THE FORM MAILERS ACTUALLY EMIT. RFC 3986 angle-bracket delimiting, which after escaping
    # reads &lt;https://…&gt; and showed the buyer literal brackets.
    out = readable("Open it here: <https://example.com/a/b?x=1&y=2>")
    ok("an angle-bracketed URL becomes an anchor", '<a href="https://example.com/a/b?x=1&amp;y=2"' in out, out)
    ok("...and the literal brackets are gone", "&lt;http" not in out, out)
    ok("...opening safely", 'rel="noopener noreferrer nofollow"' in out and 'target="_blank"' in out, out)
    bare = readable("see https://example.com/thing for more")
    ok("a bare URL becomes an anchor", '<a href="https://example.com/thing"' in bare, bare)
    # A SENTENCE'S FULL STOP IS NOT PART OF THE LINK.
    dotted = readable("go to https://example.com/x.")
    ok("trailing punctuation stays outside the link", 'href="https://example.com/x"' in dotted, dotted)
    ok("...and is still shown", dotted.rstrip().endswith("."), dotted)
    # THE HREF KEEPS EVERY CHARACTER even when the visible text is elided.
    long_url = "https://example.com/" + "a" * 90
    shown = readable(long_url)
    ok("a long URL's href is complete", f'href="{long_url}"' in shown, shown[:100])
    ok("...while its visible text is shortened", "…" in shown, shown[:140])


def test_a_body_with_nothing_in_it_is_not_a_crash():
    print("test_a_body_with_nothing_in_it_is_not_a_crash")
    for empty in ("", "   ", "\n\n\n", None):
        try:
            out = readable(empty)
            ok(f"{empty!r} renders as nothing, quietly", out.strip() == "", repr(out))
        except Exception as e:                           # noqa: BLE001
            ok(f"{empty!r} renders", False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    test_nothing_a_sender_writes_becomes_markup()
    test_the_gaps_go_and_the_shape_stays()
    test_links_become_links()
    test_a_body_with_nothing_in_it_is_not_a_crash()

    print("\n— and this file cannot silently fall out of CI —")
    import pathlib
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("test_a_message_reads_like_a_person_wrote_it is in the workflow's suite list",
           "test_a_message_reads_like_a_person_wrote_it" in _wf.read_text())
    else:
        print("  --   no workflow file here (a box, not the repo) — nothing to check")

    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
