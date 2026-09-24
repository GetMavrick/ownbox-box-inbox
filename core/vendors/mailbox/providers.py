"""Which mail server a buyer's mailbox lives on — the presets, and the one provider that cannot work.

WHY THIS EXISTS (#1483, finding 1, OSDev1 made it the top backend item on 2026-09-23): the inbox only
knew Gmail. Every step was Google's and the form had no server field, so a plumber whose mail lives
at Yahoo, iCloud, their web host or GoDaddy had no way in at all — the most likely place in the
whole journey for a real buyer to stop.

EVERY PRESET HERE IS PLAIN IMAP WITH A PASSWORD, and that is a real restriction, not a shortcut. The
reader (`email_channel`) and the verifier speak IMAP over TLS on 993 with a login; each provider
below still accepts that, with an app password where it has them. The submission host follows the
`imap.` -> `smtp.` convention in `verify.smtp_host_for`, which holds for every preset listed.

MICROSOFT IS THE EXCEPTION, AND IT IS SAID UP FRONT RATHER THAN DISCOVERED AFTER A TIMEOUT.
Microsoft turned off password sign-in for IMAP in Microsoft 365 (all tenants, 2022–23 — "Basic
authentication is now disabled in all tenants", learn.microsoft.com, *Deprecation of Basic
authentication in Exchange Online*, page dated 2026-07-10) and for Outlook.com and Hotmail on
2024-09-16; app passwords are blocked with it. Reading those mailboxes needs Microsoft's own
sign-in (OAuth), which this box does not have yet. So a Microsoft server or address is refused with
that sentence before a password is ever sent — a buyer told "wrong password" would make a new one,
and it would fail the same way.
"""
from __future__ import annotations

import re

# key -> (what the buyer sees, the IMAP host, one line on where their password comes from)
PRESETS: dict[str, tuple[str, str, str]] = {
    "gmail": ("Gmail or Google Workspace", "imap.gmail.com",
              "Google needs an app password: 16 letters you make in your Google Account."),
    "yahoo": ("Yahoo Mail", "imap.mail.yahoo.com",
              "In your Yahoo account's security settings, generate an app password and paste it."),
    "icloud": ("iCloud Mail", "imap.mail.me.com",
               "Make an app-specific password in your Apple Account under Sign-In and Security."),
    "aol": ("AOL Mail", "imap.aol.com",
            "In your AOL account's security settings, generate an app password and paste it."),
    "zoho": ("Zoho Mail", "imap.zoho.com",
             "Use your Zoho password, or an app-specific password if you have two-factor sign-in on."),
    "fastmail": ("Fastmail", "imap.fastmail.com",
                 "Fastmail needs an app password: Settings, then Privacy & Security."),
    "godaddy": ("GoDaddy email (not Microsoft 365)", "imap.secureserver.net",
                "Use the password for this email address."),
    "other": ("Another provider", "",
              "Your provider's help pages list an IMAP server — put that in, with your password."),
}
DEFAULT = "gmail"

# Microsoft's IMAP hosts, and the address domains that are always Microsoft's. A business address
# on its own domain can live at Microsoft too; that is caught by the host, since the buyer picks it.
_MICROSOFT_HOSTS = ("outlook.office365.com", "outlook.office.com", "imap-mail.outlook.com",
                    "imap.outlook.com", "outlook.live.com", "imap.office365.com")
_MICROSOFT_DOMAINS = ("outlook.com", "hotmail.com", "live.com", "msn.com", "hotmail.co.uk",
                      "outlook.co.uk", "live.co.uk")

MICROSOFT_SAID = ("Outlook.com, Hotmail and Microsoft 365 mailboxes can't be connected yet. Microsoft "
                  "no longer lets any app read mail with a password — not even an app password — and "
                  "the Microsoft sign-in this box would need instead is not built yet. Nothing was "
                  "stored.")

_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def is_google(host: str) -> bool:
    return str(host or "").strip().lower() in ("imap.gmail.com", "imap.googlemail.com", "")


def is_microsoft(host: str = "", address: str = "") -> bool:
    h = str(host or "").strip().lower()
    domain = str(address or "").strip().lower().rsplit("@", 1)[-1] if "@" in str(address or "") else ""
    return (h in _MICROSOFT_HOSTS or h.endswith(".office365.com") or h.endswith(".outlook.com")
            or domain in _MICROSOFT_DOMAINS)


def host_for(provider: str, typed_host: str = "") -> str:
    """The IMAP host for a preset, or the host the buyer typed for "other". Raises ValueError with a
    sentence for the buyer. Never a URL, never a port: a host name, lower-case."""
    key = str(provider or DEFAULT).strip().lower()
    if key not in PRESETS:
        raise ValueError("Choose where your email lives from the list.")
    preset = PRESETS[key][1]
    if preset:
        return preset
    host = str(typed_host or "").strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/", 1)[0].split(":", 1)[0]
    if not _HOST.match(host):
        raise ValueError("Put in your provider's IMAP server — it looks like imap.example.com. "
                         "Their help pages list it under IMAP or email-app settings.")
    return host


def provider_of(host: str) -> str:
    """The preset a stored host belongs to, or "other"."""
    h = str(host or "").strip().lower() or PRESETS[DEFAULT][1]
    for key, (_label, preset, _how) in PRESETS.items():
        if preset and preset == h:
            return key
    return "other"
