"""Integration tests for the settings repo get path.

Three-tier dispatch:
  Tier 1 (halal)  -> _halal.read (file only)
  Tier 2 (safety) -> DB row if present, else _safety_seed.read (file fallback)
  Tier 3          -> DB row if present, else policy.yaml seed (graceful degrade)
"""
from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _seed_policy_setting(async_url: str, key: str, value_json: str, value_type: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO policy_settings (key, value, value_type, updated_by) "
                        "VALUES (:k, CAST(:v AS jsonb), :t, 'seed')"
                    ),
                    {"k": key, "v": value_json, "t": value_type},
                )
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_repo_reads_tier3_from_db(env_for_postgres):
    """A Tier 3 key with a DB row returns the DB value."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _seed_policy_setting(url, "regime_classifier.adx_trend_threshold", "30", "int")

        val = asyncio.run(repo.get("regime_classifier.adx_trend_threshold"))
        assert val == 30


@pytest.mark.integration
def test_repo_falls_back_to_yaml_for_tier3_when_no_db_row(env_for_postgres):
    """If a Tier 3 key has no DB row, the repo returns the policy.yaml default
    (graceful degradation; logs a warning)."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        # No DB row inserted; the live policy.yaml has 'max_order_usd: 200'

        val = asyncio.run(repo.get("max_order_usd"))
        assert val == 200


@pytest.mark.integration
def test_repo_tier1_routes_to_halal(env_for_postgres):
    """A Tier 1 read goes through _halal — never DB. Even if a (nefarious)
    DB row existed for a halal key, _halal would not see it."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        # Live policy.yaml has max_leverage: 1
        val = asyncio.run(repo.get("max_leverage"))
        assert val == 1


@pytest.mark.integration
def test_repo_tier2_db_override_wins_over_seed(env_for_postgres):
    """A Tier 2 key with a DB row returns the DB value (operator override).
    With no DB row, falls back to file seed."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # First, no DB row -> seed value (current policy.yaml: 25)
        val_seed = asyncio.run(repo.get("max_account_drawdown_pct"))
        assert val_seed == 25

        # Then operator override -> DB wins
        _seed_policy_setting(url, "max_account_drawdown_pct", "30", "int")
        val_db = asyncio.run(repo.get("max_account_drawdown_pct"))
        assert val_db == 30


@pytest.mark.integration
def test_repo_returns_none_for_unknown_tier3_key(env_for_postgres):
    """Unknown Tier 3 keys return None (caller decides default). Halal/safety
    keys raise KeyError because their absence is structural."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        val = asyncio.run(repo.get("totally.not.a.real.key.zzz"))
        assert val is None
