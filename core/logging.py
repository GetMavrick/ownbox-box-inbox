"""Tiny dependency-free structured logger: get_logger(__name__).info("event", key=val)."""
import json
import logging
import re
import sys

# Query-string / header secrets that must never reach a log line, exception message, or a
# persisted error column. Vendor clients SHOULD keep keys out of URLs, but if one leaks (e.g.
# Hunter's `?api_key=` query param), this is the defense-in-depth backstop before durable sinks.
_KEYISH = r"api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|signature|password|passwd|pwd"
_SECRET_QS_RE = re.compile(r"(?i)(" + _KEYISH + r")=[^&\s\"']+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
# W2.5 (audit C4): the old two patterns missed every OTHER shape a secret travels in.
# JSON bodies ("api_key": "sk-..."), header-colon lines (X-Api-Key: abc, Authorization:
# xyz), BARE vendor tokens pasted into an exception (xoxb-…, sk-ant-…), and
# connection-string passwords (postgres://user:pw@host) all passed through verbatim.
_SECRET_JSON_RE = re.compile(r"(?i)([\"'](?:" + _KEYISH + r")[\"']\s*:\s*)[\"'][^\"']+[\"']")
# Header lines are line-structured: consume to end-of-line so a two-token value
# ("Authorization: Basic <cred>") never leaves its second token behind.
_SECRET_HDR_RE = re.compile(
    r"(?i)\b(x-api-key|api-key|authorization|x-auth-token|x-secret)\s*:\s*[^\r\n]+")
# Known vendor token prefixes only (never a generic long-string match — that would
# redact ids/urls): Slack xox*/xapp, Anthropic sk-ant-*, Resend re_*, Prospeo pk_*.
_BARE_TOKEN_RE = re.compile(
    r"\b(xox[a-z]-[A-Za-z0-9-]{8,}|xapp-[A-Za-z0-9-]{8,}|sk-ant-[A-Za-z0-9_-]{8,}"
    r"|re_[A-Za-z0-9]{16,}|pk_[A-Za-z0-9]{16,})\b")
_URL_PW_RE = re.compile(r"(://[^/:@\s]+:)[^@\s]+(@)")

# A CREDENTIAL CARRIED AS A URL *PATH SEGMENT* survived every rule above (measured 2026-09-06:
# myemailverifier's /verifier/getcredits/<key> came through verbatim). There is no `key=` to
# anchor on and it is not a bearer, so the only handle is the marker segment in front of it.
# The length floor keeps an ordinary route ("/api/v2/leads/list") from being eaten.
_SECRET_PATH_RE = re.compile(
    r"(?i)(/(?:getcredits|credits|apikey|api[_-]?key|key|token|secret)/)[A-Za-z0-9._-]{12,}")


def scrub_secrets(text: str) -> str:
    """Redact every known secret SHAPE from a string bound for a log/exception/DB-error
    sink: query-string pairs, Bearer headers, JSON key/value pairs, header-colon lines,
    bare vendor tokens (known prefixes only), and connection-string passwords.
    Behaviour-preserving on strings that carry no secret; empty/falsy returns unchanged."""
    if not text:
        return text
    s = _SECRET_QS_RE.sub(lambda m: m.group(1) + "=REDACTED", str(text))
    s = _BEARER_RE.sub("Bearer REDACTED", s)
    s = _SECRET_JSON_RE.sub(lambda m: m.group(1) + '"REDACTED"', s)
    s = _SECRET_HDR_RE.sub(lambda m: m.group(1) + ": REDACTED", s)
    s = _BARE_TOKEN_RE.sub("REDACTED", s)
    s = _URL_PW_RE.sub(r"\1REDACTED\2", s)
    return _SECRET_PATH_RE.sub(lambda m: m.group(1) + "REDACTED", s)


def redact(text, *values) -> str:
    """Remove secret values the caller HOLDS, exactly, then run the shape-guessing backstop.

    scrub_secrets infers a secret from its surroundings; that is the wrong tool when the
    credential is a bare path segment with nothing around it to key on. A caller with the key
    in hand can do better, and should. Values under 8 characters are ignored — redacting those
    would eat ordinary words out of the message and tell nobody anything."""
    s = str(text)
    for v in values:
        v = (v or "").strip()
        if len(v) >= 8:
            s = s.replace(v, "REDACTED")
    return scrub_secrets(s)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        # W2.5 (audit C4): the formatter is the LAST line of defense — every log.*(...,
        # field=value) used to reach stdout/journald unscrubbed; a raw token in any field
        # (an exception string, a vendor response echo) landed in the journal verbatim.
        # Scrubbing the rendered line covers msg + every field in one pass.
        return scrub_secrets(json.dumps(payload, default=str))


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JsonFormatter())


class _Logger:
    def __init__(self, name: str):
        self._l = logging.getLogger(name)
        self._l.setLevel(logging.INFO)
        if not self._l.handlers:
            self._l.addHandler(_handler)

    def _log(self, level: int, msg: str, **fields) -> None:
        self._l.log(level, msg, extra={"fields": fields})

    def info(self, msg: str, **fields) -> None:
        self._log(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields) -> None:
        self._log(logging.WARNING, msg, **fields)

    def error(self, msg: str, **fields) -> None:
        self._log(logging.ERROR, msg, **fields)


def get_logger(name: str) -> _Logger:
    return _Logger(name)
