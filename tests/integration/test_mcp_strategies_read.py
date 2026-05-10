"""Phase 3 plan Task 1.11 — MCP read tools for strategies.

Surface (spec §3.5):
  list_strategies(active_only=True) -> list[dict]
  get_strategy_performance(strategy_id, since='7d') -> dict
  get_account_allocation() -> dict
  get_regime_signals(symbol) -> dict

Tests pin shape + behavior end-to-end against Postgres.
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


def _exec(async_url: str, sql: str, params: dict | None = None) -> None:
    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_list_strategies_returns_active_and_paused_only(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_read
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        s_active = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_active))
        s_paused = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="ETHUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_paused))
        asyncio.run(repo.mark_paused(s_paused))
        s_pending = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="SOLUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))

        result = asyncio.run(strategies_read.list_strategies(active_only=True))
        ids = sorted(r["id"] for r in result)
        assert ids == sorted([s_active, s_paused])
        # Each row has the expected keys.
        for r in result:
            assert set(r.keys()) >= {
                "id", "strategy_type", "symbol", "status",
                "capital_allocated_usd", "params", "deployed_by", "deployed_at",
            }


@pytest.mark.integration
def test_list_strategies_active_only_false_returns_all(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_read
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        s_pending = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        s_active = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="ETHUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_active))
        asyncio.run(repo.mark_winding_down(s_active))
        asyncio.run(repo.mark_completed(s_active))

        result = asyncio.run(strategies_read.list_strategies(active_only=False))
        ids = sorted(r["id"] for r in result)
        assert ids == sorted([s_pending, s_active])


@pytest.mark.integration
def test_get_strategy_performance_returns_pnl_breakdown(env_for_postgres):
    """The MCP tool just wraps performance.compute_realized_pnl,
    formatting the result as a dict with serializable values."""
    from trading_sandwich.mcp.tools import strategies_read
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        # No orders -> zero PnL report.
        result = asyncio.run(strategies_read.get_strategy_performance(
            sid, since="7d",
        ))
        assert result["strategy_id"] == sid
        assert result["realized_pnl_usd"] == "0"
        assert result["entry_count"] == 0
        assert result["exit_count"] == 0
        assert result["window"] == "7d"


@pytest.mark.integration
def test_get_strategy_performance_404_for_unknown_id(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_read

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_read.get_strategy_performance(
            999_999, since="7d",
        ))
        assert result.get("error") == "not_found"


@pytest.mark.integration
def test_get_account_allocation_aggregates_capital(env_for_postgres):
    """Sums capital_allocated_usd across active+paused strategies,
    grouped by symbol. Returns total_allocated_usd plus breakdown."""
    from trading_sandwich.mcp.tools import strategies_read
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Two on BTC (different strategy types — UNIQUE (strategy_type,
        # symbol) partial index prevents duplicates on the same pair).
        for stype, sym, cap in [
            ("grid_standard", "BTCUSDT", 50),
            ("dca_calendar", "BTCUSDT", 25),
            ("grid_standard", "ETHUSDT", 30),
        ]:
            sid = asyncio.run(repo.create_strategy(
                strategy_type=stype, symbol=sym,
                capital_allocated_usd=Decimal(cap),
                params={"levels": 5}, deployed_by="claude",
            ))
            asyncio.run(repo.mark_active(sid))

        # Add a completed one to confirm it's excluded
        s_done = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="SOLUSDT",
            capital_allocated_usd=Decimal("100"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_done))
        asyncio.run(repo.mark_winding_down(s_done))
        asyncio.run(repo.mark_completed(s_done))

        result = asyncio.run(strategies_read.get_account_allocation())
        assert result["total_allocated_usd"] == "105"
        # Per-symbol breakdown
        per_sym = {row["symbol"]: row["allocated_usd"] for row in result["by_symbol"]}
        assert per_sym == {"BTCUSDT": "75", "ETHUSDT": "30"}


@pytest.mark.integration
def test_get_regime_signals_returns_latest_classification(env_for_postgres):
    """Returns the latest classification + effective regime + recent
    classification stream + most recent pivot for a symbol."""
    from trading_sandwich.mcp.tools import strategies_read
    from trading_sandwich.regime import strategy_classifier as sc
    from trading_sandwich.regime.strategy_classifier import RegimeSignals

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sigs = RegimeSignals(
            price=Decimal("60000"), ma_medium=Decimal("58000"),
            ma_long=Decimal("55000"), ma_medium_slope_bps=8.0,
            adx=30.0, atr_pct=0.025,
        )
        # Two TREND_UP classifications to clear hysteresis -> baseline.
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", sigs))
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", sigs))

        result = asyncio.run(strategies_read.get_regime_signals("BTCUSDT"))
        assert result["symbol"] == "BTCUSDT"
        assert result["latest_regime"] == "trend_up"
        assert result["effective_regime"] == "trend_up"
        # Most recent classifications visible
        assert len(result["recent_classifications"]) >= 2
        # No pivots yet (cold-start baseline doesn't pivot)
        assert result["last_pivot"] is None


@pytest.mark.integration
def test_get_regime_signals_unknown_symbol_returns_empty_state(env_for_postgres):
    from trading_sandwich.mcp.tools import strategies_read

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(strategies_read.get_regime_signals("XXXUSDT"))
        assert result["symbol"] == "XXXUSDT"
        assert result["latest_regime"] is None
        assert result["effective_regime"] is None
        assert result["recent_classifications"] == []
        assert result["last_pivot"] is None
