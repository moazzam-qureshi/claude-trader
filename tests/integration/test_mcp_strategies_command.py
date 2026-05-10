"""Phase 3 plan Task 1.12 — MCP active commands for strategies.

Surface (spec §3.5):
  deploy_strategy(strategy_type, symbol, capital_usd, params)
  wind_down_strategy(strategy_id, urgency)
  pause_strategy(strategy_id, reason)
  resume_strategy(strategy_id)
  adjust_allocation(strategy_id, new_capital_usd)
  adjust_params(strategy_id, params)
  override_regime(symbol, regime, duration_hours, reason)

Each call:
  - Validates against the strategies/regime_compat catalog when relevant.
  - Persists state changes via strategies.repo (which gates transitions).
  - Writes a portfolio_decisions audit row capturing the rationale,
    decided_by='claude', and prompt_version=git HEAD.
  - Returns a structured dict (success or error) — never raises to MCP.

The strategy-worker (Task 1.15) reads the persisted state and acts;
these commands are the WRITE side. The worker is the READ side.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

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


# --- deploy_strategy --------------------------------------------------------


@pytest.mark.integration
def test_deploy_strategy_creates_active_row(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="grid_standard",
            symbol="BTCUSDT",
            capital_usd=30,
            params={"low": 60000, "high": 70000, "levels": 5},
            rationale="range_volatile regime favors grid; 30 USDT seed sized",
        ))
        assert result["status"] == "ok"
        sid = result["strategy_id"]

        rows = _query(url,
            "SELECT strategy_type, symbol, status, capital_allocated_usd "
            "FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("grid_standard", "BTCUSDT", "active", Decimal("30"))]


@pytest.mark.integration
def test_deploy_strategy_rejects_unknown_strategy_type(env_for_postgres):
    """Unknown strategy_type → returned as error, no DB row written."""
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="nonexistent_strategy",
            symbol="BTCUSDT", capital_usd=30,
            params={}, rationale="test",
        ))
        assert result["status"] == "error"
        assert result["error"] == "unknown_strategy_type"

        rows = _query(url, "SELECT COUNT(*) FROM strategies", {})
        assert rows == [(0,)]


@pytest.mark.integration
def test_deploy_strategy_writes_portfolio_decision_row(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_usd=30, params={"levels": 5},
            rationale="range_volatile + tight ATR → grid",
        ))
        sid = result["strategy_id"]

        rows = _query(url,
            "SELECT decision_type, target_strategy_id, target_symbol, "
            "       rationale, decided_by "
            "FROM portfolio_decisions WHERE target_strategy_id = :i",
            {"i": sid})
        assert rows == [(
            "deploy", sid, "BTCUSDT",
            "range_volatile + tight ATR → grid", "claude",
        )]


# --- wind_down_strategy -----------------------------------------------------


@pytest.mark.integration
def test_wind_down_active_strategy_transitions_to_winding_down(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(strategies_command.wind_down_strategy(
            strategy_id=sid, urgency="graceful",
            rationale="regime shifted to trend_down; grid expected return turned negative",
        ))
        assert result["status"] == "ok"

        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("winding_down",)]


@pytest.mark.integration
def test_wind_down_rejects_pending_strategy(env_for_postgres):
    """Can't wind down a strategy that hasn't been deployed yet — has
    to go through active first."""
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        result = asyncio.run(strategies_command.wind_down_strategy(
            strategy_id=sid, urgency="graceful", rationale="x",
        ))
        assert result["status"] == "error"
        assert result["error"] == "invalid_transition"

        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("pending",)]


# --- pause / resume --------------------------------------------------------


@pytest.mark.integration
def test_pause_then_resume_round_trip(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(strategies_command.pause_strategy(
            strategy_id=sid, reason="checking grid range vs new ATR",
        ))
        assert result["status"] == "ok"
        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("paused",)]

        result = asyncio.run(strategies_command.resume_strategy(
            strategy_id=sid, rationale="ATR confirmed within range",
        ))
        assert result["status"] == "ok"
        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("active",)]


# --- adjust_allocation ------------------------------------------------------


@pytest.mark.integration
def test_adjust_allocation_changes_capital(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(strategies_command.adjust_allocation(
            strategy_id=sid, new_capital_usd=50,
            rationale="regime confirmed strong; doubling capital",
        ))
        assert result["status"] == "ok"
        assert result["old_capital_usd"] == "30"
        assert result["new_capital_usd"] == "50"

        rows = _query(url,
            "SELECT capital_allocated_usd FROM strategies WHERE id = :i",
            {"i": sid})
        assert rows == [(Decimal("50"),)]


# --- adjust_params ----------------------------------------------------------


@pytest.mark.integration
def test_adjust_params_merges_into_existing(env_for_postgres):
    """adjust_params merges the new dict into the existing params, not
    replace. Lets you tune one knob without re-supplying everything."""
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"low": 60000, "high": 70000, "levels": 5},
            deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(strategies_command.adjust_params(
            strategy_id=sid, params={"high": 75000},
            rationale="extending grid ceiling per breakout",
        ))
        assert result["status"] == "ok"

        rows = _query(url, "SELECT params FROM strategies WHERE id = :i", {"i": sid})
        assert rows[0][0] == {"low": 60000, "high": 75000, "levels": 5}


# --- override_regime --------------------------------------------------------


@pytest.mark.integration
def test_override_regime_writes_pivot_with_claude_override(env_for_postgres):
    """override_regime writes a regime_pivots row triggered_by=
    'claude_override'. Strategies acting on the regime pick this up
    on next tick."""
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.override_regime(
            symbol="BTCUSDT", regime="range_volatile",
            duration_hours=48,
            rationale="breakout failed at resistance; expect choppy two-day digestion",
        ))
        assert result["status"] == "ok"

        rows = _query(url,
            "SELECT to_regime, triggered_by FROM regime_pivots "
            "WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert rows == [("range_volatile", "claude_override")]


@pytest.mark.integration
def test_override_regime_rejects_invalid_regime(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.override_regime(
            symbol="BTCUSDT", regime="MOON_PHASE",
            duration_hours=48, rationale="x",
        ))
        assert result["status"] == "error"
        assert result["error"] == "unknown_regime"


@pytest.mark.integration
def test_override_regime_rejects_excessive_duration(env_for_postgres):
    """policy.yaml regime_classifier.manual_override_max_duration_hours = 168
    (1 week). Override longer than that is rejected."""
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_command.override_regime(
            symbol="BTCUSDT", regime="trend_up",
            duration_hours=200, rationale="x",
        ))
        assert result["status"] == "error"
        assert result["error"] == "duration_too_long"
