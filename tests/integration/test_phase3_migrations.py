"""Integration tests for Phase 3 migrations (0013-0015).

Mirrors the pattern in test_heartbeat_migrations.py: spin up a pgvector
testcontainer, run alembic upgrade head, assert the new tables and
their required columns exist.
"""
from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _check_table_exists(async_url: str, table: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(f"SELECT to_regclass('public.{table}')"))
                assert r.scalar() == table, f"{table} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _check_columns(async_url: str, table: str, required: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                )
                cols = {row[0] for row in r}
                for col in required:
                    assert col in cols, f"missing column {col} in {table}"
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _check_fk(async_url: str, table: str, column: str, ref_table: str, ref_column: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text(
                        """
                        SELECT
                          kcu.column_name,
                          ccu.table_name AS ref_table,
                          ccu.column_name AS ref_column
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                        JOIN information_schema.constraint_column_usage ccu
                          ON ccu.constraint_name = tc.constraint_name
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND tc.table_name = :t
                          AND kcu.column_name = :c
                        """
                    ),
                    {"t": table, "c": column},
                )
                rows = list(r)
                assert rows, f"no FK on {table}.{column}"
                _, rt, rc = rows[0]
                assert rt == ref_table and rc == ref_column, (
                    f"{table}.{column} FK -> {rt}.{rc}, expected {ref_table}.{ref_column}"
                )
        finally:
            await engine.dispose()
    asyncio.run(_run())


_STRATEGIES_COLS = [
    "id",
    "strategy_type",
    "symbol",
    "status",
    "capital_allocated_usd",
    "capital_deployed_usd",
    "params",
    "deployed_by",
    "deployed_at",
    "last_tick_at",
    "paused_at",
    "completed_at",
    "error_message",
    "prompt_version",
    "created_at",
]


_STRATEGY_STATE_COLS = ["strategy_id", "state", "updated_at"]


_STRATEGY_ORDERS_COLS = [
    "id",
    "strategy_id",
    "order_id",
    "role",
    "grid_level",
    "created_at",
]


@pytest.mark.integration
def test_strategies_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "strategies")


@pytest.mark.integration
def test_strategies_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "strategies", _STRATEGIES_COLS)


@pytest.mark.integration
def test_strategy_state_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "strategy_state")


@pytest.mark.integration
def test_strategy_state_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "strategy_state", _STRATEGY_STATE_COLS)


@pytest.mark.integration
def test_strategy_state_fk_to_strategies(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_fk(url, "strategy_state", "strategy_id", "strategies", "id")


@pytest.mark.integration
def test_strategy_orders_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "strategy_orders")


@pytest.mark.integration
def test_strategy_orders_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "strategy_orders", _STRATEGY_ORDERS_COLS)


@pytest.mark.integration
def test_strategy_orders_fk_to_orders(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_fk(url, "strategy_orders", "order_id", "orders", "order_id")


@pytest.mark.integration
def test_strategy_orders_fk_to_strategies(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_fk(url, "strategy_orders", "strategy_id", "strategies", "id")
