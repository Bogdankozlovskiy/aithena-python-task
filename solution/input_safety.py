"""Reject recognizable credentials before source text reaches an API."""

import re

_SECRET = re.compile(
    r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    r"|(?i:(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)"
    r"\s*[=:]\s*[\"'][^\"'\r\n]{16,}[\"'])"
)


def reject_credentials(source: str) -> None:
    if _SECRET.search(source):
        raise ValueError("Possible credential in input; file was not sent to the model")


# Case-insensitive credential labels followed by '=' or ':' trigger value redaction.
# Skip separator whitespace and an opening quote, then replace up to 15 value
# characters, stopping at a quote or line break. Apply once at the API boundary.
# This requested fixed window is NOT complete secret removal:
# values longer than 15 characters can retain a visible suffix. Known-key
# rejection must run on the original text before this transformation.
_TRIGGER_VALUE = re.compile(
    r"(?P<prefix>\b(?:login|username|user|pwd|password|passwd|passphrase|pass|"
    r"api[_-]?key|access[_-]?token|token|client[_-]?secret|secret|"
    r"\u043b\u043e\u0433\u0438\u043d|\u043f\u0430\u0440\u043e\u043b\u044c)\b"
    r"[ \t]*+[=:][ \t]*+[\"']?+)"
    r"(?P<value>[^\r\n\"']{1,15})",
    re.IGNORECASE,
)


def redact_trigger_values(source: str) -> str:
    return _TRIGGER_VALUE.sub(lambda match: match["prefix"] + "[REDACTED]", source)
