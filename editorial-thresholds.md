# Editorial thresholds

**Change a number here and the next sweep uses it. No PR, no deploy.**

This file is the only place these numbers are written down. The gate that refuses a piece
(`scripts/preflight_written.py`) and the prompt that tells the writer what to aim for
(`marketing/content_machine/written/daily_seed.py`) both read it. Neither restates a value.

That is the whole point. Every silent failure this week was one rule stated twice and drifting
apart — a 280-character beat cap the prompt still ordered after the gate stopped enforcing it, a
column the sync wrote after the board renamed it. Editorial numbers are the same shape of
problem, and they are the ones you were paying for in arguments.

**When you disagree with the gate, edit this file. Do not argue with the gate.**

Anything not `key: value` is ignored, so write whatever you like around the numbers.

---

## Length

The tier menu is the one that actually steers the writing. A model reading a list whose first
entry says "say it and stop" anchors there and writes short — that is why a piece came back at
800 words when nothing anywhere rejected it at 800. Change the tiers, not just the bounds.

body_min_words: 500
body_max_words: 2000
body_tiers: 500-800 one sharp claim | 900-1400 an argument with structure | 1500-2000 a guide

## Search

`meta_min_chars`/`meta_max_chars` are a narrow band on purpose: Google truncates a description
around 160 and pads a short one with scraped text, so both ends cost a click.

title_max_chars: 60
meta_min_chars: 150
meta_max_chars: 160
slug_min_words: 3
slug_max_words: 6

## Shape

h2_min: 3
h2_max: 8
headline_max_chars: 70

## Reel script

These govern the SPOKEN script only — the `Reel Script` column — and nothing else this machine
writes. The craft rules live where they always have,
`marketing/content_machine/reel/clients/default/script-guide.md`; only the things a gate can
mechanically refuse live here, because a gate that refuses work has to be arguable with by
editing a file rather than by opening a PR.

`script_min_words`/`script_max_words` bound ONE VIDEO SEED — one audience, two hooks, an angle,
an anchor and a source. The machine stopped writing scripts on 2026-08-12; it writes raw material
and the owner writes the script.

RE-MEASURE THE BOARD BEFORE YOU TOUCH THESE. DO NOT REASON FROM THE SPEC. This bound has now been
wrong three times, every time because it was fitted to an artifact the board had already moved
past: 100-160 refused 18 of 20 seeds, 40-120 refused 22 of 24, 90-220 would refuse every
single-audience one. Each was defensible on the day it was written and wrong within hours.

MEASURED, 2026-08-12, from the 24 hand-written single-audience sections on the board: 62 to 109
words, median 73. 45 sits below the shortest real one and still catches a stub; 140 sits above the
longest and still catches a runaway.

script_min_words: 45
script_max_words: 140

`script_banned_cta` is why the gate exists (owner, 2026-08-12: *"I'm not trying to write ads
with this. Only value driven reel scripts."*). Matched case-insensitively anywhere in the
script. A URL is refused by pattern rather than by this list, so it needs no entry.

A PRICE IS NOT REFUSED, and that is deliberate (owner, 2026-08-13: *"I never said that at all.
That was a different conversation, not related to this."*). An earlier revision refused every
dollar amount. His own pitch is built on one — `$99 a month` against the `$3,900` a human
receptionist costs — and `hook-frameworks.md` offers *"$40k a month vs $400k a month. Same
team."* as a model hook, so the gate was refusing the strongest thing the writer has. Name a
number whenever the piece does.

DELIBERATELY ABSENT: `comment`. The lead-magnet funnel depends on saying a keyword out loud in
the reel, so banning it here would gate the one CTA that is supposed to be there. Add `comment`
to this line if you decide the keyword belongs only in the caption.

script_banned_cta: book a call, dm me, link in bio, follow for more, sign up, swipe up, click the link, try me, buy now, get started today, book a demo, schedule a call

`script_trade_nouns` are the NARROW ones, banned in BOTH sections. Generalized industries are
welcome — home services, local businesses, agencies — because the category keeps everyone in the
room while the trade noun shrinks the audience to the one person in it.

script_trade_nouns: plumber, plumbers, roofer, roofers, hvac, contractor, contractors, technician, technicians, homeowner, homeowners, dispatch, water heater, crawl space, electrician, electricians, landscaper, landscapers

`script_hook_assumptions` are phrases that assume something about the viewer — that they have
clients, a team, revenue. Owner, on "your client can't find techs": "Who am I to assume people
have clients or even give a shit about that." Checked on HOOK lines only.

script_hook_assumptions: your client, your clients, your team, your techs, your employees, your crew, your staff, your revenue

`script_spoken_words` is the simple-words rule, absolute for spoken work. The bar: if one word
makes one viewer feel dumb, they scroll. It grows by editing this line.

script_spoken_words: injected, diff, interface, adoption, allocation, non-billable, median, qualify, open-weights, deploy, enforce, pipeline, constraint, utilization, iterate, leverage, optimize, dependency, capacity

`script_burned_hooks` is the script guide's own hook-aging list, which was written down and
never enforced. A script opening on one of these is refused and rewritten once.

script_burned_hooks: you know that feeling when, pov:, here's a story about, three things nobody tells you, the #1 reason why, this one thing changed everything, let me tell you about, trust me on this, hot take:

## X Article caption

Publishing an X Article has a step before the Publish button: a caption box, hard-capped by X at
256 characters, and THAT is what shows in the feed — the article card sits underneath it. Nothing
generated it before 2026-08-12, so it was written by hand at the moment of publishing.

It is NOT the first beat of the `X / Twitter` thread. Measured on the six rows that have both:
those beats run 611 to 781 characters, two to three times over. Different artifact, different
bound.

`x_caption_max_chars` is X's own limit and lowering it is the only sane edit — raising it past 256
produces something that cannot be pasted.

x_caption_max_chars: 256

## Never say

`banned_words` is matched on word boundaries, so "unlocked" trips "unlock" but "leverages" does
not trip "leverage" twice. `banned_openers` is matched against the start of the body only.

banned_words: leverage, unlock, revolutionize, game-changer, cutting-edge, synergy, deep dive, thought leadership, value proposition, optimize, streamline, robust, best-in-class, next-level, holistic, utilize, journey, transformation, transform, elevate, empower, authentic, lean into, intentional, aligned, abundance, showing up, really, very, truly, literally, basically, actually, amazing, incredible, mind-blowing, absolutely, totally
banned_openers: in today, many people, as a , let's talk about, today i want, i want to tell you about, have you ever wondered, did you know, listen up, here's the thing
