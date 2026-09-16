"""core.vendors.mailbox — the IMAP credential gateway.

ONE PLACE THAT KNOWS WHAT GOOGLE'S REFUSALS MEAN. The sentences a buyer reads when a mailbox will
not connect are written once, here, and used by both the screen that saves the credential and the
poller that later finds it dead. Two copies would drift, and the half that drifted would be the
one a person is reading while they try to fix something.

IN `core`, NOT IN THE INBOX MACHINE, because `core.box_secrets` has to verify a credential before
storing it and core imports no machine — a Lead box has no `marketing/customer_voice` at all.
"""
from .verify import (IMAP_HOST_DEFAULT, MailboxAuthError, classify_auth_failure,
                     verify_credential)

__all__ = ["verify_credential", "classify_auth_failure", "MailboxAuthError", "IMAP_HOST_DEFAULT"]
