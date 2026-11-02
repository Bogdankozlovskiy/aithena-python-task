from pathlib import Path
from unittest.mock import Mock

import pytest

from solution.errors import safe_error
from solution.licensing import normalize_license
from solution.llm import LLMAnalyzer
from solution.models import Analysis, Function
from solution.pipeline import process_directory
from solution.rust_sandbox import validate_rust_isolated
from solution.source_validation import extract_functions


def test_python_parameters_and_nested_functions():
    source = """async def foo(a, /, b=1, *args, c=2, **kwargs):
    def inner(): pass
    return b
"""
    assert extract_functions(source, "Python") == [
        Function(name="foo", argument_count=5),
        Function(name="inner", argument_count=0),
    ]


def test_javascript_parameters_and_duplicates():
    source = "function foo({a,b}, x=2, ...rest) {}\nfunction foo() {}\nfoo(1);"
    assert extract_functions(source, "JavaScript") == [
        Function(name="foo", argument_count=3),
        Function(name="foo", argument_count=0),
    ]


def test_invalid_javascript():
    with pytest.raises(ValueError, match="syntax"):
        extract_functions("function {", "JavaScript")


def test_archive_parsed_without_execution():
    data = Path(__file__).resolve().parents[1] / "data"
    assert len(extract_functions((data / "2.py").read_text(), "Python")) == 5
    assert extract_functions((data / "3.py").read_text(), "JavaScript") == [
        Function(name="hello", argument_count=1),
    ]


def test_mismatch_fails_closed(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.py").write_text("def foo(): pass")
    backend = Mock()
    backend.analyze.return_value = Analysis(
        language="Python",
        copyright_holders=[],
        license_name="MIT",
        license_category="permissive",
        functions=[],
    )
    (result,) = process_directory(source, tmp_path / "out", backend, verify_functions=True)
    assert result.status == "error"
    assert "disagrees" in result.error
    backend.translate.assert_not_called()


def test_redact_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-value")
    message = safe_error(ValueError("test-secret-value and sk-proj-test-token"))
    assert "test-secret-value" not in message
    assert "sk-proj" not in message


def test_license_normalization_and_conflict():
    value = Analysis(
        language="JavaScript",
        copyright_holders=[],
        license_name="MIT License",
        license_category="permissive",
        functions=[],
    )
    assert normalize_license(value).license_name == "MIT"
    with pytest.raises(ValueError, match="category"):
        normalize_license(value.model_copy(update={"license_category": "copyleft"}))


def test_call_budget():
    backend = object.__new__(LLMAnalyzer)
    backend.model = Mock()
    backend.calls = []
    backend.max_calls = 0
    with pytest.raises(RuntimeError, match="limit"):
        backend.analyze("a.py", "pass")
    backend.model.with_structured_output.return_value.invoke.assert_not_called()


def test_sandbox_isolation_flags(monkeypatch):
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr("solution.rust_sandbox.subprocess.run", run)
    validate_rust_isolated("pub fn foo() {}")
    command = run.call_args_list[0].args[0]
    for flag in [
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--memory=256m",
        "--pids-limit=64",
        "--security-opt=no-new-privileges",
    ]:
        assert flag in command
    assert not any("OPENAI" in value for value in command)
    assert run.call_args_list[1].args[0][:3] == ["docker", "rm", "-f"]


def test_sandbox_rejects_invalid_code_and_cleans_up(monkeypatch):
    run = Mock(side_effect=[Mock(returncode=1), Mock(returncode=0)])
    monkeypatch.setattr("solution.rust_sandbox.subprocess.run", run)
    with pytest.raises(ValueError, match="compilation failed"):
        validate_rust_isolated("invalid")
    assert run.call_count == 2


def test_sandbox_timeout_still_cleans_up(monkeypatch):
    import subprocess

    run = Mock(side_effect=[subprocess.TimeoutExpired("docker", 45), Mock(returncode=0)])
    monkeypatch.setattr("solution.rust_sandbox.subprocess.run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        validate_rust_isolated("invalid")
    assert run.call_count == 2
