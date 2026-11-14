FROM ghcr.io/astral-sh/uv:0.9.28 AS uv
FROM python:3.12-slim AS runtime
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
RUN uv sync --locked --no-dev --no-editable

FROM runtime AS test
RUN uv sync --locked --no-editable
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q"]
