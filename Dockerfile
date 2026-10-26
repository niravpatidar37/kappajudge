# syntax=docker/dockerfile:1
# Multi-stage build: dependencies resolved from uv.lock in a builder, copied into a slim non-root runtime.
FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.9.21@sha256:15f68a476b768083505fe1dbfcc998344d0135f0ca1b8465c4760b323904f05a /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
WORKDIR /app
# Streamlit writes a machine id under $HOME/.streamlit; give the non-root user exactly that directory.
RUN mkdir -p /app/.streamlit && chown app:app /app/.streamlit
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JUDGECAL_ROOT=/app PORT=8504 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true DEEPEVAL_TELEMETRY_OPT_OUT=YES \
    STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none ENV=dev
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app rubrics ./rubrics
COPY --chown=app:app prompts ./prompts
COPY --chown=app:app data/p1_label_queue.jsonl data/p1_eval_set.json data/calibration_sample.jsonl ./data/
COPY --chown=app:app results ./results
COPY --chown=app:app judge_release.json gate_config.json ./
# The scheduled eval job compares each fresh run with this baseline (gate-drift alert).
COPY --chown=app:app eval_runs/baseline.json ./eval_runs/baseline.json
USER app
EXPOSE 8504
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8504\")}/healthz', timeout=4)" || exit 1
# Shell form so $PORT (set by Cloud Run) is honored; exec keeps uvicorn as PID 1 for graceful SIGTERM.
CMD ["sh", "-c", "exec uvicorn judgecal.asgi:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 20 --no-server-header --no-access-log"]
