from unittest.mock import Mock

import pytest

from solution.licensing import normalize_license
from solution.models import Analysis, Function, RustSource
from solution.pipeline import process_directory
from solution.source_validation import detect_functions


def run_case(tmp_path, source, language, functions, license_name="MIT", category="permissive"):
    directory = tmp_path / "input"
    directory.mkdir()
    (directory / "untrusted.extension").write_text(source)
    backend = Mock()
    backend.analyze.return_value = Analysis(
        language=language,
        copyright_holders=[],
        license_name=license_name,
        license_category=category,
        functions=functions,
    )
    backend.translate.return_value = RustSource(code="pub fn f(a:i64) {}")
    (result,) = process_directory(
        directory, tmp_path / "out", backend, verify_functions=True, check_rust=False
    )
    return result, backend


@pytest.mark.parametrize("label", ["Python 3", "Node.js", "Ruby", "Unknown"])
@pytest.mark.parametrize("source", ["def f(a): pass", "function f(a) {}"])
def test_false_functions_rejected_for_any_model_language(tmp_path, label, source):
    result, backend = run_case(tmp_path, source, label, [])
    assert result.status == "error"
    assert "disagrees" in result.error
    assert result.action is None
    backend.translate.assert_not_called()


@pytest.mark.parametrize(
    "label,source",
    [
        ("Python 3", "def f(a): pass"),
        ("Node.js", "function f(a) {}"),
        ("JavaScript", "def f(a): pass"),
        ("Python", "function f(a) {}"),
    ],
)
def test_valid_functions_verified_independently_of_labels(tmp_path, label, source):
    result, _ = run_case(tmp_path, source, label, [Function(name="f", argument_count=1)])
    assert result.status == "ok"
    assert result.functions_validation == "passed"


def test_unsupported_syntax_cannot_produce_success(tmp_path):
    result, backend = run_case(tmp_path, "int f(void) { return 1; }", "C", [])
    assert result.status == "unsupported"
    assert result.functions_validation == "unsupported"
    assert result.action is None
    backend.translate.assert_not_called()


@pytest.mark.parametrize("source", ["", "x = 1"])
def test_both_parsers_agree_on_empty_function_list(source):
    assert detect_functions(source) == []


def test_disagreeing_parsers_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "solution.source_validation.extract_functions",
        Mock(side_effect=[[Function(name="f", argument_count=1)], []]),
    )
    with pytest.raises(ValueError, match="parsers disagree"):
        detect_functions("ambiguous source")


@pytest.mark.parametrize(
    "name", ["GPL-3.0", "GPL-3.0+", "GPLv3", "Custom-License", "MIT OR GPL-3.0-only"]
)
@pytest.mark.parametrize("category", ["permissive", "copyleft"])
def test_unknown_license_cannot_select_an_action(tmp_path, name, category):
    result, backend = run_case(
        tmp_path, "def f(a): pass", "Python", [Function(name="f", argument_count=1)], name, category
    )
    assert result.status == "unsupported"
    assert result.analysis.license_name == name
    assert result.analysis.license_category == "unknown"
    assert result.action is None
    backend.translate.assert_not_called()


@pytest.mark.parametrize(
    "name,category,action",
    [
        ("MIT License", "permissive", "extract_functions"),
        ("GPL-3.0-only", "copyleft", "translate_to_rust"),
        ("GPL-3.0-or-later", "copyleft", "translate_to_rust"),
    ],
)
def test_known_licenses_keep_expected_branch(tmp_path, name, category, action):
    result, backend = run_case(
        tmp_path, "def f(a): pass", "Python", [Function(name="f", argument_count=1)], name, category
    )
    assert result.status == "ok"
    assert result.action == action
    assert backend.translate.call_count == (action == "translate_to_rust")


def test_known_license_with_unknown_category_remains_abstention():
    value = Analysis(
        language="Python",
        copyright_holders=[],
        license_name="MIT",
        license_category="unknown",
        functions=[],
    )
    assert normalize_license(value).license_category == "unknown"
