# DEV STATE — the shared coordination channel

<!-- POST:  bash .claude/scripts/devstate-post.sh "$ROLE" "[FROM→TO] TYPE: tl;dr"
     TYPE ∈ ASSIGNED BLOCKED SHIPPED FACT GOTCHA OWNER FYI  ·  routing arrow required.
     HARD CAP 700 chars — the script rejects longer. Detail goes in a doc or the PR body.
     AUTO-PRUNED to the newest 12 posts on every write; git history holds the rest.
     NEVER edit this file inside a code PR — it conflicts every time. Post separately. -->

## What this file is

The one place your agents tell each other things. Not a log, not a chat — a **wall**, newest
first, with a hard character cap so a post has to be worth reading.

It exists because context does not survive. A session ends and everything it learned goes
with it: the dead end someone already tried, the API that answers 400 when you send the
obvious parameter, the decision you made on Tuesday and cannot reconstruct on Friday. Files
survive. Conversations do not. The wall is where a fact earns its way into the former.

**The grammar is the point.** `[FROM→TO] TYPE: message`. The arrow forces you to name who
needs this, which is how you find out whether anyone did. The TYPE tells a reader in one
glance whether it is a fact they can rely on, a landmine to avoid, or work assigned to them.

- `GOTCHA` is the highest-value post here. Something that silently did the wrong thing.
- `BLOCKED` names what you cannot proceed without, and who owns it.
- `SHIPPED` says what changed and where it is running now — never "done".
- `OWNER` is for a decision only a human can make. It should carry the options.

## Rules worth keeping

1. **One post, one idea.** The cap is not a nuisance; it is what makes the wall scannable.
2. **Post the correction, not just the fix.** "I was wrong about X" saves the next reader.
3. **Never relay through a human.** If a post is for another agent, address it to them.
4. **Claim work before you start it**, so two agents do not build the same thing twice.

## Messages

<!-- Your first post goes here. Nothing below this line ships from us — the wall starts empty
     on purpose, because the only posts worth having are the ones your own system earned. -->
