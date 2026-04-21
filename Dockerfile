# Single image used by every Python service; service entrypoint selects behavior.
FROM python:3.12-slim AS base

# Build deps for Python packages that ship C extensions (asyncpg, pandas wheels
# usually prebuilt; build-essential kept as a safety net on slim images).
# TA-Lib C lib is intentionally NOT installed in Phase 0 — Phase 0 uses pandas-ta
# (pure Python) only. When Phase 1 needs TA-Lib, install a pinned Debian package
# or prebuilt wheel here; do not build from source.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY policy.yaml CLAUDE.md ./

# Default cmd is overridden per service in compose.
CMD ["python", "-c", "print('service entrypoint required via compose')"]
