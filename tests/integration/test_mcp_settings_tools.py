"""Integration tests for the four settings MCP tools.

These tools are how Claude (the portfolio strategist) reads, lists,
mutates, and audits policy values from inside its shift. The
authority enforcement still lives in `repo.set_setting()` — these
tools always pass `authority='mcp_default', changed_by='claude'`,
which means:
  - Tier 3 keys: applied
  - Tier 2 keys: rejected (operator_only_key) — operator must use /safety
  - Tier 1 keys: rejected (halal_inviolable)

Discord notifications are fired but not asserted here (the webhook
post is best-effort/safe and silently skips when DISCORD_*_WEBHOOK_URL
is unset, which is the test environment).

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §8.
"""
from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _query(async_url: str, sql: str, params: dict | None = None) -> list[tuple]:
    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(sql), params or {})
                return [tuple(row) for row in r]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


# --- get_setting -----------------------------------------------------------


@pytest.mark.integration
def test_get_setting_returns_seeded_tier3_value(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        out = asyncio.run(mcp_settings.get_setting(key="regime.adx_trend_threshold"))
        assert out["key"] == "regime.adx_trend_threshold"
        assert out["value"] == 20
        assert out["value_type"] == "int"
        assert out["tier"] == 3
        assert out["updated_by"] == "seed"
        assert out["updated_at"] is not None


@pytest.mark.integration
def test_get_setting_returns_tier1_halal_with_marker(env_for_postgres):
    """Tier 1 reads come from policy.yaml; tier marker says 1."""
    from trading_sandwich.mcp.tools import settings as mcp_settings

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        out = asyncio.run(mcp_settings.get_setting(key="max_leverage"))
        assert out["key"] == "max_leverage"
        assert out["value"] == 1
        assert out["tier"] == 1
        assert out["updated_by"] == "file"


@pytest.mark.integration
def test_get_setting_unknown_key_returns_error(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        out = asyncio.run(mcp_settings.get_setting(key="totally.bogus.key"))
        assert out["error"] == "key_not_found"
        assert out["key"] == "totally.bogus.key"


# --- list_settings ---------------------------------------------------------


@pytest.mark.integration
def test_list_settings_no_prefix_returns_all(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        out = asyncio.run(mcp_settings.list_settings())
        assert isinstance(out, list)
        assert len(out) > 10
        keys = {row["key"] for row in out}
        assert "max_order_usd" in keys
        assert "regime.adx_trend_threshold" in keys


@pytest.mark.integration
def test_list_settings_with_prefix_filters(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        out = asyncio.run(mcp_settings.list_settings(prefix="position_sizing"))
        assert all(row["key"].startswith("position_sizing") for row in out)
        assert any(row["key"] == "position_sizing.base_pct" for row in out)


# --- set_setting -----------------------------------------------------------


@pytest.mark.integration
def test_set_setting_tier3_applied(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        out = asyncio.run(mcp_settings.set_setting(
            key="regime.adx_trend_threshold",
            value=33,
            rationale="adx tighter for choppy regime",
        ))
        assert out["applied"] is True
        assert out["key"] == "regime.adx_trend_threshold"
        assert out["new_value"] == 33
        assert out["old_value"] == 20

        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(33,)]


@pytest.mark.integration
def test_set_setting_tier2_rejected_operator_only(env_for_postgres):
    """Claude (mcp_default authority) cannot mutate Tier 2 — must redirect to /safety."""
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        out = asyncio.run(mcp_settings.set_setting(
            key="max_account_drawdown_pct",
            value=99,
            rationale="i would like to raise my drawdown cap please",
        ))
        assert out["applied"] is False
        assert out["error"] == "operator_only_key"
        assert "/safety" in out["message"]

        # No mutation
        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == [(25,)]  # seed value unchanged

        # Audit row exists
        audit = _query(url, "SELECT applied, rejection_reason FROM policy_changes "
                            "WHERE key = :k AND changed_by = 'claude'",
                       {"k": "max_account_drawdown_pct"})
        assert audit == [(False, "operator_only_key")]


@pytest.mark.integration
def test_set_setting_tier1_rejected_halal(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        out = asyncio.run(mcp_settings.set_setting(
            key="max_leverage", value=2,
            rationale="lol",
        ))
        assert out["applied"] is False
        assert out["error"] == "halal_inviolable"

        # Audit row exists
        audit = _query(url, "SELECT applied, rejection_reason FROM policy_changes "
                            "WHERE key = :k",
                       {"k": "max_leverage"})
        assert audit == [(False, "halal_inviolable")]


@pytest.mark.integration
def test_set_setting_infers_value_type_from_existing_row(env_for_postgres):
    """When the key already has a value_type, the MCP tool reuses it."""
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        # base_pct was seeded as float; set as float
        out = asyncio.run(mcp_settings.set_setting(
            key="position_sizing.base_pct",
            value=0.55,
            rationale="x",
        ))
        assert out["applied"] is True


# --- get_setting_history ---------------------------------------------------


@pytest.mark.integration
def test_get_setting_history_returns_audit_chain(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        # Mutate twice
        asyncio.run(mcp_settings.set_setting(
            key="regime.adx_trend_threshold", value=25, rationale="first tune",
        ))
        asyncio.run(mcp_settings.set_setting(
            key="regime.adx_trend_threshold", value=30, rationale="second tune",
        ))

        history = asyncio.run(mcp_settings.get_setting_history(
            key="regime.adx_trend_threshold"
        ))
        # Should have 3 rows: seed + 2 tunes, newest first
        assert len(history) == 3
        assert history[0]["new_value"] == 30
        assert history[0]["changed_by"] == "claude"
        assert history[0]["rationale"] == "second tune"
        assert history[1]["new_value"] == 25
        assert history[2]["new_value"] == 20
        assert history[2]["changed_by"] == "seed"


@pytest.mark.integration
def test_get_setting_history_respects_limit(env_for_postgres):
    from trading_sandwich.mcp.tools import settings as mcp_settings
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        for v in [21, 22, 23, 24, 25]:
            asyncio.run(mcp_settings.set_setting(
                key="regime.adx_trend_threshold", value=v, rationale=f"tune {v}",
            ))

        history = asyncio.run(mcp_settings.get_setting_history(
            key="regime.adx_trend_threshold", limit=2,
        ))
        assert len(history) == 2
        assert history[0]["new_value"] == 25
        assert history[1]["new_value"] == 24
