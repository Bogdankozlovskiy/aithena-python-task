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
