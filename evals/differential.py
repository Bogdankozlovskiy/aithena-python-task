"""Compare generated Rust against Python 3.13 in credential-free containers."""

import json
import random
import re

# Explicit argv; the reference source executes only inside a restricted container.
import subprocess  # nosec B404
import uuid
from contextlib import suppress
from itertools import product
from typing import Any

MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
SEED = 20261102

# This program is sent to isolated Python, never executed in the host process.
REFERENCE = """
import json, sys
assert sys.version_info[:2] == (3, 13)
payload = json.load(sys.stdin)
namespace = {}
exec(compile(payload['source'], '<reference>', 'exec'), namespace)
results = []
for call in payload['calls']:
    try:
        value = namespace[call['function']](*call['args'])
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise ValueError('Reference result is outside the signed i64 contract')
        results.append({'value': value})
    except ZeroDivisionError:
        results.append({'error': 'ZeroDivisionError'})
print(json.dumps(results))
"""


def input_calls(case_id: str) -> list[dict[str, Any]]:
    """Deterministic boundaries plus seeded generated inputs within the stated domain."""
    rng = random.Random(SEED)  # nosec B311
    edges = [MIN_I64, MIN_I64 + 1, -7, -3, -1, 0, 1, 3, 7, MAX_I64 - 1, MAX_I64]
    pairs = list(product(edges, repeat=2)) + [
        (rng.randint(MIN_I64, MAX_I64), rng.randint(MIN_I64, MAX_I64)) for _ in range(100)
    ]
    calls: list[dict[str, Any]] = []

    def add(function: str, *args: int) -> None:
        calls.append({"function": function, "args": list(args)})

    if case_id == "holdout_gcd":
        for a, b in pairs:
            if a != MIN_I64 and b != MIN_I64:
                add("gcd", a, b)
    elif case_id == "holdout_factorial":
        for n in range(21):
            add("factorial", n)
    elif case_id == "holdout_sign":
        for n in edges + [rng.randint(MIN_I64, MAX_I64) for _ in range(100)]:
            add("sign", n)
    elif case_id == "add":
        for a, b in pairs:
            if MIN_I64 <= a + b <= MAX_I64:
                add("add", a, b)
    elif case_id == "floor_mod":
        for a, b in pairs:
            # MIN / -1 overflows Rust's intermediate quotient, even for remainder.
            if (a, b) != (MIN_I64, -1):
                add("floor_div", a, b)
                add("remainder", a, b)
    elif case_id == "clamp":
        for n in edges + [rng.randint(MIN_I64, MAX_I64) for _ in range(100)]:
            for low, high in [(MIN_I64, MAX_I64), (-3, 7), (0, 0)]:
                add("clamp", n, low, high)
    elif case_id == "range_sum":
        for n in [MIN_I64, -7, -1, 0, 1, 2, 1000] + [rng.randint(-100, 1000) for _ in range(100)]:
            add("total", n)
    elif case_id == "absolute":
        for n in edges[1:] + [rng.randint(MIN_I64 + 1, MAX_I64) for _ in range(100)]:
            add("absolute", n)
    else:
        raise ValueError("No differential input strategy for case: " + case_id)
    return calls


def python_results(source: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    name = "aithena-reference-" + uuid.uuid4().hex
    try:
        # Fixed argv, no host mounts, no inherited container credentials, no networking.
        result = subprocess.run(  # nosec B603, B607
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--name",
                name,
                "-i",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                "--memory=128m",
                "--cpus=1",
                "--pids-limit=32",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=16m,mode=1777",
                "aithena-python-reference:local",
                "python",
                "-I",
                "-c",
                REFERENCE,
            ],
            input=json.dumps({"source": source, "calls": calls}),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise ValueError("Isolated Python reference failed")
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != len(calls):
            raise ValueError("Reference result count mismatch")
        return rows
    finally:
        with suppress(OSError, subprocess.TimeoutExpired):
            # Fixed cleanup argv and a generated container name.
            subprocess.run(  # nosec B603, B607
                ["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False
            )


def rust_assertions(calls: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    if len(calls) != len(results):
        raise ValueError("Reference result count mismatch")
    assertions = []
    for index, (call, result) in enumerate(zip(calls, results, strict=True)):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", call["function"]):
            raise ValueError("Invalid reference function name")
        values = call["args"] + ([] if "error" in result else [result.get("value")])
        if any(type(v) is not int or not MIN_I64 <= v <= MAX_I64 for v in values):
            raise ValueError("Expected signed i64 integers")
        arguments = ", ".join(f"{v}_i64" for v in call["args"])
        expression = f"{call['function']}({arguments})"
        if result.get("error") == "ZeroDivisionError":
            assertion = f"assert!(std::panic::catch_unwind(|| {expression}).is_err());"
        elif "error" in result:
            raise ValueError("Unsupported reference exception")
        else:
            assertion = f'assert_eq!({expression}, {result["value"]}_i64, "input {index}");'
        assertions.append(assertion)
    return "\n".join(assertions)


def differential_assertions(case: dict[str, Any]) -> tuple[str, int]:
    calls = input_calls(case["id"])
    return rust_assertions(calls, python_results(case["source"], calls)), len(calls)
