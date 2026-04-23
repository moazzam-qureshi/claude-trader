import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _assert_tables(async_url: str, tables: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                for tbl in tables:
                    result = await conn.execute(text(f"SELECT to_regclass('public.{tbl}')"))
                    assert result.scalar() == tbl, f"{tbl} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_migrations_run_and_create_raw_candles(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(url, ["raw_candles"])


@pytest.mark.integration
def test_all_phase_0_tables_exist(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(
            url, ["raw_candles", "features", "signals", "signal_outcomes", "claude_decisions"]
        )
