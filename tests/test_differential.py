import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import differential as diff
from evals.run import rust_test

CASES = json.loads((Path(__file__).parents[1] / "evals/translation.json").read_text())["cases"]
RUST = {
    "add": "pub fn add(a:i64,b:i64)->i64 {a+b}",
    "floor_mod": """
        pub fn floor_div(a:i64,b:i64)->i64 {
            let q=a/b; let r=a%b;
            if r!=0 && ((r>0)!=(b>0)) {q-1} else {q}
        }
        pub fn remainder(a:i64,b:i64)->i64 {
            let r=a%b;
            if r!=0 && ((r>0)!=(b>0)) {r+b} else {r}
        }
    """,
    "clamp": "pub fn clamp(n:i64,low:i64,high:i64)->i64 {n.max(low).min(high)}",
    "range_sum": "pub fn total(n:i64)->i64 {(0..n).sum()}",
    "absolute": "pub fn absolute(n:i64)->i64 {if n<0 {-n} else {n}}",
}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_input_generation_is_repeatable_and_bounded(case):
    calls = diff.input_calls(case["id"])
    assert calls == diff.input_calls(case["id"])
    assert len(calls) >= 100
    assert all(diff.MIN_I64 <= v <= diff.MAX_I64 for c in calls for v in c["args"])


def test_domain_exclusions_and_zero_divisors():
    calls = diff.input_calls("floor_mod")
    assert not any(c["args"] == [diff.MIN_I64, -1] for c in calls)
    assert any(c["args"][1] == 0 for c in calls)
    assert any(c["args"][0] == diff.MIN_I64 for c in calls)
    with pytest.raises(ValueError, match="No differential"):
        diff.input_calls("unknown")


@pytest.mark.parametrize(
    "function,args,result",
    [
        ("bad();", [1], {"value": 1}),
        ("f", [True], {"value": 1}),
        ("f", [2**63], {"value": 1}),
        ("f", [1], {"value": "1"}),
        ("f", [1], {"error": "OtherError"}),
    ],
)
def test_assertion_builder_rejects_invalid_reference_data(function, args, result):
    with pytest.raises(ValueError):
        diff.rust_assertions([{"function": function, "args": args}], [result])


def test_reference_count_mismatch():
    with pytest.raises(ValueError, match="count mismatch"):
        diff.rust_assertions([{"function": "f", "args": []}], [])


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "wrong_count"])
def test_reference_container_boundaries_and_cleanup(monkeypatch, outcome):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "rm":
            return SimpleNamespace(returncode=0)
        assert kwargs["timeout"] == 30
        assert "shell" not in kwargs
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        return SimpleNamespace(
            returncode=1 if outcome == "failure" else 0,
            stdout="[]" if outcome == "wrong_count" else '[{"value": 2}]',
        )

    monkeypatch.setattr(diff.subprocess, "run", run)
    calls = [{"function": "f", "args": [1]}]
    if outcome == "success":
        assert diff.python_results("def f(x): return x+1", calls) == [{"value": 2}]
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            diff.python_results("def f(x): return x+1", calls)
    command = commands[0]
    for flag in ["--network=none", "--read-only", "--cap-drop=ALL", "--memory=128m"]:
        assert flag in command
    assert "--env" not in command and "-e" not in command and "-v" not in command
    assert commands[-1][:3] == ["docker", "rm", "-f"]


sandbox = pytest.mark.skipif(
    os.getenv("RUN_SANDBOX_TESTS") != "1", reason="requires explicitly enabled Docker sandboxes"
)


@sandbox
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_real_python_rust_differential_and_wrong_translation(case):
    assertions, count = diff.differential_assertions(case)
    assert count >= 100
    assert rust_test(RUST[case["id"]], assertions)["passed"]
    # A compilable but semantically wrong function must fail the same oracle.
    wrong = RUST[case["id"]].replace("a+b", "a-b").replace("q-1", "q")
    wrong = wrong.replace("n.max(low).min(high)", "n").replace("(0..n).sum()", "n")
    wrong = wrong.replace("if n<0 {-n} else {n}", "n")
    assert not rust_test(wrong, assertions)["passed"]


@sandbox
def test_reference_has_no_host_secret_or_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sandbox-test-canary")
    source = """
import os, socket
def probe():
    assert 'OPENAI_API_KEY' not in os.environ
    try:
        socket.create_connection(('192.0.2.1', 80), timeout=0.1)
    except OSError:
        return 1
    raise AssertionError('Unexpected network access')
"""
    assert diff.python_results(source, [{"function": "probe", "args": []}]) == [{"value": 1}]


@sandbox
@pytest.mark.parametrize(
    "code",
    [
        "this is invalid Rust",
        "pub fn add(a:i64,b:i64)->i64 {std::process::exit(0)}",
    ],
)
def test_compilation_failure_or_early_exit_is_not_success(code):
    assert not rust_test(code, "assert_eq!(add(1,2),3);")["passed"]
