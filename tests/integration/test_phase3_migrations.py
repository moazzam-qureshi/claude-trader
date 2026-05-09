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


# --- 0014 regime_classifications + regime_pivots -----------------------------


_REGIME_CLASSIFICATIONS_COLS = [
    "id",
    "symbol",
    "timeframe",
    "regime",
    "signals",
    "classified_at",
]


_REGIME_PIVOTS_COLS = [
    "id",
    "symbol",
    "from_regime",
    "to_regime",
    "triggered_by",
    "triggered_at",
    "actions_taken",
    "prompt_version",
]


@pytest.mark.integration
def test_regime_classifications_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "regime_classifications")


@pytest.mark.integration
def test_regime_classifications_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "regime_classifications", _REGIME_CLASSIFICATIONS_COLS)


@pytest.mark.integration
def test_regime_pivots_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "regime_pivots")


@pytest.mark.integration
def test_regime_pivots_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "regime_pivots", _REGIME_PIVOTS_COLS)


# --- 0015 portfolio_decisions ------------------------------------------------


_PORTFOLIO_DECISIONS_COLS = [
    "id",
    "decision_type",
    "target_strategy_id",
    "target_symbol",
    "rationale",
    "market_context",
    "decided_by",
    "decided_at",
    "prompt_version",
]


@pytest.mark.integration
def test_portfolio_decisions_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "portfolio_decisions")


@pytest.mark.integration
def test_portfolio_decisions_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "portfolio_decisions", _PORTFOLIO_DECISIONS_COLS)


@pytest.mark.integration
def test_portfolio_decisions_fk_to_strategies(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_fk(url, "portfolio_decisions", "target_strategy_id", "strategies", "id")


# --- 0016 policy_settings + policy_changes -----------------------------------


_POLICY_SETTINGS_COLS = [
    "key",
    "value",
    "value_type",
    "description",
    "updated_at",
    "updated_by",
]


_POLICY_CHANGES_COLS = [
    "id",
    "key",
    "old_value",
    "new_value",
    "rationale",
    "changed_by",
    "authority",
    "applied",
    "rejection_reason",
    "changed_at",
    "prompt_version",
]


@pytest.mark.integration
def test_policy_settings_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "policy_settings")


@pytest.mark.integration
def test_policy_settings_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "policy_settings", _POLICY_SETTINGS_COLS)


@pytest.mark.integration
def test_policy_changes_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "policy_changes")


@pytest.mark.integration
def test_policy_changes_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "policy_changes", _POLICY_CHANGES_COLS)


@pytest.mark.integration
def test_policy_changes_rejected_row_requires_reason(env_for_postgres):
    """ck_policy_changes_rejection_has_reason must reject a row with applied=false
    and rejection_reason NULL."""
    from sqlalchemy.exc import IntegrityError

    async def _run(async_url: str) -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO policy_changes "
                    "(key, new_value, rationale, changed_by, authority, applied) "
                    "VALUES ('test.k', '1'::jsonb, 'r', 'system', 'seed', true)"
                ))
            try:
                async with engine.begin() as conn2:
                    await conn2.execute(text(
                        "INSERT INTO policy_changes "
                        "(key, new_value, rationale, changed_by, authority, applied) "
                        "VALUES ('test.k', '1'::jsonb, 'r', 'system', 'seed', false)"
                    ))
                raise AssertionError("expected check constraint violation")
            except IntegrityError:
                pass  # expected
        finally:
            await engine.dispose()

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_run(url))


# --- 0017 policy_snapshot column on decision tables --------------------------


@pytest.mark.integration
def test_claude_decisions_has_policy_snapshot(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "claude_decisions", ["policy_snapshot"])


@pytest.mark.integration
def test_portfolio_decisions_has_policy_snapshot(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "portfolio_decisions", ["policy_snapshot"])
