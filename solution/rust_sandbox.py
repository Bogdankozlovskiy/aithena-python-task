"""Compile generated code in a short-lived container without credentials or networking."""

# explicit argv; compiler execution is required
import subprocess  # nosec B404
import uuid
from contextlib import suppress


def validate_rust_isolated(code: str, image: str = "aithena-rust-validator:local") -> None:
    name = "aithena-check-" + uuid.uuid4().hex
    command = [
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
        "--tmpfs=/tmp:rw,noexec,nosuid,size=32m,mode=1777",
        image,
        "sh",
        "-c",
        (
            "cat > /tmp/lib.rs && rustc --edition=2021 --crate-type=lib "
            "--emit=metadata /tmp/lib.rs -o /tmp/lib.rmeta"
        ),
    ]
    try:
        # fixed Docker argv; source goes to stdin
        result = subprocess.run(  # nosec B603
            command, input=code, text=True, capture_output=True, timeout=45, check=False
        )
        if result.returncode:
            # Compiler output may contain source text or include! expansions: never persist it.
            raise ValueError("Isolated Rust compilation failed; inspect code in a sandbox")
    finally:
        # A cleanup failure must not hide the actual compiler/timeout error.
        with suppress(OSError, subprocess.TimeoutExpired):
            # fixed cleanup command and generated name
            subprocess.run(  # nosec B603, B607
                ["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False
            )
