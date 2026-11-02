from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Function(StrictModel):
    name: str = Field(min_length=1)
    argument_count: int = Field(ge=0)


class Analysis(StrictModel):
    language: str = Field(min_length=1)
    copyright_holders: list[str]
    license_name: str | None
    license_category: Literal["permissive", "copyleft", "unknown"]
    functions: list[Function]


class RustSource(StrictModel):
    code: str = Field(min_length=1)


class Result(StrictModel):
    path: str
    status: Literal["ok", "unsupported", "error"]
    source_sha256: str | None = None
    functions_validation: Literal["passed", "unsupported"] | None = None
    analysis: Analysis | None = None
    action: Literal["extract_functions", "translate_to_rust"] | None = None
    rust_path: str | None = None
    rust_validation: Literal["passed", "skipped"] | None = None
    error: str | None = None
