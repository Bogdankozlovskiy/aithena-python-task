FROM python:3.13-slim AS python-reference
USER 65534:65534
WORKDIR /tmp

FROM debian:bookworm-slim AS rust-validator
RUN apt-get update && apt-get install -y --no-install-recommends rustc \
    && rm -rf /var/lib/apt/lists/*
USER 65534:65534
WORKDIR /tmp

FROM python:3.13-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends rustc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN pip install --no-cache-dir uv==0.11.8
COPY solution ./solution
RUN uv sync --frozen --no-dev --python 3.13 --no-python-downloads
COPY data ./data
RUN useradd --uid 10001 --create-home appuser
USER appuser
ENTRYPOINT ["/app/.venv/bin/python", "-m", "solution"]
