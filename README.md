# The Unified Inbox Machine

A machine that watches what the outside world can see about your business — whether your
front door is open, how fast it answers, and how you compare to the competitors **you**
name — and writes it into one page you read in the morning. It runs on a small Linux
server you control, on your own API keys. We never see your keys or your numbers.

Start with `setup-instructions.md`. This file tells you what you have; that one tells you
what to type, in order.

## What it is not

A reputation service that posts on your behalf. Nothing in this box writes to a customer,
a review site, or a social account. It reads, it records, and it tells you. The ability to
reply on your behalf is not switched off — it is absent from the code that ships here.

It is also not a crawler. It watches the site you name and the competitors you list, and
it never goes looking for more on its own. That is what keeps a metered lookup a metered
lookup instead of a bill.

## What is in the box

| Directory | What it is |
|---|---|
| `core/` | The kernel: config, database, cost guard, worker, the one network door |
| `marketing/customer_voice/` | The machine |
| `marketing/customer_voice/seo/health.py` | The two rails that need no account: uptime and PageSpeed |
| `marketing/customer_voice/competitors/roster.py` | The competitors you name, read monthly through Google Places |
| `marketing/customer_voice/rails.py` | Which rails this business has, and what state each one is in |
| `marketing/customer_voice/report.py` | The Unified Inbox section of your morning page |
| `core/net.py` | Every fetch this machine makes goes through this one door |
| `config/aios.config.yaml` | Behaviour. Every knob is commented in place |
| `my/settings.yaml` | Your values, merged over the shipped config so updates stay clean |
| `scripts/` | Operator tools and the test suites |
| `BRAND_INVENTORY.txt` | Every file that still mentions us instead of you — your edit list |
| `LICENSE`, `licence.json.example` | Your licence; stamp it during setup |

## What it watches today

**Your front door.** Every fifteen minutes it asks your site for its front page and records
what came back: answered, answered slowly, or did not answer. Slow is its own state on
purpose — a site that takes four seconds is not down, but a visitor feels four seconds and
leaves, and calling that "down" would cry wolf until you stopped reading the page.

**How fast it is.** Once a day, Google's own PageSpeed score for your front page, with the
largest-paint time beside it. Google's bands, not ours: 90 and up is good, 50 to 89 needs
work, under 50 is poor.

**Who you are up against.** The competitors you name — and only those — read once a month
through Google Places, with your own listing beside theirs so the comparison means
something. A listing with too few reviews is shown but takes no position on the table,
because a 5.0 from two people is not ahead of a 4.9 from four hundred.

**One page each morning.** All of it lands as the Unified Inbox section of your daily
report, with a date picker back to your first day. Every day is kept.

## What is not here yet

This matters more than the list above, so it is on the same page rather than in a footnote.

- **Reviews** — Google Business Profile, Yelp and Trustpilot — and **comments** on Meta,
  Instagram and YouTube are not in this box. Both need a platform approval against *your*
  own accounts, with their own forms and review queues, and neither queue is ours to jump.
- **Search** — Search Console and Analytics — needs a Google sign-in flow that is not
  written yet.
- **Drafted replies** are not here. When they arrive they will wait for your approval, and
  that is the point of them, but today there is nothing to approve.

Everything in "What it watches today" runs the day you install it, with no account and no
approval. Everything in this section will arrive as a `git pull`.

## The four states, and why two of them are not failures

A rail you do not have renders **nothing at all** — no line, no zero, no nag. A rail you
have but have not connected says *connect your Google Business Profile*, which is a next
step and reads like one. A rail that is connected and quiet says so. Only a rail that is
connected and failing shows as a failure.

Getting that backwards is how a dashboard turns a setup step into a support ticket, and
how people learn to ignore a red light. The box asks you which rails this business has
rather than guessing, because nothing here can tell whether a B2B consultancy will ever
have a Yelp listing.

## Honest limits

- One server, one SQLite file. Right for one business; this is not a multi-client console.
- PageSpeed's free anonymous quota is shared by everyone who calls without a key, and it is
  usually empty. A free key of your own fixes it, and the box tells you so in plain words
  rather than showing a red light you cannot clear.
- Competitor standings cost one metered Google Places lookup per name per month. Six names
  is six lookups a month. That is the whole spend of this machine.
- Uptime is checked from your box. If your box and your site are behind the same outage,
  it cannot tell you.
- We do not see your box. The licence tells you how to reach us.
