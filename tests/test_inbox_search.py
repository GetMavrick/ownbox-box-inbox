"""The inbox can be searched — the $499 card's bullet 6, which the box did not have.

WHAT WAS TRUE BEFORE THIS. `docs/AUDIT_499_CARD.md` §6: the card says "Search everything" and the
only retrieval on the screen was `list_conversations` — fifty most-recent rows, filterable by
channel. No text predicate existed anywhere in the store; the word did not appear in the file. A
person looking for what a customer said last month had no way to ask.

THIS SUITE COVERS THE STORE HALF ONLY. The query box and the next-page control belong to the app,
which is OSDev5's; `search_conversations` is the reader they will call. What is asserted here is
the part that must be right before any screen touches it: that it finds by MESSAGE as well as by
name, that it returns conversations rather than hits, that a person's own punctuation cannot turn
into a wildcard, and that it can never cross a Space.

Run: python tests/test_inbox_search.py
"""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "search.db")

from core import state                                                # noqa: E402

state.init_db()

try:
    from marketing.customer_voice.schema import DDL
    from marketing.customer_voice.inbox import store
except ImportError:
    # A BOX WITHOUT THIS LANE. The suite ships wherever the exporter sends it; a Lead box has no
    # customer_voice package and nothing here applies to it. Saying so beats dying on the import
    # and taking that box's whole run down — the failure test_box_shaped suites were written for.
    print("  --   no customer_voice on this box — there is no inbox to search")
    sys.exit(0)

with state.connect() as c:
    c.executescript(DDL)

SPACE, OTHER = "acme", "notacme"
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def msg(space, zcid, body):
    with state.connect() as c:
        c.execute("INSERT INTO inbox_messages (id, space, zernio_conversation_id, direction, body,"
                  " created_at) VALUES (?,?,?,?,?,?)",
                  (str(uuid.uuid4()), space, zcid, "in", body, "2026-09-01T00:00:00Z"))


def names(rows):
    return sorted(r["participant"] for r in rows)


def seed():
    store.upsert_conversation(space=SPACE, zcid="c1", participant="Dana Roofing",
                              last_inbound_at="2026-09-01T00:00:00Z")
    store.upsert_conversation(space=SPACE, zcid="c2", participant="Ed Plumbing",
                              last_inbound_at="2026-09-02T00:00:00Z")
    store.upsert_conversation(space=SPACE, zcid="c3", participant="Fay Tiling",
                              last_inbound_at="2026-09-03T00:00:00Z")
    msg(SPACE, "c1", "there is a leak under the sink")
    msg(SPACE, "c1", "the leak is worse today")          # two hits, ONE conversation
    msg(SPACE, "c2", "can you quote for 100% coverage")
    msg(SPACE, "c3", "thanks!")
    # another tenant, same words — the boundary this file exists to hold
    store.upsert_conversation(space=OTHER, zcid="x1", participant="Someone Else",
                              last_inbound_at="2026-09-04T00:00:00Z")
    msg(OTHER, "x1", "there is a leak here too")


def test_it_finds_by_what_was_said_not_only_by_who_said_it():
    print("test_it_finds_by_what_was_said_not_only_by_who_said_it")
    ok("a word from a MESSAGE finds the conversation",
       names(store.search_conversations(SPACE, "leak")) == ["Dana Roofing"],
       str(names(store.search_conversations(SPACE, "leak"))))
    ok("...and the participant's name still works, for when that is what is remembered",
       names(store.search_conversations(SPACE, "Plumb")) == ["Ed Plumbing"])
    ok("...case does not matter, because nobody types a name the way it is stored",
       names(store.search_conversations(SPACE, "dana")) == ["Dana Roofing"])
    ok("a word nobody said finds nothing", store.search_conversations(SPACE, "helicopter") == [])


def test_a_thread_with_many_hits_is_still_one_row():
    print("test_a_thread_with_many_hits_is_still_one_row")
    # c1 says "leak" twice. A join without EXISTS would return it twice and a busy thread would
    # bury every other result under its own repetitions.
    rows = store.search_conversations(SPACE, "leak")
    ok("two matching messages in one thread return ONE conversation", len(rows) == 1, str(len(rows)))
    ok("...and it still carries its message count for the screen",
       rows and rows[0].get("message_count") == 2, str(rows[0].get("message_count") if rows else None))


def test_a_persons_own_punctuation_is_not_a_wildcard():
    print("test_a_persons_own_punctuation_is_not_a_wildcard")
    # THE BUG THIS PREVENTS IS A SEARCH THAT LOOKS BROKEN. Unescaped, `%` is LIKE's "anything", so
    # a customer searching their own quote for "100%" would get every conversation in the box and
    # conclude the feature does not work.
    ok("searching 100% finds the thread that SAYS 100%, not everything",
       names(store.search_conversations(SPACE, "100%")) == ["Ed Plumbing"],
       str(names(store.search_conversations(SPACE, "100%"))))
    ok("a bare % is a literal percent sign, not 'match all'",
       names(store.search_conversations(SPACE, "%")) == ["Ed Plumbing"],
       str(names(store.search_conversations(SPACE, "%"))))
    ok("a bare _ is a literal underscore, not 'any character'",
       store.search_conversations(SPACE, "_") == [],
       str(names(store.search_conversations(SPACE, "_"))))
    # A BACKSLASH IS THE ESCAPE CHARACTER ITSELF, so it has to be escaped first or the escaping
    # this function just did becomes the thing being escaped.
    msg(SPACE, "c3", r"path is C:\temp\bill")
    ok("a backslash a person typed is a literal backslash",
       names(store.search_conversations(SPACE, r"C:\temp")) == ["Fay Tiling"],
       str(names(store.search_conversations(SPACE, r"C:\temp"))))


def test_an_empty_query_returns_nothing_not_everything():
    print("test_an_empty_query_returns_nothing_not_everything")
    # A blank box is a person who has not asked yet. Answering it with the whole inbox makes the
    # screen flicker between two meanings of empty and hides the difference from the reader.
    for blank in ("", "   ", None):
        ok(f"a blank query ({blank!r}) returns nothing", store.search_conversations(SPACE, blank) == [])


def test_it_can_never_cross_a_space():
    print("test_it_can_never_cross_a_space")
    # Rubric #1. The other tenant has a conversation whose message says "leak" too.
    ok("another Space's matching conversation is not returned",
       names(store.search_conversations(SPACE, "leak")) == ["Dana Roofing"])
    ok("...and searching THAT Space returns only its own",
       names(store.search_conversations(OTHER, "leak")) == ["Someone Else"])
    ok("a Space with nothing in it finds nothing", store.search_conversations("empty", "leak") == [])


def test_it_pages_and_filters_like_the_list_it_sits_beside():
    print("test_it_pages_and_filters_like_the_list_it_sits_beside")
    # The screen already knows how to page `list_conversations`; search answering a different
    # shape would make the caller special-case it, which is how two readers drift apart.
    msg(SPACE, "c2", "leak in the roof as well")
    msg(SPACE, "c3", "a leak, apparently")
    every = store.search_conversations(SPACE, "leak")
    ok("all three match now", len(every) == 3, str(len(every)))
    first = store.search_conversations(SPACE, "leak", limit=1)
    second = store.search_conversations(SPACE, "leak", limit=1, offset=1)
    ok("limit returns one", len(first) == 1)
    ok("...and offset returns the NEXT one, not the same one",
       first[0]["id"] != second[0]["id"], f'{first[0]["id"]} vs {second[0]["id"]}')
    ok("newest inbound first, exactly as the list orders",
       [r["participant"] for r in every] == ["Fay Tiling", "Ed Plumbing", "Dana Roofing"],
       str([r["participant"] for r in every]))
    only = store.search_conversations(SPACE, "leak", platform="instagram")
    ok("a channel filter narrows it, and an unmatched channel returns nothing", only == [],
       str(names(only)))


if __name__ == "__main__":
    seed()
    test_it_finds_by_what_was_said_not_only_by_who_said_it()
    test_a_thread_with_many_hits_is_still_one_row()
    test_a_persons_own_punctuation_is_not_a_wildcard()
    test_an_empty_query_returns_nothing_not_everything()
    test_it_can_never_cross_a_space()
    test_it_pages_and_filters_like_the_list_it_sits_beside()
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
