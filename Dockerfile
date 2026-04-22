# syntax=docker/dockerfile:1.7
# Dev-optimized image: deps baked in; source provided via bind mount + PYTHONPATH.
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.4.27 /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install dependencies only. Source is bind-mounted at runtime; PYTHONPATH
# picks it up. This layer is only invalidated when pyproject.toml changes.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
      sqlalchemy[asyncio]>=2.0.30 \
      asyncpg>=0.29 \
      alembic>=1.13 \
      pydantic>=2.7 \
      pydantic-settings>=2.3 \
      celery[redis]>=5.4 \
      redis>=5.0 \
      ccxt>=4.3 \
      pandas>=2.2 \
      pandas-ta>=0.3.14b \
      numpy>=1.26 \
      structlog>=24.1 \
      typer>=0.12 \
      prometheus-client>=0.20 \
      python-json-logger>=2.0 \
      pytest>=8.2 \
      pytest-asyncio>=0.23 \
      pytest-cov>=5.0 \
      "testcontainers[postgres,redis]>=4.5" \
      ruff>=0.5 \
      mypy>=1.10 \
      types-PyYAML

# Default cmd is overridden per service in compose.
CMD ["python", "-c", "print('service entrypoint required via compose')"]
