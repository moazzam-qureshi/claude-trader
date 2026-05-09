"""Integration tests for Discord /settings + /safety command handlers.

These handlers are pure async functions taking the actor's Discord user
ID, the configured operator ID, and the typed slash-command args. They
return a markdown-formatted reply string for Discord and (where
mutating) write through the settings repo with the correct authority.

The split between /settings and /safety is structural:
  /settings set  -> always authority='mcp_default', changed_by='operator'
                    -> Tier 2 keys come back with operator_only_key (use /safety)
  /safety set    -> verifies actor_id == operator_id; passes authority='operator_safety'
                    -> non-operator -> 'not_operator' rejected audit row
                    -> Tier 2 keys go through; Tier 3 keys rejected (use /settings)

This test file pins the SAFETY-CRITICAL authority handoff that prevents
non-operator Discord users from raising Claude's circuit breakers.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §9.
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


# --- /settings list / get --------------------------------------------------


@pytest.mark.integration
def test_settings_list_returns_seeded_keys(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_settings_list
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_settings_list(prefix=""))
        assert "max_order_usd" in reply
        assert "regime.adx_trend_threshold" in reply


@pytest.mark.integration
def test_settings_list_with_prefix(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_settings_list
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_settings_list(prefix="position_sizing"))
        assert "position_sizing.base_pct" in reply
        # max_order_usd does not start with position_sizing
        assert "max_order_usd" not in reply


@pytest.mark.integration
def test_settings_get_returns_value(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_settings_get
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_settings_get(key="regime.adx_trend_threshold"))
        assert "regime.adx_trend_threshold" in reply
        assert "20" in reply


@pytest.mark.integration
def test_settings_get_unknown_key(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_settings_get

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(handle_settings_get(key="bogus.key"))
        assert "key_not_found" in reply or "not found" in reply.lower()


# --- /settings set: tier 3 --------------------------------------------------


@pytest.mark.integration
def test_settings_set_tier3_applied(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_settings_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_settings_set(
            key="regime.adx_trend_threshold",
            value_str="33",
            rationale="tighter adx for choppy market",
        ))
        assert "applied" in reply.lower()

        rows = _query(url, "SELECT value, updated_by FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(33, "operator")]


# --- /settings set redirected away from Tier 2 ----------------------------


@pytest.mark.integration
def test_settings_set_tier2_redirects_to_safety(env_for_postgres):
    """Operator using /settings set on a Tier 2 key gets redirected — the
    structural split prevents accidentally-bypassing the /safety surface."""
    from trading_sandwich.discord.settings_handlers import handle_settings_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_settings_set(
            key="max_account_drawdown_pct",
            value_str="40",
            rationale="raising cap; reviewed",
        ))
        assert "/safety set" in reply
        # No mutation
        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == [(25,)]


# --- /safety: operator-only authority gate --------------------------------


@pytest.mark.integration
def test_safety_set_non_operator_rejected_and_audited(env_for_postgres):
    """A non-operator Discord user invoking /safety set must NEVER mutate.

    SAFETY CRITICAL: this is the test that prevents random users with
    Discord access from raising Claude's drawdown cap. A `policy_changes`
    audit row with applied=false, rejection_reason='not_operator' is
    written so the operator can see attempted abuse.
    """
    from trading_sandwich.discord.settings_handlers import handle_safety_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_safety_set(
            actor_id="999_random_user",
            operator_id="111_operator",
            key="max_account_drawdown_pct",
            value_str="99",
            rationale="i am not the operator but please raise this",
        ))
        assert "not authorized" in reply.lower() or "not_operator" in reply

        # No mutation
        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == [(25,)]

        # Rejected audit row with reason='not_operator'
        audit = _query(url, "SELECT applied, rejection_reason, changed_by FROM policy_changes "
                            "WHERE key = :k AND rejection_reason = 'not_operator'",
                       {"k": "max_account_drawdown_pct"})
        assert len(audit) == 1
        assert audit[0] == (False, "not_operator", "operator")


@pytest.mark.integration
def test_safety_set_operator_applied(env_for_postgres):
    """The configured operator can mutate Tier 2 via /safety set."""
    from trading_sandwich.discord.settings_handlers import handle_safety_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_safety_set(
            actor_id="111_operator",
            operator_id="111_operator",
            key="max_account_drawdown_pct",
            value_str="30",
            rationale="reducing risk; equity grew",
        ))
        assert "applied" in reply.lower()

        rows = _query(url, "SELECT value, updated_by FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == [(30, "operator")]

        # Audit row with authority=operator_safety, applied=true
        # (excluding the seed bootstrap row which has authority='seed')
        audit = _query(url, "SELECT applied, authority FROM policy_changes "
                            "WHERE key = :k AND applied = true "
                            "AND authority = 'operator_safety'",
                       {"k": "max_account_drawdown_pct"})
        assert audit == [(True, "operator_safety")]


@pytest.mark.integration
def test_safety_set_rejects_tier3_key(env_for_postgres):
    """/safety set on a Tier 3 key is rejected — that's the Tier-3 surface's job."""
    from trading_sandwich.discord.settings_handlers import handle_safety_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_safety_set(
            actor_id="111_operator",
            operator_id="111_operator",
            key="regime.adx_trend_threshold",
            value_str="33",
            rationale="x",
        ))
        assert "/settings set" in reply
        # Value unchanged
        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(20,)]


@pytest.mark.integration
def test_safety_set_tier1_halal_rejected(env_for_postgres):
    """Even the operator via /safety cannot mutate Tier 1 halal keys."""
    from trading_sandwich.discord.settings_handlers import handle_safety_set

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(handle_safety_set(
            actor_id="111_operator",
            operator_id="111_operator",
            key="max_leverage",
            value_str="2",
            rationale="x",
        ))
        assert "halal" in reply.lower()


@pytest.mark.integration
def test_safety_list_shows_tier2_keys(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_safety_list
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_safety_list())
        assert "max_account_drawdown_pct" in reply
        assert "trading_enabled" in reply


@pytest.mark.integration
def test_safety_reset_restores_seed_value(env_for_postgres):
    """/safety reset deletes the DB row so the next read returns the file seed."""
    from trading_sandwich.discord.settings_handlers import handle_safety_reset, handle_safety_set
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        # Operator overrides
        asyncio.run(handle_safety_set(
            actor_id="111_operator", operator_id="111_operator",
            key="max_account_drawdown_pct", value_str="40",
            rationale="loosen during testing",
        ))

        # Reset
        reply = asyncio.run(handle_safety_reset(
            actor_id="111_operator", operator_id="111_operator",
            key="max_account_drawdown_pct",
        ))
        assert "reset" in reply.lower() or "restored" in reply.lower()

        # Row gone
        rows = _query(url, "SELECT * FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == []


@pytest.mark.integration
def test_safety_reset_non_operator_rejected(env_for_postgres):
    from trading_sandwich.discord.settings_handlers import handle_safety_reset
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(seed.bootstrap())

        reply = asyncio.run(handle_safety_reset(
            actor_id="999_random",
            operator_id="111_operator",
            key="max_account_drawdown_pct",
        ))
        assert "not authorized" in reply.lower() or "not_operator" in reply
