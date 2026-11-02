import os
from pathlib import Path

import pytest

from solution.llm import LLMAnalyzer
from solution.pipeline import process_directory, validate_rust
from solution.rust_sandbox import validate_rust_isolated


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_LLM_TESTS") != "1", reason="Opt-in paid API test")
def test_live_archive(tmp_path):
    results = process_directory(
        Path(__file__).resolve().parents[1] / "data",
        tmp_path / "out",
        LLMAnalyzer(os.environ["LLM_MODEL"], max_calls=6),
        verify_functions=True,
        rust_validator=validate_rust_isolated,
    )
    assert [r.status for r in results] == ["ok"] * 3
    assert [r.analysis.copyright_holders for r in results] == [
        ["Elon Musk"],
        ["Guido Musk"],
        ["Brendan Eich"],
    ]
    assert [r.analysis.license_name for r in results] == [
        "GPL-3.0-or-later",
        "GPL-3.0-or-later",
        "MIT",
    ]
    assert results[0].rust_validation == "passed"
    assert [(f.name, f.argument_count) for f in results[1].analysis.functions] == [
        ("foo", 3),
        ("bar", 1),
        ("foo1", 1),
        ("bar1", 1),
        ("bar1", 1),
    ]
    assert [(f.name, f.argument_count) for f in results[2].analysis.functions] == [("hello", 1)]


def test_real_rust_compiler():
    import shutil

    if not shutil.which("rustc"):
        pytest.skip("rustc is not installed")
    validate_rust('pub fn foo() { println!("foo"); }')
    with pytest.raises(ValueError, match="compilation failed"):
        validate_rust("this is not Rust")
