"""Does this mailbox credential actually open the mailbox? Asked before it is stored.

WHY THIS EXISTS (OSDev5, 2026-09-16, on the wall): `put_email` wrote `status = connected` after a
SHAPE check — sixteen alphanumeric characters — and never once signed in. So a buyer who pasted
last month's revoked app password saw "Connected", and found out hours later from an inbox that
stayed empty, if they noticed at all. That is the same failure `put_zernio` was written to avoid,
one credential over.

VERIFIED WITH THE VENDOR, WHILE THE PERSON IS STANDING THERE. A login costs one round trip at the
only moment somebody is present to fix the answer, and it catches the thing a shape check never
can: a perfectly well-formed password that has been revoked.

READ-ONLY AND IT TOUCHES NOTHING. It logs in, asks the server to SELECT the mailbox read-only so
that a credential which authenticates but cannot open INBOX still fails here, and logs out. No
message is fetched, and nothing is marked read — `email_channel` is careful about that with
BODY.PEEK and this must not be the thing that undoes it.
"""
import imaplib
import os

IMAP_HOST_DEFAULT = "imap.gmail.com"
# Long enough that a slow server is not called a bad password; short enough that a person is still
# looking at the screen when the answer comes back. The poller uses its own, longer timeout.
_TIMEOUT_S = 20


class MailboxAuthError(RuntimeError):
    """The server refused. Carries WHICH refusal, because the two need different instructions."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def classify_auth_failure(msg: str) -> MailboxAuthError:
    """Turn an opaque IMAP refusal into something a buyer can act on.

    Google returns the same shape whether the app password was revoked — which happens
    automatically whenever the account password changes — or a Workspace administrator switched
    app passwords off for the whole domain. A person told "authentication failed" learns nothing
    they can do about it, and the two answers have completely different next steps: make a new
    one, or go and ask somebody.
    """
    low = (msg or "").lower()
    if "disabled" in low or "not enabled" in low or "administrator" in low:
        return MailboxAuthError("admin_disabled",
                                "Your Google administrator has turned off app passwords for your "
                                "organisation. Ask them to allow them, then reconnect.")
    return MailboxAuthError("needs_reauth",
                            "Google refused the app password. This usually means the Google account "
                            "password changed, which revokes app passwords. Make a new one and "
                            "paste it in again.")


def verify_credential(host: str, user: str, password: str) -> tuple[bool, str, str]:
    """(ok, status, sentence). NEVER RAISES — a verifier that throws is one callers skip.

    `status` is one of the values `box_secrets.note_email_status` accepts, so the answer can be
    stored as-is rather than translated at each call site.

    A NETWORK PROBLEM IS NOT A BAD PASSWORD, and saying so matters: told "Google refused your app
    password" when the box simply could not reach Gmail, a person throws away a working credential
    and makes a new one. DNS, TLS and timeouts get their own sentence and `needs_reauth` is not
    written for them — the poller will try again on its own in under a minute.
    """
    host = str(host or "").strip() or IMAP_HOST_DEFAULT
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(host, timeout=_TIMEOUT_S)
        conn.login(user, password)
        # AUTHENTICATED IS NOT THE SAME AS USABLE. A credential can log in and still be unable to
        # open INBOX; the sweep would then fail every time on a mailbox the screen called
        # connected. `readonly=True` for the same reason the sweep uses it — verifying must never
        # be the thing that marks somebody's mail as read.
        conn.select("INBOX", readonly=True)
    except imaplib.IMAP4.error as e:
        err = classify_auth_failure(str(e))
        return False, err.status, err.detail
    except (OSError, imaplib.IMAP4.abort) as e:      # DNS, TLS, refused, timed out
        return False, "unreachable", (
            "The box could not reach your mail server just now. Nothing is wrong with what you "
            f"typed — try again in a minute. ({str(e)[:80]})")
    except Exception as e:                           # noqa: BLE001 — a verifier never throws
        return False, "unreachable", (
            f"Something went wrong checking that mailbox. Try again in a minute. ({str(e)[:80]})")
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:                        # noqa: BLE001 — a failed logout is not a
                pass                                 # failed verification
    return True, "connected", ""


# ── CAN THIS CREDENTIAL ALSO *SEND*? ────────────────────────────────────────────────────────────
#
# READING AND SENDING ARE TWO DIFFERENT PERMISSIONS ON ONE PASSWORD. A Google Workspace
# administrator can leave IMAP on and turn SMTP off, so a credential that opens the mailbox
# perfectly may be unable to send a single message. Without this check a buyer finds that out at
# the worst possible moment: a customer is waiting, they press send, and it fails.
#
# ASKED AT SET-UP, WHERE THE VERIFY ABOVE ALREADY IS, for the reason that one exists — the person
# is standing there and can act on the answer. It is one more round trip on a screen that already
# makes one.
#
# IT AUTHENTICATES AND STOPS. `login()` completes the SMTP AUTH exchange; no `sendmail`, no
# recipient, no message. Nothing leaves the box, so this cannot be the thing that mails somebody.

SMTP_HOST_DEFAULT = "smtp.gmail.com"
SMTP_PORT = 587                  # submission + STARTTLS (RFC 6409). 465 is implicit TLS, not this.
# SHORTER THAN THE READ CHECK, ON PURPOSE. The read check's answer DECIDES whether a credential is
# stored, so it is worth waiting on. This one decides nothing — an unreachable server is recorded
# as `unknown`, which is where the box already starts. Waiting twenty seconds to learn nothing, on
# a screen with somebody sitting in front of it, is the worse trade.
_SEND_TIMEOUT_S = 6


def smtp_host_for(imap_host: str) -> str:
    """The submission host that goes with a mailbox host.

    A CONVENTION, AND NAMED AS ONE. `imap.gmail.com` -> `smtp.gmail.com` holds for Gmail and for
    every provider that follows the same naming, which is most of them. It is a guess for the rest,
    and a wrong guess here costs a failed check and a sentence saying sending is unavailable —
    never a stored credential and never a lost message, because nothing downstream trusts it
    beyond that. A box that needs a different host will carry one explicitly; today none does, and
    inventing the setting before there is a buyer to use it is how a set-up screen grows a field
    nobody can answer.
    """
    host = str(imap_host or "").strip().lower() or IMAP_HOST_DEFAULT
    if host.startswith("imap."):
        return "smtp." + host[len("imap."):]
    return SMTP_HOST_DEFAULT if host == IMAP_HOST_DEFAULT else host


def _may_ask(env=None) -> bool:
    """May this box open a socket to a submission server right now?

    THE SHAPE `core.slack.py:178` ALREADY USES, opt-in first, and here for the reason
    `find_linkedin.py` records the expensive way: a check nobody thought about reaches a real
    vendor from a test runner.

    WHY THIS HALF NEEDS A GUARD AND THE READ CHECK ABOVE DOES NOT. `verify_credential`'s answer
    DECIDES whether a credential is stored, so a suite that forgets to fake IMAP fails loudly, at
    once, on its own first assertion — it cannot be overlooked. This one decides nothing: unfaked,
    it waits out the timeout and returns `unknown`, which is where the box already starts. So it
    degrades SILENTLY, and every suite that ever calls `put_email` pays for it without being told
    why. Measured before it shipped: tests/test_mailbox_screen.py went from 1.7s to 68s.

    `AIOS_ALLOW_SMTP_CHECK=1` COMES FIRST, exactly as `AIOS_OPERATOR_ALERTS` does in core.slack,
    so the suite that actually drives this function turns it back on and tests the real path.
    """
    env = os.environ if env is None else env
    if env.get("AIOS_ALLOW_SMTP_CHECK") == "1":
        return True
    return not (env.get("AIOS_HERMETIC_TEST") or env.get("GITHUB_ACTIONS") or env.get("CI"))


def verify_send(host: str, user: str, password: str) -> tuple[bool, str, str]:
    """(ok, status, sentence). NEVER RAISES, and never sends anything.

    `status` is `can_send`, `refused` or `unknown`, and the difference between the last two is the
    whole reason this returns three values instead of a bool:

      refused   the server answered, and the answer was no. The buyer can act on this — it is
                almost always a Workspace policy, and the sentence says who to ask.
      unknown   we could not get an answer: DNS, TLS, a timeout, a host that is not theirs. This
                must NOT read as "you cannot send". The box simply does not know yet, and saying
                otherwise sends somebody to argue with an administrator about a setting that was
                never off.

    A `False` HERE NEVER BLOCKS THE CONNECTION. Reading their mailbox is most of what this box
    does today and all of what it did yesterday; refusing to store a working IMAP credential
    because a send check failed would break the feature that works to protect one that has not
    shipped. The answer is recorded, not enforced.
    """
    import smtplib

    if not _may_ask():
        return False, "unknown", ""
    host = smtp_host_for(host)
    conn = None
    try:
        conn = smtplib.SMTP(host, SMTP_PORT, timeout=_SEND_TIMEOUT_S)
        conn.ehlo()
        conn.starttls()
        # EHLO AGAIN AFTER STARTTLS, and it is not a formality: the server's advertised
        # capabilities — AUTH among them — are re-sent on the encrypted channel, and a client that
        # reuses the pre-TLS list is trusting something it read in the clear.
        conn.ehlo()
        conn.login(user, password)
    except smtplib.SMTPAuthenticationError as e:
        return False, "refused", _smtp_refusal(str(e))
    except smtplib.SMTPNotSupportedError as e:
        # The server will not do STARTTLS or will not do AUTH. Either way it is a server answer,
        # and an unencrypted fallback is not on the table for a password.
        return False, "refused", ("Your mail server will not accept an encrypted sign-in for "
                                  f"sending. Ask whoever runs it to allow SMTP. ({str(e)[:80]})")
    except (OSError, smtplib.SMTPException) as e:
        return False, "unknown", ("The box could not reach your mail server to check sending. "
                                  f"This is not a problem with your password. ({str(e)[:80]})")
    except Exception as e:                           # noqa: BLE001 — a verifier never throws
        return False, "unknown", ("Something went wrong checking whether this mailbox can send. "
                                  f"({str(e)[:80]})")
    finally:
        if conn is not None:
            try:
                conn.quit()
            except Exception:                        # noqa: BLE001 — a failed QUIT is not a
                pass                                 # failed verification
    return True, "can_send", ""


def _smtp_refusal(msg: str) -> str:
    """A refusal a person can act on, in the same spirit as `classify_auth_failure` above.

    THE COMMON CASE IS NOT A BAD PASSWORD. The same app password just opened the mailbox seconds
    earlier, so "wrong password" is the one explanation we can usually rule out — which makes the
    default sentence point at the setting that is actually off.
    """
    low = (msg or "").lower()
    if "disabled" in low or "not enabled" in low or "administrator" in low or "policy" in low:
        return ("Your Google administrator has turned off sending over SMTP for your "
                "organisation. Ask them to allow it, then reconnect.")
    return ("Your mail server accepted this password for reading but refused it for sending. "
            "On Workspace that is usually an administrator setting — ask them to allow SMTP.")
