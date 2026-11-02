from unittest.mock import Mock

import pytest

from solution.cli import main
from solution.models import Result


def test_missing_key(monkeypatch):
    monkeypatch.setattr("sys.argv", ["solution", "--model", "test-model"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("status,exit_code", [("ok", 0), ("error", 1), ("unsupported", 1)])
def test_exit_status(monkeypatch, status, exit_code):
    monkeypatch.setattr("sys.argv", ["solution", "--model", "test-model"])
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setattr("solution.cli.LLMAnalyzer", Mock(return_value=Mock(calls=[])))
    monkeypatch.setattr("solution.cli.atomic_write", Mock())
    monkeypatch.setattr(
        "solution.cli.process_directory",
        Mock(
            return_value=[
                Result(path="test.py", status=status),
            ]
        ),
    )
    assert main() == exit_code


def test_explicit_env_file(monkeypatch, tmp_path):
    env = tmp_path / "settings.env"
    env.write_text("OPENAI_API_KEY=test-key\nLLM_MODEL=test-model\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr("sys.argv", ["solution", "--env-file", str(env)])
    factory = Mock(return_value=Mock(calls=[]))
    monkeypatch.setattr("solution.cli.LLMAnalyzer", factory)
    monkeypatch.setattr("solution.cli.atomic_write", Mock())
    monkeypatch.setattr("solution.cli.process_directory", Mock(return_value=[]))
    assert main() == 0
    assert factory.call_args.args[0] == "test-model"


@pytest.fixture(autouse=True)
def isolated_cli_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--model", "   "],
        ["--max-calls", "0"],
        ["--max-calls", "-1"],
        ["--max-output-tokens", "0"],
        ["--timeout", "0"],
        ["--timeout", "-1"],
        ["--timeout", "nan"],
        ["--timeout", "inf"],
    ],
)
def test_invalid_configuration(monkeypatch, args):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    if args and args[0] != "--model":
        monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr("sys.argv", ["solution", *args])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_whitespace_key_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    monkeypatch.setattr("sys.argv", ["solution", "--model", "test-model"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_cli_exception_redacts_key(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-value")
    monkeypatch.setattr("sys.argv", ["solution", "--model", "test-model"])
    monkeypatch.setattr(
        "solution.cli.LLMAnalyzer", Mock(side_effect=ValueError("private-test-value"))
    )
    assert main() == 1
    assert "private-test-value" not in capsys.readouterr().err


def test_environment_overrides_dotenv_and_local_backend(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=from-file\nLLM_MODEL=from-file\n")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setattr("sys.argv", ["solution", "--rust-backend", "local", "--skip-rust-check"])
    factory = Mock(return_value=Mock(calls=[]))
    process = Mock(return_value=[Result(path="a.py", status="ok")])
    monkeypatch.setattr("solution.cli.LLMAnalyzer", factory)
    monkeypatch.setattr("solution.cli.process_directory", process)
    monkeypatch.setattr("solution.cli.atomic_write", Mock())
    assert main() == 0
    assert factory.call_args.args[0] == "env-model"
    assert process.call_args.kwargs["rust_validator"] is None
    assert process.call_args.kwargs["check_rust"] is False


def test_cli_persists_usage_and_report_with_fake_llm(monkeypatch, tmp_path):
    import json

    from solution.models import Analysis

    source = tmp_path / "input"
    source.mkdir()
    (source / "a.py").write_text("# no functions")
    output = tmp_path / "out"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "sys.argv",
        ["solution", "--model", "test-model", "--input", str(source), "--output", str(output)],
    )
    fake = Mock(calls=[{"input_tokens": 10, "output_tokens": 2}])
    fake.analyze.return_value = Analysis(
        language="Python",
        copyright_holders=[],
        license_name="MIT",
        license_category="permissive",
        functions=[],
    )
    monkeypatch.setattr("solution.cli.LLMAnalyzer", Mock(return_value=fake))
    assert main() == 0
    assert json.loads((output / "usage.json").read_text())["calls"] == fake.calls
    assert json.loads((output / "results.json").read_text())["results"][0]["status"] == "ok"
