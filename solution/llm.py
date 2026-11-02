import json
import time
from typing import Any, Protocol, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from .models import Analysis, RustSource
from .prompts import CURRENT_VERSION, load_current_prompt

PROMPT_VERSION = CURRENT_VERSION
_PROMPTS = load_current_prompt()
SYSTEM, ANALYZE, TRANSLATE = (_PROMPTS[key] for key in ("system", "analyze", "translate"))


class Analyzer(Protocol):
    def analyze(self, path: str, source: str) -> Analysis: ...
    def translate(self, path: str, source: str) -> RustSource: ...


T = TypeVar("T", bound=BaseModel)


class LLMAnalyzer:
    def __init__(
        self, model: str, timeout: float = 60, max_output_tokens: int = 8192, max_calls: int = 100
    ) -> None:
        self.model = ChatOpenAI(
            model=model,
            timeout=timeout,
            max_retries=2,
            max_completion_tokens=max_output_tokens,
            temperature=0,
            reasoning_effort="none",
        )
        self.model_name = model
        self.max_calls = max_calls
        self.calls: list[dict[str, Any]] = []

    def _invoke(self, schema: type[T], instruction: str, path: str, source: str) -> T:
        chain = self.model.with_structured_output(
            schema, method="json_schema", strict=True, include_raw=True
        )
        messages = [
            ("system", SYSTEM + instruction),
            ("human", json.dumps({"path": path, "source": source}, ensure_ascii=False)),
        ]
        # One bounded retry for malformed structured responses; transport retries live in the SDK.
        for attempt in range(2):
            try:
                if len(self.calls) >= self.max_calls:
                    raise RuntimeError("LLM call limit reached")
                call: dict[str, Any] = {
                    "path": path,
                    "operation": schema.__name__,
                    "model": self.model_name,
                    "attempt": attempt + 1,
                    "prompt_version": PROMPT_VERSION,
                    "status": "error",
                }
                self.calls.append(call)
                started = time.monotonic()
                try:
                    response = chain.invoke(messages)
                    usage = response["raw"].usage_metadata or {}
                    call.update(
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                    )
                    if response.get("parsing_error"):
                        raise OutputParserException("Invalid structured model response")
                    if response.get("parsed") is None:
                        raise ValueError("Model refused or returned no structured result")
                    parsed = schema.model_validate(response["parsed"])
                    call["status"] = "ok"
                    return parsed
                finally:
                    call["duration_seconds"] = round(time.monotonic() - started, 3)
            except (ValidationError, OutputParserException):
                if attempt:
                    raise

        raise RuntimeError("Structured response retry loop exhausted")

    def analyze(self, path: str, source: str) -> Analysis:
        return self._invoke(Analysis, ANALYZE, path, source)

    def translate(self, path: str, source: str) -> RustSource:
        return self._invoke(RustSource, TRANSLATE, path, source)
