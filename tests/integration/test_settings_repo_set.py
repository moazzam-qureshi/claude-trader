"""Integration tests for the settings repo set path — SAFETY CRITICAL.

These tests pin the three-tier authority enforcement that prevents Claude from
raising its own circuit breakers. Every adversarial path that could let a halal
or operator-safety value get mutated through the wrong codepath has a test.

If any of these tests get weakened or removed without a corresponding spec
amendment, that's a red flag.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §7, §15.
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


# --- Tier 3 happy path ------------------------------------------------------


@pytest.mark.integration
def test_tier3_set_with_mcp_default_authority_succeeds(env_for_postgres):
    """Tier 3 keys can be mutated through the standard MCP authority."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(repo.set_setting(
            key="regime_classifier.adx_trend_threshold",
            new_value=30,
            value_type="int",
            rationale="test",
            changed_by="claude",
            authority="mcp_default",
        ))
        assert result.applied is True

        rows = _query(url, "SELECT value, value_type, updated_by FROM policy_settings WHERE key = :k",
                      {"k": "regime_classifier.adx_trend_threshold"})
        assert rows == [(30, "int", "claude")]

        audit = _query(url, "SELECT applied, changed_by, authority, rejection_reason "
                            "FROM policy_changes WHERE key = :k",
                       {"k": "regime_classifier.adx_trend_threshold"})
        assert audit == [(True, "claude", "mcp_default", None)]


# --- Tier 1 (halal) — every path MUST reject -------------------------------


@pytest.mark.integration
def test_tier1_halal_rejects_mcp_default_authority(env_for_postgres):
    """Even with mcp_default authority, halal keys cannot be mutated."""
    from trading_sandwich.settings import repo, _halal

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        with pytest.raises(_halal.HalalViolationError):
            asyncio.run(repo.set_setting(
                key="max_leverage", new_value=2, value_type="int",
                rationale="lol", changed_by="claude", authority="mcp_default",
            ))

        # No policy_settings row created
        rows = _query(url, "SELECT * FROM policy_settings WHERE key = :k", {"k": "max_leverage"})
        assert rows == []

        # Rejected audit row exists with reason
        audit = _query(url, "SELECT applied, rejection_reason FROM policy_changes WHERE key = :k",
                       {"k": "max_leverage"})
        assert audit == [(False, "halal_inviolable")]


@pytest.mark.integration
def test_tier1_halal_rejects_operator_safety_authority(env_for_postgres):
    """Even operator_safety authority cannot mutate halal."""
    from trading_sandwich.settings import repo, _halal

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        with pytest.raises(_halal.HalalViolationError):
            asyncio.run(repo.set_setting(
                key="longs_only", new_value=False, value_type="bool",
                rationale="i am operator", changed_by="operator",
                authority="operator_safety",
            ))


# --- Tier 2 (safety) — only operator_safety authority gets through ---------


@pytest.mark.integration
def test_tier2_rejects_mcp_default_authority(env_for_postgres):
    """Claude (mcp_default) CANNOT mutate Tier 2. This is THE check that
    prevents Claude raising its own circuit breakers."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        with pytest.raises(repo.OperatorOnlyKeyError):
            asyncio.run(repo.set_setting(
                key="max_account_drawdown_pct", new_value=99,
                value_type="int",
                rationale="i am claude and i would like to raise my drawdown cap please",
                changed_by="claude", authority="mcp_default",
            ))

        # No policy_settings row created
        rows = _query(url, "SELECT * FROM policy_settings WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == []

        # Rejected audit row exists with reason
        audit = _query(url, "SELECT applied, changed_by, rejection_reason FROM policy_changes "
                            "WHERE key = :k",
                       {"k": "max_account_drawdown_pct"})
        assert audit == [(False, "claude", "operator_only_key")]


@pytest.mark.integration
def test_tier2_accepts_operator_safety_authority(env_for_postgres):
    """Operator can mutate Tier 2 via operator_safety authority."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(repo.set_setting(
            key="max_account_drawdown_pct", new_value=30,
            value_type="int",
            rationale="reducing risk; equity grew",
            changed_by="operator", authority="operator_safety",
        ))
        assert result.applied is True

        rows = _query(url, "SELECT value, value_type, updated_by FROM policy_settings "
                           "WHERE key = :k",
                      {"k": "max_account_drawdown_pct"})
        assert rows == [(30, "int", "operator")]


# --- Type validation --------------------------------------------------------


@pytest.mark.integration
def test_type_mismatch_rejected(env_for_postgres):
    """Setting a key to a value that doesn't match value_type raises."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # First, write an int value
        asyncio.run(repo.set_setting(
            key="strategies.grid_standard.default_levels", new_value=5,
            value_type="int", rationale="initial", changed_by="seed",
            authority="seed",
        ))

        # Now try to overwrite with a string -> reject
        with pytest.raises(repo.TypeMismatchError):
            asyncio.run(repo.set_setting(
                key="strategies.grid_standard.default_levels", new_value="not_a_number",
                value_type="string", rationale="oops", changed_by="claude",
                authority="mcp_default",
            ))


# --- Audit row hygiene ------------------------------------------------------


@pytest.mark.integration
def test_audit_row_has_old_value_on_overwrite(env_for_postgres):
    """When mutating an existing key, policy_changes records old_value."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(repo.set_setting(
            key="regime_classifier.atr_pct_quiet_threshold", new_value=0.015,
            value_type="float", rationale="initial", changed_by="seed",
            authority="seed",
        ))
        asyncio.run(repo.set_setting(
            key="regime_classifier.atr_pct_quiet_threshold", new_value=0.020,
            value_type="float", rationale="loosen quiet threshold", changed_by="claude",
            authority="mcp_default",
        ))

        audit = _query(url, "SELECT old_value, new_value, changed_by, applied "
                            "FROM policy_changes WHERE key = :k ORDER BY id",
                       {"k": "regime_classifier.atr_pct_quiet_threshold"})
        assert audit[0] == (None, 0.015, "seed", True)
        # second row's old_value matches first row's new_value
        assert audit[1][0] == 0.015
        assert audit[1][1] == 0.020


@pytest.mark.integration
def test_unknown_authority_rejected(env_for_postgres):
    """Pass an authority string that isn't in the allowlist -> raise."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        with pytest.raises(ValueError, match="authority"):
            asyncio.run(repo.set_setting(
                key="regime_classifier.adx_trend_threshold", new_value=30,
                value_type="int", rationale="test", changed_by="claude",
                authority="i_am_root",  # not allowed
            ))


@pytest.mark.integration
def test_unknown_changed_by_rejected(env_for_postgres):
    """Pass a changed_by string that isn't in the allowlist -> raise."""
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        with pytest.raises(ValueError, match="changed_by"):
            asyncio.run(repo.set_setting(
                key="regime_classifier.adx_trend_threshold", new_value=30,
                value_type="int", rationale="test", changed_by="hacker",
                authority="mcp_default",
            ))
