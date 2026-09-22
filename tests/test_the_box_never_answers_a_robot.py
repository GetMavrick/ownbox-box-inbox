"""The box never drafts a reply to a machine.

THE NIGHT THIS COST. 2026-09-22: the owner opened his own Gmail and found FIFTY drafts the box
had written into it — replies to LinkedIn job alerts, Google security alerts, uspto.gov,
Spaceship, DigitalOcean's referral robot, and to the box's OWN Morning Review. Every one was a
model call billed to his own subscription. "None of these needed drafts. And it's just wasting
my tokens. Turn them off until you get this right."

EVERY CASE BELOW IS A REAL ADDRESS FROM HIS MAILBOX, not an invention. That is the point: the
rule was fitted to the board rather than to a guess about what robots look like, and these are
the senders it was fitted to.

THE TWO WAYS TO GET THIS WRONG, and only one of them is visible:

  · answer a robot     — he sees it, it wastes his money, and it made the product look like a toy
  · refuse a person    — he NEVER sees it. A customer's question sits there and the box that was
                         sold to answer it says nothing at all.

The second is worse and it is silent, so this suite spends more of its assertions there: every
role address a small business actually uses must still get a draft.

Run: python tests/test_the_box_never_answers_a_robot.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"

from marketing.customer_voice.drafter.who_wrote import is_a_person, why   # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


# ── 1. the robots that were actually in his inbox ───────────────────────────────────────────
print("test_the_robots_he_actually_received")
ROBOTS_BY_NAME = [
    "jobalerts-noreply@linkedin.com", "notifications-noreply@linkedin.com",
    "messaging-digest-noreply@linkedin.com", "jobs-noreply@linkedin.com",
    "invitations@linkedin.com", "no-reply@accounts.google.com", "gmail-noreply@google.com",
    "alert@spaceship.com", "no-reply@referrals.digitalocean.com", "noreply@skool.com",
    "noreply@notify.cloudflare.com", "no-reply@uspto.gov", "no-reply@mail.instagram.com",
    "noreply@x.ai", "verify@x.com", "notices@sdge.com", "noreply@mail.heymerit.ai",
    "no-reply-uvjwrbjh7kvj3igx1tf27g@mail.anthropic.com",
]
for a in ROBOTS_BY_NAME:
    ok(f"{a} is refused by its address", not is_a_person(a), why(a) or "DRAFTED")

print("test_a_newsletter_that_sets_the_standard_headers")
# These set no telling address — mercola, beehiiv, a coach's broadcast — and are caught only by
# the header. Measured: 55 of his newest 120 carry one.
for a, h in (("jm@mercola.com", {"List-Unsubscribe": "<https://mercola.com/u>"}),
             ("bigplayers@mail.beehiiv.com", {"List-Id": "<big.beehiiv.com>"}),
             ("tate@cobratate.com", {"Precedence": "bulk"}),
             ("dm@danmartell.com", {"list-unsubscribe": "<mailto:u@x>"}),
             ("robby@updates.voicedrop.ai", {"Auto-Submitted": "auto-generated"})):
    ok(f"{a} is refused by {list(h)[0]}", not is_a_person(a, h), why(a, h) or "DRAFTED")

print("test_the_box_never_answers_itself")
OURS = {"brian@nlvl.co", "brian@brian-macdonald.com"}
ok("its own Morning Review is refused", not is_a_person("brian@brian-macdonald.com", ours=OURS))
ok("...and its own mailbox address is refused", not is_a_person("brian@nlvl.co", ours=OURS))
ok("...and says which reason it was", why("brian@nlvl.co", ours=OURS) == "this box sent it")
# AND THE HEADER PATH, which is the one that works on mail the box did not know it sent. #1432
# stamps X-Ownbox on everything this box originates; his own Morning Review was the last sender
# leaking through the address rules and this is what stops it.
ok("anything carrying X-Ownbox is refused, whatever address it came from",
   not is_a_person("brian@brian-macdonald.com", {"X-Ownbox": "notice"}))
ok("...and the reason names the header",
   "x-ownbox" in why("anyone@anywhere.com", {"X-Ownbox": "notice"}))


# ── 2. THE EXPENSIVE MISTAKE: refusing a person ─────────────────────────────────────────────
print("test_a_person_always_gets_a_draft")
PEOPLE = [
    "ctervo@linkedin.com",            # a real person, at a domain full of robots
    "kamila@openbridgewell.pro", "y@heysmoothoctave.com", "bale@runbear.io",
    "jessica.t@fundaup.biz", "aditya@betwenepodcasting.com", "daniel.z@posthog.com",
    "hanoverlagunanigel@emailrelay.com",
]
for a in PEOPLE:
    ok(f"{a} still gets a draft", is_a_person(a), f"refused: {why(a)}")

print("test_a_small_business_IS_its_role_address")
# THE FALSE POSITIVE THAT WOULD BE INVISIBLE. Refusing these silences the exact customers this
# product is sold to answer, and nobody would ever see it happen.
for a in ("info@thefasciacompany.com", "hello@marketing.ottokit.com", "support@sav.com",
          "support@digitalocean.com", "hey@posthog.com", "contact@acme.co", "sales@acme.co",
          "enquiries@acme.co", "bookings@acme.co", "team@acme.co", "admin@acme.co",
          "office@acme.co", "accounts@acme.co", "help@acme.co", "orders@acme.co"):
    ok(f"{a} is a person until proven otherwise", is_a_person(a), f"refused: {why(a)}")

print("test_a_name_that_merely_contains_a_robot_word_is_not_a_robot")
for a in ("alerta.garcia@acme.co", "denotify@acme.co", "confirmed@acme.co",
          "systemsolutions@acme.co", "verifyme.co@acme.co", "notifywell@acme.co"):
    ok(f"{a} is not caught by a loose match", is_a_person(a), f"refused: {why(a)}")

print("test_the_false_positive_this_rule_accepts_on_purpose")
# NAMED RATHER THAN HIDDEN. A robot word as a whole COMPONENT is refused, so a business whose
# address begins "notify-" or "alerts-" — `notify-me-design@studio.com` — is refused with it.
# That is a real cost and it is accepted with eyes open: a customer emailing a small business
# from such an address is vanishingly rare, while `no-reply@`, `notify@` and `alerts@` are the
# commonest robots in the owner's own mailbox. If a buyer ever reports a silenced customer, THIS
# is the line to revisit — and an allow-list on the box beats loosening the rule for everyone.
ok("a component match refuses notify-me-design@studio.com, knowingly",
   not is_a_person("notify-me-design@studio.com"),
   "the rule changed — re-read the note above and decide deliberately")


# ── 3. the shape of the answer ──────────────────────────────────────────────────────────────
print("test_a_refusal_carries_a_reason_a_screen_can_show")
ok("header refusals name the header",
   "list-unsubscribe" in why("x@y.com", {"List-Unsubscribe": "<u>"}))
ok("address refusals say what is wrong",
   why("no-reply@y.com") == "an address that does not accept replies")
ok("a person's reason is empty, never a sentence", why("dana@acme.co") == "")
ok("an unknown sender is not refused on a guess", why("") == "")
ok("precedence is matched on its VALUE, not merely its presence",
   is_a_person("x@y.com", {"Precedence": "first-class"})
   and not is_a_person("x@y.com", {"Precedence": "bulk"}))

print("\nFAILED" if _failed else "\nALL PASS")
sys.exit(1 if _failed else 0)
