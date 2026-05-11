"""Phase 3 Wave 0 — Foundation integration smoke test (plan Task 1.16).

End-to-end verification that the Wave 0 plumbing is wired together:

  deploy_strategy (MCP)
    -> strategies row in 'active'
    -> portfolio_decisions row written
  worker tick (via tick_all_strategies)
    -> last_tick_at populated
    -> strategy_state row created
  get_strategy_performance / list_strategies / get_account_allocation
    -> reflects the deployed state
  pause_strategy
    -> worker skips it next tick
  resume_strategy
    -> worker ticks again
  wind_down_strategy
    -> worker doesn't tick (status not in active|paused listed)
  /strategies list (Discord handler)
    -> renders the strategy in a readable format

If this test passes, every Wave 0 task (1.5..1.15) is wired correctly
end-to-end. Wave 1 (real strategies) builds on this foundation.
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

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)


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


def _seed_candle(async_url: str, symbol: str) -> None:
    """One raw_candles row so build_snapshot returns market data —
    without it the worker skips the strategy as 'no data'."""
    from datetime import datetime, timezone

    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                ot = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
                ct = datetime(2026, 5, 11, 12, 1, tzinfo=timezone.utc)
                await conn.execute(
                    text(
                        "INSERT INTO raw_candles (symbol, timeframe, open_time, "
                        "close_time, open, high, low, close, volume) "
                        "VALUES (:s, '5m', :ot, :ct, 100, 101, 99, 100, 1)"
                    ),
                    {"s": symbol, "ot": ot, "ct": ct},
                )
        finally:
            await engine.dispose()
    asyncio.run(_run())


class FoundationStrategy(Strategy):
    """Foundation smoke-test strategy. Returns no intents; advances a
    state counter so we can verify state persistence across ticks.
    Stands in for 'dca_calendar' in this test (so the deploy_strategy
    catalog check passes). Wave 1 ships the real DCA implementation."""

    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        n = ctx.state.get("ticks", 0)
        ctx.state["ticks"] = n + 1
        return []

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0.02"), confidence=0.5)


@pytest.mark.integration
def test_wave_0_foundation_end_to_end(env_for_postgres):
    """Full deploy → tick → query → pause/resume → wind-down lifecycle."""
    from trading_sandwich.discord import strategies_handlers as h
    from trading_sandwich.mcp.tools import (
        strategies_command,
        strategies_read,
    )
    from trading_sandwich.strategies import worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _seed_candle(url, "BTCUSDT")

        REGISTRY = {"dca_calendar": FoundationStrategy}

        # --- 1. Deploy via MCP -------------------------------------
        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="dca_calendar",
            symbol="BTCUSDT",
            capital_usd=30,
            params={"weekly_amount_usd": 10},
            rationale="foundation smoke test deployment",
        ))
        assert deploy["status"] == "ok"
        sid = deploy["strategy_id"]

        # Strategy row exists in 'active' status
        rows = _query(url,
            "SELECT status, capital_allocated_usd FROM strategies WHERE id = :i",
            {"i": sid})
        assert rows == [("active", Decimal("30"))]

        # Portfolio decision row written
        decisions = _query(url,
            "SELECT decision_type, decided_by FROM portfolio_decisions "
            "WHERE target_strategy_id = :i", {"i": sid})
        assert decisions == [("deploy", "claude")]

        # --- 2. Worker ticks the strategy -------------------------
        report = asyncio.run(worker.tick_all_strategies(registry=REGISTRY))
        assert report.ticked == 1
        assert report.errored == 0

        # last_tick_at populated; state row created with ticks=1
        lt = _query(url,
            "SELECT last_tick_at FROM strategies WHERE id = :i", {"i": sid})
        assert lt[0][0] is not None
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i", {"i": sid})
        assert st[0][0] == {"ticks": 1}

        # Two more ticks → counter at 3
        for _ in range(2):
            asyncio.run(worker.tick_all_strategies(registry=REGISTRY))
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i", {"i": sid})
        assert st[0][0] == {"ticks": 3}

        # --- 3. MCP read tools reflect deployed state -------------
        listed = asyncio.run(strategies_read.list_strategies(active_only=True))
        assert len(listed) == 1
        assert listed[0]["id"] == sid
        assert listed[0]["strategy_type"] == "dca_calendar"
        assert listed[0]["status"] == "active"

        perf = asyncio.run(strategies_read.get_strategy_performance(
            sid, since="7d",
        ))
        assert perf["realized_pnl_usd"] == "0"  # No orders submitted yet
        assert perf["entry_count"] == 0

        alloc = asyncio.run(strategies_read.get_account_allocation())
        assert alloc["total_allocated_usd"] == "30"
        assert any(
            r["symbol"] == "BTCUSDT" and r["allocated_usd"] == "30"
            for r in alloc["by_symbol"]
        )

        # --- 4. Discord handler renders correctly -----------------
        reply = asyncio.run(h.handle_strategies_list())
        assert "dca_calendar" in reply
        assert "BTCUSDT" in reply
        assert "active" in reply
        assert str(sid) in reply

        # --- 5. Pause via MCP → worker skips it -------------------
        pause = asyncio.run(strategies_command.pause_strategy(
            strategy_id=sid, reason="smoke-test pause",
        ))
        assert pause["status"] == "ok"

        report = asyncio.run(worker.tick_all_strategies(registry=REGISTRY))
        assert report.ticked == 0
        assert report.skipped_paused == 1

        # State counter NOT incremented while paused
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i", {"i": sid})
        assert st[0][0] == {"ticks": 3}

        # --- 6. Resume → worker ticks again ----------------------
        resume = asyncio.run(strategies_command.resume_strategy(
            strategy_id=sid, rationale="smoke-test resume",
        ))
        assert resume["status"] == "ok"

        asyncio.run(worker.tick_all_strategies(registry=REGISTRY))
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i", {"i": sid})
        assert st[0][0] == {"ticks": 4}

        # --- 7. Wind down → worker doesn't tick -------------------
        wd = asyncio.run(strategies_command.wind_down_strategy(
            strategy_id=sid, urgency="graceful",
            rationale="smoke-test wind down",
        ))
        assert wd["status"] == "ok"

        report = asyncio.run(worker.tick_all_strategies(registry=REGISTRY))
        # winding_down isn't in list_active() — counted nowhere
        assert report.ticked == 0
        assert report.skipped_paused == 0

        # State unchanged
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i", {"i": sid})
        assert st[0][0] == {"ticks": 4}

        # --- 8. Final audit trail -------------------------------
        decisions = _query(url,
            "SELECT decision_type FROM portfolio_decisions "
            "WHERE target_strategy_id = :i ORDER BY id", {"i": sid})
        assert [d[0] for d in decisions] == [
            "deploy", "pause", "resume", "wind_down",
        ]
