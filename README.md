# Aithena Python test task

A Python 3.13 CLI for LLM-assisted license/function extraction and conditional Rust translation.
The original [assignment](task.md) and input files in `data/` are preserved.

## Quick start

Requires Python **3.13 only**, uv, Docker, and an OpenAI API key.

```bash
uv sync --frozen --python 3.13
docker build --target rust-validator -t aithena-rust-validator:local .
export OPENAI_API_KEY='your-key'
export LLM_MODEL='gpt-5.4-mini-2026-03-17'
uv run python -m solution --input data --output outputs
```

The CLI loads `.env`; existing environment variables take precedence. Input is sent to the API.
Recognizable credentials and credential labels followed by `=` or `:` reject the entire file.
No partial value is sent; screening is heuristic and can miss secrets or reject harmless examples.
Use `--help` for options and [detailed usage](docs/usage.md) for Docker and configuration examples.
The application image requires an explicit `--rust-backend local` choice for trusted inputs;
it never silently falls back from isolated validation to a local compiler.

## Behavior and results

- Permissive license: extract functions and argument counts.
- Copyleft with more than two definitions: extract functions; otherwise translate to Rust.
- Unknown license identifiers or unverifiable source syntax: unsupported, with no action selected.
- Python/JavaScript parsers verify functions independently of the model's language label.
- Reports are checkpointed atomically to `outputs/results.json`; Rust goes to `outputs/rust/`.
- Exit codes: `0` success, `1` processing/unsupported errors, `2` invalid configuration.

`data/1.py` translates to Rust; `2.py` preserves all five definitions; `3.py` is JavaScript/MIT.
[Example output](examples/results.json) uses mocked model responses and an illustrative timestamp.

## Tests and CI

```bash
uv run python -m pytest --cov=solution --cov-branch --cov-report=json:coverage.json -q
uv run python scripts/check_coverage.py coverage.json
uv run ruff check solution tests scripts evals
uv run ruff format --check solution tests scripts evals
uv run mypy
uv run flake8 solution tests scripts evals
uv run bandit -r solution scripts evals -q
```

PR CI checks Python 3.13 tests, >=95% line/branch coverage, lint/types/security, Docker sandboxes,
and an installed wheel outside the source tree. `main` additionally runs paid LLM evaluations.
The protected `main` branch requires a successful `CI required` check. PRs receive no API secrets.

## Evaluation and security

Production and evaluations share one [current prompt](solution/prompts-v3.json).
[Evaluation documentation](evals/README.md) covers repeated model calls, Python/Rust comparisons,
a separately frozen synthetic translation holdout, provenance, failures, and limits of evidence.

**Generated Rust is untrusted.** Compilation and passing tests do not establish safety.
Default compilation and evaluation execution use restricted containers without network, host mounts,
or credentials. Containers share a kernel and are not an absolute security boundary; deliberately
hostile workloads warrant disposable VM/microVM isolation, which is not implemented here.
Never run generated code directly with host access or secrets merely because tests passed.
See [SECURITY.md](SECURITY.md) and [execution risks](docs/usage.md#security-risk-generated-rust-is-untrusted-code).
