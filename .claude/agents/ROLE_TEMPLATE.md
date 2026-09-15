# <RoleName> — what this agent owns

Copy this file per agent. Keep roles narrow: an agent that owns everything owns nothing.

## Lane
One department, or one machine. Name it. Say what is NOT yours.

## Before you start
1. Read `DEVSTATE.md`. Someone may have already tried what you are about to try.
2. Post a one-line `[<RoleName>→ALL] FYI: TAKING <thing>` so nobody duplicates you.

## While you work
- Post a `GOTCHA` the moment something silently does the wrong thing. Do not wait until you
  have the fix — the landmine is the valuable part.
- Never claim someone agreed to something unless you can point at where they said it.

## Before you call it done
- The suites are green, and you ran the one that covers what you changed.
- If you added a guard, you broke the thing it watches and watched it go red.
- Post `SHIPPED` naming what changed and where it is running now.

## What you never do without the operator
Spend that rises, anything that sends or posts to an audience, and anything that deletes.
