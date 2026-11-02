"""Paid, opt-in, repeatable evaluation against a frozen hand-authored dataset."""

import argparse
import hashlib
import json
import os

# explicit argv; compiler execution is required
import subprocess  # nosec B404
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from solution import llm as llm_module
from solution.errors import safe_error
from solution.licensing import normalize_license
from solution.llm import LLMAnalyzer
from solution.models import Analysis
from solution.pipeline import atomic_write
from solution.source_validation import extract_functions

ROOT = Path(__file__).resolve().parent


def rust_test(code: str, assertions: str) -> dict[str, Any]:
    name = "aithena-eval-" + uuid.uuid4().hex
    harness = (
        "\n#[cfg(test)] mod checks { use super::*; #[test] fn golden() { " + assertions + " } }"
    )
    try:
        # fixed Docker argv; source goes to stdin
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
                "--memory=256m",
                "--cpus=1",
                "--pids-limit=64",
                "--tmpfs=/tmp:rw,exec,nosuid,size=32m,mode=1777",
                "aithena-rust-validator:local",
                "sh",
                "-c",
                "cat > /tmp/test.rs && rustc --edition=2021 --test /tmp/test.rs -o /tmp/check && /tmp/check",
            ],
            input=code + harness,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        return {
            "passed": result.returncode == 0,
            "diagnostic": (result.stdout + result.stderr)[-4000:],
        }
    finally:
        with suppress(OSError, subprocess.TimeoutExpired):
            # fixed cleanup command and generated name
            subprocess.run(  # nosec B603, B607
                ["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False
            )


def evaluate(
    case: dict[str, Any], repeat: int, model: str, kind: str, settings: str
) -> dict[str, Any]:
    backend = LLMAnalyzer(model, max_calls=2, max_output_tokens=4096)
    if settings == "baseline":
        backend.model = backend.model.model_copy(
            update={"temperature": None, "reasoning_effort": None}
        )
    if settings == "temperature-zero":
        backend.model = backend.model.model_copy(
            update={"temperature": 0, "reasoning_effort": "none"}
        )
    row: dict[str, Any] = {"case": case["id"], "repeat": repeat, "passed": False, "calls": []}
    try:
        if kind == "analysis":
            actual = backend.analyze(case["path"], case["source"])
            row["raw"] = actual.model_dump()
            normalized = normalize_license(actual).model_dump()
            row["actual"] = normalized
            row["differences"] = {
                k: {"expected": v, "actual": normalized[k]}
                for k, v in case["expected"].items()
                if normalized[k] != v
            }
            row["passed"] = not row["differences"]
        else:
            code = backend.translate(case["id"] + ".py", case["source"]).code
            row["code"] = code
            row["code_sha256"] = hashlib.sha256(code.encode()).hexdigest()
            row.update(rust_test(code, case["tests"]))
            row["license_preserved"] = all(
                line[1:].strip() in code
                for line in case["source"].splitlines()
                if line.startswith("#")
            )
            row["passed"] = row["passed"] and row["license_preserved"]
    except Exception as exc:  # Report failed evals without exposing provider bodies.
        row["error"] = safe_error(exc)
    row["calls"] = backend.calls
    return row


def summarize(rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    groups: defaultdict[str, list[str | None]] = defaultdict(list)
    for row in rows:
        signature = row.get("actual") if kind == "analysis" else row.get("code_sha256")
        groups[row["case"]].append(
            None if signature is None else json.dumps(signature, sort_keys=True, ensure_ascii=False)
        )
    eligible = {k: v for k, v in groups.items() if len(v) > 1}
    calls = [call for row in rows for call in row["calls"]]
    input_tokens = sum(call.get("input_tokens") or 0 for call in calls)
    output_tokens = sum(call.get("output_tokens") or 0 for call in calls)
    return {
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "exactly_repeatable_cases": sum(
            None not in v and len(set(v)) == 1 for v in eligible.values()
        ),
        "repeated_cases": len(eligible),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd_no_cache_discount": (input_tokens * 0.75 + output_tokens * 4.5) / 1_000_000,
        "failed_cases": sorted({row["case"] for row in rows if not row["passed"]}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--prompt-version", choices=["v1", "v2"], default="v2")
    parser.add_argument("--kind", choices=["analysis", "translation"], default="analysis")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument(
        "--settings", choices=["baseline", "temperature-zero"], default="temperature-zero"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 1 <= args.workers <= 4:
        parser.error("repeats must be 1..10; workers 1..4")
    load_dotenv(args.env_file, override=False)
    model = os.environ["LLM_MODEL"]
    prompts = json.loads((ROOT / ("prompts-" + args.prompt_version + ".json")).read_text())
    llm_module.SYSTEM = prompts["system"]
    llm_module.ANALYZE = prompts["analyze"]
    llm_module.TRANSLATE = prompts["translate"]
    llm_module.PROMPT_VERSION = args.prompt_version
    dataset = json.loads((ROOT / (args.kind + ".json")).read_text())
    cases = [
        c
        for c in dataset["cases"]
        if args.kind == "translation" or args.split == "all" or c["split"] == args.split
    ]
    if args.kind == "analysis":
        for case in cases:
            expected = Analysis.model_validate(case["expected"])
            parsed = extract_functions(case["source"], expected.language)
            if parsed is not None and parsed != expected.functions:
                raise ValueError("Invalid gold function list: " + case["id"])
    tasks: list[tuple[dict[str, Any], int]] = [
        (case, repeat) for repeat in range(1, args.repeats + 1) for case in cases
    ]
    report: dict[str, Any] = {
        "model": model,
        "settings": args.settings,
        "kind": args.kind,
        "split": args.split,
        "prompt_version": args.prompt_version,
        "prompt_sha256": hashlib.sha256(
            (
                prompts["system"] + prompts["analyze" if args.kind == "analysis" else "translate"]
            ).encode()
        ).hexdigest(),
        "dataset_sha256": hashlib.sha256((ROOT / (args.kind + ".json")).read_bytes()).hexdigest(),
        "prompts": {
            "system": prompts["system"],
            "task": prompts["analyze" if args.kind == "analysis" else "translate"],
        },
        "rows": [],
    }
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(
            lambda task: evaluate(task[0], task[1], model, args.kind, args.settings), tasks
        ):
            report["rows"].append(row)
            report["summary"] = summarize(report["rows"], args.kind)
            atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(
                f"{len(report['rows'])}/{len(tasks)} {row['case']} {'PASS' if row['passed'] else 'FAIL'}",
                flush=True,
            )
    print(json.dumps(report["summary"], indent=2), flush=True)
    return 0 if report["summary"]["passed"] == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
