import os
import re

from langchain_core.exceptions import OutputParserException
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError


def safe_error(exc: Exception) -> str:
    """Omit known provider/schema details; redact the configured key and sk- patterns.

    Other exception text is truncated, not comprehensively scrubbed of secrets.
    """
    if isinstance(exc, APIStatusError):
        return f"Provider HTTP {exc.status_code}; check credentials, balance and rate limits"
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return f"{type(exc).__name__}: provider connection failed"
    if isinstance(exc, OutputParserException):
        return "OutputParserException: invalid structured response"
    if isinstance(exc, ValidationError):
        return "ValidationError: response does not match the required schema"
    message = str(exc)
    secret = os.getenv("OPENAI_API_KEY")
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", f"{type(exc).__name__}: {message}")[:1000]
