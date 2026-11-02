from unittest.mock import Mock

import pytest

from solution.input_safety import reject_credentials
from solution.pipeline import process_directory


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-" + "x" * 40,
        "ghp_" + "x" * 40,
        "github_pat_" + "x" * 40,
        "AKIA" + "X" * 16,
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        'API_KEY="' + "x" * 20 + '"',
        'password = "' + "x" * 20 + '"',
    ],
)
def test_credentials_never_reach_model_or_report(tmp_path, secret):
    source = tmp_path / "input"
    source.mkdir()
    (source / "example.py").write_text("# " + secret)
    backend = Mock()
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    assert "not sent" in result.error
    backend.analyze.assert_not_called()
    backend.translate.assert_not_called()
    assert secret not in (tmp_path / "out/results.json").read_text()


@pytest.mark.parametrize(
    "source",
    [
        "def api_key(): pass",
        "# OPENAI_API_KEY is required",
        "# SPDX-License-Identifier: MIT\ndef f(a): return a",
    ],
)
def test_non_secret_source_is_allowed(source):
    reject_credentials(source)


def test_container_requires_explicit_trust_choice_before_api(monkeypatch, capsys):
    from solution import cli

    monkeypatch.setenv("AITHENA_CONTAINER", "1")
    monkeypatch.setattr("sys.argv", ["aithena"])
    backend = Mock()
    monkeypatch.setattr(cli, "LLMAnalyzer", backend)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "--rust-backend local" in capsys.readouterr().err
    backend.assert_not_called()


def test_credential_in_filename_is_not_sent(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    name = "sk-proj-" + "x" * 40 + ".py"
    (source / name).write_text("pass")
    backend = Mock()
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    backend.analyze.assert_not_called()
    assert name not in (tmp_path / "out/results.json").read_text()


@pytest.mark.parametrize("choice", [["--rust-backend", "local"], ["--skip-rust-check"]])
def test_container_accepts_explicit_choice(monkeypatch, tmp_path, choice):
    from solution import cli

    monkeypatch.setenv("AITHENA_CONTAINER", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "sys.argv", ["aithena", "--model", "test-model", "--output", str(tmp_path), *choice]
    )
    backend = Mock(calls=[])
    monkeypatch.setattr(cli, "LLMAnalyzer", Mock(return_value=backend))
    monkeypatch.setattr(cli, "process_directory", Mock(return_value=[]))
    assert cli.main() == 0


@pytest.mark.parametrize("field", ["path", "source"])
def test_direct_adapter_also_blocks_credentials(field):
    from solution.llm import LLMAnalyzer

    backend = object.__new__(LLMAnalyzer)
    backend.model = Mock()
    arguments = {"path": "sample.py", "source": "pass"}
    arguments[field] = "sk-proj-" + "x" * 40
    with pytest.raises(ValueError, match="not sent"):
        backend.analyze(**arguments)
    backend.model.with_structured_output.assert_not_called()


@pytest.mark.parametrize(
    "label",
    [
        "login",
        "LOGIN",
        "Login",
        "PWD",
        "passwd",
        "password",
        "username",
        "token",
        "\u043f\u0430\u0440\u043e\u043b\u044c",
        "\u041f\u0410\u0420\u041e\u041b\u042c",
        "\u043b\u043e\u0433\u0438\u043d",
    ],
)
@pytest.mark.parametrize("separator", ["=", ":"])
def test_credential_label_rejects_whole_file_before_analysis(tmp_path, label, separator):
    source = tmp_path / "input"
    source.mkdir()
    value = "sensitive-value-longer-than-fifteen-characters"
    (source / "sample.py").write_text(f'# {label} {separator} "{value}"\npass')
    backend = Mock()
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.status == "error"
    assert result.action is None
    backend.analyze.assert_not_called()
    backend.translate.assert_not_called()
    assert value not in (tmp_path / "out/results.json").read_text()


@pytest.mark.parametrize(
    "text",
    [
        "login=",
        'PWD: ""',
        'password="your-key"',
        '{"password": "abc"}',
        "login: 'unterminated",
        "PWD=first\nsecond",
        "password: str",
        "# LOGIN=abc",
    ],
)
def test_empty_ambiguous_and_quoted_assignments_are_rejected(text):
    with pytest.raises(ValueError, match="not sent"):
        reject_credentials(text)


@pytest.mark.parametrize(
    "text", ["login", "login instructions", "notlogin=abc", "def password(): pass"]
)
def test_labels_without_assignment_are_not_rejected(text):
    reject_credentials(text)


@pytest.mark.parametrize("operation", ["analyze", "translate"])
@pytest.mark.parametrize("field", ["path", "source"])
def test_adapter_rejects_trigger_without_constructing_api_request(operation, field):
    from solution.llm import LLMAnalyzer

    backend = object.__new__(LLMAnalyzer)
    backend.model = Mock()
    arguments = {"path": "sample.py", "source": "pass"}
    arguments[field] = "LOGIN=private-value"
    with pytest.raises(ValueError, match="not sent"):
        getattr(backend, operation)(**arguments)
    backend.model.with_structured_output.assert_not_called()


def test_trigger_filename_is_hidden_in_report(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    filename = "LOGIN=private-value.py"
    (source / filename).write_text("pass")
    backend = Mock()
    (result,) = process_directory(source, tmp_path / "out", backend)
    assert result.path == "[redacted filename]"
    backend.analyze.assert_not_called()
    assert filename not in (tmp_path / "out/results.json").read_text()
