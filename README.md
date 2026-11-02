# Aithena Python test task

A Python 3.13 CLI that uses LangChain to extract source licenses, copyright holders,
and function signatures, then translates small copyleft files into Rust.
The original assignment is in [task.md](task.md); its input files are unchanged in `data/`.

## Requirements and quick start

**Python 3.13 only**, uv, Docker, and an OpenAI API key are required for the default workflow.
`.python-version`, package metadata, the lockfile, Docker, and CI use Python 3.13.
The package rejects other Python minor versions even when executed directly from source.

```bash
uv python install 3.13
uv sync --frozen --python 3.13
docker build --target rust-validator -t aithena-rust-validator:local .
export OPENAI_API_KEY='your-key'
export LLM_MODEL='gpt-5.4-mini-2026-03-17'
uv run python -m solution --input data --output outputs
```

The model must support structured output, `temperature=0`, and `reasoning_effort=none`.
Use `--model` to override the model and `--timeout 60` to set the API timeout.
The CLI loads `.env` from the current directory; `--env-file /path/to/file` selects
another file. Existing environment variables take precedence. Source files are sent
to the selected API; API keys are excluded from reports.

Without Docker, `--skip-rust-check` saves translations with `rust_validation: "skipped"`.
Exit codes: `0` for success, `1` for processing errors or unsupported licenses,
and `2` for invalid arguments or missing settings. Partial failures are recorded
in `results.json`, and processing continues for other files.

## Processing rules

1. Read UTF-8 files recursively in stable order.
2. Ask the LLM for language, copyright holders, license name/category, and functions.
   Pydantic validates the response. Source code is supplied as JSON data separately
   from system instructions. Python AST and JavaScript Tree-sitter independently
   verify function names and parameter counts; disagreement fails that file.
   Verification for other languages is marked unsupported. Known licenses are
   normalized, and conflicts with their known categories are rejected.
3. Select the action in application code:
   - Permissive license: save function names and argument counts.
   - Copyleft license with more than two definitions: save function metadata.
   - Copyleft license with at most two definitions: request a Rust translation.
   - Unknown, ambiguous, or proprietary license: report `unsupported`.
4. Check Rust with `rustc --crate-type=lib --emit=metadata`, without executing it.
   The default validator runs in a separate unprivileged container without network,
   API keys, or host files, with a read-only filesystem and resource/time limits.
   `--rust-backend local` explicitly selects the host compiler without this isolation.
   Save successful translations and atomically checkpoint the report after each file.

The SDK retries transport failures up to twice. Invalid structured responses receive
one additional attempt. Compiler failures are reported as failures; automatic Rust
repair is not implemented.

## Output

`outputs/results.json` includes `schema_version: 2`, a UTC update timestamp, and `results`.
Each result contains the relative source path, status, analysis, selected action,
Rust path/validation status, error, source SHA-256, and independent function verification.
Analysis includes `language`, `copyright_holders`, `license_name`, `license_category`,
and `functions` with `name` and `argument_count`. The function count is the list length.

Rust files use `outputs/rust/<relative-source-path>.rs`, for example `rust/1.py.rs`.
Keeping the original extension avoids collisions between `foo.py` and `foo.js`.
Rust paths in reports are relative to the output directory. Repeated runs replace
reports and successful translations, but do not delete stale translations; use the
current report to identify current results. Input/output directories must not overlap.

`examples/` contains **illustrative output produced with mocked LLM responses** and
handwritten Rust, not a recorded API run. Local `live-results/` is excluded from Git.

| Input | License | Copyright holder | Expected result |
|---|---|---|---|
| `1.py` | GPL-3.0-or-later | Elon Musk | Rust library with public `foo` and `bar`, no automatic calls |
| `2.py` | GPL-3.0-or-later | Guido Musk | `foo(3), bar(1), foo1(1), bar1(1), bar1(1)` |
| `3.py` | MIT | Brendan Eich | JavaScript: `hello(1)` |

## Tests and quality checks

```bash
uv sync --frozen --python 3.13
uv run python -m pytest --cov=solution --cov-branch --cov-report=term-missing --cov-report=json:coverage.json -q
uv run python scripts/check_coverage.py coverage.json
uv run ruff check solution tests scripts evals
uv run ruff format --check solution tests scripts evals
uv run mypy
uv run flake8 solution tests scripts evals
uv run bandit -r solution scripts evals -q
```

Offline tests mock API responses. They cover positive, negative, and boundary scenarios:

| Area | Successful and boundary cases | Failure cases |
|---|---|---|
| Licenses | Aliases, GPL versions, empty functions, two/three-definition boundary | Missing license, blank name, category conflicts |
| Parsers | Async/nested functions, methods, decorators, variadic/positional-only parameters, JS generators/destructuring, Unicode | Invalid syntax, LLM disagreement, unsupported language |
| API | Strict schemas, translations, usage metrics, successful retry | HTTP errors, timeout, connection failure, refusal, invalid/empty responses, call limit |
| Files | UTF-8 BOM, Unicode paths, nesting, exact size limit | Oversize, invalid encoding, missing/empty input, read denial, symlinks, overlapping paths |
| Reports | Atomic replacement, per-file checkpoints | Disk errors, replacement failure, interrupted processing |
| Rust | Successful compilation, separate validator | Compiler errors, missing tools, timeout, cleanup failure |
| CLI | Environment precedence, reports, exit codes | Missing credentials/model, invalid limits, nonfinite timeout, safe error messages |
| Interpreter | Python 3.13 runtime | Rejection of unsupported Python versions |

CI enforces **at least 95% line coverage and independently 95% branch coverage**, without
rounding up. Coverage.py also enforces a combined 95% threshold. CLI, API adapter, error
handling, and module entry points remain included. Paid API calls are not needed to reach
these thresholds. Coverage reports are available as GitHub Actions artifacts.

To run the original-file integration test with real, paid API calls:

```bash
RUN_LLM_TESTS=1 uv run python -m pytest -m integration -q
```

Export `OPENAI_API_KEY` and `LLM_MODEL` first and build the Rust validator image.
Unlike the CLI, pytest does not automatically load `.env`. The integration test checks
extracted metadata and compilation. The local compiler test skips when `rustc` is absent.

## Docker

```bash
docker build -t aithena-solution .
mkdir -p outputs
docker run --rm --user "$(id -u):$(id -g)" \
  -e OPENAI_API_KEY -e LLM_MODEL \
  -v "$PWD/outputs:/results" \
  aithena-solution --output /results --rust-backend local
```

The runtime image and its virtual environment use Python 3.13. For custom input,
add `-v "$PWD/my-data:/input:ro"` and `--input /input`.
This example runs the compiler inside the API client container. For untrusted code,
prefer the host CLI with the separate validator image; do not mount the Docker socket
into the application container. Dependencies are locked in `uv.lock`; keys are not
included in the image.

## Limits and observability

- `--max-output-tokens 8192` limits each response, including reasoning tokens.
- `--max-calls 100` limits LangChain calls, including schema retries. SDK transport
  retries are additional (up to two per call). This is not a monetary budget.
- `usage.json` records model, operation, path, duration, status, and returned token usage.
  It excludes source contents, model response text, and keys. Missing usage is `null`,
  not zero; connection failures can leave the actual charge unknown.
- Provider errors omit response bodies; schema errors omit input contents.
- UTF-8 BOM is removed before analysis, but SHA-256 uses the original bytes.
  All discovered symlinks, including broken links, are reported as errors.

## Assumptions and limitations

Language is determined from content; `3.py` is JavaScript. Definitions include duplicates,
nested functions, and methods, but not calls or anonymous functions. Declared parameters
include `self`/`cls`, defaults, and each variadic parameter as one argument. Ambiguous or
dual licenses require manual resolution. Function metadata is extracted even for small
copyleft inputs so application code can choose the branch.

Rust is generated as a standard-library library with license notices preserved.
Compilation checks syntax and types, not semantic equivalence. Schema validation does
not prove factual accuracy, and prompt-based resistance to embedded instructions is not
an absolute guarantee. File size is limited to 1 MB; binary/non-UTF-8 files are unsupported.
Use dedicated directories: path checks do not protect against concurrent hostile changes.
Automatic resume, caching, batch parallelism, and concurrent writes to one output directory
are not supported. This is a hardened assignment CLI, not a certified production service.

## LLM evaluations

The dataset contains 30 analysis cases (20 development, 10 holdout) and five Rust behavior
cases. Reports preserve failures as well as successes. The current prompt is **v3** with
`temperature=0` and `reasoning_effort=none`, using `gpt-5.4-mini-2026-03-17`.
GitHub CI exposed a recurring negative-division error in v2; v3 adds an explicit signed
quotient/remainder correction algorithm. The subsequent CI run passed 90/90 analysis
responses and 15/15 Rust behavior checks. This is observed performance, not a guarantee
of deterministic correctness. See [evaluation methodology](evals/README.md).

## GitHub Actions

The [repository](https://github.com/Bogdankozlovskiy/aithena-python-task) is private.

- Pull requests and merge queues run Ruff lint/format, mypy, Flake8, Bandit,
  Python 3.13 tests, coverage gates, Docker builds, and interpreter-version assertions.
- Pushes/merges to `main` and manual runs on `main` also run the complete pytest suite
  with live API access, 30 analysis cases three times, and five Rust cases three times.
  These paid evaluations cost approximately $0.09 per successful run at the rates
  recorded in reports, excluding retries.
- `OPENAI_API_KEY` is a GitHub Actions secret, injected only into API test steps on `main`.
- `CI required` aggregates job results. Coverage HTML/XML/JSON and JUnit are uploaded;
  live evaluation reports are retained for 30 days.
- Mypy checks `solution`, `evals`, and `scripts`. Ruff and Flake8 also check tests.
  Bandit checks runtime and evaluation scripts, with narrowly scoped, documented
  subprocess suppressions for explicit argument lists and isolated generated code.

GitHub rejected required-check branch protection for this private repository with HTTP 403:
GitHub Pro or public visibility is required. Workflows report failures, but merges are
not currently blocked. After upgrading, require `CI required` on `main` with up-to-date branches.

LangChain API reference:
[ChatOpenAI.with_structured_output](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output).
