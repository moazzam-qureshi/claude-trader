import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


_PHASE_2_TABLES = [
    "orders",
    "trade_proposals",
    "order_modifications",
    "positions",
    "risk_events",
    "kill_switch_state",
    "alerts",
]


def _assert_tables(async_url: str, tables: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                for tbl in tables:
                    r = await conn.execute(text(f"SELECT to_regclass('public.{tbl}')"))
                    assert r.scalar() == tbl, f"{tbl} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_phase2_tables_exist(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(url, _PHASE_2_TABLES)


@pytest.mark.integration
def test_kill_switch_state_singleton_seeded(env_for_postgres):
    async def _check(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text("SELECT id, active FROM kill_switch_state")
                )
                rows = r.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 1
                assert rows[0][1] is False
        finally:
            await engine.dispose()

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_check(url))


@pytest.mark.integration
def test_claude_decisions_unique_signal_invocation_mode(env_for_postgres):
    async def _check(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='claude_decisions' "
                    "AND indexname='uq_claude_decisions_signal_invocation_mode'"
                ))
                assert r.scalar() is not None
        finally:
            await engine.dispose()

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_check(url))
