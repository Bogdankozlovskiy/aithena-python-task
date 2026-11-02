import hashlib
import json
import os
import shutil

# explicit argv; compiler execution is required
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .errors import safe_error
from .licensing import normalize_license
from .llm import Analyzer
from .models import Analysis, Result, RustSource
from .source_validation import detect_functions

MAX_SOURCE_BYTES = 1_000_000


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_rust(code: str) -> None:
    compiler = shutil.which("rustc")
    if not compiler:
        raise RuntimeError("rustc is required; install Rust or use --skip-rust-check")
    with tempfile.TemporaryDirectory(prefix="aithena-rust-") as directory:
        source = Path(directory) / "lib.rs"
        source.write_text(code, encoding="utf-8")
        # fixed argv; source is data, not host shell code
        completed = subprocess.run(  # nosec B603
            [
                compiler,
                "--edition=2021",
                "--crate-type=lib",
                "--emit=metadata",
                str(source),
                "-o",
                str(Path(directory) / "lib.rmeta"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            raise ValueError("Rust compilation failed")


def process_directory(
    input_dir: Path,
    output_dir: Path,
    analyzer: Analyzer,
    *,
    check_rust: bool = True,
    verify_functions: bool = False,
    rust_validator: Callable[[str], None] | None = None,
) -> list[Result]:
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if (
        input_dir == output_dir
        or input_dir in output_dir.parents
        or output_dir in input_dir.parents
    ):
        raise ValueError("Input and output directories must not overlap")
    paths = sorted(p for p in input_dir.rglob("*") if p.is_file() or p.is_symlink())
    if not paths:
        raise ValueError("Input directory contains no files")
    results = []
    for path in paths:
        relative = path.relative_to(input_dir).as_posix()
        result = Result(path=relative, status="error")
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(input_dir):
                raise ValueError("Symbolic links are not supported")
            with path.open("rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError(f"Source exceeds {MAX_SOURCE_BYTES} bytes")
            source = raw.decode("utf-8-sig")
            result.source_sha256 = hashlib.sha256(raw).hexdigest()
            analysis = normalize_license(
                Analysis.model_validate(analyzer.analyze(relative, source))
            )
            result.analysis = analysis
            if verify_functions:
                parsed = detect_functions(source)
                if parsed is not None:
                    if parsed != analysis.functions:
                        raise ValueError("LLM function extraction disagrees with source parser")
                    result.functions_validation = "passed"
                else:
                    result.functions_validation = "unsupported"
                    result.status = "unsupported"
                    raise ValueError("Source syntax cannot be independently verified")
            if analysis.license_category == "unknown" or analysis.license_name is None:
                result.status = "unsupported"
                result.error = "License is missing, unsupported or ambiguous"
            elif analysis.license_category == "permissive" or len(analysis.functions) > 2:
                result.action = "extract_functions"
                result.status = "ok"
            else:
                result.action = "translate_to_rust"
                translated = RustSource.model_validate(analyzer.translate(relative, source))
                if check_rust:
                    (rust_validator or validate_rust)(translated.code)
                # Retain the original extension to avoid collisions between foo.py and foo.js.
                rust_relative = Path("rust") / (relative + ".rs")
                destination = output_dir / rust_relative
                if not destination.resolve().is_relative_to(output_dir):
                    raise ValueError("Rust output path escapes the output directory")
                atomic_write(destination, translated.code)
                result.rust_path = rust_relative.as_posix()
                result.rust_validation = "passed" if check_rust else "skipped"
                result.status = "ok"
        except Exception as exc:  # noqa: BLE001 — CLI/file boundary reports provider failures
            result.error = safe_error(exc)
        results.append(result)
        # Checkpoint after every file: interrupted batches retain completed work.
        atomic_write(
            output_dir / "results.json",
            json.dumps(
                {
                    "schema_version": 2,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "results": [r.model_dump() for r in results],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    return results
