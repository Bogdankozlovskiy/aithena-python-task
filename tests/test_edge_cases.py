"""Boundary and failure-path regression tests, with no paid API calls."""

import json
import runpy
import subprocess
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from openai import APIConnectionError, APIStatusError, APITimeoutError

from solution.errors import safe_error
from solution.licensing import normalize_license
from solution.llm import LLMAnalyzer
from solution.models import Analysis, Function, RustSource
from solution.pipeline import atomic_write, process_directory, validate_rust
from solution.rust_sandbox import validate_rust_isolated
from solution.source_validation import extract_functions


def analysis(**changes):
    return Analysis(
        **{
            "language": "Python",
            "copyright_holders": [],
            "license_name": "MIT",
            "license_category": "permissive",
            "functions": [],
            **changes,
        }
    )


@pytest.fixture
def backend():
    return Mock(
        analyze=Mock(return_value=analysis()),
        translate=Mock(return_value=RustSource(code="pub fn foo() {}")),
    )


@pytest.fixture
def dirs(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    source.mkdir()
    (source / "file.py").write_text("# no functions\n")
    return source, output


@pytest.fixture
def llm(monkeypatch):
    model = Mock()
    constructor = Mock(return_value=model)
    monkeypatch.setattr("solution.llm.ChatOpenAI", constructor)
    backend = LLMAnalyzer("test-model", timeout=12, max_output_tokens=200, max_calls=3)
    constructor.assert_called_once_with(
        model="test-model",
        timeout=12,
        max_retries=2,
        max_completion_tokens=200,
        temperature=0,
        reasoning_effort="none",
    )
    return backend, model.with_structured_output.return_value


def response(parsed=None, error=None, usage=None):
    return {"raw": Mock(usage_metadata=usage), "parsed": parsed, "parsing_error": error}


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_provider_http_errors_do_not_leak_body(status):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    error = APIStatusError(
        "private-source-and-key",
        response=httpx.Response(status, request=request),
        body={"message": "private-source-and-key"},
    )
    message = safe_error(error)
    assert str(status) in message
    assert "private-source" not in message


@pytest.mark.parametrize("kind", [APITimeoutError, APIConnectionError])
def test_provider_connection_error_redaction(kind):
    request = httpx.Request("POST", "https://example.invalid")
    assert "provider connection failed" in safe_error(kind(request=request))


def test_parser_exception_never_prints_source():
    assert "private-source" not in safe_error(OutputParserException("private-source"))


def test_error_without_environment_key_and_length_cap(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert safe_error(ValueError("ordinary error")) == "ValueError: ordinary error"
    assert len(safe_error(ValueError("x" * 2000))) <= 1000


def test_structured_parsing_error_retries_then_succeeds(llm):
    backend, chain = llm
    chain.invoke.side_effect = [response(error=ValueError("secret source")), response(analysis())]
    assert backend.analyze("x.py", "pass") == analysis()
    assert len(backend.calls) == 2
    assert [c["status"] for c in backend.calls] == ["error", "ok"]
    assert backend.calls[-1]["input_tokens"] is None  # unavailable usage is not zero cost
    assert "secret source" not in json.dumps(backend.calls)


def test_invalid_schema_retries_and_fails(llm):
    backend, chain = llm
    chain.invoke.return_value = response({"language": "Python"})
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        backend.analyze("x.py", "pass")
    assert chain.invoke.call_count == 2
    assert all(c["status"] == "error" for c in backend.calls)


def test_refusal_does_not_retry(llm):
    backend, chain = llm
    chain.invoke.return_value = response()
    with pytest.raises(ValueError, match="refused"):
        backend.analyze("x.py", "pass")
    assert chain.invoke.call_count == 1
    assert backend.calls[0]["duration_seconds"] >= 0


def test_successful_translation_and_shared_budget(llm):
    backend, chain = llm
    backend.max_calls = 1
    chain.invoke.return_value = response(
        RustSource(code="pub fn x() {}"), usage={"input_tokens": 20, "output_tokens": 10}
    )
    assert backend.translate("x.py", "pass").code == "pub fn x() {}"
    assert backend.calls[0]["operation"] == "RustSource"
    with pytest.raises(RuntimeError, match="limit"):
        backend.analyze("x.py", "pass")
    assert chain.invoke.call_count == 1


def test_budget_blocks_schema_retry(llm):
    backend, chain = llm
    backend.max_calls = 1
    chain.invoke.return_value = response(error=ValueError("bad JSON"))
    with pytest.raises(RuntimeError, match="limit"):
        backend.analyze("x.py", "pass")
    assert chain.invoke.call_count == 1


def test_transport_error_records_timing_without_application_retry(llm):
    backend, chain = llm
    chain.invoke.side_effect = APITimeoutError(
        request=httpx.Request("POST", "https://example.invalid")
    )
    with pytest.raises(APITimeoutError):
        backend.analyze("x.py", "pass")
    assert chain.invoke.call_count == 1  # SDK itself owns transport retries
    assert backend.calls[0]["status"] == "error"
    assert "duration_seconds" in backend.calls[0]


@pytest.mark.parametrize("name", [None, "", "   ", "\n\t"])
def test_missing_license_is_normalized(name):
    assert normalize_license(analysis(license_name=name)).license_name is None


@pytest.mark.parametrize(
    "name,expected",
    [
        (" mit license ", "MIT"),
        ("MIT", "MIT"),
        ("apache-2.0", "Apache-2.0"),
        ("Custom license", "Custom license"),
    ],
)
def test_license_aliases_preserve_unknown_names(name, expected):
    assert normalize_license(analysis(license_name=name)).license_name == expected


@pytest.mark.parametrize("state", ["missing", "empty", "regular_file"])
def test_invalid_input_directory(tmp_path, backend, state):
    source = tmp_path / "input"
    if state == "empty":
        source.mkdir()
    elif state == "regular_file":
        source.write_text("x")
    with pytest.raises(ValueError):
        process_directory(source, tmp_path / "out", backend)
    backend.analyze.assert_not_called()


@pytest.mark.parametrize("overlap", ["same", "output_child", "output_parent"])
def test_all_directory_overlaps(dirs, backend, overlap):
    source, _ = dirs
    output = {"same": source, "output_child": source / "out", "output_parent": source.parent}[
        overlap
    ]
    with pytest.raises(ValueError, match="overlap"):
        process_directory(source, output, backend)
    backend.analyze.assert_not_called()


@pytest.mark.parametrize("kind", ["file", "dangling", "directory"])
def test_symlinks_are_reported_without_reading_target(dirs, backend, tmp_path, kind):
    source, output = dirs
    (source / "file.py").unlink()
    target = tmp_path / "secret"
    if kind == "file":
        target.write_text("private-data")
    elif kind == "directory":
        target.mkdir()
        (target / "secret.py").write_text("private-data")
    (source / "link.py").symlink_to(target, target_is_directory=kind == "directory")
    (result,) = process_directory(source, output, backend)
    assert result.status == "error"
    assert "Symbolic links" in result.error
    backend.analyze.assert_not_called()


@pytest.mark.parametrize("size,ok", [(7, True), (8, True), (9, False)])
def test_byte_limit_boundary(dirs, backend, monkeypatch, size, ok):
    source, output = dirs
    monkeypatch.setattr("solution.pipeline.MAX_SOURCE_BYTES", 8)
    (source / "file.py").write_bytes(b"a" * size)
    (result,) = process_directory(source, output, backend)
    assert (result.status == "ok") is ok
    assert backend.analyze.call_count == int(ok)


def test_utf8_bom_and_unicode_paths(dirs, backend):
    source, output = dirs
    (source / "file.py").unlink()
    (source / "caf\u00e9.py").write_text("# café\n", encoding="utf-8-sig")
    (result,) = process_directory(source, output, backend, verify_functions=True)
    assert result.status == "ok"
    assert result.path == "caf\u00e9.py"
    assert backend.analyze.call_args.args[1] == "# café\n"
    assert len(result.source_sha256) == 64


def test_parser_success_and_unsupported_language(dirs, backend):
    source, output = dirs
    for language, status in [("Python", "passed"), ("Ruby", "unsupported")]:
        backend.analyze.return_value = analysis(language=language)
        (result,) = process_directory(source, output, backend, verify_functions=True)
        assert result.status == "ok"
        assert result.functions_validation == status


def test_rust_validator_injection_and_nested_output(dirs, backend):
    source, output = dirs
    (source / "nested").mkdir()
    (source / "file.py").rename(source / "nested" / "file.py")
    backend.analyze.return_value = analysis(
        license_name="GPL-3.0-only", license_category="copyleft"
    )
    validator = Mock()
    (result,) = process_directory(source, output, backend, rust_validator=validator)
    validator.assert_called_once_with("pub fn foo() {}")
    assert result.rust_validation == "passed"
    assert (output / "rust/nested/file.py.rs").read_text() == "pub fn foo() {}"


def test_rust_output_symlink_cannot_escape(dirs, backend, tmp_path):
    source, output = dirs
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "rust").symlink_to(outside, target_is_directory=True)
    backend.analyze.return_value = analysis(
        license_name="GPL-3.0-only", license_category="copyleft"
    )
    (result,) = process_directory(source, output, backend, check_rust=False)
    assert result.status == "error"
    assert not list(outside.iterdir())


def test_read_permission_failure_is_reported(dirs, backend, monkeypatch):
    source, output = dirs
    real_open = Path.open

    def fail_read(self, *args, **kwargs):
        if self == source / "file.py":
            raise PermissionError("read denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_read)
    (result,) = process_directory(source, output, backend)
    assert "PermissionError" in result.error
    backend.analyze.assert_not_called()


def test_atomic_failure_preserves_previous_report_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "results.json"
    target.write_text("old report")
    monkeypatch.setattr("solution.pipeline.os.replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        atomic_write(target, "new report")
    assert target.read_text() == "old report"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json"]


def test_checkpoint_survives_interruption(dirs, backend):
    source, output = dirs
    (source / "z.py").write_text("pass")
    backend.analyze.side_effect = [analysis(), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        process_directory(source, output, backend)
    report = json.loads((output / "results.json").read_text())
    assert len(report["results"]) == 1
    assert report["results"][0]["status"] == "ok"


def test_report_write_failure_is_not_success(dirs, backend, monkeypatch):
    source, output = dirs
    monkeypatch.setattr("solution.pipeline.atomic_write", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        process_directory(source, output, backend)


def test_missing_local_compiler(monkeypatch):
    monkeypatch.setattr("solution.pipeline.shutil.which", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="rustc is required"):
        validate_rust("pub fn foo() {}")


@pytest.mark.parametrize("returncode", [0, 1])
def test_local_compiler_and_temp_cleanup(monkeypatch, returncode):
    monkeypatch.setattr("solution.pipeline.shutil.which", Mock(return_value="/fake/rustc"))
    source_paths = []

    def compile_source(command, **kwargs):
        source = Path(command[4])
        source_paths.append(source)
        assert source.read_text() == "pub fn foo() {}"
        assert kwargs["timeout"] == 30
        return Mock(returncode=returncode)

    monkeypatch.setattr("solution.pipeline.subprocess.run", compile_source)
    if returncode:
        with pytest.raises(ValueError, match="compilation failed"):
            validate_rust("pub fn foo() {}")
    else:
        validate_rust("pub fn foo() {}")
    assert not source_paths[0].exists()


@pytest.mark.parametrize(
    "cleanup_error", [OSError("daemon down"), subprocess.TimeoutExpired("rm", 10)]
)
def test_cleanup_failure_does_not_mask_compiler_error(monkeypatch, cleanup_error):
    monkeypatch.setattr(
        "solution.rust_sandbox.subprocess.run",
        Mock(
            side_effect=[
                Mock(returncode=1),
                cleanup_error,
            ]
        ),
    )
    with pytest.raises(ValueError, match="compilation failed"):
        validate_rust_isolated("bad")


def test_missing_docker_preserves_original_error(monkeypatch):
    monkeypatch.setattr(
        "solution.rust_sandbox.subprocess.run", Mock(side_effect=FileNotFoundError("docker"))
    )
    with pytest.raises(FileNotFoundError, match="docker"):
        validate_rust_isolated("bad")


def test_python_decorators_methods_async_and_lambda():
    source = """class A:
    @staticmethod
    def first(x=1): pass
    async def second(self, a, /, *args, b, **kwargs): pass
f = lambda x: x
"""
    assert extract_functions(source, " Python ") == [
        Function(name="first", argument_count=1),
        Function(name="second", argument_count=5),
    ]


def test_invalid_python_is_not_silently_accepted():
    with pytest.raises(SyntaxError):
        extract_functions("def :", "Python")


def test_js_named_anonymous_generators_methods_comments():
    source = """const anon = function(a) {}; const arrow = a => a;
const named = function inner(a, /* comment */ b) {};
function* generator(...items) {}
class A { method({a, b}) {} }
"""
    assert extract_functions(source, "JS") == [
        Function(name="inner", argument_count=2),
        Function(name="generator", argument_count=1),
        Function(name="method", argument_count=1),
    ]


def test_module_entrypoint_propagates_exit_code(monkeypatch):
    monkeypatch.setattr("solution.cli.main", Mock(return_value=7))
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("solution", run_name="__main__")
    assert exc.value.code == 7


@pytest.mark.parametrize("lines,branches,exit_code", [(100, 94, 1), (94, 100, 1), (95, 95, 0)])
def test_coverage_gate_checks_each_metric(tmp_path, monkeypatch, lines, branches, exit_code):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": lines,
                    "num_statements": 100,
                    "covered_branches": branches,
                    "num_branches": 100,
                }
            }
        )
    )
    script = Path(__file__).resolve().parents[1] / "scripts/check_coverage.py"
    monkeypatch.setattr("sys.argv", [str(script), str(report)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code == exit_code
