import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _point_settings_at(url: str) -> None:
    parsed = url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = parsed.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, db = hostdb.split("/", 1)
    host, port = hostport.split(":", 1)
    os.environ["POSTGRES_USER"] = user
    os.environ["POSTGRES_PASSWORD"] = password
    os.environ["POSTGRES_DB"] = db
    os.environ["POSTGRES_HOST"] = host
    os.environ["POSTGRES_PORT"] = port
    os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/0"
    os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/1"

    import trading_sandwich.config as cfg
    cfg._settings = None


@pytest.mark.integration
async def test_migrations_run_and_create_raw_candles():
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        _point_settings_at(url)
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

        engine = create_async_engine(url)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT to_regclass('public.raw_candles')"))
            assert result.scalar() == "raw_candles"
        await engine.dispose()


@pytest.mark.integration
async def test_all_phase_0_tables_exist():
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        _point_settings_at(url)
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

        engine = create_async_engine(url)
        async with engine.connect() as conn:
            for tbl in ["raw_candles", "features", "signals", "signal_outcomes", "claude_decisions"]:
                result = await conn.execute(text(f"SELECT to_regclass('public.{tbl}')"))
                assert result.scalar() == tbl, f"{tbl} missing"
        await engine.dispose()
