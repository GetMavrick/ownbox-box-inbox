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
