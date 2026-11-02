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


# Reject the entire input when a case-insensitive credential label is followed
# by '=' or ':', including quoted JSON-style keys. Do not infer where its value
# ends, truncate it, or send any part to the model. Even empty assignments,
# placeholders, comments, and type annotations are rejected conservatively.
# This is heuristic screening, not a parser or a guarantee of detecting secrets.
_CREDENTIAL_LABEL = re.compile(
    r"\b(?:login|username|user|pwd|password|passwd|passphrase|pass|"
    r"api[_-]?key|access[_-]?token|token|client[_-]?secret|secret|"
    r"\u043b\u043e\u0433\u0438\u043d|\u043f\u0430\u0440\u043e\u043b\u044c)\b"
    r"[\"']?\s*[=:]",
    re.IGNORECASE,
)


def reject_credentials(source: str) -> None:
    if _SECRET.search(source) or _CREDENTIAL_LABEL.search(source):
        raise ValueError("Possible credential in input; file was not sent to the model")
