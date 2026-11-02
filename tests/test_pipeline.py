from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from solution.models import Analysis, Function, RustSource
from solution.pipeline import process_directory

DATA = Path(__file__).resolve().parents[1] / "data"


def analysis(count=2, category="copyleft", **kwargs):
    return Analysis(
        language="Python",
        copyright_holders=["Elon Musk"],
        license_name="MIT" if category == "permissive" else "GPL-3.0-or-later",
        license_category=category,
        functions=[Function(name=f"f{i}", argument_count=0) for i in range(count)],
    ).model_copy(update=kwargs)


@pytest.fixture
def backend():
    backend = Mock()
    backend.analyze.return_value = analysis()
    backend.translate.return_value = RustSource(code='pub fn foo() { println!("foo"); }')
    return backend


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "input"
    directory.mkdir()
    (directory / "test.py").write_text("def foo(): pass")
    return directory


@pytest.mark.parametrize(
    "count,category,action",
    [
        (0, "copyleft", "translate_to_rust"),
        (2, "copyleft", "translate_to_rust"),
        (3, "copyleft", "extract_functions"),
        (1, "permissive", "extract_functions"),
    ],
)
def test_branches(tmp_path, source, backend, count, category, action):
    backend.analyze.return_value = analysis(count, category)
    (result,) = process_directory(source, tmp_path / "out", backend, check_rust=False)
    assert result.status == "ok"
    assert result.action == action
    assert backend.translate.call_count == (action == "translate_to_rust")
    if result.rust_path:
        assert (
            tmp_path / "out" / result.rust_path
        ).read_text() == backend.translate.return_value.code
        assert result.rust_validation == "skipped"


def test_archive_cases(tmp_path, backend):
    backend.analyze.side_effect = [
        analysis(
            functions=[
                Function(name="foo", argument_count=0),
                Function(name="bar", argument_count=0),
            ]
        ),
        analysis(
            copyright_holders=["Guido Musk"],
            functions=[
                Function(name=name, argument_count=count)
                for name, count in [("foo", 3), ("bar", 1), ("foo1", 1), ("bar1", 1), ("bar1", 1)]
            ],
        ),
        analysis(
            category="permissive",
            language="JavaScript",
            license_name="MIT",
            copyright_holders=["Brendan Eich"],
            functions=[Function(name="hello", argument_count=1)],
        ),
    ]
    results = process_directory(DATA, tmp_path / "out", backend, check_rust=False)
    assert [r.action for r in results] == [
        "translate_to_rust",
        "extract_functions",
        "extract_functions",
    ]
    assert len(results[1].analysis.functions) == 5
    assert results[1].analysis.functions[-2:] == [Function(name="bar1", argument_count=1)] * 2
    assert results[2].analysis.language == "JavaScript"
    assert "function hello(world)" in backend.analyze.call_args_list[2].args[1]


@pytest.mark.parametrize("value", [analysis(category="unknown"), analysis(license_name=None)])
def test_unknown_license(tmp_path, source, backend, value):
    backend.analyze.return_value = value
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "unsupported"
    backend.translate.assert_not_called()


def test_partial_failure(tmp_path, source, backend):
    (source / "z.py").write_text("def bar(): pass")
    backend.analyze.side_effect = [TimeoutError("API unavailable"), analysis(3)]
    results = process_directory(source, tmp_path / "out", backend)
    assert [r.status for r in results] == ["error", "ok"]
    assert "TimeoutError" in results[0].error
    assert (tmp_path / "out/results.json").is_file()


def test_invalid_model_response(tmp_path, source, backend):
    backend.analyze.return_value = {"language": "Python"}
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    assert "ValidationError" in result.error


def test_compile_failure_is_not_success(tmp_path, source, backend, monkeypatch):
    monkeypatch.setattr("solution.pipeline.validate_rust", Mock(side_effect=ValueError("bad Rust")))
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    assert result.analysis.license_name == "GPL-3.0-or-later"
    assert result.rust_path is None


def test_compiler_called(tmp_path, source, backend, monkeypatch):
    compiler = Mock()
    monkeypatch.setattr("solution.pipeline.validate_rust", compiler)
    (result,) = process_directory(source, tmp_path / "out", backend)
    compiler.assert_called_once_with(backend.translate.return_value.code)
    assert result.rust_validation == "passed"


def test_overlap_rejected(source, backend):
    with pytest.raises(ValueError, match="overlap"):
        process_directory(source, source / "out", backend)


def test_bad_encoding(tmp_path, source, backend):
    (source / "test.py").write_bytes(b"\xff")
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    backend.analyze.assert_not_called()


def test_function_schema_rejects_negative_and_coercion():
    for count in [-1, "1", True]:
        with pytest.raises(ValidationError):
            Function(name="foo", argument_count=count)


def test_extension_collision(tmp_path, source, backend):
    (source / "test.js").write_text("function foo() {}")
    results = process_directory(source, tmp_path / "out", backend, check_rust=False)
    assert {r.rust_path for r in results} == {"rust/test.js.rs", "rust/test.py.rs"}
