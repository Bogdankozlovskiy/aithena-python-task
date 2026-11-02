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
        'api_key="your-key"',
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
