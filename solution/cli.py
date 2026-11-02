import argparse
import json
import math
import os
import sys
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from .errors import safe_error
from .llm import LLMAnalyzer
from .pipeline import atomic_write, process_directory
from .rust_sandbox import validate_rust_isolated


def main() -> int:
    env_parser = argparse.ArgumentParser(add_help=False)
    env_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    env_args, _ = env_parser.parse_known_args()
    load_dotenv(env_args.env_file, override=False)
    parser = argparse.ArgumentParser(description="Analyze source licenses with an LLM")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--input", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default=os.getenv("LLM_MODEL"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--rust-backend", choices=["docker", "local"], default=None)
    parser.add_argument("--rust-image", default="aithena-rust-validator:local")
    parser.add_argument("--skip-rust-check", action="store_true")
    args = parser.parse_args()
    if os.getenv("AITHENA_CONTAINER") == "1" and not args.skip_rust_check:
        if args.rust_backend != "local":
            parser.error(
                "The application image cannot launch a separate Docker sandbox. "
                "For trusted inputs explicitly use --rust-backend local; "
                "otherwise run the host CLI with the isolated Docker backend. "
                "--skip-rust-check saves unverified output."
            )
    args.rust_backend = args.rust_backend or "docker"
    if not args.model or not args.model.strip():
        parser.error("Set LLM_MODEL or pass --model (must support structured output)")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        parser.error("Set OPENAI_API_KEY")
    if args.max_output_tokens <= 0 or args.max_calls <= 0:
        parser.error("Token and call limits must be positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        analyzer = LLMAnalyzer(args.model, args.timeout, args.max_output_tokens, args.max_calls)
        results = process_directory(
            args.input,
            args.output,
            analyzer,
            check_rust=not args.skip_rust_check,
            verify_functions=True,
            rust_validator=(
                partial(validate_rust_isolated, image=args.rust_image)
                if args.rust_backend == "docker"
                else None
            ),
        )
        atomic_write(
            args.output / "usage.json",
            json.dumps(
                {"model": args.model, "calls": analyzer.calls},
                indent=2,
            )
            + "\n",
        )
    except Exception as exc:  # noqa: BLE001 — CLI/file boundary reports provider failures
        print(f"Error: {safe_error(exc)}", file=sys.stderr)
        return 1
    for result in results:
        print(f"{result.path}: {result.status} ({result.action or 'no action'})")
    print(f"Report: {args.output.resolve() / 'results.json'}")
    return 0 if all(r.status == "ok" for r in results) else 1
